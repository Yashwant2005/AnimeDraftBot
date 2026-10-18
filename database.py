# database.py
import json
import os
import logging
import threading
from datetime import datetime
from pymongo import MongoClient
from config import *
import global_state

MONGO_CLIENT = None
MONGO_DB = None
COL_ACTIVE_CHATS = None
COL_ACTIVE_USERS = None
COL_LEADERBOARD = None
COL_GROUP_LEADERBOARD = None
COL_CHARACTERS = None
COL_MODS = None  # New collection for mods
COL_WAIFUS = None
COL_WAIFU_USERS = None
COL_WAIFU_TOTALS = None
COL_WAIFU_GROUP_USERS = None
COL_WAIFU_TOP_GROUPS = None
COL_WAIFU_MARKET = None
COL_WAIFU_CREWS = None
USE_MONGO = False

# Thread-local storage for current client context (for clones)
_thread_local = threading.local()

def set_current_client(client):
    """Set the current client context for the thread (used for clone detection)."""
    _thread_local.current_client = client
    logging.debug(f"[DB] Set current client context to {getattr(client, 'name', 'unknown')}")

def get_current_client():
    """Get the current client context for the thread."""
    return getattr(_thread_local, 'current_client', None)

def get_clone_owner_id(client=None):
    """Return the owner ID of the clone context.

    If ``client`` is supplied it will be inspected first, otherwise we fall
    back to the thread-local :func:`get_current_client` behaviour.  This makes
    callers more reliable when operating across Pyrogram's internal worker
    threads.
    """
    if client is not None and getattr(client, 'is_clone', False):
        return getattr(client, 'clone_owner_id', None)
    client = get_current_client()
    if client and getattr(client, 'is_clone', False):
        return getattr(client, 'clone_owner_id', None)
    return None

def get_clone_db(owner_id=None, client=None):
    """Return a clone-specific MongoDB database.

    ``owner_id`` may be supplied explicitly.  If omitted the function will
    try to derive the owner from ``client`` (if provided) or fall back to
    :func:`get_clone_owner_id` which uses thread-local state.  This makes the
    helper safe to call from any thread without relying on thread-local
    context, which previously caused clone handlers to accidentally query the
    main database.

    Returns ``None`` if we are not running in a clone context or if MongoDB
    is unavailable.
    """
    # explicit precedence: owner_id > client > thread-local
    if owner_id is None:
        if client is not None and getattr(client, 'is_clone', False):
            owner_id = getattr(client, 'clone_owner_id', None)
        else:
            owner_id = get_clone_owner_id()
    if not owner_id:
        return None
    if not USE_MONGO or not MONGO_CLIENT:
        logging.debug(f"[DB] MongoDB not available for clone {owner_id}")
        return None
    try:
        db_name = f"anime_bot_clone_{owner_id}"
        logging.debug(f"[DB] Getting clone DB: {db_name}")
        return MONGO_CLIENT[db_name]
    except Exception as e:
        logging.error(f"[DB] Error getting clone DB: {e}")
        return None

def init_mongo():
    global MONGO_CLIENT, MONGO_DB, COL_ACTIVE_CHATS, COL_ACTIVE_USERS, COL_LEADERBOARD, COL_GROUP_LEADERBOARD, COL_CHARACTERS, COL_MODS, USE_MONGO
    global COL_WAIFUS, COL_WAIFU_USERS, COL_WAIFU_TOTALS, COL_WAIFU_GROUP_USERS, COL_WAIFU_TOP_GROUPS, COL_WAIFU_MARKET, COL_WAIFU_CREWS
    try:
        MONGO_CLIENT = MongoClient(
            MONGO_URI,
            serverSelectionTimeoutMS=5000,
            maxPoolSize=50,
            minPoolSize=5,
            maxIdleTimeMS=30000,
            socketTimeoutMS=20000,
            retryWrites=True
        )
        MONGO_CLIENT.admin.command('ping')
        MONGO_DB = MONGO_CLIENT[MONGO_DB_NAME]
        COL_ACTIVE_CHATS = MONGO_DB['active_chats']
        COL_ACTIVE_USERS = MONGO_DB['active_users']
        COL_LEADERBOARD = MONGO_DB['leaderboard']
        COL_GROUP_LEADERBOARD = MONGO_DB['group_leaderboard']
        COL_CHARACTERS = MONGO_DB['characters']
        COL_MODS = MONGO_DB['mods']  # Initialize mods collection
        COL_WAIFUS = MONGO_DB['anime_characters']
        COL_WAIFU_USERS = MONGO_DB['users']
        COL_WAIFU_TOTALS = MONGO_DB['chat_settings']
        COL_WAIFU_GROUP_USERS = MONGO_DB['group_user_totals']
        COL_WAIFU_TOP_GROUPS = MONGO_DB['top_groups']
        COL_WAIFU_MARKET = MONGO_DB['market_listings']
        COL_WAIFU_CREWS = MONGO_DB['crews']
        
        # Ensure Indexes
        try:
            COL_ACTIVE_CHATS.create_index('chat_id', unique=True)
            COL_ACTIVE_USERS.create_index('user_id', unique=True)
            COL_LEADERBOARD.create_index('user_id', unique=True)
            COL_GROUP_LEADERBOARD.create_index([('chat_id', 1), ('user_id', 1)], unique=True)
            COL_GROUP_LEADERBOARD.create_index([('chat_id', 1), ('rating', -1)])
            COL_GROUP_LEADERBOARD.create_index([('chat_id', 1), ('wins', -1)])
            COL_CHARACTERS.create_index('name', unique=True)
            COL_MODS.create_index('user_id', unique=True)  # Unique index for mods
            COL_WAIFUS.create_index('id', unique=True)
            COL_WAIFUS.create_index('name')
            COL_WAIFU_USERS.create_index('id', unique=True)
            COL_WAIFU_CREWS.create_index('name', unique=True)
        except: pass

        USE_MONGO = True
        logging.info("✅ Connected to MongoDB.")
    except Exception as e:
        USE_MONGO = False
        logging.error(f"❌ MongoDB connection failed: {e}")

def load_id_set(filename, client=None):
    """Load a set of IDs (chats or users), optionally using ``client`` to
    determine clone context.

    ``client`` should be the :class:`Client` instance received by handlers.
    If provided, we derive the clone owner directly from it; otherwise we fall
    back to the thread-local ``get_clone_owner_id()``.  This avoids relying on
    thread-local storage, which Pyrogram may not preserve across its worker
    threads and was the root cause of clones accidentally reading the main
    bot's collections.
    """
    # determine clone owner id using explicit client when available
    clone_owner = get_clone_owner_id(client)
    if USE_MONGO:
        # choose appropriate collection depending on clone context
        if clone_owner:
            # avoid thread-local by asking get_clone_db with explicit owner
            clone_db = get_clone_db(owner_id=clone_owner, client=client)
            if clone_db is not None:
                col = clone_db['active_chats'] if filename == CHATS_FILE else clone_db['active_users']
                logging.info(f"[DB] (clone) owner={clone_owner} using collection {col.name}")
            else:
                # should not happen, fallback to main but log loudly
                col = COL_ACTIVE_CHATS if filename == CHATS_FILE else COL_ACTIVE_USERS
                logging.warning(f"[DB] Clone DB None for owner {clone_owner}; falling back to main collection")
        else:
            col = COL_ACTIVE_CHATS if filename == CHATS_FILE else COL_ACTIVE_USERS
            logging.info(f"[DB] (main) using collection {col.name}")
        key = "chat_id" if filename == CHATS_FILE else "user_id"
        try:
            return set(d[key] for d in col.find({}, {"_id": 0, key: 1}))
        except Exception as e:
            logging.error(f"[DB] load_id_set mongo error: {e}")
            return set()
    # JSON fallback
    if clone_owner:
        base, ext = os.path.splitext(filename)
        filename = f"{base}.clone_{clone_owner}{ext}"
        logging.debug(f"[DB] Using clone-specific file {filename} for load_id_set")
    if os.path.exists(filename):
        try:
            with open(filename, "r") as f:
                return set(json.load(f))
        except Exception as e:
            logging.error(f"[DB] load_id_set file error: {e}")
            return set()
    return set()

def save_id_set(filename, id_set, client=None):
    """Save a set of IDs, routing to main or clone storage as needed.

    ``client`` may be provided to guarantee correct clone detection as above.
    """
    # unify clone detection using our new helper which accepts a client
    clone_owner = get_clone_owner_id(client)
    if USE_MONGO:
        if clone_owner:
            clone_db = get_clone_db(owner_id=clone_owner, client=client)
            if clone_db is not None:
                col = clone_db['active_chats'] if filename == CHATS_FILE else clone_db['active_users']
                logging.info(f"[DB] (clone) owner={clone_owner} saving to collection {col.name}")
            else:
                col = COL_ACTIVE_CHATS if filename == CHATS_FILE else COL_ACTIVE_USERS
                logging.warning(f"[DB] Clone DB None for owner {clone_owner}; saving to main collection")
        else:
            col = COL_ACTIVE_CHATS if filename == CHATS_FILE else COL_ACTIVE_USERS
            logging.info(f"[DB] (main) saving to collection {col.name}")
        key = "chat_id" if filename == CHATS_FILE else "user_id"
        try:
            if not id_set:
                return
            for i in id_set:
                try:
                    col.update_one({key: i}, {"$set": {key: i}}, upsert=True)
                except Exception:
                    pass
        except Exception as e:
            logging.error(f"[DB] save_id_set mongo error: {e}")
        return
    # JSON fallback
    if clone_owner:
        base, ext = os.path.splitext(filename)
        filename = f"{base}.clone_{clone_owner}{ext}"
        logging.debug(f"[DB] Using clone-specific file {filename} for save_id_set")
    try:
        with open(filename, "w") as f:
            json.dump(list(id_set), f)
    except Exception as e:
        logging.error(f"[DB] save_id_set file error: {e}")

def get_user_stats(user_id, name="Unknown"):
    """Get user stats from main or clone-specific database."""
    default = {"name": name, "wins": 0, "matches": 0, "rating": 1200}
    try:
        clone_db = get_clone_db()
        if clone_db is not None:
            # Use clone-specific collection
            logging.debug(f"[DB] Getting user {user_id} stats from clone DB")
            col = clone_db['leaderboard']
            u = col.find_one({"user_id": user_id})
            if u:
                return u
            return default
        elif USE_MONGO:
            u = COL_LEADERBOARD.find_one({"user_id": user_id})
            if u:
                return u
            return default
        else:
            if os.path.exists(LEADERBOARD_FILE):
                with open(LEADERBOARD_FILE, "r") as f: data = json.load(f)
                return data.get(str(user_id), default)
            return default
    except Exception as e:
        logging.error(f"[DB] Error getting user stats for {user_id}: {e}")
        return default

def save_user_stats(user_id, stats):
    """Save user stats to main or clone-specific database."""
    try:
        clone_db = get_clone_db()
        if clone_db is not None:
            logging.debug(f"[DB] Saving user {user_id} stats to clone DB")
            col = clone_db['leaderboard']
            col.update_one({"user_id": user_id}, {"$set": stats}, upsert=True)
        elif USE_MONGO:
            COL_LEADERBOARD.update_one({"user_id": user_id}, {"$set": stats}, upsert=True)
        else:
            data = {}
            if os.path.exists(LEADERBOARD_FILE):
                with open(LEADERBOARD_FILE, "r") as f: data = json.load(f)
            data[str(user_id)] = stats
            with open(LEADERBOARD_FILE, "w") as f: json.dump(data, f)
    except Exception as e:
        logging.error(f"[DB] Error saving user stats for {user_id}: {e}")

def load_admin_ids():
    try:
        if os.path.exists(ADMIN_IDS_FILE):
            with open(ADMIN_IDS_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, list):
                    global_state.ADMIN_IDS = loaded
    except Exception as e:
        logging.error(f"Failed to load admin IDs: {e}")

def save_admin_ids():
    try:
        with open(ADMIN_IDS_FILE, "w", encoding="utf-8") as f:
            json.dump(list(global_state.ADMIN_IDS), f, indent=2)
    except Exception: pass

# ==================== MOD FUNCTIONS ====================

def get_mod(user_id):
    """Get mod from database"""
    if USE_MONGO:
        try:
            return COL_MODS.find_one({"user_id": user_id})
        except Exception as e:
            logging.error(f"Error getting mod: {e}")
            return None
    else:
        # File-based storage
        mods = load_mods_file()
        for mod in mods:
            if mod.get("user_id") == user_id:
                return mod
        return None

def add_mod(user_id, username=None, first_name=None, added_by=None):
    """Add a mod to database"""
    if USE_MONGO:
        try:
            # Check if already mod
            if COL_MODS.find_one({"user_id": user_id}):
                return False
                
            mod_data = {
                "user_id": user_id,
                "username": username,
                "first_name": first_name,
                "added_by": added_by,
                "bot_token": None,
                "added_at": datetime.now()
            }
            COL_MODS.insert_one(mod_data)
            return True
        except Exception as e:
            logging.error(f"Error adding mod: {e}")
            return False
    else:
        # File-based storage
        mods = load_mods_file()
        # Check if already exists
        if any(m.get("user_id") == user_id for m in mods):
            return False
        mods.append({
            "user_id": user_id,
            "username": username,
            "first_name": first_name,
            "added_by": added_by,
            "bot_token": None,
            "added_at": datetime.now().isoformat()
        })
        save_mods_file(mods)
        return True

def update_mod_token(user_id, bot_token):
    """Set or update the bot_token for a mod (file or mongo)."""
    if USE_MONGO:
        try:
            COL_MODS.update_one({"user_id": user_id}, {"$set": {"bot_token": bot_token}}, upsert=False)
            return True
        except Exception as e:
            logging.error(f"Error updating mod token: {e}")
            return False
    else:
        mods = load_mods_file()
        changed = False
        for m in mods:
            if m.get("user_id") == user_id:
                m["bot_token"] = bot_token
                changed = True
                break
        if not changed:
            # If mod not found, add a new entry with token
            mods.append({
                "user_id": user_id,
                "username": None,
                "first_name": None,
                "added_by": None,
                "bot_token": bot_token,
                "added_at": datetime.now().isoformat()
            })
        save_mods_file(mods)
        return True

def is_mod(user_id):
    """Check if a user is a registered mod (clone owner)."""
    if USE_MONGO:
        try:
            return COL_MODS.find_one({"user_id": user_id}) is not None
        except Exception:
            return False
    else:
        return is_mod_file_based(user_id)

def remove_mod(user_id):
    """Remove a mod from database"""
    if USE_MONGO:
        try:
            result = COL_MODS.delete_one({"user_id": user_id})
            return result.deleted_count > 0
        except Exception as e:
            logging.error(f"Error removing mod: {e}")
            return False
    else:
        # File-based storage
        mods = load_mods_file()
        new_mods = [m for m in mods if m.get("user_id") != user_id]
        if len(new_mods) == len(mods):
            return False  # No change, user wasn't a mod
        save_mods_file(new_mods)
        return True

def get_all_mods():
    """Get all mods"""
    if USE_MONGO:
        try:
            return list(COL_MODS.find())
        except Exception as e:
            logging.error(f"Error getting mods: {e}")
            return []
    else:
        return load_mods_file()

def is_mod_file_based(user_id):
    """Check if user is mod in file-based storage"""
    mods = load_mods_file()
    return any(m.get("user_id") == user_id for m in mods)

def load_mods_file():
    """Load mods from file"""
    mods_file = os.path.join(DATA_DIR, "mods.json")
    if os.path.exists(mods_file):
        try:
            with open(mods_file, 'r') as f:
                return json.load(f)
        except:
            return []
    return []

def save_mods_file(mods):
    """Save mods to file"""
    mods_file = os.path.join(DATA_DIR, "mods.json")
    try:
        with open(mods_file, 'w') as f:
            json.dump(mods, f, indent=2)
    except Exception as e:
        logging.error(f"Error saving mods file: {e}")

# ==================== CHARACTER FUNCTIONS ====================

def load_characters():
    """Load all characters from database"""
    if USE_MONGO:
        try:
            return list(COL_CHARACTERS.find())
        except:
            return []
    else:
        if os.path.exists(CHARACTERS_FILE):
            try:
                with open(CHARACTERS_FILE, 'r') as f:
                    return json.load(f)
            except:
                return []
        return []

def save_character(char_data):
    """Save a character to database"""
    if USE_MONGO:
        try:
            COL_CHARACTERS.update_one(
                {"name": char_data["name"]},
                {"$set": char_data},
                upsert=True
            )
            return True
        except:
            return False
    else:
        characters = load_characters()
        # Remove existing if any
        characters = [c for c in characters if c.get("name") != char_data["name"]]
        characters.append(char_data)
        try:
            with open(CHARACTERS_FILE, 'w') as f:
                json.dump(characters, f, indent=2)
            return True
        except:
            return False

# ==================== UTILITY FUNCTIONS ====================

def close_connection():
    """Close MongoDB connection"""
    global MONGO_CLIENT
    if MONGO_CLIENT:
        MONGO_CLIENT.close()
        logging.info("🔌 MongoDB connection closed.")
