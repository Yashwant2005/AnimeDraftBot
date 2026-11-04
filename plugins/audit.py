"""Private Telegram audit feed for operational bot events.

Only action metadata is sent: command, actor, source chat, and safe result
details. Ordinary message content and configuration secrets are never copied.
"""

import html
import logging
from datetime import datetime, timezone

from pyrogram import Client, filters
from pyrogram.enums import ParseMode

import config


def _chat_label(chat):
    if not chat:
        return 'Unknown chat'
    title = getattr(chat, 'title', None) or getattr(chat, 'first_name', None) or 'Private chat'
    username = getattr(chat, 'username', None)
    return f"{title}{f' (@{username})' if username else ''} [{chat.id}]"


def _actor_label(actor):
    if not actor:
        return 'System'
    name = getattr(actor, 'first_name', None) or getattr(actor, 'username', None) or 'Unknown user'
    username = getattr(actor, 'username', None)
    return f"{name}{f' (@{username})' if username else ''} [{actor.id}]"


async def log_event(client, event, *, actor=None, chat=None, details=None):
    """Deliver one audit event without ever interrupting the actual feature."""
    if not config.AUDIT_LOG_CHAT_ID:
        return
    lines = [
        '🧾 <b>BOT AUDIT</b>',
        f'<b>Event:</b> {html.escape(str(event))}',
        f'<b>By:</b> {html.escape(_actor_label(actor))}',
    ]
    if chat:
        lines.append(f'<b>Source:</b> {html.escape(_chat_label(chat))}')
    if details:
        lines.append(f'<b>Details:</b> {html.escape(str(details)[:1200])}')
    lines.append(f'<b>UTC:</b> {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")}')
    try:
        await client.send_message(
            config.AUDIT_LOG_CHAT_ID,
            '\n'.join(lines),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception as exc:
        logging.warning('[AUDIT] Could not deliver %s: %s', event, exc)


@Client.on_message(
    filters.incoming & filters.text & filters.regex(r'^/[A-Za-z][A-Za-z0-9_]*(?:@\w+)?(?:\s|$)'),
    group=-100,
)
async def audit_command_invocation(client, message):
    """Track every bot command globally, without storing its arguments."""
    if not message.from_user or message.from_user.is_bot:
        return
    command = (message.text or '/unknown').split(maxsplit=1)[0][1:].split('@', 1)[0].lower()
    await log_event(client, 'COMMAND USED', actor=message.from_user, chat=message.chat, details=f'/{command}')
