import json
import os
import re
import logging
import database
import global_state
from config import CHARACTERS_FILE, POKEMON_FILE, ACHIEVEMENTS_FILE, DATA_DIR

def load_data():
    try:
        data = []
        
        # Prefer local JSON for anime characters so edits to data/character.json
        # are reflected immediately after restart even when Mongo is enabled.
        if os.path.exists(CHARACTERS_FILE):
            with open(CHARACTERS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        
        # Fallback to Mongo only if the JSON file is missing/empty.
        if not data and database.USE_MONGO and database.COL_CHARACTERS is not None:
            try:
                data = list(database.COL_CHARACTERS.find({}))
            except Exception:
                data = []

        if not data:
            logging.error(f"❌ No characters found! Checked MongoDB and {CHARACTERS_FILE}")
            return

        for char in data:
            name = char.get("name")
            stats = char.get("stats")
            series = char.get("series", "Unknown")
            img = char.get("img")

            if name and stats:
                unique_char_id = f"{name} | {series}"
                global_state.CHAR_STATS[unique_char_id] = stats
                if img:
                    global_state.CHAR_IMAGES[unique_char_id] = img

                norm_series = re.sub(r'[^a-z0-9]', '', series.lower())
                global_state.SERIES_MAP.setdefault(norm_series, []).append(unique_char_id)
                global_state.SERIES_DISPLAY[norm_series] = series

        global_state.ANIME_CHARACTERS = list(global_state.CHAR_STATS.keys())
        logging.info(f"✅ Loaded {len(global_state.ANIME_CHARACTERS)} characters.")
    except Exception as e:
        logging.error(f"❌ Failed to load data: {e}")

def load_pokemon_data():
    try:
        if os.path.exists(POKEMON_FILE):
            with open(POKEMON_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for p in data:
                if all(k in p for k in ["name", "stats", "types", "region", "is_legendary"]):
                    poke_name = p["name"]
                    global_state.POKEMON_DATA[poke_name] = {
                        "stats": p["stats"],
                        "types": p["types"],
                        "region": p["region"],
                        "is_legendary": p["is_legendary"]
                    }
                    global_state.POKEMON_LIST.append(poke_name)
                    # Load Pokemon images if available
                    if "img" in p:
                        unique_poke_id = f"{poke_name} | Pokemon"
                        global_state.CHAR_IMAGES[unique_poke_id] = p["img"]
            logging.info(f"✅ Loaded {len(global_state.POKEMON_LIST)} Pokémon.")
        else:
            logging.error(f"❌ {POKEMON_FILE} not found!")
    except Exception as e:
        logging.error(f"❌ Failed to load Pokémon data: {e}")

def load_marvel_data():
    try:
        marvel_file = os.path.join(DATA_DIR, "marvel.json")
        if os.path.exists(marvel_file):
            with open(marvel_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            for char in data:
                name = char.get("name")
                stats = char.get("stats")
                img = char.get("img")
                series = char.get("series", "Marvel")
                
                if name and stats:
                    unique_char_id = f"{name} | Marvel"
                    global_state.CHAR_STATS[unique_char_id] = stats
                    if img:
                        global_state.CHAR_IMAGES[unique_char_id] = img
                    # Register in a separate list for comics
                    global_state.MARVEL_CHARACTERS.append(unique_char_id)
                    
                    # Build series map for Marvel (similar to anime)
                    norm_series = re.sub(r'[^a-z0-9]', '', series.lower())
                    global_state.MARVEL_SERIES_MAP.setdefault(norm_series, []).append(unique_char_id)
                    global_state.MARVEL_SERIES_DISPLAY[norm_series] = series
                    
            logging.info(f"✅ Loaded {len(global_state.MARVEL_CHARACTERS)} Marvel characters with {len(global_state.MARVEL_SERIES_DISPLAY)} series.")
        else:
            logging.warning(f"⚠️ {marvel_file} not found!")
    except Exception as e:
        logging.error(f"❌ Failed to load Marvel data: {e}")

def load_achievements():
    try:
        if os.path.exists(ACHIEVEMENTS_FILE):
            with open(ACHIEVEMENTS_FILE, 'r', encoding='utf-8') as f:
                global_state.USER_ACHIEVEMENTS = json.load(f)
    except Exception:
        global_state.USER_ACHIEVEMENTS = {}
