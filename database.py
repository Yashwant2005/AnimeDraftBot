# database.py
import json
import os
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from pymongo import MongoClient, UpdateOne
from pymongo.errors import DuplicateKeyError
from config import *
import global_state

MONGO_CLIENT = None
MONGO_DB = None
IMAGE_MONGO_CLIENT = None
IMAGE_MONGO_DB = None
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
COL_IMAGE_CHARACTERS = None
COL_GAME_HINTS = None
COL_AUCTIONS = None
COL_AUCTION_BIDS = None
COL_AUCTION_CHARACTER_POOL = None
COL_DRAFT_GAMES = None
COL_GROUP_ACTIVITY = None
COL_GROUP_ACTIVITY_DAILY = None
COL_GROUP_ACTIVITY_USERS = None
COL_GROUP_ACTIVITY_UPDATES = None
COL_DELETION_REQUESTS = None
COL_PRIVATE_USERS = None
USE_MONGO = False

# Thread-local storage for current client context (for clones)
_thread_local = threading.local()

# Fast in-memory cache for user stats to minimize DB lookups and boost speed
_USER_STATS_CACHE = {}
_USER_STATS_CACHE_LOCK = threading.Lock()
_CACHE_TTL = 60  # seconds

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

def _ensure_character_identity_index(collection=None):
    """Allow same-name characters from different series without ambiguous upserts."""
    collection = COL_CHARACTERS if collection is None else collection
    duplicate = next(collection.aggregate([
        {
            '$group': {
                '_id': {'name': '$name', 'series': '$series'},
                'count': {'$sum': 1},
            }
        },
        {'$match': {'count': {'$gt': 1}}},
        {'$limit': 1},
    ]), None)
    if duplicate:
        identity = duplicate.get('_id') or {}
        raise RuntimeError(
            'Cannot migrate character identity index: duplicate name/series '
            f'pair {identity.get("name")!r} / {identity.get("series")!r}.'
        )

    indexes = collection.index_information()
    compound = [
        (name, spec)
        for name, spec in indexes.items()
        if spec.get('key') == [('name', 1), ('series', 1)]
    ]
    if not any(spec.get('unique') for _, spec in compound):
        for name, _ in compound:
            collection.drop_index(name)
        collection.create_index(
            [('name', 1), ('series', 1)],
            unique=True,
            name='name_series_unique',
        )

    for name, spec in indexes.items():
        if spec.get('key') == [('name', 1)] and spec.get('unique'):
            collection.drop_index(name)

def init_mongo():
    global MONGO_CLIENT, MONGO_DB, IMAGE_MONGO_CLIENT, IMAGE_MONGO_DB, COL_ACTIVE_CHATS, COL_ACTIVE_USERS, COL_LEADERBOARD, COL_GROUP_LEADERBOARD, COL_CHARACTERS, COL_MODS, USE_MONGO
    global COL_WAIFUS, COL_IMAGE_CHARACTERS, COL_GAME_HINTS, COL_AUCTIONS, COL_AUCTION_BIDS, COL_AUCTION_CHARACTER_POOL, COL_DRAFT_GAMES, COL_WAIFU_USERS, COL_WAIFU_TOTALS, COL_WAIFU_GROUP_USERS, COL_WAIFU_TOP_GROUPS, COL_WAIFU_MARKET, COL_WAIFU_CREWS
    global COL_GROUP_ACTIVITY, COL_GROUP_ACTIVITY_DAILY, COL_GROUP_ACTIVITY_USERS, COL_GROUP_ACTIVITY_UPDATES, COL_DELETION_REQUESTS, COL_PRIVATE_USERS
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
        COL_WAIFU_USERS = MONGO_DB['users']
        COL_WAIFU_TOTALS = MONGO_DB['chat_settings']
        COL_WAIFU_GROUP_USERS = MONGO_DB['group_user_totals']
        COL_WAIFU_TOP_GROUPS = MONGO_DB['top_groups']
        COL_WAIFU_MARKET = MONGO_DB['market_listings']
        COL_WAIFU_CREWS = MONGO_DB['crews']
        COL_GROUP_ACTIVITY = MONGO_DB['group_activity']
        COL_GROUP_ACTIVITY_DAILY = MONGO_DB['group_activity_daily']
        COL_GROUP_ACTIVITY_USERS = MONGO_DB['group_activity_users']
        COL_GROUP_ACTIVITY_UPDATES = MONGO_DB['group_activity_updates']
        COL_DELETION_REQUESTS = MONGO_DB['deletion_requests']
        COL_PRIVATE_USERS = MONGO_DB['private_users']
        COL_GAME_HINTS = MONGO_DB['game_hint_cache']
        COL_AUCTIONS = MONGO_DB['auctions']
        COL_AUCTION_BIDS = MONGO_DB['auction_bids']
        COL_AUCTION_CHARACTER_POOL = MONGO_DB['auction_character_pool']
        COL_DRAFT_GAMES = MONGO_DB['draft_games']

        # Keep the legacy catalog database read-only. It may be hosted on the
        # same Atlas cluster, but it is deliberately a separate database handle.
        try:
            if IMAGE_MONGO_URI == MONGO_URI:
                IMAGE_MONGO_CLIENT = MONGO_CLIENT
            else:
                IMAGE_MONGO_CLIENT = MongoClient(
                    IMAGE_MONGO_URI, serverSelectionTimeoutMS=5000,
                    maxPoolSize=20, socketTimeoutMS=20000, retryWrites=False,
                )
                IMAGE_MONGO_CLIENT.admin.command('ping')
            IMAGE_MONGO_DB = IMAGE_MONGO_CLIENT[IMAGE_MONGO_DB_NAME]
            COL_IMAGE_CHARACTERS = IMAGE_MONGO_DB['anime_characters']
            # Backward-compatible name for code that only reads catalog images.
            COL_WAIFUS = COL_IMAGE_CHARACTERS
        except Exception as exc:
            COL_IMAGE_CHARACTERS = COL_WAIFUS = None
            logging.warning('⚠️ Image catalog database unavailable: %s', exc)
        
        # Ensure Indexes
        _ensure_character_identity_index()
        try:
            COL_ACTIVE_CHATS.create_index('chat_id', unique=True)
            COL_ACTIVE_USERS.create_index('user_id', unique=True)
            COL_LEADERBOARD.create_index('user_id', unique=True)
            COL_GROUP_LEADERBOARD.create_index([('chat_id', 1), ('user_id', 1)], unique=True)
            COL_GROUP_LEADERBOARD.create_index([('chat_id', 1), ('rating', -1)])
            COL_GROUP_LEADERBOARD.create_index([('chat_id', 1), ('wins', -1)])
            COL_MODS.create_index('user_id', unique=True)  # Unique index for mods
            COL_WAIFU_USERS.create_index('id', unique=True)
            COL_WAIFU_CREWS.create_index('name', unique=True)
            COL_GAME_HINTS.create_index([('character_key', 1), ('game_type', 1)], unique=True)
            COL_AUCTIONS.create_index([('status', 1), ('ends_at', 1)])
            COL_AUCTION_BIDS.create_index([('auction_id', 1), ('amount', -1)])
            COL_AUCTION_BIDS.create_index([('auction_id', 1), ('bidder_id', 1)])
            COL_AUCTION_CHARACTER_POOL.create_index('pool_id', unique=True)
            COL_AUCTION_CHARACTER_POOL.create_index([('enabled', 1), ('anime', 1)])
            COL_DRAFT_GAMES.create_index([('chat_id', 1), ('status', 1)])
            COL_DRAFT_GAMES.create_index('game_id', unique=True)
            COL_GROUP_ACTIVITY.create_index('chat_id', unique=True)
            COL_GROUP_ACTIVITY.create_index([('is_member', 1), ('public_username', 1), ('weekly_message_count', -1), ('daily_message_count', -1)])
            COL_GROUP_ACTIVITY_DAILY.create_index([('chat_id', 1), ('day', 1)], unique=True)
            COL_GROUP_ACTIVITY_DAILY.create_index([('day', 1), ('message_count', -1)])
            COL_GROUP_ACTIVITY_USERS.create_index([('chat_id', 1), ('user_id', 1)], unique=True)
            COL_GROUP_ACTIVITY_UPDATES.create_index([('chat_id', 1), ('message_id', 1)], unique=True)
            COL_GROUP_ACTIVITY_UPDATES.create_index('expires_at', expireAfterSeconds=0)
            COL_DELETION_REQUESTS.create_index('expires_at', expireAfterSeconds=0)
            COL_DELETION_REQUESTS.create_index('request_id', unique=True)
            COL_PRIVATE_USERS.create_index('user_id', unique=True)
        except: pass

        # Seed the checked-in auction-only CSV. It never modifies the legacy
        # character catalog; its image URLs were copied from that catalog.
        try:
            from auction_catalog import AUCTION_CHARACTER_POOL
            for character in AUCTION_CHARACTER_POOL:
                COL_AUCTION_CHARACTER_POOL.update_one(
                    {'pool_id': character['pool_id']}, {'$set': character}, upsert=True,
                )
        except Exception as exc:
            logging.exception('Could not seed auction character pool: %s', exc)

        USE_MONGO = True
        logging.info("✅ Connected to game MongoDB (%s); image catalog is read-only (%s).", MONGO_DB_NAME, IMAGE_MONGO_DB_NAME)
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
    """Get user stats from main or clone-specific database with fast in-memory caching."""
    import time
    default = {"name": name, "wins": 0, "matches": 0, "rating": 1200}
    cache_key = f"{get_clone_owner_id() or 'main'}_{user_id}"

    with _USER_STATS_CACHE_LOCK:
        if cache_key in _USER_STATS_CACHE:
            cached_data, timestamp = _USER_STATS_CACHE[cache_key]
            if time.time() - timestamp < _CACHE_TTL:
                return cached_data.copy()

    try:
        clone_db = get_clone_db()
        result = default
        if clone_db is not None:
            logging.debug(f"[DB] Getting user {user_id} stats from clone DB")
            col = clone_db['leaderboard']
            u = col.find_one({"user_id": user_id})
            result = u if u else default
        elif USE_MONGO:
            u = COL_LEADERBOARD.find_one({"user_id": user_id})
            result = u if u else default
        else:
            if os.path.exists(LEADERBOARD_FILE):
                with open(LEADERBOARD_FILE, "r") as f: data = json.load(f)
                result = data.get(str(user_id), default)

        with _USER_STATS_CACHE_LOCK:
            _USER_STATS_CACHE[cache_key] = (result.copy(), time.time())
        return result
    except Exception as e:
        logging.error(f"[DB] Error getting user stats for {user_id}: {e}")
        return default

def save_user_stats(user_id, stats):
    """Save user stats to main or clone-specific database and update cache."""
    import time
    cache_key = f"{get_clone_owner_id() or 'main'}_{user_id}"
    with _USER_STATS_CACHE_LOCK:
        _USER_STATS_CACHE[cache_key] = (stats.copy(), time.time())

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
            return list(COL_CHARACTERS.find({'archived': {'$ne': True}}))
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
                {"name": char_data["name"], "series": char_data.get("series", "Unknown")},
                {"$set": char_data},
                upsert=True
            )
            return True
        except:
            return False
    else:
        characters = load_characters()
        # Remove existing if any
        identity = (
            char_data["name"],
            char_data.get("series", "Unknown"),
        )
        characters = [
            c for c in characters
            if (c.get("name"), c.get("series", "Unknown")) != identity
        ]
        characters.append(char_data)
        try:
            with open(CHARACTERS_FILE, 'w') as f:
                json.dump(characters, f, indent=2)
            return True
        except:
            return False

# ==================== GROUP ACTIVITY FUNCTIONS ====================

def _utc_day(value=None):
    """Return the UTC date bucket used by group activity aggregates."""
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.strftime('%Y-%m-%d')


def record_group_message(chat, message_id, user_id, received_at=None):
    """Atomically record one group message without retaining its content.

    The lightweight update ledger is TTL-expired.  It makes retried Telegram
    updates harmless while the aggregate documents remain compact.
    """
    if not USE_MONGO or COL_GROUP_ACTIVITY is None or not user_id:
        return False

    received_at = received_at or datetime.now(timezone.utc)
    if received_at.tzinfo is None:
        received_at = received_at.replace(tzinfo=timezone.utc)
    chat_id = chat.id
    try:
        COL_GROUP_ACTIVITY_UPDATES.insert_one({
            'chat_id': chat_id,
            'message_id': message_id,
            'expires_at': received_at + timedelta(days=2),
        })
    except DuplicateKeyError:
        return False
    except Exception as exc:
        logging.warning('[GROUPS] Could not create duplicate-update guard for %s: %s', chat_id, exc)
        return False

    username = getattr(chat, 'username', None)
    # A Telegram username is the only link we can safely treat as public from
    # ordinary message updates. Invite links can be private and are never shown.
    public_link = f'https://t.me/{username}' if username else None
    base_update = {
        '$inc': {'total_messages': 1},
        '$set': {
            'title': getattr(chat, 'title', None) or 'Untitled group',
            'public_username': username,
            'public_link': public_link,
            'is_member': True,
            'last_activity_at': received_at,
            'last_membership_verified_at': received_at,
            'updated_at': received_at,
        },
        '$setOnInsert': {'chat_id': chat_id, 'created_at': received_at, 'unique_active_users': 0},
    }
    try:
        COL_GROUP_ACTIVITY.update_one({'chat_id': chat_id}, base_update, upsert=True)
        COL_GROUP_ACTIVITY_DAILY.update_one(
            {'chat_id': chat_id, 'day': _utc_day(received_at)},
            {'$inc': {'message_count': 1}, '$set': {'updated_at': received_at},
             '$setOnInsert': {'chat_id': chat_id, 'day': _utc_day(received_at)}},
            upsert=True,
        )
        active_user = COL_GROUP_ACTIVITY_USERS.update_one(
            {'chat_id': chat_id, 'user_id': user_id},
            {'$set': {'last_activity_at': received_at},
             '$setOnInsert': {'chat_id': chat_id, 'user_id': user_id, 'first_activity_at': received_at}},
            upsert=True,
        )
        if active_user.upserted_id is not None:
            COL_GROUP_ACTIVITY.update_one({'chat_id': chat_id}, {'$inc': {'unique_active_users': 1}})
        return True
    except Exception as exc:
        logging.exception('[GROUPS] Failed to aggregate message for group %s: %s', chat_id, exc)
        return False


def set_group_membership(chat_id, is_member, *, title=None, username=None):
    if not USE_MONGO or COL_GROUP_ACTIVITY is None:
        return
    now = datetime.now(timezone.utc)
    changes = {'is_member': bool(is_member), 'last_membership_verified_at': now, 'updated_at': now}
    if title is not None:
        changes['title'] = title
    if username is not None:
        changes['public_username'] = username
        changes['public_link'] = f'https://t.me/{username}' if username else None
    COL_GROUP_ACTIVITY.update_one({'chat_id': chat_id}, {'$set': changes}, upsert=False)


def refresh_group_activity_rollups():
    """Refresh denormalized daily/rolling-week counters for indexed ranking."""
    if not USE_MONGO or COL_GROUP_ACTIVITY is None:
        return 0
    today = datetime.now(timezone.utc).date()
    days = [(today - timedelta(days=offset)).isoformat() for offset in range(7)]
    pipeline = [
        {'$match': {'day': {'$in': days}}},
        {'$group': {
            '_id': '$chat_id',
            'weekly_message_count': {'$sum': '$message_count'},
            'daily_message_count': {'$sum': {'$cond': [{'$eq': ['$day', today.isoformat()]}, '$message_count', 0]}},
        }},
    ]
    try:
        operations = [
            UpdateOne({'chat_id': row['_id']}, {'$set': {
                'weekly_message_count': row['weekly_message_count'],
                'daily_message_count': row['daily_message_count'],
                'rollup_updated_at': datetime.now(timezone.utc),
            }})
            for row in COL_GROUP_ACTIVITY_DAILY.aggregate(pipeline)
        ]
        if operations:
            COL_GROUP_ACTIVITY.bulk_write(operations, ordered=False)
        return len(operations)
    except Exception as exc:
        logging.warning('[GROUPS] Rollup refresh failed: %s', exc)
        return 0


def group_activity_rollup_worker(interval_seconds=900):
    """Periodic rollup worker; daily source buckets remain the audit record."""
    while True:
        refresh_group_activity_rollups()
        time.sleep(interval_seconds)


def expire_stale_auctions():
    """Release abandoned auction slots so they never block a player forever."""
    if not USE_MONGO or COL_AUCTIONS is None:
        return 0
    now = datetime.now(timezone.utc)
    try:
        waiting_cutoff = now - timedelta(seconds=AUCTION_WAITING_TIMEOUT_SECONDS)
        active_cutoff = now - timedelta(seconds=AUCTION_STALE_TIMEOUT_SECONDS)
        waiting = COL_AUCTIONS.update_many(
            {'status': 'waiting', 'updated_at': {'$lt': waiting_cutoff}},
            {'$set': {'status': 'expired', 'phase': 'expired', 'expired_at': now, 'expiry_reason': 'waiting_timeout'}},
        ).modified_count
        abandoned = COL_AUCTIONS.update_many(
            {'status': {'$in': ['auction', 'battle']}, 'updated_at': {'$lt': active_cutoff}},
            {'$set': {'status': 'expired', 'phase': 'expired', 'expired_at': now, 'expiry_reason': 'inactivity_timeout'}},
        ).modified_count
        if waiting or abandoned:
            logging.info('[AUCTION] Expired %s waiting and %s inactive game(s).', waiting, abandoned)
        return waiting + abandoned
    except Exception as exc:
        logging.warning('[AUCTION] Expiry cleanup failed: %s', exc)
        return 0


def auction_expiry_worker(interval_seconds=60):
    while True:
        expire_stale_auctions()
        time.sleep(interval_seconds)


def get_group_activity_candidates(mode='weekly', limit=50):
    if not USE_MONGO or COL_GROUP_ACTIVITY is None:
        return []
    refresh_group_activity_rollups()
    fields = {
        'weekly': [('weekly_message_count', -1), ('daily_message_count', -1), ('last_activity_at', -1)],
        'today': [('daily_message_count', -1), ('weekly_message_count', -1), ('last_activity_at', -1)],
        'all': [('total_messages', -1), ('last_activity_at', -1)],
    }[mode]
    # A public username is a verified, non-private Telegram join route.
    query = {'is_member': True, 'public_username': {'$type': 'string', '$ne': ''}}
    return list(COL_GROUP_ACTIVITY.find(query).sort(fields).limit(limit))


def get_group_activity_stats():
    if not USE_MONGO or COL_GROUP_ACTIVITY is None:
        return None
    return {
        'tracked_groups': COL_GROUP_ACTIVITY.count_documents({}),
        'member_groups': COL_GROUP_ACTIVITY.count_documents({'is_member': True}),
        'public_groups': COL_GROUP_ACTIVITY.count_documents({'is_member': True, 'public_username': {'$type': 'string', '$ne': ''}}),
        'messages': sum(row.get('total_messages', 0) for row in COL_GROUP_ACTIVITY.find({}, {'total_messages': 1})),
        'active_users': sum(row.get('unique_active_users', 0) for row in COL_GROUP_ACTIVITY.find({}, {'unique_active_users': 1})),
    }


def mark_private_user(user_id):
    """Record that a user can receive bot DMs after an explicit private /start."""
    if not USE_MONGO or COL_PRIVATE_USERS is None:
        return
    COL_PRIVATE_USERS.update_one(
        {'user_id': user_id},
        {'$set': {'last_started_at': datetime.now(timezone.utc)}, '$setOnInsert': {'user_id': user_id}},
        upsert=True,
    )


def has_private_start(user_id):
    if not USE_MONGO or COL_PRIVATE_USERS is None:
        return False
    return COL_PRIVATE_USERS.find_one({'user_id': user_id}, {'_id': 1}) is not None

# ==================== UTILITY FUNCTIONS ====================

def close_connection():
    """Close MongoDB connection"""
    global MONGO_CLIENT
    if MONGO_CLIENT:
        MONGO_CLIENT.close()
        logging.info("🔌 MongoDB connection closed.")
    if IMAGE_MONGO_CLIENT and IMAGE_MONGO_CLIENT is not MONGO_CLIENT:
        IMAGE_MONGO_CLIENT.close()
