import html
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.enums import ParseMode

import database
from utils import is_admin

_predictions = {} # chat_id -> prediction dict
_DISABLED_FILTER = filters.create(lambda _, __, ___: False)

@Client.on_message(_DISABLED_FILTER & filters.command("prediction") & filters.group)
async def prediction_command(client, message):
    if len(message.command) < 2:
        await message.reply_text("Usage: /prediction [question]\nExample: /prediction Will Luffy defeat Kaido?")
        return

    chat_id = message.chat.id
    if chat_id in _predictions:
        await message.reply_text("❌ An active prediction is already running in this group! Resolve or cancel it first.")
        return

    question = " ".join(message.command[1:]).strip()
    _predictions[chat_id] = {
        "question": question,
        "creator_id": message.from_user.id,
        "creator_name": message.from_user.first_name,
        "bets": {"yes": {}, "no": {}} # user_id -> wager
    }

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👍 Yes", callback_data=f"pred_bet:{chat_id}:yes"),
            InlineKeyboardButton("👎 No", callback_data=f"pred_bet:{chat_id}:no")
        ]
    ])

    await message.reply_text(
        f"🔮 <b>New Prediction Event!</b>\n\n"
        f"🙋‍♀️ <b>Question:</b> \"{html.escape(question)}\"\n\n"
        f"💰 <i>Use /bet [yes/no] [amount] to place a bet, or click the buttons below to wager 100 coins!</i>",
        reply_markup=kb,
        parse_mode=ParseMode.HTML
    )

@Client.on_message(_DISABLED_FILTER & filters.command("bet") & filters.group)
async def bet_command(client, message):
    chat_id = message.chat.id
    if chat_id not in _predictions:
        await message.reply_text("❌ There is no active prediction running in this chat.")
        return

    if len(message.command) < 3:
        await message.reply_text("Usage: /bet [yes/no] [amount]\nExample: /bet yes 500")
        return

    option = message.command[1].strip().lower()
    amount_str = message.command[2].strip()

    if option not in ("yes", "no"):
        await message.reply_text("❌ Option must be either 'yes' or 'no'.")
        return
    if not amount_str.isdigit() or int(amount_str) <= 0:
        await message.reply_text("❌ Bet amount must be a positive number.")
        return
    amount = int(amount_str)

    user = message.from_user
    if not user:
        return

    if not database.USE_MONGO or database.COL_WAIFU_USERS is None:
        await message.reply_text("❌ Database not ready.")
        return

    # Check user coins
    doc = database.COL_WAIFU_USERS.find_one({"id": user.id})
    coins = doc.get("coins", 0) if doc else 0

    if coins < amount:
        await message.reply_text(f"❌ You don't have enough coins! Current balance: <b>{coins:,} coins</b>.")
        return

    pred = _predictions[chat_id]
    
    # Deduct coins
    database.COL_WAIFU_USERS.update_one({"id": user.id}, {"$inc": {"coins": -amount, "bounty": -amount}})

    # Record bet
    pred["bets"][option][user.id] = pred["bets"][option].get(user.id, 0) + amount

    await message.reply_text(
        f"✅ <a href='tg://user?id={user.id}'>{html.escape(user.first_name)}</a> bet <b>{amount:,} coins</b> on **{option.upper()}**!",
        parse_mode=ParseMode.HTML
    )

@Client.on_callback_query(_DISABLED_FILTER & filters.regex(r"^pred_bet:"))
async def pred_bet_callback(client, callback_query: CallbackQuery):
    _, chat_id_str, option = callback_query.data.split(":")
    chat_id = int(chat_id_str)

    if chat_id not in _predictions:
        await callback_query.answer("❌ Prediction expired or ended.", show_alert=True)
        return

    user = callback_query.from_user
    if not database.USE_MONGO or database.COL_WAIFU_USERS is None:
        return

    # Button click bets fixed 100 coins
    amount = 100
    doc = database.COL_WAIFU_USERS.find_one({"id": user.id})
    coins = doc.get("coins", 0) if doc else 0

    if coins < amount:
        await callback_query.answer("❌ Not enough coins! You need 100 coins to quick-bet.", show_alert=True)
        return

    # Deduct and bet
    database.COL_WAIFU_USERS.update_one({"id": user.id}, {"$inc": {"coins": -amount, "bounty": -amount}})
    pred = _predictions[chat_id]
    pred["bets"][option][user.id] = pred["bets"][option].get(user.id, 0) + amount

    await callback_query.answer(f"👍 Placed 100 coin bet on {option.upper()}!", show_alert=False)
    await callback_query.message.reply_text(
        f"✅ <a href='tg://user?id={user.id}'>{html.escape(user.first_name)}</a> quick-bet <b>100 coins</b> on **{option.upper()}**!",
        parse_mode=ParseMode.HTML
    )

@Client.on_message(_DISABLED_FILTER & filters.command("resolveprediction") & filters.group)
async def resolveprediction_command(client, message):
    chat_id = message.chat.id
    if chat_id not in _predictions:
        await message.reply_text("❌ No active prediction to resolve.")
        return

    pred = _predictions[chat_id]
    user_id = message.from_user.id if message.from_user else 0

    # Only creator or chat admin can resolve
    is_authorized = (user_id == pred["creator_id"]) or await is_admin(user_id)
    if not is_authorized:
        await message.reply_text("❌ Only the creator of the prediction or an admin can resolve it.")
        return

    if len(message.command) < 2:
        await message.reply_text("Usage: /resolveprediction [yes/no]")
        return

    winning_option = message.command[1].strip().lower()
    if winning_option not in ("yes", "no"):
        await message.reply_text("❌ Winner must be 'yes' or 'no'.")
        return

    # Resolve bets
    _predictions.pop(chat_id)
    yes_bets = pred["bets"]["yes"]
    no_bets = pred["bets"]["no"]

    total_pool = sum(yes_bets.values()) + sum(no_bets.values())
    winning_bets = yes_bets if winning_option == "yes" else no_bets
    losing_bets = no_bets if winning_option == "yes" else yes_bets

    if total_pool == 0:
        await message.reply_text(f"🏁 Prediction resolved as **{winning_option.upper()}**.\nNo wagers were placed.")
        return

    total_winners_wager = sum(winning_bets.values())
    if total_winners_wager == 0:
        await message.reply_text(
            f"🏁 Prediction resolved as **{winning_option.upper()}**.\n"
            f"Nobody bet on the winning side. The total pool of **{total_pool:,} coins** goes to the house!"
        )
        return

    # Distribute pool proportionally
    dist_txt = []
    for winner_uid, wager in winning_bets.items():
        share = int((wager / total_winners_wager) * total_pool)
        database.COL_WAIFU_USERS.update_one({"id": winner_uid}, {"$inc": {"coins": share, "bounty": share}})
        
        # Try getting member name
        try:
            member = await client.get_chat_member(chat_id, winner_uid)
            name = member.user.first_name
        except Exception:
            name = f"User {winner_uid}"
        dist_txt.append(f"• {html.escape(name)}: won <b>{share:,} coins</b> 🪙 (wagered {wager:,})")

    payout_report = "\n".join(dist_txt)
    await message.reply_text(
        f"🏁 <b>Prediction Resolved!</b>\n"
        f"🙋‍♀️ <b>Question:</b> \"{html.escape(pred['question'])}\"\n"
        f"✅ <b>Winning Side:</b> **{winning_option.upper()}**\n"
        f"💰 <b>Total Pool:</b> {total_pool:,} coins\n\n"
        f"🏆 <b>Payouts:</b>\n{payout_report}",
        parse_mode=ParseMode.HTML
    )

@Client.on_message(_DISABLED_FILTER & filters.command("cancelprediction") & filters.group)
async def cancelprediction_command(client, message):
    chat_id = message.chat.id
    if chat_id not in _predictions:
        await message.reply_text("❌ No active prediction to cancel.")
        return

    pred = _predictions[chat_id]
    user_id = message.from_user.id if message.from_user else 0

    is_authorized = (user_id == pred["creator_id"]) or await is_admin(user_id)
    if not is_authorized:
        await message.reply_text("❌ Only the creator of the prediction or an admin can cancel it.")
        return

    _predictions.pop(chat_id)

    # Refund bets
    for option in ("yes", "no"):
        for uid, wager in pred["bets"][option].items():
            database.COL_WAIFU_USERS.update_one({"id": uid}, {"$inc": {"coins": wager, "bounty": wager}})

    await message.reply_text("❌ **Prediction Event Cancelled.** All wagers have been refunded.")
