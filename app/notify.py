"""Анти-спам/кулдаун и отправка уведомлений в чат."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime

from aiogram import Bot

from app.analysis import Evaluation, PriceStatus
from app.bot.formatting import format_notification
from app.pricing.source import Direction
from app.storage import models as storage_models

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
    bot: Bot,
    conn: sqlite3.Connection,
    chat_id: int,
    direction: Direction,
    evaluation: Evaluation,
    eta_min: int | None,
    now: datetime,
) -> bool:
    """Шлёт уведомление, если статус хороший и кулдаун пройден. Возвращает, отправили ли."""
    if not _should_notify(conn, direction, evaluation, now):
        return False

    text = format_notification(direction, evaluation, eta_min)
    await bot.send_message(chat_id, text)

    storage_models.log_notification(
        conn,
        storage_models.NotificationLogEntry(
            direction=direction.value,
            ts=now,
            price=evaluation.current_price,
            notif_type=evaluation.status.value,
        ),
    )
    logger.info("Отправил уведомление %s для %s: %.0f ₽", evaluation.status.value, direction.value, evaluation.current_price)
    return True
