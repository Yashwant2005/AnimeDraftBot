import os
import random
import time
import html
import logging
import asyncio
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.enums import ParseMode

import database
import config

# Try importing wantedposter, fallback if not installed/fails
try:
    from wantedposter.wantedposter import WantedPoster, VerticalAlignment, CaptureCondition, Effect, Stamp
    WANTEDPOSTER_AVAILABLE = True
except ImportError:
    WANTEDPOSTER_AVAILABLE = False

async def _create_wanted_poster(client, target_id, first_name, bounty):
    if not WANTEDPOSTER_AVAILABLE:
        return None, []

    cleanup_paths = []
    portrait_path = None
    try:
        async for photo in client.get_chat_photos(target_id, limit=1):
            portrait_path = await client.download_media(photo.file_id)
            break
    except Exception as exc:
        logging.warning(f"[POSTER] Failed to download profile photo: {exc}")

    if portrait_path and os.path.exists(portrait_path):
        cleanup_paths.append(portrait_path)
    else:
        portrait_path = None

    output_path = os.path.join(config.DATA_DIR, f"wanted_{target_id}_{int(time.time())}.jpg")
    cleanup_paths.append(output_path)

    try:
        wanted_poster = WantedPoster(
            portrait=portrait_path,
            first_name=first_name,
            last_name="",
            bounty=bounty
        )
        await asyncio.to_thread(
            wanted_poster.generate,
            output_poster_path=output_path,
            portrait_vertical_align=VerticalAlignment.TOP,
            capture_condition=CaptureCondition.DEAD_OR_ALIVE,
            effects=[],
            stamp=None
        )
        if os.path.exists(output_path):
            return output_path, cleanup_paths
    except Exception as exc:
        logging.error(f"[POSTER] Generation failed: {exc}")

    return None, cleanup_paths

def _cleanup_files(paths):
    for path in paths:
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass

@Client.on_message(filters.command("bounty"))
async def bounty_command(client, message):
    user = message.from_user
    target = message.reply_to_message.from_user if message.reply_to_message else user
    if not target:
        return

    if not database.USE_MONGO or database.COL_WAIFU_USERS is None:
        await message.reply_text("❌ Database not ready.")
        return

    doc = database.COL_WAIFU_USERS.find_one({"id": target.id})
    stats = database.get_user_stats(target.id, target.first_name)

    bounty = doc.get("coins", 0) if doc else 0
    wins = stats.get("wins", 0)
    matches = stats.get("matches", 0)
    rating = stats.get("rating", 1200)

    txt = (
        f"🏴‍☠️ <b>Bounty Profile: {html.escape(target.first_name)}</b>\n\n"
        f"💰 <b>Bounty:</b> {bounty:,} 🪙\n"
        f"💠 <b>Rank Points:</b> {rating} pts\n"
        f"⚔️ <b>Draft Matches:</b> {matches} (Wins: {wins})\n"
    )

    poster_path, cleanup_paths = await _create_wanted_poster(client, target.id, target.first_name or "Pirate", bounty)
    try:
        if poster_path:
            await message.reply_photo(
                photo=poster_path,
                caption=txt,
                parse_mode=ParseMode.HTML
            )
        else:
            await message.reply_text(txt, parse_mode=ParseMode.HTML)
    finally:
        _cleanup_files(cleanup_paths)

@Client.on_callback_query(filters.regex(r"^poster:\d+$"))
async def poster_callback(client, callback_query: CallbackQuery):
    target_id = int(callback_query.data.split(":")[1])
    await callback_query.answer("Generating Wanted Poster...", show_alert=False)

    if not database.USE_MONGO or database.COL_WAIFU_USERS is None:
        return

    doc = database.COL_WAIFU_USERS.find_one({"id": target_id})
    bounty = doc.get("coins", 0) if doc else 0
    first_name = doc.get("first_name", "Pirate") if doc else "Pirate"
    poster_path, cleanup_paths = await _create_wanted_poster(client, target_id, first_name, bounty)

    try:
        if not poster_path:
            return await callback_query.message.reply_text("❌ Wanted poster is not available right now.")
        await callback_query.message.reply_photo(
            photo=poster_path,
            caption=(
                f"🏴‍☠️ <b>Bounty Profile: {html.escape(first_name)}</b>\n\n"
                f"💰 <b>Bounty:</b> {bounty:,} 🪙"
            ),
            parse_mode=ParseMode.HTML
        )
    finally:
        _cleanup_files(cleanup_paths)

@Client.on_message(filters.command("fight") & filters.group)
async def fight_command(client, message):
    if not message.reply_to_message:
        await message.reply_text("Reply to a user to fight them!\nUsage: /fight (reply)")
        return

    a = message.from_user
    b = message.reply_to_message.from_user
    if not a or not b:
        return

    if a.id == b.id:
        await message.reply_text("❌ You cannot fight yourself!")
        return
    if b.is_bot:
        await message.reply_text("❌ You cannot fight a bot!")
        return

    if not database.USE_MONGO or database.COL_WAIFU_USERS is None:
        await message.reply_text("❌ Database not ready.")
        return

    a_doc = database.COL_WAIFU_USERS.find_one({"id": a.id})
    b_doc = database.COL_WAIFU_USERS.find_one({"id": b.id})

    a_bounty = a_doc.get("coins", 0) if a_doc else 0
    b_bounty = b_doc.get("coins", 0) if b_doc else 0

    if a_bounty < 100:
        await message.reply_text("❌ You need at least 100 bounty to start a fight!")
        return
    if b_bounty < 100:
        await message.reply_text(f"❌ {html.escape(b.first_name)} doesn't have enough bounty to fight!")
        return

    # Deterministic winner with random variance
    win_chance = 0.5
    # Let ELO rating influence win rate slightly
    stats_a = database.get_user_stats(a.id, a.first_name)
    stats_b = database.get_user_stats(b.id, b.first_name)
    rating_diff = stats_a.get("rating", 1200) - stats_b.get("rating", 1200)
    win_chance += max(-0.2, min(0.2, rating_diff / 1000))

    winner_is_a = random.random() < win_chance
    wager = int(min(a_bounty, b_bounty) * 0.10)

    if winner_is_a:
        database.COL_WAIFU_USERS.update_one({"id": a.id}, {"$inc": {"coins": wager, "bounty": wager}})
        database.COL_WAIFU_USERS.update_one({"id": b.id}, {"$inc": {"coins": -wager, "bounty": -wager}})
        await message.reply_text(
            f"⚔️ <b>{html.escape(a.first_name)}</b> defeated <b>{html.escape(b.first_name)}</b> in combat!\n"
            f"🏆 Winner takes 10% of the bounty: <b>+{wager:,} coins</b> 🪙"
        )
    else:
        database.COL_WAIFU_USERS.update_one({"id": a.id}, {"$inc": {"coins": -wager, "bounty": -wager}})
        database.COL_WAIFU_USERS.update_one({"id": b.id}, {"$inc": {"coins": wager, "bounty": wager}})
        await message.reply_text(
            f"⚔️ <b>{html.escape(b.first_name)}</b> defeated <b>{html.escape(a.first_name)}</b> in combat!\n"
            f"🏆 Winner takes 10% of the bounty: <b>+{wager:,} coins</b> 🪙"
        )

@Client.on_message(filters.command("plunder") & filters.group)
async def plunder_command(client, message):
    if not message.reply_to_message:
        await message.reply_text("Reply to a user to plunder them!\nUsage: /plunder (reply)")
        return

    a = message.from_user
    b = message.reply_to_message.from_user
    if not a or not b:
        return

    if a.id == b.id:
        await message.reply_text("❌ You cannot plunder yourself!")
        return

    if not database.USE_MONGO or database.COL_WAIFU_USERS is None:
        await message.reply_text("❌ Database not ready.")
        return

    a_doc = database.COL_WAIFU_USERS.find_one({"id": a.id})
    b_doc = database.COL_WAIFU_USERS.find_one({"id": b.id})

    a_bounty = a_doc.get("coins", 0) if a_doc else 0
    b_bounty = b_doc.get("coins", 0) if b_doc else 0

    if a_bounty < 200:
        await message.reply_text("❌ You need at least 200 bounty to start a plunder!")
        return
    if b_bounty < 200:
        await message.reply_text(f"❌ {html.escape(b.first_name)} doesn't have enough bounty to plunder!")
        return

    # Plunder has high risk: 40% success rate, but steals 20% on success, loses 15% on fail
    success = random.random() < 0.40

    if success:
        stolen = int(b_bounty * 0.20)
        database.COL_WAIFU_USERS.update_one({"id": a.id}, {"$inc": {"coins": stolen, "bounty": stolen}})
        database.COL_WAIFU_USERS.update_one({"id": b.id}, {"$inc": {"coins": -stolen, "bounty": -stolen}})
        await message.reply_text(
            f"🏴‍☠️ <b>{html.escape(a.first_name)}</b> successfully plundered <b>{html.escape(b.first_name)}</b>!\n"
            f"💰 Stole 20% of their bounty: <b>+{stolen:,} coins</b> 🪙"
        )
    else:
        penalty = int(a_bounty * 0.15)
        database.COL_WAIFU_USERS.update_one({"id": a.id}, {"$inc": {"coins": -penalty, "bounty": -penalty}})
        database.COL_WAIFU_USERS.update_one({"id": b.id}, {"$inc": {"coins": penalty, "bounty": penalty}})
        await message.reply_text(
            f"🛡️ <b>{html.escape(a.first_name)}</b>'s plunder failed! <b>{html.escape(b.first_name)}</b> defended successfully.\n"
            f"💸 Plunderer pays 15% penalty: <b>-{penalty:,} coins</b> 🪙"
        )
