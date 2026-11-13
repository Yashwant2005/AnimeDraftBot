import asyncio
import random
import time
import html
import json
import logging
import string
import re
import threading
import unicodedata
import urllib.error
import urllib.request
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.enums import ParseMode

import database
import config

# Game State Stores
_shambles_games = {}
_whoswho_games = {}
_gol_games = {}
_punk_games = {}

# Game Costs & Rewards
GAME_COST = 50
SHAMBLES_WIN = 200
WHOSWHO_WIN = 300
GOL_WIN = 300
GOL_LOSE = 100
PUNK_WIN = 250
GUESS_GAME_TYPES = {"shambles", "whoswho", "punk"}
GUESS_GAME_TITLES = {
    "shambles": "SHAMBLES",
    "whoswho": "WHO'S WHO",
    "punk": "PUNK RECORDS",
}
_DISABLED_FILTER = filters.create(lambda _, __, ___: False)
_HINT_COUNT = 4
_HINT_REVEAL_DELAY = 30
_FINAL_GUESS_DELAY = 45
_CHARACTER_CACHE_TTL_SECONDS = 300
_character_cache = []
_character_cache_expires_at = 0.0
_character_cache_lock = threading.Lock()

def _matches_anime_filter(char, anime_filter):
    if not anime_filter:
        return True
    anime_norm = _normalize_guess_text(_get_char_anime(char)).replace(" ", "")
    filter_norm = _normalize_guess_text(anime_filter).replace(" ", "")
    return bool(filter_norm and filter_norm in anime_norm)

def _get_random_character(anime_filter=None):
    global _character_cache, _character_cache_expires_at

    if not database.USE_MONGO or database.COL_WAIFUS is None:
        return None

    with _character_cache_lock:
        if time.monotonic() >= _character_cache_expires_at:
            projection = {
                "id": 1,
                "name": 1,
                "anime": 1,
                "series": 1,
                "img_url": 1,
                "game_hint_cache": 1,
                "affiliation": 1,
                "team": 1,
                "crew": 1,
                "clan": 1,
                "group": 1,
                "organization": 1,
                "occupation": 1,
            }
            _character_cache = list(database.COL_WAIFUS.find(
                {
                    "img_url": {"$exists": True, "$ne": ""},
                    "archived": {"$ne": True},
                },
                projection,
            ))
            _character_cache_expires_at = time.monotonic() + _CHARACTER_CACHE_TTL_SECONDS
        chars = _character_cache

    if anime_filter:
        chars = [char for char in chars if _matches_anime_filter(char, anime_filter)]
    if not chars:
        return None
    return random.choice(chars)

def _normalize_guess_text(value):
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    without_marks = "".join(c for c in normalized if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", without_marks.lower()).strip()

def _is_correct_name(guess, name):
    guess_norm = _normalize_guess_text(guess)
    name_norm = _normalize_guess_text(name)
    if not guess_norm or not name_norm:
        return False
    guess_parts = guess_norm.split()
    name_parts = name_norm.split()
    guess_compact = "".join(guess_parts)
    name_compact = "".join(name_parts)
    return (
        guess_norm == name_norm
        or guess_compact == name_compact
        or sorted(guess_parts) == sorted(name_parts)
        or any(part == guess_norm for part in name_parts)
    )

def _get_char_anime(char):
    return char.get("anime") or char.get("series") or "Unknown"

def _get_char_affiliation(char):
    for key in ("affiliation", "team", "crew", "clan", "group", "organization", "occupation"):
        value = char.get(key)
        if value:
            if isinstance(value, (list, tuple)):
                return ", ".join(str(item) for item in value if item)
            return str(value)
    return None

def _name_without_marks(name):
    normalized = unicodedata.normalize("NFKD", str(name or ""))
    return "".join(c for c in normalized if not unicodedata.combining(c))

def _name_first_word(name):
    parts = _normalize_guess_text(name).split()
    return parts[0].title() if parts else "Unknown"

def _safe_name_char(name, index):
    plain = _name_without_marks(name)
    letters = [c for c in plain if c.isalnum()]
    if not letters:
        return "?"
    return letters[index]

def _name_pattern(name):
    parts = []
    for raw_part in _normalize_guess_text(name).split():
        if len(raw_part) <= 2:
            parts.append(raw_part[0].upper() + "-" * (len(raw_part) - 1))
        else:
            parts.append(raw_part[0].upper() + "-" * (len(raw_part) - 2) + raw_part[-1])
    return " ".join(parts) or "Unknown"

def _char_cache_query(char):
    if char.get("id"):
        return {"id": char["id"]}
    if char.get("_id"):
        return {"_id": char["_id"]}
    return {"name": char.get("name"), "anime": _get_char_anime(char)}


def _hint_cache_key(char):
    """Stable identifier for game-owned hints, never written to image source."""
    return str(
        char.get("id")
        or char.get("_id")
        or f'{char.get("name", "")} | {_get_char_anime(char)}'
    )

def _is_bad_hint(hint, char_name, anime, index):
    hint_norm = _normalize_guess_text(hint)
    name_norm = _normalize_guess_text(char_name)
    anime_norm = _normalize_guess_text(anime)
    if not hint_norm:
        return True
    if name_norm and name_norm in hint_norm:
        return True
    if anime_norm and anime_norm in hint_norm:
        return True
    bad_phrases = (
        "known from",
        "belongs to",
        "from the anime",
        "from this anime",
        "visual clues",
        "displayed anime",
        "this series",
    )
    if any(phrase in hint_norm for phrase in bad_phrases):
        return True
    if index < 3:
        name_parts = [p for p in name_norm.split() if len(p) > 2]
        if any(part in hint_norm for part in name_parts):
            return True
    return False

def _cached_game_hints(char, game_type):
    cache = char.get("game_hint_cache") or {}
    hints = cache.get(game_type) if isinstance(cache, dict) else None
    if isinstance(hints, list) and len(hints) >= _HINT_COUNT:
        anime = _get_char_anime(char)
        cleaned = [
            str(hint).strip()
            for idx, hint in enumerate(hints[:_HINT_COUNT])
            if not _is_bad_hint(str(hint).strip(), char["name"], anime, idx)
        ]
        if len(cleaned) >= _HINT_COUNT:
            return cleaned[:_HINT_COUNT]
    if database.USE_MONGO and database.COL_GAME_HINTS is not None:
        try:
            stored = database.COL_GAME_HINTS.find_one({
                'character_key': _hint_cache_key(char), 'game_type': game_type,
            }) or {}
            hints = stored.get('hints')
            if isinstance(hints, list) and len(hints) >= _HINT_COUNT:
                return hints[:_HINT_COUNT]
        except Exception as exc:
            logging.debug(f"[HINTS] Could not read game hint cache: {exc}")
    return None

def _save_game_hints(char, game_type, hints):
    if not database.USE_MONGO or database.COL_GAME_HINTS is None:
        return
    if not hints or len(hints) < _HINT_COUNT:
        return
    try:
        database.COL_GAME_HINTS.update_one(
            {'character_key': _hint_cache_key(char), 'game_type': game_type},
            {'$set': {'hints': hints[:_HINT_COUNT]}},
            upsert=True,
        )
    except Exception as exc:
        logging.debug(f"[HINTS] Failed to cache {game_type} hints: {exc}")

def _clean_ai_hint(value, char_name, anime, index):
    hint = re.sub(r"\s+", " ", str(value or "")).strip(" \t\r\n-•\"'")
    if _is_bad_hint(hint, char_name, anime, index):
        return None
    words = hint.split()
    if len(words) > 18:
        hint = " ".join(words[:18]).rstrip(".,;:") + "..."
    return hint

def _fallback_character_hints(char, game_type):
    name = char["name"]
    parts = _normalize_guess_text(name).split()
    compact = "".join(parts)
    vowels = sum(1 for c in compact if c in "aeiou")
    hints = []
    affiliation = _get_char_affiliation(char)
    if affiliation:
        hints.append(f"Connected to {affiliation}, but not always as an ally.")
    else:
        hints.append(f"The answer is written as {len(parts) or 1} word(s).")

    if len(parts) > 1:
        hints.append(f"The last word has {len(parts[-1])} letters.")
    else:
        hints.append(f"The name contains {vowels} vowel(s).")

    hints.extend([
        f"The full answer has {len(compact)} letters.",
        f"Name pattern: {_name_pattern(name)}.",
    ])

    while len(hints) < _HINT_COUNT:
        hints.append(f"The final letter is {_safe_name_char(name, -1).upper()}.")
    return hints[:_HINT_COUNT]

def _gemini_prompt(game_type, anime, name):
    game_label = "Who's Who image guessing game" if game_type == "whoswho" else "Punk Records character guessing game"
    return (
        f"You are a hint generator for an anime character guessing game.\n"
        f"Game: {game_label}\n"
        f"Anime: {anime}\n"
        f"Character: {name}\n\n"
        "Generate 4 progressive hints from hard to easy.\n"
        "Rules:\n"
        "1. Do not mention the character's full name.\n"
        "2. Do not use direct name clues like first name, first letter, or raw name length.\n"
        "3. Do not mention the anime name, because it is already visible to players.\n"
        "4. Do not say known from, belongs to, visual clues, or this series.\n"
        "5. Do not mention rarity.\n"
        "6. Hints 1-3 must not include exact signature attacks or direct epithets.\n"
        "7. Keep each hint under 15 words.\n"
        "8. Return strictly JSON with keys hint_1, hint_2, hint_3, hint_4."
    )

def _request_gemini_hints_sync(game_type, anime, name):
    api_key = getattr(config, "GEMINI_API_KEY", "")
    if not api_key:
        return None
    model = getattr(config, "GEMINI_MODEL", "gemini-1.5-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": _gemini_prompt(game_type, anime, name)}]}],
        "generationConfig": {
            "response_mime_type": "application/json",
            "temperature": 0.65,
        },
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=12) as response:
        raw = json.loads(response.read().decode("utf-8"))
    text = raw["candidates"][0]["content"]["parts"][0]["text"]
    parsed = json.loads(text)
    hints = []
    for idx in range(_HINT_COUNT):
        cleaned = _clean_ai_hint(parsed.get(f"hint_{idx + 1}"), name, anime, idx)
        if cleaned:
            hints.append(cleaned)
    return hints if len(hints) == _HINT_COUNT else None

async def _build_character_hints(char, game_type):
    cached = _cached_game_hints(char, game_type)
    if cached:
        return cached

    anime = _get_char_anime(char)
    try:
        hints = await asyncio.to_thread(_request_gemini_hints_sync, game_type, anime, char["name"])
        if hints:
            _save_game_hints(char, game_type, hints)
            return hints
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError, json.JSONDecodeError) as exc:
        logging.info(f"[HINTS] Gemini unavailable for {game_type}: {exc}")
    except Exception as exc:
        logging.warning(f"[HINTS] Gemini hint generation failed for {game_type}: {exc}")

    return _fallback_character_hints(char, game_type)

def _direction_from_coords(word_coords):
    if len(word_coords) < 2:
        return "single cell"
    x1, y1 = word_coords[0]
    x2, y2 = word_coords[1]
    dx, dy = x2 - x1, y2 - y1
    if dx and dy:
        return "diagonal down-right"
    if dx:
        return "left to right"
    return "top to bottom"

def _build_shambles_hints(char, word_coords):
    letter_count = len(word_coords)
    if not word_coords:
        return ["The grid hides the answer."] * _HINT_COUNT
    start_x, start_y = word_coords[0]
    return [
        f"The hidden answer uses {letter_count} grid letters.",
        f"The hidden word starts somewhere on row {start_y + 1}.",
        f"The hidden word reads {_direction_from_coords(word_coords)}.",
        f"The hidden word starts at row {start_y + 1}, column {start_x + 1}.",
    ]

def _hint_text(hints, count):
    return "\n".join(
        f"Hint {idx + 1}: <b>{html.escape(str(hints[idx]))}</b>"
        for idx in range(min(count, len(hints)))
    )

async def _pin_guess_message(client, chat_id, msg_id):
    try:
        await client.pin_chat_message(chat_id, msg_id, disable_notification=True)
    except Exception as exc:
        logging.debug(f"[GAMES] Could not pin guess game message {msg_id}: {exc}")

async def _unpin_guess_message(client, chat_id, msg_id):
    if not msg_id:
        return
    try:
        await client.unpin_chat_message(chat_id, msg_id)
    except Exception as exc:
        logging.debug(f"[GAMES] Could not unpin guess game message {msg_id}: {exc}")

def _deduct_coins(user_id, amount):
    if not database.USE_MONGO or database.COL_WAIFU_USERS is None:
        return False
    
    doc = database.COL_WAIFU_USERS.find_one({"id": user_id})
    coins = doc.get("coins", 0) if doc else 0
    if coins < amount:
        return False
        
    database.COL_WAIFU_USERS.update_one(
        {"id": user_id}, 
        {"$inc": {"coins": -amount, "bounty": -amount}}
    )
    return True

def _add_coins(user_id, amount):
    if not database.USE_MONGO or database.COL_WAIFU_USERS is None:
        return
    database.COL_WAIFU_USERS.update_one(
        {"id": user_id}, 
        {"$inc": {"coins": amount, "bounty": amount}},
        upsert=True
    )

def _guess_store(game_type):
    return {
        "shambles": _shambles_games,
        "whoswho": _whoswho_games,
        "punk": _punk_games,
    }[game_type]

def _find_guess_game(chat_id):
    for game_type in GUESS_GAME_TYPES:
        game = _guess_store(game_type).get(chat_id)
        if game and game.get("active"):
            return game_type, game
    return None, None

def _find_guess_game_by_token(token):
    for game_type in GUESS_GAME_TYPES:
        for chat_id, game in _guess_store(game_type).items():
            if game.get("token") == token and game.get("active"):
                return game_type, chat_id, game
    return None, None, None

def _clear_guess_game(chat_id, game_type):
    game = _guess_store(game_type).pop(chat_id, None)
    if game:
        game["active"] = False
    return game

def _parse_lobby_args(message):
    args = list((message.command or [])[1:])
    if not args:
        return GAME_COST, None

    fee = GAME_COST
    filter_parts = args
    fee_raw = None
    if args[0].replace(",", "").isdigit():
        fee_raw = args[0]
        filter_parts = args[1:]
    elif args[-1].replace(",", "").isdigit():
        fee_raw = args[-1]
        filter_parts = args[:-1]

    if fee_raw is not None:
        fee = int(fee_raw.replace(",", ""))

    anime_filter = " ".join(filter_parts).strip() or None
    return (fee, anime_filter) if fee > 0 else (None, anime_filter)

def _parse_entry_fee(message):
    fee, _ = _parse_lobby_args(message)
    return fee

def _format_lobby_usage(game_type):
    return (
        f"Usage: /{game_type} [entry_fee] [anime name]\n"
        f"Examples:\n/{game_type} 100\n/{game_type} one piece\n/{game_type} 100 one piece"
    )

def _filter_line(game):
    anime_filter = game.get("anime_filter")
    return f"Anime filter: <b>{html.escape(anime_filter)}</b>\n" if anime_filter else ""

def _player_link(user_id, name):
    return f"<a href='tg://user?id={user_id}'>{html.escape(str(name or user_id))}</a>"

def _format_lobby_players(game):
    lines = []
    for idx, (uid, pdata) in enumerate(game["players"].items(), 1):
        host_tag = " (host)" if uid == game["host_id"] else ""
        lines.append(f"{idx}. {_player_link(uid, pdata['name'])}{host_tag}")
    return "\n".join(lines)

def _guess_game_keyboard(game):
    token = game["token"]
    if game["status"] == "lobby":
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Join", callback_data=f"mg:{token}:join"),
                InlineKeyboardButton("Start", callback_data=f"mg:{token}:start"),
            ],
            [InlineKeyboardButton("Cancel", callback_data=f"mg:{token}:cancel")],
        ])
    return InlineKeyboardMarkup([[InlineKeyboardButton("Cancel Game", callback_data=f"mg:{token}:cancel")]])

def _lobby_text(game):
    import rich_ui
    title = GUESS_GAME_TITLES[game["type"]]
    total = game["pot"]
    prize = int(total * 0.9)
    players_str = "\n".join(
        f"{idx}. {pdata['name']}{' (host)' if uid == game['host_id'] else ''}"
        for idx, (uid, pdata) in enumerate(game["players"].items(), 1)
    )
    return rich_ui.build_rich_card(
        title=f"🎮 {title} Lobby",
        badges=[game["type"].upper(), f"Players: {len(game['players'])}"],
        table_data=[
            ("Host", game["host_name"]),
            ("Entry Fee", f"{game['entry_fee']:,}"),
            ("Prize Pool", f"{prize:,}")
        ],
        expandable_quote=f"Players Joined:\n{players_str}\n\nHost can press Start when ready."
    )

async def _is_group_admin(client, chat_id, user_id):
    try:
        member = await client.get_chat_member(chat_id, user_id)
        status = str(member.status).lower()
        return "administrator" in status or "owner" in status or "creator" in status
    except Exception:
        return False

async def _can_control_game(client, chat_id, user_id, game):
    return user_id == game.get("host_id") or await _is_group_admin(client, chat_id, user_id)

async def _create_guess_lobby(client, message, game_type):
    chat_id = message.chat.id
    user = message.from_user
    if not user:
        return

    existing_type, existing = _find_guess_game(chat_id)
    if existing:
        title = GUESS_GAME_TITLES[existing_type]
        await message.reply_text(f"A {title} lobby/game is already active in this chat.")
        return

    fee, anime_filter = _parse_lobby_args(message)
    if fee is None:
        await message.reply_text(_format_lobby_usage(game_type))
        return

    if not _deduct_coins(user.id, fee):
        await message.reply_text(f"You need {fee} coins to create this lobby.")
        return

    token = "".join(random.choices(string.ascii_letters + string.digits, k=8))
    game = {
        "type": game_type,
        "token": token,
        "host_id": user.id,
        "host_name": user.first_name,
        "players": {user.id: {"name": user.first_name}},
        "entry_fee": fee,
        "anime_filter": anime_filter,
        "pot": fee,
        "status": "lobby",
        "active": True,
        "created_at": time.time(),
    }
    _guess_store(game_type)[chat_id] = game

    msg = await message.reply_text(
        _lobby_text(game),
        reply_markup=_guess_game_keyboard(game),
        parse_mode=ParseMode.HTML,
    )
    game["msg_id"] = msg.id

async def _refund_guess_players(game, percent):
    refund = int(game["entry_fee"] * percent)
    if refund <= 0:
        return 0
    for uid in game["players"]:
        _add_coins(uid, refund)
    return refund

async def _cancel_guess_game(client, chat_id, game_type, reason="cancelled", actor=None, message=None, msg_id=None):
    game = _guess_store(game_type).get(chat_id)
    if not game or not game.get("active"):
        return False
    if msg_id and game.get("msg_id") != msg_id:
        return False

    pinned_msg_id = game.get("msg_id")
    _clear_guess_game(chat_id, game_type)
    await _unpin_guess_message(client, chat_id, pinned_msg_id)
    percent = 1.0 if game.get("status") == "lobby" else 0.4
    refund = await _refund_guess_players(game, percent)
    title = GUESS_GAME_TITLES[game_type]
    reason_txt = "Time's up" if reason == "timeout" else "Game cancelled"
    actor_txt = f" by {html.escape(actor)}" if actor else ""
    refund_note = "full refund" if percent == 1.0 else "40% refund"
    text = (
        f"<b>{title}</b>\n\n"
        f"{reason_txt}{actor_txt}.\n"
        f"Players received a {refund_note}: <b>{refund}</b> coins each."
    )

    if message is not None:
        try:
            await message.edit_text(text, reply_markup=None, parse_mode=ParseMode.HTML)
            return True
        except Exception:
            try:
                await message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
            pass
    try:
        await client.send_message(chat_id, text, reply_to_message_id=game.get("msg_id"), parse_mode=ParseMode.HTML)
    except Exception:
        pass
    return True

async def _start_guess_game(client, callback_query, chat_id, game_type, game):
    if len(game["players"]) < 2:
        await callback_query.answer("Need at least 2 players to start.", show_alert=True)
        return

    char = _get_random_character(game.get("anime_filter"))
    if not char:
        if game.get("anime_filter"):
            await callback_query.answer(f"No characters found for {game['anime_filter']}.", show_alert=True)
        else:
            await callback_query.answer("Not enough characters in DB.", show_alert=True)
        await _cancel_guess_game(client, chat_id, game_type, reason="cancelled", message=callback_query.message)
        return

    game["status"] = "active"
    game["char_name"] = char["name"]
    game["anime"] = _get_char_anime(char)
    game["start_time"] = time.time()
    title = GUESS_GAME_TITLES[game_type]
    players_line = f"Players: {len(game['players'])} | Prize: {int(game['pot'] * 0.9)} coins"
    anime_line = f"Anime: <b>{html.escape(game['anime'])}</b>"

    if game_type == "shambles":
        grid, word_coords = _create_shambles_grid(char["name"])
        game["grid"] = grid
        game["word_coords"] = word_coords
        game["hints"] = _build_shambles_hints(char, word_coords)
        game["step"] = 0
        await callback_query.message.edit_text(
            f"<b>{title}</b>\n{players_line}\n\n"
            f"{anime_line}\n"
            f"Find the hidden character name in this grid.\n"
            f"Type <code>/answer &lt;name&gt;</code> to guess.\n\n"
            f"{_render_shambles_grid(grid)}",
            reply_markup=_guess_game_keyboard(game),
            parse_mode=ParseMode.HTML,
        )
        game["msg_id"] = callback_query.message.id
        await _pin_guess_message(client, chat_id, game["msg_id"])
        asyncio.create_task(_shambles_timer(client, chat_id, game["msg_id"]))
    elif game_type == "whoswho":
        img = char.get("img_url")
        game["hints"] = await _build_character_hints(char, "whoswho")
        game["step"] = 0
        await callback_query.message.edit_text(
            f"<b>{title}</b> started.\n{players_line}",
            reply_markup=None,
            parse_mode=ParseMode.HTML,
        )
        try:
            msg = await client.send_photo(
                chat_id,
                photo=img,
                caption=(
                    f"<b>{title}</b>\n{players_line}\n\n"
                    f"{anime_line}\n"
                    f"Who is this character?\n"
                    f"Type <code>/answer &lt;name&gt;</code> to guess."
                ),
                reply_markup=_guess_game_keyboard(game),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            await _cancel_guess_game(client, chat_id, game_type, reason="cancelled", message=callback_query.message)
            return
        game["msg_id"] = msg.id
        await _pin_guess_message(client, chat_id, game["msg_id"])
        asyncio.create_task(_whoswho_timer(client, chat_id, msg.id))
    else:
        hints = await _build_character_hints(char, "punk")
        game["hints"] = hints
        game["step"] = 1
        await callback_query.message.edit_text(
            f"<b>{title}</b>\n{players_line}\n\n"
            f"{anime_line}\n"
            f"Guess the character based on these details.\n"
            f"Type <code>/answer &lt;name&gt;</code> to guess.\n\n"
            f"{_hint_text(hints, 1)}",
            reply_markup=_guess_game_keyboard(game),
            parse_mode=ParseMode.HTML,
        )
        game["msg_id"] = callback_query.message.id
        await _pin_guess_message(client, chat_id, game["msg_id"])
        asyncio.create_task(_punk_timer(client, chat_id, game["msg_id"]))

    await callback_query.answer("Game started!")

@Client.on_callback_query(filters.regex(r"^mg:"))
async def cb_guess_lobby(client, callback_query: CallbackQuery):
    try:
        _, token, action = callback_query.data.split(":", 2)
    except ValueError:
        return await callback_query.answer("Invalid game button.", show_alert=True)

    game_type, chat_id, game = _find_guess_game_by_token(token)
    if not game:
        return await callback_query.answer("Game expired.", show_alert=True)

    uid = callback_query.from_user.id
    name = callback_query.from_user.first_name

    if action == "join":
        if game["status"] != "lobby":
            return await callback_query.answer("This game already started.", show_alert=True)
        if uid in game["players"]:
            return await callback_query.answer("You are already in the lobby.", show_alert=True)
        fee = game["entry_fee"]
        if not _deduct_coins(uid, fee):
            return await callback_query.answer(f"You need {fee} coins to join.", show_alert=True)
        game["players"][uid] = {"name": name}
        game["pot"] += fee
        await callback_query.message.edit_text(
            _lobby_text(game),
            reply_markup=_guess_game_keyboard(game),
            parse_mode=ParseMode.HTML,
        )
        return await callback_query.answer("Joined lobby.")

    if action == "start":
        if uid != game["host_id"]:
            return await callback_query.answer("Only the host can start.", show_alert=True)
        if game["status"] != "lobby":
            return await callback_query.answer("Game already started.", show_alert=True)
        return await _start_guess_game(client, callback_query, chat_id, game_type, game)

    if action == "cancel":
        if not await _can_control_game(client, chat_id, uid, game):
            return await callback_query.answer("Only the host or a group admin can cancel.", show_alert=True)
        await _cancel_guess_game(
            client,
            chat_id,
            game_type,
            actor=name,
            message=callback_query.message,
        )
        return await callback_query.answer("Cancelled.")

    await callback_query.answer("Unknown action.", show_alert=True)

@Client.on_message(filters.command("cancelgame") & filters.group)
async def cmd_cancel_guess_game(client, message):
    chat_id = message.chat.id
    game_type, game = _find_guess_game(chat_id)
    if not game:
        return await message.reply_text("There is no active guessing game or lobby in this chat.")
    if not await _can_control_game(client, chat_id, message.from_user.id, game):
        return await message.reply_text("Only the host or a group admin can cancel this game.")
    await _cancel_guess_game(client, chat_id, game_type, actor=message.from_user.first_name)

# ==========================================
# 1. SHAMBLES (Word Search)
# ==========================================

def _create_shambles_grid(word, size=None):
    hidden_word = _name_without_marks(word)
    hidden_word = "".join(c for c in hidden_word if c.isalpha()).upper()
    if not hidden_word:
        hidden_word = "CHARACTER"
    size = max(8, len(hidden_word)) if size is None else max(size, len(hidden_word))
    
    grid = [[" " for _ in range(size)] for _ in range(size)]
    direction = random.choice([(0, 1), (1, 0), (1, 1)]) # Right, Down, Diagonal-Down-Right
    
    # Place word
    start_x = random.randint(0, size - 1 - (len(hidden_word) - 1) * direction[0])
    start_y = random.randint(0, size - 1 - (len(hidden_word) - 1) * direction[1])
    
    word_coords = []
    for i, char in enumerate(hidden_word):
        x = start_x + i * direction[0]
        y = start_y + i * direction[1]
        grid[y][x] = char
        word_coords.append((x, y))
        
    # Fill remaining
    for y in range(size):
        for x in range(size):
            if grid[y][x] == " ":
                grid[y][x] = random.choice(string.ascii_uppercase)
                
    return grid, word_coords

def _render_shambles_grid(grid):
    return "<code>" + "\n".join(" ".join(row) for row in grid) + "</code>"

@Client.on_message(filters.command("shambles") & filters.group)
async def cmd_shambles(client, message):
    await _create_guess_lobby(client, message, "shambles")
    return
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    if not _deduct_coins(user_id, GAME_COST):
        await message.reply_text(f"❌ You need {GAME_COST} coins to play Shambles!")
        return
        
    char = _get_random_character()
    if not char:
        await message.reply_text("❌ Not enough characters in DB.")
        _add_coins(user_id, GAME_COST) # refund
        return
        
    await _cancel_other_games(chat_id)
    
    grid, word_coords = _create_shambles_grid(char["name"])
    
    msg = await message.reply_text(
        f"🧩 <b>SHAMBLES</b>\n\n"
        f"Find the hidden character name in this grid!\n"
        f"Type <code>/answer &lt;name&gt;</code> to guess.\n\n"
        f"{_render_shambles_grid(grid)}",
        parse_mode=ParseMode.HTML
    )
    
    _shambles_games[chat_id] = {
        "user_id": user_id,
        "char_name": char["name"],
        "grid": grid,
        "word_coords": word_coords,
        "msg_id": msg.id,
        "active": True,
        "start_time": time.time()
    }
    
    asyncio.create_task(_shambles_timer(client, chat_id, msg.id))

async def _shambles_timer(client, chat_id, msg_id):
    for i in range(4): # 4 steps, 30s each (2 mins total)
        await asyncio.sleep(_HINT_REVEAL_DELAY)
        
        game = _shambles_games.get(chat_id)
        if not game or not game["active"] or game["msg_id"] != msg_id:
            return
            
        # Replace some non-word letters with dots
        grid = game["grid"]
        word_coords = game["word_coords"]
        size = len(grid)
        
        non_word = [(x,y) for y in range(size) for x in range(size) if (x,y) not in word_coords and grid[y][x] != "·"]
        if not non_word:
            continue
            
        # Replace up to 10 letters per step
        to_replace = random.sample(non_word, min(10, len(non_word)))
        for x, y in to_replace:
            grid[y][x] = "·"

        game["step"] = game.get("step", 0) + 1
        players_line = f"Players: {len(game.get('players', {}))} | Prize: {int(game.get('pot', 0) * 0.9)} coins"
        anime_line = f"Anime: <b>{html.escape(game.get('anime') or 'Unknown')}</b>"
        hints = _hint_text(game.get("hints", []), game["step"])
        try:
            await client.edit_message_text(
                chat_id, msg_id,
                f"🧩 <b>SHAMBLES</b>\n{players_line}\n\n"
                f"{anime_line}\n"
                f"{hints}\n\n"
                f"Find the hidden character name in this grid.\n"
                f"Type <code>/answer &lt;name&gt;</code> to guess.\n\n"
                f"{_render_shambles_grid(grid)}",
                reply_markup=_guess_game_keyboard(game),
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass
            
    await asyncio.sleep(_FINAL_GUESS_DELAY)

    # Timeout
    game = _shambles_games.get(chat_id)
    if game and game["active"] and game["msg_id"] == msg_id:
        await _cancel_guess_game(client, chat_id, "shambles", reason="timeout", msg_id=msg_id)
        return
        game["active"] = False
        del _shambles_games[chat_id]
        try:
            await client.send_message(
                chat_id, 
                f"⌛ <b>Time's up!</b>\n"
                f"The Shambles puzzle expired.\n"
                f"The character was <b>{game['char_name']}</b>.\n"
                f"💸 You lost {GAME_COST} coins.",
                reply_to_message_id=msg_id,
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass


# ==========================================
# 2. WHO'S WHO (Blurred/Hint Game)
# ==========================================

@Client.on_message(filters.command("whoswho") & filters.group)
async def cmd_whoswho(client, message):
    await _create_guess_lobby(client, message, "whoswho")
    return
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    if not _deduct_coins(user_id, GAME_COST):
        await message.reply_text(f"❌ You need {GAME_COST} coins to play Who's Who!")
        return
        
    char = _get_random_character()
    if not char:
        await message.reply_text("❌ Not enough characters in DB.")
        _add_coins(user_id, GAME_COST)
        return
        
    await _cancel_other_games(chat_id)
    
    img = char.get("img_url")
    anime = char.get("anime", "Unknown Anime")
    
    caption = (
        f"🕵️‍♂️ <b>WHO'S WHO?</b>\n\n"
        f"Anime: <b>{anime}</b>\n"
        f"Who is this character?\n"
        f"Type <code>/answer &lt;name&gt;</code> to guess."
    )
    
    try:
        msg = await client.send_photo(
            chat_id, 
            photo=img,
            caption=caption,
            parse_mode=ParseMode.HTML
        )
    except Exception:
        await message.reply_text("❌ Error sending character image.")
        _add_coins(user_id, GAME_COST)
        return
        
    _whoswho_games[chat_id] = {
        "user_id": user_id,
        "char_name": char["name"],
        "anime": anime,
        "msg_id": msg.id,
        "active": True,
        "step": 0
    }
    
    asyncio.create_task(_whoswho_timer(client, chat_id, msg.id))

async def _whoswho_timer(client, chat_id, msg_id):
    for i in range(4): # 4 steps, 30s each
        await asyncio.sleep(_HINT_REVEAL_DELAY)
        
        game = _whoswho_games.get(chat_id)
        if not game or not game["active"] or game["msg_id"] != msg_id:
            return
            
        game["step"] += 1
        step = game["step"]
        hints = game.get("hints") or _fallback_character_hints(
            {"name": game["char_name"], "anime": game.get("anime")},
            "whoswho",
        )
        hint = hints[step - 1] if step - 1 < len(hints) else hints[-1]
             
        try:
            await client.send_message(
                chat_id,
                f"🕵️‍♂️ <b>Who's Who Hint #{step}</b>\n<b>{html.escape(hint)}</b>",
                reply_to_message_id=msg_id,
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass
            
    await asyncio.sleep(_FINAL_GUESS_DELAY)

    # Timeout
    game = _whoswho_games.get(chat_id)
    if game and game["active"] and game["msg_id"] == msg_id:
        await _cancel_guess_game(client, chat_id, "whoswho", reason="timeout", msg_id=msg_id)
        return
        game["active"] = False
        del _whoswho_games[chat_id]
        try:
            await client.send_message(
                chat_id, 
                f"⌛ <b>Time's up!</b>\n"
                f"The character was <b>{game['char_name']}</b>.\n"
                f"💸 You lost {GAME_COST} coins.",
                reply_to_message_id=msg_id,
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass


# ==========================================
# 3. GUESS OR LIFE (2-Player Hangman)
# ==========================================

@Client.on_message(_DISABLED_FILTER & filters.command("gol") & filters.group)
async def cmd_gol(client, message):
    if not message.reply_to_message:
        await message.reply_text("Reply to a user to challenge them to Guess or Life!\nUsage: /gol")
        return
        
    p1 = message.from_user
    p2 = message.reply_to_message.from_user
    
    if not p1 or not p2 or p1.id == p2.id or p2.is_bot:
        await message.reply_text("❌ Invalid opponent.")
        return
        
    if not _deduct_coins(p1.id, GAME_COST):
        await message.reply_text(f"❌ {p1.first_name}, you need {GAME_COST} coins to challenge!")
        return
        
    # We won't deduct from p2 immediately to avoid taking money if they don't want to play.
    # Actually, the prompt says "Deduct coins at game start". Let's deduct from both.
    if not _deduct_coins(p2.id, GAME_COST):
        await message.reply_text(f"❌ {p2.first_name} doesn't have {GAME_COST} coins to play.")
        _add_coins(p1.id, GAME_COST) # refund p1
        return
        
    char = _get_random_character()
    if not char:
        await message.reply_text("❌ Not enough characters in DB.")
        _add_coins(p1.id, GAME_COST)
        _add_coins(p2.id, GAME_COST)
        return
        
    word = "".join(c for c in char["name"] if c.isalpha() or c == ' ').upper()
    
    game_id = f"gol_{p1.id}_{p2.id}_{int(time.time())}"
    
    _gol_games[game_id] = {
        "p1_id": p1.id, "p1_name": p1.first_name, "p1_lives": 5,
        "p2_id": p2.id, "p2_name": p2.first_name, "p2_lives": 5,
        "turn": p1.id,
        "word": word,
        "guessed": set([" "]), # space is pre-guessed
        "active": True
    }
    
    await _send_gol_board(client, message.chat.id, game_id)

async def _send_gol_board(client, chat_id, game_id, edit_msg=None):
    game = _gol_games.get(game_id)
    if not game:
        return
        
    word = game["word"]
    guessed = game["guessed"]
    
    display_word = " ".join([c if c in guessed else "_" for c in word])
    
    turn_name = game["p1_name"] if game["turn"] == game["p1_id"] else game["p2_name"]
    
    text = (
        f"❤️ <b>GUESS OR LIFE</b>\n\n"
        f"{game['p1_name']}: {'❤️' * game['p1_lives']} {'🖤' * (5-game['p1_lives'])}\n"
        f"{game['p2_name']}: {'❤️' * game['p2_lives']} {'🖤' * (5-game['p2_lives'])}\n\n"
        f"<code>{display_word}</code>\n\n"
        f"👉 <b>{turn_name}'s turn!</b>"
    )
    
    # Build A-Z keyboard
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    rows = []
    curr_row = []
    for l in letters:
        if l in guessed:
            curr_row.append(InlineKeyboardButton("·", callback_data="ignore"))
        else:
            curr_row.append(InlineKeyboardButton(l, callback_data=f"gol:{game_id}:{l}"))
        if len(curr_row) == 5:
            rows.append(curr_row)
            curr_row = []
    if curr_row:
        rows.append(curr_row)
        
    kb = InlineKeyboardMarkup(rows)
    
    if edit_msg:
        await edit_msg.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    else:
        await client.send_message(chat_id, text, reply_markup=kb, parse_mode=ParseMode.HTML)

@Client.on_callback_query(_DISABLED_FILTER & filters.regex(r"^gol:"))
async def cb_gol(client, callback_query: CallbackQuery):
    _, game_id, letter = callback_query.data.split(":")
    game = _gol_games.get(game_id)
    
    if not game or not game["active"]:
        await callback_query.answer("Game is over or invalid.", show_alert=True)
        return
        
    user_id = callback_query.from_user.id
    if user_id != game["p1_id"] and user_id != game["p2_id"]:
        await callback_query.answer("You are not playing this game!", show_alert=True)
        return
        
    if user_id != game["turn"]:
        await callback_query.answer("It's not your turn!", show_alert=True)
        return
        
    if letter in game["guessed"]:
        await callback_query.answer("Letter already guessed!", show_alert=True)
        return
        
    game["guessed"].add(letter)
    
    # Check if correct
    if letter not in game["word"]:
        if user_id == game["p1_id"]:
            game["p1_lives"] -= 1
        else:
            game["p2_lives"] -= 1
        await callback_query.answer(f"Wrong! -1 Life", show_alert=False)
    else:
        await callback_query.answer("Correct!", show_alert=False)
        
    # Check win/lose conditions
    word_complete = all(c in game["guessed"] for c in game["word"])
    p1_dead = game["p1_lives"] <= 0
    p2_dead = game["p2_lives"] <= 0
    
    if word_complete or p1_dead or p2_dead:
        game["active"] = False
        winner_id = None
        winner_name = None
        
        if word_complete:
            winner_id = user_id
            winner_name = callback_query.from_user.first_name
        elif p1_dead:
            winner_id = game["p2_id"]
            winner_name = game["p2_name"]
        elif p2_dead:
            winner_id = game["p1_id"]
            winner_name = game["p1_name"]
            
        loser_id = game["p1_id"] if winner_id == game["p2_id"] else game["p2_id"]
        
        _add_coins(winner_id, GOL_WIN)
        # Loser already paid GAME_COST at start. The spec says loser gets -100 coins.
        # Since they paid 50 to start, let's deduct another 50 to make it -100 total.
        _deduct_coins(loser_id, GOL_LOSE - GAME_COST) 
        
        await callback_query.edit_message_text(
            f"❤️ <b>GUESS OR LIFE - GAME OVER</b>\n\n"
            f"The character was <b>{game['word']}</b>.\n\n"
            f"🏆 <b>{winner_name}</b> wins +{GOL_WIN} coins!\n",
            parse_mode=ParseMode.HTML
        )
        del _gol_games[game_id]
        return
        
    # Switch turns
    game["turn"] = game["p2_id"] if user_id == game["p1_id"] else game["p1_id"]
    await _send_gol_board(client, callback_query.message.chat.id, game_id, edit_msg=callback_query.message)


# ==========================================
# 4. PUNK RECORDS (Detail Guessing)
# ==========================================

@Client.on_message(filters.command("punk") & filters.group)
async def cmd_punk(client, message):
    await _create_guess_lobby(client, message, "punk")
    return
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    if not _deduct_coins(user_id, GAME_COST):
        await message.reply_text(f"❌ You need {GAME_COST} coins to play Punk Records!")
        return
        
    char = _get_random_character()
    if not char:
        await message.reply_text("❌ Not enough characters in DB.")
        _add_coins(user_id, GAME_COST)
        return
        
    await _cancel_other_games(chat_id)
    
    hints = _fallback_character_hints(char, "punk")
    
    msg = await message.reply_text(
        f"🎵 <b>PUNK RECORDS</b>\n\n"
        f"Guess the character based on these details!\n"
        f"Type <code>/answer &lt;name&gt;</code> to guess.\n\n"
        f"Hint 1: {hints[0]}",
        parse_mode=ParseMode.HTML
    )
    
    _punk_games[chat_id] = {
        "user_id": user_id,
        "char_name": char["name"],
        "hints": hints,
        "msg_id": msg.id,
        "active": True,
        "step": 1
    }
    
    asyncio.create_task(_punk_timer(client, chat_id, msg.id))

async def _punk_timer(client, chat_id, msg_id):
    for i in range(_HINT_COUNT - 1): # first hint is shown when the game starts
        await asyncio.sleep(_HINT_REVEAL_DELAY)
        
        game = _punk_games.get(chat_id)
        if not game or not game["active"] or game["msg_id"] != msg_id:
            return
            
        game["step"] += 1
        step = game["step"]
        players_line = f"Players: {len(game.get('players', {}))} | Prize: {int(game.get('pot', 0) * 0.9)} coins"
        anime_line = f"Anime: <b>{html.escape(game.get('anime') or 'Unknown')}</b>"
        hints_str = _hint_text(game.get("hints", []), step)
        
        try:
            await client.edit_message_text(
                chat_id, msg_id,
                f"🎵 <b>PUNK RECORDS</b>\n{players_line}\n\n"
                f"{anime_line}\n"
                f"Guess the character based on these details!\n"
                f"Type <code>/answer &lt;name&gt;</code> to guess.\n\n"
                f"{hints_str}",
                reply_markup=_guess_game_keyboard(game),
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass
            
    await asyncio.sleep(_FINAL_GUESS_DELAY)

    # Final timeout wait
    game = _punk_games.get(chat_id)
    if game and game["active"] and game["msg_id"] == msg_id:
        await _cancel_guess_game(client, chat_id, "punk", reason="timeout", msg_id=msg_id)
        return
        game["active"] = False
        del _punk_games[chat_id]
        try:
            await client.send_message(
                chat_id, 
                f"⌛ <b>Time's up!</b>\n"
                f"The character was <b>{game['char_name']}</b>.\n"
                f"💸 You lost {GAME_COST} coins.",
                reply_to_message_id=msg_id,
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass


# ==========================================
# SHARED /ANSWER COMMAND
# ==========================================

@Client.on_message(filters.command("answer") & filters.group)
async def cmd_answer(client, message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    if len(message.command) < 2:
        await message.reply_text("Usage: /answer <name>")
        return
        
    guess = " ".join(message.command[1:]).strip().lower()

    game_type, game = _find_guess_game(chat_id)
    if game:
        title = GUESS_GAME_TITLES[game_type]
        if game.get("status") == "lobby":
            await message.reply_text(f"{title} is still in lobby. Host must start it first.")
            return
        if user_id not in game["players"]:
            await message.reply_text("You are not in this game lobby.")
            return
        if _is_correct_name(guess, game["char_name"]):
            pinned_msg_id = game.get("msg_id")
            _clear_guess_game(chat_id, game_type)
            await _unpin_guess_message(client, chat_id, pinned_msg_id)
            prize = int(game["pot"] * 0.9)
            _add_coins(user_id, prize)
            try:
                await client.edit_message_reply_markup(chat_id, game.get("msg_id"), reply_markup=None)
            except Exception:
                pass
            winner_name = html.escape(str(message.from_user.first_name or user_id))
            await message.reply_text(
                f"<b>Correct!</b>\n"
                f"The character was <b>{html.escape(game['char_name'])}</b>.\n"
                f"{winner_name} won <b>{prize}</b> coins!",
                parse_mode=ParseMode.HTML,
            )
        else:
            await message.reply_text("Wrong answer. Keep trying.")
        return
    
    # Check Shambles
    shambles = _shambles_games.get(chat_id)
    if shambles and shambles["active"]:
        if user_id != shambles["user_id"]:
            await message.reply_text("❌ This game is not yours!")
            return
        if _is_correct_name(guess, shambles["char_name"]):
            shambles["active"] = False
            del _shambles_games[chat_id]
            _add_coins(user_id, SHAMBLES_WIN)
            await message.reply_text(f"🎉 <b>Correct!</b>\nThe Shambles character was {shambles['char_name']}.\nYou won +{SHAMBLES_WIN} coins!", parse_mode=ParseMode.HTML)
        else:
            await message.reply_text("❌ Wrong! Keep trying.")
        return
        
    # Check WhosWho
    whoswho = _whoswho_games.get(chat_id)
    if whoswho and whoswho["active"]:
        if user_id != whoswho["user_id"]:
            await message.reply_text("❌ This game is not yours!")
            return
        if _is_correct_name(guess, whoswho["char_name"]):
            whoswho["active"] = False
            del _whoswho_games[chat_id]
            _add_coins(user_id, WHOSWHO_WIN)
            await message.reply_text(f"🎉 <b>Correct!</b>\nThe character was {whoswho['char_name']}.\nYou won +{WHOSWHO_WIN} coins!", parse_mode=ParseMode.HTML)
        else:
            await message.reply_text("❌ Wrong! Wait for more hints or try again.")
        return
        
    # Check Punk
    punk = _punk_games.get(chat_id)
    if punk and punk["active"]:
        if user_id != punk["user_id"]:
            await message.reply_text("❌ This game is not yours!")
            return
        if _is_correct_name(guess, punk["char_name"]):
            punk["active"] = False
            del _punk_games[chat_id]
            bonus = (5 - punk["step"]) * 50
            total_win = PUNK_WIN + bonus
            _add_coins(user_id, total_win)
            await message.reply_text(f"🎉 <b>Correct!</b>\nThe character was {punk['char_name']}.\nYou guessed in {punk['step']} hints!\nYou won +{total_win} coins!", parse_mode=ParseMode.HTML)
        else:
            await message.reply_text("❌ Wrong! Wait for more hints or try again.")
        return

    await message.reply_text("❌ There is no active game in this chat that requires an answer.")
