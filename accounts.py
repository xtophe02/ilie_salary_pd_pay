from __future__ import annotations

import json
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'accounts_db.json')


def _load() -> dict:
    if not os.path.exists(DB_PATH):
        return {}
    with open(DB_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def _save(db: dict):
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with open(DB_PATH, 'w', encoding='utf-8') as f:
        json.dump(db, f, indent=2, ensure_ascii=False)


def get_iban(name: str) -> str | None:
    db = _load()
    return db.get(name.upper(), {}).get('iban')


def update_iban(name: str, iban: str, source: str = 'manual'):
    db = _load()
    key = name.upper()
    db[key] = {
        'name': name,
        'iban': iban.upper().replace(' ', ''),
        'source': source,
        'updated': datetime.now().isoformat(),
    }
    _save(db)


def get_all() -> dict:
    return _load()


def delete_account(name: str):
    db = _load()
    key = name.upper()
    if key in db:
        del db[key]
        _save(db)
