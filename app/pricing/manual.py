"""PriceSource на ручном вводе - аварийный источник через /report_price.

Это не реализация PriceSource.get_price(origin, destination, tariff) -
человек уже сам посмотрел цену в приложении, разворачивать маршрут не
нужно. build_manual_price() просто оборачивает то, что он ввёл, в Price
той же формы, что и у автоматических источников, плюс проверяет, что
цена похожа на цену такси, а не на опечатку.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.pricing.source import Price, TariffClass

MIN_SANE_PRICE = 30.0
MAX_SANE_PRICE = 10_000.0


class InvalidManualPrice(ValueError):
    """Введённая вручную цена не похожа на цену такси."""


def build_manual_price(amount: float, tariff: TariffClass, eta_min: int | None = None) -> Price:
    if not (MIN_SANE_PRICE <= amount <= MAX_SANE_PRICE):
        raise InvalidManualPrice(
            f"Цена {amount:g} ₽ выглядит неправдоподобно "
            f"(ожидал {MIN_SANE_PRICE:.0f}-{MAX_SANE_PRICE:.0f} ₽)"
        )
    if eta_min is not None and eta_min < 0:
        raise InvalidManualPrice(f"Время в пути не может быть отрицательным: {eta_min}")

    return Price(
        amount=amount,
        tariff=tariff,
        source="manual",
        ts=datetime.now(timezone.utc),
        eta_min=eta_min,
    )
