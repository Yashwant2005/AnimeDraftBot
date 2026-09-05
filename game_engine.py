import random
import time
import math
import asyncio
import threading
import logging
import html
from config import *
import global_state
from utils import update_leaderboard_elo
from ui import ensure_display_message, get_team_display, pokemon_get_team_display
from performance import manager  # Import the new manager
# --- ANIME GAME LOGIC ---
def switch_turn(game):
    p1_c = len(game["p1"]["team"])
    p2_c = len(game["p2"]["team"])
    curr = game["turn"]
    if curr == game["p1"]["id"]:
        if p2_c < 8: game["turn"] = game["p2"]["id"]
    else:
        if p1_c < 8: game["turn"] = game["p1"]["id"]

def generate_deck(pool, size=8):
    pool = [p for p in pool if p]
    if not pool: return []
    unique = list(dict.fromkeys(pool))
    deck = random.sample(unique, size) if len(unique) >= size else random.choices(unique, k=size)
    random.shuffle(deck)
    return deck[:size]

def _process_anime_battle_sync(game):
    """
    Synchronous helper containing all heavy Anime battle logic.
    Runs in a separate thread to avoid blocking the bot.
    """
    p1, p2 = game["p1"], game["p2"]
    score1, score2 = 0, 0
    log = "🏟 <b>BATTLE ARENA SIMULATION</b>\n\n"

    # Define key maps for both schemas
    key_map_anime = {
        "Captain": "captain", "Vice Captain": "vice_captain", "Tank": "tank",
        "Healer": "healer", "Assassin": "assassin", "Support 1": "support",
        "Support 2": "support", "Traitor": "traitor"
    }

    key_map_marvel = {
        "Paragon": "paragon", "Genius": "genius", "Powerhouse": "powerhouse",
        "Mystic": "mystic", "Street Level": "street_level", "Cosmic": "cosmic",
        "Trickster": "trickster", "Herald": "herald"
    }

    # Matchups
    matchups_anime = [
        ("Captain", "Captain", 30), ("Vice Captain", "Vice Captain", 25),
        ("Tank", "Tank", 15), ("Support 1", "Support 1", 10),
        ("Support 2", "Support 2", 10), ("Assassin", "Healer", 20),
        ("Healer", "Assassin", 20)
    ]

    matchups_marvel = [
        ("Paragon", "Paragon", 30), ("Genius", "Genius", 25),
        ("Powerhouse", "Powerhouse", 20), ("Cosmic", "Cosmic", 20),
        ("Mystic", "Mystic", 15), ("Street Level", "Street Level", 10),
        ("Herald", "Herald", 15), ("Trickster", "Trickster", 10)
    ]

    # Detect Schema
    def detect_marvel_schema():
        for team in (p1["team"].values(), p2["team"].values()):
            for char_id in team:
                if not char_id: continue
                stats = global_state.CHAR_STATS.get(char_id, {})
                if any(k in stats for k in ("paragon", "genius", "powerhouse")):
                    return True
        return False

    is_marvel = detect_marvel_schema()
    key_map = key_map_marvel if is_marvel else key_map_anime
    matchups = matchups_marvel if is_marvel else matchups_anime

    def get_stat(char_id, role_name):
        if not char_id: return 0
        s = global_state.CHAR_STATS.get(char_id, {})
        json_key = key_map.get(role_name, None)
        if json_key:
            return s.get(json_key, DEFAULT_POWER)
        return DEFAULT_POWER

    # Run matchups
    for i, (r1, r2, pts) in enumerate(matchups, 1):
        c1 = p1["team"].get(r1)
        c2 = p2["team"].get(r2)

        if c1 and c2:
            s1 = get_stat(c1, r1)
            s2 = get_stat(c2, r2)

            # Anime-specific healer bonus
            if not is_marvel:
                if r1 == "Assassin" and r2 == "Healer": s2 = int(s2 * HEALER_BONUS)
                if r1 == "Healer" and r2 == "Assassin": s1 = int(s1 * HEALER_BONUS)

            n1 = c1.split('|')[0].strip()
            n2 = c2.split('|')[0].strip()

            role_label = r1 if r1 == r2 else f"{r1} vs {r2}"
            log += f"{i}. <b>{role_label}</b>: "
            if s1 > s2:
                score1 += pts
                log += f"🔵 {html.escape(n1)} def. 🔴 {html.escape(n2)} (+{pts} Pts)\n\n"
            elif s2 > s1:
                score2 += pts
                log += f"🔴 {html.escape(n2)} def. 🔵 {html.escape(n1)} (+ {pts} Pts)\n\n"
            else:
                log += f"⚖️ Draw ({html.escape(n1)} vs {html.escape(n2)})\n\n"
        else:
            log += f"{i}. {r1}: Missing Character (Draw)\n"

    # Traitor Mechanics (Anime)
    if not is_marvel:
        for p_key, team_obj, label in [("p1", p1, "🔵"), ("p2", p2, "🔴")]:
            tid = team_obj["team"].get("Traitor")
            if tid:
                stat = get_stat(tid, "Traitor")
                if random.randint(1, 100) < stat:
                    log += f"🎭 {label} {tid.split('|')[0]} BETRAYED! (-30)\n"
                    if p_key == "p1": score1 -= 30
                    else: score2 -= 30
                else:
                    log += f"{label} Traitor stayed loyal.\n"

    # Trickster Mechanics (Marvel)
    t1_id = p1["team"].get("Trickster")
    t2_id = p2["team"].get("Trickster")
    if t1_id:
        val = get_stat(t1_id, "Trickster")
        if val >= 90:
            score1 += 10
            score2 = max(0, score2 - 10)
            log += f"\n🃏 <b>TRICK!</b> 🔵 {t1_id.split('|')[0]} stole 10 points!\n"

    if t2_id:
        val = get_stat(t2_id, "Trickster")
        if val >= 90:
            score2 += 10
            score1 = max(0, score1 - 10)
            log += f"\n🃏 <b>TRICK!</b> 🔴 {t2_id.split('|')[0]} stole 10 points!\n"

    # Finalize Scores and ELO
    result = 1 if score1 > score2 else 0 if score2 > score1 else 0.5
    winner = p1['name'] if result == 1 else p2['name'] if result == 0 else "Draw"
    
    # Update ELO (Database write happens here, but since we are in a thread, it won't block main loop)
    d1, d2 = update_leaderboard_elo(
        p1['id'],
        p1['name'],
        p2['id'],
        p2['name'],
        result,
        chat_id=game.get("chat_id"),
        chat_title=game.get("chat_title"),
    )

    final_text = (
        f"{log}\n"
        f"➖➖➖➖➖➖➖➖➖➖\n"
        f"🔵 <b>{p1['name']}</b>: {score1} ({d1:+})\n"
        f"🔴 <b>{p2['name']}</b>: {score2} ({d2:+})\n\n"
        f"🏆 <b>WINNER: {html.escape(winner)}</b>"
    )
    
    game['status'] = 'finished'
    return final_text

async def simulate_anime_battle(client, message, game):
    chat_id = message.chat.id
    # Identify priority: Use Player 1's ID to determine which semaphore to use
    priority_user_id = game.get("p1", {}).get("id", 0)

    # Temporary message so user knows something is happening
    temp_msg = await client.send_message(chat_id, "⚔️ **Simulating Battle...**")

    # Acquire Slot (Respects 30/70 split)
    async with manager.get_semaphore(priority_user_id):
        try:
            # Run heavy math in a separate thread
            final_text = await asyncio.to_thread(_process_anime_battle_sync, game)
            
            # Update UI
            await ensure_display_message(client, chat_id, game, final_text, preview=False)
            try:
                await temp_msg.delete()
            except: pass
            
        except Exception as e:
            logging.error(f"Anime Battle Error: {e}", exc_info=True)
            await temp_msg.edit_text("❌ An error occurred during battle simulation.")
# --- POKEMON LOGIC ---
def pokemon_switch_turn(game):
    p1, p2 = game['p1'], game['p2']
    if len(p1["team"]) == 7 and len(p2["team"]) == 7: return
    game["turn"] = p2['id'] if game["turn"] == p1['id'] else p1['id']

def get_best_move_info(attacker_types, defender_types):
    best_multiplier = -1
    best_type = attacker_types[0]
    for move_type in attacker_types:
        current_multiplier = 1.0
        for def_type in defender_types:
            current_multiplier *= TYPE_CHART.get(move_type, {}).get(def_type, 1.0)
        if current_multiplier > best_multiplier:
            best_multiplier = current_multiplier
            best_type = move_type
    return best_type, best_multiplier

def calculate_damage(attack_stat, defense_stat, move_type, attacker_types, type_multiplier):
    stab = 1.5 if move_type in attacker_types else 1.0
    damage = (((((2 * POKEMON_LEVEL / 5) + 2) * MOVE_POWER * (attack_stat / defense_stat)) / 50) + 2) * stab * type_multiplier
    return max(1, int(damage))

def simulate_matchup(attacker, defender):
    move_type_A, type_mult_A = get_best_move_info(attacker['types'], defender['types'])
    damage_A = calculate_damage(attacker['atk'], defender['def'], move_type_A, attacker['types'], type_mult_A)
    turns_to_ko_defender = math.ceil(defender['hp'] / damage_A)

    move_type_D, type_mult_D = get_best_move_info(defender['types'], attacker['types'])
    damage_D = calculate_damage(defender['atk'], attacker['def'], move_type_D, defender['types'], type_mult_D)
    turns_to_ko_attacker = math.ceil(attacker['hp'] / damage_D)

    if turns_to_ko_defender < turns_to_ko_attacker: winner = 'attacker'
    elif turns_to_ko_attacker < turns_to_ko_defender: winner = 'defender'
    else: winner = 'attacker' if attacker['spe'] > defender['spe'] else 'defender' if defender['spe'] > attacker['spe'] else 'draw'

    return {"winner": winner, "attacker_turns": turns_to_ko_defender, "defender_turns": turns_to_ko_attacker}

def _process_pokemon_battle_sync(game):
    """
    Synchronous helper containing all heavy Pokemon battle logic.
    """
    p1, p2 = game["p1"], game["p2"]
    score1, score2 = 0, 0
    log = "🏟 <b>POKEMON BATTLE</b>\n\n"

    # 1. HP Check
    h1, h2 = p1['team'].get('HP', {}).get('value', 0), p2['team'].get('HP', {}).get('value', 0)
    log += f"❤️ HP: {h1} vs {h2} -> "
    if h1 > h2: score1 += 1; log += "🔵 +1\n"
    elif h2 > h1: score2 += 1; log += "🔴 +1\n"
    else: log += "Tie\n"

    # Type check
    n1, n2 = p1['team'].get('Type', {}).get('pokemon'), p2['team'].get('Type', {}).get('pokemon')
    if n1 and n2:
        d1, d2 = global_state.POKEMON_DATA[n1], global_state.POKEMON_DATA[n2]
        
        # 2. Physical
        p1_stats = {'hp': h1, 'atk': p1['team']['Atk']['value'], 'def': p1['team']['Def']['value'], 'spe': p1['team']['Spe']['value'], 'types': d1['types']}
        p2_stats = {'hp': h2, 'atk': p2['team']['Atk']['value'], 'def': p2['team']['Def']['value'], 'spe': p2['team']['Spe']['value'], 'types': d2['types']}
        res_phy = simulate_matchup(p1_stats, p2_stats)
        
        log += "⚔️ Physical: "
        if res_phy['winner'] == 'attacker': score1+=1; log += f"🔵 wins in {res_phy['attacker_turns']} hits\n"
        elif res_phy['winner'] == 'defender': score2+=1; log += f"🔴 wins in {res_phy['defender_turns']} hits\n"
        else: log += "Draw\n"

        # 3. Special
        p1_sp = {'hp': h1, 'atk': p1['team']['SpA']['value'], 'def': p1['team']['SpD']['value'], 'spe': p1['team']['Spe']['value'], 'types': d1['types']}
        p2_sp = {'hp': h2, 'atk': p2['team']['SpA']['value'], 'def': p2['team']['SpD']['value'], 'spe': p2['team']['Spe']['value'], 'types': d2['types']}
        res_sp = simulate_matchup(p1_sp, p2_sp)

        log += "🔮 Special: "
        if res_sp['winner'] == 'attacker': score1+=1; log += f"🔵 wins in {res_sp['attacker_turns']} hits\n"
        elif res_sp['winner'] == 'defender': score2+=1; log += f"🔴 wins in {res_sp['defender_turns']} hits\n"
        else: log += "Draw\n"

    result = 1 if score1 > score2 else 0 if score2 > score1 else 0.5
    winner = p1['name'] if result == 1 else p2['name'] if result == 0 else "Draw"
    
    # ELO Update
    d1, d2 = update_leaderboard_elo(
        p1['id'],
        p1['name'],
        p2['id'],
        p2['name'],
        result,
        chat_id=game.get("chat_id"),
        chat_title=game.get("chat_title"),
    )
    
    final = f"{log}\n🔵 {score1} ({d1:+}) | 🔴 {score2} ({d2:+})\n🏆 Winner: {html.escape(winner)}"
    game['status'] = 'finished'
    return final

async def simulate_pokemon_battle(client, message, game):
    chat_id = message.chat.id
    priority_user_id = game.get("p1", {}).get("id", 0)

    temp_msg = await client.send_message(chat_id, "⚔️ **Simulating Pokemon Battle...**")

    # Acquire Slot
    async with manager.get_semaphore(priority_user_id):
        try:
            final_text = await asyncio.to_thread(_process_pokemon_battle_sync, game)
            
            await ensure_display_message(client, chat_id, game, final_text)
            try:
                await temp_msg.delete()
            except: pass
        except Exception as e:
            logging.error(f"Pokemon Battle Error: {e}", exc_info=True)
            await temp_msg.edit_text("❌ An error occurred during battle simulation.")
# --- CLEANUP THREADS ---
def forfeit_monitor(app_ref):
    while True:
        time.sleep(FORFEIT_CHECK_INTERVAL)
        now = time.time()
        with global_state.GAMES_LOCK:
            for chat_id, games in list(global_state.GAMES.items()):
                for gid, game in list(games.items()):
                    if game.get('status') == 'finished': continue
                    if (now - game.get('last_activity', 0)) > FORFEIT_TIMEOUT:
                        game['status'] = 'finished'
                        # In a real scenario, you'd use app_ref to send a message here
                        logging.info(f"Game {gid} forfeited due to timeout")

def cleanup_old_games():
    while True:
        time.sleep(GAME_CLEANUP_INTERVAL)
        now = time.time()
        with global_state.GAMES_LOCK:
            for chat_id, games in list(global_state.GAMES.items()):
                for gid, game in list(games.items()):
                    inactivity = now - game.get('last_activity', 0)
                    if inactivity > GAME_INACTIVITY_TIMEOUT or (game.get('status') == 'finished' and inactivity > FINISHED_GAME_TIMEOUT):
                        games.pop(gid, None)
                if not games:
                    global_state.GAMES.pop(chat_id, None)
