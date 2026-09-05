import asyncio
import random
import time
import html
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.enums import ParseMode

import database
import config

_pending = {}
_EXPIRY = 120

_RARITY_POWER = {
    "⚪ Common": 1,
    "🟢 Medium": 2,
    "🟣 Rare": 3,
    "🟡 Legendary": 4,
    "💮 Special Edition": 5,
}

def _duel_id(a: int, b: int) -> str:
    return f"duel_{a}_{b}_{int(time.time())}"

async def _expire(did: str) -> None:
    await asyncio.sleep(_EXPIRY)
    _pending.pop(did, None)

def _power(char: dict) -> int:
    base = _RARITY_POWER.get(char.get("rarity", ""), 1)
    return base * random.randint(80, 120)

@Client.on_message(filters.command("duel") & filters.group)
async def duel_command(client, message):
    if not message.reply_to_message:
        await message.reply_text("Reply to a user's message to duel them!\nUsage: /duel (reply)")
        return

    a = message.from_user
    b = message.reply_to_message.from_user
    if not a or not b:
        return

    if a.id == b.id:
        await message.reply_text("❌ You can't duel yourself!")
        return
    if b.is_bot:
        await message.reply_text("❌ You can't duel a bot!")
        return

    if not database.USE_MONGO or database.COL_WAIFU_USERS is None:
        await message.reply_text("❌ Database not ready.")
        return

    a_doc = database.COL_WAIFU_USERS.find_one({"id": a.id})
    b_doc = database.COL_WAIFU_USERS.find_one({"id": b.id})

    if not a_doc or not a_doc.get("characters"):
        await message.reply_text("❌ You need at least one character to duel!")
        return
    if not b_doc or not b_doc.get("characters"):
        await message.reply_text(f"❌ {html.escape(b.first_name)} has no characters yet!", parse_mode=ParseMode.HTML)
        return

    did = _duel_id(a.id, b.id)

    # Pick top 5 unique characters
    unique_a = list({c["id"]: c for c in a_doc["characters"]}.values())[:5]
    unique_b = list({c["id"]: c for c in b_doc["characters"]}.values())[:5]

    _pending[did] = {
        "challenger_id": a.id,
        "challenger_name": a.first_name,
        "opponent_id": b.id,
        "opponent_name": b.first_name,
        "a_chars": unique_a,
        "b_chars": unique_b,
        "a_pick": None,
        "b_pick": None,
    }
    asyncio.create_task(_expire(did))

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"{c.get('rarity','🎴')} {c['name']}",
            callback_data=f"duel_pick_a:{did}:{i}",
        )]
        for i, c in enumerate(unique_a)
    ])

    await message.reply_text(
        f"⚔️ <b>{html.escape(a.first_name)}</b> challenged <b>{html.escape(b.first_name)}</b>!\n\n"
        f"{html.escape(a.first_name)}, pick your fighter:",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )

@Client.on_callback_query(filters.regex(r"^duel_pick_a:"))
async def duel_pick_a_callback(client, callback_query: CallbackQuery):
    parts = callback_query.data.split(":")
    did = parts[1]
    idx = int(parts[2])

    state = _pending.get(did)
    if not state:
        await callback_query.answer("⌛ Duel expired!", show_alert=True)
        return
    if callback_query.from_user.id != state["challenger_id"]:
        await callback_query.answer("❌ Not your pick!", show_alert=True)
        return

    await callback_query.answer()
    state["a_pick"] = state["a_chars"][idx]

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"{c.get('rarity','🎴')} {c['name']}",
            callback_data=f"duel_pick_b:{did}:{i}",
        )]
        for i, c in enumerate(state["b_chars"])
    ])

    await callback_query.edit_message_text(
        f"⚔️ <b>{html.escape(state['challenger_name'])}</b> picked "
        f"<b>{html.escape(state['a_pick']['name'])}</b>!\n\n"
        f"<b>{html.escape(state['opponent_name'])}</b>, choose your fighter:",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )

@Client.on_callback_query(filters.regex(r"^duel_pick_b:"))
async def duel_pick_b_callback(client, callback_query: CallbackQuery):
    parts = callback_query.data.split(":")
    did = parts[1]
    idx = int(parts[2])

    state = _pending.get(did)
    if not state:
        await callback_query.answer("⌛ Duel expired!", show_alert=True)
        return
    if callback_query.from_user.id != state["opponent_id"]:
        await callback_query.answer("❌ Not your pick!", show_alert=True)
        return
    if state["a_pick"] is None:
        await callback_query.answer("⌛ Waiting for challenger…", show_alert=True)
        return

    await callback_query.answer()
    _pending.pop(did, None)

    state["b_pick"] = state["b_chars"][idx]
    a_char = state["a_pick"]
    b_char = state["b_pick"]

    a_power = _power(a_char)
    b_power = _power(b_char)

    if a_power >= b_power:
        winner_id = state["challenger_id"]
        loser_id = state["opponent_id"]
        winner_name = state["challenger_name"]
        loser_name = state["opponent_name"]
        win_char = a_char["name"]
        lose_char = b_char["name"]
    else:
        winner_id = state["opponent_id"]
        loser_id = state["challenger_id"]
        winner_name = state["opponent_name"]
        loser_name = state["challenger_name"]
        win_char = b_char["name"]
        lose_char = a_char["name"]

    # Reward winner & loser
    database.COL_WAIFU_USERS.update_one(
        {"id": winner_id},
        {"$inc": {"coins": config.DUEL_WIN_COINS, "xp": 100, "wins": 1}}
    )
    database.COL_WAIFU_USERS.update_one(
        {"id": loser_id},
        {"$inc": {"coins": config.DUEL_LOSE_COINS, "xp": 25}}
    )

    await callback_query.edit_message_text(
        f"⚔️ <b>Duel Result!</b>\n\n"
        f"🏆 <b>{html.escape(winner_name)}</b> won with <b>{html.escape(win_char)}</b>!\n"
        f"   Power: <b>{max(a_power, b_power)}</b>\n\n"
        f"💀 <b>{html.escape(loser_name)}</b> lost with <b>{html.escape(lose_char)}</b>.\n"
        f"   Power: <b>{min(a_power, b_power)}</b>\n\n"
        f"🏅 Winner earns <b>{config.DUEL_WIN_COINS} 🪙</b> + 100 XP\n"
        f"🎖️ Loser earns <b>{config.DUEL_LOSE_COINS} 🪙</b> + 25 XP",
        parse_mode=ParseMode.HTML,
    )
