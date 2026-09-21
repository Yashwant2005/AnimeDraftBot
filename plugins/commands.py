from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ParseMode
import html
import json
import database
import global_state
import ui
import logging
import os
import clone_manager
from config import DATA_DIR
from utils import is_admin, get_top_3_users, is_mod, is_owner
import database
import clone_manager
from pyrogram.enums import ParseMode
from config import DATA_DIR, API_ID, API_HASH
import os

@Client.on_message(filters.command("start"))
async def start_cmd(c, m):
    user = m.from_user
    logging.info(f"[CMD] /start invoked by {user.id} ({user.first_name})")
    await m.reply_text(
        "👋 <b>Welcome to Anime Draft Wars!</b>\n\n"
        "Draft a team of 8 anime characters and battle friends!\n"
        "Use /draft to play.\n"
        "Use /guide for the short guide or /commands for command usage.",
        parse_mode=ParseMode.HTML
    )

_GUIDE_PAGES = [
    "📚 **Bot Guide - Page 1 (General & Minigames)**\n\n"
    "**General**\n"
    "• `/start` - Start the bot\n"
    "• `/profile` - View your profile and stats\n"
    "• `/leaderboard` - Choose a leaderboard\n"
    "• `/topglobal` - Choose a global leaderboard\n"
    "• `/topgroup` - Choose this group's leaderboard\n"
    "• `/commands` - Full usage guide for every active command\n"
    "• `/list` - List available series\n"
    "• `/clone` - Clone the bot\n\n"
    "**Minigames**\n"
    "• `/shambles` - Word search game\n"
    "• `/whoswho` - Blurred character guess\n"
    "• `/punk` - Detail guessing game\n"
    "• `/answer <name>` - Submit answer for solo games\n"
    "• `/docq` - Apple explosion minigame\n"
    "• `/roulette` - Russian roulette\n"
    "• `/rps` - Rock paper scissors",

    "📚 **Bot Guide - Page 2 (Economy & Trading)**\n\n"
    "**Economy & Trading**\n"
    "• `/balance` - Check your coins\n"
    "• `/daily` - Claim daily free coins\n"
    "• `/trade` / `/gift` - Trade/gift users",

    "📚 **Bot Guide - Page 3 (RPG & Combat)**\n\n"
    "• `/bounty` - Check bounty stats\n"
    "• `/fight` / `/plunder` - Attack other users",

    "📚 **Bot Guide - Page 4 (Admins)**\n\n"
    "**Admin Management**\n"
    "• `/add` / `/addpoints` - Give currency/points\n"
    "• `/characters` - Manage DB\n"
    "• `/acast` - Broadcast message\n"
    "• `/sudo` / `/mods` - Manage admins\n"
    "• `/exportdb` - Export database\n"
    "• `/status` - Check bot status"
]

@Client.on_message(filters.command("guide"))
async def guide_cmd(c, m):
    logging.info(f"[CMD] /guide invoked by {m.from_user.id}")

    kb = InlineKeyboardMarkup([[InlineKeyboardButton("Next ➡️", callback_data="guide_1")]])
    await m.reply_text(
        _GUIDE_PAGES[0],
        reply_markup=kb,
        parse_mode=ParseMode.MARKDOWN
    )

@Client.on_callback_query(filters.regex(r"^guide_(\d+)$"))
async def guide_cb(client, callback_query):
    page = int(callback_query.matches[0].group(1))

    if page < 0 or page >= len(_GUIDE_PAGES):
        return await callback_query.answer("Invalid page.", show_alert=True)

    kb = []
    row = []
    if page > 0:
        row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"guide_{page-1}"))
    if page < len(_GUIDE_PAGES) - 1:
        row.append(InlineKeyboardButton("Next ➡️", callback_data=f"guide_{page+1}"))
    if row:
        kb.append(row)

    await callback_query.edit_message_text(
        _GUIDE_PAGES[page],
        reply_markup=InlineKeyboardMarkup(kb) if kb else None,
        parse_mode=ParseMode.MARKDOWN
    )

_COMMANDS_PAGES = [
    (
        "Start + Info",
        "📘 <b>Commands Guide - Start + Info</b>\n\n"
        "<b>/start</b>\n"
        "<code>/start</code> - Open the bot intro.\n\n"
        "<b>/guide</b>\n"
        "<code>/guide</code> - Short feature guide with next/prev buttons.\n\n"
        "<b>/commands</b>\n"
        "<code>/commands</code> - Open this full usage guide.\n"
        "<code>/commands 3</code> - Open a specific guide page.\n\n"
        "<b>/profile</b>\n"
        "<code>/profile</code> - Show your draft points, matches, wins, rank, and achievements.\n\n"
        "<b>/balance</b>\n"
        "<code>/balance</code> - Show your coins.\n\n"
        "<b>/daily</b>\n"
        "<code>/daily</code> - Claim daily coins.\n\n"
        "<b>/list</b>\n"
        "<code>/list</code> - Show anime series.\n"
        "<code>/list comics</code> - Show comic series.\n\n"
        "<b>/bounty</b>\n"
        "<code>/bounty</code> - Show your bounty profile with wanted poster when available.\n"
        "<code>/bounty</code> as a reply - Show that user's bounty profile/poster.\n\n"
        "<b>/status</b>\n"
        "<code>/status</code> - Show active bot users and chats."
    ),
    (
        "Leaderboards",
        "🏆 <b>Commands Guide - Leaderboards</b>\n\n"
        "<b>Open selector</b>\n"
        "<code>/leaderboard</code> or <code>/top</code> - Ask which leaderboard to view.\n"
        "<code>/topglobal</code> - Open global leaderboard selector.\n"
        "<code>/topgroup</code> or <code>/grouptop</code> - Open group leaderboard selector.\n\n"
        "<b>Direct global boards</b>\n"
        "<code>/topglobal draft</code> - Draft points.\n"
        "<code>/topglobal draft wins</code> - Most draft wins.\n"
        "<code>/topglobal bounty</code> - Bounty ranking.\n"
        "<b>Direct group boards</b>\n"
        "<code>/topgroup draft</code> - Group draft points.\n"
        "<code>/topgroup draft wins</code> - Group draft wins.\n"
        "<code>/topgroup bounty</code> - Group bounty ranking.\n"
        "Use the buttons under a leaderboard to switch Global/Group, board type, metric, and page."
    ),
    (
        "Draft + Combat",
        "⚔️ <b>Commands Guide - Draft + Combat</b>\n\n"
        "<b>Draft battles</b>\n"
        "<code>/draft</code> as a reply - Challenge a user to anime draft.\n"
        "<code>/draft Naruto</code> as a reply - Draft only from that series.\n"
        "<code>/draft_v2</code> as a reply - Start draft v2.\n"
        "<code>/pokemondraft</code> or <code>/pdraft</code> as a reply - Start Pokemon draft.\n"
        "<code>/pdraft Kanto 0l</code> - Region filter, no legendary Pokemon.\n"
        "<code>/pdraft Sinnoh 6l</code> - Region filter, legendary Pokemon only.\n"
        "<code>/cdraft</code> as a reply - Start comics draft.\n"
        "<code>/cdraft Avengers</code> as a reply - Comics draft filtered by series.\n\n"
        "<b>Combat</b>\n"
        "<code>/duel</code> as a reply - Character duel challenge.\n"
        "<code>/fight</code> as a reply - Fight a user for 10% bounty swing.\n"
        "<code>/plunder</code> as a reply - Riskier attack: steal 20% on success, lose 15% on fail.\n\n"
        "For reply commands, reply to the opponent's message and send the command."
    ),
    (
        "Games",
        "🎮 <b>Commands Guide - Games</b>\n\n"
        "<b>Lobby guessing games</b>\n"
        "<code>/shambles 100</code> - Create Shambles lobby with 100 coin entry.\n"
        "<code>/whoswho 100</code> - Create Who's Who lobby with 100 coin entry.\n"
        "<code>/punk 100</code> - Create Punk Records lobby with 100 coin entry.\n"
        "<code>/punk one piece</code> - Create Punk Records for one anime with default entry.\n"
        "<code>/punk 100 one piece</code> - Create Punk Records with entry fee and anime filter.\n"
        "<code>/answer Luffy</code> - Submit an answer after the host starts the lobby.\n"
        "<code>/cancelgame</code> - Cancel the active guessing lobby/game.\n\n"
        "Players join with the Join button. Host starts with Start. Winner gets 90% of the pot. If cancelled mid-game, joined players get 40% back. Only host or group admins can cancel.\n\n"
        "<b>Button minigames</b>\n"
        "<code>/docq</code> - Pick one of two apples; one is explosive.\n"
        "<code>/roulette</code> as a reply - Start Russian Roulette with default 100 coin wager.\n"
        "<code>/roulette 500</code> as a reply - Start roulette with 500 coin wager.\n"
        "<code>/rps</code> as a reply - Rock Paper Scissors challenge.\n\n"
        "Roulette is 1v1: players take turns clicking chambers; the middle button is locked, and the player who hits the bullet loses."
    ),
    (
        "Admin",
        "🛠 <b>Commands Guide - Admin</b>\n\n"
        "<b>Broadcast + status</b>\n"
        "<code>/acast message text</code> - Broadcast to saved chats.\n"
        "<code>/status</code> - Show active users/chats.\n"
        "<code>/exportdb</code> or <code>/backupdb</code> - Export database backup.\n\n"
        "<b>Draft database</b>\n"
        "<code>/add Character Name</code> - Add anime character using multiline format: command line, anime, image URL, then stats.\n"
        "<code>/add_comic Character Name</code> - Add comic character using multiline format: command line, series, image URL, then stats.\n"
        "<code>/comicimg \"url\" \"name\" \"series name\"</code> - Update comic character image.\n"
        "<code>/characters</code> - Browse anime character database.\n"
        "<code>/characters comics</code> - Browse comics database.\n\n"
        "<b>User/admin management</b>\n"
        "<code>/addpoints USER_ID POINTS</code> - Add or remove draft points.\n"
        "<code>/newyear on</code> / <code>/newyear off</code> - Toggle event mode.\n"
        "<code>/sudo promote</code> as a reply - Promote moderator. Owner only.\n"
        "<code>/sudo demote @username</code> - Demote moderator. Owner only.\n"
        "<code>/mods</code> - List moderators.\n"
        "<code>/add_sudo</code> as a reply - Promote a user to clone admin.\n\n"
        "<b>Clone</b>\n"
        "<code>/clone BOT_TOKEN</code> - Register and start your cloned bot."
    ),
]

def _commands_markup(page):
    rows = []
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("Prev", callback_data=f"cmds:{page - 1}"))
    nav.append(InlineKeyboardButton(f"{page + 1}/{len(_COMMANDS_PAGES)}", callback_data="cmds:noop"))
    if page < len(_COMMANDS_PAGES) - 1:
        nav.append(InlineKeyboardButton("Next", callback_data=f"cmds:{page + 1}"))
    rows.append(nav)

    shortcut_rows = [
        [("Info", 0), ("Top", 1), ("Draft", 2)],
        [("Games", 3), ("Admin", 4)],
    ]
    for shortcut_row in shortcut_rows:
        rows.append([
            InlineKeyboardButton(label, callback_data="cmds:noop" if idx == page else f"cmds:{idx}")
            for label, idx in shortcut_row
        ])
    return InlineKeyboardMarkup(rows)

def _commands_text(page):
    title, body = _COMMANDS_PAGES[page]
    return f"{body}\n\n<b>Page:</b> {page + 1}/{len(_COMMANDS_PAGES)} - {title}"

def _commands_page_from_message(message):
    try:
        raw_page = (message.command or [None, "1"])[1]
        return max(0, min(len(_COMMANDS_PAGES) - 1, int(raw_page) - 1))
    except (IndexError, TypeError, ValueError):
        return 0

@Client.on_message(filters.command(["commands", "cmds"]))
async def commands_cmd(c, m):
    logging.info(f"[CMD] /commands invoked by {m.from_user.id}")
    page = _commands_page_from_message(m)
    await m.reply_text(
        _commands_text(page),
        parse_mode=ParseMode.HTML,
        reply_markup=_commands_markup(page),
        disable_web_page_preview=True,
    )

@Client.on_callback_query(filters.regex(r"^cmds:(noop|\d+)$"))
async def commands_cb(client, callback_query):
    page_raw = callback_query.matches[0].group(1)
    if page_raw == "noop":
        return await callback_query.answer()

    page = max(0, min(len(_COMMANDS_PAGES) - 1, int(page_raw)))
    await callback_query.answer()
    await callback_query.edit_message_text(
        _commands_text(page),
        parse_mode=ParseMode.HTML,
        reply_markup=_commands_markup(page),
        disable_web_page_preview=True,
    )

@Client.on_message(filters.command("list"))
async def list_cmd(c, m):
    logging.info(f"[CMD] /list invoked by {m.from_user.id} with text: {m.text}")
    mode = "anime"
    # Check if user specified comics mode
    if len(m.text.split()) > 1 and m.text.split()[1].lower() == "comics":
        mode = "comics"
    
    if mode == "anime":
        if not global_state.SERIES_DISPLAY:
            return await m.reply_text("📚 No anime series found.")
        
        series = sorted(set(global_state.SERIES_DISPLAY.values()))
    else:  # comics
        if not global_state.MARVEL_SERIES_DISPLAY:
            return await m.reply_text("🎬 No comics found.")
        
        series = sorted(set(global_state.MARVEL_SERIES_DISPLAY.values()))
    
    page = 1
    if len(m.text.split()) > 1:
        try:
            page = max(1, int(m.text.split()[1]))
        except ValueError:
            page = 1
    
    per_page = 25
    total_pages = (len(series) + per_page - 1) // per_page
    page = min(page, total_pages)
    
    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    page_series = series[start_idx:end_idx]
    
    if mode == "anime":
        txt = f"📚 <b>Available Anime Series</b> (Page {page}/{total_pages})\n\n" + "\n".join(f"• {html.escape(s)}" for s in page_series)
    else:
        txt = f"🎬 <b>Available Comics</b> (Page {page}/{total_pages})\n\n" + "\n".join(f"• {html.escape(s)}" for s in page_series)
    
    kb = []
    row = []
    if page > 1:
        row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"list_{mode}_{page-1}"))
    if page < total_pages:
        row.append(InlineKeyboardButton("Next ➡️", callback_data=f"list_{mode}_{page+1}"))
    if row:
        kb.append(row)
    
    # Add toggle button for Anime/Comics
    toggle_btn = InlineKeyboardButton("🎬 Comics", callback_data="list_comics_1") if mode == "anime" else InlineKeyboardButton("🎭 Anime", callback_data="list_anime_1")
    kb.append([toggle_btn])
    
    await m.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb) if kb else None, parse_mode=ParseMode.HTML)

@Client.on_message(filters.command("profile"))
async def profile_cmd(c, m):
    user = m.from_user
    logging.info(f"[CMD] /profile invoked by {user.id}")
    stats = database.get_user_stats(user.id, user.first_name)

    wins = stats.get("wins", 0)
    matches = stats.get("matches", 0)
    rating = stats.get("rating", 1200)
    wr = round((wins / matches * 100), 1) if matches > 0 else 0

    # Get top 3 users
    top_3 = get_top_3_users()
    is_top_3 = user.id in top_3

    if rating < 1000: rank = "🐣 Novice"
    elif rating < 1300: rank = "🥉 Bronze"
    elif rating < 1600: rank = "🥈 Silver"
    elif rating < 2000: rank = "🥇 Gold"
    elif rating < 2500: rank = "💎 Diamond"
    elif is_top_3: rank = "👑 Anime King"
    else: rank = "⭐ Legendary"

    # Achievements
    user_achis = global_state.USER_ACHIEVEMENTS.get(str(user.id), [])
    achievements_txt = "\n".join([f"  {global_state.AVAILABLE_ACHIEVEMENTS.get(k, k)}" for k in user_achis]) if user_achis else "  No achievements yet"

    txt = (
        f"👤 <b>Profile: {html.escape(user.first_name)}</b>\n\n"
        f"🏅 <b>Rank:</b> {rank}\n"
        f"💠 <b>Points:</b> {rating}\n"
        f"⚔️ <b>Matches:</b> {matches}\n"
        f"🏆 <b>Wins:</b> {wins}\n"
        f"📈 <b>Win Rate:</b> {wr}%\n\n"
        f"🎖 <b>Achievements:</b>\n{achievements_txt}"
    )
    await m.reply(txt, parse_mode=ParseMode.HTML)


@Client.on_message(filters.command("clone"))
async def clone_cmd(c, m):
    """Register a cloned bot token with the main bot.
    Usage: /clone <BOT_TOKEN>
    This stores the token linked to the user so main admins can manage mods.
    """
    database.set_current_client(c)
    logging.info(f"[CLONE CMD] User {m.from_user.id} executing /clone command")
    try:
        parts = m.text.split(maxsplit=1)
        if len(parts) < 2:
            logging.warning(f"[CLONE CMD] Invalid usage from {m.from_user.id}: no token provided")
            return await m.reply_text("❌ Usage: /clone <BOT_TOKEN>\nYour token will be saved locally for management.")
        
        token = parts[1].strip()
        if not token or len(token) < 10:
            logging.warning(f"[CLONE CMD] Invalid token from {m.from_user.id}: too short or empty")
            return await m.reply_text("❌ Invalid bot token. Please check and try again.")
        
        logging.info(f"[CLONE CMD] Saving token for user {m.from_user.id}")
        # Ensure mod entry exists
        try:
            database.add_mod(m.from_user.id, getattr(m.from_user, 'username', None), m.from_user.first_name, added_by=m.from_user.id)
            database.update_mod_token(m.from_user.id, token)
            logging.info(f"[CLONE CMD] Mod entry updated for user {m.from_user.id}")
        except Exception as e:
            logging.error(f"[CLONE CMD] Error saving mod token for {m.from_user.id}: {e}", exc_info=True)
            return await m.reply_text(f"❌ Error saving token: {e}")

        # Start clone client automatically in background
        logging.info(f"[CLONE CMD] Attempting to start clone for user {m.from_user.id}")
        started = False
        try:
            started = clone_manager.start_clone(m.from_user.id, token)
        except Exception as e:
            logging.error(f"[CLONE CMD] Error starting clone for {m.from_user.id}: {e}", exc_info=True)
            started = False

        if started:
            logging.info(f"[CLONE CMD] Clone started successfully for user {m.from_user.id}")
            await m.reply_text(
                "✅ Your bot token was saved and your cloned bot was started! \n"
                "✨ All game commands (draft, pdraft, cdraft, draftv2) work in the cloned bot.\n"
                "📢 Only /broadcast is available for admins (main bot admins can also broadcast).\n"
                "🚫 Other admin commands are disabled on clones.\n"
                "📊 Your clone has its own separate database & leaderboard!"
            )
        else:
            logging.warning(f"[CLONE CMD] Auto-start failed for user {m.from_user.id}, writing fallback runner script")
            # Fallback: write runner script
            try:
                runner_name = f"clone_runner_{m.from_user.id}.py"
                runner_path = os.path.join(DATA_DIR, runner_name)
                runner_content = f"""from pyrogram import Client
from config import API_ID, API_HASH

# Your bot token (already set by the main bot):
BOT_TOKEN = '{token}'

app = Client('clone_bot_{m.from_user.id}', api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, plugins=dict(root='plugins'))

if __name__ == '__main__':
    app.run()
"""
                with open(runner_path, 'w', encoding='utf-8') as f:
                    f.write(runner_content)
                logging.info(f"[CLONE CMD] Fallback runner script written to {runner_path}")
                await m.reply_text(
                    f"⚠️ Auto-start failed, but bot token was saved.\n"
                    f"📄 Runner file saved to: `{runner_path}`\n"
                    f"💡 Run that file to manually start your cloned bot.",
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception as e:
                logging.error(f"[CLONE CMD] Error writing runner script: {e}", exc_info=True)
                await m.reply_text(
                    "⚠️ Auto-start failed. Bot token was saved.\n"
                    "💡 Please try /clone again with the token, or contact support."
                )
    except Exception as e:
        logging.exception(f"[CLONE CMD] Unexpected error for user {m.from_user.id}: {e}")
        await m.reply_text(f"❌ Unexpected error: {e}")


@Client.on_message(filters.command("add_sudo"))
async def add_sudo_cmd(c, m):
    """Main-admins can reply to a user's message with /add_sudo to promote them to a mod.

    Usage: reply to a user message with /add_sudo
    """
    logging.info(f"[CMD] /add_sudo invoked by {m.from_user.id}")
    # Only allow owners or admins
    allowed = False
    try:
        if is_owner(m.from_user.id):
            allowed = True
        else:
            # is_admin is async
            if await is_admin(m.from_user.id):
                allowed = True
    except Exception:
        allowed = False

    if not allowed:
        return await m.reply_text("❌ Only main admins can use this command.")

    if not m.reply_to_message or not m.reply_to_message.from_user:
        return await m.reply_text("❌ Reply to the user's message you want to promote, then send /add_sudo")

    target = m.reply_to_message.from_user
    ok = database.add_mod(target.id, getattr(target, 'username', None), target.first_name, added_by=m.from_user.id)
    if ok:
        await m.reply_text(f"✅ User {target.id} promoted to mod (clone admin). They can use limited admin features.")
    else:
        await m.reply_text(f"⚠️ User {target.id} is already a mod.")


@Client.on_message(filters.command("status"))
async def status_cmd(c, m):
    database.set_current_client(c)
    user_id = m.from_user.id
    is_clone = getattr(c, 'is_clone', False)
    if not await is_admin(user_id):
        return await m.reply("Admin only.")
    logging.info(f"[STATUS] User {user_id} (admin) requesting status {'(clone)' if is_clone else '(main)'}")
    
    c_count = len(database.load_id_set(database.CHATS_FILE, client=c))
    u_count = len(database.load_id_set(database.USERS_FILE, client=c))
    logging.info(f"[STATUS] Sending stats: {u_count} users, {c_count} chats (from user {user_id})")
    await m.reply(f"📊 **Bot Statistics**\n👥 Active Users: **{u_count}**\n💬 Active Chats: **{c_count}**")


_LB_PAGE_SIZE = 10
_LB_BOARDS = {
    "draft": "Draft",
    "bounty": "Bounty",
}

def _lb_is_group_chat(chat) -> bool:
    chat_type = str(getattr(chat, "type", "")).lower()
    return "group" in chat_type or "supergroup" in chat_type

def _lb_user_link(user_id, name) -> str:
    safe_name = html.escape(str(name or user_id or "Unknown"))
    if user_id:
        return f"<a href='tg://user?id={user_id}'>{safe_name}</a>"
    return safe_name

def _lb_default_metric(board):
    if board == "draft":
        return "points"
    return "value"

def _lb_scope_from_command(command_parts):
    command = (command_parts[0] if command_parts else "leaderboard").lower()
    args = [arg.lower() for arg in command_parts[1:]]
    if command in ("topgroup", "grouptop") or any(arg in ("group", "local", "chat") for arg in args):
        return "group"
    return "global"

def _lb_board_from_command(command_parts):
    args = [arg.lower() for arg in command_parts[1:]]
    for arg in args:
        if arg in ("draft", "normal", "points", "point", "wins", "win"):
            return "draft"
        if arg in ("bounty", "coin", "coins", "belly"):
            return "bounty"
    return None

def _lb_metric_from_command(board, command_parts):
    args = [arg.lower() for arg in command_parts[1:]]
    if board == "draft" and any(arg in ("wins", "win", "mostwins") for arg in args):
        return "wins"
    return _lb_default_metric(board)

def _lb_page_from_command(command_parts):
    for arg in command_parts[1:]:
        if str(arg).isdigit():
            return max(1, int(arg))
    return 1

def _lb_selector_markup(scope):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Draft", callback_data=f"lb:draft:{scope}:points:1")],
        [InlineKeyboardButton("Bounty", callback_data=f"lb:bounty:{scope}:value:1")],
        [
            InlineKeyboardButton("Global", callback_data="lbsel:global"),
            InlineKeyboardButton("Group", callback_data="lbsel:group"),
        ],
    ])

def _lb_selector_text(scope):
    scope_title = "Global" if scope == "global" else "Group"
    return (
        f"🏆 <b>{scope_title} Leaderboards</b>\n\n"
        "Which leaderboard do you want to see?"
    )

def _lb_col_draft(scope):
    if scope == "group":
        return database.COL_GROUP_LEADERBOARD if database.USE_MONGO else None
    clone_db = database.get_clone_db()
    if clone_db is not None:
        return clone_db["leaderboard"]
    return database.COL_LEADERBOARD if database.USE_MONGO else None

def _lb_draft_entries(scope, chat_id, metric, page):
    sort_key = "wins" if metric == "wins" else "rating"
    secondary = "rating" if metric == "wins" else "wins"
    start = (page - 1) * _LB_PAGE_SIZE
    col = _lb_col_draft(scope)
    if col is not None:
        query = {"matches": {"$gt": 0}}
        if scope == "group":
            query["chat_id"] = chat_id
        docs = list(col.find(query).sort([(sort_key, -1), (secondary, -1)]).skip(start).limit(_LB_PAGE_SIZE))
        total = col.count_documents(query)
        return [
            {
                "id": d.get("user_id"),
                "name": d.get("name") or d.get("user_id"),
                "rating": d.get("rating", 1200),
                "wins": d.get("wins", 0),
                "matches": d.get("matches", 0),
            }
            for d in docs
        ], total

    if scope == "global" and os.path.exists(database.LEADERBOARD_FILE):
        with open(database.LEADERBOARD_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        rows = [dict(stats, user_id=int(uid) if str(uid).isdigit() else uid) for uid, stats in data.items()]
        rows = [r for r in rows if r.get("matches", 0) > 0]
        rows.sort(key=lambda r: (r.get(sort_key, 0), r.get(secondary, 0)), reverse=True)
        docs = rows[start:start + _LB_PAGE_SIZE]
        return [
            {
                "id": d.get("user_id"),
                "name": d.get("name") or d.get("user_id"),
                "rating": d.get("rating", 1200),
                "wins": d.get("wins", 0),
                "matches": d.get("matches", 0),
            }
            for d in docs
        ], len(rows)

    return [], 0

def _lb_bounty_entries(scope, chat_id, page):
    if not database.USE_MONGO or database.COL_WAIFU_USERS is None:
        return None, 0
    start = (page - 1) * _LB_PAGE_SIZE
    score_expr = {"$ifNull": ["$bounty", {"$ifNull": ["$coins", 0]}]}

    if scope == "group":
        if database.COL_WAIFU_GROUP_USERS is None:
            return [], 0
        base = [
            {"$match": {"group_id": chat_id}},
            {
                "$lookup": {
                    "from": database.COL_WAIFU_USERS.name,
                    "localField": "user_id",
                    "foreignField": "id",
                    "as": "user",
                }
            },
            {"$unwind": {"path": "$user", "preserveNullAndEmptyArrays": True}},
            {"$addFields": {"score": {"$ifNull": ["$user.bounty", {"$ifNull": ["$user.coins", 0]}]}}},
            {"$match": {"score": {"$gt": 0}}},
        ]
        docs = list(database.COL_WAIFU_GROUP_USERS.aggregate(base + [
            {"$sort": {"score": -1, "count": -1}},
            {"$skip": start},
            {"$limit": _LB_PAGE_SIZE},
        ]))
        total_doc = list(database.COL_WAIFU_GROUP_USERS.aggregate(base + [{"$count": "total"}]))
        total = total_doc[0]["total"] if total_doc else 0
        return [
            {
                "id": d.get("user_id"),
                "name": (d.get("user") or {}).get("first_name") or d.get("first_name") or d.get("username") or d.get("user_id"),
                "score": d.get("score", 0),
            }
            for d in docs
        ], total

    base = [
        {"$addFields": {"score": score_expr}},
        {"$match": {"score": {"$gt": 0}}},
    ]
    docs = list(database.COL_WAIFU_USERS.aggregate(base + [
        {"$sort": {"score": -1, "total_guesses": -1}},
        {"$skip": start},
        {"$limit": _LB_PAGE_SIZE},
    ]))
    total_doc = list(database.COL_WAIFU_USERS.aggregate(base + [{"$count": "total"}]))
    total = total_doc[0]["total"] if total_doc else 0
    return [
        {
            "id": d.get("id"),
            "name": d.get("first_name") or d.get("username") or d.get("id"),
            "score": d.get("score", 0),
        }
        for d in docs
    ], total

def _lb_entries(board, scope, chat_id, metric, page):
    if board == "draft":
        return _lb_draft_entries(scope, chat_id, metric, page)
    if board == "bounty":
        return _lb_bounty_entries(scope, chat_id, page)
    return [], 0

def _lb_markup(board, scope, metric, page, total_pages):
    rows = [[
        InlineKeyboardButton("Draft", callback_data=f"lb:draft:{scope}:points:1"),
        InlineKeyboardButton("Bounty", callback_data=f"lb:bounty:{scope}:value:1"),
    ]]
    rows.append([
        InlineKeyboardButton("Global", callback_data=f"lb:{board}:global:{metric}:1"),
        InlineKeyboardButton("Group", callback_data=f"lb:{board}:group:{metric}:1"),
    ])
    if board == "draft":
        rows.append([
            InlineKeyboardButton("Points", callback_data=f"lb:draft:{scope}:points:1"),
            InlineKeyboardButton("Wins", callback_data=f"lb:draft:{scope}:wins:1"),
        ])

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("Prev", callback_data=f"lb:{board}:{scope}:{metric}:{page - 1}"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("Next", callback_data=f"lb:{board}:{scope}:{metric}:{page + 1}"))
    if nav:
        rows.append(nav)
    return InlineKeyboardMarkup(rows)

def _lb_text_and_markup(chat, board, scope, metric, page):
    if scope == "group" and not _lb_is_group_chat(chat):
        return "Group leaderboard only works inside a group chat.", _lb_selector_markup("global")

    metric = metric if board == "draft" and metric in ("points", "wins") else _lb_default_metric(board)
    page = max(1, page)
    entries, total = _lb_entries(board, scope, chat.id, metric, page)
    if entries is None:
        return "❌ Database not ready.", None

    total_pages = max(1, (total + _LB_PAGE_SIZE - 1) // _LB_PAGE_SIZE)
    if page > total_pages:
        page = total_pages
        entries, total = _lb_entries(board, scope, chat.id, metric, page)

    board_title = _LB_BOARDS.get(board, "Leaderboard")
    if board == "draft":
        board_title = "Draft Wins" if metric == "wins" else "Draft Points"
    scope_title = "Global" if scope == "global" else html.escape(getattr(chat, "title", None) or "This Group")
    lines = [f"<b>Top {scope_title} {board_title}</b> - Page {page}/{total_pages}\n"]

    if not entries:
        lines.append("No entries yet.")
    else:
        start_rank = (page - 1) * _LB_PAGE_SIZE + 1
        for offset, entry in enumerate(entries):
            rank = start_rank + offset
            name = _lb_user_link(entry.get("id"), entry.get("name"))
            if board == "draft":
                rating = int(entry.get("rating") or 0)
                wins = int(entry.get("wins") or 0)
                if metric == "wins":
                    lines.append(f"{rank}. {name} - <b>{wins:,}</b> wins ({rating:,} pts)")
                else:
                    lines.append(f"{rank}. {name} - <b>{rating:,}</b> pts ({wins:,} wins)")
            elif board == "bounty":
                score = int(entry.get("score") or 0)
                lines.append(f"{rank}. {name} - <b>{score:,}</b> bounty")
            else:
                score = int(entry.get("score") or 0)
                lines.append(f"{rank}. {name} - <b>{score:,}</b>")

    return "\n".join(lines), _lb_markup(board, scope, metric, page, total_pages)

async def _lb_send_selector(message, scope):
    await message.reply_text(
        _lb_selector_text(scope),
        parse_mode=ParseMode.HTML,
        reply_markup=_lb_selector_markup(scope),
    )

async def _lb_send(message, board, scope, metric, page):
    text, markup = _lb_text_and_markup(message.chat, board, scope, metric, page)
    await message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)

@Client.on_callback_query(filters.regex(r"^lbsel:(global|group)$"))
async def leaderboard_selector_callback(client, callback_query):
    scope = callback_query.matches[0].group(1)
    await callback_query.answer()
    await callback_query.edit_message_text(
        _lb_selector_text(scope),
        parse_mode=ParseMode.HTML,
        reply_markup=_lb_selector_markup(scope),
    )

@Client.on_callback_query(filters.regex(r"^lb:(draft|bounty):(global|group):(points|wins|value):(\d+)$"))
async def leaderboard_switch_callback(client, callback_query):
    board = callback_query.matches[0].group(1)
    scope = callback_query.matches[0].group(2)
    metric = callback_query.matches[0].group(3)
    page = int(callback_query.matches[0].group(4))
    text, markup = _lb_text_and_markup(callback_query.message.chat, board, scope, metric, page)
    await callback_query.answer()
    await callback_query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)

@Client.on_message(filters.command(["leaderboard", "top", "topglobal", "topgroup", "grouptop"]))
async def leaderboard_entry_cmd(c, m):
    database.set_current_client(c)
    command_parts = m.command or []
    scope = _lb_scope_from_command(command_parts)
    board = _lb_board_from_command(command_parts)
    if board is None:
        return await _lb_send_selector(m, scope)

    metric = _lb_metric_from_command(board, command_parts)
    page = _lb_page_from_command(command_parts)
    await _lb_send(m, board, scope, metric, page)


_TOP_PAGE_SIZE = 10

def _is_group_chat(chat) -> bool:
    chat_type = str(getattr(chat, "type", "")).lower()
    return "group" in chat_type or "supergroup" in chat_type

def _parse_top_mode(command_parts):
    command = (command_parts[0] if command_parts else "leaderboard").lower()
    args = [arg.lower() for arg in command_parts[1:]]
    if command in ("topgroup", "grouptop") or any(arg in ("group", "local", "chat") for arg in args):
        return "group"
    return "global"

def _parse_top_page(command_parts):
    for arg in command_parts[1:]:
        if str(arg).isdigit():
            return max(1, int(arg))
    return 1

    # page param (optional)
    args = m.text.split()
    page = 1
    if len(args) > 1:
        try:
            page = max(1, int(args[1]))
        except ValueError:
            page = 1
    logging.debug(f"[LEADERBOARD] Page {page} requested by user {user_id}")

    per_page = 16
    start = (page - 1) * per_page
    end = start + per_page

    # Fetch leaderboard entries
    entries = []
    try:
        clone_db = database.get_clone_db()
        if clone_db is not None:
            logging.debug(f"[LEADERBOARD] Using clone database for user {user_id}")
            col = clone_db["leaderboard"]
        else:
            logging.debug(f"[LEADERBOARD] Using main database for user {user_id}")
            col = database.COL_LEADERBOARD

        if col is not None:
            docs = list(col.find({}).sort("rating", -1).skip(start).limit(per_page))
            logging.debug(f"[LEADERBOARD] Retrieved {len(docs)} entries from database")
            for d in docs:
                uid = d.get("user_id")
                name = d.get("name") or str(uid)
                rating = d.get("rating", 0)
                wins = d.get("wins", 0)
                entries.append((name, rating, wins))
        else:
            logging.debug(f"[LEADERBOARD] No MongoDB collection, falling back to JSON file")
            if os.path.exists(database.LEADERBOARD_FILE):
                with open(database.LEADERBOARD_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                sorted_users = sorted(data.items(), key=lambda x: x[1].get("rating", 0), reverse=True)
                page_slice = sorted_users[start:end]
                for uid, stats in page_slice:
                    name = stats.get("name") or uid
                    rating = stats.get("rating", 0)
                    wins = stats.get("wins", 0)
                    entries.append((name, rating, wins))
                logging.debug(f"[LEADERBOARD] Retrieved {len(entries)} entries from JSON file")
    except Exception as e:
        logging.error(f"[LEADERBOARD] Failed to load leaderboard for user {user_id}: {e}", exc_info=True)
        return await m.reply_text("❌ Failed to load leaderboard.")

    if not entries:
        logging.info(f"[LEADERBOARD] Empty leaderboard for page {page} requested by {user_id}")
        return await m.reply_text("🏆 Leaderboard is empty or page out of range.")

    txt_lines = [f"🏆 <b>Leaderboard</b> — Page {page}\n"]
    rank = start + 1
    for name, rating, wins in entries:
        txt_lines.append(f"{rank}. {html.escape(str(name))} — {rating} pts")
        rank += 1

    # navigation
    # try to determine total pages if possible
    total_pages = None
    try:
        clone_db = database.get_clone_db()
        if clone_db is not None:
            col = clone_db["leaderboard"]
        else:
            col = database.COL_LEADERBOARD
            
        if col is not None:
            total = col.count_documents({})
            total_pages = (total + per_page - 1) // per_page
            logging.debug(f"[LEADERBOARD] Total pages calculated: {total_pages} from {total} total entries")
        else:
            if os.path.exists(database.LEADERBOARD_FILE):
                with open(database.LEADERBOARD_FILE, "r", encoding="utf-8") as f:
                    total = len(json.load(f))
                    total_pages = (total + per_page - 1) // per_page
                    logging.debug(f"[LEADERBOARD] Total pages from JSON: {total_pages}")
    except Exception as e:
        logging.debug(f"[LEADERBOARD] Could not determine total pages: {e}")
        total_pages = None

    kb = []
    row = []
    if page > 1:
        row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"leader_{page-1}"))
    if total_pages and page < total_pages:
        row.append(InlineKeyboardButton("Next ➡️", callback_data=f"leader_{page+1}"))
    if row:
        kb.append(row)
    kb.append([InlineKeyboardButton("🏆 Most Wins", callback_data=f"lb_toggle_wins_{page}")])
    
    logging.info(f"[LEADERBOARD] Sending leaderboard page {page} to user {user_id} with {len(entries)} entries")
    await m.reply_text("\n".join(txt_lines), reply_markup=InlineKeyboardMarkup(kb) if kb else None, parse_mode=ParseMode.HTML)
