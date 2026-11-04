"""$20 blind auction game. All mutable state lives in the game MongoDB."""

import asyncio
import hashlib
import html
import logging
import random
import re
import time
import uuid
from datetime import datetime, timezone

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import config
import database
from plugins.audit import log_event
from utils import is_admin


_LOCKS = {}
_TASKS = {}


def _now():
    return datetime.now(timezone.utc)


def _lock(auction_id):
    return _LOCKS.setdefault(auction_id, asyncio.Lock())


def _player(game, user_id):
    return next((player for player in game['players'] if player['id'] == user_id), None)


def _other(game, user_id):
    return next(player for player in game['players'] if player['id'] != user_id)


def _character(game, pool_id):
    return next((char for char in game['queue'] if char['pool_id'] == pool_id), None)


def _save(game):
    """Optimistic persistent update; callbacks are additionally serialized per game."""
    previous_version = game.get('version', 0)
    game['version'] = previous_version + 1
    game['updated_at'] = _now()
    result = database.COL_AUCTIONS.replace_one(
        {'auction_id': game['auction_id'], 'version': previous_version}, game,
    )
    if result.modified_count != 1:
        raise RuntimeError('Auction state changed. Please try again.')


def _read(auction_id):
    return database.COL_AUCTIONS.find_one({'auction_id': auction_id})


def _image_url(char):
    """Read only the image field from the legacy source database."""
    source = database.COL_IMAGE_CHARACTERS
    if source is None:
        return None
    try:
        document = source.find_one(
            {
                'name': {'$regex': f'^{re.escape(char["image_lookup"])}$', '$options': 'i'},
                'series': {'$regex': f'^{re.escape(char["anime"])}$', '$options': 'i'},
            },
            {'img_url': 1, 'img': 1},
        ) or {}
        return document.get('img_url') or document.get('img')
    except Exception as exc:
        logging.info('[AUCTION] Image lookup failed for %s: %s', char['name'], exc)
        return None


def _public_card(char, game, text):
    image_url = char.get('image_url')
    image_link = (
        f'<a href="{html.escape(image_url, quote=True)}">&#160;</a>'
        if isinstance(image_url, str) and image_url.startswith(('http://', 'https://')) else ''
    )
    return (
        image_link +
        f'🔥 <b>$20 BLIND AUCTION</b>\n'
        f'<b>Character {game["queue_index"] + 1}/{len(game["queue"])}:</b> {html.escape(char["name"])}\n'
        f'<i>{html.escape(char["anime"])} • Tier {html.escape(char["tier"])}</i>\n\n{text}'
    )


async def _upsert_auction_display(client, game, caption, markup=None, preview=False):
    """Use one editable public auction board, rather than a message per bid/card.

    A hidden non-breaking-space link enables Telegram's image preview when a
    revealed card has artwork, while still allowing the same message to be
    edited for every auction round.
    """
    message_id = game.get('display_message_id')
    try:
        if message_id:
            await client.edit_message_text(
                game['chat_id'], message_id, caption, parse_mode=ParseMode.HTML,
                reply_markup=markup, disable_web_page_preview=not preview,
            )
            return
        message = await client.send_message(
            game['chat_id'], caption, parse_mode=ParseMode.HTML,
            reply_markup=markup, disable_web_page_preview=not preview,
        )
        game['display_message_id'] = message.id
        _save(game)
    except Exception as exc:
        # Telegram rejects no-op edits; that is not a game error.
        if 'MESSAGE_NOT_MODIFIED' not in str(exc).upper():
            logging.warning('[AUCTION] Could not update live board %s: %s', message_id, exc)


def _bid_markup(game):
    aid = game['auction_id']
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('➕ $1', callback_data=f'auc:{aid}:b1'), InlineKeyboardButton('➕ $5', callback_data=f'auc:{aid}:b5')],
        [InlineKeyboardButton('💰 Max bid', callback_data=f'auc:{aid}:max'), InlineKeyboardButton('✍️ Custom: /bid amount', callback_data=f'auc:{aid}:custom')],
        [InlineKeyboardButton('⏭ Pass', callback_data=f'auc:{aid}:pass')],
    ])


def _team_text(player):
    names = ', '.join(html.escape(char['name']) for char in player['team']) or '—'
    return f'<b>{html.escape(player["name"])}</b> (${player["budget"]} left): {names}'


def _assign(player, char, price=0):
    if len(player['team']) >= config.AUCTION_TEAM_SIZE:
        raise RuntimeError('Team capacity reached.')
    player['team'].append(char)
    player['budget'] -= price
    player['spent'] += price


def _complete_remaining(game):
    """Randomly fill all remaining/unsold slots without exposing future cards."""
    # The current card can already be in ``unsold`` when this helper is called
    # after an auction round, so de-duplicate by the auction-only pool key.
    remaining = []
    seen = {char['pool_id'] for player in game['players'] for char in player['team']}
    for char in game['queue'][game['queue_index']:] + game.get('unsold', []):
        if char['pool_id'] not in seen:
            seen.add(char['pool_id'])
            remaining.append(char)
    random.Random(game['seed'] + game['queue_index']).shuffle(remaining)
    for char in remaining:
        available = [p for p in game['players'] if len(p['team']) < config.AUCTION_TEAM_SIZE]
        if not available:
            break
        target = available[0] if len(available) == 1 else random.choice(available)
        _assign(target, char)
    game['queue_index'] = len(game['queue'])
    game['unsold'] = []


def _prepare_next(game):
    """Choose the next state without sending messages or touching the old DB."""
    while game['queue_index'] < len(game['queue']):
        char = game['queue'][game['queue_index']]
        first, second = game['players']
        if len(first['team']) >= config.AUCTION_TEAM_SIZE:
            _assign(second, char)
            game['queue_index'] += 1
            continue
        if len(second['team']) >= config.AUCTION_TEAM_SIZE:
            _assign(first, char)
            game['queue_index'] += 1
            continue
        broke = [p for p in game['players'] if p['budget'] <= 0]
        if len(broke) == 2:
            _complete_remaining(game)
            game['phase'] = 'auction_complete'
            return 'complete', None
        if len(broke) == 1:
            controller = _other(game, broke[0]['id'])
            game.update({'phase': 'distribution', 'distribution_controller': controller['id'], 'current_bid': None, 'passed': []})
            return 'distribution', char
        game.update({
            'phase': 'bidding', 'current_bid': None, 'passed': [],
            'round_ends_at': time.time() + config.AUCTION_BID_TIMEOUT_SECONDS,
        })
        return 'bidding', char
    if game.get('unsold'):
        _complete_remaining(game)
    game['phase'] = 'auction_complete'
    return 'complete', None


def _settle_current(game):
    char = game['queue'][game['queue_index']]
    bid = game.get('current_bid')
    if bid:
        winner = _player(game, bid['user_id'])
        _assign(winner, char, bid['amount'])
        outcome = f'🏆 <b>{html.escape(winner["name"])}</b> wins <b>{html.escape(char["name"])}</b> for <b>${bid["amount"]}</b>.'
    else:
        game.setdefault('unsold', []).append(char)
        outcome = f'⏭ <b>{html.escape(char["name"])}</b> is unsold and will be distributed later.'
    game['queue_index'] += 1
    game['current_bid'] = None
    game['passed'] = []
    return outcome


async def _publish_state(client, game, prefix=''):
    mode, char = _prepare_next(game)
    _save(game)
    if mode == 'complete':
        return await _auction_complete(client, game)
    if mode == 'distribution':
        await _upsert_auction_display(
            client, game, _public_card(char, game, prefix + '\n🎲 One player will privately assign this revealed character.'),
            preview=bool(char.get('image_url')),
        )
        return await _send_distribution_dm(client, game)
    bid = game.get('current_bid')
    bid_text = 'Starting bid: <b>$1</b>' if not bid else f'Highest bid: <b>${bid["amount"]}</b> by <b>{html.escape(_player(game, bid["user_id"])["name"])}</b>'
    body = f'{prefix}\n{bid_text}\n⏳ {config.AUCTION_BID_TIMEOUT_SECONDS}s to bid or pass.\n\n{_team_text(game["players"][0])}\n{_team_text(game["players"][1])}'
    await _upsert_auction_display(
        client, game, _public_card(char, game, body), _bid_markup(game), preview=bool(char.get('image_url')),
    )
    _schedule_bid_timeout(client, game)


def _schedule_bid_timeout(client, game):
    key = f'bid:{game["auction_id"]}'
    old = _TASKS.pop(key, None)
    if old and old is not asyncio.current_task():
        old.cancel()
    _TASKS[key] = asyncio.create_task(_bid_timeout(client, game['auction_id'], game['queue_index'], game['round_ends_at']))


async def _bid_timeout(client, auction_id, index, ends_at):
    try:
        await asyncio.sleep(max(0, ends_at - time.time()))
        async with _lock(auction_id):
            game = _read(auction_id)
            if not game or game.get('phase') != 'bidding' or game['queue_index'] != index or game.get('round_ends_at') != ends_at:
                return
            outcome = _settle_current(game)
            await _publish_state(client, game, outcome)
    except asyncio.CancelledError:
        return
    except Exception:
        logging.exception('[AUCTION] Bid timeout failed for %s', auction_id)


async def _send_distribution_dm(client, game):
    controller = _player(game, game['distribution_controller'])
    char = game['queue'][game['queue_index']]
    targets = []
    for player in game['players']:
        if len(player['team']) < config.AUCTION_TEAM_SIZE:
            label = 'Assign to My Team' if player['id'] == controller['id'] else 'Assign to Opponent Team'
            targets.append(InlineKeyboardButton(label, callback_data=f'aucd:{game["auction_id"]}:{player["id"]}'))
    try:
        await client.send_message(controller['id'], f'🎲 <b>Assign {html.escape(char["name"])} privately.</b>\nChoose its team; later characters remain hidden.', parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([targets]))
    except Exception:
        await client.send_message(game['chat_id'], f'⚠️ {html.escape(controller["name"])} must send <code>/start</code> to me in private to continue the auction.', parse_mode=ParseMode.HTML)


async def _auction_complete(client, game):
    if any(len(player['team']) != config.AUCTION_TEAM_SIZE for player in game['players']):
        game['phase'] = 'error'
        _save(game)
        return await client.send_message(game['chat_id'], '❌ Auction allocation could not form two complete teams.')
    game.update({'status': 'battle', 'phase': 'battle_select', 'battle_round': 1, 'battle_scores': [0, 0], 'battle_results': [], 'used_fighters': [], 'hidden_selections': {}})
    _save(game)
    await _upsert_auction_display(
        client, game,
        '✅ <b>AUCTION COMPLETED – BATTLE STARTING!</b>\n\n' + '\n'.join(_team_text(p) for p in game['players']),
    )
    await _send_battle_selection(client, game)


def _eligible_fighters(game, player):
    used = set(game.get('used_fighters', [])) if game.get('battle_round', 1) <= 5 else set()
    return [char for char in player['team'] if char['pool_id'] not in used]


def _battle_picker_markup(game, player):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(char['name'], callback_data=f'aucs:{game["auction_id"]}:{char["pool_id"]}')]
        for char in _eligible_fighters(game, player)
    ])


def _score_line(game):
    first, second = game['players']
    left, right = game.get('battle_scores', [0, 0])
    return f'🏆 <b>SCORE:</b> {html.escape(first["name"])} <b>{left} – {right}</b> {html.escape(second["name"])}'


async def _upsert_battle_display(client, game, text, markup=None):
    """Reuse the auction board for the entire battle; callbacks enforce IDs."""
    message_id = game.get('battle_message_id') or game.get('display_message_id')
    try:
        if message_id:
            await client.edit_message_text(game['chat_id'], message_id, text, parse_mode=ParseMode.HTML, reply_markup=markup)
            return
        message = await client.send_message(game['chat_id'], text, parse_mode=ParseMode.HTML, reply_markup=markup)
        game['battle_message_id'] = message.id
        _save(game)
    except Exception as exc:
        if 'MESSAGE_NOT_MODIFIED' not in str(exc).upper():
            logging.warning('[AUCTION] Could not update public battle picker %s: %s', message_id, exc)


async def _send_battle_selection(client, game):
    round_no = game['battle_round']
    owner = game['players'][(round_no - 1) % 2]
    game['selection_owner'] = owner['id']
    _save(game)
    await _upsert_battle_display(
        client, game,
        _score_line(game) + '\n\n' +
        (game.get('last_battle_summary', '') + '\n\n' if game.get('last_battle_summary') else '') +
        f'⚔️ <b>ROUND {round_no}</b>\n\n'
        f'<b>{html.escape(owner["name"])}</b>, select one fighter below.\n'
        '<i>Buttons are public, but only the current player can select.</i>',
        _battle_picker_markup(game, owner),
    )
    _schedule_selection_timeout(client, game, owner['id'])


def _schedule_selection_timeout(client, game, owner_id):
    key = f'select:{game["auction_id"]}'
    old = _TASKS.pop(key, None)
    if old and old is not asyncio.current_task():
        old.cancel()
    _TASKS[key] = asyncio.create_task(_selection_timeout(client, game['auction_id'], game['battle_round'], owner_id))


async def _selection_timeout(client, auction_id, round_no, owner_id):
    try:
        await asyncio.sleep(config.AUCTION_SELECTION_TIMEOUT_SECONDS)
        async with _lock(auction_id):
            game = _read(auction_id)
            if not game or game.get('phase') != 'battle_select' or game.get('battle_round') != round_no or game.get('selection_owner') != owner_id:
                return
            player = _player(game, owner_id)
            options = _eligible_fighters(game, player)
            if not options:
                return
            await _lock_fighter(client, game, owner_id, random.choice(options)['pool_id'], automatic=True)
    except asyncio.CancelledError:
        return
    except Exception:
        logging.exception('[AUCTION] Selection timeout failed for %s', auction_id)


def _battle(first, second, seed, final=False):
    rng = random.Random(seed)
    weights = {'attack': .29, 'defense': .18, 'speed': .20, 'durability': .18, 'intelligence': .15}
    first_score = sum(first[key] * weight for key, weight in weights.items())
    second_score = sum(second[key] * weight for key, weight in weights.items())
    first_score += len(first.get('special_abilities', [])) * 4 + len(first.get('abilities', [])) * 1.5
    second_score += len(second.get('special_abilities', [])) * 4 + len(second.get('abilities', [])) * 1.5
    first_score *= rng.uniform(.88, 1.12)
    second_score *= rng.uniform(.88, 1.12)
    if not final and abs(first_score - second_score) < 2.5:
        return None, 'The fighters neutralized each other after a closely matched clash.'
    winner = 0 if first_score >= second_score else 1
    narration = 'A decisive special-ability exchange created the opening.' if abs(first_score - second_score) > 10 else 'A narrow tactical edge decided the fight.'
    return winner, narration


async def _lock_fighter(client, game, user_id, pool_id, automatic=False):
    if game.get('selection_owner') != user_id:
        raise ValueError('It is not your selection turn.')
    player = _player(game, user_id)
    char = next((item for item in _eligible_fighters(game, player) if item['pool_id'] == pool_id), None)
    if not char:
        raise ValueError('That fighter is unavailable.')
    selected = game.setdefault('hidden_selections', {})
    selected[str(user_id)] = pool_id
    other = _other(game, user_id)
    if str(other['id']) not in selected:
        game['selection_owner'] = other['id']
        _save(game)
        await _upsert_battle_display(
            client, game,
            _score_line(game) + '\n\n' +
            f'🔒 <b>{html.escape(player["name"])}</b> has locked their fighter.' +
            (' <i>(auto-selected after timeout)</i>' if automatic else '') +
            f'\n\n<b>{html.escape(other["name"])}</b>, now select one fighter below.\n'
            '<i>Only this player can use these buttons.</i>',
            _battle_picker_markup(game, other),
        )
        _schedule_selection_timeout(client, game, other['id'])
        return
    first, second = game['players']
    left, right = _character(game, selected[str(first['id'])]), _character(game, selected[str(second['id'])])
    final = game['battle_round'] > 5
    winner, narration = _battle(left, right, game['seed'] + game['battle_round'], final)
    if winner is not None:
        game['battle_scores'][winner] += 1
        winner_text = f'🏆 {html.escape(game["players"][winner]["name"])} wins this fight.'
    else:
        winner_text = '🤝 This fight is a draw.'
    game['battle_results'].append({'round': game['battle_round'], 'a': left['pool_id'], 'b': right['pool_id'], 'winner': winner, 'narration': narration})
    if not final:
        game['used_fighters'].extend([left['pool_id'], right['pool_id']])
    game['hidden_selections'] = {}
    game['last_battle_summary'] = (
        f'⚔️ <b>ROUND {game["battle_round"]} RESULT</b>\n'
        f'<b>{html.escape(first["name"])}:</b> {html.escape(left["name"])}\n'
        f'<b>VS</b>\n'
        f'<b>{html.escape(second["name"])}:</b> {html.escape(right["name"])}\n\n'
        f'{winner_text}\n<i>{html.escape(narration)}</i>'
    )
    game['battle_round'] += 1
    _save(game)
    if game['battle_round'] <= 5:
        await _send_battle_selection(client, game)
    elif game['battle_scores'][0] == game['battle_scores'][1]:
        await client.send_message(game['chat_id'], '⚡ Scores are tied. Sudden-death rematch selection begins!')
        await _send_battle_selection(client, game)
    else:
        await _finish_battle(client, game)


async def _finish_battle(client, game):
    winner_index = 0 if game['battle_scores'][0] > game['battle_scores'][1] else 1
    game.update({'status': 'completed', 'phase': 'completed', 'winner_id': game['players'][winner_index]['id'], 'completed_at': _now()})
    _save(game)
    await _upsert_battle_display(
        client, game,
        _score_line(game) + '\n\n' +
        (game.get('last_battle_summary', '') + '\n\n' if game.get('last_battle_summary') else '') +
        '🏁 <b>ANIME AUCTION – MATCH COMPLETED</b>\n\n' +
        '\n'.join(_team_text(player) for player in game['players']) +
        f'\n\n🏆 Winner: <b>{html.escape(game["players"][winner_index]["name"])}</b>',
    )
    await log_event(
        client,
        'AUCTION COMPLETED',
        details=(
            f"Auction {game['auction_id']} • group {game['chat_id']} • "
            f"{game.get('pool_anime', 'Dragon Ball')} • winner: {game['players'][winner_index]['name']}"
        ),
    )


@Client.on_message(filters.command('auction') & filters.group)
async def auction_command(client, message):
    if not database.USE_MONGO or database.COL_AUCTION_CHARACTER_POOL is None:
        return await message.reply('❌ Auction game database is unavailable.')
    database.expire_stale_auctions()
    requested_pool = ' '.join((message.command or [])[1:]).strip().casefold()
    pool_anime = 'Dragon Ball'
    roster_filter = {'enabled': True, '$or': [{'roster': 'auction_roster_v1'}, {'source': 'aucadd'}]}
    if requested_pool and requested_pool.replace(' ', '') not in {'dragonball', 'db'}:
        match = database.COL_AUCTION_CHARACTER_POOL.find_one({
            **roster_filter,
            'anime': {'$regex': f'^{re.escape(requested_pool)}$', '$options': 'i'},
        }, {'anime': 1})
        if not match:
            return await message.reply('❌ That auction series is unavailable. Use <code>/alist</code> to see the independent auction roster.', parse_mode=ParseMode.HTML)
        pool_anime = match['anime']
    creator = message.from_user
    opponent = message.reply_to_message.from_user if message.reply_to_message else None
    if not creator or (opponent and (opponent.is_bot or creator.id == opponent.id)):
        return await message.reply('❌ Choose a valid human opponent.')
    participants = [{'players.id': creator.id}]
    if opponent:
        participants.append({'players.id': opponent.id})
    active = database.COL_AUCTIONS.find_one({'status': {'$in': ['waiting', 'auction', 'battle']}, 'chat_id': message.chat.id, '$or': participants})
    if active:
        return await message.reply('❌ One of these players already has an active auction in this group.')
    aid = uuid.uuid4().hex[:12]
    game = {
        'auction_id': aid, 'chat_id': message.chat.id, 'status': 'waiting', 'phase': 'waiting', 'version': 0,
        'created_at': _now(), 'updated_at': _now(), 'seed': random.randint(1, 2**31 - 1),
        'players': [{'id': creator.id, 'name': creator.first_name, 'budget': config.AUCTION_STARTING_BUDGET, 'spent': 0, 'team': []}],
        'challenged_id': opponent.id if opponent else None,
        'challenged_name': opponent.first_name if opponent else None,
        'pool_anime': pool_anime,
        'roster': 'auction_roster_v1',
    }
    database.COL_AUCTIONS.insert_one(game)
    markup = InlineKeyboardMarkup([[InlineKeyboardButton('✅ Join Auction', callback_data=f'auc:{aid}:join'), InlineKeyboardButton('❌ Cancel', callback_data=f'auc:{aid}:cancel')]])
    challenge_text = (
        f'{html.escape(creator.first_name)} challenged {html.escape(opponent.first_name)}.'
        if opponent else f'{html.escape(creator.first_name)} opened an auction lobby. First eligible player to join becomes the opponent.'
    )
    board = await message.reply(f'🔥 <b>$20 Blind Auction Challenge</b>\n\n{challenge_text}\nEach player builds a five-character team from ten hidden upcoming cards.', parse_mode=ParseMode.HTML, reply_markup=markup)
    game['display_message_id'] = board.id
    _save(game)
    await log_event(
        client,
        'AUCTION LOBBY CREATED',
        actor=creator,
        chat=message.chat,
        details=f"Auction {aid} • series: {pool_anime} • opponent: {getattr(opponent, 'first_name', 'open lobby')}",
    )


@Client.on_callback_query(filters.regex(r'^auc:([a-f0-9]{12}):(join|cancel|b1|b5|max|pass|custom)$'))
async def auction_callback(client, query):
    aid, action = query.matches[0].group(1), query.matches[0].group(2)
    async with _lock(aid):
        game = _read(aid)
        if not game:
            return await query.answer('Auction no longer exists.', show_alert=True)
        uid = query.from_user.id
        if action == 'join':
            if game['status'] != 'waiting' or uid == game['players'][0]['id']:
                return await query.answer('This player cannot join this auction.', show_alert=True)
            if game.get('challenged_id') is not None and uid != game['challenged_id']:
                return await query.answer('Only the challenged player can join.', show_alert=True)
            missing = [player['name'] for player in game['players'] if not database.has_private_start(player['id'])]
            if not database.has_private_start(uid):
                missing.append(query.from_user.first_name)
            if missing:
                return await query.answer('Before starting, both players must open the bot in DM and send /start: ' + ', '.join(dict.fromkeys(missing)), show_alert=True)
            pool = list(database.COL_AUCTION_CHARACTER_POOL.find({
                'enabled': True,
                'anime': game.get('pool_anime', 'Dragon Ball'),
                '$or': [
                    {'roster': game.get('roster', 'auction_roster_v1')},
                    {'source': 'aucadd'},
                ],
            }, {'_id': 0}))
            if len(pool) < config.AUCTION_POOL_SIZE:
                return await query.answer('Auction pool needs at least 10 characters.', show_alert=True)
            chosen = random.sample(pool, config.AUCTION_POOL_SIZE)
            for char in chosen:
                # Manually-added auction characters own their image URL. The
                # legacy catalog is only a fallback image source.
                char['image_url'] = char.get('image_url') or _image_url(char)
            game['players'].append({'id': uid, 'name': query.from_user.first_name, 'budget': config.AUCTION_STARTING_BUDGET, 'spent': 0, 'team': []})
            game.update({'status': 'auction', 'phase': 'starting', 'queue': chosen, 'queue_index': 0, 'unsold': [], 'current_bid': None, 'passed': []})
            _save(game)
            await query.answer('Auction joined!')
            await log_event(
                client,
                'AUCTION STARTED',
                actor=query.from_user,
                chat=query.message.chat,
                details=(
                    f"Auction {aid} • series: {game.get('pool_anime', 'Dragon Ball')} • "
                    f"{game['players'][0]['name']} vs {game['players'][1]['name']}"
                ),
            )
            return await _publish_state(client, game, '✅ Both players joined. Ten independent auction-pool characters were shuffled. First reveal starting now!')
        if action == 'cancel':
            if game['status'] != 'waiting' or uid != game['players'][0]['id']:
                return await query.answer('Only the challenger can cancel before joining.', show_alert=True)
            game.update({'status': 'cancelled', 'phase': 'cancelled'})
            _save(game)
            await query.answer('Cancelled.')
            return await query.message.edit_text('❌ Auction challenge cancelled.')
        if uid not in [p['id'] for p in game.get('players', [])]:
            return await query.answer('Only auction players can bid.', show_alert=True)
        if action == 'custom':
            return await query.answer('Send /bid AMOUNT in this group, e.g. /bid 7.', show_alert=True)
        if action == 'pass':
            return await _pass(client, game, uid, query)
        amount = None
        if action == 'b1':
            amount = (game.get('current_bid') or {}).get('amount', 0) + 1
        elif action == 'b5':
            amount = (game.get('current_bid') or {}).get('amount', 0) + 5
        elif action == 'max':
            amount = _player(game, uid)['budget']
        return await _place_bid(client, game, uid, amount, query)


async def _place_bid(client, game, user_id, amount, query=None):
    if game.get('phase') != 'bidding':
        message = 'Bidding is not active.'
    elif user_id in game.get('passed', []):
        message = 'You already passed this character.'
    else:
        player, old = _player(game, user_id), game.get('current_bid')
        minimum = (old['amount'] + 1) if old else 1
        if old and old['user_id'] == user_id:
            message = 'You already hold the highest bid.'
        elif not isinstance(amount, int) or amount < minimum:
            message = f'Minimum valid bid is ${minimum}.'
        elif amount > player['budget']:
            message = f'Your remaining budget is only ${player["budget"]}.'
        else:
            game['current_bid'] = {'user_id': user_id, 'amount': amount}
            database.COL_AUCTION_BIDS.insert_one({'auction_id': game['auction_id'], 'round_index': game['queue_index'], 'bidder_id': user_id, 'amount': amount, 'created_at': _now()})
            _save(game)
            char = game['queue'][game['queue_index']]
            body = (
                f'Highest bid: <b>${amount}</b> by <b>{html.escape(player["name"])}</b>\n'
                f'⏳ Bidding closes at the end of this round.\n\n'
                f'{_team_text(game["players"][0])}\n{_team_text(game["players"][1])}'
            )
            await _upsert_auction_display(
                client, game, _public_card(char, game, body), _bid_markup(game), preview=bool(char.get('image_url')),
            )
            if query:
                return await query.answer('Bid accepted.')
            return True
    if query:
        return await query.answer(message, show_alert=True)
    return message


async def _pass(client, game, user_id, query):
    if game.get('phase') != 'bidding':
        return await query.answer('Bidding is not active.', show_alert=True)
    if user_id in game.get('passed', []):
        return await query.answer('You already passed.', show_alert=True)
    game['passed'].append(user_id)
    other = _other(game, user_id)
    bid = game.get('current_bid')
    settle = len(game['passed']) == 2 or (bid and bid['user_id'] == other['id'])
    if settle:
        outcome = _settle_current(game)
        await query.answer('Passed.')
        return await _publish_state(client, game, outcome)
    _save(game)
    await query.answer('Passed.')
    char = game['queue'][game['queue_index']]
    bid = game.get('current_bid')
    bid_text = 'No bid yet.' if not bid else f'Highest bid: <b>${bid["amount"]}</b> by <b>{html.escape(_player(game, bid["user_id"])["name"])}</b>'
    body = f'⏭ {html.escape(_player(game, user_id)["name"])} passed.\n{bid_text}\n\n{_team_text(game["players"][0])}\n{_team_text(game["players"][1])}'
    await _upsert_auction_display(
        client, game, _public_card(char, game, body), _bid_markup(game), preview=bool(char.get('image_url')),
    )


@Client.on_message(filters.command('bid') & filters.group)
async def custom_bid_command(client, message):
    try:
        amount = int(message.command[1])
    except (IndexError, ValueError):
        return await message.reply('Usage: <code>/bid AMOUNT</code>', parse_mode=ParseMode.HTML)
    game = database.COL_AUCTIONS.find_one({'chat_id': message.chat.id, 'status': 'auction', 'phase': 'bidding'})
    if not game:
        return await message.reply('No active auction bid round here.')
    async with _lock(game['auction_id']):
        game = _read(game['auction_id'])
        result = await _place_bid(client, game, message.from_user.id, amount)
        if result is not True:
            await message.reply(f'❌ {result}')


@Client.on_callback_query(filters.regex(r'^aucd:([a-f0-9]{12}):(\d+)$'))
async def distribution_callback(client, query):
    aid, target_id = query.matches[0].group(1), int(query.matches[0].group(2))
    async with _lock(aid):
        game = _read(aid)
        if not game or game.get('phase') != 'distribution' or query.from_user.id != game.get('distribution_controller'):
            return await query.answer('This assignment is unavailable.', show_alert=True)
        target = _player(game, target_id)
        if not target or len(target['team']) >= config.AUCTION_TEAM_SIZE:
            return await query.answer('That team is full.', show_alert=True)
        char = game['queue'][game['queue_index']]
        _assign(target, char)
        game['queue_index'] += 1
        await query.answer('Assigned.')
        await _publish_state(client, game, f'🎲 <b>{html.escape(char["name"])}</b> was assigned to {html.escape(target["name"])}.')


@Client.on_callback_query(filters.regex(r'^aucs:([a-f0-9]{12}):((?:db|auc)_[a-z0-9_]+)$'))
async def fighter_selection_callback(client, query):
    aid, pool_id = query.matches[0].group(1), query.matches[0].group(2)
    async with _lock(aid):
        game = _read(aid)
        if not game or game.get('phase') != 'battle_select':
            return await query.answer('This selection expired.', show_alert=True)
        try:
            await _lock_fighter(client, game, query.from_user.id, pool_id)
            await query.answer('Fighter locked.')
        except ValueError as exc:
            await query.answer(str(exc), show_alert=True)


@Client.on_message(filters.command('auction_cancel') & filters.group)
async def auction_cancel_command(client, message):
    game = database.COL_AUCTIONS.find_one({
        'chat_id': message.chat.id,
        'status': {'$in': ['waiting', 'auction', 'battle']},
        'players.id': message.from_user.id,
    })
    if not game:
        return await message.reply('You have no active auction in this group.')
    async with _lock(game['auction_id']):
        game = _read(game['auction_id'])
        if not game or game['status'] not in {'waiting', 'auction', 'battle'} or not _player(game, message.from_user.id):
            return await message.reply('This auction is no longer active.')
        if game['status'] == 'waiting' and game['players'][0]['id'] != message.from_user.id:
            return await message.reply('Only the challenger may cancel a waiting auction.')
        was_battle = game['status'] == 'battle'
        game.update({'status': 'cancelled', 'phase': 'cancelled'})
        _save(game)
        text = f'❌ <b>Auction cancelled by {html.escape(message.from_user.first_name)}.</b>'
        if was_battle:
            await _upsert_battle_display(client, game, text)
        else:
            await _upsert_auction_display(client, game, text)


@Client.on_message(filters.command('auction_stats'))
async def auction_stats_command(_, message):
    if not database.USE_MONGO:
        return await message.reply('Auction database unavailable.')
    uid = message.from_user.id
    matches = list(database.COL_AUCTIONS.find({'status': 'completed', 'players.id': uid}, {'winner_id': 1}))
    wins = sum(1 for match in matches if match.get('winner_id') == uid)
    await message.reply(f'📊 <b>Auction Stats</b>\nMatches: <b>{len(matches)}</b>\nWins: <b>{wins}</b>', parse_mode=ParseMode.HTML)


@Client.on_message(filters.command('auction_help'))
async def auction_help_command(_, message):
    await message.reply('🔥 <b>$20 Blind Auction</b>\n\nBefore joining, both players must open the bot in private DM and send <code>/start</code>. Use <code>/auction</code> in a group to open a lobby, or reply with it to challenge one player directly. Each player has $20 and builds a five-character team from ten shuffled cards. Bid with buttons or <code>/bid AMOUNT</code>. Fighter selection happens one at a time in the group; only the current player’s button tap is accepted.', parse_mode=ParseMode.HTML)


def _auction_tier(total_power):
    if total_power >= 90:
        return 'S'
    if total_power >= 75:
        return 'A'
    if total_power >= 55:
        return 'B'
    if total_power >= 35:
        return 'C'
    return 'D'


@Client.on_message(filters.command('aucadd'))
async def auction_add_character_command(_, message):
    """Add/update an independent auction-pool character, never old catalog data.

    Format:
      /aucadd Character Name
      https://image.example/character.jpg
      Series Name
      85
    """
    if not message.from_user or not await is_admin(message.from_user.id):
        return await message.reply('Admin only.')
    if not database.USE_MONGO or database.COL_AUCTION_CHARACTER_POOL is None:
        return await message.reply('❌ Auction game database is unavailable.')
    lines = [line.strip() for line in (message.text or '').splitlines()]
    if len(lines) != 4 or len(lines[0].split(maxsplit=1)) < 2:
        return await message.reply('Usage:\n<code>/aucadd Character Name\nhttps://image-url\nSeries Name\nTotal Power (1-100)</code>', parse_mode=ParseMode.HTML)
    name = lines[0].split(maxsplit=1)[1].strip()
    image_url, series = lines[1], lines[2]
    if not name or not series or not re.match(r'^https?://', image_url, re.I):
        return await message.reply('❌ Provide a character name, an http(s) image URL, and a series name.')
    try:
        total_power = int(lines[3])
    except ValueError:
        return await message.reply('❌ Total Power must be a whole number from 1 to 100.')
    if not 1 <= total_power <= 100:
        return await message.reply('❌ Total Power must be between 1 and 100.')
    identity = f'{name.casefold()}|{series.casefold()}'.encode('utf-8')
    pool_id = 'auc_' + hashlib.sha1(identity).hexdigest()[:16]
    # The provided total power is converted to balanced baseline battle stats.
    # More detailed attributes can be added later without touching old datasets.
    character = {
        'pool_id': pool_id, 'name': name, 'anime': series, 'image_url': image_url,
        'image_lookup': name, 'total_power': total_power,
        'attack': total_power, 'defense': total_power, 'speed': total_power,
        'durability': total_power, 'intelligence': total_power,
        'tier': _auction_tier(total_power), 'abilities': [], 'special_abilities': [],
        'enabled': True, 'source': 'aucadd', 'updated_at': _now(),
    }
    database.COL_AUCTION_CHARACTER_POOL.update_one(
        {'pool_id': pool_id}, {'$set': character, '$setOnInsert': {'created_at': _now()}}, upsert=True,
    )
    await log_event(
        _,
        'AUCTION CHARACTER ADDED',
        actor=message.from_user,
        chat=message.chat,
        details=f'{name} • {series} • total power {total_power}',
    )
    await message.reply(
        f'✅ Added to the independent auction pool:\n'
        f'<b>{html.escape(name)}</b> — {html.escape(series)}\n'
        f'Total Power: <b>{total_power}</b> • Tier <b>{character["tier"]}</b>',
        parse_mode=ParseMode.HTML,
    )


@Client.on_message(filters.command('alist'))
async def auction_series_list_command(_, message):
    """Public listing of series available in the independent auction roster."""
    if not database.USE_MONGO or database.COL_AUCTION_CHARACTER_POOL is None:
        return await message.reply('❌ Auction game database is unavailable.')
    rows = list(database.COL_AUCTION_CHARACTER_POOL.aggregate([
        {'$match': {'enabled': True, '$or': [{'roster': 'auction_roster_v1'}, {'source': 'aucadd'}]}},
        {'$group': {'_id': '$anime', 'characters': {'$sum': 1}}},
        {'$sort': {'_id': 1}},
    ]))
    if not rows:
        return await message.reply('No auction series are available yet.')
    lines = ['📚 <b>AUCTION SERIES</b>', '']
    lines.extend(f'• <b>{html.escape(str(row["_id"]))}</b> — {row["characters"]} character(s)' for row in rows)
    lines.append('\nAdmins can add more with <code>/aucadd</code>.')
    await message.reply('\n'.join(lines), parse_mode=ParseMode.HTML)

