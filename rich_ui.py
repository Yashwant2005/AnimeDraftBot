import html
import logging
import json
import asyncio
import urllib.request
import urllib.error
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ParseMode

def build_rich_card(title, subtitle=None, badges=None, description=None, table_data=None, expandable_quote=None, footer=None):
    """Builds a styled Rich Message card string using HTML markup and Bot API 10.3 block formatting.

    badges: list of strings (e.g. ['96% match', 'Adventure', 'Epic'])
    table_data: dict or list of (label, val) tuples for compact tables
    expandable_quote: text to put inside an expandable blockquote
    """
    card_lines = []

    if title:
        card_lines.append(f"<b>{html.escape(str(title))}</b>")
    if subtitle:
        card_lines.append(f"<i>{html.escape(str(subtitle))}</i>")
    if title or subtitle:
        card_lines.append("")

    if description:
        card_lines.append(f"{str(description)}\n")

    if badges:
        badge_str = " ".join([f"<code>{html.escape(str(b))}</code>" for b in badges])
        card_lines.append(badge_str + "\n")

    if table_data:
        if isinstance(table_data, dict):
            items = list(table_data.items())
        else:
            items = list(table_data)

        k_width = max(12, min(22, max((len(str(k)) for k, _ in items), default=12)))
        v_width = max(10, min(20, max((len(str(v)) for _, v in items), default=10)))
        total_dash = k_width + v_width + 3

        card_lines.append(f"<code>┌{'─' * total_dash}┐</code>")
        for k, v in items:
            key_raw = str(k)[:k_width]
            val_raw = str(v)[:v_width]
            key_str = html.escape(key_raw).ljust(k_width)
            val_str = html.escape(val_raw).rjust(v_width)
            card_lines.append(f"<code>│ {key_str} {val_str} │</code>")
        card_lines.append(f"<code>└{'─' * total_dash}┘</code>\n")

    if expandable_quote:
        card_lines.append(f"<blockquote expandable>{str(expandable_quote)}</blockquote>\n")

    if footer:
        card_lines.append(f"<i>{str(footer)}</i>")

    return "\n".join(card_lines).strip()

def build_disabled_button(text, callback_data="disabled_noop"):
    """Returns an InlineKeyboardButton with a disabled indicator."""
    return InlineKeyboardButton(f"🔒 {text}", callback_data=callback_data)

def format_rich_keyboard(rows):
    """Takes a list of button lists (or InlineKeyboardButton objects),
    wrapping raw tuples/dicts into InlineKeyboardButton, supporting disabled items.
    """
    if not rows:
        return None
    if isinstance(rows, InlineKeyboardMarkup):
        return rows

    formatted_rows = []
    for row in rows:
        formatted_row = []
        for btn in row:
            if isinstance(btn, InlineKeyboardButton):
                formatted_row.append(btn)
            elif isinstance(btn, dict):
                label = btn.get("text", "Button")
                if btn.get("disabled"):
                    label = f"🔒 {label}"
                cb = btn.get("callback_data", "noop")
                url = btn.get("url")
                if url:
                    formatted_row.append(InlineKeyboardButton(label, url=url))
                else:
                    formatted_row.append(InlineKeyboardButton(label, callback_data=cb))
            elif isinstance(btn, tuple) or isinstance(btn, list):
                label = btn[0]
                cb_or_url = btn[1] if len(btn) > 1 else "noop"
                is_disabled = btn[2] if len(btn) > 2 else False
                if is_disabled:
                    label = f"🔒 {label}"
                if str(cb_or_url).startswith("http://") or str(cb_or_url).startswith("https://"):
                    formatted_row.append(InlineKeyboardButton(label, url=str(cb_or_url)))
                else:
                    formatted_row.append(InlineKeyboardButton(label, callback_data=str(cb_or_url)))
        if formatted_row:
            formatted_rows.append(formatted_row)

    return InlineKeyboardMarkup(formatted_rows)

async def send_rich_message(client, chat_id, text, reply_markup=None, ephemeral_params=None, parse_mode=ParseMode.HTML, preview=False):
    """Sends a rich message using Pyrogram Client or HTTP Bot API if ephemeral parameters provided."""
    markup = format_rich_keyboard(reply_markup)
    token = getattr(client, "bot_token", None)

    if token and ephemeral_params:
        try:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": not preview,
                "ephemeral_message_parameters": ephemeral_params
            }
            if markup and hasattr(markup, "inline_keyboard"):
                keyboard_dict = []
                for row in markup.inline_keyboard:
                    row_list = []
                    for b in row:
                        b_dict = {"text": b.text}
                        if b.callback_data:
                            b_dict["callback_data"] = b.callback_data
                        elif b.url:
                            b_dict["url"] = b.url
                        row_list.append(b_dict)
                    keyboard_dict.append(row_list)
                payload["reply_markup"] = {"inline_keyboard": keyboard_dict}

            def _do_http():
                req = urllib.request.Request(
                    url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"}
                )
                with urllib.request.urlopen(req, timeout=5) as response:
                    if response.status == 200:
                        return json.loads(response.read().decode("utf-8"))
                return None

            resp_data = await asyncio.to_thread(_do_http)
            if resp_data and resp_data.get("ok"):
                result = resp_data.get("result", {})
                return result.get("message_id")
        except Exception as e:
            logging.debug(f"[RICH_UI] HTTP sendMessage failed: {e}, falling back to Pyrogram")

    try:
        sent = await client.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=markup,
            parse_mode=parse_mode,
            disable_web_page_preview=not preview
        )
        return sent.id if sent else None
    except Exception as e:
        logging.error(f"[RICH_UI] Error sending message to {chat_id}: {e}")
        return None

async def edit_rich_message(client, chat_id, message_id, text, reply_markup=None, parse_mode=ParseMode.HTML, preview=False):
    """Edits a rich message using Pyrogram client or HTTP Bot API fallback."""
    markup = format_rich_keyboard(reply_markup)
    token = getattr(client, "bot_token", None)

    if token:
        try:
            url = f"https://api.telegram.org/bot{token}/editMessageText"
            payload = {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": not preview
            }
            if markup and hasattr(markup, "inline_keyboard"):
                keyboard_dict = []
                for row in markup.inline_keyboard:
                    row_list = []
                    for b in row:
                        b_dict = {"text": b.text}
                        if b.callback_data:
                            b_dict["callback_data"] = b.callback_data
                        elif b.url:
                            b_dict["url"] = b.url
                        row_list.append(b_dict)
                    keyboard_dict.append(row_list)
                payload["reply_markup"] = {"inline_keyboard": keyboard_dict}

            def _do_http_edit():
                req = urllib.request.Request(
                    url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"}
                )
                with urllib.request.urlopen(req, timeout=5) as response:
                    if response.status == 200:
                        return json.loads(response.read().decode("utf-8"))
                return None

            resp_data = await asyncio.to_thread(_do_http_edit)
            if resp_data and resp_data.get("ok"):
                return True
        except Exception as e:
            logging.debug(f"[RICH_UI] HTTP editMessageText failed: {e}, falling back to Pyrogram")

    try:
        await client.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=markup,
            parse_mode=parse_mode,
            disable_web_page_preview=not preview
        )
        return True
    except Exception as e:
        err_str = str(e).lower()
        if "not modified" in err_str or "message_not_modified" in err_str:
            return True
        logging.debug(f"[RICH_UI] Pyrogram edit_message_text error: {e}")
        return False
