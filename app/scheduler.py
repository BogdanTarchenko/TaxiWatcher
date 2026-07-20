"""APScheduler: опрос цены по обоим направлениям в активном окне (будни, 09:30-21:00)."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.analysis import Evaluation, evaluate
from app.config import Settings
from app.notify import maybe_notify
from app.pricing.maps_scraper import MapsScraperSource, RateLimitedError, ScrapeError
from app.pricing.source import Direction, Price, PriceSource, TariffClass
from app.storage import models as storage_models
from app.webpush import VapidKeys

logger = logging.getLogger(__name__)

# 429 у Яндекса бывает и коротким всплеском (следующий запрос через пару минут уже
# проходит), и затяжной блокировкой (час и больше). Раз не знаем заранее, какой это
# случай - начинаем с короткой паузы и удваиваем на каждый следующий подряд идущий
# 429, до потолка; первый же успешный запрос сбрасывает счётчик обратно.
BASE_RATE_LIMIT_BACKOFF = timedelta(minutes=5)
MAX_RATE_LIMIT_BACKOFF = timedelta(minutes=30)
FRIDAY = 4  # datetime.weekday(): Monday=0 ... Sunday=6, опрашиваем только пн-пт

# Два запроса к Яндекс.Картам подряд без паузы почти всегда ловят 429 на втором,
# даже если первый только что прошёл успешно - похоже на короткий burst-лимит,
# а не на что-то специфичное для направления. Пауза между направлениями лечит это.
FETCH_GAP_SECONDS = 5


class PriceScheduler:
    """Владеет живущим весь процесс источником цены и опрашивает оба направления по расписанию."""

    def __init__(
        self,
        settings: Settings,
        conn: sqlite3.Connection,
        vapid_keys: VapidKeys,
        source: PriceSource | None = None,
    ) -> None:
        self._settings = settings
        self._conn = conn
        self._vapid_keys = vapid_keys
        self._source = source if source is not None else MapsScraperSource()
        self._scheduler = AsyncIOScheduler(timezone=settings.timezone)
        self._paused_until: datetime | None = None
        self._consecutive_rate_limits = 0

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

    @property
    def paused_until(self) -> datetime | None:
        """Не None, пока действует бэкофф после RateLimitedError - для /health."""
        return self._paused_until

    def _in_active_window(self, now: datetime) -> bool:
        if now.weekday() > FRIDAY:
            return False
        return self._settings.active_window_start <= now.time() <= self._settings.active_window_end

    async def fetch_price(self, direction: Direction) -> Price:
        """Разовый запрос цены к источнику - без сохранения и без учёта паузы/окна."""
        origin, destination = direction.route(self._settings.home, self._settings.office)
        return await self._source.get_price(origin, destination, TariffClass.ECONOM)

    async def fetch_price_respecting_pause(self, direction: Direction, now: datetime) -> Price:
        """Как fetch_price(), но проверяет и продлевает паузу после RateLimitedError.

        Нужен отдельно от _poll_once, потому что веб-страница тоже дёргает цену
        по требованию (см. web/routes.py) - без этой проверки каждое открытие
        страницы во время паузы било бы по Яндексу заново, вместо того чтобы
        просто отдать уже известный rate_limited из памяти.
        """
        if self._paused_until is not None and now < self._paused_until:
            raise RateLimitedError(retry_after_sec=int((self._paused_until - now).total_seconds()))
        self._paused_until = None

        try:
            price = await self.fetch_price(direction)
        except RateLimitedError as exc:
            self._consecutive_rate_limits += 1
            backoff = BASE_RATE_LIMIT_BACKOFF * (2 ** (self._consecutive_rate_limits - 1))
            if exc.retry_after_sec:
                backoff = max(backoff, timedelta(seconds=exc.retry_after_sec))
            backoff = min(backoff, MAX_RATE_LIMIT_BACKOFF)
            self._paused_until = now + backoff
            logger.warning(
                "Rate limited (%d раз подряд), пауза до %s",
                self._consecutive_rate_limits,
                self._paused_until,
            )
            raise
        else:
            self._consecutive_rate_limits = 0
            return price

    async def record_and_maybe_notify(self, direction: Direction, price: Price, now: datetime) -> Evaluation:
        """Сохраняет цену, оценивает её и, если статус хороший, уведомляет чат.

        Общая точка для планового опроса, /now и /report_price - чтобы у всех
        трёх путей была одна и та же логика оценки и анти-спама, а не три копии.
        """
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
            self._vapid_keys,
            self._conn,
            self._settings.vapid_contact_email,
            direction,
            price.amount,
            self._settings.good_price_threshold,
            self._settings.good_price_step,
            now,
        )
        return evaluation

    async def _poll_once(self, now: datetime | None = None) -> None:
        now = now if now is not None else datetime.now(self._settings.timezone)

        if not self._in_active_window(now):
            return

        for i, direction in enumerate((Direction.TO_OFFICE, Direction.TO_HOME)):
            if i > 0:
                await asyncio.sleep(FETCH_GAP_SECONDS)
            try:
                price = await self.fetch_price_respecting_pause(direction, now)
            except RateLimitedError:
                return  # второе направление почти наверняка тоже упрётся в лимит - не пробуем
            except ScrapeError:
                logger.exception("Не удалось получить цену для направления %s", direction.value)
                continue

            await self.record_and_maybe_notify(direction, price, now)
