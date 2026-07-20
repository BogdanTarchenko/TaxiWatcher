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


def _bucket_key(ts: datetime) -> tuple[int, int]:
    return ts.weekday(), ts.hour


def _percentile(sorted_values: list[float], fraction: float) -> float:
    """Без интерполяции - для пары десятков сэмплов в бакете разница не важна."""
    index = min(len(sorted_values) - 1, int(len(sorted_values) * fraction))
    return sorted_values[index]


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

    window_start = now - HISTORY_WINDOW
    target_bucket = _bucket_key(now)
    bucket_prices = sorted(
        sample.price
        for sample in history
        if sample.ts >= window_start and _bucket_key(sample.ts) == target_bucket
    )

    if not bucket_prices:
        return Evaluation(PriceStatus.NOT_ENOUGH_DATA, current_price, None, None, 0)

    bucket_median = median(bucket_prices)
    bucket_p25 = _percentile(bucket_prices, 0.25)

    if current_price <= bucket_p25:
        status = PriceStatus.BEST
    elif current_price <= bucket_median:
        status = PriceStatus.ACCEPTABLE
    else:
        status = PriceStatus.NORMAL

    return Evaluation(status, current_price, bucket_median, bucket_p25, len(bucket_prices))
