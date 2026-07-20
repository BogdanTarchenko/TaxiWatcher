"""Уведомления по порогу хорошей цены: рассылка push подписчикам.

Логика (без статистики, работает с первого дня):
- цена < threshold -> "коридор хорошей цены". Первое пересечение уведомляет.
- дальнейшее падение уведомляет только на каждой следующей ступени вниз
  (threshold - step, threshold - 2*step, ...), а не на каждый чих.
- цена >= threshold после того, как была в коридоре -> одно уведомление
  о возврате к обычной цене, дальнейший рост (порог+step, +2*step, ...)
  уже не уведомляет.

"Ступень, на которой уже уведомляли" хранится в таблице settings (не в
памяти процесса) - переживает рестарт контейнера.
"""

from __future__ import annotations

import logging
import math
import sqlite3
from datetime import datetime

from app.formatting import build_good_price_payload, build_price_recovered_payload
from app.pricing.source import Direction
from app.storage import models as storage_models
from app.webpush import DeadSubscription, VapidKeys, send_push

logger = logging.getLogger(__name__)


def _level_setting_key(direction: Direction) -> str:
    return f"price_level:{direction.value}"


def _current_level(price: float, threshold: float, step: float) -> float | None:
    """Ступень ниже threshold, на которой сейчас цена, или None, если цена >= threshold."""
    if price >= threshold:
        return None
    steps_down = math.floor((threshold - price) / step)
    return threshold - step * steps_down


async def maybe_notify(
    keys: VapidKeys,
    conn: sqlite3.Connection,
    contact_email: str,
    direction: Direction,
    price: float,
    threshold: float,
    step: float,
    now: datetime,
) -> bool:
    """Шлёт push, если цена пересекла новую ступень вниз или вернулась выше порога."""
    setting_key = _level_setting_key(direction)
    last_level_raw = storage_models.get_setting(conn, setting_key)
    last_level = float(last_level_raw) if last_level_raw else None

    current_level = _current_level(price, threshold, step)

    if current_level is not None:
        if last_level is not None and current_level >= last_level:
            return False  # уже уведомляли про эту или более низкую ступень
        payload = build_good_price_payload(direction, price, threshold)
        notif_type = "good_price"
        new_level_raw = str(current_level)
    else:
        if last_level is None:
            return False  # и так были выше порога - нечего анонсировать
        payload = build_price_recovered_payload(direction, price, threshold)
        notif_type = "price_recovered"
        new_level_raw = ""

    subscriptions = storage_models.fetch_active_push_subscriptions(conn)
    if not subscriptions:
        logger.info("Порог сработал (%s, %.0f ₽), но активных push-подписок пока нет", notif_type, price)
        return False

    for subscription in subscriptions:
        try:
            await send_push(keys, subscription, payload, contact_email)
        except DeadSubscription:
            storage_models.deactivate_push_subscription(conn, subscription.endpoint)
            logger.info("Деактивировал мёртвую подписку: %s", subscription.device_name or subscription.endpoint)

    storage_models.set_setting(conn, setting_key, new_level_raw)
    storage_models.log_notification(
        conn,
        storage_models.NotificationLogEntry(
            direction=direction.value,
            ts=now,
            price=price,
            notif_type=notif_type,
        ),
    )
    logger.info("Уведомление %s для %s разослано %d подпискам", notif_type, direction.value, len(subscriptions))
    return True
