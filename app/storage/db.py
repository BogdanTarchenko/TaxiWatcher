"""Подключение к SQLite."""

from __future__ import annotations

import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path("data/taxi_watcher.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS price_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    direction TEXT NOT NULL CHECK (direction IN ('to_office', 'to_home')),
    ts TEXT NOT NULL,
    price REAL NOT NULL,
    eta_min INTEGER,
    tariff TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('maps_scrape', 'manual'))
);
CREATE INDEX IF NOT EXISTS idx_price_samples_direction_ts ON price_samples(direction, ts);

CREATE TABLE IF NOT EXISTS notifications_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    direction TEXT NOT NULL CHECK (direction IN ('to_office', 'to_home')),
    ts TEXT NOT NULL,
    price REAL NOT NULL,
    notif_type TEXT NOT NULL CHECK (notif_type IN ('best', 'acceptable'))
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS push_subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint TEXT NOT NULL UNIQUE,
    p256dh TEXT NOT NULL,
    auth TEXT NOT NULL,
    device_name TEXT,
    created_at TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1
);
"""


def connect(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Открывает соединение с БД, создаёт файл/директорию и таблицы при первом запуске."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn
