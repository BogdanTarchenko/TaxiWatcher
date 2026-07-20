"""Таблицы: price_samples, notifications_log, settings, push_subscriptions.

Направление хранится строкой ('to_office' / 'to_home') — этот же контракт
использует Direction в pricing/source.py, отдельный enum-тип в БД не нужен.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class PriceSample:
    direction: str
    ts: datetime
    price: float
    tariff: str
    source: str
    eta_min: int | None = None
    id: int | None = None


@dataclass(frozen=True)
class NotificationLogEntry:
    direction: str
    ts: datetime
    price: float
    notif_type: str
    id: int | None = None


def insert_price_sample(conn: sqlite3.Connection, sample: PriceSample) -> int:
    cur = conn.execute(
        "INSERT INTO price_samples (direction, ts, price, eta_min, tariff, source) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (sample.direction, sample.ts.isoformat(), sample.price, sample.eta_min, sample.tariff, sample.source),
    )
    conn.commit()
    return cur.lastrowid


def fetch_price_samples(
    conn: sqlite3.Connection,
    direction: str,
    since: datetime | None = None,
) -> list[PriceSample]:
    query = "SELECT id, direction, ts, price, eta_min, tariff, source FROM price_samples WHERE direction = ?"
    params: list[object] = [direction]
    if since is not None:
        query += " AND ts >= ?"
        params.append(since.isoformat())
    query += " ORDER BY ts"
    rows = conn.execute(query, params).fetchall()
    return [
        PriceSample(
            id=row["id"],
            direction=row["direction"],
            ts=datetime.fromisoformat(row["ts"]),
            price=row["price"],
            eta_min=row["eta_min"],
            tariff=row["tariff"],
            source=row["source"],
        )
        for row in rows
    ]


def log_notification(conn: sqlite3.Connection, entry: NotificationLogEntry) -> int:
    cur = conn.execute(
        "INSERT INTO notifications_log (direction, ts, price, notif_type) VALUES (?, ?, ?, ?)",
        (entry.direction, entry.ts.isoformat(), entry.price, entry.notif_type),
    )
    conn.commit()
    return cur.lastrowid


def last_notification(conn: sqlite3.Connection, direction: str) -> NotificationLogEntry | None:
    row = conn.execute(
        "SELECT id, direction, ts, price, notif_type FROM notifications_log "
        "WHERE direction = ? ORDER BY ts DESC LIMIT 1",
        (direction,),
    ).fetchone()
    if row is None:
        return None
    return NotificationLogEntry(
        id=row["id"],
        direction=row["direction"],
        ts=datetime.fromisoformat(row["ts"]),
        price=row["price"],
        notif_type=row["notif_type"],
    )


def get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


@dataclass(frozen=True)
class PushSubscription:
    endpoint: str
    p256dh: str
    auth: str
    device_name: str | None = None
    created_at: datetime | None = None
    is_active: bool = True
    id: int | None = None


def upsert_push_subscription(
    conn: sqlite3.Connection,
    endpoint: str,
    p256dh: str,
    auth: str,
    device_name: str | None,
    now: datetime,
) -> int:
    """Один браузер/устройство = одна запись по endpoint. Повторная подписка того же
    устройства обновляет ключи и снова помечает подписку активной."""
    cur = conn.execute(
        "INSERT INTO push_subscriptions (endpoint, p256dh, auth, device_name, created_at, is_active) "
        "VALUES (?, ?, ?, ?, ?, 1) "
        "ON CONFLICT(endpoint) DO UPDATE SET "
        "p256dh = excluded.p256dh, auth = excluded.auth, device_name = excluded.device_name, is_active = 1",
        (endpoint, p256dh, auth, device_name, now.isoformat()),
    )
    conn.commit()
    return cur.lastrowid


def deactivate_push_subscription(conn: sqlite3.Connection, endpoint: str) -> None:
    """Отписка или "мёртвый" endpoint (push вернул 404/410) - не удаляем строку, просто гасим."""
    conn.execute("UPDATE push_subscriptions SET is_active = 0 WHERE endpoint = ?", (endpoint,))
    conn.commit()


def fetch_active_push_subscriptions(conn: sqlite3.Connection) -> list[PushSubscription]:
    rows = conn.execute(
        "SELECT id, endpoint, p256dh, auth, device_name, created_at, is_active "
        "FROM push_subscriptions WHERE is_active = 1"
    ).fetchall()
    return [
        PushSubscription(
            id=row["id"],
            endpoint=row["endpoint"],
            p256dh=row["p256dh"],
            auth=row["auth"],
            device_name=row["device_name"],
            created_at=datetime.fromisoformat(row["created_at"]),
            is_active=bool(row["is_active"]),
        )
        for row in rows
    ]
