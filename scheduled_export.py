import http.client
import json
import logging
import mimetypes
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

from config import BOT_TOKEN, MONGO_DB_NAME, MONGO_URI
from mongo_exporter import export_mongo_documents, zip_export


DEFAULT_TARGET_CHAT = os.getenv("EXPORT_TARGET_CHAT", "@bakflirtervps")
DEFAULT_TIMEZONE = os.getenv("EXPORT_TIMEZONE", "Asia/Kolkata")
DEFAULT_HOUR = int(os.getenv("EXPORT_DAILY_HOUR", "19"))
DEFAULT_MINUTE = int(os.getenv("EXPORT_DAILY_MINUTE", "0"))

_scheduler_thread = None
_stop_event = threading.Event()


def _get_timezone(timezone_name):
    if ZoneInfo is None:
        return None
    try:
        return ZoneInfo(timezone_name)
    except Exception:
        logging.warning("[EXPORT] Unknown timezone %s; using system local time", timezone_name)
        return None


def seconds_until_next_run(hour=DEFAULT_HOUR, minute=DEFAULT_MINUTE, timezone_name=DEFAULT_TIMEZONE):
    tz = _get_timezone(timezone_name)
    now = datetime.now(tz).replace(second=0, microsecond=0)
    run_at = now.replace(hour=hour, minute=minute)
    if run_at <= now:
        run_at += timedelta(days=1)
    return max(1, int((run_at - now).total_seconds())), run_at


def _encode_multipart(fields, files):
    boundary = f"----anime-draft-export-{int(time.time() * 1000)}"
    chunks = []

    for name, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        chunks.append(str(value).encode("utf-8"))
        chunks.append(b"\r\n")

    for name, file_path in files.items():
        path = Path(file_path)
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(
            (
                f'Content-Disposition: form-data; name="{name}"; '
                f'filename="{path.name}"\r\n'
                f"Content-Type: {mime_type}\r\n\r\n"
            ).encode("utf-8")
        )
        chunks.append(path.read_bytes())
        chunks.append(b"\r\n")

    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return boundary, b"".join(chunks)


def send_document_via_bot_api(bot_token, chat_id, document_path, caption):
    if not bot_token:
        raise ValueError("BOT_TOKEN is required for scheduled Telegram export.")

    boundary, body = _encode_multipart(
        fields={"chat_id": chat_id, "caption": caption},
        files={"document": document_path},
    )
    headers = {
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Content-Length": str(len(body)),
    }

    connection = http.client.HTTPSConnection("api.telegram.org", timeout=120)
    try:
        connection.request("POST", f"/bot{bot_token}/sendDocument", body=body, headers=headers)
        response = connection.getresponse()
        response_body = response.read().decode("utf-8", errors="replace")
        if response.status >= 400:
            raise RuntimeError(f"Telegram API returned {response.status}: {response_body}")

        payload = json.loads(response_body)
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram API error: {response_body}")
        return payload
    finally:
        connection.close()


def run_export_once(target_chat=DEFAULT_TARGET_CHAT):
    export_dir, summary = export_mongo_documents(
        mongo_uri=MONGO_URI,
        output_root=None,
        database_name=MONGO_DB_NAME,
        all_databases=False,
        include_system_databases=False,
    )
    archive = zip_export(export_dir)
    caption = (
        "Daily MongoDB export\n"
        f"Database: {MONGO_DB_NAME}\n"
        f"Documents: {summary['total_documents']}\n"
        f"Exported: {summary['exported_at']}"
    )
    send_document_via_bot_api(BOT_TOKEN, target_chat, archive, caption)
    return export_dir, archive, summary


def _scheduler_loop(target_chat, hour, minute, timezone_name):
    logging.info(
        "[EXPORT] Daily MongoDB export scheduler enabled for %02d:%02d %s -> %s",
        hour,
        minute,
        timezone_name,
        target_chat,
    )

    while not _stop_event.is_set():
        delay, next_run = seconds_until_next_run(hour, minute, timezone_name)
        logging.info("[EXPORT] Next scheduled export at %s", next_run.isoformat())

        if _stop_event.wait(delay):
            break

        try:
            export_dir, archive, summary = run_export_once(target_chat)
            logging.info(
                "[EXPORT] Sent daily export with %s document(s): %s",
                summary["total_documents"],
                archive,
            )
            logging.info("[EXPORT] Export folder kept at %s", export_dir)
        except Exception:
            logging.exception("[EXPORT] Scheduled MongoDB export failed")

        _stop_event.wait(60)


def start_daily_export_scheduler(
    target_chat=DEFAULT_TARGET_CHAT,
    hour=DEFAULT_HOUR,
    minute=DEFAULT_MINUTE,
    timezone_name=DEFAULT_TIMEZONE,
):
    global _scheduler_thread

    enabled = os.getenv("EXPORT_SCHEDULE_ENABLED", "true").strip().lower()
    if enabled in {"0", "false", "no", "off"}:
        logging.info("[EXPORT] Daily MongoDB export scheduler disabled")
        return None

    if _scheduler_thread and _scheduler_thread.is_alive():
        return _scheduler_thread

    _stop_event.clear()
    _scheduler_thread = threading.Thread(
        target=_scheduler_loop,
        args=(target_chat, hour, minute, timezone_name),
        name="daily-mongo-export",
        daemon=True,
    )
    _scheduler_thread.start()
    return _scheduler_thread


def stop_daily_export_scheduler():
    _stop_event.set()
