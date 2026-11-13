"""Privacy-preserving public group activity directory."""

import html
import logging

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import database
import rich_ui
from utils import is_admin


PAGE_SIZE = 5
MAX_GROUPS = 15
_VALID_MEMBER_STATUSES = {'owner', 'administrator', 'member', 'restricted'}


def _mode_from_message(message):
    argument = (message.command[1].lower() if len(message.command or []) > 1 else 'weekly')
    return argument if argument in {'weekly', 'today', 'all'} else None


def _status_is_member(member):
    status = getattr(member, 'status', '')
    status = getattr(status, 'value', status)
    status = str(status).lower()
    return status in _VALID_MEMBER_STATUSES and getattr(member, 'is_member', True) is not False


async def _verify_group(client, group, bot_id=None):
    """Verify both bot membership and public username with Telegram before display."""
    try:
        chat_id = group['chat_id']
        if bot_id is None:
            bot_id = (await client.get_me()).id
        member = await client.get_chat_member(chat_id, bot_id)
        if not _status_is_member(member):
            database.set_group_membership(chat_id, False)
            return None
        chat = await client.get_chat(chat_id)
        username = getattr(chat, 'username', None)
        if not username:
            # A group that lost its username became private. Do not list it.
            database.set_group_membership(chat_id, True, title=getattr(chat, 'title', None), username='')
            return None
        database.set_group_membership(chat_id, True, title=chat.title, username=username)
        group.update({'title': chat.title, 'public_username': username, 'public_link': f'https://t.me/{username}'})
        return group
    except Exception as exc:
        # Inability to verify is treated as ineligible, never as permission to expose it.
        logging.info('[GROUPS] Membership verification failed for %s: %s', group.get('chat_id'), exc)
        database.set_group_membership(group['chat_id'], False)
        return None


async def _verified_groups(client, mode):
    candidates = database.get_group_activity_candidates(mode, limit=60)
    groups = []
    bot_id = (await client.get_me()).id
    for candidate in candidates:
        verified = await _verify_group(client, candidate, bot_id)
        if verified:
            groups.append(verified)
        if len(groups) >= MAX_GROUPS:
            break
    return groups


def _ranking_count(group, mode):
    if mode == 'today':
        return group.get('daily_message_count', 0)
    if mode == 'all':
        return group.get('total_messages', 0)
    return group.get('weekly_message_count', 0)


def _listing_html(groups, mode, page):
    ru = rich_ui
    label = {'weekly': 'weekly', 'today': "today's", 'all': 'all-time'}[mode]
    body = ru.r_heading('\U0001f525 ' + ru.sc('Top 15 Active AnimeDraft Groups'), 2, raw=True)
    if not groups:
        return body + ru.r_p(ru.sc('No verified public groups have activity yet.'))
    body += ru.r_p('<i>' + ru.sc('Sorted by ' + label + ' activity') + '</i>')
    start = page * PAGE_SIZE
    table_rows = []
    for index, group in enumerate(groups[start:start + PAGE_SIZE], start + 1):
        username = group.get('public_username')
        link = '<a href="https://t.me/' + ru.esc(username) + '">@' + ru.esc(username) + '</a>' if username else '-'
        name = ru.esc_name(group.get('title') or 'Untitled group', 40)
        messages = int(_ranking_count(group, mode))
        users = int(group.get('unique_active_users', 0))
        body += ru.r_heading(str(index) + '. ' + name, 4, raw=True)
        body += ru.r_kv([
            (ru.sc(label.title() + ' messages'), format(messages, ',')),
            (ru.sc('Active users'), format(users, ',')),
            (ru.sc('Link'), link),
        ])
    total_pages = max(1, (len(groups) + PAGE_SIZE - 1) // PAGE_SIZE)
    return body + ru.r_p(ru.sc('Page') + ' ' + str(page + 1) + '/' + str(total_pages))


def _listing_rows(groups, mode, page):
    # No per-group "Join" buttons: every group's @username link is inside the text.
    return [
        rich_ui.nav_row(
            f'groups:{mode}:{page - 1}' if page > 0 else None,
            f'groups:{mode}:{page + 1}' if (page + 1) * PAGE_SIZE < len(groups) else None,
        ),
        [rich_ui.Btn('\u21bb REFRESH', cb=f'groups:{mode}:{page}')],
    ]


async def _send_or_edit_listing(client, target, mode, page, *, edit=False):
    groups = await _verified_groups(client, mode)
    max_page = max(0, (len(groups) - 1) // PAGE_SIZE)
    page = max(0, min(page, max_page))
    body = _listing_html(groups, mode, page)
    rows = _listing_rows(groups, mode, page)
    if edit:
        message = target.message
        await rich_ui.rich_edit(client, message.chat.id, message.id, body, rows)
    else:
        await rich_ui.rich_send(client, target.chat.id, body, rows)


@Client.on_message(filters.group & ~filters.bot, group=-1)
async def track_group_activity(_, message):
    """The early handler records every user group message before command handlers run."""
    if not message.from_user or message.sender_chat:
        return
    database.record_group_message(message.chat, message.id, message.from_user.id, message.date)


@Client.on_chat_member_updated(group=-1)
async def track_bot_membership(_, update):
    """Mark a tracked group ineligible as soon as Telegram reports the bot left."""
    try:
        user = getattr(update.new_chat_member, 'user', None)
        if user and user.is_self:
            database.set_group_membership(update.chat.id, _status_is_member(update.new_chat_member),
                                          title=getattr(update.chat, 'title', None),
                                          username=getattr(update.chat, 'username', None))
    except Exception:
        logging.exception('[GROUPS] Could not process bot membership update')


@Client.on_message(filters.command('groups'))
async def groups_command(client, message):
    mode = _mode_from_message(message)
    if mode is None:
        return await message.reply_text('Usage: <code>/groups</code>, <code>/groups today</code>, or <code>/groups all</code>', parse_mode=ParseMode.HTML)
    if not database.USE_MONGO:
        return await message.reply_text('Group activity tracking requires the configured database.')
    await _send_or_edit_listing(client, message, mode, 0)


@Client.on_callback_query(filters.regex(r'^groups:(weekly|today|all):(\d+)$'))
async def groups_pagination_callback(client, callback_query):
    mode = callback_query.matches[0].group(1)
    page = int(callback_query.matches[0].group(2))
    await callback_query.answer('Refreshing verified group data…')
    await _send_or_edit_listing(client, callback_query, mode, page, edit=True)


async def _refresh_all_memberships(client):
    if not database.USE_MONGO or database.COL_GROUP_ACTIVITY is None:
        return 0, 0
    checked = removed = 0
    bot_id = (await client.get_me()).id
    for group in database.COL_GROUP_ACTIVITY.find({'is_member': True}).limit(200):
        checked += 1
        if not await _verify_group(client, group, bot_id):
            removed += 1
    return checked, removed


@Client.on_message(filters.command('groups_stats'))
async def groups_stats_command(client, message):
    if not message.from_user or not await is_admin(message.from_user.id):
        return await message.reply_text('Admin only.')
    stats = database.get_group_activity_stats()
    if stats is None:
        return await message.reply_text('Group activity tracking requires the configured database.')
    if len(message.command or []) > 1 and message.command[1].lower() == 'list':
        groups = list(database.COL_GROUP_ACTIVITY.find(
            {}, {'title': 1, 'chat_id': 1, 'is_member': 1, 'public_username': 1, 'total_messages': 1}
        ).sort('last_activity_at', -1).limit(30))
        if not groups:
            return await message.reply_text('No groups have been tracked yet.')
        lines = ['📋 <b>TRACKED GROUPS</b> <i>(most recently active)</i>']
        for group in groups:
            state = 'member' if group.get('is_member') else 'excluded'
            visibility = 'public' if group.get('public_username') else 'private'
            lines.append(
                f'• <b>{html.escape(group.get("title") or "Untitled group")}</b> '
                f'<code>{group["chat_id"]}</code> — {state}, {visibility}, {int(group.get("total_messages", 0)):,} msgs'
            )
        return await message.reply_text('\n'.join(lines), parse_mode=ParseMode.HTML)
    ru = rich_ui
    sc = ru.sc
    body = (
        ru.r_heading('\u00bb ' + sc('Group Tracking Statistics'), 2, raw=True)
        + ru.r_kv([
            (sc('All tracked groups'), format(stats['tracked_groups'], ',')),
            (sc('Current bot-member groups'), format(stats['member_groups'], ',')),
            (sc('Publicly eligible groups'), format(stats['public_groups'], ',')),
            (sc('Aggregate messages'), format(stats['messages'], ',')),
            (sc('Aggregate active users'), format(stats['active_users'], ',')),
        ])
        + ru.r_p('<i>' + sc('Only aggregate counts are retained; message content and public user IDs are never stored.') + '</i>')
    )
    await ru.rich_send(client, message.chat.id, body, [[ru.Btn('\u21bb REFRESH MEMBERSHIP', cb='grpadmin:refresh', style='primary')]])


@Client.on_callback_query(filters.regex(r'^grpadmin:refresh$'))
async def groups_admin_refresh_callback(client, callback_query):
    if not callback_query.from_user or not await is_admin(callback_query.from_user.id):
        return await callback_query.answer('Admin only.', show_alert=True)
    await callback_query.answer('Verifying tracked groups…')
    checked, removed = await _refresh_all_memberships(client)
    await callback_query.message.reply_text(f'✅ Membership refresh complete. Checked {checked}; excluded {removed} unavailable or private groups.')

