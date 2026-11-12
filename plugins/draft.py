from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ParseMode
from plugins.audit import log_event
import time
import random
import re
import logging
import global_state
import database
import rich_ui
import html
import os
import json
import uuid
from types import SimpleNamespace
# --- FIX: Split imports correctly ---
from config import MAX_ACTIVE_DRAFTS_PER_CHAT, MARVEL_ROLES, ROLES, DATA_DIR
from global_state import SERIES_MAP, SERIES_DISPLAY, POKEMON_LIST, POKEMON_DATA


_DRAFT_MODE_PICKERS = {}


async def _send_draft_mode_picker(client, message):
    """Offer all draft variants from one entry command."""
    token = uuid.uuid4().hex[:12]
    _DRAFT_MODE_PICKERS[token] = {
        'owner_id': message.from_user.id,
        'reply_to_message': message.reply_to_message,
    }
    card = rich_ui.build_rich_card(
        title='⚔️ Choose Draft Mode',
        badges=['Draft Setup'],
        expandable_quote='Choose a mode to challenge the replied player.',
    )
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton('🎭 Draft', callback_data=f'draftmenu:{token}:anime'),
            InlineKeyboardButton('🔥 Auction Draft', callback_data=f'draftmenu:{token}:auction'),
        ],
        [
            InlineKeyboardButton('⚡ Pokémon Draft', callback_data=f'draftmenu:{token}:pokemon'),
            InlineKeyboardButton('🎬 Comic Draft', callback_data=f'draftmenu:{token}:comic'),
        ],
        [InlineKeyboardButton('✖️ Cancel', callback_data=f'draftmenu:{token}:cancel')],
    ])
    await rich_ui.send_rich_message(client, message.chat.id, card, reply_markup=keyboard)


def _menu_message(callback_query, command, reply_to_message):
    """Expose the picker context through the same small message API as commands."""
    return SimpleNamespace(
        from_user=callback_query.from_user,
        reply_to_message=reply_to_message,
        chat=callback_query.message.chat,
        command=[command],
        text=f'/{command}',
        reply=callback_query.message.reply,
        _from_draft_mode_picker=True,
    )


@Client.on_callback_query(filters.regex(r'^draftmenu:([a-f0-9]{12}):(anime|auction|pokemon|comic|cancel)$'))
async def draft_mode_picker_callback(client, callback_query):
    token, mode = callback_query.matches[0].group(1), callback_query.matches[0].group(2)
    picker = _DRAFT_MODE_PICKERS.get(token)
    if not picker:
        return await callback_query.answer('This draft menu expired. Send /draft again.', show_alert=True)
    if callback_query.from_user.id != picker['owner_id']:
        return await callback_query.answer('Only the player who opened this menu can choose a mode.', show_alert=True)
    _DRAFT_MODE_PICKERS.pop(token, None)
    if mode == 'cancel':
        await callback_query.answer('Cancelled.')
        return await callback_query.message.edit_text('❌ Draft setup cancelled.')

    labels = {'anime': 'Draft', 'auction': 'Auction Draft', 'pokemon': 'Pokémon Draft', 'comic': 'Comic Draft'}
    await callback_query.answer(f'{labels[mode]} selected.')
    await callback_query.message.edit_text(f'⏳ Starting <b>{labels[mode]}</b>...', parse_mode=ParseMode.HTML)
    message = _menu_message(
        callback_query,
        {'anime': 'draft', 'auction': 'auction', 'pokemon': 'pokemondraft', 'comic': 'cdraft'}[mode],
        picker['reply_to_message'],
    )
    if mode == 'anime':
        return await draft_cmd(client, message)
    if mode == 'pokemon':
        return await pokemon_draft_cmd(client, message)
    if mode == 'comic':
        return await comic_draft_cmd(client, message)
    # Import lazily to avoid a module-load cycle between draft and auction.
    from plugins.auction import auction_command
    return await auction_command(client, message)

@Client.on_message(filters.command(["draft", "draft_v2"]))
async def draft_cmd(c, m):
    if not m.reply_to_message:
        logging.warning("[DRAFT] %s must reply to a friend before choosing a draft mode", m.from_user.id)
        return await m.reply_text(
            "<b>⚠️ Reply to a friend's message first, then use /draft to choose a mode.</b>",
            parse_mode=ParseMode.HTML,
        )
    if (
        m.command[0].lower() == 'draft'
        and len(m.command) == 1
        and not getattr(m, '_from_draft_mode_picker', False)
    ):
        return await _send_draft_mode_picker(c, m)
    database.set_current_client(c)
    is_v2 = "v2" in m.command[0]
    p1, p2 = m.from_user, m.reply_to_message.from_user if m.reply_to_message else None
    is_clone = getattr(c, 'is_clone', False)
    logging.info(f"[DRAFT] {p1.id} initiated {'DRAFT_V2' if is_v2 else 'DRAFT'} {'(clone)' if is_clone else '(main)'}")
    
    if not p2 or p2.is_bot or p1.id == p2.id:
        logging.warning(f"[DRAFT] {p1.id} invalid opponent - bot:{p2.is_bot if p2 else 'None'}, same:{p1.id == p2.id if p2 else 'N/A'}")
        return await m.reply("⚠️ Invalid opponent.")

    # Series Filter
    series_filter = None
    if len(m.text.split()) > 1:
        raw = re.sub(r'[^a-z0-9]', '', m.text.split(maxsplit=1)[1].lower())
        if raw in SERIES_MAP:
            series_filter = raw
            logging.debug(f"[DRAFT] Series filter applied: {raw}")
        else:
            logging.warning(f"[DRAFT] Invalid series filter: {raw}")
            return await m.reply("❌ Series not found.")

    # Save Active Data
    database.save_id_set(database.CHATS_FILE, {m.chat.id}, client=c)
    database.save_id_set(database.USERS_FILE, {p1.id, p2.id}, client=c)

    game_id = f"{m.chat.id}_{int(time.time())}_{random.randint(100,999)}"
    mode = "draft_v2" if is_v2 else "draft"
    logging.debug(f"[DRAFT] Created game_id {game_id} for {p1.id} vs {p2.id} in chat {m.chat.id}")
    
    game = {
        "game_id": game_id, "status": "waiting", "mode": mode, "turn": p1.id,
        "chat_id": m.chat.id, "chat_title": getattr(m.chat, "title", None),
        "last_activity": time.time(), "filter": series_filter,
        "p1": {"id": p1.id, "name": p1.first_name, "team": {}, "skips": 2},
        "p2": {"id": p2.id, "name": p2.first_name, "team": {}, "skips": 2},
        "used_chars": [], "ready": {"p1": False, "p2": False}
    }

    # Roles for this game (default to anime roles)
    game["roles"] = ROLES

    with global_state.GAMES_LOCK:
        global_state.GAMES.setdefault(m.chat.id, {})[game_id] = game

    s_name = SERIES_DISPLAY.get(series_filter, "All Anime")
    kb = [[InlineKeyboardButton("✅ Accept Battle", callback_data=f"accept_{game_id}")]]

    p1_link = f"<a href='tg://user?id={p1.id}'>{html.escape(p1.first_name)}</a>"
    p2_link = f"<a href='tg://user?id={p2.id}'>{html.escape(p2.first_name)}</a>"

    card = rich_ui.build_rich_card(
        title=f"⚔️ {mode.upper().replace('_', ' ')} Challenge",
        badges=[f"Series: {s_name}", "Draft Match"],
        description=f"<b>{p1_link}</b> challenges <b>{p2_link}</b> to an Anime Draft Battle!",
        expandable_quote="Rules:\n• Turn-based character draft\n• 8 unique roles to fill\n• Strategy & stat synergy determine victory"
    )
    msg_id = await rich_ui.send_rich_message(c, m.chat.id, card, reply_markup=kb)
    if msg_id:
        game['display_message'] = {'chat_id': m.chat.id, 'msg_id': msg_id}
    logging.info(f"[DRAFT] Draft initialized - {p1.id} vs {p2.id}, waiting for acceptance")
    await log_event(
        c,
        'DRAFT INITIALIZED',
        actor=p1,
        chat=m.chat,
        details=f'{mode} • {p1.first_name} vs {p2.first_name} • series: {s_name}',
    )

@Client.on_message(filters.command(["pokemondraft", "pdraft"]))
async def pokemon_draft_cmd(c, m):
    database.set_current_client(c)
    p1 = m.from_user
    p2 = m.reply_to_message.from_user if m.reply_to_message else None
    is_clone = getattr(c, 'is_clone', False)
    logging.info(f"[PDRAFT] {p1.id} initiated Pokemon Draft {'(clone)' if is_clone else '(main)'}")
    
    if not m.reply_to_message:
        logging.warning(f"[PDRAFT] {p1.id} missing opponent (no reply)")
        return await m.reply("⚠️ Reply to a friend!")
    if p2.is_bot or p1.id == p2.id:
        logging.warning(f"[PDRAFT] {p1.id} invalid opponent - bot:{p2.is_bot}, same:{p1.id == p2.id}")
        return await m.reply("⚠️ Invalid opponent.")

    with global_state.GAMES_LOCK:
        if len(global_state.GAMES.get(m.chat.id, {})) >= MAX_ACTIVE_DRAFTS_PER_CHAT:
            logging.warning(f"[PDRAFT] {p1.id} too many active drafts in chat {m.chat.id}")
            return await m.reply("⚠️ Too many active drafts here.")

    # Filters
    args = m.text.split()[1:]
    filters_map = {"regions": [], "legendary_status": None}
    for arg in args:
        if arg.lower() == "6l": filters_map["legendary_status"] = True
        elif arg.lower() == "0l": filters_map["legendary_status"] = False
        else: filters_map["regions"].append(arg.capitalize())
    
    if filters_map["regions"] or filters_map["legendary_status"] is not None:
        logging.debug(f"[PDRAFT] Filters applied: regions={filters_map['regions']}, legendary={filters_map['legendary_status']}")

    # Validate filter has enough Pokemon (need 14 minimum for 2 players × 7 slots)
    available = POKEMON_LIST.copy()
    if filters_map["regions"]:
        available = [p for p in available if POKEMON_DATA[p]["region"] in filters_map["regions"]]
    if filters_map["legendary_status"] is not None:
        available = [p for p in available if POKEMON_DATA[p]["is_legendary"] == filters_map["legendary_status"]]
    
    if len(available) < 14:
        logging.warning(f"[PDRAFT] {p1.id} insufficient Pokemon: {len(available)}/14")
        return await m.reply(f"❌ Not enough Pokémon! ({len(available)}/14 needed)")

    game_id = f"{m.chat.id}_{int(time.time())}_{random.randint(100,999)}"
    logging.debug(f"[PDRAFT] Created game_id {game_id} for {p1.id} vs {p2.id}, {len(available)} Pokemon available")
    
    game = {
        "game_id": game_id, "status": "waiting", "mode": "pdraft", "turn": p1.id,
        "chat_id": m.chat.id, "chat_title": getattr(m.chat, "title", None),
        "last_activity": time.time(), "filters": filters_map,
        "p1": {"id": p1.id, "name": p1.first_name, "team": {}, "skips": 2},
        "p2": {"id": p2.id, "name": p2.first_name, "team": {}, "skips": 2},
        "used_players": []
    }

    with global_state.GAMES_LOCK:
        global_state.GAMES.setdefault(m.chat.id, {})[game_id] = game

    kb = [[InlineKeyboardButton("✅ Accept Challenge", callback_data=f"paccept_{game_id}")]]
    p1_link = f"<a href='tg://user?id={p1.id}'>{html.escape(p1.first_name)}</a>"
    p2_link = f"<a href='tg://user?id={p2.id}'>{html.escape(p2.first_name)}</a>"

    card = rich_ui.build_rich_card(
        title="⚔️ Pokémon Draft Challenge",
        badges=["Pokémon Draft", "Stat Battle"],
        description=f"<b>{p1_link}</b> challenges <b>{p2_link}</b> to a Pokémon Draft!"
    )
    msg_id = await rich_ui.send_rich_message(c, m.chat.id, card, reply_markup=kb)
    if msg_id:
        game['display_message'] = {'chat_id': m.chat.id, 'msg_id': msg_id}
    logging.info(f"[PDRAFT] Draft initialized - {p1.id} vs {p2.id}, waiting for acceptance")
    await log_event(c, 'POKÉMON DRAFT INITIALIZED', actor=p1, chat=m.chat, details=f'{p1.first_name} vs {p2.first_name}')


@Client.on_message(filters.command(["cdraft"]))
async def comic_draft_cmd(c, m):
    database.set_current_client(c)
    # Marvel / Comics draft command with series filtering
    p1 = m.from_user
    p2 = m.reply_to_message.from_user if m.reply_to_message else None
    is_clone = getattr(c, 'is_clone', False)
    logging.info(f"[CDRAFT] {p1.id} initiated Comics Draft {'(clone)' if is_clone else '(main)'}")
    
    if not m.reply_to_message:
        logging.warning(f"[CDRAFT] {p1.id} missing opponent (no reply)")
        return await m.reply("⚠️ Reply to a friend!")
    if not p2 or p2.is_bot or p1.id == p2.id:
        logging.warning(f"[CDRAFT] {p1.id} invalid opponent - bot:{p2.is_bot if p2 else 'None'}, same:{p1.id == p2.id if p2 else 'N/A'}")
        return await m.reply("⚠️ Invalid opponent.")

    with global_state.GAMES_LOCK:
        if len(global_state.GAMES.get(m.chat.id, {})) >= MAX_ACTIVE_DRAFTS_PER_CHAT:
            logging.warning(f"[CDRAFT] {p1.id} too many active drafts in chat {m.chat.id}")
            return await m.reply("⚠️ Too many active drafts here.")

    # Load Marvel pool from data/marvel.json
    marvel_file = os.path.join(DATA_DIR, "marvel.json")
    if not os.path.exists(marvel_file):
        logging.error(f"[CDRAFT] Marvel data file not found at {marvel_file}")
        return await m.reply("❌ Marvel data not found.")

    try:
        with open(marvel_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        logging.debug(f"[CDRAFT] Loaded Marvel data with {len(data)} characters")
    except Exception as e:
        logging.error(f"[CDRAFT] Failed to load Marvel data: {e}")
        return await m.reply("❌ Failed to load Marvel data.")

    # Series Filter (like draft)
    series_filter = None
    series_name = "All Marvel"
    if len(m.text.split()) > 1:
        raw = re.sub(r'[^a-z0-9]', '', m.text.split(maxsplit=1)[1].lower())
        # Check if filter matches any series in marvel data
        for entry in data:
            if "series" in entry:
                entry_series = re.sub(r'[^a-z0-9]', '', entry["series"].lower())
                if entry_series == raw:
                    series_filter = raw
                    series_name = entry["series"]
                    break
        if not series_filter:
            logging.warning(f"[CDRAFT] {p1.id} invalid series filter: {raw}")
            return await m.reply("❌ Series not found in Marvel data.")
        else:
            logging.debug(f"[CDRAFT] Series filter applied: {series_name}")

    pool = []
    for entry in data:
        name = entry.get('name')
        stats = entry.get('stats')
        img = entry.get('img')
        series = entry.get('series', 'Marvel')
        
        # Filter by series if specified
        if series_filter:
            entry_series = re.sub(r'[^a-z0-9]', '', series.lower())
            if entry_series != series_filter:
                continue
        
        if not name or not stats: continue
        uid = f"{name} | Marvel"
        pool.append(uid)
        # register stats/images into global cache so battle logic can find them
        if uid not in global_state.CHAR_STATS:
            global_state.CHAR_STATS[uid] = stats
        if img:
            global_state.CHAR_IMAGES[uid] = img

    if not pool:
        logging.warning(f"[CDRAFT] {p1.id} no characters found for series filter")
        return await m.reply("❌ No characters found for that series.")

    logging.debug(f"[CDRAFT] Character pool created with {len(pool)} characters")

    # Save Active Data
    database.save_id_set(database.CHATS_FILE, {m.chat.id}, client=c)
    database.save_id_set(database.USERS_FILE, {p1.id, p2.id}, client=c)

    game_id = f"{m.chat.id}_{int(time.time())}_{random.randint(100,999)}"
    logging.debug(f"[CDRAFT] Created game_id {game_id} for {p1.id} vs {p2.id}")
    
    game = {
        "game_id": game_id, "status": "waiting", "mode": "cdraft", "turn": p1.id,
        "chat_id": m.chat.id, "chat_title": getattr(m.chat, "title", None),
        "last_activity": time.time(), "filter": series_filter,
        "p1": {"id": p1.id, "name": p1.first_name, "team": {}, "skips": 2},
        "p2": {"id": p2.id, "name": p2.first_name, "team": {}, "skips": 2},
        "used_chars": [], "ready": {"p1": False, "p2": False},
        "pool": pool,
        "roles": MARVEL_ROLES
    }

    with global_state.GAMES_LOCK:
        global_state.GAMES.setdefault(m.chat.id, {})[game_id] = game

    kb = [[InlineKeyboardButton("✅ Accept Battle", callback_data=f"accept_{game_id}")]]
    p1_link = f"<a href='tg://user?id={p1.id}'>{html.escape(p1.first_name)}</a>"
    p2_link = f"<a href='tg://user?id={p2.id}'>{html.escape(p2.first_name)}</a>"

    card = rich_ui.build_rich_card(
        title="⚔️ Comics Draft Challenge",
        badges=[f"Series: {series_name}", "Comics Battle"],
        description=f"<b>{p1_link}</b> challenges <b>{p2_link}</b> to a Marvel/Comics Draft!"
    )
    msg_id = await rich_ui.send_rich_message(c, m.chat.id, card, reply_markup=kb)
    if msg_id:
        game['display_message'] = {'chat_id': m.chat.id, 'msg_id': msg_id}
    logging.info(f"[CDRAFT] Draft initialized - {p1.id} vs {p2.id}, waiting for acceptance")
    await log_event(c, 'COMIC DRAFT INITIALIZED', actor=p1, chat=m.chat, details=f'{p1.first_name} vs {p2.first_name} • series: {series_name}')
