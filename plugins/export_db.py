import asyncio
import os

from pyrogram import Client, filters

from config import MONGO_DB_NAME, MONGO_URI
from mongo_exporter import export_mongo_documents, zip_export
from utils import is_owner


DEFAULT_EXPORT_TARGET = os.getenv("EXPORT_TARGET_CHAT", "@bakflirtervps")


def _parse_target(message):
    args = message.text.split(maxsplit=1)
    if len(args) == 1:
        return DEFAULT_EXPORT_TARGET

    target = args[1].strip()
    if target.lower() in {"here", "chat"}:
        return message.chat.id
    if target.startswith("https://t.me/"):
        target = "@" + target.rstrip("/").split("/")[-1]
    return target


@Client.on_message(filters.command(["exportdb", "backupdb"]))
async def export_db_cmd(client, message):
    if not message.from_user or not is_owner(message.from_user.id):
        return await message.reply_text("Only the bot owner can export the database.")

    target_chat = _parse_target(message)
    status = await message.reply_text("Exporting MongoDB data...")

    try:
        export_dir, summary = await asyncio.to_thread(
            export_mongo_documents,
            MONGO_URI,
            None,
            MONGO_DB_NAME,
            False,
            False,
        )
        archive = await asyncio.to_thread(zip_export, export_dir)

        caption = (
            f"MongoDB export complete\n"
            f"Database: {MONGO_DB_NAME}\n"
            f"Documents: {summary['total_documents']}"
        )
        await client.send_document(
            chat_id=target_chat,
            document=str(archive),
            caption=caption,
        )
        await status.edit_text(
            f"Export sent to {target_chat}.\n"
            f"Local folder: {export_dir}",
        )
    except Exception as exc:
        await status.edit_text(f"Export failed: {exc}")
