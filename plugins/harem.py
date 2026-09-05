import math
import html
from itertools import groupby
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.enums import ParseMode
from pyrogram.errors import MessageNotModified

import database

_PAGE_SIZE = 15

_MEDALS = {
    "⚪ Common": "⚪",
    "🟣 Rare": "🟣",
    "🟡 Legendary": "🟡",
    "🟢 Medium": "🟢",
    "💮 Special Edition": "💮",
}

def _rarity_icon(rarity: str) -> str:
    return _MEDALS.get(rarity, "🎴")

def _anime_totals(animes: list[str]) -> dict[str, int]:
    if not database.USE_MONGO or database.COL_WAIFUS is None:
        return {}
    pipeline = [
        {"$match": {"anime": {"$in": animes}}},
        {"$group": {"_id": "$anime", "n": {"$sum": 1}}},
    ]
    cursor = database.COL_WAIFUS.aggregate(pipeline)
    return {d["_id"]: d["n"] for d in cursor}

def _build_harem_page(user_id: int, page: int) -> tuple[str, InlineKeyboardMarkup | None, str | None]:
    if not database.USE_MONGO or database.COL_WAIFU_USERS is None:
        return "❌ Database not initialized.", None, None

    user = database.COL_WAIFU_USERS.find_one({"id": user_id})
    if not user or not user.get("characters"):
        return (
            "📭 <b>Harem is empty</b>\n\n"
            "Catch a dropped character with <code>/guess name</code> first.",
            None,
            None,
        )

    chars = user["characters"]
    
    # Deduplicate and count occurrences
    id_counts = {}
    for c in chars:
        id_counts[c["id"]] = id_counts.get(c["id"], 0) + 1

    unique = list({c["id"]: c for c in chars}.values())
    unique.sort(key=lambda x: (x.get("anime", "Unknown"), x["id"]))

    total_unique = len(unique)
    total_pages = max(1, math.ceil(total_unique / _PAGE_SIZE))
    page = max(0, min(page, total_pages - 1))

    page_chars = unique[page * _PAGE_SIZE:(page + 1) * _PAGE_SIZE]
    animes = list({c.get("anime", "Unknown") for c in page_chars})
    db_totals = _anime_totals(animes)

    fav_id = None
    favs = user.get("favorites")
    if favs and isinstance(favs, list) and len(favs) > 0:
        fav_id = favs[0]

    lines = [
        f"<b>🌸 {html.escape(user.get('first_name', 'User'))}'s Harem</b>",
        f"📦 {total_unique} unique  |  🗂 {len(chars)} total  |  "
        f"💰 {user.get('coins', 0):,} coins",
        f"Page {page+1}/{total_pages}\n",
    ]

    # Group by anime/series
    sorted_page = sorted(page_chars, key=lambda x: x.get("anime", "Unknown"))
    for anime, group_iter in groupby(sorted_page, key=lambda x: x.get("anime", "Unknown")):
        group_list = list(group_iter)
        db_total = db_totals.get(anime, "?")
        lines.append(f"\n<b>{html.escape(anime)}  {len(group_list)}/{db_total}</b>")
        for c in group_list:
            icon = _rarity_icon(c.get("rarity", ""))
            cnt = id_counts.get(c["id"], 1)
            dup = f" ×{cnt}" if cnt > 1 else ""
            fav = " ⭐" if c["id"] == fav_id else ""
            lines.append(f"  {icon} <code>{c['id']}</code> {html.escape(c['name'])}{dup}{fav}")

    text = "\n".join(lines)

    kb = []
    kb.append([InlineKeyboardButton(
        f"🔍 Browse Collection ({len(chars)})",
        switch_inline_query_current_chat=f"collection.{user_id}",
    )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"harem:{page-1}:{user_id}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"harem:{page+1}:{user_id}"))
    if len(nav) > 1:
        kb.append(nav)

    markup = InlineKeyboardMarkup(kb)

    # Resolve display photo
    photo = None
    if fav_id:
        fav_char = next((c for c in chars if c["id"] == fav_id), None)
        photo = (fav_char or {}).get("img_url")
    if not photo and page_chars:
        photo = page_chars[0].get("img_url")

    return text, markup, photo

@Client.on_message(filters.command(["harem", "collection"]))
async def harem_command(client, message):
    user_id = message.from_user.id if message.from_user else 0
    if not user_id:
        return

    text, markup, photo = _build_harem_page(user_id, 0)
    try:
        if photo:
            await message.reply_photo(
                photo,
                caption=text,
                parse_mode=ParseMode.HTML,
                reply_markup=markup
            )
        else:
            await message.reply_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=markup
            )
    except Exception as e:
        # Fallback to plain text if photo sending fails
        await message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=markup
        )

@Client.on_callback_query(filters.regex(r"^harem:\d+:\d+$"))
async def harem_callback(client, callback_query: CallbackQuery):
    _, page_str, uid_str = callback_query.data.split(":")
    page = int(page_str)
    uid = int(uid_str)

    if callback_query.from_user.id != uid:
        await callback_query.answer("❌ That's not your harem!", show_alert=True)
        return

    await callback_query.answer()
    text, markup, photo = _build_harem_page(uid, page)

    try:
        # If it has a photo, edit the caption
        if callback_query.message.photo:
            await callback_query.edit_message_caption(
                caption=text,
                parse_mode=ParseMode.HTML,
                reply_markup=markup
            )
        else:
            await callback_query.edit_message_text(
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=markup
            )
    except MessageNotModified:
        pass
    except Exception:
        # Fallback to editing text
        try:
            await callback_query.edit_message_text(
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=markup
            )
        except Exception:
            pass

@Client.on_callback_query(filters.regex(r"^noop$"))
async def noop_callback(client, callback_query: CallbackQuery):
    await callback_query.answer()
