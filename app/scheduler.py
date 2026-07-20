"""APScheduler: опрос цены по обоим направлениям в активном окне (будни, 09:30-21:00)."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.analysis import evaluate
from app.config import Settings
from app.notify import maybe_notify
from app.pricing.maps_scraper import MapsScraperSource, RateLimitedError, ScrapeError
from app.pricing.source import Direction, PriceSource, TariffClass
from app.storage import models as storage_models

logger = logging.getLogger(__name__)

# Даже без Retry-After не ретраим быстро - живой 429 у нас держался дольше пары минут.
DEFAULT_RATE_LIMIT_BACKOFF = timedelta(minutes=30)
FRIDAY = 4  # datetime.weekday(): Monday=0 ... Sunday=6, опрашиваем только пн-пт


class PriceScheduler:
    """Владеет живущим весь процесс источником цены и опрашивает оба направления по расписанию."""

    def __init__(
        self,
        settings: Settings,
        conn: sqlite3.Connection,
        bot: Bot,
        source: PriceSource | None = None,
    ) -> None:
        self._settings = settings
        self._conn = conn
        self._bot = bot
        self._source = source if source is not None else MapsScraperSource()
        self._scheduler = AsyncIOScheduler(timezone=settings.timezone)
        self._paused_until: datetime | None = None

    async def start(self) -> None:
        if hasattr(self._source, "start"):
            await self._source.start()
        self._scheduler.add_job(
            self._poll_once,
            trigger=IntervalTrigger(minutes=self._settings.poll_interval_min),
            id="poll_prices",
            next_run_time=datetime.now(self._settings.timezone),
        )
        self._scheduler.start()

    async def stop(self) -> None:
        self._scheduler.shutdown(wait=False)
        if hasattr(self._source, "stop"):
            await self._source.stop()

    def _in_active_window(self, now: datetime) -> bool:
        if now.weekday() > FRIDAY:
            return False
        return self._settings.active_window_start <= now.time() <= self._settings.active_window_end

    async def _poll_once(self, now: datetime | None = None) -> None:
        now = now if now is not None else datetime.now(self._settings.timezone)

        if self._paused_until is not None:
            if now < self._paused_until:
                return
            self._paused_until = None

        if not self._in_active_window(now):
            return

        for direction in (Direction.TO_OFFICE, Direction.TO_HOME):
            origin, destination = direction.route(self._settings.home, self._settings.office)
            try:
                price = await self._source.get_price(origin, destination, TariffClass.ECONOM)
            except RateLimitedError as exc:
                backoff = timedelta(seconds=exc.retry_after_sec) if exc.retry_after_sec else DEFAULT_RATE_LIMIT_BACKOFF
                backoff = max(backoff, DEFAULT_RATE_LIMIT_BACKOFF)
                self._paused_until = now + backoff
                logger.warning("Rate limited, пауза опроса до %s", self._paused_until)
                return  # второе направление почти наверняка тоже упрётся в лимит - не пробуем
            except ScrapeError:
                logger.exception("Не удалось получить цену для направления %s", direction.value)
                continue

            # История - до вставки нового сэмпла, иначе evaluate() сравнит цену саму с собой.
            history = storage_models.fetch_price_samples(self._conn, direction.value)
            evaluation = evaluate(history, price.amount, now)

            sample = storage_models.PriceSample(
                direction=direction.value,
                ts=price.ts,
                price=price.amount,
                tariff=price.tariff.value,
                source=price.source,
                eta_min=price.eta_min,
            )
            storage_models.insert_price_sample(self._conn, sample)
            logger.info("%s: %.0f ₽ (%s мин), статус %s", direction.value, price.amount, price.eta_min, evaluation.status.value)

            await maybe_notify(
                self._bot,
                self._conn,
                self._settings.chat_id,
                direction,
                evaluation,
                price.eta_min,
                now,
            )
