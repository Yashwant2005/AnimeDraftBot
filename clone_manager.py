import threading
import logging
import asyncio
import time
import json
import os
from pyrogram import Client
import database
from config import API_ID, API_HASH

# Map owner_id -> (Client, thread_ref)
ACTIVE_CLONES = {}
LOCK = threading.RLock()

async def _keep_alive():
    """Simple async loop to keep the client alive without signal handlers."""
    try:
        while True:
            await asyncio.sleep(60)
    except asyncio.CancelledError:
        pass

async def _run_client_async(app: Client):
    """Async wrapper to start and keep client alive."""
    try:
        logging.info(f"[{app.name}] Starting client...")
        await app.start()
        logging.info(f"[{app.name}] Client started successfully")
        
        # Start keep-alive task
        keep_alive_task = asyncio.create_task(_keep_alive())
        setattr(app, '_keep_alive_task', keep_alive_task)
        
        # Block until cancelled
        await keep_alive_task
    except asyncio.CancelledError:
        logging.info(f"[{app.name}] Keep-alive cancelled")
    except Exception as e:
        logging.error(f"[{app.name}] Error during execution: {e}", exc_info=True)
    finally:
        try:
            logging.info(f"[{app.name}] Stopping client...")
            await app.stop()
            logging.info(f"[{app.name}] Client stopped")
        except Exception as e:
            logging.warning(f"[{app.name}] Error stopping: {e}", exc_info=True)

def _run_client(app: Client):
    """
    Run a cloned client in a background thread.
    - Creates a new event loop per thread (required for asyncio in bg thread)
    - Runs the async client in the loop
    """
    try:
        logging.info(f"[{app.name}] Thread starting...")
        # Each thread needs its own event loop for asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        setattr(app, '_loop', loop)
        setattr(app, '_keep_alive_task', None)

        try:
            logging.info(f"[{app.name}] Running async client...")
            loop.run_until_complete(_run_client_async(app))
        except Exception as e:
            logging.error(f"[{app.name}] Async execution error: {e}", exc_info=True)

    except Exception as e:
        logging.exception(f"[{app.name}] Thread error: {e}")
    finally:
        try:
            loop = getattr(app, '_loop', None)
            if loop and not loop.is_closed():
                try:
                    logging.info(f"[{app.name}] Closing event loop...")
                    loop.close()
                    logging.info(f"[{app.name}] Event loop closed")
                except Exception as e:
                    logging.warning(f"[{app.name}] Error closing loop: {e}")
        except Exception:
            pass
        logging.info(f"[{app.name}] Thread ended")

def start_clone(owner_id, bot_token):
    """
    Start a cloned bot client in a background thread.
    Each clone gets its own session file and MongoDB collections.
    Returns True if successfully started, False if error.
    """
    try:
        logging.info(f"[CLONE] Starting clone registration for owner {owner_id}...")
        with LOCK:
            # Stop existing clone if any
            if owner_id in ACTIVE_CLONES:
                logging.info(f"[CLONE] Stopping old clone for owner {owner_id}...")
                try:
                    stop_clone(owner_id)
                except Exception as e:
                    logging.warning(f"[CLONE] Error stopping old clone: {e}")
                time.sleep(0.5)  # Give it a moment to shut down

            # Create client with unique session name per clone
            session_name = f"clone_bot_{owner_id}"
            try:
                logging.info(f"[CLONE] Creating Pyrogram client for {session_name}...")
                client = Client(
                    session_name,
                    api_id=API_ID,
                    api_hash=API_HASH,
                    bot_token=bot_token,
                    plugins=dict(root="plugins")
                )
                # Mark as clone so handlers can check
                setattr(client, 'is_clone', True)
                setattr(client, 'clone_owner_id', owner_id)
                logging.info(f"[CLONE] Client created for owner {owner_id}")

                # remove any leftover chat/user records that may have been written
                # to the *main* database by earlier buggy versions.  a clean slate
                # ensures broadcasts will only target freshly saved chats.
                try:
                    if database.USE_MONGO:
                        clone_db = database.get_clone_db(owner_id=owner_id, client=client)
                        if clone_db is not None:
                            clone_db['active_chats'].delete_many({})
                            clone_db['active_users'].delete_many({})
                            logging.info(f"[CLONE] Cleared stale chat/user data for clone {owner_id}")
                except Exception as e:
                    logging.warning(f"[CLONE] Could not clear previous data for owner {owner_id}: {e}")
            except Exception as e:
                logging.error(f"[CLONE] Failed to create client: {e}", exc_info=True)
                return False

            # Start in background thread
            try:
                logging.info(f"[CLONE] Creating background thread for owner {owner_id}...")
                t = threading.Thread(target=_run_client, args=(client,), daemon=True, name=f"clone-{owner_id}")
                t.start()
                ACTIVE_CLONES[owner_id] = (client, t)
                logging.info(f"✅ [CLONE] Clone successfully started for owner {owner_id}")
                return True
            except Exception as e:
                logging.error(f"[CLONE] Failed to start thread: {e}", exc_info=True)
                return False
    except Exception as e:
        logging.exception(f"[CLONE] Unexpected error in start_clone: {e}")
        return False

def stop_clone(owner_id):
    """Stop a cloned bot client safely."""
    try:
        logging.info(f"[CLONE] Stopping clone for owner {owner_id}...")
        with LOCK:
            clone_data = ACTIVE_CLONES.pop(owner_id, None)
            if not clone_data:
                logging.info(f"[CLONE] No active clone for owner {owner_id}")
                return False

            client, thread_ref = clone_data
            loop = getattr(client, '_loop', None)
            if loop and not loop.is_closed():
                try:
                    # Cancel keep-alive task
                    task = getattr(client, '_keep_alive_task', None)
                    if task:
                        logging.info(f"[CLONE] Cancelling keep-alive task for owner {owner_id}...")
                        task.cancel()
                    
                    # Stop client via thread-safe coroutine
                    logging.info(f"[CLONE] Requesting client stop for owner {owner_id}...")
                    fut = asyncio.run_coroutine_threadsafe(client.stop(), loop)
                    try:
                        fut.result(timeout=5)
                        logging.info(f"[CLONE] Client stop completed for owner {owner_id}")
                    except asyncio.TimeoutError:
                        logging.warning(f"[CLONE] Timeout stopping clone {owner_id}")
                    except Exception as e:
                        logging.warning(f"[CLONE] Error stopping clone {owner_id}: {e}")
                except Exception as e:
                    logging.warning(f"[CLONE] Error in stop_clone: {e}")
            logging.info(f"✅ [CLONE] Stop request sent for owner {owner_id}")
            return True
    except Exception as e:
        logging.exception(f"[CLONE] Unexpected error in stop_clone: {e}")
        return False

def clear_clone_runtime_state(owner_id):
    """Clear stale clone status counters without deleting persistent clone data."""
    try:
        if database.USE_MONGO:
            clone_db = database.get_clone_db(owner_id=owner_id)
            if clone_db is not None:
                clone_db['active_chats'].delete_many({})
                clone_db['active_users'].delete_many({})
                logging.info(f"[CLONE] Cleared runtime stats for saved clone {owner_id}")
                return True
            return False

        cleared = False
        for filename in (database.CHATS_FILE, database.USERS_FILE):
            base, ext = os.path.splitext(filename)
            clone_filename = f"{base}.clone_{owner_id}{ext}"
            if os.path.exists(clone_filename):
                with open(clone_filename, "w", encoding="utf-8") as f:
                    json.dump([], f)
                cleared = True
        if cleared:
            logging.info(f"[CLONE] Cleared file runtime stats for saved clone {owner_id}")
        return cleared
    except Exception as e:
        logging.warning(f"[CLONE] Could not clear runtime stats for owner {owner_id}: {e}")
        return False

def clear_all_clone_runtime_state():
    """Clear stale status counters for saved clones without starting them."""
    try:
        mods = database.get_all_mods()
        cleared = 0
        for mod in mods:
            owner_id = mod.get("user_id")
            if owner_id and clear_clone_runtime_state(owner_id):
                cleared += 1
        logging.info(f"[CLONE] Startup cleared runtime stats for {cleared}/{len(mods)} saved clones; auto-start skipped.")
        return cleared
    except Exception as e:
        logging.exception(f"[CLONE] Error clearing saved clone runtime stats: {e}")
        return 0

def start_all_clones():
    """Startup clone restore is disabled; clones must be started explicitly."""
    logging.info("[CLONE] Auto-start disabled; registered clones were not initialized.")
    return 0
