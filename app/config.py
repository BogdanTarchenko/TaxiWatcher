"""Загружает настройки из .env: координаты, окно, тайм-зона, тариф, веб-доступ."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo, available_timezones

from dotenv import load_dotenv


class ConfigError(RuntimeError):
    """Отсутствует или некорректна обязательная переменная окружения."""


@dataclass(frozen=True)
class Coordinates:
    lat: float
    lon: float

    def __str__(self) -> str:
        return f"{self.lat},{self.lon}"


@dataclass(frozen=True)
class Settings:
    home: Coordinates
    office: Coordinates
    tariff: str
    timezone: ZoneInfo
    active_window_start: time
    active_window_end: time
    poll_interval_min: int
    vapid_contact_email: str
    app_secret_path: str


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"Не задана переменная окружения {name}")
    return value


def _parse_int(name: str) -> int:
    value = _require(name)
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"{name} должна быть целым числом, получено {value!r}") from exc


def _parse_float(name: str) -> float:
    value = _require(name)
    try:
        return float(value)
    except ValueError as exc:
        raise ConfigError(f"{name} должна быть числом, получено {value!r}") from exc


def _parse_time(name: str) -> time:
    value = _require(name)
    try:
        hours, minutes = value.split(":")
        return time(int(hours), int(minutes))
    except ValueError as exc:
        raise ConfigError(f"{name} должна быть в формате HH:MM, получено {value!r}") from exc


def _parse_timezone(name: str) -> ZoneInfo:
    value = _require(name)
    if value not in available_timezones():
        raise ConfigError(f"{name}={value!r} — неизвестная IANA-таймзона")
    return ZoneInfo(value)


def load_settings(env_file: Path | str = ".env") -> Settings:
    """Читает .env и возвращает провалидированные настройки. Бросает ConfigError, если чего-то не хватает."""
    load_dotenv(env_file, override=False)

    return Settings(
        home=Coordinates(_parse_float("HOME_LAT"), _parse_float("HOME_LON")),
        office=Coordinates(_parse_float("OFFICE_LAT"), _parse_float("OFFICE_LON")),
        tariff=_require("TARIFF"),
        timezone=_parse_timezone("TIMEZONE"),
        active_window_start=_parse_time("ACTIVE_WINDOW_START"),
        active_window_end=_parse_time("ACTIVE_WINDOW_END"),
        poll_interval_min=_parse_int("POLL_INTERVAL_MIN"),
        vapid_contact_email=_require("VAPID_CONTACT_EMAIL"),
        app_secret_path=_require("APP_SECRET_PATH"),
    )
