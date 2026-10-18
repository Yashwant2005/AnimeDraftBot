import html
import asyncio
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ParseMode
from pyrogram.errors import FloodWait
import global_state
import rich_ui
from config import ROLES, POKEMON_ROLES, DEFAULT_POWER

async def ensure_display_message(client, chat_id, game, text, reply_markup=None, preview=False):
    disp = game.get('display_message')
    markup = rich_ui.format_rich_keyboard(reply_markup)
    
    if disp:
        ok = await rich_ui.edit_rich_message(
            client, chat_id=disp['chat_id'], message_id=disp['msg_id'],
            text=text, reply_markup=markup, preview=preview
        )
        if ok:
            return

    msg_id = await rich_ui.send_rich_message(
        client, chat_id=chat_id, text=text,
        reply_markup=markup, preview=preview
    )
    if msg_id:
        game['display_message'] = {'chat_id': chat_id, 'msg_id': msg_id}

def get_team_display(game, show_pending=False):
    p1, p2 = game['p1'], game['p2']
    roles_iter = game.get('roles', ROLES)
    
    table_p1 = []
    for r in roles_iter:
        unique_char_id = p1['team'].get(r)
        if not unique_char_id and show_pending:
            r_index = game.get('current_role_index', 0)
            if r_index < len(game.get('roles_order', [])) and game['roles_order'][r_index] == r:
                char_display = "❓ Picking..."
            else:
                char_display = "..."
        else:
            char_display = unique_char_id.split(' | ')[0] if unique_char_id else "..."
        table_p1.append((r, char_display))

    table_p2 = []
    for r in roles_iter:
        unique_char_id = p2['team'].get(r)
        if not unique_char_id and show_pending:
            r_index = game.get('current_role_index', 0)
            if r_index < len(game.get('roles_order', [])) and game['roles_order'][r_index] == r:
                char_display = "❓ Picking..."
            else:
                char_display = "..."
        else:
            char_display = unique_char_id.split(' | ')[0] if unique_char_id else "..."
        table_p2.append((r, char_display))

    p1_card = rich_ui.build_rich_card(
        title=f"🔵 {p1['name']}'s Team",
        table_data=table_p1
    )
    p2_card = rich_ui.build_rich_card(
        title=f"🔴 {p2['name']}'s Team",
        table_data=table_p2
    )

    return f"{p1_card}\n\n{p2_card}"

def pokemon_get_team_display(game):
    p1, p2 = game['p1'], game['p2']

    table_p1 = []
    for role in POKEMON_ROLES:
        entry = p1['team'].get(role)
        if role == "Type":
            val = f"{'/'.join(entry['types'])} ({entry['pokemon']})" if entry else "..."
        else:
            val = f"{entry['value']} ({entry['pokemon']})" if entry else "..."
        table_p1.append((role, val))

    table_p2 = []
    for role in POKEMON_ROLES:
        entry = p2['team'].get(role)
        if role == "Type":
            val = f"{'/'.join(entry['types'])} ({entry['pokemon']})" if entry else "..."
        else:
            val = f"{entry['value']} ({entry['pokemon']})" if entry else "..."
        table_p2.append((role, val))

    p1_card = rich_ui.build_rich_card(
        title=f"🔵 {p1['name']}'s Build",
        table_data=table_p1
    )
    p2_card = rich_ui.build_rich_card(
        title=f"🔴 {p2['name']}'s Build",
        table_data=table_p2
    )

    return f"{p1_card}\n\n{p2_card}"
