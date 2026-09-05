import html
import asyncio
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ParseMode
from pyrogram.errors import FloodWait
import global_state
from config import ROLES, POKEMON_ROLES, DEFAULT_POWER

async def ensure_display_message(client, chat_id, game, text, reply_markup=None, preview=False):
    disp = game.get('display_message')
    kb = InlineKeyboardMarkup(reply_markup) if isinstance(reply_markup, list) else reply_markup
    
    if disp:
        try:
            await client.edit_message_text(
                chat_id=disp['chat_id'], message_id=disp['msg_id'],
                text=text, reply_markup=kb,
                disable_web_page_preview=not preview, parse_mode=ParseMode.HTML
            )
            return
        except FloodWait as e:
            await asyncio.sleep(e.value)
            return await ensure_display_message(client, chat_id, game, text, reply_markup, preview)
        except Exception: return

    try:
        sent = await client.send_message(
            chat_id=chat_id, text=text, reply_markup=kb,
            disable_web_page_preview=not preview, parse_mode=ParseMode.HTML
        )
        game['display_message'] = {'chat_id': sent.chat.id, 'msg_id': sent.id}
    except Exception: pass

def get_team_display(game, show_pending=False):
    p1, p2 = game['p1'], game['p2']
    
    def fmt_role(r, team_dict, is_p1):
        unique_char_id = team_dict.get(r)
        if not unique_char_id and show_pending:
            r_index = game.get('current_role_index', 0)
            if r_index < len(game.get('roles_order', [])) and game['roles_order'][r_index] == r:
                 return f"• {r}: <code>❓ Picking...</code>"
        
        char_display = unique_char_id.split(' | ')[0] if unique_char_id else ". . ."
        return f"• {r}: <code>{html.escape(char_display)}</code>"

    roles_iter = game.get('roles', ROLES)
    txt = f"🔵 <b>{html.escape(p1['name'])}'s Team</b>:\n"
    for role in roles_iter: txt += fmt_role(role, p1['team'], True) + "\n"
    txt += f"\n🔴 <b>{html.escape(p2['name'])}'s Team</b>:\n"
    for role in roles_iter: txt += fmt_role(role, p2['team'], False) + "\n"
    return txt

def pokemon_get_team_display(game):
    p1, p2 = game['p1'], game['p2']
    def format_stat_line(role, team_dict):
        entry = team_dict.get(role)
        if role == "Type":
            val = f"{'/'.join(entry['types'])} ({html.escape(entry['pokemon'])})" if entry else "..."
        else:
            val = f"{entry['value']} ({html.escape(entry['pokemon'])})" if entry else "..."
        return f"• {role}: <code>{val}</code>"

    txt = f"🔵 <b>{html.escape(p1['name'])}'s Build</b>:\n" + "\n".join(format_stat_line(r, p1['team']) for r in POKEMON_ROLES)
    txt += f"\n\n🔴 <b>{html.escape(p2['name'])}'s Build</b>:\n" + "\n".join(format_stat_line(r, p2['team']) for r in POKEMON_ROLES)
    return txt