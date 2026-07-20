"""Статистика по бакетам (день недели x час) и решение "хорошая ли цена сейчас"."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from statistics import median

from app.storage.models import PriceSample

HISTORY_WINDOW = timedelta(weeks=12)
COLD_START_PERIOD = timedelta(days=7)


class PriceStatus(str, Enum):
    NOT_ENOUGH_DATA = "not_enough_data"
    BEST = "best"
    ACCEPTABLE = "acceptable"
    NORMAL = "normal"


@dataclass(frozen=True)
class Evaluation:
    status: PriceStatus
    current_price: float
    bucket_median: float | None
    bucket_p25: float | None
    bucket_sample_count: int


@dataclass(frozen=True)
class BucketStats:
    weekday: int
    hour: int
    median: float | None
    p25: float | None
    sample_count: int


def _bucket_key(ts: datetime) -> tuple[int, int]:
    return ts.weekday(), ts.hour


def _percentile(sorted_values: list[float], fraction: float) -> float:
    """Без интерполяции - для пары десятков сэмплов в бакете разница не важна."""
    index = min(len(sorted_values) - 1, int(len(sorted_values) * fraction))
    return sorted_values[index]


def bucket_stats(history: list[PriceSample], weekday: int, hour: int, now: datetime) -> BucketStats:
    """Медиана/p25/кол-во сэмплов для конкретного бакета (день недели, час) за последние 12 недель."""
    window_start = now - HISTORY_WINDOW
    prices = sorted(
        sample.price
        for sample in history
        if sample.ts >= window_start and _bucket_key(sample.ts) == (weekday, hour)
    )
    if not prices:
        return BucketStats(weekday, hour, None, None, 0)
    return BucketStats(weekday, hour, median(prices), _percentile(prices, 0.25), len(prices))


def evaluate(history: list[PriceSample], current_price: float, now: datetime) -> Evaluation:
    """Классифицирует current_price относительно истории того же (день недели, час).

    `history` - прошлые сэмплы одного направления (уже без только что измеренной
    цены - её сюда включать не нужно, иначе бакет будет сравнивать цену саму с собой).
    """
    if not history:
        return Evaluation(PriceStatus.NOT_ENOUGH_DATA, current_price, None, None, 0)

    earliest = min(sample.ts for sample in history)
    if now - earliest < COLD_START_PERIOD:
        return Evaluation(PriceStatus.NOT_ENOUGH_DATA, current_price, None, None, 0)

    stats = bucket_stats(history, now.weekday(), now.hour, now)
    if stats.sample_count == 0:
        return Evaluation(PriceStatus.NOT_ENOUGH_DATA, current_price, None, None, 0)

    if current_price <= stats.p25:
        status = PriceStatus.BEST
    elif current_price <= stats.median:
        status = PriceStatus.ACCEPTABLE
    else:
        status = PriceStatus.NORMAL

    return Evaluation(status, current_price, stats.median, stats.p25, stats.sample_count)
