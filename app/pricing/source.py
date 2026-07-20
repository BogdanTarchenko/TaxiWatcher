"""Интерфейс PriceSource и общие типы (Price, Direction, TariffClass)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol

from app.config import Coordinates


class Direction(str, Enum):
    TO_OFFICE = "to_office"
    TO_HOME = "to_home"

    def route(self, home: Coordinates, office: Coordinates) -> tuple[Coordinates, Coordinates]:
        """Возвращает (откуда, куда) для этого направления."""
        if self is Direction.TO_OFFICE:
            return home, office
        return office, home


class TariffClass(str, Enum):
    ECONOM = "econom"


@dataclass(frozen=True)
class Price:
    amount: float
    tariff: TariffClass
    source: str
    ts: datetime
    eta_min: int | None = None


class PriceSource(Protocol):
    """Общий интерфейс: по маршруту (откуда/куда) и тарифу — текущая цена.

    О направлении (to_office/to_home) источник не знает — это забота
    вызывающего кода (scheduler.py), который разворачивает Direction в
    пару координат перед вызовом и потом снова добавляет Direction при
    сохранении Price в storage.
    """

    async def get_price(
        self,
        origin: Coordinates,
        destination: Coordinates,
        tariff: TariffClass,
    ) -> Price: ...
