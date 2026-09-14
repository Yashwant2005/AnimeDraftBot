from flask import Flask, Response
import threading
import urllib.request
import time
import os
import json
import logging

PORT = int(os.environ.get("PORT", "8000"))
KEEPALIVE_URL = os.environ.get("KEEPALIVE_URL", f"http://localhost:{PORT}/")
web_app = Flask("anime_keepalive_app")
_bot_status_lock = threading.Lock()
_bot_status = {
    "ready": False,
    "last_started": None,
    "last_stopped": None,
    "last_error": None,
}

@web_app.route("/")
def index():
    return health()

def set_bot_status(ready, error=None):
    with _bot_status_lock:
        _bot_status["ready"] = ready
        key = "last_started" if ready else "last_stopped"
        _bot_status[key] = time.time()
        if error:
            _bot_status["last_error"] = str(error)

@web_app.route("/health")
def health():
    with _bot_status_lock:
        status = dict(_bot_status)
    status["status"] = "ok" if status["ready"] else "degraded"
    return Response(
        json.dumps(status),
        status=200 if status["ready"] else 503,
        mimetype="application/json"
    )

def _keepalive_loop():
    interval = max(60, int(os.environ.get("KEEPALIVE_INTERVAL", "300")))
    while True:
        try:
            with urllib.request.urlopen(KEEPALIVE_URL, timeout=10) as response:
                if response.status >= 500:
                    logging.warning("Keepalive request returned HTTP %s", response.status)
        except Exception as error:
            logging.warning("Keepalive request failed: %s", error)
        time.sleep(interval)

def start_server():
    t = threading.Thread(target=_keepalive_loop, daemon=True)
    t.start()
    web_thread = threading.Thread(target=lambda: web_app.run(host="0.0.0.0", port=PORT), daemon=True)
    web_thread.start()