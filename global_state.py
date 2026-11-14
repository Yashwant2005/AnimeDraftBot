import threading
import asyncio
import os
import json
from config import EVENT_FLAGS_FILE

# --- RUNTIME STATE ---
GAMES = {}
GAMES_LOCK = threading.RLock()
CALLBACK_SEMAPHORE = asyncio.Semaphore(10)

# One asyncio.Lock per game. Every draft button press for the same game is
# processed one-at-a-time, so a double tap (or two quick taps while Telegram is
# still editing the message) can never see half-updated state.
_GAME_LOCKS = {}


def get_game_lock(gid):
    lock = _GAME_LOCKS.get(gid)
    if lock is None:
        if len(_GAME_LOCKS) > 500:
            _prune_game_locks()
        lock = _GAME_LOCKS.setdefault(gid, asyncio.Lock())
    return lock


def _prune_game_locks():
    """Drop locks of games that no longer exist (called lazily, cheap)."""
    with GAMES_LOCK:
        alive = {gid for games in GAMES.values() for gid in games}
    for gid in [g for g, l in _GAME_LOCKS.items() if g not in alive and not l.locked()]:
        _GAME_LOCKS.pop(gid, None)

# --- LOADED DATA ---
CHAR_STATS = {}
CHAR_IMAGES = {}
ANIME_CHARACTERS = []
MARVEL_CHARACTERS = []
SERIES_MAP = {}
SERIES_DISPLAY = {}
MARVEL_SERIES_MAP = {}
MARVEL_SERIES_DISPLAY = {}

POKEMON_DATA = {}
POKEMON_LIST = []

# --- USER DATA CACHE ---
ACTIVE_CHATS = set()
ACTIVE_USERS = set()
ADMIN_IDS = [6265981509]
USER_ACHIEVEMENTS = {}

# --- EVENTS ---
NEW_YEAR_EVENT = os.environ.get("NEW_YEAR_EVENT", "").lower() in ("1", "true", "yes")

# Load persistent event flags
try:
    if os.path.exists(EVENT_FLAGS_FILE):
        with open(EVENT_FLAGS_FILE, "r", encoding="utf-8") as _f:
            _ef = json.load(_f)
            NEW_YEAR_EVENT = bool(_ef.get("NEW_YEAR_EVENT", NEW_YEAR_EVENT))
except Exception:
    pass

AVAILABLE_ACHIEVEMENTS = {
    "first_win": "🏆 First Victory",
    "10_wins": "⭐ 10 Wins",
    "50_wins": "💫 50 Wins",
    "100_wins": "✨ 100 Wins",
    "top_rated": "👑 Top Rated",
    "legendary": "🌟 Legendary",
    "draft_master": "🎯 Draft Master",
    "undefeated": "🎖 Undefeated Streak",
    "s1" : "🎉 Season 1 Anime King"
}