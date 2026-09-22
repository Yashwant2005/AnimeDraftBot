from pyrogram import Client, filters
from utils import log_exceptions
from pyrogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ParseMode
import asyncio
import time
import random
import html
import global_state
import game_engine
import ui
import database
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

@Client.on_callback_query(filters.regex(r"^(list_|leader_|lb_toggle_wins|char_(?!approve_|reject_)|accept_|draw_|set_|startrpg_|skip_|paccept_|pdraw_|pset_|pskip_|pbattle_)"))
@log_exceptions
async def callbacks(c, q: CallbackQuery):
    async with global_state.CALLBACK_SEMAPHORE:
        data = q.data
        if '_' not in data: return

        # --- HANDLE LIST PAGINATION FIRST ---
        if data.startswith("list_"):
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
            
            await q.edit_message_text(
                text=txt,
                reply_markup=InlineKeyboardMarkup(kb) if kb else None,
                parse_mode=ParseMode.HTML
            )
            return

        # --- LEADERBOARD INLINE NAVIGATION / TOGGLE ---
        if data.startswith("leader_"):
            try:
                parts = data.split("_")
                if len(parts) < 2:
                    return await q.answer("Invalid page", show_alert=True)
                page = int(parts[1])
            except Exception:
                return await q.answer("Invalid Data", show_alert=True)

            per_page = 16
            start = (page - 1) * per_page
            end = start + per_page

            # fetch entries by rating
            entries = []
            try:
                if database.USE_MONGO is not None and database.COL_LEADERBOARD is not None:
                    docs = list(database.COL_LEADERBOARD.find({}).sort("rating", -1).skip(start).limit(per_page))
                    for d in docs:
                        name = d.get("name") or str(d.get("user_id"))
                        entries.append((name, d.get("rating", 0), d.get("wins", 0)))
                else:
                    if os.path.exists(database.LEADERBOARD_FILE):
                        with open(database.LEADERBOARD_FILE, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        sorted_users = sorted(data.items(), key=lambda x: x[1].get("rating", 0), reverse=True)
                        for uid, stats in sorted_users[start:end]:
                            entries.append((stats.get("name") or uid, stats.get("rating", 0), stats.get("wins", 0)))
            except Exception:
                return await q.answer("Failed to load leaderboard.")

            if not entries:
                return await q.answer("No entries.")

            txt_lines = [f"🏆 <b>Leaderboard — Points</b> — Page {page}\n"]
            rank = start + 1
            for name, rating, wins in entries:
                txt_lines.append(f"{rank}. {html.escape(str(name))} — {rating} pts ")
                rank += 1

            # determine total pages if possible
            total_pages = None
            try:
                if database.USE_MONGO is not None and database.COL_LEADERBOARD is not None:
                    total = database.COL_LEADERBOARD.count_documents({})
                    total_pages = (total + per_page - 1) // per_page
                else:
                    if os.path.exists(database.LEADERBOARD_FILE):
                        with open(database.LEADERBOARD_FILE, "r", encoding="utf-8") as f:
                            total = len(json.load(f))
                            total_pages = (total + per_page - 1) // per_page
            except Exception:
                total_pages = None

            kb = []
            row = []
            if row:
                kb.append(row)

            # toggle to wins view (keep same page)
            kb.append([InlineKeyboardButton("Most Wins", callback_data=f"lb_toggle_wins_{page}")])

            await q.edit_message_text("\n".join(txt_lines), reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
            return

        if data.startswith("lb_toggle_wins"):
            # support lb_toggle_wins or lb_toggle_wins_{page}
            try:
                parts = data.split("_")
                page = 1
                if len(parts) > 2 and parts[-1].isdigit():
                    page = int(parts[-1])
            except Exception:
                page = 1

            per_page = 16
            start = (page - 1) * per_page
            end = start + per_page

            entries = []
            try:
                if database.USE_MONGO is not None and database.COL_LEADERBOARD is not None:
                    docs = list(database.COL_LEADERBOARD.find({}).sort("wins", -1).skip(start).limit(per_page))
                    for d in docs:
                        name = d.get("name") or str(d.get("user_id"))
                        entries.append((name, d.get("rating", 0), d.get("wins", 0)))
                else:
                    if os.path.exists(database.LEADERBOARD_FILE):
                        with open(database.LEADERBOARD_FILE, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        sorted_users = sorted(data.items(), key=lambda x: x[1].get("wins", 0), reverse=True)
                        for uid, stats in sorted_users[start:end]:
                            entries.append((stats.get("name") or uid, stats.get("rating", 0), stats.get("wins", 0)))
            except Exception as e:
                print(e)
                return await q.answer("Failed to load leaderboard.")

            if not entries:
                return await q.answer("No entries.")

            txt_lines = [f"🏆 <b>Leaderboard — Most Wins</b> — Page {page}\n"]
            rank = start + 1
            for name, rating, wins in entries:
                txt_lines.append(f"{rank}. {html.escape(str(name))} — {wins} wins ")
                rank += 1

            # nav
            total_pages = None
            try:
                if database.USE_MONGO is not None and database.COL_LEADERBOARD is not None:
                    total = database.COL_LEADERBOARD.count_documents({})
                    total_pages = (total + per_page - 1) // per_page
                else:
                    if os.path.exists(database.LEADERBOARD_FILE):
                        with open(database.LEADERBOARD_FILE, "r", encoding="utf-8") as f:
                            total = len(json.load(f))
                            total_pages = (total + per_page - 1) // per_page
            except Exception:
                total_pages = None

            kb = []
            row = []
            if row:
                kb.append(row)

            # button to go back to points view
            kb.append([InlineKeyboardButton("Points", callback_data=f"leader_{page}")])

            await q.edit_message_text("\n".join(txt_lines), reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
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
        
        # Find game
        game = global_state.GAMES.get(q.message.chat.id, {}).get(gid)
        if not game: return await q.answer("❌ Game expired.", show_alert=True)

        game['last_activity'] = time.time()
        uid = q.from_user.id

        # --- ANIME DRAFT ---
        if action == "accept":
            if uid != game["p2"]["id"]: return await q.answer("Not for you!", show_alert=True)
            game['status'] = 'active'
            await show_anime_draw(c, q.message, game, gid)

        elif action == "draw":
            if uid != game["turn"]: return await q.answer("Not your turn!", show_alert=True)
            # Use Marvel pool if available (cdraft), otherwise use anime SERIES_MAP
            pool = game.get("pool") or SERIES_MAP.get(game.get("filter"), ANIME_CHARACTERS)
            available = [x for x in pool if x not in game["used_chars"]]
            if not available: return await q.answer("Pool empty!", show_alert=True)
            
            char = random.choice(available)
            game["current_draw"] = char
            
            # Show Assign Menu
            kb = []
            row = []
            pkey = "p1" if uid == game["p1"]["id"] else "p2"
            roles_iter = game.get('roles') or []
            for role in roles_iter:
                if role not in game[pkey]["team"]:
                    safe_role = role.replace(' ', '-')
                    row.append(InlineKeyboardButton(f"🟢 {role}", callback_data=f"set_{gid}_{safe_role}"))
                    if len(row) == 2: kb.append(row); row = []
            if row: kb.append(row)
            if game[pkey]["skips"] > 0:
                kb.append([InlineKeyboardButton(f"🗑 Skip ({game[pkey]['skips']})", callback_data=f"skip_{gid}")])

            img = global_state.CHAR_IMAGES.get(char, "https://files.catbox.moe/knsy3i.jpg")
            p_name = game[pkey]["name"]
            char_name = char.split('|')[0].strip()
            txt = f"<a href='{img}'>&#160;</a>{ui.get_team_display(game)}\n✨ <b>{p_name}</b> Pulled: <b>{char_name}</b>"
            await ui.ensure_display_message(c, q.message.chat.id, game, txt, kb, preview=True)

        elif action == "set":
            if uid != game["turn"]: return await q.answer("Not your turn!", show_alert=True)
            if not game.get("current_draw"): return await q.answer("Draw first!", show_alert=True)
            
            role = parts[3].replace('-', ' ') # Recover role from safe string
            pkey = "p1" if uid == game["p1"]["id"] else "p2"
            
            game[pkey]["team"][role] = game["current_draw"]
            game["used_chars"].append(game["current_draw"])
            game["current_draw"] = None

            if len(game["p1"]["team"]) == 8 and len(game["p2"]["team"]) == 8:
                # Ready Phase
                p1_status = "✅ READY" if game.get('ready', {}).get('p1') else "⏳ WAITING"
                p2_status = "✅ READY" if game.get('ready', {}).get('p2') else "⏳ WAITING"
                p1n, p2n = game['p1']['name'], game['p2']['name']
                kb = [
                    [InlineKeyboardButton(f"🔵 {p1n} {p1_status}", callback_data=f"startrpg_{gid}_p1")],
                    [InlineKeyboardButton(f"🔴 {p2n} {p2_status}", callback_data=f"startrpg_{gid}_p2")]
                ]
                await ui.ensure_display_message(c, q.message.chat.id, game, f"🏁 <b>Ready?</b>\n{ui.get_team_display(game)}", kb)
            else:
                game_engine.switch_turn(game)
                await show_anime_draw(c, q.message, game, gid)
                
        elif action == "startrpg":
            pkey = parts[3]
            is_p1 = (pkey == "p1")
            with global_state.GAMES_LOCK:
                if (is_p1 and uid == game["p1"]["id"]): game["ready"]["p1"] = True
                elif (not is_p1 and uid == game["p2"]["id"]): game["ready"]["p2"] = True
                else: return await q.answer("Wrong button!", show_alert=True)

                if game["ready"]["p1"] and game["ready"]["p2"]:
                    await game_engine.simulate_anime_battle(c, q.message, game)
                else:
                    await q.answer("✅ Checked in!")

        elif action == "skip":
             if uid != game["turn"]: return await q.answer("Not your turn!", show_alert=True)
             pkey = "p1" if uid == game["p1"]["id"] else "p2"
             if game[pkey]["skips"] > 0:
                 game[pkey]["skips"] -= 1
                 if game.get("current_draw"): game["used_chars"].append(game["current_draw"])
                 game["current_draw"] = None
                 game_engine.switch_turn(game)
                 await show_anime_draw(c, q.message, game, gid)
             else:
                 await q.answer("No skips left!", show_alert=True)

        # --- POKEMON CALLBACKS ---
        elif action == "paccept":
            if uid != game["p2"]["id"]: return await q.answer("Not for you!", show_alert=True)
            game['status'] = 'active'
            await show_pokemon_draw(c, q.message, game, gid)

        elif action == "pdraw":
            if uid != game["turn"]: return await q.answer("Not your turn!", show_alert=True)
            if game.get("current_draw"): return await q.answer("Already drawn!", show_alert=True)

            # Filter logic - apply region and legendary status filters
            available = [p for p in POKEMON_LIST if p not in game["used_players"]]
            
            # Apply region filter
            if game.get("filters", {}).get("regions"):
                available = [p for p in available if POKEMON_DATA[p]["region"] in game["filters"]["regions"]]
            
            # Apply legendary status filter
            if game.get("filters", {}).get("legendary_status") is not None:
                legendary_filter = game["filters"]["legendary_status"]
                available = [p for p in available if POKEMON_DATA[p]["is_legendary"] == legendary_filter]
            
            if not available: return await q.answer("None left!", show_alert=True)
            pokemon = random.choice(available)
            game["current_draw"] = pokemon

            kb = []
            row = []
            pkey = "p1" if uid == game["p1"]["id"] else "p2"
            for role in POKEMON_ROLES:
                if role not in game[pkey]["team"]:
                    row.append(InlineKeyboardButton(f"🟢 {role}", callback_data=f"pset_{gid}_{role}"))
                    if len(row) == 2: kb.append(row); row = []
            if row: kb.append(row)
            if game[pkey]["skips"] > 0:
                kb.append([InlineKeyboardButton(f"🗑 Skip ({game[pkey]['skips']})", callback_data=f"pskip_{gid}")])
            
            p_name = game[pkey]["name"]
            img_url = CHAR_IMAGES.get(f"{pokemon} | Pokemon")
            txt = f"{ui.pokemon_get_team_display(game)}\n✨ <b>{p_name}</b> Pulled: <b>{pokemon}</b>"
            if img_url:
                try:
                    await c.send_photo(
                        q.message.chat.id,
                        photo=img_url,
                        caption=f"✨ <b>{p_name}</b> pulled <b>{pokemon}</b>",
                        parse_mode=ParseMode.HTML,
                    )
                except Exception as exc:
                    logging.warning("[PDRAFT] Could not send Pokemon image for %s: %s", pokemon, exc)
            await ui.ensure_display_message(c, q.message.chat.id, game, txt, kb, preview=True)

        elif action == "pset":
            if uid != game["turn"]: return await q.answer("Not your turn!", show_alert=True)
            role = parts[3]
            pokemon = game["current_draw"]
            p_data = POKEMON_DATA[pokemon]
            pkey = "p1" if uid == game["p1"]["id"] else "p2"

            if role == "Type":
                game[pkey]["team"][role] = {"pokemon": pokemon, "types": p_data["types"]}
            else:
                val = p_data["stats"][STAT_MAP[role]]
                game[pkey]["team"][role] = {"pokemon": pokemon, "value": val}

            game["used_players"].append(pokemon)
            game["current_draw"] = None

            if len(game["p1"]["team"]) == 7 and len(game["p2"]["team"]) == 7:
                 kb = [[InlineKeyboardButton("💥 Start Battle", callback_data=f"pbattle_{gid}")]]
                 await ui.ensure_display_message(c, q.message.chat.id, game, f"🏁 <b>Ready!</b>\n{ui.pokemon_get_team_display(game)}", kb)
            else:
                game_engine.pokemon_switch_turn(game)
                await show_pokemon_draw(c, q.message, game, gid)

        elif action == "pskip":
             if uid != game["turn"]: return await q.answer("Not your turn!", show_alert=True)
             pkey = "p1" if uid == game["p1"]["id"] else "p2"
             if game[pkey]["skips"] > 0:
                 game[pkey]["skips"] -= 1
                 if game.get("current_draw"): game["used_players"].append(game["current_draw"])
                 game["current_draw"] = None
                 game_engine.pokemon_switch_turn(game)
                 await show_pokemon_draw(c, q.message, game, gid)
             else:
                 await q.answer("No skips left!", show_alert=True)

        elif action == "pbattle":
             if uid not in [game['p1']['id'], game['p2']['id']]: return await q.answer("Spectator mode only.")
             await game_engine.simulate_pokemon_battle(c, q.message, game)
