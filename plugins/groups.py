"""Privacy-preserving public group activity directory."""

import html
import logging

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import database
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


def _listing_text(groups, mode, page):
    label = {'weekly': 'WEEKLY', 'today': "TODAY'S", 'all': 'ALL-TIME'}[mode]
    if not groups:
        return f'🔥 <b>TOP ACTIVE ANIMEDRAFT GROUPS</b>\n\nNo verified public groups have activity yet.'
    start = page * PAGE_SIZE
    entries = []
    for index, group in enumerate(groups[start:start + PAGE_SIZE], start + 1):
        entries.append(
            f'<b>{index}. {html.escape(group.get("title") or "Untitled group")}</b>\n'
            f'💬 {label.title()} messages: {int(_ranking_count(group, mode)):,}\n'
            f'👥 Active users: {int(group.get("unique_active_users", 0)):,}'
        )
    return '🔥 <b>TOP 15 ACTIVE ANIMEDRAFT GROUPS</b>\n' + f'<i>Sorted by {label.lower()} activity</i>\n\n' + '\n\n'.join(entries)


def _listing_markup(groups, mode, page):
    start = page * PAGE_SIZE
    keyboard = []
    for group in groups[start:start + PAGE_SIZE]:
        link = group.get('public_link')
        if link:
            keyboard.append([InlineKeyboardButton('🔗 Join Group', url=link)])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton('◀ Previous', callback_data=f'groups:{mode}:{page - 1}'))
    if (page + 1) * PAGE_SIZE < len(groups):
        nav.append(InlineKeyboardButton('Next ▶', callback_data=f'groups:{mode}:{page + 1}'))
    if nav:
        keyboard.append(nav)
    keyboard.append([InlineKeyboardButton('🔄 Refresh', callback_data=f'groups:{mode}:{page}')])
    return InlineKeyboardMarkup(keyboard)


async def _send_or_edit_listing(client, target, mode, page, *, edit=False):
    groups = await _verified_groups(client, mode)
    max_page = max(0, (len(groups) - 1) // PAGE_SIZE)
    page = max(0, min(page, max_page))
    text = _listing_text(groups, mode, page)
    markup = _listing_markup(groups, mode, page)
    if edit:
        await target.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=markup, disable_web_page_preview=True)
    else:
        await target.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=markup, disable_web_page_preview=True)


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
    text = (
        '📊 <b>GROUP TRACKING STATISTICS</b>\n\n'
        f'All tracked groups: <b>{stats["tracked_groups"]:,}</b>\n'
        f'Current bot-member groups: <b>{stats["member_groups"]:,}</b>\n'
        f'Publicly eligible groups: <b>{stats["public_groups"]:,}</b>\n'
        f'Aggregate messages: <b>{stats["messages"]:,}</b>\n'
        f'Aggregate active users: <b>{stats["active_users"]:,}</b>\n\n'
        '<i>Only aggregate counts are retained; message content and public user IDs are never stored.</i>'
    )
    markup = InlineKeyboardMarkup([[InlineKeyboardButton('🔄 Refresh membership', callback_data='grpadmin:refresh')]])
    await message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)


@Client.on_callback_query(filters.regex(r'^grpadmin:refresh$'))
async def groups_admin_refresh_callback(client, callback_query):
    if not callback_query.from_user or not await is_admin(callback_query.from_user.id):
        return await callback_query.answer('Admin only.', show_alert=True)
    await callback_query.answer('Verifying tracked groups…')
    checked, removed = await _refresh_all_memberships(client)
    await callback_query.message.reply_text(f'✅ Membership refresh complete. Checked {checked}; excluded {removed} unavailable or private groups.')

