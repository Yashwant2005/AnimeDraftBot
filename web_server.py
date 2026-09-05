from flask import Flask, Response
import threading
import urllib.request
import time
import os

PORT = int(os.environ.get("PORT", "8000"))
KEEPALIVE_URL = os.environ.get("KEEPALIVE_URL", f"http://localhost:{PORT}/")
web_app = Flask("anime_keepalive_app")

@web_app.route("/")
def index(): return Response("OK", status=200)

def _keepalive_loop():
    while True:
        try:
            with urllib.request.urlopen(KEEPALIVE_URL, timeout=10): pass
        except: pass
        time.sleep(600)

def start_server():
    t = threading.Thread(target=_keepalive_loop, daemon=True)
    t.start()
    web_thread = threading.Thread(target=lambda: web_app.run(host="0.0.0.0", port=PORT), daemon=True)
    web_thread.start()