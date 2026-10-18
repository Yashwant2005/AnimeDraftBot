import math
import time
import html
from bson import ObjectId
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.enums import ParseMode

import database
import config
import rich_ui

_DAILY_COOLDOWN = 86400
_PAGE_SIZE = 8
_DISABLED_FILTER = filters.create(lambda _, __, ___: False)

def _fmt_time(secs: int) -> str:
    h, r = divmod(secs, 3600)
    m, s = divmod(r, 60)
    return f"{h}h {m}m {s}s" if h else (f"{m}m {s}s" if m else f"{s}s")

def _ensure_user(user_id: int, user_obj) -> dict:
    doc = database.COL_WAIFU_USERS.find_one({"id": user_id})
    if not doc:
        doc = {
            "id": user_id, "username": user_obj.username,
            "first_name": user_obj.first_name, "characters": [],
            "coins": 0, "xp": 0, "wins": 0,
            "total_guesses": 0, "favorites": [],
            "bounty": 0
        }
        database.COL_WAIFU_USERS.insert_one(doc)
    return doc

@Client.on_message(filters.command("balance"))
async def balance_command(client, message):
    user = message.from_user
    if not user:
        return
    if not database.USE_MONGO or database.COL_WAIFU_USERS is None:
        await message.reply_text("❌ Database not ready.")
        return

    doc = _ensure_user(user.id, user)
    coins = doc.get("coins", 0)

    card = rich_ui.build_rich_card(
        title=f"💰 Wallet Balance: {user.first_name}",
        badges=[f"Coins: {coins:,} 🪙"],
        table_data=[("Balance", f"{coins:,} coins")],
        footer="Use /daily to claim free coins daily!"
    )
    await rich_ui.send_rich_message(client, message.chat.id, card)

@Client.on_message(filters.command("daily"))
async def daily_command(client, message):
    user = message.from_user
    if not user:
        return
    if not database.USE_MONGO or database.COL_WAIFU_USERS is None:
        await message.reply_text("❌ Database not ready.")
        return

    doc = _ensure_user(user.id, user)
    now = time.time()
    last = doc.get("last_daily", 0)

    if now - last < _DAILY_COOLDOWN:
        remaining = int(_DAILY_COOLDOWN - (now - last))
        await message.reply_text(
            f"⏳ Daily already claimed!\nCome back in <b>{_fmt_time(remaining)}</b>.",
            parse_mode=ParseMode.HTML,
        )
        return

    reward = config.DAILY_COINS
    new_coins = doc.get("coins", 0) + reward
    database.COL_WAIFU_USERS.update_one(
        {"id": user.id},
        {"$inc": {"coins": reward}, "$set": {"last_daily": now, "bounty": new_coins}},
    )
    card = rich_ui.build_rich_card(
        title="🎁 Daily Reward Claimed!",
        badges=[f"+{reward:,} Coins", "Daily Streak"],
        table_data=[
            ("Claimed", f"{reward:,} 🪙"),
            ("New Balance", f"{new_coins:,} 🪙")
        ],
        footer="Come back in 24 hours for your next reward!"
    )
    await rich_ui.send_rich_message(client, message.chat.id, card)

@Client.on_message(_DISABLED_FILTER & filters.command("sell"))
async def sell_command(client, message):
    user = message.from_user
    if not user:
        return
    if len(message.command) < 3:
        await message.reply_text("Usage: <code>/sell [char_id] [price]</code>", parse_mode=ParseMode.HTML)
        return

    char_id = message.command[1].strip()
    price_str = message.command[2].strip()

    if not price_str.isdigit() or int(price_str) <= 0:
        await message.reply_text("❌ Price must be a positive number.")
        return
    price = int(price_str)

    if not database.USE_MONGO or database.COL_WAIFU_USERS is None:
        await message.reply_text("❌ Database not ready.")
        return

    doc = database.COL_WAIFU_USERS.find_one({"id": user.id})
    if not doc:
        await message.reply_text("❌ You have no characters.")
        return

    char = next((c for c in doc.get("characters", []) if c["id"] == char_id), None)
    if not char:
        await message.reply_text("❌ That character isn't in your collection.")
        return

    # Escrow while listed
    database.COL_WAIFU_USERS.update_one(
        {"id": user.id},
        {"$pull": {"characters": {"id": char_id}}}
    )
    listing = {
        "seller_id": user.id,
        "seller_name": user.first_name,
        "char_id": char_id,
        "char": char,
        "price": price,
        "listed_at": time.time(),
    }
    result = database.COL_WAIFU_MARKET.insert_one(listing)

    await message.reply_text(
        f"🏪 <b>{html.escape(char['name'])}</b> listed for <b>{price:,} coins</b>!\n"
        f"Listing ID: <code>{result.inserted_id}</code>",
        parse_mode=ParseMode.HTML,
    )

def _build_market_page(page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    total = database.COL_WAIFU_MARKET.count_documents({})
    if total == 0:
        return "🏪 The market is empty right now.", InlineKeyboardMarkup([])

    total_pages = max(1, math.ceil(total / _PAGE_SIZE))
    page = max(0, min(page, total_pages - 1))

    listings = list(database.COL_WAIFU_MARKET.find({}).sort("price", 1).skip(page * _PAGE_SIZE).limit(_PAGE_SIZE))

    lines = [f"🏪 <b>Market</b>  (page {page+1}/{total_pages})\n"]
    for lst in listings:
        char = lst["char"]
        lines.append(
            f"{char.get('rarity','🎴')}  <b>{html.escape(char['name'])}</b>  "
            f"— <b>{lst['price']:,} 🪙</b>\n"
            f"   Seller: {html.escape(lst['seller_name'])}  |  "
            f"<code>/buy {lst['_id']}</code>"
        )

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"market:{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"market:{page+1}"))

    kb = InlineKeyboardMarkup([nav] if nav else [])
    return "\n".join(lines), kb

@Client.on_message(_DISABLED_FILTER & filters.command("market"))
async def market_command(client, message):
    if not database.USE_MONGO or database.COL_WAIFU_MARKET is None:
        await message.reply_text("❌ Database not ready.")
        return

    page = 0
    if len(message.command) > 1 and message.command[1].isdigit():
        page = int(message.command[1]) - 1

    text, kb = _build_market_page(page)
    await message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)

@Client.on_callback_query(_DISABLED_FILTER & filters.regex(r"^market:\d+$"))
async def market_page_callback(client, callback_query: CallbackQuery):
    page = int(callback_query.data.split(":")[1])
    await callback_query.answer()

    text, kb = _build_market_page(page)
    try:
        await callback_query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    except Exception:
        pass

@Client.on_message(_DISABLED_FILTER & filters.command("buy"))
async def buy_command(client, message):
    user = message.from_user
    if not user:
        return
    if len(message.command) < 2:
        await message.reply_text("Usage: <code>/buy [listing_id]</code>", parse_mode=ParseMode.HTML)
        return

    try:
        oid = ObjectId(message.command[1].strip())
    except Exception:
        await message.reply_text("❌ Invalid listing ID.")
        return

    if not database.USE_MONGO or database.COL_WAIFU_MARKET is None:
        await message.reply_text("❌ Database not ready.")
        return

    listing = database.COL_WAIFU_MARKET.find_one({"_id": oid})
    if not listing:
        await message.reply_text("❌ Listing not found or already sold.")
        return
    if listing["seller_id"] == user.id:
        await message.reply_text("❌ You can't buy your own listing.")
        return

    buyer = _ensure_user(user.id, user)
    if buyer.get("coins", 0) < listing["price"]:
        await message.reply_text(f"❌ Not enough coins. Need <b>{listing['price']:,}</b>.", parse_mode=ParseMode.HTML)
        return

    # Atomic exchange
    database.COL_WAIFU_USERS.update_one(
        {"id": user.id},
        {"$inc": {"coins": -listing["price"]}, "$push": {"characters": listing["char"]}}
    )
    database.COL_WAIFU_USERS.update_one(
        {"id": listing["seller_id"]},
        {"$inc": {"coins": listing["price"]}}
    )
    database.COL_WAIFU_MARKET.delete_one({"_id": oid})

    # Update bounty fields for both users
    buyer_doc = database.COL_WAIFU_USERS.find_one({"id": user.id}, {"coins": 1})
    seller_doc = database.COL_WAIFU_USERS.find_one({"id": listing["seller_id"]}, {"coins": 1})
    if buyer_doc:
        database.COL_WAIFU_USERS.update_one({"id": user.id}, {"$set": {"bounty": buyer_doc.get("coins", 0)}})
    if seller_doc:
        database.COL_WAIFU_USERS.update_one({"id": listing["seller_id"]}, {"$set": {"bounty": seller_doc.get("coins", 0)}})

    char = listing["char"]
    await message.reply_text(
        f"✅ You bought <b>{html.escape(char['name'])}</b> for <b>{listing['price']:,} 🪙</b>!",
        parse_mode=ParseMode.HTML,
    )

@Client.on_message(_DISABLED_FILTER & filters.command("delist"))
async def delist_command(client, message):
    user = message.from_user
    if not user:
        return
    if len(message.command) < 2:
        await message.reply_text("Usage: <code>/delist [listing_id]</code>", parse_mode=ParseMode.HTML)
        return

    try:
        oid = ObjectId(message.command[1].strip())
    except Exception:
        await message.reply_text("❌ Invalid listing ID.")
        return

    if not database.USE_MONGO or database.COL_WAIFU_MARKET is None:
        await message.reply_text("❌ Database not ready.")
        return

    listing = database.COL_WAIFU_MARKET.find_one({"_id": oid})
    if not listing:
        await message.reply_text("❌ Listing not found.")
        return
    if listing["seller_id"] != user.id:
        await message.reply_text("❌ That's not your listing.")
        return

    database.COL_WAIFU_MARKET.delete_one({"_id": oid})
    database.COL_WAIFU_USERS.update_one({"id": user.id}, {"$push": {"characters": listing["char"]}})
    await message.reply_text("✅ Listing removed. Character returned to your harem.")
