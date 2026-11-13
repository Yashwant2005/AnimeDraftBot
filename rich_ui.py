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

        # Mobile-friendly compact key-value display capped at max 36 chars per line
        table_lines = []
        for k, v in items:
            k_raw = str(k)
            v_raw = str(v)
            prefix = f"• {k_raw}: "
            allowed_v = max(6, 35 - len(prefix))
            if len(v_raw) > allowed_v:
                v_raw = v_raw[:allowed_v - 1] + "…"
            key_str = html.escape(k_raw)
            val_str = html.escape(v_raw)
            table_lines.append(f"• <b>{key_str}:</b> <code>{val_str}</code>")
        card_lines.append("\n".join(table_lines) + "\n")

    if expandable_quote:
        card_lines.append(f"<blockquote expandable>{str(expandable_quote)}</blockquote>\n")

    if footer:
        card_lines.append(f"<i>{str(footer)}</i>")

    return "\n".join(card_lines).strip()


# ---------------------------------------------------------------------------
# Fancy "small-caps" rich style (GreDex-like look)
# Only static labels are converted; user supplied text must be html.escape()d
# and passed through untouched.
# ---------------------------------------------------------------------------
import re as _re

_SC_MAP = dict(zip(
    "abcdefghijklmnopqrstuvwxyz",
    "\u1d00\u0299\u1d04\u1d05\u1d07\ua730\u0262\u029c\u026a\u1d0a\u1d0b\u029f\u1d0d\u0274\u1d0f\u1d18\u01eb\u0280\ua731\u1d1b\u1d1c\u1d20\u1d21x\u028f\u1d22",
))
_TOKEN_RE = _re.compile(r"(<[^>]+>|&[a-zA-Z#0-9]+;|https?://\S+|[/@][A-Za-z0-9_]+)")


def sc(text, keep_first_caps=True):
    """Small caps conversion for static labels.

    Left untouched: HTML tags/entities, anything inside <code>/<pre>, /commands,
    @mentions and URLs (so copy-paste commands keep working).
    """
    out = []
    in_code = 0
    for part in _TOKEN_RE.split(str(text)):
        if not part:
            continue
        low = part.lower()
        if low.startswith("<code") or low.startswith("<pre"):
            in_code += 1
        elif low in ("</code>", "</pre>"):
            in_code = max(0, in_code - 1)
        if in_code or _TOKEN_RE.fullmatch(part):
            out.append(part)
        else:
            out.append("".join(_SC_MAP.get(ch, ch) for ch in part))
    return "".join(out)


def head(text):
    """\u300c \u0299\u1d0f\u029f\u1d05 \u300d style section header."""
    return f"\u300c <b>{sc(text)}</b> \u300d"


def bullet(text, raw=False):
    """\u27ab bullet line. raw=True keeps text as-is (use for dynamic/escaped text)."""
    return f"\u27ab {text if raw else sc(text)}"


def title_line(text):
    return f"\u00bb {sc(text)}"


def fancy_card(title=None, sections=None, intro=None, footer=None):
    """Build a GreDex-style message.

    title    : static text -> '\u00bb Small Caps'
    intro    : already-formatted html (use sc() yourself for static parts)
    sections : list of (header, [lines]) ; header is static text, lines are
               already formatted html strings (they get the \u27a3 bullet).
    """
    blocks = []
    if title:
        blocks.append(title_line(title))
    if intro:
        blocks.append(intro)
    for header, lines in (sections or []):
        body = "\n".join(lines)
        blocks.append(f"{head(header)}\n{body}" if header else body)
    if footer:
        blocks.append(footer)
    return "\n\n".join(blocks)


def sc_button(text, **kw):
    """InlineKeyboardButton with an uppercase label (GreDex button look)."""
    return InlineKeyboardButton(str(text).upper(), **kw)



def series_list_text(title, items, page, total_pages):
    """Fancy paginated series list (used by /list, /alist and their callbacks)."""
    lines = [bullet(html.escape(str(i)), raw=True) for i in items]
    return (
        f"{title_line(title)}\n\n"
        f"{head('series')}\n" + "\n".join(lines) +
        f"\n\n{sc('Page')} {page}/{total_pages}"
    )


def nav_buttons(prev_cb=None, next_cb=None):
    """Rich style Previous / Next row (returns [] when neither applies)."""
    row = []
    if prev_cb:
        row.append(InlineKeyboardButton("\u25c2 PREVIOUS", callback_data=prev_cb))
    if next_cb:
        row.append(InlineKeyboardButton("NEXT \u25b8", callback_data=next_cb))
    return row


def style_guide_body(body):
    """Turn a legacy <b>heading</b>/<code>cmd</code> guide body into the fancy style."""
    out = []
    for line in str(body).split("\n"):
        stripped = line.strip()
        if not stripped:
            out.append("")
            continue
        m = _re.fullmatch(r"<b>(.+)</b>", stripped)
        if m and not m.group(1).startswith("/"):
            out.append(head(m.group(1)))
        elif m:
            out.append(f"{bullet('', raw=True)}<b>{m.group(1)}</b>".replace("\u27ab ", "\u27ab ", 1))
        else:
            out.append(bullet(sc(stripped), raw=True))
    return "\n".join(out)



def list_home_text():
    return (
        f"{title_line('Available Series')}\n\n"
        f"{sc('Select the game mode whose series you want to browse.')}"
    )


def list_home_markup():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("DRAFT", callback_data="list_anime_1"),
        InlineKeyboardButton("AUCTION DRAFT", callback_data="alist:1"),
    ]])


def list_page_rows(mode, page, total_pages):
    """Keyboard for /list pages: Prev/Next, anime<->comics toggle, back."""
    rows = []
    nav = nav_buttons(
        f"list_{mode}_{page - 1}" if page > 1 else None,
        f"list_{mode}_{page + 1}" if page < total_pages else None,
    )
    if nav:
        rows.append(nav)
    toggle = (InlineKeyboardButton("COMICS", callback_data="list_comics_1") if mode == "anime"
              else InlineKeyboardButton("ANIME", callback_data="list_anime_1"))
    rows.append([toggle])
    rows.append([InlineKeyboardButton("\u25c2 BACK", callback_data="list_home")])
    return InlineKeyboardMarkup(rows)



# ===========================================================================
# Native Bot API 10.3 Rich Messages
# ---------------------------------------------------------------------------
# * sendRichMessage      -> {"chat_id": .., "rich_message": {"html": ..}}
# * editMessageText      -> {"rich_message": {"html": ..}}  (no "text")
# * buttons live INSIDE the html:  <tg-button-row><tg-button type="callback_data"
#   data=".." style="primary">..</tg-button></tg-button-row>
#   (1-8 buttons per row, callback data 1-64 bytes, url buttons use type="url")
# * Pyrogram 2.0.x has no rich message support, so raw HTTP Bot API is used.
# * If Telegram rejects the rich message, we fall back to the styled
#   Unicode text + normal inline keyboard, so a command never goes silent.
# Set RICH_NATIVE=0 in the environment to disable the native path entirely.
# ===========================================================================
import os as _os
from collections import namedtuple as _namedtuple

NATIVE_ENABLED = _os.getenv("RICH_NATIVE", "1") != "0"
_BTN_STYLES = {"primary", "success", "danger"}
Btn = _namedtuple("Btn", "label cb url style", defaults=(None, None, None))


import unicodedata as _ud

# Characters that break rich parsing / table layout: control chars, bidi overrides
# (they flip the arrow and column order, like "\u0628\u0627\u062f\u0634\u0627\u06c1" rows), and blank
# "filler" characters people use to fake empty names.
_STRIP_CODES = {
    0x200E, 0x200F, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0x2066, 0x2067, 0x2068, 0x2069,
    0x061C, 0x2800, 0x3164, 0x115F, 0x1160, 0xFFA0, 0x200B, 0x2060, 0xFEFF, 0x00AD,
}


def clean_text(value):
    """Remove control/bidi/invisible characters but keep letters, emoji, marks and ZWJ."""
    out = []
    for ch in str(value):
        code = ord(ch)
        if code in _STRIP_CODES:
            continue
        cat = _ud.category(ch)
        if cat in ("Cs", "Co", "Cn"):
            continue
        if cat == "Cc" and ch not in "\n\t":
            continue
        out.append(ch)
    return "".join(out)


def esc(value):
    """Escape dynamic text for rich html (names, titles, usernames...)."""
    return html.escape(clean_text(value), quote=True)


def esc_name(value, limit=32):
    """Player/group names: sanitized, single line, truncated, never empty."""
    text = " ".join(clean_text(value).split())
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "\u2026"
    return html.escape(text or "Unknown", quote=True)


def hard_clean_html(rich_html):
    """Last-resort cleanup of text nodes (tags untouched): keep only letters, numbers,
    marks, punctuation, symbols and spaces."""
    parts = _re.split(r"(<[^>]*>)", rich_html)
    cleaned = []
    for part in parts:
        if part.startswith("<") and part.endswith(">"):
            cleaned.append(part)
        else:
            cleaned.append("".join(ch for ch in part if _ud.category(ch)[0] in "LNPSZM" or ch in "\n\t"))
    return "".join(cleaned)


def r_heading(text, level=3, raw=False):
    return f"<h{level}>{text if raw else esc(text)}</h{level}>"


def r_p(text, raw=True):
    return f"<p>{text if raw else esc(text)}</p>"


def r_list(items, raw=True):
    body = "".join(f"<li>{i if raw else esc(i)}</li>" for i in items)
    return f"<ul>{body}</ul>"


def r_table(rows, header=None, striped=True):
    """rows: list of lists of ready html cell strings."""
    out = "<table bordered" + (" striped" if striped else "") + ">"
    if header:
        out += "<tr>" + "".join(f"<th>{h}</th>" for h in header) + "</tr>"
    for row in rows:
        out += "<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>"
    return out + "</table>"


def r_kv(pairs):
    """Two column key/value table. keys are static labels, values are html."""
    return r_table([[f"<b>{k}</b>", v] for k, v in pairs])


def r_details(summary, body_html):
    return f"<details><summary>{summary}</summary>{body_html}</details>"


# The Bot API has no width option for rich buttons (<tg-button-row> only supports
# align = left | center | right), so "expanded" buttons are done by padding every label
# in a row with invisible Braille blanks so all buttons get the same width.
# Set RICH_BTN_PAD=0 to turn this off.
BTN_PAD = _os.getenv("RICH_BTN_PAD", "1") != "0"
_PAD_CHAR = "\u2800"
_ROW_WIDTH = {1: 26, 2: 17, 3: 11}


def _pad_label(label, target):
    missing = target - len(label)
    if not BTN_PAD or missing <= 0:
        return label
    left = missing // 2
    return _PAD_CHAR * left + label + _PAD_CHAR * (missing - left)


def r_button(btn, target=0):
    label = _pad_label(esc(btn.label), target)
    style = f' style="{btn.style}"' if btn.style in _BTN_STYLES else ""
    if btn.url:
        return f'<tg-button type="url" url="{esc(btn.url)}"{style}>{label}</tg-button>'
    data = str(btn.cb or "noop")
    if len(data.encode("utf-8")) > 64:
        data = data[:64]
    return f'<tg-button type="callback_data" data="{esc(data)}"{style}>{label}</tg-button>'


def r_rows(rows, align="center"):
    """rows: list of lists of Btn (max 8 per row). Buttons in a row get equal width."""
    out = []
    for row in rows or []:
        row = [b for b in row if b is not None][:8]
        if not row:
            continue
        longest = max(len(b.label) for b in row)
        target = min(max(longest, 6), _ROW_WIDTH.get(len(row), 8)) if len(row) > 1 else min(max(longest, 14), _ROW_WIDTH[1])
        if len(row) > 1:
            target = max(target, min(longest, _ROW_WIDTH.get(len(row), 8)))
        out.append(f'<tg-button-row align="{align}">' + "".join(r_button(b, target) for b in row) + "</tg-button-row>")
    return "".join(out)


def to_markup(rows):
    """Same button rows as a normal inline keyboard (fallback path)."""
    keyboard = []
    for row in rows or []:
        out = []
        for b in row:
            if b is None:
                continue
            if b.url:
                out.append(InlineKeyboardButton(b.label, url=b.url))
            else:
                out.append(InlineKeyboardButton(b.label, callback_data=b.cb or "noop"))
        if out:
            keyboard.append(out)
    return InlineKeyboardMarkup(keyboard) if keyboard else None


_TAG = _re.compile(r"</?(?:tg-button-row|tg-button)[^>]*>")


def html_to_fallback(rich_html):
    """Turn rich html into plain Telegram HTML (b/i/u/s/code/a) with line breaks."""
    t = rich_html
    t = _re.sub(r"<tg-button-row.*?</tg-button-row>", "", t, flags=_re.S)
    t = _re.sub(r"<img[^>]*/?>", "", t)
    t = _re.sub(r"<h[1-6][^>]*>(.*?)</h[1-6]>", r"\n<b>\1</b>\n", t, flags=_re.S)
    t = _re.sub(r"<li[^>]*>", "\n\u27ab ", t)
    t = _re.sub(r"</(?:p|ul|ol|li|details|summary|footer)>", "\n", t)
    t = _re.sub(r"</t[dh]>\s*<t[dh][^>]*>", " \u27ab ", t)
    t = _re.sub(r"</tr>", "\n", t)
    t = _re.sub(r"<(?!/?(?:b|i|u|s|code|pre|a)[\s>])[^>]+>", "", t)
    t = _re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _api_call(token, method, payload):
    url = f"https://api.telegram.org/bot{token}/{method}"
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode("utf-8"))
        except Exception:
            return {"ok": False, "description": str(exc)}
    except Exception as exc:
        return {"ok": False, "description": repr(exc)}


def _rich_payload(body_html, rows, photo_id, skip_entities=False):
    content = ""
    rich = {"skip_entity_detection": bool(skip_entities)}
    if photo_id:
        content += '<img src="tg://photo?id=hero"/>'
        rich["media"] = [{"id": "hero", "media": {"type": "photo", "media": photo_id}}]
    rich["html"] = content + body_html + r_rows(rows)
    return rich


_NO_RETRY = ("not found", "can't be edited", "forbidden", "blocked", "kicked", "chat not", "not enough rights")


async def _rich_call(token, method, base_payload, body_html, rows, photo_id):
    """Call a rich method with a retry ladder:
    1) normal   2) 429 -> wait and retry   3) 4xx parse problem -> hard-cleaned text, no entity detection.
    Returns the last Bot API result dict."""
    def build(body, skip):
        payload = dict(base_payload)
        payload["rich_message"] = _rich_payload(body, rows, photo_id, skip)
        return payload

    result = await asyncio.to_thread(_api_call, token, method, build(body_html, False))
    for attempt in range(2):
        if result.get("ok"):
            return result
        desc = str(result.get("description", "")).lower()
        if "not modified" in desc:
            return {"ok": True, "result": {}}
        code = result.get("error_code")
        if code == 429:
            wait = min(int((result.get("parameters") or {}).get("retry_after", 1)), 5)
            logging.warning("[RICH_UI] %s rate limited, retrying in %ss", method, wait)
            await asyncio.sleep(wait)
            result = await asyncio.to_thread(_api_call, token, method, build(body_html, False))
            continue
        if code and 400 <= int(code) < 500 and not any(k in desc for k in _NO_RETRY):
            logging.warning("[RICH_UI] %s rejected (%s); retrying with cleaned text", method, result.get("description"))
            result = await asyncio.to_thread(_api_call, token, method, build(hard_clean_html(body_html), True))
            continue
        if not code:  # network error / timeout: one more try
            logging.warning("[RICH_UI] %s network error (%s); retrying", method, result.get("description"))
            result = await asyncio.to_thread(_api_call, token, method, build(body_html, False))
            continue
        break
    return result


async def rich_send(client, chat_id, body_html, rows=None, photo_id=None, reply_to=None):
    """Send a native rich message (fallback: styled text + inline keyboard). Returns message id."""
    token = getattr(client, "bot_token", None)
    if NATIVE_ENABLED and token:
        base = {"chat_id": chat_id}
        if reply_to:
            base["reply_parameters"] = {"message_id": reply_to}
        result = await _rich_call(token, "sendRichMessage", base, body_html, rows, photo_id)
        if result.get("ok"):
            return (result.get("result") or {}).get("message_id")
        logging.warning("[RICH_UI] sendRichMessage failed, using fallback: %s", result.get("description"))
    try:
        sent = await client.send_message(
            chat_id, html_to_fallback(body_html), reply_markup=to_markup(rows),
            parse_mode=ParseMode.HTML, disable_web_page_preview=True,
            reply_to_message_id=reply_to,
        )
        return sent.id if sent else None
    except Exception as exc:
        logging.error("[RICH_UI] fallback send failed for %s: %s", chat_id, exc)
        return None


async def rich_edit(client, chat_id, message_id, body_html, rows=None):
    """Edit a message into a native rich message (fallback: normal edit)."""
    token = getattr(client, "bot_token", None)
    if NATIVE_ENABLED and token:
        base = {"chat_id": chat_id, "message_id": message_id}
        result = await _rich_call(token, "editMessageText", base, body_html, rows, None)
        if result.get("ok"):
            return True
        logging.warning(
            "[RICH_UI] rich editMessageText failed (chat=%s msg=%s), using fallback: %s",
            chat_id, message_id, result.get("description"),
        )
    try:
        await client.edit_message_text(
            chat_id, message_id, html_to_fallback(body_html),
            reply_markup=to_markup(rows), parse_mode=ParseMode.HTML, disable_web_page_preview=True,
        )
        return True
    except Exception as exc:
        if "not modified" in str(exc).lower():
            return True
        logging.error("[RICH_UI] fallback edit failed: %s", exc)
        return False


def legacy_to_rich(body, static_sc=True):
    """Convert an old guide body (<b>heading</b>, <code>cmd</code> lines) to rich html blocks."""
    blocks, items = [], []

    def flush():
        if items:
            blocks.append(r_list(items))
            items.clear()

    for line in str(body).split("\n"):
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        m = _re.fullmatch(r"<b>(.+)</b>", stripped)
        if m and not m.group(1).startswith("/"):
            flush()
            blocks.append(r_heading(sc(m.group(1)) if static_sc else m.group(1), 4, raw=True))
        elif m:
            flush()
            blocks.append(f"<p><b>{m.group(1)}</b></p>")
        else:
            items.append(sc(stripped) if static_sc else stripped)
    flush()
    return "".join(blocks)


# ---- native series list helpers (/list, /alist) ----
def series_html(title, items, page, total_pages):
    return (
        r_heading("\u00bb " + sc(title), 2, raw=True)
        + r_heading("\u300c " + sc("series") + " \u300d", 3, raw=True)
        + r_list([esc(i) for i in items])
        + r_p(sc("Page") + " " + str(page) + "/" + str(total_pages))
    )


def nav_row(prev_cb=None, next_cb=None):
    return [
        Btn("\u25c2 PREVIOUS", cb=prev_cb) if prev_cb else None,
        Btn("NEXT \u25b8", cb=next_cb) if next_cb else None,
    ]


def list_home_html():
    return (
        r_heading("\u00bb " + sc("Available Series"), 2, raw=True)
        + r_p(sc("Select the game mode whose series you want to browse."))
    )


def list_home_rows():
    return [[Btn("DRAFT", cb="list_anime_1", style="primary"), Btn("AUCTION DRAFT", cb="alist:1", style="success")]]


def list_rows(mode, page, total_pages):
    toggle = Btn("COMICS", cb="list_comics_1") if mode == "anime" else Btn("ANIME", cb="list_anime_1")
    return [
        nav_row(f"list_{mode}_{page - 1}" if page > 1 else None,
                f"list_{mode}_{page + 1}" if page < total_pages else None),
        [toggle, Btn("\u25c2 BACK", cb="list_home")],
    ]


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
