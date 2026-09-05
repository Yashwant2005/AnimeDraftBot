import random
import time
import html
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.enums import ParseMode

import database

_rps_games = {}
_docq_states = {}
_roulette_games = {}

@Client.on_message(filters.command("docq") & filters.group)
async def docq_command(client, message):
    user = message.from_user
    if not user:
        return

    # Check cooldown or database
    if not database.USE_MONGO or database.COL_WAIFU_USERS is None:
        await message.reply_text("❌ Database not ready.")
        return

    # Doc Q game: pick one of two apples
    did = f"dq_{user.id}_{int(time.time())}"
    good_apple = random.randint(1, 2)
    _docq_states[did] = {
        "user_id": user.id,
        "good": good_apple
    }

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🍎 Apple 1", callback_data=f"docq:{did}:1"),
            InlineKeyboardButton("🍎 Apple 2", callback_data=f"docq:{did}:2")
        ]
    ])

    await message.reply_text(
        f"🍎 <b>Doc Q</b> appears and offers you two apples!\n"
        f"One of them is explosive, the other is normal. Choose wisely!",
        reply_markup=kb,
        parse_mode=ParseMode.HTML
    )

@Client.on_callback_query(filters.regex(r"^docq:"))
async def docq_callback(client, callback_query: CallbackQuery):
    _, did, pick_str = callback_query.data.split(":")
    pick = int(pick_str)

    state = _docq_states.get(did)
    if not state:
        await callback_query.answer("⌛ Game expired!", show_alert=True)
        return

    if callback_query.from_user.id != state["user_id"]:
        await callback_query.answer("❌ This apple offer is not for you!", show_alert=True)
        return

    _docq_states.pop(did, None)
    await callback_query.answer()
    uid = state["user_id"]
    is_win = (pick == state["good"])

    if is_win:
        reward = 150
        database.COL_WAIFU_USERS.update_one({"id": uid}, {"$inc": {"coins": reward, "bounty": reward}})
        await callback_query.edit_message_text(
            f"🍎 <b>Doc Q:</b> \"Fwahaha! You chose... wisely.\"\n\n"
            f"😋 The apple was delicious! You feel energized.\n"
            f"💰 You won: <b>+{reward} coins</b> 🪙",
            parse_mode=ParseMode.HTML
        )
    else:
        penalty = 75
        # Deduct but prevent going below zero
        doc = database.COL_WAIFU_USERS.find_one({"id": uid})
        current = doc.get("coins", 0) if doc else 0
        actual_penalty = min(penalty, current)
        database.COL_WAIFU_USERS.update_one({"id": uid}, {"$inc": {"coins": -actual_penalty, "bounty": -actual_penalty}})
        await callback_query.edit_message_text(
            f"💥 <b>BOOM!</b>\n\n"
            f"🤮 The apple was filled with explosives and blew up in your face!\n"
            f"💸 You lost: <b>-{actual_penalty} coins</b> 🪙",
            parse_mode=ParseMode.HTML
        )

@Client.on_message(filters.command("roulette") & filters.group)
async def roulette_command(client, message):
    challenger = message.from_user
    opponent = message.reply_to_message.from_user if message.reply_to_message else None
    if not challenger:
        return
    if not opponent:
        await message.reply_text("Reply to a user to challenge them to Russian Roulette!\nUsage: /roulette [wager]")
        return
    if challenger.id == opponent.id:
        await message.reply_text("❌ You cannot challenge yourself!")
        return
    if opponent.is_bot:
        await message.reply_text("❌ You cannot challenge a bot!")
        return

    if not database.USE_MONGO or database.COL_WAIFU_USERS is None:
        await message.reply_text("❌ Database not ready.")
        return

    wager = 100
    if len(message.command) > 1:
        try:
            wager = max(1, int(message.command[1].replace(",", "")))
        except ValueError:
            await message.reply_text("Usage: /roulette [wager]")
            return

    challenger_doc = database.COL_WAIFU_USERS.find_one({"id": challenger.id})
    opponent_doc = database.COL_WAIFU_USERS.find_one({"id": opponent.id})
    challenger_coins = challenger_doc.get("coins", 0) if challenger_doc else 0
    opponent_coins = opponent_doc.get("coins", 0) if opponent_doc else 0

    if challenger_coins < wager:
        await message.reply_text(f"❌ You need at least {wager:,} coins to play Russian Roulette!")
        return
    if opponent_coins < wager:
        await message.reply_text(f"❌ {html.escape(opponent.first_name)} needs at least {wager:,} coins to accept this wager.")
        return

    for gid, state in list(_roulette_games.items()):
        if time.time() - state.get("created_at", 0) > 900:
            _roulette_games.pop(gid, None)
        elif state.get("chat_id") == message.chat.id and (
            challenger.id in state.get("players", {}) or opponent.id in state.get("players", {})
        ):
            await message.reply_text("❌ One of these players already has an active roulette game.")
            return

    gid = f"rr_{challenger.id}_{opponent.id}_{int(time.time())}_{random.randint(100, 999)}"
    live_chambers = [1, 2, 3, 4, 6, 7, 8, 9]
    _roulette_games[gid] = {
        "chat_id": message.chat.id,
        "players": {
            challenger.id: challenger.first_name,
            opponent.id: opponent.first_name,
        },
        "p1": challenger.id,
        "p2": opponent.id,
        "turn_id": challenger.id,
        "wager": wager,
        "bullet": random.choice(live_chambers),
        "opened": [],
        "created_at": time.time(),
    }

    await message.reply_text(
        _roulette_text(_roulette_games[gid]),
        reply_markup=_roulette_markup(gid),
        parse_mode=ParseMode.HTML
    )

def _roulette_text(state, result=None, finished=False):
    p1 = _user_link(state["p1"], state["players"].get(state["p1"]))
    p2 = _user_link(state["p2"], state["players"].get(state["p2"]))
    turn = _user_link(state["turn_id"], state["players"].get(state["turn_id"]))
    text = (
        "🔫 <b>Russian Roulette</b>\n\n"
        "<b>Description:</b> Take turns choosing chambers. Hit the bullet and you are out.\n\n"
        f"{p1} vs {p2}\n"
        f"Wager: <b>฿{state['wager']:,}</b>"
    )
    if finished:
        text += "\nStatus: <b>Finished</b>"
    else:
        text += f"\nTurn: {turn}"
    opened = len(state.get("opened", []))
    if opened:
        text += f"\nSafe chambers opened: <b>{opened}</b>/7"
    if result:
        text += f"\n\n{result}"
    return text

def _user_link(user_id, name):
    return f"<a href='tg://user?id={user_id}'>{html.escape(str(name or user_id))}</a>"

def _roulette_markup(gid, picked=None, bullet=None):
    state = _roulette_games.get(gid)
    opened = set(state.get("opened", [])) if state else set()
    rows = []
    for start in (1, 4, 7):
        row = []
        for chamber in range(start, start + 3):
            if chamber == 5:
                label = "⚪"
            elif bullet == chamber:
                label = "💥"
            elif picked == chamber or chamber in opened:
                label = "⭕"
            else:
                label = "🔴"
            row.append(InlineKeyboardButton(label, callback_data=f"rr:{gid}:{chamber}"))
        rows.append(row)
    return InlineKeyboardMarkup(rows)

@Client.on_callback_query(filters.regex(r"^rr:"))
async def roulette_callback(client, callback_query: CallbackQuery):
    try:
        _, gid, chamber_raw = callback_query.data.split(":")
        chamber = int(chamber_raw)
    except ValueError:
        return await callback_query.answer("Invalid chamber.", show_alert=True)

    state = _roulette_games.get(gid)
    if not state:
        return await callback_query.answer("⌛ Roulette expired!", show_alert=True)

    if chamber == 5:
        return await callback_query.answer("The middle chamber is locked.", show_alert=True)

    user_id = callback_query.from_user.id
    if user_id not in state["players"]:
        return await callback_query.answer("❌ This roulette is not yours.", show_alert=True)
    if user_id != state["turn_id"]:
        turn_name = state["players"].get(state["turn_id"], "the other player")
        return await callback_query.answer(f"It is {turn_name}'s turn.", show_alert=True)
    if chamber in state.get("opened", []):
        return await callback_query.answer("That chamber is already open.", show_alert=True)

    wager = int(state["wager"])
    bullet = int(state["bullet"])

    if chamber == bullet:
        loser_id = user_id
        winner_id = state["p2"] if loser_id == state["p1"] else state["p1"]
        loser_name = state["players"].get(loser_id, loser_id)
        winner_name = state["players"].get(winner_id, winner_id)
        markup = _roulette_markup(gid, picked=chamber, bullet=bullet)
        _roulette_games.pop(gid, None)
        database.COL_WAIFU_USERS.update_one(
            {"id": loser_id},
            {"$inc": {"coins": -wager, "bounty": -wager}}
        )
        database.COL_WAIFU_USERS.update_one(
            {"id": winner_id},
            {"$inc": {"coins": wager, "bounty": wager}},
            upsert=True
        )
        result = (
            f"💥 <b>BANG!</b>\n"
            f"💀 {html.escape(str(loser_name))} hit the bullet and is out.\n"
            f"🏆 {_user_link(winner_id, winner_name)} won <b>{wager:,}</b> coins."
        )
        await callback_query.answer()
        await callback_query.edit_message_text(
            _roulette_text(state, result, finished=True),
            reply_markup=markup,
            parse_mode=ParseMode.HTML
        )
        return

    state.setdefault("opened", []).append(chamber)
    state["turn_id"] = state["p2"] if user_id == state["p1"] else state["p1"]
    state["created_at"] = time.time()
    result = (
        f"🌀 <b>Click!</b>\n"
        f"{_user_link(user_id, state['players'].get(user_id))} survived. "
        f"Now {_user_link(state['turn_id'], state['players'].get(state['turn_id']))} must choose."
    )
    await callback_query.answer()
    await callback_query.edit_message_text(
        _roulette_text(state, result),
        reply_markup=_roulette_markup(gid, picked=chamber),
        parse_mode=ParseMode.HTML
    )

@Client.on_message(filters.command("rps") & filters.group)
async def rps_command(client, message):
    if not message.reply_to_message:
        await message.reply_text("Reply to a user to challenge them to Rock Paper Scissors!\nUsage: /rps (reply)")
        return

    a = message.from_user
    b = message.reply_to_message.from_user
    if not a or not b:
        return

    if a.id == b.id:
        await message.reply_text("❌ You cannot challenge yourself!")
        return
    if b.is_bot:
        await message.reply_text("❌ You cannot challenge a bot!")
        return

    gid = f"rps_{a.id}_{b.id}_{int(time.time())}"
    _rps_games[gid] = {
        "a_id": a.id, "a_name": a.first_name, "a_pick": None,
        "b_id": b.id, "b_name": b.first_name, "b_pick": None,
    }

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✊ Rock", callback_data=f"rps:{gid}:rock"),
            InlineKeyboardButton("✋ Paper", callback_data=f"rps:{gid}:paper"),
            InlineKeyboardButton("✌️ Scissors", callback_data=f"rps:{gid}:scissors")
        ]
    ])

    await message.reply_text(
        f"✊✋✌️ <b>Rock Paper Scissors Challenge!</b>\n\n"
        f"<a href='tg://user?id={a.id}'>{html.escape(a.first_name)}</a> challenges "
        f"<a href='tg://user?id={b.id}'>{html.escape(b.first_name)}</a>.\n"
        f"Both players, choose your move below!",
        reply_markup=kb,
        parse_mode=ParseMode.HTML
    )

@Client.on_callback_query(filters.regex(r"^rps:"))
async def rps_callback(client, callback_query: CallbackQuery):
    _, gid, move = callback_query.data.split(":")
    state = _rps_games.get(gid)

    if not state:
        await callback_query.answer("⌛ Game expired!", show_alert=True)
        return

    uid = callback_query.from_user.id
    if uid == state["a_id"]:
        if state["a_pick"]:
            await callback_query.answer("You already picked!", show_alert=True)
            return
        state["a_pick"] = move
        await callback_query.answer("Choice recorded!", show_alert=False)
    elif uid == state["b_id"]:
        if state["b_pick"]:
            await callback_query.answer("You already picked!", show_alert=True)
            return
        state["b_pick"] = move
        await callback_query.answer("Choice recorded!", show_alert=False)
    else:
        await callback_query.answer("❌ Not your game!", show_alert=True)
        return

    # Check if both have picked
    if state["a_pick"] and state["b_pick"]:
        _rps_games.pop(gid, None)
        move_a = state["a_pick"]
        move_b = state["b_pick"]

        # RPS Game Logic
        winner_id = None
        if move_a == move_b:
            result_txt = "It's a Tie! Both players keep their coins."
        else:
            wins_over = {"rock": "scissors", "paper": "rock", "scissors": "paper"}
            if wins_over[move_a] == move_b:
                winner_id = state["a_id"]
                winner_name = state["a_name"]
            else:
                winner_id = state["b_id"]
                winner_name = state["b_name"]
            
            result_txt = f"🏆 <b>{html.escape(winner_name)}</b> won! (+50 coins 🪙)"
            if database.USE_MONGO and database.COL_WAIFU_USERS is not None:
                database.COL_WAIFU_USERS.update_one({"id": winner_id}, {"$inc": {"coins": 50, "bounty": 50}})

        moves_display = {
            "rock": "✊ Rock",
            "paper": "✋ Paper",
            "scissors": "✌️ Scissors"
        }

        await callback_query.edit_message_text(
            f"✊✋✌️ <b>RPS Battle Results!</b>\n\n"
            f"• {html.escape(state['a_name'])} chose: <b>{moves_display[move_a]}</b>\n"
            f"• {html.escape(state['b_name'])} chose: <b>{moves_display[move_b]}</b>\n\n"
            f"{result_txt}",
            parse_mode=ParseMode.HTML
        )
