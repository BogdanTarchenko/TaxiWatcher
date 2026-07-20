"""Анти-спам/кулдаун и рассылка push-уведомлений подписчикам."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime

from app.analysis import Evaluation, PriceStatus
from app.formatting import build_notification_payload
from app.pricing.source import Direction
from app.storage import models as storage_models
from app.webpush import DeadSubscription, VapidKeys, send_push

logger = logging.getLogger(__name__)

# Повторное уведомление в тот же день - только если цена упала ещё заметнее.
REPEAT_DROP_FRACTION = 0.10


def _should_notify(
    conn: sqlite3.Connection,
    direction: Direction,
    evaluation: Evaluation,
    now: datetime,
) -> bool:
    if evaluation.status not in (PriceStatus.BEST, PriceStatus.ACCEPTABLE):
        return False

    last = storage_models.last_notification(conn, direction.value)
    if last is None:
        return True

    same_day = last.ts.astimezone(now.tzinfo).date() == now.date()
    if not same_day:
        return True

    return evaluation.current_price <= last.price * (1 - REPEAT_DROP_FRACTION)


async def maybe_notify(
    keys: VapidKeys,
    conn: sqlite3.Connection,
    contact_email: str,
    direction: Direction,
    evaluation: Evaluation,
    eta_min: int | None,
    now: datetime,
) -> bool:
    """Шлёт push всем активным подпискам, если статус хороший и кулдаун пройден.

    Возвращает, разослали ли реально (False и если статус не тот, и если
    подписчиков ещё нет вообще - в этом случае кулдаун тоже не трогаем,
    незачем гасить будущее уведомление ради того, кто его не получил).
    """
    if not _should_notify(conn, direction, evaluation, now):
        return False

    subscriptions = storage_models.fetch_active_push_subscriptions(conn)
    if not subscriptions:
        logger.info("Цена хорошая (%s), но активных push-подписок пока нет", evaluation.status.value)
        return False

    payload = build_notification_payload(direction, evaluation, eta_min)
    for subscription in subscriptions:
        try:
            await send_push(keys, subscription, payload, contact_email)
        except DeadSubscription:
            storage_models.deactivate_push_subscription(conn, subscription.endpoint)
            logger.info("Деактивировал мёртвую подписку: %s", subscription.device_name or subscription.endpoint)

    storage_models.log_notification(
        conn,
        storage_models.NotificationLogEntry(
            direction=direction.value,
            ts=now,
            price=evaluation.current_price,
            notif_type=evaluation.status.value,
        ),
    )
    logger.info(
        "Уведомление %s для %s разослано %d подпискам",
        evaluation.status.value,
        direction.value,
        len(subscriptions),
    )
    return True
