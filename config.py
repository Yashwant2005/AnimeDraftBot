import os

# --- CREDENTIALS ---
API_ID = 25695711
API_HASH = "f20065cc26d4a31bf0efc0b44edaffa9"
BOT_TOKEN = "8327638135:AAHnkljBy8S6XklWEt6hCVeINR_TpQRyaOQ"
# Image/catalog source: this database is read-only to the bot.  It is used to
# obtain existing character artwork and metadata, never for game state.
IMAGE_MONGO_URI = os.getenv("IMAGE_MONGO_URI", "mongodb+srv://yesvashisht2005_db_user:yash2005@cluster0.nd8dam5.mongodb.net/?appName=Cluster0")
IMAGE_MONGO_DB_NAME = os.getenv("IMAGE_MONGO_DB_NAME", "anime_bot2")

# Game state (auction, bids, drafts, balances, rankings, groups, etc.) lives
# in its own logical MongoDB database. Mongo creates it on first startup.
GAME_MONGO_URI = os.getenv("GAME_MONGO_URI", IMAGE_MONGO_URI)
GAME_MONGO_DB_NAME = os.getenv("GAME_MONGO_DB_NAME", "anime_draft_game")

# Existing modules use these names for the writable game database.
MONGO_URI = GAME_MONGO_URI
MONGO_DB_NAME = GAME_MONGO_DB_NAME

# $20 Blind Auction mode
AUCTION_STARTING_BUDGET = 20
AUCTION_TEAM_SIZE = 5
AUCTION_POOL_SIZE = 10
AUCTION_BID_TIMEOUT_SECONDS = int(os.getenv("AUCTION_BID_TIMEOUT_SECONDS", "30"))
AUCTION_SELECTION_TIMEOUT_SECONDS = int(os.getenv("AUCTION_SELECTION_TIMEOUT_SECONDS", "60"))
# A lobby cannot reserve users forever, and a game whose timer jobs disappeared
# after a restart is released automatically after this much inactivity.
AUCTION_WAITING_TIMEOUT_SECONDS = int(os.getenv("AUCTION_WAITING_TIMEOUT_SECONDS", "300"))
AUCTION_STALE_TIMEOUT_SECONDS = int(os.getenv("AUCTION_STALE_TIMEOUT_SECONDS", "900"))

# Central private audit feed. Set AUDIT_LOG_CHAT_ID=0 to disable Telegram
# audit messages while keeping the normal local bot.log file.
AUDIT_LOG_CHAT_ID = int(os.getenv("AUDIT_LOG_CHAT_ID", "-1003972934720"))
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyBqc-Ws0SHRblCsp_qNUUtgoNUIT1FNRok")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

# --- DIRECTORY SETUP ---
DATA_DIR = "data"
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

# --- FILE PATHS ---
LEADERBOARD_FILE = os.path.join(DATA_DIR, "leaderboard.json")
CHATS_FILE = os.path.join(DATA_DIR, "active_chats.json")
USERS_FILE = os.path.join(DATA_DIR, "active_users.json")
CHARACTERS_FILE = os.path.join(DATA_DIR, "character.json")
ACHIEVEMENTS_FILE = os.path.join(DATA_DIR, "achievements.json")
EVENT_FLAGS_FILE = os.path.join(DATA_DIR, "event_flags.json")
POKEMON_FILE = os.path.join(DATA_DIR, "pokemon.json")
ADMIN_IDS_FILE = os.path.join(DATA_DIR, "admin_ids.json")

# --- GAME SETTINGS ---
DEFAULT_POWER = 50

# --- ROLE SETS ---
# Anime role names (keeps existing behavior/UI)
ANIME_ROLES = ["Captain", "Vice Captain", "Tank", "Healer", "Assassin", "Support 1", "Support 2", "Traitor"]

# Marvel / Comics role names (for data/marvel.json)
MARVEL_ROLES = [
    "Paragon",
    "Genius",
    "Powerhouse",
    "Mystic",
    "Street Level",
    "Cosmic",
    "Trickster",
    "Herald"
]

# Default role set used by UI and draft flow (keeps anime as default)
ROLES = ANIME_ROLES
# Primary owner ID (can be overridden via OWNER_ID env var)
OWNER_ID = int(os.getenv("OWNER_ID", "6265981509"))
# Additional owner IDs allowed to perform owner actions
OWNER_IDS = [6265981509]
CHAR_APPROVER_ID = int(os.getenv("CHAR_APPROVER_ID", "6265981509"))
HEALER_BONUS = 2.3

# Pokemon Settings
POKEMON_ROLES = ["HP", "Atk", "Def", "SpA", "SpD", "Spe", "Type"]
STAT_MAP = {
    "HP": "hp", "Atk": "attack", "Def": "defense", 
    "SpA": "special-attack", "SpD": "special-defense", "Spe": "speed"
}
POKEMON_LEVEL = 100
MOVE_POWER = 50
ASSIGN_TIMER_DURATION = 12
MAX_ACTIVE_DRAFTS_PER_CHAT = 50

# Timeouts (Seconds)
GAME_CLEANUP_INTERVAL = 180
GAME_INACTIVITY_TIMEOUT = 600
FINISHED_GAME_TIMEOUT = 1800
FORFEIT_TIMEOUT = 300
FORFEIT_CHECK_INTERVAL = 50

# --- TYPE CHART ---
TYPE_CHART = {
    'Normal': {'Rock': 0.5, 'Ghost': 0, 'Steel': 0.5},
    'Fire': {'Fire': 0.5, 'Water': 0.5, 'Grass': 2, 'Ice': 2, 'Bug': 2, 'Rock': 0.5, 'Dragon': 0.5, 'Steel': 2},
    'Water': {'Fire': 2, 'Water': 0.5, 'Grass': 0.5, 'Ground': 2, 'Rock': 2, 'Dragon': 0.5},
    'Electric': {'Water': 2, 'Electric': 0.5, 'Grass': 0.5, 'Ground': 0, 'Flying': 2, 'Dragon': 0.5},
    'Grass': {'Fire': 0.5, 'Water': 2, 'Grass': 0.5, 'Poison': 0.5, 'Ground': 2, 'Flying': 0.5, 'Bug': 0.5, 'Rock': 2, 'Dragon': 0.5, 'Steel': 0.5},
    'Ice': {'Fire': 0.5, 'Water': 0.5, 'Grass': 2, 'Ice': 0.5, 'Ground': 2, 'Flying': 2, 'Dragon': 2, 'Steel': 0.5},
    'Fighting': {'Normal': 2, 'Ice': 2, 'Poison': 0.5, 'Flying': 0.5, 'Psychic': 0.5, 'Bug': 0.5, 'Rock': 2, 'Ghost': 0, 'Dark': 2, 'Steel': 2, 'Fairy': 0.5},
    'Poison': {'Grass': 2, 'Poison': 0.5, 'Ground': 0.5, 'Rock': 0.5, 'Ghost': 0.5, 'Steel': 0, 'Fairy': 2},
    'Ground': {'Fire': 2, 'Electric': 2, 'Grass': 0.5, 'Poison': 2, 'Flying': 0, 'Bug': 0.5, 'Rock': 2, 'Steel': 2},
    'Flying': {'Electric': 0.5, 'Grass': 2, 'Fighting': 2, 'Bug': 2, 'Rock': 0.5, 'Steel': 0.5},
    'Psychic': {'Fighting': 2, 'Poison': 2, 'Psychic': 0.5, 'Dark': 0, 'Steel': 0.5},
    'Bug': {'Fire': 0.5, 'Grass': 2, 'Fighting': 0.5, 'Poison': 0.5, 'Flying': 0.5, 'Psychic': 2, 'Ghost': 0.5, 'Dark': 2, 'Steel': 0.5, 'Fairy': 0.5},
    'Rock': {'Fire': 2, 'Ice': 2, 'Fighting': 0.5, 'Ground': 0.5, 'Flying': 2, 'Bug': 2, 'Steel': 0.5},
    'Ghost': {'Normal': 0, 'Psychic': 2, 'Ghost': 2, 'Dark': 0.5},
    'Dragon': {'Dragon': 2, 'Steel': 0.5, 'Fairy': 0},
    'Dark': {'Psychic': 2, 'Ghost': 2, 'Dark': 0.5, 'Fighting': 0.5, 'Fairy': 0.5},
    'Steel': {'Ice': 2, 'Rock': 2, 'Fairy': 2, 'Fire': 0.5, 'Water': 0.5, 'Electric': 0.5, 'Steel': 0.5},
    'Fairy': {'Fighting': 2, 'Dragon': 2, 'Dark': 2, 'Fire': 0.5, 'Poison': 0.5, 'Steel': 0.5}
}

# --- MERGED BOTS CONFIGURATION ---
DAILY_COINS = 500
DUEL_WIN_COINS = 200
DUEL_LOSE_COINS = 50

