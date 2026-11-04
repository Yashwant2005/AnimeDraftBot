"""CSV-backed character roster for the independent auction game."""

import csv
import hashlib
from pathlib import Path


_ROSTER_FILE = Path(__file__).with_name('auction_roster.csv')
_ROSTER_ID = 'auction_roster_v1'


def _pool_id(name, series):
    identity = f'{name.casefold()}|{series.casefold()}'.encode('utf-8')
    return 'auc_' + hashlib.sha1(identity).hexdigest()[:16]


def _tier(total_power):
    if total_power >= 90:
        return 'S'
    if total_power >= 75:
        return 'A'
    if total_power >= 55:
        return 'B'
    if total_power >= 35:
        return 'C'
    return 'D'


def _load_roster():
    """Convert the checked-in auction CSV into safe game-pool records."""
    characters = []
    with _ROSTER_FILE.open(encoding='utf-8-sig', newline='') as roster:
        for row in csv.DictReader(roster):
            name = (row.get('Character Name') or '').strip()
            series = (row.get('Series') or '').strip()
            image_url = (row.get('Image URL') or '').strip()
            try:
                total_power = int((row.get('Total Power') or '').strip())
            except ValueError:
                continue
            if not name or not series or not 1 <= total_power <= 100:
                continue
            characters.append({
                'pool_id': _pool_id(name, series),
                'name': name,
                'anime': series,
                'image_url': image_url if image_url.startswith(('http://', 'https://')) else None,
                'image_lookup': name,
                'total_power': total_power,
                'attack': total_power,
                'defense': total_power,
                'speed': total_power,
                'durability': total_power,
                'intelligence': total_power,
                'tier': _tier(total_power),
                'abilities': [],
                'special_abilities': [],
                'enabled': True,
                'roster': _ROSTER_ID,
                'catalog_version': 2,
            })
    return characters


AUCTION_CHARACTER_POOL = _load_roster()
