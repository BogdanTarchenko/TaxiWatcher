"""Роутер aiogram: /now, /stats, /best_today, /report_price, /settings, /health.

/set_threshold из PRD сюда сознательно не попал - ручного порога в analysis.py
больше нет (холодный старт теперь чисто по времени), команда была бы ни к чему
не подключена.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from app.analysis import bucket_stats
from app.bot.formatting import DIRECTION_LABELS
from app.config import Settings
from app.pricing.manual import InvalidManualPrice, build_manual_price
from app.pricing.maps_scraper import RateLimitedError, ScrapeError
from app.pricing.source import Direction, TariffClass
from app.scheduler import PriceScheduler
from app.storage import models as storage_models

router = Router(name="commands")

WEEKDAY_NAMES = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]

HELP_TEXT = (
    "Taxi Watcher следит за ценой такси Яндекс Go по маршруту дом ⇄ офис.\n\n"
    "/now — цена сейчас по обоим направлениям\n"
    "/best_today — в какие часы сегодня обычно дешевле\n"
    "/stats — текущий бакет (медиана/p25) по обоим направлениям\n"
    "/report_price office|home <цена> [минуты] — сообщить цену вручную\n"
    "/settings — текущие настройки бота\n"
    "/health — когда бот последний раз успешно проверял цену"
)

DIRECTION_TOKENS = {"office": Direction.TO_OFFICE, "home": Direction.TO_HOME}


def _format_age(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes < 60:
        return f"{minutes} мин"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} ч {minutes} мин"


@router.message(Command("start", "help"))
async def cmd_start(message: Message) -> None:
    await message.answer(HELP_TEXT)


@router.message(Command("now"))
async def cmd_now(
    message: Message,
    settings: Settings,
    conn: sqlite3.Connection,
    scheduler: PriceScheduler,
) -> None:
    now = datetime.now(settings.timezone)
    freshness = timedelta(minutes=settings.poll_interval_min)
    lines: list[str] = []

    for direction in (Direction.TO_OFFICE, Direction.TO_HOME):
        label = DIRECTION_LABELS[direction]
        samples = storage_models.fetch_price_samples(conn, direction.value)
        latest = samples[-1] if samples else None

        if latest is not None and (now - latest.ts) <= freshness:
            local_ts = latest.ts.astimezone(settings.timezone)
            lines.append(f"{label}: {latest.price:.0f} ₽ (на {local_ts:%H:%M})")
            continue

        try:
            price = await scheduler.fetch_price(direction)
        except RateLimitedError:
            lines.append(f"{label}: Яндекс временно ограничил запросы, попробуй попозже")
            continue
        except ScrapeError:
            lines.append(f"{label}: не удалось получить цену прямо сейчас")
            continue

        await scheduler.record_and_maybe_notify(direction, price, now)
        lines.append(f"{label}: {price.amount:.0f} ₽ (только что)")

    await message.answer("\n".join(lines))


@router.message(Command("report_price"))
async def cmd_report_price(
    message: Message,
    command: CommandObject,
    settings: Settings,
    scheduler: PriceScheduler,
) -> None:
    args = (command.args or "").split()
    if len(args) not in (2, 3):
        await message.answer(
            "Формат: /report_price office|home <цена> [минуты]\nНапример: /report_price office 320 12"
        )
        return

    direction_token, price_token, *rest = args
    direction = DIRECTION_TOKENS.get(direction_token.lower())
    if direction is None:
        await message.answer("Первый аргумент — office или home")
        return

    try:
        amount = float(price_token.replace(",", "."))
    except ValueError:
        await message.answer(f"Не понял цену: {price_token!r}")
        return

    eta_min: int | None = None
    if rest:
        try:
            eta_min = int(rest[0])
        except ValueError:
            await message.answer(f"Не понял время в пути: {rest[0]!r}")
            return

    try:
        price = build_manual_price(amount, TariffClass(settings.tariff), eta_min=eta_min)
    except InvalidManualPrice as exc:
        await message.answer(str(exc))
        return

    now = datetime.now(settings.timezone)
    await scheduler.record_and_maybe_notify(direction, price, now)
    await message.answer(f"Записал: {DIRECTION_LABELS[direction]} — {amount:.0f} ₽")


@router.message(Command("stats"))
async def cmd_stats(message: Message, settings: Settings, conn: sqlite3.Connection) -> None:
    now = datetime.now(settings.timezone)
    lines = [f"Бакет: {WEEKDAY_NAMES[now.weekday()]}, {now.hour:02d}:00–{now.hour + 1:02d}:00"]

    for direction in (Direction.TO_OFFICE, Direction.TO_HOME):
        history = storage_models.fetch_price_samples(conn, direction.value)
        stats = bucket_stats(history, now.weekday(), now.hour, now)
        label = DIRECTION_LABELS[direction]
        if stats.sample_count == 0:
            lines.append(f"{label}: пока нет данных для этого часа")
        else:
            lines.append(
                f"{label}: медиана {stats.median:.0f} ₽, p25 {stats.p25:.0f} ₽ ({stats.sample_count} замеров)"
            )

    await message.answer("\n".join(lines))


@router.message(Command("best_today"))
async def cmd_best_today(message: Message, settings: Settings, conn: sqlite3.Connection) -> None:
    now = datetime.now(settings.timezone)
    weekday = now.weekday()
    lines = [f"Обычные цены по часам ({WEEKDAY_NAMES[weekday]}):"]
    has_any_data = False

    for direction in (Direction.TO_OFFICE, Direction.TO_HOME):
        history = storage_models.fetch_price_samples(conn, direction.value)
        rows = [
            stats
            for hour in range(24)
            if (stats := bucket_stats(history, weekday, hour, now)).sample_count > 0
        ]
        label = DIRECTION_LABELS[direction]

        if not rows:
            lines.append(f"\n{label}: пока нет данных по этому дню недели")
            continue

        has_any_data = True
        best = min(rows, key=lambda s: s.median)
        lines.append(f"\n{label} (дешевле всего в {best.hour:02d}:00, ~{best.median:.0f} ₽):")
        for stats in sorted(rows, key=lambda s: s.hour):
            marker = " ←" if stats.hour == best.hour else ""
            lines.append(f"  {stats.hour:02d}:00 — ~{stats.median:.0f} ₽{marker}")

    if not has_any_data:
        lines.append("Данных пока недостаточно — первые 7 дней бот только собирает статистику.")

    await message.answer("\n".join(lines))


@router.message(Command("settings"))
async def cmd_settings(message: Message, settings: Settings) -> None:
    lines = [
        f"Тариф: {settings.tariff}",
        f"Часовой пояс: {settings.timezone}",
        f"Активное окно: {settings.active_window_start:%H:%M}–{settings.active_window_end:%H:%M}, будни",
        f"Интервал опроса: {settings.poll_interval_min} мин",
        f"Дом: {settings.home}",
        f"Офис: {settings.office}",
    ]
    await message.answer("\n".join(lines))


@router.message(Command("health"))
async def cmd_health(
    message: Message,
    settings: Settings,
    conn: sqlite3.Connection,
    scheduler: PriceScheduler,
) -> None:
    now = datetime.now(settings.timezone)
    lines: list[str] = []

    for direction in (Direction.TO_OFFICE, Direction.TO_HOME):
        label = DIRECTION_LABELS[direction]
        samples = storage_models.fetch_price_samples(conn, direction.value)
        if not samples:
            lines.append(f"{label}: данных ещё нет")
            continue
        latest = samples[-1]
        local_ts = latest.ts.astimezone(settings.timezone)
        lines.append(
            f"{label}: {latest.price:.0f} ₽ в {local_ts:%d.%m %H:%M} "
            f"({_format_age(now - latest.ts)} назад, источник {latest.source})"
        )

    if scheduler.paused_until is not None and now < scheduler.paused_until:
        paused_local = scheduler.paused_until.astimezone(settings.timezone)
        lines.append(f"\n⚠️ Опрос на паузе (rate limit) до {paused_local:%H:%M}")

    await message.answer("\n".join(lines))
