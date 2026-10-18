import time
import html
from bson import ObjectId
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ParseMode

import database

_DISABLED_FILTER = filters.create(lambda _, __, ___: False)

@Client.on_message(_DISABLED_FILTER & filters.command("createcrew") & filters.group)
async def createcrew_command(client, message):
    user = message.from_user
    if not user:
        return
    if len(message.command) < 2:
        await message.reply_text("Usage: /createcrew [crew name]")
        return

    crew_name = " ".join(message.command[1:]).strip()
    if not crew_name or len(crew_name) > 30:
        await message.reply_text("❌ Crew name must be between 1 and 30 characters.")
        return

    if not database.USE_MONGO or database.COL_WAIFU_USERS is None or database.COL_WAIFU_CREWS is None:
        await message.reply_text("❌ Database not ready.")
        return

    user_doc = database.COL_WAIFU_USERS.find_one({"id": user.id})
    if user_doc and user_doc.get("crew_id"):
        await message.reply_text("❌ You are already in a crew! Leave or disband it first.")
        return

    existing_crew = database.COL_WAIFU_CREWS.find_one({"name": {"$regex": f"^{re.escape(crew_name)}$", "$options": "i"}}) if 're' in globals() else database.COL_WAIFU_CREWS.find_one({"name": crew_name})
    if existing_crew:
        await message.reply_text("❌ A crew with that name already exists.")
        return

    # Create crew doc
    crew_id = ObjectId()
    crew_doc = {
        "_id": crew_id,
        "name": crew_name,
        "captain_id": user.id,
        "captain_name": user.first_name,
        "members": [user.id],
        "created_at": time.time()
    }
    database.COL_WAIFU_CREWS.insert_one(crew_doc)

    # Update captain's user doc
    database.COL_WAIFU_USERS.update_one(
        {"id": user.id},
        {"$set": {"crew_id": crew_id}},
        upsert=True
    )

    await message.reply_text(
        f"🏴‍☠️ <b>{html.escape(user.first_name)}</b> has formed the pirate crew: <b>{html.escape(crew_name)}</b>!",
        parse_mode=ParseMode.HTML
    )

@Client.on_message(_DISABLED_FILTER & filters.command("joincrew") & filters.group)
async def joincrew_command(client, message):
    user = message.from_user
    if not user:
        return
    if len(message.command) < 2:
        await message.reply_text("Usage: /joincrew [crew name]")
        return

    crew_name = " ".join(message.command[1:]).strip()

    if not database.USE_MONGO or database.COL_WAIFU_USERS is None or database.COL_WAIFU_CREWS is None:
        await message.reply_text("❌ Database not ready.")
        return

    user_doc = database.COL_WAIFU_USERS.find_one({"id": user.id})
    if user_doc and user_doc.get("crew_id"):
        await message.reply_text("❌ You are already in a crew! Leave it first.")
        return

    crew = database.COL_WAIFU_CREWS.find_one({"name": {"$regex": f"^{crew_name}$", "$options": "i"}})
    if not crew:
        await message.reply_text("❌ Crew not found.")
        return

    # Add member
    database.COL_WAIFU_CREWS.update_one(
        {"_id": crew["_id"]},
        {"$push": {"members": user.id}}
    )
    database.COL_WAIFU_USERS.update_one(
        {"id": user.id},
        {"$set": {"crew_id": crew["_id"]}},
        upsert=True
    )

    await message.reply_text(
        f"⚓ <b>{html.escape(user.first_name)}</b> has joined the crew <b>{html.escape(crew['name'])}</b>!",
        parse_mode=ParseMode.HTML
    )

@Client.on_message(_DISABLED_FILTER & filters.command("disbandcrew") & filters.group)
async def disbandcrew_command(client, message):
    user = message.from_user
    if not user:
        return

    if not database.USE_MONGO or database.COL_WAIFU_USERS is None or database.COL_WAIFU_CREWS is None:
        await message.reply_text("❌ Database not ready.")
        return

    user_doc = database.COL_WAIFU_USERS.find_one({"id": user.id})
    if not user_doc or not user_doc.get("crew_id"):
        await message.reply_text("❌ You are not in any crew.")
        return

    crew = database.COL_WAIFU_CREWS.find_one({"_id": user_doc["crew_id"]})
    if not crew:
        await message.reply_text("❌ Crew not found.")
        return

    if crew["captain_id"] != user.id:
        await message.reply_text("❌ Only the captain can disband the crew!")
        return

    # Remove crew reference from all members
    database.COL_WAIFU_USERS.update_many(
        {"crew_id": crew["_id"]},
        {"$unset": {"crew_id": ""}}
    )

    # Delete crew
    database.COL_WAIFU_CREWS.delete_one({"_id": crew["_id"]})

    await message.reply_text(
        f"💥 Crew <b>{html.escape(crew['name'])}</b> has been disbanded by the Captain!",
        parse_mode=ParseMode.HTML
    )

@Client.on_message(_DISABLED_FILTER & filters.command(["crew", "crewinfo"]) & filters.group)
async def crew_command(client, message):
    user = message.from_user
    if not user:
        return

    if not database.USE_MONGO or database.COL_WAIFU_USERS is None or database.COL_WAIFU_CREWS is None:
        await message.reply_text("❌ Database not ready.")
        return

    crew_name = " ".join(message.command[1:]).strip()
    crew = None

    if crew_name:
        crew = database.COL_WAIFU_CREWS.find_one({"name": {"$regex": f"^{crew_name}$", "$options": "i"}})
    else:
        user_doc = database.COL_WAIFU_USERS.find_one({"id": user.id})
        if user_doc and user_doc.get("crew_id"):
            crew = database.COL_WAIFU_CREWS.find_one({"_id": user_doc["crew_id"]})

    if not crew:
        await message.reply_text("❌ Crew not found or you are not in a crew. Usage: /crew [crew name]")
        return

    import rich_ui
    members = list(database.COL_WAIFU_USERS.find({"id": {"$in": crew["members"]}}))
    bounties = [m.get("coins", 0) for m in members]
    avg_bounty = int(sum(bounties) / len(bounties)) if bounties else 0

    members_txt = "\n".join(f"• {m['first_name']} — {m.get('coins', 0):,} 🪙" for m in members)

    card = rich_ui.build_rich_card(
        title=f"🏴‍☠️ Crew: {crew['name']}",
        badges=["Crew", f"Captain: {crew['captain_name']}"],
        table_data=[
            ("Captain", crew["captain_name"]),
            ("Avg Bounty", f"{avg_bounty:,}"),
            ("Members", str(len(members)))
        ],
        expandable_quote=f"Members:\n{members_txt}"
    )

    await rich_ui.send_rich_message(client, message.chat.id, card)
