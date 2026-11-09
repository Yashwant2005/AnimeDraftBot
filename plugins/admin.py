from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from utils import is_admin, is_admin_sync, is_mod, is_owner, is_clone_instance
from config import OWNER_ID, OWNER_IDS
from plugins.audit import log_event
import global_state
import database
from config import DATA_DIR
import config
import json
import logging
import os
import re
import html
import time
import uuid
from datetime import datetime, timedelta, timezone

try:
    from bson import ObjectId
except ImportError:  # pragma: no cover - pymongo installs bson
    ObjectId = None

_pending_draft_adds = {}


# Owner-confirmed destructive database actions are persisted in Mongo rather
# than held only in memory. This makes expiration and duplicate taps safe even
# after a bot restart.
_DELETION_TTL = timedelta(minutes=5)


def _character_collections():
    return [
        ('characters', database.COL_CHARACTERS),
    ]


def _safe_object_id(value):
    if ObjectId is None:
        return None
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def _document_id(document):
    return str(document.get('_id') or document.get('id') or '')


def _character_series(document):
    return document.get('series') or document.get('anime') or 'Unknown'


def _find_character_matches(term, *, series=False):
    """Search both established character catalogs without exposing secrets."""
    if not database.USE_MONGO:
        return []
    exact_id = _safe_object_id(term)
    id_options = [{'id': term}]
    if str(term).isdigit():
        id_options.append({'id': int(term)})
    if exact_id:
        id_options.append({'_id': exact_id})
    field_queries = ([
        {'series': {'$regex': f'^{re.escape(term)}$', '$options': 'i'}},
        {'anime': {'$regex': f'^{re.escape(term)}$', '$options': 'i'}},
    ] if series else [{'name': {'$regex': f'^{re.escape(term)}$', '$options': 'i'}}])
    query = {
        'archived': {'$ne': True},
        '$or': id_options + field_queries,
    }
    matches = []
    for source, collection in _character_collections():
        if collection is None:
            continue
        for document in collection.find(query).limit(25):
            document['_source_collection'] = source
            matches.append(document)
    return matches


def _character_reference_count(document):
    """Count known persisted and in-memory references before a hard delete."""
    char_id, name = document.get('id'), document.get('name')
    user_alternatives = [{'characters.name': name}]
    market_alternatives = [{'character.name': name}]
    if char_id is not None:
        user_alternatives.append({'characters.id': char_id})
        market_alternatives.extend([{'character_id': char_id}, {'character.id': char_id}])
    references = 0
    if database.COL_WAIFU_USERS is not None:
        references += database.COL_WAIFU_USERS.count_documents({'$or': user_alternatives})
    if database.COL_WAIFU_MARKET is not None:
        references += database.COL_WAIFU_MARKET.count_documents({'$or': market_alternatives})
    # An active draft has not yet become historical data; archive instead of
    # pulling a card from a live game.
    keys = {f'{name} | {_character_series(document)}', f'{name} | Marvel', name}
    with global_state.GAMES_LOCK:
        for games in global_state.GAMES.values():
            for game in games.values():
                if keys.intersection(set(game.get('used_chars', [])) | set((game.get('p1', {}).get('team') or {}).values()) | set((game.get('p2', {}).get('team') or {}).values())):
                    references += 1
    return references


def _remove_document_from_local_source(document):
    """Keep JSON bootstrap data from reintroducing an archived/deleted card."""
    if document.get('_source_collection') != 'characters':
        return
    path = os.path.join(DATA_DIR, 'marvel.json' if document.get('kind') == 'comic' else 'character.json')
    if not os.path.exists(path):
        return
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            data = json.load(handle)
        if not isinstance(data, list):
            return
        name, series = document.get('name'), _character_series(document)
        retained = [item for item in data if not (item.get('name') == name and _character_series(item) == series)]
        if len(retained) != len(data):
            with open(path, 'w', encoding='utf-8') as handle:
                json.dump(retained, handle, ensure_ascii=False, indent=2)
    except Exception as exc:
        logging.warning('[DELETE] Could not update local catalog source: %s', exc)


def _purge_character_from_runtime(document):
    name, series = document.get('name'), _character_series(document)
    keys = {f'{name} | {series}', f'{name} | Marvel'}
    for key in list(global_state.CHAR_STATS):
        if key in keys:
            global_state.CHAR_STATS.pop(key, None)
            global_state.CHAR_IMAGES.pop(key, None)
    global_state.ANIME_CHARACTERS[:] = [item for item in global_state.ANIME_CHARACTERS if item not in keys and item != name]
    global_state.MARVEL_CHARACTERS[:] = [item for item in global_state.MARVEL_CHARACTERS if item not in keys]
    for mapping, display in ((global_state.SERIES_MAP, global_state.SERIES_DISPLAY),
                             (global_state.MARVEL_SERIES_MAP, global_state.MARVEL_SERIES_DISPLAY)):
        for normalized, members in list(mapping.items()):
            mapping[normalized] = [item for item in members if item not in keys]
            if not mapping[normalized]:
                mapping.pop(normalized, None)
                display.pop(normalized, None)


def _write_delete_action(request):
    source = request['source_collection']
    collection = dict(_character_collections()).get(source)
    if collection is None:
        raise RuntimeError('The source collection is unavailable.')
    ids = [_safe_object_id(value) for value in request['document_ids']]
    if not all(ids):
        raise RuntimeError('Invalid stored character identifier.')
    documents = list(collection.find({'_id': {'$in': ids}, 'archived': {'$ne': True}}))
    if len(documents) != len(ids):
        raise RuntimeError('One or more records no longer exist or were already archived.')
    for document in documents:
        document['_source_collection'] = source
    references = sum(_character_reference_count(document) for document in documents)
    now = datetime.now(timezone.utc)

    def write(session=None):
        if references:
            collection.update_many(
                {'_id': {'$in': ids}},
                {'$set': {'archived': True, 'archived_at': now, 'archived_reason': 'owner_delete_with_references'}},
                session=session,
            )
            return 'archived'
        collection.delete_many({'_id': {'$in': ids}}, session=session)
        return 'deleted'

    # Atlas/repl-set deployments support transactions; standalone MongoDB
    # deployments use the same guarded writes without pretending atomics exist.
    try:
        with database.MONGO_CLIENT.start_session() as session:
            with session.start_transaction():
                result = write(session)
    except Exception as exc:
        if 'Transaction numbers are only allowed' not in str(exc) and 'replica set' not in str(exc).lower():
            raise
        result = write()
    for document in documents:
        _remove_document_from_local_source(document)
        _purge_character_from_runtime(document)
    return result, len(documents), references


def _deletion_message(request):
    requester = request['requester']
    warning = 'Deletion may affect existing game data. Referenced records will be archived instead of hard-deleted.'
    if request['action'] == 'series':
        subject = (
            f"<b>Series:</b> {html.escape(request['series'])}\n"
            f"<b>Associated characters:</b> {len(request['document_ids'])}"
        )
    else:
        subject = (
            f"<b>Character:</b> {html.escape(request['character_name'])}\n"
            f"<b>Character ID:</b> <code>{request['document_ids'][0]}</code>\n"
            f"<b>Series:</b> {html.escape(request['series'])}"
        )
    return (
        '⚠️ <b>OWNER CONFIRMATION REQUIRED</b>\n\n'
        f'<b>Requested by:</b> {html.escape(requester["name"])} (<code>{requester["id"]}</code>)\n'
        f'{subject}\n\n{warning}\n\nExpires in 5 minutes.'
    )


async def _request_owner_confirmation(client, message, action, documents):
    now = datetime.now(timezone.utc)
    source = documents[0]['_source_collection']
    request = {
        'request_id': uuid.uuid4().hex[:16],
        'action': action,
        'source_collection': source,
        'document_ids': [_document_id(document) for document in documents],
        'character_name': documents[0].get('name', 'Unknown'),
        'series': _character_series(documents[0]),
        'requester': {'id': message.from_user.id, 'name': message.from_user.first_name or str(message.from_user.id)},
        'requester_chat_id': message.chat.id,
        'status': 'pending',
        'created_at': now,
        'expires_at': now + _DELETION_TTL,
    }
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton('✅ Confirm Delete', callback_data=f"delete:{request['request_id']}:confirm"),
        InlineKeyboardButton('❌ Cancel', callback_data=f"delete:{request['request_id']}:cancel"),
    ]])
    try:
        database.COL_DELETION_REQUESTS.insert_one(request)
    except Exception as exc:
        logging.exception('[DELETE] Could not persist confirmation request: %s', exc)
        return await message.reply('❌ Could not create a deletion confirmation request. Please try again later.')
    try:
        await client.send_message(config.OWNER_ID, _deletion_message(request), parse_mode=ParseMode.HTML, reply_markup=keyboard)
    except Exception as exc:
        database.COL_DELETION_REQUESTS.delete_one({'request_id': request['request_id']})
        logging.warning('[DELETE] Could not deliver owner confirmation: %s', exc)
        return await message.reply('❌ The configured bot owner must open a private chat and send /start before this request can be delivered.')
    logging.info('[DELETE] %s request %s submitted by %s', action, request['request_id'], message.from_user.id)
    if action == 'series':
        found = f"🔎 Found <b>{html.escape(request['series'])}</b> with <b>{len(documents)}</b> associated character(s)."
    else:
        found = (
            f"🔎 Found <b>{html.escape(request['character_name'])}</b> "
            f"(<code>{request['document_ids'][0]}</code>) from <b>{html.escape(request['series'])}</b>."
        )
    await message.reply(found + '\n\n⏳ Sent to the configured bot owner for private confirmation. The request expires in 5 minutes.', parse_mode=ParseMode.HTML)


def _show_match_list(matches, action):
    title = 'series' if action == 'series' else 'characters'
    lines = [f'⚠️ Multiple {title} matched. Re-run using one displayed ID:']
    for document in matches[:10]:
        lines.append(f'• <b>{html.escape(document.get("name", _character_series(document)))}</b> — <code>{_document_id(document)}</code> ({html.escape(_character_series(document))})')
    return '\n'.join(lines)


async def _delete_command(client, message, action):
    if getattr(client, 'is_clone', False):
        return await message.reply('❌ Database deletion is disabled on cloned bot instances.')
    if not message.from_user or not await is_admin(message.from_user.id):
        logging.warning('[DELETE] Unauthorized /del%s attempt by %s', action, getattr(message.from_user, 'id', None))
        return await message.reply('Admin only.')
    if not database.USE_MONGO or database.COL_DELETION_REQUESTS is None:
        return await message.reply('❌ The configured database is unavailable; no deletion request was created.')
    if len(message.command or []) < 2:
        noun = 'series name or ID' if action == 'series' else 'character name or ID'
        return await message.reply(f'Usage: <code>/del{action} &lt;{noun}&gt;</code>', parse_mode=ParseMode.HTML)
    term = message.text.split(maxsplit=1)[1].strip()
    matches = _find_character_matches(term, series=(action == 'series'))
    if not matches:
        return await message.reply('❌ No active matching record was found.')
    if action == 'character':
        if len(matches) != 1:
            return await message.reply(_show_match_list(matches, action), parse_mode=ParseMode.HTML)
        return await _request_owner_confirmation(client, message, action, matches)

    # A series is represented by its characters in this project (there is no
    # separate series table). Only a single exact source/series group is safe.
    sources = {document['_source_collection'] for document in matches}
    series_names = {_character_series(document).casefold() for document in matches}
    if len(sources) != 1 or len(series_names) != 1:
        return await message.reply(_show_match_list(matches, action), parse_mode=ParseMode.HTML)
    return await _request_owner_confirmation(client, message, action, matches)


@Client.on_message(filters.command('delchar'))
async def delchar_command(client, message):
    await _delete_command(client, message, 'character')


@Client.on_message(filters.command('delseries'))
async def delseries_command(client, message):
    await _delete_command(client, message, 'series')


async def _notify_deletion_request(client, request, text):
    try:
        await client.send_message(request['requester_chat_id'], text, parse_mode=ParseMode.HTML)
    except Exception as exc:
        logging.warning('[DELETE] Could not notify requester %s: %s', request['requester']['id'], exc)


@Client.on_callback_query(filters.regex(r'^delete:([a-f0-9]{16}):(confirm|cancel)$'))
async def deletion_confirmation_callback(client, callback_query: CallbackQuery):
    if not callback_query.from_user or callback_query.from_user.id != config.OWNER_ID:
        return await callback_query.answer('Only the configured bot owner can confirm this deletion.', show_alert=True)
    # Confirmation is deliberately accepted only from the configured owner’s
    # private chat; an owner button forwarded to a group is not sufficient.
    if callback_query.message.chat.id != config.OWNER_ID:
        return await callback_query.answer('Confirmation must happen in the owner’s private chat.', show_alert=True)
    request_id, action = callback_query.matches[0].group(1), callback_query.matches[0].group(2)
    now = datetime.now(timezone.utc)
    claimed = database.COL_DELETION_REQUESTS.update_one(
        {'request_id': request_id, 'status': 'pending', 'expires_at': {'$gt': now}},
        {'$set': {'status': 'processing', 'owner_id': callback_query.from_user.id, 'processing_at': now}},
    )
    if claimed.modified_count != 1:
        return await callback_query.answer('This request is expired or has already been handled.', show_alert=True)
    request = database.COL_DELETION_REQUESTS.find_one({'request_id': request_id})
    if action == 'cancel':
        database.COL_DELETION_REQUESTS.update_one({'request_id': request_id}, {'$set': {'status': 'cancelled', 'completed_at': now}})
        logging.info('[DELETE] Request %s cancelled by owner', request_id)
        await log_event(client, 'DELETE REQUEST CANCELLED', actor=callback_query.from_user, details=f"Request {request_id} • {request['action']}")
        await _notify_deletion_request(client, request, '❌ Your deletion request was cancelled by the bot owner.')
        await callback_query.edit_message_text('❌ <b>Deletion cancelled.</b>', parse_mode=ParseMode.HTML)
        return await callback_query.answer('Cancelled.')
    try:
        outcome, count, references = _write_delete_action(request)
        database.COL_DELETION_REQUESTS.update_one(
            {'request_id': request_id},
            {'$set': {'status': 'completed', 'completed_at': datetime.now(timezone.utc), 'outcome': outcome,
                      'affected_records': count, 'reference_count': references}},
        )
        verb = 'archived' if outcome == 'archived' else 'deleted'
        logging.info('[DELETE] Request %s approved: %s %s record(s), refs=%s', request_id, verb, count, references)
        await log_event(client, 'DELETE REQUEST COMPLETED', actor=callback_query.from_user, details=f"Request {request_id} • {count} record(s) {verb} • references: {references}")
        await _notify_deletion_request(
            client, request,
            f'✅ Owner approved your request. {count} record(s) {verb}. ' +
            ('They were archived to preserve existing game/player references.' if outcome == 'archived' else ''),
        )
        await callback_query.edit_message_text(
            f'✅ <b>Deletion approved.</b> {count} record(s) {verb}.', parse_mode=ParseMode.HTML,
        )
        await callback_query.answer('Deletion completed.')
    except Exception as exc:
        logging.exception('[DELETE] Request %s failed: %s', request_id, exc)
        database.COL_DELETION_REQUESTS.update_one(
            {'request_id': request_id},
            {'$set': {'status': 'failed', 'completed_at': datetime.now(timezone.utc), 'failure': str(exc)[:300]}},
        )
        await _notify_deletion_request(client, request, '❌ The approved deletion could not be completed. No further action was taken; check bot logs.')
        await callback_query.edit_message_text('❌ <b>Deletion failed.</b> The requester has been notified.', parse_mode=ParseMode.HTML)
        await callback_query.answer('Deletion failed.', show_alert=True)


def _parse_anime_add(lines):
    if len(lines) < 11:
        raise ValueError("Invalid format. Expected command line, anime, image URL, then stats.")
    char_name = lines[0].split(maxsplit=1)[1].strip()
    anime_name = lines[1].strip()
    img_url = lines[2].strip()
    stats = {
        "captain": int(lines[3]),
        "vice_captain": int(lines[4]),
        "tank": int(lines[5]),
        "healer": int(lines[6]),
        "assassin": int(lines[7]),
        "support": int(lines[8]),
        "traitor": int(lines[10]),
    }
    return {"name": char_name, "series": anime_name, "img": img_url, "stats": stats}

def _parse_comic_add(lines):
    if len(lines) < 11:
        raise ValueError("Invalid format. Expected command line, series, image URL, then 8 stats.")
    char_name = lines[0].split(maxsplit=1)[1].strip()
    series_name = lines[1].strip()
    img_url = lines[2].strip()
    stats = {
        "paragon": int(lines[3]),
        "genius": int(lines[4]),
        "powerhouse": int(lines[5]),
        "mystic": int(lines[6]),
        "street_level": int(lines[7]),
        "cosmic": int(lines[8]),
        "trickster": int(lines[9]),
        "herald": int(lines[10]),
    }
    return {"name": char_name, "series": series_name, "img": img_url, "stats": stats}

def _save_anime_add(char_doc):
    char_name = char_doc["name"]
    anime_name = char_doc["series"]
    img_url = char_doc["img"]
    stats = char_doc["stats"]

    unique_id = f"{char_name} | {anime_name}"
    global_state.CHAR_STATS[unique_id] = stats
    global_state.CHAR_IMAGES[unique_id] = img_url
    if char_name not in global_state.ANIME_CHARACTERS:
        global_state.ANIME_CHARACTERS.append(char_name)

    norm = re.sub(r'[^a-z0-9]', '', anime_name.lower())
    global_state.SERIES_MAP.setdefault(norm, [])
    if unique_id not in global_state.SERIES_MAP[norm]:
        global_state.SERIES_MAP[norm].append(unique_id)
    global_state.SERIES_DISPLAY[norm] = anime_name

    char_doc = {**char_doc, "kind": "anime"}
    if database.USE_MONGO and database.COL_CHARACTERS is not None:
        database.COL_CHARACTERS.update_one(
            {"name": char_name, "series": anime_name},
            {"$set": char_doc},
            upsert=True,
        )
    else:
        data = []
        if os.path.exists(database.CHARACTERS_FILE):
            with open(database.CHARACTERS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
        data = [
            item for item in data
            if (item.get("name"), item.get("series", "Unknown")) != (char_name, anime_name)
        ]
        data.append(char_doc)
        with open(database.CHARACTERS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f)

def _save_comic_add(char_doc):
    char_name = char_doc["name"]
    series_name = char_doc["series"]
    img_url = char_doc["img"]
    stats = char_doc["stats"]

    unique_id = f"{char_name} | Marvel"
    global_state.CHAR_STATS[unique_id] = stats
    global_state.CHAR_IMAGES[unique_id] = img_url
    if unique_id not in global_state.MARVEL_CHARACTERS:
        global_state.MARVEL_CHARACTERS.append(unique_id)

    norm = re.sub(r'[^a-z0-9]', '', series_name.lower())
    global_state.MARVEL_SERIES_MAP.setdefault(norm, [])
    if unique_id not in global_state.MARVEL_SERIES_MAP[norm]:
        global_state.MARVEL_SERIES_MAP[norm].append(unique_id)
    global_state.MARVEL_SERIES_DISPLAY[norm] = series_name

    char_doc = {**char_doc, "kind": "comic"}
    if database.USE_MONGO and database.COL_CHARACTERS is not None:
        database.COL_CHARACTERS.update_one(
            {"name": char_name, "series": series_name},
            {"$set": char_doc},
            upsert=True,
        )
    else:
        marvel_file = os.path.join(DATA_DIR, "marvel.json")
        data = []
        if os.path.exists(marvel_file):
            with open(marvel_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
        data = [
            item for item in data
            if (item.get("name"), item.get("series", "Unknown")) != (char_name, series_name)
        ]
        data.append(char_doc)
        with open(marvel_file, 'w', encoding='utf-8') as f:
            json.dump(data, f)

def _draft_add_caption(kind, char_doc, submitter):
    title = "Anime Character" if kind == "anime" else "Comic Character"
    stat_lines = "\n".join(f"{key}: {value}" for key, value in char_doc["stats"].items())
    return (
        f"📝 <b>Pending {title}</b>\n\n"
        f"<b>Name:</b> {html.escape(char_doc['name'])}\n"
        f"<b>Series:</b> {html.escape(char_doc['series'])}\n"
        f"<b>Image:</b> {html.escape(char_doc['img'])}\n\n"
        f"<b>Stats:</b>\n<code>{html.escape(stat_lines)}</code>\n\n"
        f"Submitted by <a href='tg://user?id={submitter['id']}'>{html.escape(submitter['name'])}</a>"
    )

async def _send_draft_add_approval(client, message, kind, char_doc):
    submitter = {"id": message.from_user.id, "name": message.from_user.first_name or str(message.from_user.id)}
    token = f"{kind}_{submitter['id']}_{int(time.time())}_{len(_pending_draft_adds)}"
    _pending_draft_adds[token] = {"kind": kind, "char": char_doc, "submitter": submitter}
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"draftadd:{kind}:approve:{token}"),
        InlineKeyboardButton("❌ Reject", callback_data=f"draftadd:{kind}:reject:{token}"),
    ]])
    caption = _draft_add_caption(kind, char_doc, submitter)
    try:
        await client.send_photo(
            chat_id=config.CHAR_APPROVER_ID,
            photo=char_doc["img"],
            caption=caption + "\n\n⚠️ <b>Pending Admin Approval</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )
    except Exception:
        await client.send_message(
            chat_id=config.CHAR_APPROVER_ID,
            text=caption + "\n\n⚠️ <b>Pending Admin Approval</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
            disable_web_page_preview=False,
        )
    await message.reply(
        f"⏳ <b>{html.escape(char_doc['name'])}</b> was sent to owner approval.",
        parse_mode=ParseMode.HTML,
    )

async def _edit_draft_add_approval(callback_query, text):
    try:
        if callback_query.message.photo:
            await callback_query.edit_message_caption(caption=text, parse_mode=ParseMode.HTML)
        else:
            await callback_query.edit_message_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=False)
    except Exception:
        pass

@Client.on_callback_query(filters.regex(r"^draftadd:(anime|comic):(approve|reject):([^:]+)$"))
async def draft_add_approval_cb(client, callback_query: CallbackQuery):
    if callback_query.from_user.id != config.CHAR_APPROVER_ID:
        return await callback_query.answer("Only the owner approver can use this.", show_alert=True)

    kind = callback_query.matches[0].group(1)
    action = callback_query.matches[0].group(2)
    token = callback_query.matches[0].group(3)
    pending = _pending_draft_adds.get(token)
    if not pending:
        return await callback_query.answer("This approval request expired.", show_alert=True)

    char_doc = pending["char"]
    submitter = pending["submitter"]
    caption = _draft_add_caption(kind, char_doc, submitter)

    if action == "reject":
        _pending_draft_adds.pop(token, None)
        await _edit_draft_add_approval(
            callback_query,
            caption + f"\n\n❌ <b>Rejected by {html.escape(callback_query.from_user.first_name)}</b>",
        )
        return await callback_query.answer("Rejected.")

    try:
        if kind == "comic":
            _save_comic_add(char_doc)
        else:
            _save_anime_add(char_doc)
    except Exception as exc:
        return await callback_query.answer(f"Save failed: {exc}", show_alert=True)

    _pending_draft_adds.pop(token, None)
    await log_event(
        client,
        'CHARACTER ADD APPROVED',
        actor=callback_query.from_user,
        chat=callback_query.message.chat,
        details=f"{kind} • {char_doc['name']} • {char_doc['series']}",
    )
    await _edit_draft_add_approval(
        callback_query,
        caption + f"\n\n✅ <b>Approved by {html.escape(callback_query.from_user.first_name)}</b>",
    )
    await callback_query.answer("Approved and saved.")

@Client.on_message(filters.command("acast"))
async def acast_cmd(c, m):
    # permission logic differs between main bot and clones
    database.set_current_client(c)
    caller_id = m.from_user.id
    is_clone = getattr(c, 'is_clone', False)
    owner_id = getattr(c, 'clone_owner_id', None) if is_clone else None
    logging.info(f"[ACAST] User {caller_id} invoked /acast {'(clone)' if is_clone else '(main)'} owner={owner_id}")

    if is_clone:
        # clones allow their owner (mod) or any admin/owner
        if not (is_mod(caller_id) or await is_admin(caller_id)):
            logging.warning(f"[ACAST] Unauthorized attempt by {caller_id} on clone {owner_id}")
            return await m.reply("❌ You are not authorized to broadcast on this clone.")
    else:
        # main instance: only the configured owner/admin list, not mods
        if caller_id != OWNER_ID and caller_id not in OWNER_IDS:
            logging.warning(f"[ACAST] Unauthorized attempt by {caller_id} on main bot")
            return await m.reply("❌ Only main bot admins can use /acast on the primary bot.")

    if len(m.command) < 2:
        logging.debug(f"[ACAST] No message provided by {caller_id}")
        return await m.reply("Usage: /acast <message>")
    msg = m.text.split(maxsplit=1)[1]
    sent, failed = 0, 0
    await m.reply("📡 Sending broadcast...")
    chats = database.load_id_set(database.CHATS_FILE, client=c)
    users = database.load_id_set(database.USERS_FILE, client=c)
    targets = chats | users
    logging.info(f"[ACAST] Broadcasting to %s unique target(s): %s chats + %s user DMs (owner=%s)", len(targets), len(chats), len(users), owner_id)
    for chat_id in targets:
        try:
            await c.send_message(chat_id, f"📢 <b>Broadcast</b>\n\n{msg}", parse_mode=ParseMode.HTML)
            sent += 1
        except Exception as exc:
            failed += 1
            logging.debug(f"[ACAST] send_message failed for chat {chat_id}: {exc}")
    logging.info(f"[ACAST] Broadcast finished: sent={sent}, failed={failed}")
    await m.reply(f"✅ Sent: {sent} | ❌ Failed: {failed}")

@Client.on_message(filters.command("add"))
async def add_char_cmd(c, m):
    logging.info(f"[ADMIN] /add invoked by {m.from_user.id}")
    if getattr(c, 'is_clone', False):
        logging.warning(f"[ADMIN] User {m.from_user.id} attempted /add on clone instance (blocked)")
        return await m.reply("❌ This command is disabled on cloned instances.")
    lines = m.text.split('\n')
    try:
        char_doc = _parse_anime_add(lines)
    except Exception as e:
        return await m.reply(f"Error: {e}\nUsage: /add Character Name\nAnime\nImage URL\n<8 stats>")

    if not await is_admin(m.from_user.id):
        return await _send_draft_add_approval(c, m, "anime", char_doc)

    try:
        _save_anime_add(char_doc)
        await log_event(c, 'DRAFT CHARACTER ADDED', actor=m.from_user, chat=m.chat, details=f"{char_doc['name']} • {char_doc['series']}")
        return await m.reply(
            f"Added <b>{html.escape(char_doc['name'])}</b> from {html.escape(char_doc['series'])}!",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        return await m.reply(f"Error: {e}")

@Client.on_message(filters.command("addpoints"))
async def addpoints_cmd(c, m):
    logging.info(f"[ADMIN] /addpoints invoked by {m.from_user.id}")
    if getattr(c, 'is_clone', False):
        logging.warning(f"[ADMIN] User {m.from_user.id} attempted /addpoints on clone instance (blocked)")
        return await m.reply("❌ This command is disabled on cloned instances.")
    if not await is_admin(m.from_user.id):
        return await m.reply("Admin only.")
    try:
        _, uid, pts = m.text.split()
        uid, pts = int(uid), int(pts)
        stats = database.get_user_stats(uid)
        stats["rating"] = max(0, stats.get("rating", 1200) + pts)
        database.save_user_stats(uid, stats)
        await m.reply(f"✅ Added {pts} points to user {uid}.")
    except: await m.reply("Usage: /addpoints <uid> <points>")

@Client.on_message(filters.command("newyear"))
async def newyear_cmd(c, m):
    logging.info(f"[ADMIN] /newyear invoked by {m.from_user.id} with text: {m.text}")
    if getattr(c, 'is_clone', False):
        logging.warning(f"[ADMIN] User {m.from_user.id} attempted /newyear on clone instance (blocked)")
        return await m.reply("❌ This command is disabled on cloned instances.")
    if not await is_admin(m.from_user.id):
        return await m.reply("Admin only.")
    arg = m.text.split()[1].lower() if len(m.text.split()) > 1 else ""
    if arg in ("on", "true", "yes"):
        global_state.NEW_YEAR_EVENT = True
        await m.reply("✅ New Year Event: ON")
    else:
        global_state.NEW_YEAR_EVENT = False
        await m.reply("❌ New Year Event: OFF")
    
    with open(database.EVENT_FLAGS_FILE, "w") as f:
        json.dump({"NEW_YEAR_EVENT": global_state.NEW_YEAR_EVENT}, f)

@Client.on_message(filters.command("comicimg"))
async def comicimg_cmd(c, m):
    logging.info(f"[ADMIN] /comicimg invoked by {m.from_user.id}")
    if getattr(c, 'is_clone', False):
        logging.warning(f"[ADMIN] User {m.from_user.id} attempted /comicimg on clone instance (blocked)")
        return await m.reply("❌ This command is disabled on cloned instances.")
    if not await is_admin(m.from_user.id):
        return await m.reply("Admin only.")
    """Update character image: /comicimg "url" "name" "series name" """
    try:
        parts = m.text.split('"')
        if len(parts) < 6:
            return await m.reply('Usage: /comicimg "url" "name" "series name"')
        
        img_url = parts[1]
        char_name = parts[3]
        series_name = parts[5]
        
        unique_id = f"{char_name} | Marvel"
        global_state.CHAR_IMAGES[unique_id] = img_url
        
        # Update in stats database too if character exists
        if unique_id in global_state.CHAR_STATS:
            if database.USE_MONGO and database.COL_CHARACTERS:
                database.COL_CHARACTERS.update_one({"name": char_name, "series": series_name}, {"$set": {"img": img_url}}, upsert=False)
            else:
                marvel_file = os.path.join(DATA_DIR, "marvel.json")
                if os.path.exists(marvel_file):
                    with open(marvel_file, 'r') as f: data = json.load(f)
                    for char in data:
                        if char.get("name") == char_name and char.get("series") == series_name:
                            char["img"] = img_url
                            break
                    with open(marvel_file, 'w') as f: json.dump(data, f)
        
        await m.reply(f"✅ Updated image for <b>{char_name}</b>", parse_mode=ParseMode.HTML)
    except Exception as e:
        await m.reply(f"❌ Error: {e}")

@Client.on_message(filters.command("add_comic"))
async def add_comic_cmd(c, m):
    logging.info(f"[ADMIN] /add_comic invoked by {m.from_user.id}")
    if getattr(c, 'is_clone', False):
        logging.warning(f"[ADMIN] User {m.from_user.id} attempted /add_comic on clone instance (blocked)")
        return await m.reply("❌ This command is disabled on cloned instances.")
    lines = m.text.split('\n')
    try:
        char_doc = _parse_comic_add(lines)
    except Exception as e:
        return await m.reply(f"Error: {e}\nUsage: /add_comic Character Name\nSeries\nImage URL\n<8 stats>")

    if not await is_admin(m.from_user.id):
        return await _send_draft_add_approval(c, m, "comic", char_doc)

    try:
        _save_comic_add(char_doc)
        await log_event(c, 'COMIC CHARACTER ADDED', actor=m.from_user, chat=m.chat, details=f"{char_doc['name']} • {char_doc['series']}")
        return await m.reply(
            f"Added <b>{html.escape(char_doc['name'])}</b> from {html.escape(char_doc['series'])}!",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        return await m.reply(f"Error: {e}")

@Client.on_message(filters.command("characters"))
async def characters_cmd(c, m):
    if getattr(c, 'is_clone', False):
        logging.warning(f"[ADMIN] User {m.from_user.id} attempted /characters on clone instance (blocked)")
        return await m.reply("❌ This command is disabled on cloned instances.")
    if not await is_admin(m.from_user.id):
        return await m.reply("Admin only.")
    """Show all characters with toggle between anime/comics - admin only"""
    mode = "anime"
    if len(m.text.split()) > 1 and m.text.split()[1].lower() == "comics":
        mode = "comics"
    
    if mode == "anime":
        if not global_state.SERIES_DISPLAY:
            return await m.reply_text("📚 No anime series found.")
        all_series = sorted(set(global_state.SERIES_DISPLAY.items()))  # Get tuples of (normalized, display)
    else:  # comics
        if not global_state.MARVEL_SERIES_DISPLAY:
            return await m.reply_text("🎬 No comics found.")
        all_series = sorted(set(global_state.MARVEL_SERIES_DISPLAY.items()))
    
    txt = f"{'🎭 Anime Series' if mode == 'anime' else '🎬 Comics'}\n(Click to see characters)\n\n"
    
    kb = []
    buttons_in_row = 2
    row = []
    for norm_name, display_name in all_series:
        row.append(InlineKeyboardButton(display_name, callback_data=f"char_{mode}_{norm_name}"))
        if len(row) == buttons_in_row:
            kb.append(row)
            row = []
    if row:
        kb.append(row)
    
    # Add toggle button
    toggle_btn = InlineKeyboardButton("🎬 Comics", callback_data="char_comics_list") if mode == "anime" else InlineKeyboardButton("🎭 Anime", callback_data="char_anime_list")
    kb.append([toggle_btn])
    
    await m.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb) if kb else None)

@Client.on_message(filters.command("sudo") & filters.create(lambda _, __, m: is_owner(m.from_user.id)))
async def sudo_cmd(c, m):
    """Promote/Demote moderators - Admin only"""
    if getattr(c, 'is_clone', False):
        logging.warning(f"[ADMIN] User {m.from_user.id} attempted /sudo on clone instance (blocked)")
        return await m.reply("❌ This command is disabled on cloned instances.")
    
    # Check if replying to a user
    reply = m.reply_to_message
    
    # Parse command arguments
    args = m.text.split()
    if len(args) < 2:
        return await m.reply_text(
            "❌ **Usage:**\n"
            "`/sudo promote` - Reply to a user to promote\n"
            "`/sudo demote` - Reply to a user to demote\n"
            "Or: `/sudo promote @username` / `/sudo demote @username`",
            parse_mode=ParseMode.MARKDOWN
        )
    
    action = args[1].lower()
    
    if action not in ["promote", "demote"]:
        return await m.reply_text("❌ Use `promote` or `demote`", parse_mode=ParseMode.MARKDOWN)
    
    # Get target user
    target_user = None
    
    if reply:
        target_user = reply.from_user
    else:
        # Try to get from username
        if len(args) >= 3:
            username = args[2].replace("@", "")
            try:
                target_user = await c.get_users(username)
            except:
                return await m.reply_text("❌ User not found!")
    
    if not target_user:
        return await m.reply_text("❌ Please reply to a user or provide username!")
    
    user_id = target_user.id
    username = target_user.username or "No username"
    first_name = target_user.first_name or "Unknown"
    
    # Handle promote
    if action == "promote":
        # Check if already mod
        existing_mod = database.get_mod(user_id)
        if existing_mod:
            return await m.reply_text(f"ℹ️ {first_name} is already a moderator!")
        
        # Add as mod
        success = database.add_mod(
            user_id=user_id,
            username=target_user.username,
            first_name=first_name,
            added_by=m.from_user.id
        )
        
        if success:
            await m.reply_text(
                f"✅ **Promoted to Moderator!**\n\n"
                f"User: {first_name}\n"
                f"ID: `{user_id}`\n"
                f"Username: @{target_user.username if target_user.username else 'N/A'}",
                parse_mode=ParseMode.MARKDOWN
            )
            # Notify
            try:
                await c.send_message(
                    user_id,
                    "🎉 You have been promoted to moderator!"
                )
            except:
                pass
        else:
            await m.reply_text("❌ Failed to promote!")
    
    # Handle demote
    elif action == "demote":
        # Remove mod
        success = database.remove_mod(user_id)
        
        if success:
            await m.reply_text(
                f"✅ **Demoted from Moderator!**\n\n"
                f"User: {first_name}\n"
                f"ID: `{user_id}`",
                parse_mode=ParseMode.MARKDOWN
            )
            # Notify
            try:
                await c.send_message(
                    user_id,
                    "⚠️ You have been demoted from moderator."
                )
            except:
                pass
        else:
            await m.reply_text("❌ Failed to demote or user wasn't a mod!")

@Client.on_message(filters.command("mods"))
async def mods_list_cmd(c, m):
    if getattr(c, 'is_clone', False):
        logging.warning(f"[ADMIN] User {m.from_user.id} attempted /mods on clone instance (blocked)")
        return await m.reply("❌ This command is disabled on cloned instances.")
    if not await is_admin(m.from_user.id):
        return await m.reply("Admin only.")
    """List all moderators"""
    mods = database.get_all_mods()
    
    if not mods:
        return await m.reply_text("📋 No moderators found.")
    
    text = "👥 **Moderators List:**\n\n"
    for i, mod in enumerate(mods, 1):
        user_id = mod.get("user_id")
        username = mod.get("username", "No username")
        first_name = mod.get("first_name", "Unknown")
        added_by = mod.get("added_by", "Unknown")
        
        text += f"{i}. **{first_name}**\n"
        text += f"   ├ ID: `{user_id}`\n"
        text += f"   ├ Username: @{username}\n"
        text += f"   └ Added by: `{added_by}`\n\n"
    
    await m.reply_text(text, parse_mode=ParseMode.MARKDOWN)
