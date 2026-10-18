import asyncio
import time
import html
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.enums import ParseMode

import database

_trades = {}
_gifts = {}
_EXPIRY = 300

async def _expire(store: dict, key: str) -> None:
    await asyncio.sleep(_EXPIRY)
    store.pop(key, None)

@Client.on_message(filters.command("trade") & filters.group)
async def trade_command(client, message):
    if not message.reply_to_message:
        await message.reply_text(
            "Reply to a user's message to trade.\n"
            "Usage: <code>/trade YOUR_CHAR_ID THEIR_CHAR_ID</code>",
            parse_mode=ParseMode.HTML
        )
        return

    if len(message.command) < 3:
        await message.reply_text(
            "Usage: <code>/trade YOUR_CHAR_ID THEIR_CHAR_ID</code>",
            parse_mode=ParseMode.HTML
        )
        return

    a = message.from_user
    b = message.reply_to_message.from_user
    if not a or not b:
        return

    if a.id == b.id:
        await message.reply_text("❌ Can't trade with yourself!")
        return

    char_a_id = message.command[1].strip()
    char_b_id = message.command[2].strip()

    if not database.USE_MONGO or database.COL_WAIFU_USERS is None:
        await message.reply_text("❌ Database not ready.")
        return

    a_doc = database.COL_WAIFU_USERS.find_one({"id": a.id})
    b_doc = database.COL_WAIFU_USERS.find_one({"id": b.id})

    a_char = next((c for c in (a_doc or {}).get("characters", []) if c["id"] == char_a_id), None)
    b_char = next((c for c in (b_doc or {}).get("characters", []) if c["id"] == char_b_id), None)

    if not a_char:
        await message.reply_text("❌ You don't own that character.")
        return
    if not b_char:
        await message.reply_text("❌ They don't own that character.")
        return

    tid = f"tr_{a.id}_{b.id}_{int(time.time())}"
    _trades[tid] = {
        "a_id": a.id, "a_name": a.first_name, "a_char": a_char,
        "b_id": b.id, "b_name": b.first_name, "b_char": b_char,
    }
    asyncio.create_task(_expire(_trades, tid))

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Accept", callback_data=f"trade_yes:{tid}"),
        InlineKeyboardButton("❌ Reject", callback_data=f"trade_no:{tid}"),
    ]])

    import rich_ui
    card = rich_ui.build_rich_card(
        title="🔄 Trade Proposal",
        badges=["Trade", f"Target: {b.first_name}"],
        description=f"{a.first_name} offers {a_char['name']} for {b_char['name']} from {b.first_name}.",
        table_data=[
            ("Offered", a_char["name"]),
            ("Requested", b_char["name"])
        ],
        footer=f"{b.first_name}, do you accept?"
    )
    await rich_ui.send_rich_message(
        client, message.chat.id, card, reply_markup=kb
    )

@Client.on_callback_query(filters.regex(r"^trade_(yes|no):"))
async def trade_callback(client, callback_query: CallbackQuery):
    action, tid = callback_query.data.split(":", 1)
    state = _trades.pop(tid, None)
    if not state:
        await callback_query.answer("⌛ Trade expired!", show_alert=True)
        return
    if callback_query.from_user.id != state["b_id"]:
        await callback_query.answer("❌ Not your trade!", show_alert=True)
        return

    await callback_query.answer()
    if action == "trade_no":
        await callback_query.edit_message_text("❌ Trade declined.")
        return

    a_id, b_id = state["a_id"], state["b_id"]
    a_char, b_char = state["a_char"], state["b_char"]

    # Atomically swap characters
    database.COL_WAIFU_USERS.update_one({"id": a_id}, {"$pull": {"characters": {"id": a_char["id"]}}})
    database.COL_WAIFU_USERS.update_one({"id": b_id}, {"$pull": {"characters": {"id": b_char["id"]}}})
    database.COL_WAIFU_USERS.update_one({"id": a_id}, {"$push": {"characters": b_char}})
    database.COL_WAIFU_USERS.update_one({"id": b_id}, {"$push": {"characters": a_char}})

    await callback_query.edit_message_text(
        f"✅ Trade complete!\n\n"
        f"<a href='tg://user?id={a_id}'>{html.escape(state['a_name'])}</a> ← <b>{html.escape(b_char['name'])}</b>\n"
        f"<a href='tg://user?id={b_id}'>{html.escape(state['b_name'])}</a> ← <b>{html.escape(a_char['name'])}</b>",
        parse_mode=ParseMode.HTML,
    )

@Client.on_message(filters.command("gift") & filters.group)
async def gift_command(client, message):
    if not message.reply_to_message:
        await message.reply_text("Reply to a user. Usage: <code>/gift CHAR_ID</code>", parse_mode=ParseMode.HTML)
        return
    if len(message.command) < 2:
        await message.reply_text("Usage: <code>/gift CHAR_ID</code>", parse_mode=ParseMode.HTML)
        return

    a = message.from_user
    b = message.reply_to_message.from_user
    if not a or not b:
        return

    if a.id == b.id:
        await message.reply_text("❌ Can't gift to yourself!")
        return

    char_id = message.command[1].strip()

    if not database.USE_MONGO or database.COL_WAIFU_USERS is None:
        await message.reply_text("❌ Database not ready.")
        return

    a_doc = database.COL_WAIFU_USERS.find_one({"id": a.id})
    char = next((c for c in (a_doc or {}).get("characters", []) if c["id"] == char_id), None)
    if not char:
        await message.reply_text("❌ That character isn't in your collection.")
        return

    gid = f"gi_{a.id}_{b.id}_{int(time.time())}"
    _gifts[gid] = {
        "a_id": a.id, "a_name": a.first_name,
        "b_id": b.id, "b_name": b.first_name, "b_username": b.username,
        "char": char,
    }
    asyncio.create_task(_expire(_gifts, gid))

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🎁 Confirm", callback_data=f"gift_yes:{gid}"),
        InlineKeyboardButton("❌ Cancel", callback_data=f"gift_no:{gid}"),
    ]])

    import rich_ui
    card = rich_ui.build_rich_card(
        title="🎁 Character Gift",
        badges=["Gift", f"To: {b.first_name}"],
        description=f"Gift {char['name']} to {b.first_name}?",
        table_data=[
            ("Character", char["name"]),
            ("Recipient", b.first_name)
        ]
    )
    await rich_ui.send_rich_message(
        client, message.chat.id, card, reply_markup=kb
    )

@Client.on_callback_query(filters.regex(r"^gift_(yes|no):"))
async def gift_callback(client, callback_query: CallbackQuery):
    action, gid = callback_query.data.split(":", 1)
    state = _gifts.pop(gid, None)
    if not state:
        await callback_query.answer("⌛ Gift expired!", show_alert=True)
        return
    if callback_query.from_user.id != state["a_id"]:
        await callback_query.answer("❌ Not your gift!", show_alert=True)
        return

    await callback_query.answer()
    if action == "gift_no":
        await callback_query.edit_message_text("❌ Gift cancelled.")
        return

    char = state["char"]
    database.COL_WAIFU_USERS.update_one({"id": state["a_id"]}, {"$pull": {"characters": {"id": char["id"]}}})

    b_doc = database.COL_WAIFU_USERS.find_one({"id": state["b_id"]})
    if b_doc:
        database.COL_WAIFU_USERS.update_one({"id": state["b_id"]}, {"$push": {"characters": char}})
    else:
        database.COL_WAIFU_USERS.insert_one({
            "id": state["b_id"], "username": state["b_username"],
            "first_name": state["b_name"], "characters": [char],
            "coins": 0, "xp": 0, "wins": 0, "total_guesses": 0, "favorites": [],
        })

    await callback_query.edit_message_text(
        f"✅ <b>{html.escape(char['name'])}</b> gifted to "
        f"<a href='tg://user?id={state['b_id']}'>{html.escape(state['b_name'])}</a>!",
        parse_mode=ParseMode.HTML,
    )
