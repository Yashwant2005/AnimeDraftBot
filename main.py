import logging
import threading
import sys
import os
import time
from pyrogram import Client
from pyrogram import idle
from config import API_ID, API_HASH, BOT_TOKEN
from database import init_mongo, load_admin_ids
from data_loader import load_data, load_pokemon_data, load_achievements, load_marvel_data
import web_server
import game_engine
import global_state

# --- FIX FOR WINDOWS CONSOLE CRASHES ---
# This line prevents the "UnicodeEncodeError: 'charmap' codec" error
# by forcing the terminal to accept Emojis (✅, ❌, etc.)
try:
    if sys.stdout.encoding != 'utf-8':
        sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

# --- LOGGING SETUP ---
# We use specific handlers to ensure the log file is also UTF-8
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)

def main():
    # 1. Initialize Database and Data
    logging.info("Initializing Database and Data...")
    init_mongo()
    load_data()
    load_pokemon_data()
    load_marvel_data()
    load_admin_ids()
    load_achievements()

    # 2. Start Web Server (for Keepalive/Render)
    logging.info("Starting Web Server...")
    web_server.start_server()

    # 3. Start Background Threads
    logging.info("Starting Background Threads...")
    
    # Thread to clean up finished games from memory
    t_cleanup = threading.Thread(target=game_engine.cleanup_old_games, daemon=True)
    t_cleanup.start()
    
    # Thread to monitor AFK players (Forfeit logic)
    # We pass None because the client isn't fully started yet, 
    # the engine handles the logging.
    t_monitor = threading.Thread(target=game_engine.forfeit_monitor, args=(None,), daemon=True)
    t_monitor.start()

    try:
        import clone_manager
        clone_manager.clear_all_clone_runtime_state()
    except Exception as e:
        logging.warning(f"[CLONE] Could not clear saved clone runtime stats on restart: {e}")
    logging.info("[CLONE] Startup auto-initialization disabled; saved clones will not be restored on restart.")

    restart_delay = max(5, int(os.environ.get("BOT_RESTART_DELAY", "15")))
    while True:
        app = Client(
            "anime_bot",
            api_id=API_ID,
            api_hash=API_HASH,
            bot_token=BOT_TOKEN,
            plugins=dict(root="plugins")
        )
        try:
            logging.info("Starting Pyrogram Client...")
            app.start()
            web_server.set_bot_status(True)
            logging.info("Telegram bot is online and polling updates.")
            idle()
        except Exception as error:
            web_server.set_bot_status(False, error)
            logging.exception("Telegram client stopped unexpectedly; restarting in %ss", restart_delay)
        finally:
            web_server.set_bot_status(False)
            try:
                if app.is_connected:
                    app.stop()
            except Exception:
                logging.warning("Telegram client cleanup skipped: already terminated")

        logging.warning("Telegram client stopped; restarting in %ss", restart_delay)
        time.sleep(restart_delay)

if __name__ == "__main__":
    main()
