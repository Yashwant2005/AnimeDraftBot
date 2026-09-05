import asyncio
import random
import time
import re
import html
import logging
import unicodedata
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ParseMode
from pyrogram.errors import BadRequest

import database
import config
from utils import is_admin

# Scheduler for timed drops
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

# Cache and in-memory states
_active_char = {}      # chat_id -> character dict
_claimed = {}          # chat_id -> user_id who claimed it
_msg_counts = {}       # chat_id -> count of messages
_last_user = {}        # chat_id -> {"user_id": uid, "count": cnt}
_warned = {}           # user_id -> timestamp
_sent_ids = {}         # chat_id -> list of recently sent char IDs
_pending_chars = {}    # char_id -> char_dict (for approval)
_drop_locks = {}       # (event_loop_id, chat_id) -> asyncio.Lock
_last_drop_at = {}     # chat_id -> timestamp
_registered_chats = set()
_scheduler_started = False
scheduler = AsyncIOScheduler(timezone="UTC")

_XP_PER_GUESS = 50
_MIN_DROP_GAP = 30

# --- HELPERS ---

def _positive_int(value, default: int) -> int:
    try:
        return max(1, int(value))
    except Exception:
        return default

def _chat_drop_settings(chat_id: int) -> dict:
    default = {
        "drop_mode": "messages",
        "message_frequency": config.DEFAULT_MSG_FREQUENCY,
        "time_frequency_min": config.DROP_INTERVAL_MIN,
        "last_timed_drop": 0,
    }
    if not database.USE_MONGO or database.COL_WAIFU_TOTALS is None:
        return default

    doc = database.COL_WAIFU_TOTALS.find_one({"chat_id": chat_id}) or {}
    mode = str(doc.get("drop_mode") or default["drop_mode"]).lower()
    if mode not in ("messages", "time"):
        mode = default["drop_mode"]

    return {
        "drop_mode": mode,
        "message_frequency": _positive_int(doc.get("message_frequency"), config.DEFAULT_MSG_FREQUENCY),
        "time_frequency_min": _positive_int(doc.get("time_frequency_min"), config.DROP_INTERVAL_MIN),
        "last_timed_drop": float(doc.get("last_timed_drop") or 0),
    }

async def _chat_frequency(chat_id: int) -> int:
    return _chat_drop_settings(chat_id)["message_frequency"]

async def _is_group_admin(client: Client, chat_id: int, user_id: int) -> bool:
    try:
        if await is_admin(user_id):
            return True
    except Exception:
        pass
    try:
        member = await client.get_chat_member(chat_id, user_id)
        status = str(member.status).lower()
        return "administrator" in status or "owner" in status or "creator" in status
    except Exception:
        return False

def _parse_time_minutes(raw: str) -> int | None:
    match = re.fullmatch(r"(\d+)([mh])?", raw.lower().replace(",", ""))
    if not match:
        return None
    value = int(match.group(1))
    unit = match.group(2)
    return value * 60 if unit == "h" else value

def _parse_changetime(command: list[str]) -> tuple[str | None, int | None]:
    args = [arg.strip().lower() for arg in command[1:] if arg.strip()]
    if not args:
        return None, None
    if args[0] in ("status", "show", "current"):
        return "status", None

    first = args[0]
    second = args[1] if len(args) > 1 else None
    message_words = {"message", "messages", "msg", "msgs"}
    time_words = {"time", "timer", "minute", "minutes", "min", "mins", "hour", "hours"}

    if first in message_words and second:
        return "messages", _parse_time_minutes(second)
    if first in time_words and second:
        return "time", _parse_time_minutes(second)
    if second in message_words:
        return "messages", _parse_time_minutes(first)
    if second in time_words:
        return "time", _parse_time_minutes(first)

    value = _parse_time_minutes(first)
    if value is None:
        return None, None
    if first.endswith(("m", "h")):
        return "time", value
    return "messages", value

def _changetime_usage() -> str:
    return (
        "Usage:\n"
        "<code>/changetime messages 100</code>\n"
        "<code>/changetime time 10m</code>\n"
        "<code>/changetime 50</code> for message mode\n"
        "<code>/changetime 2h</code> for time mode\n"
        "<code>/changetime status</code>"
    )

def _format_minutes(minutes: int) -> str:
    if minutes % 60 == 0:
        hours = minutes // 60
        return f"{hours} hour{'s' if hours != 1 else ''}"
    return f"{minutes} minute{'s' if minutes != 1 else ''}"

def _rolling_window_size(total_chars: int) -> int:
    return max(20, total_chars // 2)

def _drop_lock(chat_id: int) -> asyncio.Lock:
    key = (id(asyncio.get_running_loop()), chat_id)
    lock = _drop_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _drop_locks[key] = lock
    return lock

def _normalize_guess_text(value) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    without_marks = "".join(c for c in normalized if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", without_marks.lower()).strip()

def _is_correct_name(guess, name) -> bool:
    guess_norm = _normalize_guess_text(guess)
    name_norm = _normalize_guess_text(name)
    if not guess_norm or not name_norm:
        return False
    guess_parts = guess_norm.split()
    name_parts = name_norm.split()
    return (
        guess_norm == name_norm
        or sorted(guess_parts) == sorted(name_parts)
        or any(part == guess_norm for part in name_parts)
    )

def _get_char_anime(char: dict) -> str:
    return char.get("anime") or char.get("series") or "Unknown"

async def _send_drop(chat_id: int, client: Client) -> bool:
    async with _drop_lock(chat_id):
        if _active_char.get(chat_id):
            logging.debug(f"[DROP] Skipped drop in chat {chat_id}; active character already exists.")
            return False

        now = time.time()
        if now - _last_drop_at.get(chat_id, 0) < _MIN_DROP_GAP:
            logging.debug(f"[DROP] Skipped duplicate drop in chat {chat_id}; too soon after last drop.")
            return False

        if not database.USE_MONGO or database.COL_WAIFUS is None:
            logging.warning("[DROP] MongoDB not ready for drop.")
            return False

        all_chars = list(database.COL_WAIFUS.find({}))
        if not all_chars:
            logging.debug(f"[DROP] No characters in DB for drop in chat {chat_id}")
            return False

        window = _rolling_window_size(len(all_chars))
        sent = _sent_ids.get(chat_id, [])
        unsent = [c for c in all_chars if c.get("id") not in sent]

        if not unsent:
            _sent_ids[chat_id] = []
            sent = []
            unsent = all_chars
            logging.debug(f"[DROP] Sent window reset for chat {chat_id}")

        char = random.choice(unsent)
        _sent_ids[chat_id] = (sent + [char.get("id")])[-window:]

        _active_char[chat_id] = char
        _claimed.pop(chat_id, None)

        try:
            caption = (
                "✨ <b>A new character appeared!</b>\n\n"
                "<i>Use /guess [name] to add them to your harem!</i>"
            )
            img_url = char.get("img_url") or char.get("img")
            await client.send_photo(
                chat_id=chat_id,
                photo=img_url,
                caption=caption,
                parse_mode=ParseMode.HTML
            )
            _last_drop_at[chat_id] = now
            logging.info(f"[DROP] Sent character {char['name']} to chat {chat_id}")
            return True
        except Exception as e:
            _active_char.pop(chat_id, None)
            logging.warning(f"[DROP] Failed sending drop to {chat_id}: {e}")
            return False

async def _timed_drop_job(client: Client) -> None:
    now = time.time()
    for chat_id in list(_registered_chats):
        settings = _chat_drop_settings(chat_id)
        if settings["drop_mode"] != "time":
            continue
        interval_seconds = settings["time_frequency_min"] * 60
        if now - settings.get("last_timed_drop", 0) < interval_seconds:
            continue
        sent = await _send_drop(chat_id, client)
        if sent and database.USE_MONGO and database.COL_WAIFU_TOTALS is not None:
            database.COL_WAIFU_TOTALS.update_one(
                {"chat_id": chat_id},
                {"$set": {"last_timed_drop": now}},
                upsert=True,
            )

def start_drop_scheduler(client: Client) -> None:
    global _scheduler_started
    if _scheduler_started:
        return
    scheduler.add_job(
        _timed_drop_job,
        trigger=IntervalTrigger(minutes=1),
        kwargs={"client": client},
        id="timed_drop",
        replace_existing=True,
    )
    scheduler.start()
    _scheduler_started = True
    logging.info("[DROP] Scheduler started for timed chat drops.")

# --- HANDLERS ---

@Client.on_message(filters.command("changetime") & filters.group)
async def changetime_command(client, message):
    user = message.from_user
    if not user:
        return

    if not database.USE_MONGO or database.COL_WAIFU_TOTALS is None:
        await message.reply_text("❌ Database not ready.")
        return

    if not await _is_group_admin(client, message.chat.id, user.id):
        await message.reply_text("❌ Only group admins can change waifu drop timing.")
        return

    mode, value = _parse_changetime(message.command or [])
    if mode == "status":
        settings = _chat_drop_settings(message.chat.id)
        if settings["drop_mode"] == "time":
            detail = f"every {_format_minutes(settings['time_frequency_min'])}"
        else:
            detail = f"every {settings['message_frequency']:,} messages"
        await message.reply_text(
            f"⏱ <b>Waifu Drop Timing</b>\n\n"
            f"Current mode: <b>{settings['drop_mode']}</b>\n"
            f"Drops: <b>{detail}</b>",
            parse_mode=ParseMode.HTML
        )
        return

    if mode not in ("messages", "time") or not value:
        await message.reply_text(_changetime_usage(), parse_mode=ParseMode.HTML)
        return

    now = time.time()
    update = {
        "chat_id": message.chat.id,
        "chat_title": message.chat.title,
        "drop_mode": mode,
        "updated_by": user.id,
        "updated_at": now,
    }
    if mode == "messages":
        update["message_frequency"] = value
        _msg_counts[message.chat.id] = 0
        detail = f"after every {value:,} messages"
    else:
        update["time_frequency_min"] = value
        update["last_timed_drop"] = now
        _registered_chats.add(message.chat.id)
        start_drop_scheduler(client)
        detail = f"every {_format_minutes(value)}"

    database.COL_WAIFU_TOTALS.update_one(
        {"chat_id": message.chat.id},
        {"$set": update},
        upsert=True
    )

    await message.reply_text(
        f"✅ Waifu drops will now appear <b>{detail}</b>.",
        parse_mode=ParseMode.HTML
    )

@Client.on_message(filters.text & filters.group, group=1)
async def message_counter_handler(client, message):
    chat_id = message.chat.id
    user_id = message.from_user.id if message.from_user else 0
    if not user_id:
        return

    _registered_chats.add(chat_id)
    start_drop_scheduler(client)

    # Anti-spam: 10 consecutive messages -> warning & ignore
    last = _last_user.get(chat_id)
    if last and last["user_id"] == user_id:
        last["count"] += 1
        if last["count"] >= 10:
            warned_at = _warned.get(user_id, 0)
            if time.time() - warned_at < 600:
                message.continue_propagation()
                return
            _warned[user_id] = time.time()
            try:
                await message.reply_text(
                    f"⚠️ {html.escape(message.from_user.first_name)}, slow down!\n"
                    f"Your messages will be ignored for 10 minutes."
                )
            except Exception:
                pass
            message.continue_propagation()
            return
    else:
        _last_user[chat_id] = {"user_id": user_id, "count": 1}

    settings = _chat_drop_settings(chat_id)
    if settings["drop_mode"] == "messages":
        _msg_counts[chat_id] = _msg_counts.get(chat_id, 0) + 1
        freq = await _chat_frequency(chat_id)
        if _msg_counts[chat_id] >= freq:
            _msg_counts[chat_id] = 0
            await _send_drop(chat_id, client)

    message.continue_propagation()

@Client.on_message(filters.command(["guess", "grab", "collect", "protecc", "hunt"]))
async def guess_handler(client, message):
    chat_id = message.chat.id
    user = message.from_user
    if not user:
        return

    char = _active_char.get(chat_id)
    if not char:
        return

    if chat_id in _claimed:
        await message.reply_text("❌ Already claimed by someone else! Wait for the next character.")
        return

    user_guess = " ".join(message.command[1:]).strip() if len(message.command) > 1 else ""
    if not user_guess:
        await message.reply_text("Usage: /guess [character name]")
        return

    # Anti-malicious inputs
    user_guess_check = user_guess.lower()
    if any(bad in user_guess_check for bad in ("()", "&&", "||", "<script")):
        await message.reply_text("❌ Invalid characters in guess.")
        return

    correct = _is_correct_name(user_guess, char["name"])

    if not correct:
        await message.reply_text("❌ Wrong name, try again!")
        return

    # Claim the character
    _claimed[chat_id] = user.id
    _active_char.pop(chat_id, None)

    # Persist in user collection
    if database.USE_MONGO and database.COL_WAIFU_USERS is not None:
        database.COL_WAIFU_USERS.update_one(
            {"id": user.id},
            {
                "$push": {"characters": char},
                "$inc": {"total_guesses": 1, "xp": _XP_PER_GUESS, "coins": 100},
                "$set": {
                    "username": user.username,
                    "first_name": user.first_name,
                    "bounty": database.COL_WAIFU_USERS.find_one({"id": user.id}, {"coins": 1}).get("coins", 0) + 100 if database.COL_WAIFU_USERS.find_one({"id": user.id}) else 100
                },
                "$setOnInsert": {
                    "wins": 0,
                    "favorites": [],
                }
            },
            upsert=True
        )

        # Increment group counts
        database.COL_WAIFU_GROUP_USERS.update_one(
            {"user_id": user.id, "group_id": chat_id},
            {"$set": {"username": user.username, "first_name": user.first_name}, "$inc": {"count": 1}},
            upsert=True
        )
        database.COL_WAIFU_TOP_GROUPS.update_one(
            {"group_id": chat_id},
            {"$set": {"group_name": message.chat.title}, "$inc": {"count": 1}},
            upsert=True
        )

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("📖 My Harem", switch_inline_query_current_chat=f"collection.{user.id}")
    ]])

    await message.reply_text(
        f'🎉 <a href="tg://user?id={user.id}">{html.escape(user.first_name)}</a> guessed it!\n\n'
        f'🌸 <b>{html.escape(char["name"])}</b>\n'
        f'📺 {html.escape(_get_char_anime(char))}\n'
        f'💎 {char.get("rarity", "⚪ Common")}\n\n'
        f'Added to your harem! +100 Coins 🪙 +{_XP_PER_GUESS} XP ✨',
        parse_mode=ParseMode.HTML,
        reply_markup=kb
    )

@Client.on_message(filters.command("fav"))
async def fav_handler(client, message):
    user = message.from_user
    if not user:
        return
    if len(message.command) < 2:
        await message.reply_text("Usage: /fav <character_id>")
        return

    char_id = message.command[1].strip()
    if not database.USE_MONGO or database.COL_WAIFU_USERS is None:
        await message.reply_text("❌ Database not ready.")
        return

    user_doc = database.COL_WAIFU_USERS.find_one({"id": user.id})
    if not user_doc or not user_doc.get("characters"):
        await message.reply_text("You haven't guessed any characters yet.")
        return

    char = next((c for c in user_doc["characters"] if c["id"] == char_id), None)
    if not char:
        await message.reply_text("That character isn't in your collection.")
        return

    database.COL_WAIFU_USERS.update_one({"id": user.id}, {"$set": {"favorites": [char_id]}})
    await message.reply_text(f"⭐ <b>{html.escape(char['name'])}</b> set as your favourite!", parse_mode=ParseMode.HTML)

# --- UPLOAD / MANAGE COMMANDS (SUDO ONLY) ---

async def _next_id() -> str:
    # Auto incrementing IDs
    doc = database.MONGO_DB.sequences.find_one_and_update(
        {"_id": "character_id"},
        {"$inc": {"sequence_value": 1}},
        upsert=True,
        return_document=True
    )
    return str(doc["sequence_value"]).zfill(4)

def _char_caption(char: dict, uploader_id: int, uploader_name: str) -> str:
    return (
        f"🍀 <b>Name:</b> {char['name']}\n"
        f"🍋 <b>Rarity:</b> {char['rarity']}\n"
        f"🌸 <b>Anime:</b> {char['anime']}\n"
        f"🌱 <b>ID:</b> {char['id']}\n\n"
        f"Added by <a href='tg://user?id={uploader_id}'>{uploader_name}</a>"
    )

async def _send_pending_character_for_approval(client, message, char: dict, photo) -> bool:
    submitter = message.from_user
    char["submitted_by"] = {
        "id": submitter.id,
        "name": submitter.first_name,
    }
    _pending_chars[char["id"]] = char
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"char_approve_{char['id']}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"char_reject_{char['id']}")
        ]
    ])
    try:
        await client.send_photo(
            chat_id=config.CHAR_APPROVER_ID,
            photo=photo,
            caption=(
                _char_caption(char, submitter.id, submitter.first_name)
                + "\n\n⚠️ <b>Pending Admin Approval</b>"
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=kb
        )
    except Exception as exc:
        _pending_chars.pop(char["id"], None)
        await message.reply_text(f"❌ Could not send approval request to owner DM: {exc}")
        return False

    await message.reply_text(
        f"⏳ Your character <b>{html.escape(char['name'])}</b> was sent for approval. "
        f"ID: <code>{char['id']}</code>",
        parse_mode=ParseMode.HTML
    )
    return True

@Client.on_message(filters.command("upload"))
async def upload_command(client, message):
    is_user_admin = await is_admin(message.from_user.id)

    args = message.command[1:]
    if len(args) < 4:
        await message.reply_text(
            "❌ Format: /upload IMG_URL character-name anime-name rarity_number\n"
            "Example: /upload https://link.to/img Luffy One-Piece 4\n"
            "Rarity mapping: 1=Common, 2=Medium, 3=Rare, 4=Legendary, 5=Special Edition"
        )
        return

    img_url = args[0]
    raw_rarity = args[-1]
    try:
        rarity_num = int(raw_rarity)
        rarity = config.RARITY_MAP[rarity_num]
    except Exception:
        await message.reply_text("❌ Invalid rarity number. Must be 1 to 5.")
        return

    name_anime = args[1:-1]
    if len(name_anime) >= 2:
        name = name_anime[0].replace("-", " ").title()
        anime = name_anime[1].replace("-", " ").title()
    else:
        name = name_anime[0].replace("-", " ").title()
        anime = "Unknown"

    char_id = await _next_id()
    char = {
        "img_url": img_url,
        "name": name,
        "anime": anime,
        "rarity": rarity,
        "id": char_id
    }

    try:
        if is_user_admin:
            msg = await client.send_photo(
                chat_id=config.CHARA_CHANNEL_ID,
                photo=img_url,
                caption=_char_caption(char, message.from_user.id, message.from_user.first_name),
                parse_mode=ParseMode.HTML
            )
            char["message_id"] = msg.id
            database.COL_WAIFUS.insert_one(char)
            await message.reply_text(f"✅ <b>{name}</b> added! ID: <code>{char_id}</code>", parse_mode=ParseMode.HTML)
        else:
            await _send_pending_character_for_approval(client, message, char, img_url)
    except Exception as e:
        await message.reply_text(f"❌ Failed to post/save character: {e}")

@Client.on_message(filters.command("uploadchar"))
async def uploadchar_command(client, message):
    is_user_admin = await is_admin(message.from_user.id)

    replied = message.reply_to_message
    if not replied or not (replied.photo or (replied.document and replied.document.mime_type.startswith("image/"))):
        await message.reply_text("❌ Reply to an image/photo message with caption details.")
        return

    photo_file_id = replied.photo.file_id if replied.photo else replied.document.file_id
    caption = replied.caption or ""

    # Parse caption fields
    name_match = re.search(r"Name\s*:\s*(.+)", caption, re.IGNORECASE)
    anime_match = re.search(r"Anime\s*:\s*(.+)", caption, re.IGNORECASE)
    rarity_match = re.search(r"Rarity\s*:\s*(.+)", caption, re.IGNORECASE)
    id_match = re.search(r"ID\s*:\s*(\S+)", caption, re.IGNORECASE)

    if not name_match or not anime_match:
        await message.reply_text("❌ Caption must contain Name: and Anime: fields.")
        return

    name = name_match.group(1).strip().title()
    anime = anime_match.group(1).strip().title()
    
    rarity_str = rarity_match.group(1).strip().lower() if rarity_match else "common"
    rarity = "Common"
    for num, map_val in config.RARITY_MAP.items():
        if rarity_str in map_val.lower():
            rarity = map_val
            break

    char_id = id_match.group(1).strip() if id_match else await _next_id()

    existing = database.COL_WAIFUS.find_one({"id": char_id})
    if existing:
        await message.reply_text(f"❌ ID {char_id} already exists in DB.")
        return

    char = {
        "img_url": photo_file_id,
        "name": name,
        "anime": anime,
        "rarity": rarity,
        "id": char_id
    }

    try:
        if is_user_admin:
            msg = await client.send_photo(
                chat_id=config.CHARA_CHANNEL_ID,
                photo=photo_file_id,
                caption=_char_caption(char, message.from_user.id, message.from_user.first_name),
                parse_mode=ParseMode.HTML
            )
            char["message_id"] = msg.id
            database.COL_WAIFUS.insert_one(char)
            await message.reply_text(f"✅ <b>{name}</b> added via reply! ID: <code>{char_id}</code>", parse_mode=ParseMode.HTML)
        else:
            await _send_pending_character_for_approval(client, message, char, photo_file_id)
    except Exception as e:
        await message.reply_text(f"❌ Upload failed: {e}")

@Client.on_callback_query(filters.regex(r"^char_(approve|reject)_(.+)$"))
async def char_approval_cb(client, callback_query):
    if callback_query.from_user.id != config.CHAR_APPROVER_ID:
        return await callback_query.answer("❌ Only the character approver can use this.", show_alert=True)

    action = callback_query.matches[0].group(1)
    char_id = callback_query.matches[0].group(2)

    char = _pending_chars.get(char_id)
    if not char:
        return await callback_query.answer("❌ Character not found or already processed.", show_alert=True)

    if action == "approve":
        try:
            submitter = char.get("submitted_by") or {}
            uploader_id = submitter.get("id", callback_query.from_user.id)
            uploader_name = submitter.get("name", callback_query.from_user.first_name)
            db_char = dict(char)
            db_char.pop("submitted_by", None)
            # Send to chara channel
            msg = await client.send_photo(
                chat_id=config.CHARA_CHANNEL_ID,
                photo=db_char["img_url"],
                caption=_char_caption(db_char, uploader_id, uploader_name),
                parse_mode=ParseMode.HTML
            )
            db_char["message_id"] = msg.id
            database.COL_WAIFUS.insert_one(db_char)
            del _pending_chars[char_id]

            await callback_query.edit_message_caption(
                caption=(
                    _char_caption(db_char, uploader_id, uploader_name)
                    + f"\n\n✅ <b>Approved by {html.escape(callback_query.from_user.first_name)}</b>"
                ),
                parse_mode=ParseMode.HTML
            )
            await callback_query.answer("✅ Character approved and saved!")
        except Exception as e:
            await callback_query.answer(f"❌ Error: {e}", show_alert=True)
    elif action == "reject":
        del _pending_chars[char_id]
        await callback_query.edit_message_caption(
            caption=(
                callback_query.message.caption
                + f"\n\n❌ <b>Rejected by {html.escape(callback_query.from_user.first_name)}</b>"
            ),
            parse_mode=ParseMode.HTML
        )
        await callback_query.answer("❌ Character rejected.")

@Client.on_message(filters.command("deletechar"))
async def deletechar_command(client, message):
    if not await is_admin(message.from_user.id):
        await message.reply_text("❌ Sudo only.")
        return

    if len(message.command) < 2:
        await message.reply_text("Usage: /deletechar <ID>")
        return

    char_id = message.command[1].strip()
    char = database.COL_WAIFUS.find_one_and_delete({"id": char_id})
    if not char:
        await message.reply_text("❌ Character not found.")
        return

    if char.get("message_id"):
        try:
            await client.delete_messages(config.CHARA_CHANNEL_ID, char["message_id"])
        except Exception:
            pass
    await message.reply_text(f"✅ Deleted <b>{char['name']}</b> (<code>{char_id}</code>)", parse_mode=ParseMode.HTML)
