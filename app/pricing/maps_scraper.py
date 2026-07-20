"""PriceSource через обычный HTTP GET на Яндекс.Карты.

Цена такси отрисовывается сервером ещё до отдачи HTML (SSR) и лежит прямо в
исходной странице как кусок JSON ("taxiInfo": {...}) - в браузере нет
отдельного XHR-запроса за ней. Значит и полноценный headless-браузер не
нужен: обычный GET + регулярка на JSON-блок дают то же самое, но быстрее и
без Chromium на борту.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

from app.config import Coordinates
from app.pricing.source import Price, TariffClass

TAXI_INFO_RE = re.compile(r'"taxiInfo"\s*:\s*(\{[^{}]*\})')
PRICE_DIGITS_RE = re.compile(r"(\d[\d\s\xa0]*)")

DEBUG_DIR = Path("data/debug")
REQUEST_TIMEOUT_SEC = 20
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9",
}


class ScrapeError(RuntimeError):
    """Не удалось получить цену такси со страницы Яндекс.Карт."""


class RateLimitedError(ScrapeError):
    """Яндекс ответил 429 - похоже, слишком частые заходы с этого IP.

    Отдельный тип специально для того, чтобы scheduler.py мог отличить
    временную блокировку от сломанной вёрстки и не долбить сайт чаще,
    пока не остыло, вместо того чтобы ретраить как обычную ошибку.
    """

    def __init__(self, retry_after_sec: int | None = None) -> None:
        self.retry_after_sec = retry_after_sec
        suffix = f", Retry-After {retry_after_sec}s" if retry_after_sec else ""
        super().__init__(f"Яндекс.Карты ответили 429 (rate limit){suffix}")


def _build_url(origin: Coordinates, destination: Coordinates) -> str:
    rtext = f"{origin.lat},{origin.lon}~{destination.lat},{destination.lon}"
    return f"https://yandex.ru/maps/?mode=routes&rtext={rtext}&rtt=taxi&ruri=~"


def _parse_retry_after(value: str | None) -> int | None:
    return int(value) if value and value.isdigit() else None


def _extract_taxi_info(html: str) -> dict:
    match = TAXI_INFO_RE.search(html)
    if not match:
        raise ScrapeError("Не нашёл блок taxiInfo в ответе (изменилась структура страницы?)")
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise ScrapeError(f"taxiInfo не распарсился как JSON: {match.group(1)!r}") from exc


def _parse_price_text(price_text: str) -> float:
    match = PRICE_DIGITS_RE.search(price_text)
    if not match:
        raise ScrapeError(f"Не понял priceText: {price_text!r}")
    return float(match.group(1).replace(" ", "").replace("\xa0", ""))


class MapsScraperSource:
    """Один переиспользуемый aiohttp.ClientSession вместо запроса на каждый замер.

    Виджет такси на картах не даёт выбрать тариф - он всегда считает econom
    (видно по taxiInfo.link, который всегда ведёт на .../order?tariff=econom).
    Поэтому get_price() отказывается работать с любым другим TariffClass.
    """

    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        self._session = aiohttp.ClientSession(headers=HEADERS)

    async def stop(self) -> None:
        if self._session is not None:
            await self._session.close()
        self._session = None

    async def get_price(
        self,
        origin: Coordinates,
        destination: Coordinates,
        tariff: TariffClass,
    ) -> Price:
        if tariff is not TariffClass.ECONOM:
            raise NotImplementedError(
                f"Виджет такси на картах всегда считает econom, {tariff.value} недоступен"
            )
        if self._session is None:
            raise RuntimeError("MapsScraperSource не запущен - вызови start() перед get_price()")

        url = _build_url(origin, destination)
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SEC)

        try:
            async with self._session.get(url, timeout=timeout) as response:
                if response.status == 429:
                    raise RateLimitedError(_parse_retry_after(response.headers.get("Retry-After")))
                if response.status != 200:
                    raise ScrapeError(f"Яндекс.Карты ответили статусом {response.status}")
                html = await response.text()
        except RateLimitedError:
            raise
        except aiohttp.ClientError as exc:
            raise ScrapeError(f"Сетевая ошибка при запросе к Яндекс.Картам: {exc}") from exc

        try:
            taxi_info = _extract_taxi_info(html)
            amount = _parse_price_text(taxi_info.get("priceText", ""))
        except ScrapeError as exc:
            debug_path = await self._save_debug_html(html)
            raise ScrapeError(f"{exc}, снимок: {debug_path}") from exc

        eta_seconds = taxi_info.get("time")
        eta_min = round(eta_seconds / 60) if isinstance(eta_seconds, (int, float)) else None

        return Price(
            amount=amount,
            tariff=tariff,
            source="maps_scrape",
            ts=datetime.now(timezone.utc),
            eta_min=eta_min,
        )

    async def _save_debug_html(self, html: str) -> Path:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = DEBUG_DIR / f"scrape_fail_{stamp}.html"
        path.write_text(html, encoding="utf-8")
        return path
