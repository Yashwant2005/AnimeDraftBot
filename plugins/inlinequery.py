import re
import time
import html
import random
from cachetools import TTLCache
from pyrogram import Client
from pyrogram.enums import ParseMode
from pyrogram.types import InlineQuery, InlineQueryResultArticle, InlineQueryResultPhoto, InputTextMessageContent

import database

_all_cache = TTLCache(maxsize=1, ttl=3600)
_user_cache = TTLCache(maxsize=10000, ttl=60)
_PAGE_SIZE = 50

def _batch_global(ids: list) -> dict:
    if not database.USE_MONGO or database.COL_WAIFU_USERS is None:
        return {}
    pipeline = [
        {"$unwind": "$characters"},
        {"$match": {"characters.id": {"$in": ids}}},
        {"$group": {"_id": "$characters.id", "n": {"$sum": 1}}},
    ]
    cursor = database.COL_WAIFU_USERS.aggregate(pipeline)
    return {d["_id"]: d["n"] for d in cursor}

def _batch_anime(animes: list) -> dict:
    if not database.USE_MONGO or database.COL_WAIFUS is None:
        return {}
    pipeline = [
        {"$match": {"anime": {"$in": animes}}},
        {"$group": {"_id": "$anime", "n": {"$sum": 1}}},
    ]
    cursor = database.COL_WAIFUS.aggregate(pipeline)
    return {d["_id"]: d["n"] for d in cursor}

@Client.on_inline_query()
async def inlinequery_handler(client: Client, inline_query: InlineQuery):
    raw = inline_query.query.strip()
    offset = int(inline_query.offset) if inline_query.offset else 0
    user = None
    chars = []

    if not database.USE_MONGO or database.COL_WAIFUS is None:
        await inline_query.answer([], cache_time=5)
        return

    if raw.startswith("collection."):
        # User collection search
        parts = raw.split(" ", 1)
        uid_part = parts[0].split(".")[1]
        search = parts[1].strip() if len(parts) > 1 else ""

        if uid_part.isdigit():
            uid = int(uid_part)
            key = uid_part

            if key in _user_cache and _user_cache[key] is not None:
                user = _user_cache[key]
            else:
                user = database.COL_WAIFU_USERS.find_one({"id": uid})
                if user:
                    _user_cache[key] = user

            if user:
                deduped = list({c["id"]: c for c in user.get("characters", [])}.values())
                if search:
                    pat = re.compile(re.escape(search), re.IGNORECASE)
                    deduped = [
                        c for c in deduped
                        if pat.search(c.get("name", "")) or pat.search(c.get("anime", ""))
                    ]
                chars = deduped
    else:
        # Global search
        if raw:
            query = {
                "$or": [
                    {"name": {"$regex": re.escape(raw), "$options": "i"}},
                    {"anime": {"$regex": re.escape(raw), "$options": "i"}},
                ]
            }
            chars = list(database.COL_WAIFUS.find(query))
        else:
            if "all" in _all_cache:
                chars = _all_cache["all"]
            else:
                chars = list(database.COL_WAIFUS.find({}))
                _all_cache["all"] = chars

    # Paginate results
    page_chars = chars[offset:offset + _PAGE_SIZE]
    next_offset = str(offset + len(page_chars)) if len(page_chars) == _PAGE_SIZE else ""

    if not page_chars:
        if raw.startswith("collection."):
            await inline_query.answer([
                InlineQueryResultArticle(
                    id=f"empty_collection_{int(time.time())}",
                    title="Harem is empty",
                    description="Catch a dropped character with /guess first.",
                    input_message_content=InputTextMessageContent(
                        "📭 <b>Harem is empty</b>\n\n"
                        "Catch a dropped character with <code>/guess name</code> first.",
                        parse_mode=ParseMode.HTML,
                    ),
                )
            ], cache_time=5)
            return
        await inline_query.answer([], cache_time=5)
        return

    ids = [c["id"] for c in page_chars]
    animes = list({c.get("anime", "Unknown") for c in page_chars})
    g_count = _batch_global(ids)
    a_total = _batch_anime(animes)

    results = []
    for c in page_chars:
        name = html.escape(c.get("name", "Unknown"))
        anime = html.escape(c.get("anime", "Unknown"))
        photo_url = c.get("img_url", "")
        
        # Guard: Pyrogram inline query needs a valid URL or Telegram file_id
        if not photo_url:
            continue

        if user and raw.startswith("collection."):
            u_cnt = sum(1 for x in user.get("characters", []) if x["id"] == c["id"])
            u_an = sum(1 for x in user.get("characters", []) if x.get("anime") == c["anime"])
            db_an = a_total.get(c["anime"], "?")
            uid = user.get("id", "")
            uname = html.escape(user.get("first_name", str(uid)))
            cap = (
                f"<b><a href='tg://user?id={uid}'>{uname}</a>'s Character</b>\n\n"
                f"🌸 <b>{name}</b> (×{u_cnt})\n"
                f"📺 <b>{anime}</b> ({u_an}/{db_an})\n"
                f"💎 {c.get('rarity', '')}\n"
                f"🆔 {c['id']}"
            )
        else:
            gc = g_count.get(c["id"], 0)
            cap = (
                f"🌸 <b>{name}</b>\n\n"
                f"📺 {anime}\n"
                f"💎 {c.get('rarity', '')}\n"
                f"🆔 {c['id']}\n\n"
                f"Guessed globally <b>{gc}</b> time{'s' if gc != 1 else ''}."
            )

        results.append(InlineQueryResultPhoto(
            id=f"{c['id']}_{int(time.time())}_{random.randint(100,999)}",
            photo_url=photo_url,
            thumb_url=photo_url,
            caption=cap,
            parse_mode=ParseMode.HTML
        ))

    await inline_query.answer(results, next_offset=next_offset, cache_time=5)
