"""Тексты push-уведомлений (и позже - веб-страницы)."""

from __future__ import annotations

from app.pricing.source import Direction

DIRECTION_LABELS = {
    Direction.TO_OFFICE: "Дом → офис",
    Direction.TO_HOME: "Офис → дом",
}


def build_good_price_payload(direction: Direction, price: float, threshold: float) -> dict:
    """Цена только что пересекла порог (или ушла на новую ступень) вниз."""
    route = DIRECTION_LABELS[direction]
    return {
        "title": f"Хорошая цена — {route}",
        "body": f"{price:.0f} ₽ (порог {threshold:.0f} ₽)",
        "tag": f"price-{direction.value}",
    }


def build_price_recovered_payload(direction: Direction, price: float, threshold: float) -> dict:
    """Цена вернулась выше порога после того, как была в "коридоре хорошей цены"."""
    route = DIRECTION_LABELS[direction]
    return {
        "title": f"Цена снова обычная — {route}",
        "body": f"{price:.0f} ₽ (было ниже {threshold:.0f} ₽)",
        "tag": f"price-{direction.value}",
    }
