import asyncio
import time
import html
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.enums import ParseMode

import database

_gifts = {}
_EXPIRY = 300

async def _expire(store: dict, key: str) -> None:
    await asyncio.sleep(_EXPIRY)
    store.pop(key, None)

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
