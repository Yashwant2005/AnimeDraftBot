from pyrogram import Client, filters
from utils import log_exceptions
from pyrogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ParseMode
import asyncio
import os
import json
import time
import random
import html
import global_state
import game_engine
import ui
import database
import rich_ui
# --- FIX: Split imports correctly ---
from config import STAT_MAP, POKEMON_ROLES
from global_state import SERIES_MAP, ANIME_CHARACTERS, POKEMON_LIST, POKEMON_DATA, CHAR_IMAGES


async def show_anime_draw(c, m, game, gid):
    turn_id = game["turn"]
    p_name = game["p1"]["name"] if turn_id == game["p1"]["id"] else game["p2"]["name"]
    text = f"🏁 <b>Drafting</b>\n{ui.get_team_display(game)}\n🎮 Turn: {p_name}"
    kb = [[InlineKeyboardButton("🎲 Draw", callback_data=f"draw_{gid}")]]
    await ui.ensure_display_message(c, m.chat.id, game, text, kb)

async def show_pokemon_draw(c, m, game, gid):
    turn_id = game["turn"]
    p_name = game["p1"]["name"] if turn_id == game["p1"]["id"] else game["p2"]["name"]
    text = f"🏁 <b>Drafting</b>\n{ui.pokemon_get_team_display(game)}\n🎮 Turn: {p_name}"
    kb = [[InlineKeyboardButton("🎲 Draw Pokémon", callback_data=f"pdraw_{gid}")]]
    await ui.ensure_display_message(c, m.chat.id, game, text, kb)

def _turn_key(game):
    return "p1" if game["turn"] == game["p1"]["id"] else "p2"


def _anime_pulled_view(game, gid):
    """Screen shown after a draw: pulled character + free role buttons (+ skip)."""
    char = game["current_draw"]
    pkey = _turn_key(game)
    kb, row = [], []
    for role in (game.get("roles") or []):
        if role not in game[pkey]["team"]:
            safe_role = role.replace(" ", "-")
            row.append(InlineKeyboardButton(f"🟢 {role}", callback_data=f"set_{gid}_{safe_role}"))
            if len(row) == 2:
                kb.append(row); row = []
    if row:
        kb.append(row)
    if game[pkey]["skips"] > 0:
        kb.append([InlineKeyboardButton(f"🗑 Skip ({game[pkey]['skips']})", callback_data=f"skip_{gid}")])
    img = global_state.CHAR_IMAGES.get(char, "https://files.catbox.moe/knsy3i.jpg")
    char_name = char.split("|")[0].strip()
    txt = (f"<a href='{img}'>&#160;</a>{ui.get_team_display(game)}\n"
           f"✨ <b>{html.escape(game[pkey]['name'])}</b> Pulled: <b>{html.escape(char_name)}</b>")
    return txt, kb


def _anime_ready_view(game, gid):
    p1_status = "✅ READY" if game.get("ready", {}).get("p1") else "⏳ WAITING"
    p2_status = "✅ READY" if game.get("ready", {}).get("p2") else "⏳ WAITING"
    p1n, p2n = game["p1"]["name"], game["p2"]["name"]
    kb = [
        [InlineKeyboardButton(f"🔵 {p1n} {p1_status}", callback_data=f"startrpg_{gid}_p1")],
        [InlineKeyboardButton(f"🔴 {p2n} {p2_status}", callback_data=f"startrpg_{gid}_p2")],
    ]
    return f"🏁 <b>Ready?</b>\n{ui.get_team_display(game)}", kb


async def render_anime_state(c, chat_id, game, gid):
    """Redraw the correct screen from the game's *current state*.

    Used after every action and to heal stale screens (double taps, old
    buttons), so the message on Telegram always matches the server state.
    """
    if len(game["p1"]["team"]) >= 8 and len(game["p2"]["team"]) >= 8:
        txt, kb = _anime_ready_view(game, gid)
        return await ui.ensure_display_message(c, chat_id, game, txt, kb)
    if game.get("current_draw"):
        txt, kb = _anime_pulled_view(game, gid)
        return await ui.ensure_display_message(c, chat_id, game, txt, kb, preview=True)
    p_name = game["p1"]["name"] if game["turn"] == game["p1"]["id"] else game["p2"]["name"]
    text = f"🏁 <b>Drafting</b>\n{ui.get_team_display(game)}\n🎮 Turn: {p_name}"
    kb = [[InlineKeyboardButton("🎲 Draw", callback_data=f"draw_{gid}")]]
    await ui.ensure_display_message(c, chat_id, game, text, kb)


def _pokemon_pulled_view(game, gid):
    pokemon = game["current_draw"]
    pkey = _turn_key(game)
    kb, row = [], []
    for role in POKEMON_ROLES:
        if role not in game[pkey]["team"]:
            row.append(InlineKeyboardButton(f"🟢 {role}", callback_data=f"pset_{gid}_{role}"))
            if len(row) == 2:
                kb.append(row); row = []
    if row:
        kb.append(row)
    if game[pkey]["skips"] > 0:
        kb.append([InlineKeyboardButton(f"🗑 Skip ({game[pkey]['skips']})", callback_data=f"pskip_{gid}")])
    img_url = CHAR_IMAGES.get(f"{pokemon} | Pokemon")
    image_preview = f"<a href='{html.escape(img_url, quote=True)}'>&#160;</a>" if img_url else ""
    txt = (f"{image_preview}{ui.pokemon_get_team_display(game)}\n"
           f"✨ <b>{html.escape(game[pkey]['name'])}</b> Pulled: <b>{html.escape(pokemon)}</b>")
    return txt, kb


async def render_pokemon_state(c, chat_id, game, gid):
    if len(game["p1"]["team"]) >= 7 and len(game["p2"]["team"]) >= 7:
        kb = [[InlineKeyboardButton("💥 Start Battle", callback_data=f"pbattle_{gid}")]]
        return await ui.ensure_display_message(
            c, chat_id, game, f"🏁 <b>Ready!</b>\n{ui.pokemon_get_team_display(game)}", kb)
    if game.get("current_draw"):
        txt, kb = _pokemon_pulled_view(game, gid)
        return await ui.ensure_display_message(c, chat_id, game, txt, kb, preview=True)
    p_name = game["p1"]["name"] if game["turn"] == game["p1"]["id"] else game["p2"]["name"]
    text = f"🏁 <b>Drafting</b>\n{ui.pokemon_get_team_display(game)}\n🎮 Turn: {p_name}"
    kb = [[InlineKeyboardButton("🎲 Draw Pokémon", callback_data=f"pdraw_{gid}")]]
    await ui.ensure_display_message(c, chat_id, game, text, kb)


@Client.on_callback_query(filters.regex(r"^(list_|leader_|lb_toggle_wins|char_(?!approve_|reject_)|accept_|draw_|set_|startrpg_|skip_|paccept_|pdraw_|pset_|pskip_|pbattle_)"))
@log_exceptions
async def callbacks(c, q: CallbackQuery):
    async with global_state.CALLBACK_SEMAPHORE:
        data = q.data
        if '_' not in data: return

        # --- HANDLE LIST PAGINATION FIRST ---
        if data.startswith("list_"):
            if data == "list_home":
                await q.answer()
                await rich_ui.rich_edit(
                    c, q.message.chat.id, q.message.id,
                    rich_ui.list_home_html(), rich_ui.list_home_rows()
                )
                return
            try:
                parts = data.split("_")
                if len(parts) < 3:
                    return await q.answer("Invalid page", show_alert=True)
                
                mode = parts[1]  # "anime" or "comics"
                page = int(parts[2])
            except (IndexError, ValueError):
                return await q.answer("Invalid Data", show_alert=True)
            
            if mode == "anime":
                if not global_state.SERIES_DISPLAY:
                    return await q.answer("No series found.")
                series = sorted(set(global_state.SERIES_DISPLAY.values()))
            else:  # comics
                if not global_state.MARVEL_SERIES_DISPLAY:
                    return await q.answer("No comics found.")
                series = sorted(set(global_state.MARVEL_SERIES_DISPLAY.values()))
            
            per_page = 25
            total_pages = (len(series) + per_page - 1) // per_page
            page = min(max(1, page), total_pages)
            
            start_idx = (page - 1) * per_page
            end_idx = start_idx + per_page
            page_series = series[start_idx:end_idx]
            
            txt = rich_ui.series_html(f"Available {mode.capitalize()} Series", page_series, page, total_pages)
            await q.answer()
            await rich_ui.rich_edit(
                c, q.message.chat.id, q.message.id, txt,
                rich_ui.list_rows(mode, page, total_pages)
            )
            return

        # --- LEADERBOARD INLINE NAVIGATION ---
        if data.startswith("leader_") or data.startswith("lb_toggle_wins"):
            try:
                if data.startswith("lb_toggle_wins"):
                    page = int(data.rsplit("_", 1)[-1])
                else:
                    parts = data.split("_")
                    if len(parts) < 2:
                        return await q.answer("Invalid page", show_alert=True)
                    page = int(parts[1])
            except Exception:
                return await q.answer("Invalid Data", show_alert=True)

            per_page = 20
            start = (page - 1) * per_page
            end = start + per_page

            entries = []
            try:
                collection = (
                    database.IMAGE_MONGO_DB["leaderboard"]
                    if database.IMAGE_MONGO_DB is not None
                    else database.COL_LEADERBOARD
                )
                if database.USE_MONGO and collection is not None:
                    query = {"matches": {"$gt": 0}}
                    docs = list(collection.find(query).sort("rating", -1).skip(start).limit(per_page))
                    for d in docs:
                        entries.append((d.get("user_id"), d.get("name") or d.get("user_id"), d.get("rating", 0)))
                else:
                    if os.path.exists(database.LEADERBOARD_FILE):
                        with open(database.LEADERBOARD_FILE, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        sorted_users = sorted(
                            (
                                (uid, stats) for uid, stats in data.items()
                                if stats.get("matches", 0) > 0
                            ),
                            key=lambda x: x[1].get("rating", 0),
                            reverse=True,
                        )
                        for uid, stats in sorted_users[start:end]:
                            entries.append((uid, stats.get("name") or uid, stats.get("rating", 0)))
            except Exception:
                return await q.answer("Failed to load leaderboard.")

            if not entries:
                return await q.answer("No entries.")

            title = f"🏆 Leaderboard — Page {page}"
            txt_lines = [f"<b>{title}</b>", ""]
            rank = start + 1
            for user_id, name, rating in entries:
                display_name = str(name or user_id or "Unknown")
                safe_name = html.escape(display_name)
                score = f"{rating:,}"
                txt_lines.append(f"{rank}. {safe_name} — <b>{score}</b> pts")
                rank += 1

            try:
                if database.USE_MONGO and collection is not None:
                    total = collection.count_documents({"matches": {"$gt": 0}})
                else:
                    if os.path.exists(database.LEADERBOARD_FILE):
                        with open(database.LEADERBOARD_FILE, "r", encoding="utf-8") as f:
                            total = sum(stats.get("matches", 0) > 0 for stats in json.load(f).values())
                total_pages = max(1, (total + per_page - 1) // per_page)
            except Exception:
                total_pages = page

            kb = []
            nav = []
            if page > 1:
                nav.append(InlineKeyboardButton("Prev", callback_data=f"leader_{page - 1}"))
            if page < total_pages:
                nav.append(InlineKeyboardButton("Next", callback_data=f"leader_{page + 1}"))
            if nav:
                kb.append(nav)

            await q.edit_message_text(
                "\n".join(txt_lines),
                reply_markup=InlineKeyboardMarkup(kb) if kb else None,
                parse_mode=ParseMode.HTML,
            )
            return

        # --- HANDLE CHARACTER VIEWER (Admin) ---
        if data.startswith("char_"):
            try:
                parts = data.split("_", 2)
                mode = parts[1]  # "anime" or "comics"
                action_raw = parts[2] if len(parts) > 2 else None
            except (IndexError, ValueError):
                return await q.answer("Invalid Data", show_alert=True)

            # Extract optional page suffix from action (e.g. "onepiece_page_2")
            page = 1
            action = action_raw
            if action_raw and "_page_" in action_raw:
                try:
                    action, page_s = action_raw.rsplit("_page_", 1)
                    page = int(page_s)
                except Exception:
                    page = 1
            
            # Toggle between anime/comics list
            if action == "list":
                from plugins.admin import characters_cmd
                # Simulate the command with the correct mode
                class FakeMessage:
                    def __init__(self):
                        self.text = f"/characters {mode}"
                        self.reply_to_message = None
                    async def reply_text(self, *args, **kwargs):
                        await q.edit_message_text(*args, **kwargs)
                
                fake_msg = FakeMessage()
                fake_msg.reply_text = lambda txt, **kwargs: q.edit_message_text(txt, **kwargs)
                
                if mode == "anime":
                    if not global_state.SERIES_DISPLAY:
                        return await q.answer("No series found.")
                    all_series = sorted(set(global_state.SERIES_DISPLAY.items()))
                else:  # comics
                    if not global_state.MARVEL_SERIES_DISPLAY:
                        return await q.answer("No comics found.")
                    all_series = sorted(set(global_state.MARVEL_SERIES_DISPLAY.items()))
                
                txt = f"{'🎭 Anime Series' if mode == 'anime' else '🎬 Comics'}\n(Click to see characters)\n\n"
                
                kb = []
                row = []
                for norm_name, display_name in all_series:
                    row.append(InlineKeyboardButton(display_name, callback_data=f"char_{mode}_{norm_name}"))
                    if len(row) == 2:
                        kb.append(row)
                        row = []
                if row:
                    kb.append(row)
                
                toggle_btn = InlineKeyboardButton("🎬 Comics", callback_data="char_comics_list") if mode == "anime" else InlineKeyboardButton("🎭 Anime", callback_data="char_anime_list")
                kb.append([toggle_btn])
                
                await q.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb) if kb else None)
                return
            
            # Show characters from selected series
            if mode == "anime":
                if action not in global_state.SERIES_MAP:
                    return await q.answer("Series not found.")
                chars = global_state.SERIES_MAP[action]
            else:  # comics
                if action not in global_state.MARVEL_SERIES_MAP:
                    return await q.answer("Series not found.")
                chars = global_state.MARVEL_SERIES_MAP[action]
            
            if not chars:
                return await q.answer("No characters found.")
            
            # Show 8 rows x 2 cols = 16 characters per page
            per_page = 16
            total_pages = (len(chars) + per_page - 1) // per_page
            page = min(max(1, page), total_pages)
            start_idx = (page - 1) * per_page
            end_idx = start_idx + per_page
            page_chars = chars[start_idx:end_idx]
            
            series_display = action
            if mode == "anime":
                for norm, disp in global_state.SERIES_DISPLAY.items():
                    if norm == action:
                        series_display = disp
                        break
            else:
                for norm, disp in global_state.MARVEL_SERIES_DISPLAY.items():
                    if norm == action:
                        series_display = disp
                        break
            
            txt = f"{'🎭' if mode == 'anime' else '🎬'} <b>{series_display}</b> Characters (Page {page}/{total_pages})\n\n"
            
            kb = []
            row = []
            for char_id in page_chars:
                char_name = char_id.split('|')[0].strip()
                img_url = global_state.CHAR_IMAGES.get(char_id, "")
                if img_url:
                    row.append(InlineKeyboardButton(f"{char_name}", url=img_url))
                else:
                    row.append(InlineKeyboardButton(f"{char_name}", callback_data="noop"))
                if len(row) == 2:
                    kb.append(row)
                    row = []
            if row:
                kb.append(row)
            
            # Add navigation buttons
            nav_row = []
            if page > 1:
                nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"char_{mode}_{action}_page_{page-1}"))
            if page < total_pages:
                nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"char_{mode}_{action}_page_{page+1}"))
            if nav_row:
                kb.append(nav_row)
            
            # Back button
            kb.append([InlineKeyboardButton("⬅️ Back to Series", callback_data=f"char_{mode}_list")])
            
            await q.edit_message_text(
                text=txt,
                reply_markup=InlineKeyboardMarkup(kb) if kb else None,
                parse_mode=ParseMode.HTML
            )
            return

        # Robust ID Parsing for game callbacks
        action, payload = data.split('_', 1)
        # Payload usually contains gid which looks like chatid_timestamp_rand
        parts = payload.split('_')

        # We need at least 3 parts for a valid game ID (chatid, timestamp, rand)
        if len(parts) < 3: return await q.answer("Invalid Data")

        gid = f"{parts[0]}_{parts[1]}_{parts[2]}"
        chat_id = q.message.chat.id

        if not global_state.GAMES.get(chat_id, {}).get(gid):
            return await q.answer("❌ Game expired.", show_alert=True)

        # All presses for one game are handled strictly one after another.
        async with global_state.get_game_lock(gid):
            # Re-read after waiting: the game may have ended while we queued.
            game = global_state.GAMES.get(chat_id, {}).get(gid)
            if not game: return await q.answer("❌ Game expired.", show_alert=True)

            game['last_activity'] = time.time()
            # Always edit the message the player actually pressed a button on.
            # (Before, edits went to a remembered id; if that ever differed from
            # the visible message the player kept seeing the old buttons.)
            game['display_message'] = {'chat_id': chat_id, 'msg_id': q.message.id}
            await _handle_game_action(c, q, action, parts, gid, game)


async def _not_your_turn(q, game):
    """Players who tapped a now-stale button get a quiet toast; outsiders get the alert."""
    is_player = q.from_user.id in (game["p1"]["id"], game["p2"]["id"])
    await q.answer("⏳ Not your turn right now." if is_player else "Not your turn!", show_alert=not is_player)


async def _handle_game_action(c, q, action, parts, gid, game):
    uid = q.from_user.id
    chat_id = q.message.chat.id

    # --- ANIME / COMIC DRAFT ---
    if action == "accept":
        if uid != game["p2"]["id"]: return await q.answer("Not for you!", show_alert=True)
        if game.get('status') == 'active':          # double tap on Accept
            await q.answer("Already accepted.")
            return await render_anime_state(c, chat_id, game, gid)
        game['status'] = 'active'
        await q.answer("Draft accepted.")
        await render_anime_state(c, chat_id, game, gid)

    elif action == "draw":
        if uid != game["turn"]: return await _not_your_turn(q, game)
        if game.get("current_draw"):
            # Already drawn (double tap / stale "Draw" button). Do NOT re-roll:
            # just repaint the role-picking screen for the same character.
            await q.answer("You already drew — pick a role!")
            return await render_anime_state(c, chat_id, game, gid)

        # Marvel pool if available (cdraft), otherwise anime SERIES_MAP
        pool = game.get("pool") or SERIES_MAP.get(game.get("filter"), ANIME_CHARACTERS)
        used = set(game["used_chars"])
        available = [x for x in pool if x not in used]
        if not available: return await q.answer("Pool empty!", show_alert=True)

        game["current_draw"] = random.choice(available)
        await q.answer("🎲 Drawn!")
        await render_anime_state(c, chat_id, game, gid)

    elif action == "set":
        if uid != game["turn"]: return await _not_your_turn(q, game)
        if len(parts) < 4: return await q.answer("Invalid Data")

        role = parts[3].replace('-', ' ')  # Recover role from safe string
        pkey = "p1" if uid == game["p1"]["id"] else "p2"

        if not game.get("current_draw"):
            # Nothing to assign: the role was already taken by an earlier tap and
            # this is a stale button. Quietly resync instead of an error popup.
            await q.answer("✅ Already assigned.")
            return await render_anime_state(c, chat_id, game, gid)
        if role not in (game.get("roles") or []) or role in game[pkey]["team"]:
            await q.answer("That role is already filled — pick another.")
            return await render_anime_state(c, chat_id, game, gid)

        game[pkey]["team"][role] = game["current_draw"]
        game["used_chars"].append(game["current_draw"])
        game["current_draw"] = None
        await q.answer("Role assigned.")

        if not (len(game["p1"]["team"]) == 8 and len(game["p2"]["team"]) == 8):
            game_engine.switch_turn(game)
        await render_anime_state(c, chat_id, game, gid)

    elif action == "startrpg":
        if len(parts) < 4: return await q.answer("Invalid Data")
        pkey = parts[3]
        if game.get("battle_started"):
            return await q.answer("⚔️ Battle already started!")
        if pkey == "p1" and uid == game["p1"]["id"]: game["ready"]["p1"] = True
        elif pkey == "p2" and uid == game["p2"]["id"]: game["ready"]["p2"] = True
        else: return await q.answer("Wrong button!", show_alert=True)

        if game["ready"]["p1"] and game["ready"]["p2"]:
            game["battle_started"] = True
            await q.answer("Both players are ready. Starting the battle...")
            await game_engine.simulate_anime_battle(c, q.message, game)
        else:
            await q.answer("✅ Checked in!")
            await render_anime_state(c, chat_id, game, gid)   # shows the new READY status

    elif action == "skip":
        if uid != game["turn"]: return await _not_your_turn(q, game)
        pkey = "p1" if uid == game["p1"]["id"] else "p2"
        if not game.get("current_draw"):
            await q.answer("Draw first!")
            return await render_anime_state(c, chat_id, game, gid)
        if game[pkey]["skips"] > 0:
            game[pkey]["skips"] -= 1
            game["used_chars"].append(game["current_draw"])
            game["current_draw"] = None
            game_engine.switch_turn(game)
            await q.answer("Character skipped.")
            await render_anime_state(c, chat_id, game, gid)
        else:
            await q.answer("No skips left!", show_alert=True)

    # --- POKEMON ---
    elif action == "paccept":
        if uid != game["p2"]["id"]: return await q.answer("Not for you!", show_alert=True)
        if game.get('status') == 'active':
            await q.answer("Already accepted.")
            return await render_pokemon_state(c, chat_id, game, gid)
        game['status'] = 'active'
        await q.answer("Draft accepted.")
        await render_pokemon_state(c, chat_id, game, gid)

    elif action == "pdraw":
        if uid != game["turn"]: return await _not_your_turn(q, game)
        if game.get("current_draw"):
            await q.answer("You already drew — pick a stat!")
            return await render_pokemon_state(c, chat_id, game, gid)

        used = set(game["used_players"])
        available = [p for p in POKEMON_LIST if p not in used]
        flt = game.get("filters", {})
        if flt.get("regions"):
            available = [p for p in available if POKEMON_DATA[p]["region"] in flt["regions"]]
        if flt.get("legendary_status") is not None:
            available = [p for p in available if POKEMON_DATA[p]["is_legendary"] == flt["legendary_status"]]
        if not available: return await q.answer("None left!", show_alert=True)

        game["current_draw"] = random.choice(available)
        await q.answer("🎲 Drawn!")
        await render_pokemon_state(c, chat_id, game, gid)

    elif action == "pset":
        if uid != game["turn"]: return await _not_your_turn(q, game)
        if len(parts) < 4: return await q.answer("Invalid Data")
        role = parts[3]
        pkey = "p1" if uid == game["p1"]["id"] else "p2"
        pokemon = game.get("current_draw")
        if not pokemon:
            await q.answer("✅ Already assigned.")
            return await render_pokemon_state(c, chat_id, game, gid)
        if role not in POKEMON_ROLES or role in game[pkey]["team"]:
            await q.answer("That slot is already filled — pick another.")
            return await render_pokemon_state(c, chat_id, game, gid)

        p_data = POKEMON_DATA[pokemon]
        if role == "Type":
            game[pkey]["team"][role] = {"pokemon": pokemon, "types": p_data["types"]}
        else:
            game[pkey]["team"][role] = {"pokemon": pokemon, "value": p_data["stats"][STAT_MAP[role]]}

        game["used_players"].append(pokemon)
        game["current_draw"] = None
        await q.answer("Selection saved.")

        if not (len(game["p1"]["team"]) == 7 and len(game["p2"]["team"]) == 7):
            game_engine.pokemon_switch_turn(game)
        await render_pokemon_state(c, chat_id, game, gid)

    elif action == "pskip":
        if uid != game["turn"]: return await _not_your_turn(q, game)
        pkey = "p1" if uid == game["p1"]["id"] else "p2"
        if not game.get("current_draw"):
            await q.answer("Draw first!")
            return await render_pokemon_state(c, chat_id, game, gid)
        if game[pkey]["skips"] > 0:
            game[pkey]["skips"] -= 1
            game["used_players"].append(game["current_draw"])
            game["current_draw"] = None
            game_engine.pokemon_switch_turn(game)
            await q.answer("Pokémon skipped.")
            await render_pokemon_state(c, chat_id, game, gid)
        else:
            await q.answer("No skips left!", show_alert=True)

    elif action == "pbattle":
        if uid not in [game['p1']['id'], game['p2']['id']]: return await q.answer("Spectator mode only.")
        if game.get("battle_started"):
            return await q.answer("⚔️ Battle already started!")
        game["battle_started"] = True
        await q.answer("Starting the battle...")
        await game_engine.simulate_pokemon_battle(c, q.message, game)
