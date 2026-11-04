import json
import os
import re
import logging
import database
import global_state
from config import CHARACTERS_FILE, POKEMON_FILE, ACHIEVEMENTS_FILE, DATA_DIR

def _load_character_sources(local_file):
    data = []
    if os.path.exists(local_file):
        with open(local_file, "r", encoding="utf-8") as f:
            data.extend(json.load(f))

    if database.USE_MONGO and database.COL_CHARACTERS is not None:
        try:
            data.extend(database.COL_CHARACTERS.find({'archived': {'$ne': True}}))
        except Exception as exc:
            logging.warning("Could not load saved characters from MongoDB: %s", exc)

    merged = {}
    for char in data:
        name = char.get("name")
        series = char.get("series", "Unknown")
        if name:
            merged[(name, series)] = char
    return list(merged.values())

def _is_comic_character(char):
    if char.get("kind") == "comic":
        return True
    if char.get("kind") == "anime":
        return False
    return "paragon" in (char.get("stats") or {})

def load_data():
    try:
        data = [
            char for char in _load_character_sources(CHARACTERS_FILE)
            if not _is_comic_character(char)
        ]
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
                norm_series = re.sub(r"[^a-z0-9]", "", series.lower())
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
            for pokemon in data:
                if all(k in pokemon for k in ["name", "stats", "types", "region", "is_legendary"]):
                    pokemon_name = pokemon["name"]
                    global_state.POKEMON_DATA[pokemon_name] = {
                        "stats": pokemon["stats"],
                        "types": pokemon["types"],
                        "region": pokemon["region"],
                        "is_legendary": pokemon["is_legendary"],
                    }
                    global_state.POKEMON_LIST.append(pokemon_name)
                    if "img" in pokemon:
                        global_state.CHAR_IMAGES[f"{pokemon_name} | Pokemon"] = pokemon["img"]
            logging.info(f"✅ Loaded {len(global_state.POKEMON_LIST)} Pokémon.")
        else:
            logging.error(f"❌ {POKEMON_FILE} not found!")
    except Exception as e:
        logging.error(f"❌ Failed to load Pokémon data: {e}")

def load_marvel_data():
    try:
        marvel_file = os.path.join(DATA_DIR, "marvel.json")
        data = [char for char in _load_character_sources(marvel_file) if _is_comic_character(char)]
        if data:
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
        elif not os.path.exists(marvel_file):
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
