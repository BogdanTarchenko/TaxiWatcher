"""Шаблоны текстов уведомлений и ответов бота."""

from __future__ import annotations

from app.analysis import Evaluation, PriceStatus
from app.pricing.source import Direction

DIRECTION_LABELS = {
    Direction.TO_OFFICE: "Дом → офис",
    Direction.TO_HOME: "Офис → дом",
}

STATUS_LABELS = {
    PriceStatus.BEST: "🟢 Лучшая цена",
    PriceStatus.ACCEPTABLE: "🟡 Приемлемая цена",
}


def format_notification(direction: Direction, evaluation: Evaluation, eta_min: int | None) -> str:
    label = STATUS_LABELS[evaluation.status]
    route = DIRECTION_LABELS[direction]
    lines = [label, "", f"{route} сейчас: {evaluation.current_price:.0f} ₽"]

    if evaluation.bucket_median is not None:
        lines.append(f"Обычно в это время: ~{evaluation.bucket_median:.0f} ₽")
        savings_pct = (1 - evaluation.current_price / evaluation.bucket_median) * 100
        if savings_pct > 0:
            lines.append(f"Экономия ≈ {savings_pct:.0f}%")

    if eta_min is not None:
        lines.append(f"В пути ~{eta_min} мин")

    return "\n".join(lines)
