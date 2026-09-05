import json
import global_state
from config import *
import database
import os
import logging
import time
import asyncio

def calculate_elo(rating_a, rating_b, actual_score_a):
    K = 32
    expected_a = 1 / (1 + 10 ** ((rating_b - rating_a) / 400))
    new_rating_a = rating_a + K * (actual_score_a - expected_a)
    return int(new_rating_a)

def update_leaderboard_elo(p1_id, p1_name, p2_id, p2_name, result, chat_id=None, chat_title=None):
    # result: 1 (p1 wins), 0 (p2 wins), 0.5 (draw)
    u1 = database.get_user_stats(p1_id, p1_name)
    u2 = database.get_user_stats(p2_id, p2_name)
    base_u1 = dict(u1)
    base_u2 = dict(u2)

    r1, r2 = u1.get("rating", 1200), u2.get("rating", 1200)

    def rank_index(rating):
        if rating < 1000: return 0
        if rating < 1300: return 1
        if rating < 1600: return 2
        if rating < 2000: return 3
        if rating < 2500: return 4
        return 5

    def get_deltas(rating1, rating2):
        if result == 0.5:
            return 0, 0
        if result == 1:
            diff = rank_index(rating1) - rank_index(rating2)
            delta_w, delta_l = (16, -10) if diff >= 2 else (20, -15) if diff == 1 else (28, -20) if diff == 0 else (35, -12)
            return int(delta_w), int(delta_l)
        diff = rank_index(rating2) - rank_index(rating1)
        delta_w, delta_l = (16, -10) if diff >= 2 else (20, -15) if diff == 1 else (28, -20) if diff == 0 else (35, -12)
        return int(delta_l), int(delta_w)

    def apply_event_rules(delta_a, delta_b):
        if global_state.NEW_YEAR_EVENT:
            delta_a = max(0, delta_a)
            delta_b = max(0, delta_b)
        return delta_a, delta_b

    delta1, delta2 = apply_event_rules(*get_deltas(r1, r2))

    u1.update({"name": p1_name, "matches": u1["matches"] + 1, "rating": max(0, r1 + delta1)})
    u2.update({"name": p2_name, "matches": u2["matches"] + 1, "rating": max(0, r2 + delta2)})
    
    if result == 1: u1["wins"] += 1
    elif result == 0: u2["wins"] += 1

    u1["user_id"], u2["user_id"] = p1_id, p2_id
    database.save_user_stats(p1_id, u1)
    database.save_user_stats(p2_id, u2)

    if chat_id is not None and database.USE_MONGO and database.COL_GROUP_LEADERBOARD is not None:
        try:
            col = database.COL_GROUP_LEADERBOARD
            default1 = {
                "chat_id": chat_id,
                "chat_title": chat_title,
                "user_id": p1_id,
                "name": p1_name,
                "wins": base_u1.get("wins", 0),
                "matches": base_u1.get("matches", 0),
                "rating": base_u1.get("rating", 1200),
            }
            default2 = {
                "chat_id": chat_id,
                "chat_title": chat_title,
                "user_id": p2_id,
                "name": p2_name,
                "wins": base_u2.get("wins", 0),
                "matches": base_u2.get("matches", 0),
                "rating": base_u2.get("rating", 1200),
            }
            g1 = col.find_one({"chat_id": chat_id, "user_id": p1_id}) or default1
            g2 = col.find_one({"chat_id": chat_id, "user_id": p2_id}) or default2
            gr1, gr2 = g1.get("rating", 1200), g2.get("rating", 1200)
            gd1, gd2 = apply_event_rules(*get_deltas(gr1, gr2))

            g1.update({
                "chat_id": chat_id,
                "chat_title": chat_title,
                "user_id": p1_id,
                "name": p1_name,
                "matches": g1.get("matches", 0) + 1,
                "rating": max(0, gr1 + gd1),
            })
            g2.update({
                "chat_id": chat_id,
                "chat_title": chat_title,
                "user_id": p2_id,
                "name": p2_name,
                "matches": g2.get("matches", 0) + 1,
                "rating": max(0, gr2 + gd2),
            })

            if result == 1:
                g1["wins"] = g1.get("wins", 0) + 1
            elif result == 0:
                g2["wins"] = g2.get("wins", 0) + 1

            col.update_one({"chat_id": chat_id, "user_id": p1_id}, {"$set": g1}, upsert=True)
            col.update_one({"chat_id": chat_id, "user_id": p2_id}, {"$set": g2}, upsert=True)
        except Exception as e:
            logging.warning(f"Failed to update group draft leaderboard: {e}")

    return int(u1["rating"] - r1), int(u2["rating"] - r2)

def get_top_3_users():
    """Get top 3 users by rating for Anime King title"""
    try:
        if database.USE_MONGO and database.COL_LEADERBOARD is not None:
            users = list(database.COL_LEADERBOARD.find({}).sort("rating", -1).limit(3))
            return [u.get("user_id") for u in users if "user_id" in u]
        else:
            if os.path.exists(database.LEADERBOARD_FILE):
                with open(database.LEADERBOARD_FILE, "r") as f:
                    data = json.load(f)
                    sorted_users = sorted(data.items(), key=lambda x: x[1].get("rating", 0), reverse=True)
                    return [int(uid) for uid, stats in sorted_users[:3]]
            return []
    except Exception:
        return []

# Owner constants are imported via `from config import *` at the top

async def is_admin(user_id):
    """Check if user is admin (in owner list or mod)"""
    # Check if in owner list (includes main owner)
    if user_id in OWNER_IDS:
        return True
    
    # Check MongoDB for mod status
    if database.USE_MONGO:
        mod_data = database.get_mod(user_id)
        return mod_data is not None
    
    # If not using MongoDB, check file-based storage
    return database.is_mod_file_based(user_id)

def is_mod(user_id):
    """Check if a user is a registered mod (clone owner). Synchronous helper."""
    try:
        return database.is_mod(user_id)
    except Exception:
        return False

def is_clone_instance():
    """Return True if this bot instance is a cloned instance (its BOT_TOKEN is registered to a mod)."""
    try:
        # BOT_TOKEN imported via `from config import *`
        mods = database.get_all_mods()
        if not mods:
            return False
        for m in mods:
            if m.get("bot_token") and m.get("bot_token") == BOT_TOKEN:
                return True
    except Exception:
        return False
    return False

def is_owner(user_id):
    """Check if user is owner (main owner or in owner list)"""
    # Check main owner
    if user_id == OWNER_ID:
        return True
    
    # Check owner list from config
    if hasattr(OWNER_IDS, '__contains__') and user_id in OWNER_IDS:
        return True
    
    return False
    
def get_top_3_users():
    """Get the top 3 users by rating"""
    try:
        if database.USE_MONGO and database.COL_LEADERBOARD is not None:
            # Get top 3 from MongoDB
            users = list(database.COL_LEADERBOARD.find({}).sort("rating", -1).limit(3))
            return [u.get("user_id") for u in users if u.get("user_id")]
        else:
            # Load from JSON file
            if os.path.exists(LEADERBOARD_FILE):
                with open(LEADERBOARD_FILE, "r") as f:
                    data = json.load(f)
                # Sort by rating descending and get top 3
                sorted_users = sorted(data.items(), key=lambda x: x[1].get("rating", 0), reverse=True)
                return [int(uid) for uid, _ in sorted_users[:3]]
    except Exception:
        pass
    return []


# ---------------------------------------------------------------------------
# instrumentation helpers
# ---------------------------------------------------------------------------

def is_admin_sync(user_id):
    """Synchronous wrapper around :func:`is_admin` for filters.

    Avoids "coroutine was never awaited" by running the async
    ``is_admin`` in a temporary loop when necessary.
    """
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        return asyncio.run(is_admin(user_id))

    if loop.is_running():
        new_loop = asyncio.new_event_loop()
        return new_loop.run_until_complete(is_admin(user_id))
    else:
        return loop.run_until_complete(is_admin(user_id))


def log_exceptions(func):
    """Decorator to log entry, exit, duration, and exceptions in handlers."""
    import functools
    import inspect

    if inspect.iscoroutinefunction(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            start = time.time()
            logging.info(f"[HANDLER] {func.__name__} called")
            try:
                result = await func(*args, **kwargs)
                elapsed = time.time() - start
                logging.info(f"[HANDLER] {func.__name__} completed in {elapsed:.2f}s")
                return result
            except Exception:
                logging.exception(f"Unhandled exception in handler {func.__name__}")
                raise
        return wrapper
    else:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            start = time.time()
            logging.info(f"[HANDLER] {func.__name__} called")
            try:
                result = func(*args, **kwargs)
                elapsed = time.time() - start
                logging.info(f"[HANDLER] {func.__name__} completed in {elapsed:.2f}s")
                return result
            except Exception:
                logging.exception(f"Unhandled exception in handler {func.__name__}")
                raise
        return wrapper

