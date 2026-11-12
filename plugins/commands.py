from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ParseMode
import html
import json
import database
import global_state
import ui
import rich_ui
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
    database.save_id_set(database.CHATS_FILE, {m.chat.id}, client=c)
    database.save_id_set(database.USERS_FILE, {user.id}, client=c)
    chat_type = getattr(getattr(m.chat, 'type', None), 'value', getattr(m.chat, 'type', None))
    if str(chat_type).lower() == 'private':
        database.mark_private_user(user.id)

    card = rich_ui.build_rich_card(
        title="👋 Welcome to Anime Draft Wars!",
        subtitle="Join Community :- @DRaft_wars",
        badges=["Anime", "Draft Wars",],
        description="Draft a team of 8 characters and battle friends!\nUse /draft to play.",
        expandable_quote="Quick Start:\n• Reply with /draft to challenge an opponent\n• Use /guide for a short guide\n• Use /commands for full command list"
    )
    await rich_ui.send_rich_message(
        c, m.chat.id, card,
        reply_markup=[[("🎮 Play Draft", "start_draft_hint"), ("📚 View Guide", "guide_0")]]
    )

def _guide_card(page):
    section, body = _COMMANDS_PAGES[page]
    _, _, description = body.partition("\n\n")
    return rich_ui.build_rich_card(
        title=f"📚 Bot Guide - {section}",
        badges=["Guide", f"Page {page + 1}/{len(_COMMANDS_PAGES)}"],
        description=description,
    )

@Client.on_message(filters.command("guide"))
async def guide_cmd(c, m):
    logging.info(f"[CMD] /guide invoked by {m.from_user.id}")

    kb = [[InlineKeyboardButton("Next ➡️", callback_data="guide_1")]]
    card = _guide_card(0)
    await rich_ui.send_rich_message(c, m.chat.id, card, reply_markup=kb)


@Client.on_message(filters.command("about"))
async def about_cmd(c, m):
    """One shareable overview of the bot's active systems."""
    overview = (
        '🤖 <b>ANIME DRAFT BOT — COMPLETE GUIDE</b>\n\n'
        '⚔️ <b>Draft Battles</b>\n'
        'Reply to a player with <code>/draft</code> to choose a head-to-head draft mode and start an 8-role anime draft. '
        'Use <code>/draft Naruto</code> to restrict the pool to one series, <code>/draft_v2</code> for V2, '
        '<code>/pdraft</code> for Pokémon, or <code>/cdraft</code> for comics. Players take turns choosing characters, then their teams battle.\n\n'
        '🔥 <b>$20 Blind Auction</b>\n'
        'Use <code>/auction</code> without replying to open a public lobby, or reply to a player to challenge them directly. Both players must first send <code>/start</code> in bot DM. '
        'Ten hidden characters are auctioned; each player has $20 and builds a five-character team. Bid with buttons or <code>/bid 7</code>. '
        'Use <code>/list</code> to browse the Draft and Auction rosters, and <code>/auction_help</code> for rules.\n\n'
        '🎮 <b>Minigames & Combat</b>\n'
        '<code>/shambles</code>, <code>/whoswho</code>, <code>/punk</code>, <code>/docq</code>, <code>/roulette</code>, and <code>/rps</code> provide quick games. '
        'Use <code>/duel</code>, <code>/fight</code>, <code>/plunder</code>, and <code>/bounty</code> for competitive modes.\n\n'
        '💰 <b>Profile, Economy & Rankings</b>\n'
        '<code>/profile</code> shows your progress. <code>/daily</code> claims 200 coins (plus 100 if your bio includes @anime_draft_bot), <code>/balance</code> shows your coins, and <code>/gift</code> lets you share eligible items. '
        'Use <code>/leaderboard</code>, <code>/topglobal</code>, and <code>/topgroup</code> for rankings.\n\n'
        '👥 <b>Groups</b>\n'
        '<code>/groups</code> shows active verified public groups. <code>/groups today</code> and <code>/groups all</code> change the ranking period. '
        'Only aggregate activity is stored—never message contents—and private groups are never listed.\n\n'
        '🛡️ <b>Admin Tools</b>\n'
        'Admins can manage characters, run <code>/acast</code>, inspect <code>/groups_stats</code>, and add auction-only characters with:\n'
        '<code>/aucadd Name\nImage URL\nSeries\nTotal Power</code>\n'
        'Catalog deletion requests via <code>/delchar</code> or <code>/delseries</code> require the configured owner’s private confirmation.\n\n'
        '📚 Use <code>/commands</code> for the complete command-by-command reference.'
    )
    await m.reply(overview, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

@Client.on_callback_query(filters.regex(r"^guide_(\d+)$"))
async def guide_cb(client, callback_query):
    page = int(callback_query.matches[0].group(1))

    if page < 0 or page >= len(_COMMANDS_PAGES):
        return await callback_query.answer("Invalid page.", show_alert=True)

    kb = []
    row = []
    if page > 0:
        row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"guide_{page-1}"))
    if page < len(_COMMANDS_PAGES) - 1:
        row.append(InlineKeyboardButton("Next ➡️", callback_data=f"guide_{page+1}"))
    if row:
        kb.append(row)

    card = _guide_card(page)
    await rich_ui.edit_rich_message(
        client, callback_query.message.chat.id, callback_query.message.id,
        card, reply_markup=InlineKeyboardMarkup(kb) if kb else None
    )

_COMMANDS_PAGES = [
    (
        "Start + Info",
        "📘 <b>Commands Guide - Start + Info</b>\n\n"
        "<b>/start</b>\n"
        "<code>/start</code> - Register this chat and show the bot intro.\n\n"
        "<b>/guide</b>\n"
        "<code>/guide</code> - Browse this full guide with next/prev buttons.\n\n"
        "<b>/commands</b>\n"
        "<code>/commands</code> or <code>/cmds</code> - Open the detailed command guide.\n"
        "<code>/commands 3</code> - Open a specific page (page numbers start at 1).\n\n"
        "<b>/about</b>\n"
        "<code>/about</code> - Read the bot's features and systems overview.\n\n"
        "<b>/profile</b>\n"
        "<code>/profile</code> - Show your draft points, matches, wins, rank, and achievements.\n\n"
        "<b>/groups</b>\n"
        "<code>/groups</code> - Top verified public groups this week.\n"
        "<code>/groups today</code> - Today’s ranking. <code>/groups all</code> - All-time ranking.\n\n"
        "<b>/balance</b>\n"
        "<code>/balance</code> - Show your coins.\n\n"
        "<b>/daily</b>\n"
        "<code>/daily</code> - Claim 200 coins every 24 hours; add @anime_draft_bot to your bio for 100 extra coins.\n\n"
        "<b>/gift</b>\n"
        "In a group, reply to a user's message with <code>/gift CHARACTER_ID</code> to offer them a character from your collection. Confirm with the button.\n\n"
        "<b>/list</b>\n"
        "<code>/list</code> - Choose Draft or Auction Draft rosters; use Back on a list to return here.\n"
        "<code>/list comics</code> - Open the comic series list directly.\n"
        "<code>/alist</code> - Open the auction series list directly.\n\n"
        "<b>/bounty</b>\n"
        "<code>/bounty</code> - Show your bounty profile with wanted poster when available.\n"
        "<code>/bounty</code> as a reply - Show that user's bounty profile/poster.\n\n"
        "<b>/status</b>\n"
        "<code>/status</code> - Admin-only: show active bot users and chats.\n\n"
        "<b>/clone BOT_TOKEN</b>\n"
        "Privately send your bot token to start your own bot instance."
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
        "<code>/draft</code> - Reply to a friend's message first, then choose a draft mode.\n"
        "<code>/draft Naruto</code> as a reply - Directly start an Anime Draft filtered by series.\n\n"
        "<code>/draft_v2</code> as a reply - Start an Anime Draft V2 challenge.\n"
        "<code>/pdraft</code> or <code>/pokemondraft</code> as a reply - Start a Pokémon stat draft; optional filters include region names, <code>6l</code> (legendary only), or <code>0l</code> (non-legendary).\n"
        "<code>/cdraft</code> as a reply - Start a comic character draft; add a series name to filter the pool.\n\n"
        "<b>$20 Blind Auction</b>\n"
        "<code>/auction</code> - Open an auction lobby in a group; reply to a user to challenge them directly.\n"
        "<code>/bid AMOUNT</code> - Place a custom bid during an active auction.\n"
        "<code>/auction_cancel</code> - Cancel your waiting auction lobby.\n"
        "<code>/auction_help</code> - Read auction rules. <code>/auction_stats</code> - View your auction record.\n\n"
        "<code>/list</code> - Choose the Draft or Auction series catalog.\n"
        "<code>/aucadd</code> - Admin-only: add an auction character using four lines: name, image URL, series, total power (1-100).\n\n"
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
        "<b>Who can use admin commands?</b>\n"
        "Admin-only commands require a configured bot admin. Owner-only commands are restricted to the bot owner. Some catalog changes are disabled on clones.\n\n"
        "<b>Character catalog</b>\n"
        "<code>/add Character Name</code> then put Anime, Image URL, and 8 numeric stats on separate lines. Admins add it directly; other users' requests go for approval.\n"
        "<code>/add_comic Character Name</code> then put Series, Image URL, and 8 stats on separate lines. Admins add it directly; other requests go for approval.\n"
        "<code>/comicimg \"URL\" \"Character Name\" \"Series\"</code> - Admin-only: change a comic character's image.\n"
        "<code>/characters</code> / <code>/characters comics</code> - Admin-only: browse anime/comic series and characters.\n"
        "<code>/delchar NAME_OR_ID</code> - Admin-only: request a character deletion; the owner must confirm privately.\n"
        "<code>/delseries NAME_OR_ID</code> - Admin-only: request deletion of a series and its records; owner confirmation is required.\n"
        "<code>/aucadd NAME</code> followed on new lines by Image URL, Series, and Total Power (1-100) - Admin-only: add an auction-only character.\n\n"
        "<b>Moderators + points</b>\n"
        "<code>/addpoints USER_ID POINTS</code> - Admin-only: add points; use a negative number to subtract (rating cannot go below zero).\n"
        "<code>/sudo promote</code> as a reply, or <code>/sudo promote @username</code> - Owner-only: promote a moderator.\n"
        "<code>/sudo demote</code> as a reply, or <code>/sudo demote @username</code> - Owner-only: remove a moderator.\n"
        "<code>/add_sudo</code> as a reply - Admin-only: add a clone admin/moderator.\n"
        "<code>/mods</code> - Admin-only: list moderators.\n\n"
        "<b>Bot operations</b>\n"
        "<code>/acast MESSAGE</code> - Broadcast to saved chats/users; main bot owner only, while clone admins/mods can broadcast on their clone.\n"
        "<code>/groups_stats</code> - Admin-only: see aggregate group tracking totals. <code>/groups_stats list</code> lists tracked groups; Refresh updates membership status.\n"
        "<code>/newyear on</code> / <code>/newyear off</code> - Admin-only: toggle the event mode.\n"
        "<code>/status</code> - Admin-only: show active users and chats.\n"
        "<code>/exportdb</code> or <code>/backupdb</code> - Owner-only: export a database backup; optionally give a destination chat.\n"
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
    title, body = _COMMANDS_PAGES[page]
    card = rich_ui.build_rich_card(
        title=f"📘 Commands Guide - {title}",
        badges=["Commands", f"Page {page + 1}/{len(_COMMANDS_PAGES)}"],
        description=body
    )
    await rich_ui.send_rich_message(
        c, m.chat.id, card, reply_markup=_commands_markup(page)
    )

@Client.on_callback_query(filters.regex(r"^cmds:(noop|\d+)$"))
async def commands_cb(client, callback_query):
    page_raw = callback_query.matches[0].group(1)
    if page_raw == "noop":
        return await callback_query.answer()

    page = max(0, min(len(_COMMANDS_PAGES) - 1, int(page_raw)))
    await callback_query.answer()
    title, body = _COMMANDS_PAGES[page]
    card = rich_ui.build_rich_card(
        title=f"📘 Commands Guide - {title}",
        badges=["Commands", f"Page {page + 1}/{len(_COMMANDS_PAGES)}"],
        description=body
    )
    await rich_ui.edit_rich_message(
        client, callback_query.message.chat.id, callback_query.message.id,
        card, reply_markup=_commands_markup(page)
    )

@Client.on_message(filters.command("list"))
async def list_cmd(c, m):
    logging.info(f"[CMD] /list invoked by {m.from_user.id} with text: {m.text}")
    # Keep the catalog choice out of the command list. Explicit arguments
    # (for example /list comics or /list 2) retain the old direct behaviour.
    if len(m.command or []) == 1:
        card = rich_ui.build_rich_card(
            title="📚 Available Series",
            badges=["Choose a catalog"],
            expandable_quote="Select the game mode whose series you want to browse.",
        )
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🎭 Draft", callback_data="list_anime_1"),
            InlineKeyboardButton("🔥 Auction Draft", callback_data="alist:1"),
        ]])
        return await rich_ui.send_rich_message(c, m.chat.id, card, reply_markup=keyboard)

    mode = "anime"
    if len(m.text.split()) > 1 and m.text.split()[1].lower() == "comics":
        mode = "comics"
    
    if mode == "anime":
        if not global_state.SERIES_DISPLAY:
            return await m.reply_text("📚 No anime series found.")
        series = sorted(set(global_state.SERIES_DISPLAY.values()))
    else:
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
    
    series_list_str = "\n".join(f"• {html.escape(s)}" for s in page_series)
    card = rich_ui.build_rich_card(
        title=f"📚 Available {mode.capitalize()} Series",
        badges=[mode.capitalize(), f"Page {page}/{total_pages}"],
        description=series_list_str
    )
    
    kb = []
    row = []
    if page > 1:
        row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"list_{mode}_{page-1}"))
    if page < total_pages:
        row.append(InlineKeyboardButton("Next ➡️", callback_data=f"list_{mode}_{page+1}"))
    if row:
        kb.append(row)
    
    toggle_btn = InlineKeyboardButton("🎬 Comics", callback_data="list_comics_1") if mode == "anime" else InlineKeyboardButton("🎭 Anime", callback_data="list_anime_1")
    kb.append([toggle_btn])
    kb.append([InlineKeyboardButton("⬅️ Back to catalog choice", callback_data="list_home")])
    
    await rich_ui.send_rich_message(c, m.chat.id, card, reply_markup=InlineKeyboardMarkup(kb) if kb else None)

@Client.on_message(filters.command("profile"))
async def profile_cmd(c, m):
    user = m.from_user
    logging.info(f"[CMD] /profile invoked by {user.id}")
    stats = database.get_user_stats(user.id, user.first_name)

    wins = stats.get("wins", 0)
    matches = stats.get("matches", 0)
    rating = stats.get("rating", 1200)
    wr = round((wins / matches * 100), 1) if matches > 0 else 0

    top_3 = get_top_3_users()
    is_top_3 = user.id in top_3

    if rating < 1000: rank = "🐣 Novice"
    elif rating < 1300: rank = "🥉 Bronze"
    elif rating < 1600: rank = "🥈 Silver"
    elif rating < 2000: rank = "🥇 Gold"
    elif rating < 2500: rank = "💎 Diamond"
    elif is_top_3: rank = "👑 Anime King"
    else: rank = "⭐ Legendary"

    user_achis = global_state.USER_ACHIEVEMENTS.get(str(user.id), [])
    achievements_txt = "\n".join([f"• {global_state.AVAILABLE_ACHIEVEMENTS.get(k, k)}" for k in user_achis]) if user_achis else "No achievements unlocked yet."

    card = rich_ui.build_rich_card(
        title=f"👤 Profile: {user.first_name}",
        badges=[f"Rank: {rank}", f"Rating: {rating}"],
        table_data=[
            ("Points", f"{rating:,}"),
            ("Matches", f"{matches:,}"),
            ("Wins", f"{wins:,}"),
            ("Win Rate", f"{wr}%")
        ],
        expandable_quote=f"🎖 Achievements:\n{achievements_txt}"
    )
    await rich_ui.send_rich_message(c, m.chat.id, card)


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

    card = rich_ui.build_rich_card(
        title="📊 Bot Runtime Statistics",
        badges=["Admin Only", "Status"],
        table_data=[
            ("Active Users", f"{u_count:,}"),
            ("Active Chats", f"{c_count:,}")
        ]
    )
    await rich_ui.send_rich_message(c, m.chat.id, card)


_LB_PAGE_SIZE = 20
_LB_BOARDS = {
    "draft": "Draft",
    "bounty": "Bounty",
}

def _lb_is_group_chat(chat) -> bool:
    chat_type = str(getattr(chat, "type", "")).lower()
    return "group" in chat_type or "supergroup" in chat_type

def _lb_default_metric(board):
    return "points" if board == "draft" else "value"

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

def _lb_col_draft(scope):
    main_db = database.IMAGE_MONGO_DB
    if main_db is not None:
        collection = "group_leaderboard" if scope == "group" else "leaderboard"
        return main_db[collection]
    if scope == "group":
        return database.COL_GROUP_LEADERBOARD if database.USE_MONGO else None
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
    main_db = database.IMAGE_MONGO_DB
    users = main_db["users"] if main_db is not None else database.COL_WAIFU_USERS
    if not database.USE_MONGO or users is None:
        return None, 0
    start = (page - 1) * _LB_PAGE_SIZE
    score_expr = {"$ifNull": ["$bounty", {"$ifNull": ["$coins", 0]}]}

    if scope == "group":
        group_users = main_db["group_user_totals"] if main_db is not None else database.COL_WAIFU_GROUP_USERS
        if group_users is None:
            return [], 0
        base = [
            {"$match": {"group_id": chat_id}},
            {
                "$lookup": {
                    "from": users.name,
                    "localField": "user_id",
                    "foreignField": "id",
                    "as": "user",
                }
            },
            {"$unwind": {"path": "$user", "preserveNullAndEmptyArrays": True}},
            {"$addFields": {"score": {"$ifNull": ["$user.bounty", {"$ifNull": ["$user.coins", 0]}]}}},
            {"$match": {"score": {"$gt": 0}}},
        ]
        docs = list(group_users.aggregate(base + [
            {"$sort": {"score": -1, "count": -1}},
            {"$skip": start},
            {"$limit": _LB_PAGE_SIZE},
        ]))
        total_doc = list(group_users.aggregate(base + [{"$count": "total"}]))
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
    docs = list(users.aggregate(base + [
        {"$sort": {"score": -1, "total_guesses": -1}},
        {"$skip": start},
        {"$limit": _LB_PAGE_SIZE},
    ]))
    total_doc = list(users.aggregate(base + [{"$count": "total"}]))
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
    options = [
        ("draft", "points", "Points"),
        ("draft", "wins", "Wins"),
        ("bounty", "value", "Bounty"),
    ]
    current = ("draft", metric) if board == "draft" else ("bounty", "value")
    switches = [
        InlineKeyboardButton(label, callback_data=f"lb:{target_board}:{scope}:{target_metric}:1")
        for target_board, target_metric, label in options
        if (target_board, target_metric) != current
    ]
    rows = [switches]
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
        return "Group leaderboard only works inside a group chat.", None

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
    lines = [f"<b>Top {scope_title} {board_title} - Page {page}/{total_pages}</b>", ""]

    if not entries:
        lines.append("No entries yet.")
    else:
        start_rank = (page - 1) * _LB_PAGE_SIZE + 1
        for offset, entry in enumerate(entries):
            rank = start_rank + offset
            name = str(entry.get("name") or entry.get("id") or "Unknown")
            prefix = f"{rank}. "
            safe_name = html.escape(name)
            if board == "draft":
                if metric == "wins":
                    wins = int(entry.get("wins") or 0)
                    score = f"{wins:,}"
                    lines.append(f"{prefix}{safe_name} - <b>{score}</b> wins")
                else:
                    rating = int(entry.get("rating") or 0)
                    score = f"{rating:,}"
                    lines.append(f"{prefix}{safe_name} - <b>{score}</b> pts")
            elif board == "bounty":
                score = int(entry.get("score") or 0)
                score = f"{score:,}"
                lines.append(f"{prefix}{safe_name} - <b>{score}</b> bounty")
            else:
                score = int(entry.get("score") or 0)
                score = f"{score:,}"
                lines.append(f"{prefix}{safe_name} - <b>{score}</b>")

    return "\n".join(lines), _lb_markup(board, scope, metric, page, total_pages)

async def _lb_send(message, board, scope, metric, page):
    text, markup = _lb_text_and_markup(message.chat, board, scope, metric, page)
    await message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)

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
    board = _lb_board_from_command(command_parts) or "draft"
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
