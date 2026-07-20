"""Тексты push-уведомлений (и позже - веб-страницы)."""

from __future__ import annotations

from app.analysis import Evaluation, PriceStatus
from app.pricing.source import Direction

DIRECTION_LABELS = {
    Direction.TO_OFFICE: "Дом → офис",
    Direction.TO_HOME: "Офис → дом",
}

STATUS_LABELS = {
    PriceStatus.BEST: "Лучшая цена",
    PriceStatus.ACCEPTABLE: "Приемлемая цена",
}


def build_notification_payload(direction: Direction, evaluation: Evaluation, eta_min: int | None) -> dict:
    """Пейлоад для push: title/body показывает service worker, tag - чтобы повторные
    уведомления по тому же направлению заменяли друг друга на экране, а не копились."""
    route = DIRECTION_LABELS[direction]
    status_label = STATUS_LABELS[evaluation.status]

    price_line = f"{evaluation.current_price:.0f} ₽"
    if evaluation.bucket_median is not None:
        savings_pct = (1 - evaluation.current_price / evaluation.bucket_median) * 100
        price_line += f" (обычно ~{evaluation.bucket_median:.0f} ₽"
        if savings_pct > 0:
            price_line += f", экономия {savings_pct:.0f}%"
        price_line += ")"

    body_lines = [price_line]
    if eta_min is not None:
        body_lines.append(f"В пути ~{eta_min} мин")

    return {
        "title": f"{status_label} — {route}",
        "body": "\n".join(body_lines),
        "tag": f"price-{direction.value}",
    }
