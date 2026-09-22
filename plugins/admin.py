from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from utils import is_admin, is_admin_sync, is_mod, is_owner, is_clone_instance
from config import OWNER_ID, OWNER_IDS
import global_state
import database
from config import DATA_DIR
import config
import json
import logging
import os
import re
import html
import time

_pending_draft_adds = {}

def _parse_anime_add(lines):
    if len(lines) < 11:
        raise ValueError("Invalid format. Expected command line, anime, image URL, then stats.")
    char_name = lines[0].split(maxsplit=1)[1].strip()
    anime_name = lines[1].strip()
    img_url = lines[2].strip()
    stats = {
        "captain": int(lines[3]),
        "vice_captain": int(lines[4]),
        "tank": int(lines[5]),
        "healer": int(lines[6]),
        "assassin": int(lines[7]),
        "support": int(lines[8]),
        "traitor": int(lines[10]),
    }
    return {"name": char_name, "series": anime_name, "img": img_url, "stats": stats}

def _parse_comic_add(lines):
    if len(lines) < 11:
        raise ValueError("Invalid format. Expected command line, series, image URL, then 8 stats.")
    char_name = lines[0].split(maxsplit=1)[1].strip()
    series_name = lines[1].strip()
    img_url = lines[2].strip()
    stats = {
        "paragon": int(lines[3]),
        "genius": int(lines[4]),
        "powerhouse": int(lines[5]),
        "mystic": int(lines[6]),
        "street_level": int(lines[7]),
        "cosmic": int(lines[8]),
        "trickster": int(lines[9]),
        "herald": int(lines[10]),
    }
    return {"name": char_name, "series": series_name, "img": img_url, "stats": stats}

def _save_anime_add(char_doc):
    char_name = char_doc["name"]
    anime_name = char_doc["series"]
    img_url = char_doc["img"]
    stats = char_doc["stats"]

    unique_id = f"{char_name} | {anime_name}"
    global_state.CHAR_STATS[unique_id] = stats
    global_state.CHAR_IMAGES[unique_id] = img_url
    if char_name not in global_state.ANIME_CHARACTERS:
        global_state.ANIME_CHARACTERS.append(char_name)

    norm = re.sub(r'[^a-z0-9]', '', anime_name.lower())
    global_state.SERIES_MAP.setdefault(norm, [])
    if unique_id not in global_state.SERIES_MAP[norm]:
        global_state.SERIES_MAP[norm].append(unique_id)
    global_state.SERIES_DISPLAY[norm] = anime_name

    char_doc = {**char_doc, "kind": "anime"}
    if database.USE_MONGO and database.COL_CHARACTERS is not None:
        database.COL_CHARACTERS.update_one(
            {"name": char_name},
            {"$set": char_doc},
            upsert=True,
        )
    else:
        data = []
        if os.path.exists(database.CHARACTERS_FILE):
            with open(database.CHARACTERS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
        data = [item for item in data if item.get("name") != char_name]
        data.append(char_doc)
        with open(database.CHARACTERS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f)

def _save_comic_add(char_doc):
    char_name = char_doc["name"]
    series_name = char_doc["series"]
    img_url = char_doc["img"]
    stats = char_doc["stats"]

    unique_id = f"{char_name} | Marvel"
    global_state.CHAR_STATS[unique_id] = stats
    global_state.CHAR_IMAGES[unique_id] = img_url
    if unique_id not in global_state.MARVEL_CHARACTERS:
        global_state.MARVEL_CHARACTERS.append(unique_id)

    norm = re.sub(r'[^a-z0-9]', '', series_name.lower())
    global_state.MARVEL_SERIES_MAP.setdefault(norm, [])
    if unique_id not in global_state.MARVEL_SERIES_MAP[norm]:
        global_state.MARVEL_SERIES_MAP[norm].append(unique_id)
    global_state.MARVEL_SERIES_DISPLAY[norm] = series_name

    char_doc = {**char_doc, "kind": "comic"}
    if database.USE_MONGO and database.COL_CHARACTERS is not None:
        database.COL_CHARACTERS.update_one(
            {"name": char_name},
            {"$set": char_doc},
            upsert=True,
        )
    else:
        marvel_file = os.path.join(DATA_DIR, "marvel.json")
        data = []
        if os.path.exists(marvel_file):
            with open(marvel_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
        data = [item for item in data if item.get("name") != char_name]
        data.append(char_doc)
        with open(marvel_file, 'w', encoding='utf-8') as f:
            json.dump(data, f)

def _draft_add_caption(kind, char_doc, submitter):
    title = "Anime Character" if kind == "anime" else "Comic Character"
    stat_lines = "\n".join(f"{key}: {value}" for key, value in char_doc["stats"].items())
    return (
        f"📝 <b>Pending {title}</b>\n\n"
        f"<b>Name:</b> {html.escape(char_doc['name'])}\n"
        f"<b>Series:</b> {html.escape(char_doc['series'])}\n"
        f"<b>Image:</b> {html.escape(char_doc['img'])}\n\n"
        f"<b>Stats:</b>\n<code>{html.escape(stat_lines)}</code>\n\n"
        f"Submitted by <a href='tg://user?id={submitter['id']}'>{html.escape(submitter['name'])}</a>"
    )

async def _send_draft_add_approval(client, message, kind, char_doc):
    submitter = {"id": message.from_user.id, "name": message.from_user.first_name or str(message.from_user.id)}
    token = f"{kind}_{submitter['id']}_{int(time.time())}_{len(_pending_draft_adds)}"
    _pending_draft_adds[token] = {"kind": kind, "char": char_doc, "submitter": submitter}
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"draftadd:{kind}:approve:{token}"),
        InlineKeyboardButton("❌ Reject", callback_data=f"draftadd:{kind}:reject:{token}"),
    ]])
    caption = _draft_add_caption(kind, char_doc, submitter)
    try:
        await client.send_photo(
            chat_id=config.CHAR_APPROVER_ID,
            photo=char_doc["img"],
            caption=caption + "\n\n⚠️ <b>Pending Admin Approval</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )
    except Exception:
        await client.send_message(
            chat_id=config.CHAR_APPROVER_ID,
            text=caption + "\n\n⚠️ <b>Pending Admin Approval</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
            disable_web_page_preview=False,
        )
    await message.reply(
        f"⏳ <b>{html.escape(char_doc['name'])}</b> was sent to owner approval.",
        parse_mode=ParseMode.HTML,
    )

async def _edit_draft_add_approval(callback_query, text):
    try:
        if callback_query.message.photo:
            await callback_query.edit_message_caption(caption=text, parse_mode=ParseMode.HTML)
        else:
            await callback_query.edit_message_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=False)
    except Exception:
        pass

@Client.on_callback_query(filters.regex(r"^draftadd:(anime|comic):(approve|reject):([^:]+)$"))
async def draft_add_approval_cb(client, callback_query: CallbackQuery):
    if callback_query.from_user.id != config.CHAR_APPROVER_ID:
        return await callback_query.answer("Only the owner approver can use this.", show_alert=True)

    kind = callback_query.matches[0].group(1)
    action = callback_query.matches[0].group(2)
    token = callback_query.matches[0].group(3)
    pending = _pending_draft_adds.get(token)
    if not pending:
        return await callback_query.answer("This approval request expired.", show_alert=True)

    char_doc = pending["char"]
    submitter = pending["submitter"]
    caption = _draft_add_caption(kind, char_doc, submitter)

    if action == "reject":
        _pending_draft_adds.pop(token, None)
        await _edit_draft_add_approval(
            callback_query,
            caption + f"\n\n❌ <b>Rejected by {html.escape(callback_query.from_user.first_name)}</b>",
        )
        return await callback_query.answer("Rejected.")

    try:
        if kind == "comic":
            _save_comic_add(char_doc)
        else:
            _save_anime_add(char_doc)
    except Exception as exc:
        return await callback_query.answer(f"Save failed: {exc}", show_alert=True)

    _pending_draft_adds.pop(token, None)
    await _edit_draft_add_approval(
        callback_query,
        caption + f"\n\n✅ <b>Approved by {html.escape(callback_query.from_user.first_name)}</b>",
    )
    await callback_query.answer("Approved and saved.")

@Client.on_message(filters.command("acast"))
async def acast_cmd(c, m):
    # permission logic differs between main bot and clones
    database.set_current_client(c)
    caller_id = m.from_user.id
    is_clone = getattr(c, 'is_clone', False)
    owner_id = getattr(c, 'clone_owner_id', None) if is_clone else None
    logging.info(f"[ACAST] User {caller_id} invoked /acast {'(clone)' if is_clone else '(main)'} owner={owner_id}")

    if is_clone:
        # clones allow their owner (mod) or any admin/owner
        if not (is_mod(caller_id) or await is_admin(caller_id)):
            logging.warning(f"[ACAST] Unauthorized attempt by {caller_id} on clone {owner_id}")
            return await m.reply("❌ You are not authorized to broadcast on this clone.")
    else:
        # main instance: only the configured owner/admin list, not mods
        if caller_id != OWNER_ID and caller_id not in OWNER_IDS:
            logging.warning(f"[ACAST] Unauthorized attempt by {caller_id} on main bot")
            return await m.reply("❌ Only main bot admins can use /acast on the primary bot.")

    if len(m.command) < 2:
        logging.debug(f"[ACAST] No message provided by {caller_id}")
        return await m.reply("Usage: /acast <message>")
    msg = m.text.split(maxsplit=1)[1]
    sent, failed = 0, 0
    await m.reply("📡 Sending broadcast...")
    chats = database.load_id_set(database.CHATS_FILE, client=c)
    logging.info(f"[ACAST] Retrieved {len(chats)} chat(s) for broadcasting (owner={owner_id})")
    for chat_id in chats:
        try:
            await c.send_message(chat_id, f"📢 <b>Broadcast</b>\n\n{msg}", parse_mode=ParseMode.HTML)
            sent += 1
        except Exception as exc:
            failed += 1
            logging.debug(f"[ACAST] send_message failed for chat {chat_id}: {exc}")
    logging.info(f"[ACAST] Broadcast finished: sent={sent}, failed={failed}")
    await m.reply(f"✅ Sent: {sent} | ❌ Failed: {failed}")

@Client.on_message(filters.command("add"))
async def add_char_cmd(c, m):
    logging.info(f"[ADMIN] /add invoked by {m.from_user.id}")
    if getattr(c, 'is_clone', False):
        logging.warning(f"[ADMIN] User {m.from_user.id} attempted /add on clone instance (blocked)")
        return await m.reply("❌ This command is disabled on cloned instances.")
    lines = m.text.split('\n')
    try:
        char_doc = _parse_anime_add(lines)
    except Exception as e:
        return await m.reply(f"Error: {e}\nUsage: /add Character Name\nAnime\nImage URL\n<8 stats>")

    if not await is_admin(m.from_user.id):
        return await _send_draft_add_approval(c, m, "anime", char_doc)

    try:
        _save_anime_add(char_doc)
        return await m.reply(
            f"Added <b>{html.escape(char_doc['name'])}</b> from {html.escape(char_doc['series'])}!",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        return await m.reply(f"Error: {e}")

@Client.on_message(filters.command("addpoints"))
async def addpoints_cmd(c, m):
    logging.info(f"[ADMIN] /addpoints invoked by {m.from_user.id}")
    if getattr(c, 'is_clone', False):
        logging.warning(f"[ADMIN] User {m.from_user.id} attempted /addpoints on clone instance (blocked)")
        return await m.reply("❌ This command is disabled on cloned instances.")
    if not await is_admin(m.from_user.id):
        return await m.reply("Admin only.")
    try:
        _, uid, pts = m.text.split()
        uid, pts = int(uid), int(pts)
        stats = database.get_user_stats(uid)
        stats["rating"] = max(0, stats.get("rating", 1200) + pts)
        database.save_user_stats(uid, stats)
        await m.reply(f"✅ Added {pts} points to user {uid}.")
    except: await m.reply("Usage: /addpoints <uid> <points>")

@Client.on_message(filters.command("newyear"))
async def newyear_cmd(c, m):
    logging.info(f"[ADMIN] /newyear invoked by {m.from_user.id} with text: {m.text}")
    if getattr(c, 'is_clone', False):
        logging.warning(f"[ADMIN] User {m.from_user.id} attempted /newyear on clone instance (blocked)")
        return await m.reply("❌ This command is disabled on cloned instances.")
    if not await is_admin(m.from_user.id):
        return await m.reply("Admin only.")
    arg = m.text.split()[1].lower() if len(m.text.split()) > 1 else ""
    if arg in ("on", "true", "yes"):
        global_state.NEW_YEAR_EVENT = True
        await m.reply("✅ New Year Event: ON")
    else:
        global_state.NEW_YEAR_EVENT = False
        await m.reply("❌ New Year Event: OFF")
    
    with open(database.EVENT_FLAGS_FILE, "w") as f:
        json.dump({"NEW_YEAR_EVENT": global_state.NEW_YEAR_EVENT}, f)

@Client.on_message(filters.command("comicimg"))
async def comicimg_cmd(c, m):
    logging.info(f"[ADMIN] /comicimg invoked by {m.from_user.id}")
    if getattr(c, 'is_clone', False):
        logging.warning(f"[ADMIN] User {m.from_user.id} attempted /comicimg on clone instance (blocked)")
        return await m.reply("❌ This command is disabled on cloned instances.")
    if not await is_admin(m.from_user.id):
        return await m.reply("Admin only.")
    """Update character image: /comicimg "url" "name" "series name" """
    try:
        parts = m.text.split('"')
        if len(parts) < 6:
            return await m.reply('Usage: /comicimg "url" "name" "series name"')
        
        img_url = parts[1]
        char_name = parts[3]
        series_name = parts[5]
        
        unique_id = f"{char_name} | Marvel"
        global_state.CHAR_IMAGES[unique_id] = img_url
        
        # Update in stats database too if character exists
        if unique_id in global_state.CHAR_STATS:
            if database.USE_MONGO and database.COL_CHARACTERS:
                database.COL_CHARACTERS.update_one({"name": char_name, "series": series_name}, {"$set": {"img": img_url}}, upsert=False)
            else:
                marvel_file = os.path.join(DATA_DIR, "marvel.json")
                if os.path.exists(marvel_file):
                    with open(marvel_file, 'r') as f: data = json.load(f)
                    for char in data:
                        if char.get("name") == char_name and char.get("series") == series_name:
                            char["img"] = img_url
                            break
                    with open(marvel_file, 'w') as f: json.dump(data, f)
        
        await m.reply(f"✅ Updated image for <b>{char_name}</b>", parse_mode=ParseMode.HTML)
    except Exception as e:
        await m.reply(f"❌ Error: {e}")

@Client.on_message(filters.command("add_comic"))
async def add_comic_cmd(c, m):
    logging.info(f"[ADMIN] /add_comic invoked by {m.from_user.id}")
    if getattr(c, 'is_clone', False):
        logging.warning(f"[ADMIN] User {m.from_user.id} attempted /add_comic on clone instance (blocked)")
        return await m.reply("❌ This command is disabled on cloned instances.")
    lines = m.text.split('\n')
    try:
        char_doc = _parse_comic_add(lines)
    except Exception as e:
        return await m.reply(f"Error: {e}\nUsage: /add_comic Character Name\nSeries\nImage URL\n<8 stats>")

    if not await is_admin(m.from_user.id):
        return await _send_draft_add_approval(c, m, "comic", char_doc)

    try:
        _save_comic_add(char_doc)
        return await m.reply(
            f"Added <b>{html.escape(char_doc['name'])}</b> from {html.escape(char_doc['series'])}!",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        return await m.reply(f"Error: {e}")

@Client.on_message(filters.command("characters"))
async def characters_cmd(c, m):
    if getattr(c, 'is_clone', False):
        logging.warning(f"[ADMIN] User {m.from_user.id} attempted /characters on clone instance (blocked)")
        return await m.reply("❌ This command is disabled on cloned instances.")
    if not await is_admin(m.from_user.id):
        return await m.reply("Admin only.")
    """Show all characters with toggle between anime/comics - admin only"""
    mode = "anime"
    if len(m.text.split()) > 1 and m.text.split()[1].lower() == "comics":
        mode = "comics"
    
    if mode == "anime":
        if not global_state.SERIES_DISPLAY:
            return await m.reply_text("📚 No anime series found.")
        all_series = sorted(set(global_state.SERIES_DISPLAY.items()))  # Get tuples of (normalized, display)
    else:  # comics
        if not global_state.MARVEL_SERIES_DISPLAY:
            return await m.reply_text("🎬 No comics found.")
        all_series = sorted(set(global_state.MARVEL_SERIES_DISPLAY.items()))
    
    txt = f"{'🎭 Anime Series' if mode == 'anime' else '🎬 Comics'}\n(Click to see characters)\n\n"
    
    kb = []
    buttons_in_row = 2
    row = []
    for norm_name, display_name in all_series:
        row.append(InlineKeyboardButton(display_name, callback_data=f"char_{mode}_{norm_name}"))
        if len(row) == buttons_in_row:
            kb.append(row)
            row = []
    if row:
        kb.append(row)
    
    # Add toggle button
    toggle_btn = InlineKeyboardButton("🎬 Comics", callback_data="char_comics_list") if mode == "anime" else InlineKeyboardButton("🎭 Anime", callback_data="char_anime_list")
    kb.append([toggle_btn])
    
    await m.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb) if kb else None)

@Client.on_message(filters.command("sudo") & filters.create(lambda _, __, m: is_owner(m.from_user.id)))
async def sudo_cmd(c, m):
    """Promote/Demote moderators - Admin only"""
    if getattr(c, 'is_clone', False):
        logging.warning(f"[ADMIN] User {m.from_user.id} attempted /sudo on clone instance (blocked)")
        return await m.reply("❌ This command is disabled on cloned instances.")
    
    # Check if replying to a user
    reply = m.reply_to_message
    
    # Parse command arguments
    args = m.text.split()
    if len(args) < 2:
        return await m.reply_text(
            "❌ **Usage:**\n"
            "`/sudo promote` - Reply to a user to promote\n"
            "`/sudo demote` - Reply to a user to demote\n"
            "Or: `/sudo promote @username` / `/sudo demote @username`",
            parse_mode=ParseMode.MARKDOWN
        )
    
    action = args[1].lower()
    
    if action not in ["promote", "demote"]:
        return await m.reply_text("❌ Use `promote` or `demote`", parse_mode=ParseMode.MARKDOWN)
    
    # Get target user
    target_user = None
    
    if reply:
        target_user = reply.from_user
    else:
        # Try to get from username
        if len(args) >= 3:
            username = args[2].replace("@", "")
            try:
                target_user = await c.get_users(username)
            except:
                return await m.reply_text("❌ User not found!")
    
    if not target_user:
        return await m.reply_text("❌ Please reply to a user or provide username!")
    
    user_id = target_user.id
    username = target_user.username or "No username"
    first_name = target_user.first_name or "Unknown"
    
    # Handle promote
    if action == "promote":
        # Check if already mod
        existing_mod = database.get_mod(user_id)
        if existing_mod:
            return await m.reply_text(f"ℹ️ {first_name} is already a moderator!")
        
        # Add as mod
        success = database.add_mod(
            user_id=user_id,
            username=target_user.username,
            first_name=first_name,
            added_by=m.from_user.id
        )
        
        if success:
            await m.reply_text(
                f"✅ **Promoted to Moderator!**\n\n"
                f"User: {first_name}\n"
                f"ID: `{user_id}`\n"
                f"Username: @{target_user.username if target_user.username else 'N/A'}",
                parse_mode=ParseMode.MARKDOWN
            )
            # Notify
            try:
                await c.send_message(
                    user_id,
                    "🎉 You have been promoted to moderator!"
                )
            except:
                pass
        else:
            await m.reply_text("❌ Failed to promote!")
    
    # Handle demote
    elif action == "demote":
        # Remove mod
        success = database.remove_mod(user_id)
        
        if success:
            await m.reply_text(
                f"✅ **Demoted from Moderator!**\n\n"
                f"User: {first_name}\n"
                f"ID: `{user_id}`",
                parse_mode=ParseMode.MARKDOWN
            )
            # Notify
            try:
                await c.send_message(
                    user_id,
                    "⚠️ You have been demoted from moderator."
                )
            except:
                pass
        else:
            await m.reply_text("❌ Failed to demote or user wasn't a mod!")

@Client.on_message(filters.command("mods"))
async def mods_list_cmd(c, m):
    if getattr(c, 'is_clone', False):
        logging.warning(f"[ADMIN] User {m.from_user.id} attempted /mods on clone instance (blocked)")
        return await m.reply("❌ This command is disabled on cloned instances.")
    if not await is_admin(m.from_user.id):
        return await m.reply("Admin only.")
    """List all moderators"""
    mods = database.get_all_mods()
    
    if not mods:
        return await m.reply_text("📋 No moderators found.")
    
    text = "👥 **Moderators List:**\n\n"
    for i, mod in enumerate(mods, 1):
        user_id = mod.get("user_id")
        username = mod.get("username", "No username")
        first_name = mod.get("first_name", "Unknown")
        added_by = mod.get("added_by", "Unknown")
        
        text += f"{i}. **{first_name}**\n"
        text += f"   ├ ID: `{user_id}`\n"
        text += f"   ├ Username: @{username}\n"
        text += f"   └ Added by: `{added_by}`\n\n"
    
    await m.reply_text(text, parse_mode=ParseMode.MARKDOWN)
