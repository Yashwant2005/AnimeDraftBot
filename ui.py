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
    
    def fmt_role(r, team_dict):
        unique_char_id = team_dict.get(r)
        if not unique_char_id and show_pending:
            r_index = game.get('current_role_index', 0)
            if r_index < len(game.get('roles_order', [])) and game['roles_order'][r_index] == r:
                return f"• {r}: <code>❓ Picking...</code>"

        char_display = unique_char_id.split(' | ')[0] if unique_char_id else ". . ."
        # Truncate character name if line would exceed 35 chars
        prefix = f"• {r}: "
        allowed_len = max(8, 35 - len(prefix))
        if len(char_display) > allowed_len:
            char_display = char_display[:allowed_len - 1] + "…"
        return f"• {r}: <code>{html.escape(char_display)}</code>"

    roles_iter = game.get('roles', ROLES)
    p1_name = p1['name'][:20]
    p2_name = p2['name'][:20]
    txt = f"🔵 <b>{html.escape(p1_name)}'s Team</b>:\n"
    for role in roles_iter:
        txt += fmt_role(role, p1['team']) + "\n"
    txt += f"\n🔴 <b>{html.escape(p2_name)}'s Team</b>:\n"
    for role in roles_iter:
        txt += fmt_role(role, p2['team']) + "\n"
    return txt

def pokemon_get_team_display(game):
    p1, p2 = game['p1'], game['p2']
    def format_stat_line(role, team_dict):
        entry = team_dict.get(role)
        if role == "Type":
            val = f"{'/'.join(entry['types'])} ({entry['pokemon']})" if entry else ". . ."
        else:
            val = f"{entry['value']} ({entry['pokemon']})" if entry else ". . ."
        prefix = f"• {role}: "
        allowed_len = max(8, 35 - len(prefix))
        if len(val) > allowed_len:
            val = val[:allowed_len - 1] + "…"
        return f"• {role}: <code>{html.escape(val)}</code>"

    p1_name = p1['name'][:20]
    p2_name = p2['name'][:20]
    txt = f"🔵 <b>{html.escape(p1_name)}'s Build</b>:\n" + "\n".join(format_stat_line(r, p1['team']) for r in POKEMON_ROLES)
    txt += f"\n\n🔴 <b>{html.escape(p2_name)}'s Build</b>:\n" + "\n".join(format_stat_line(r, p2['team']) for r in POKEMON_ROLES)
    return txt
