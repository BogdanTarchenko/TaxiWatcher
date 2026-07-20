"""PriceSource на Playwright: цена такси с виджета сравнения на Яндекс.Картах."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from app.config import Coordinates
from app.pricing.source import Price, TariffClass

PRICE_RE = re.compile(r"~?\s*(\d[\d\s\xa0]*)\s*₽")
ETA_RE = re.compile(r"(\d+)\s*мин")

DEBUG_DIR = Path("data/debug")
NAV_TIMEOUT_MS = 20_000
PRICE_TIMEOUT_MS = 15_000


class ScrapeError(RuntimeError):
    """Не удалось получить цену такси со страницы Яндекс.Карт."""


class RateLimitedError(ScrapeError):
    """Яндекс ответил 429 — похоже, слишком частые заходы с этого IP.

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


def _parse_price_block(text: str) -> tuple[float, int | None]:
    price_match = PRICE_RE.search(text)
    if not price_match:
        raise ScrapeError(f"Не нашёл цену в тексте блока такси: {text!r}")
    amount = float(price_match.group(1).replace(" ", "").replace("\xa0", ""))

    eta_match = ETA_RE.search(text)
    eta_min = int(eta_match.group(1)) if eta_match else None

    return amount, eta_min


class MapsScraperSource:
    """Один переиспользуемый браузерный контекст вместо запуска браузера на каждый замер.

    Виджет такси на картах не даёт выбрать тариф — он всегда считает econom
    (это видно по ссылке "Выбрать тариф", которая ведёт на .../order?tariff=econom).
    Поэтому get_price() отказывается работать с любым другим TariffClass.
    """

    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    async def start(self) -> None:
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=True)
        self._context = await self._browser.new_context(locale="ru-RU")

    async def stop(self) -> None:
        if self._context is not None:
            await self._context.close()
        if self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()
        self._context = self._browser = self._playwright = None

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
        if self._context is None:
            raise RuntimeError("MapsScraperSource не запущен — вызови start() перед get_price()")

        url = _build_url(origin, destination)
        page = await self._context.new_page()
        try:
            response = await page.goto(url, timeout=NAV_TIMEOUT_MS)
            if response is not None and response.status == 429:
                raise RateLimitedError(_parse_retry_after(response.headers.get("retry-after")))

            taxi_item = page.get_by_role("listitem", name=re.compile("такси", re.IGNORECASE)).first
            await taxi_item.wait_for(timeout=PRICE_TIMEOUT_MS)
            text = await taxi_item.inner_text()
            amount, eta_min = _parse_price_block(text)
            return Price(
                amount=amount,
                tariff=tariff,
                source="maps_scrape",
                ts=datetime.now(timezone.utc),
                eta_min=eta_min,
            )
        except RateLimitedError:
            raise
        except (PlaywrightTimeoutError, ScrapeError) as exc:
            debug_path = await self._save_debug_snapshot(page)
            raise ScrapeError(f"Не удалось прочитать цену такси, снимок: {debug_path}") from exc
        finally:
            await page.close()

    async def _save_debug_snapshot(self, page: Page) -> Path:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = DEBUG_DIR / f"scrape_fail_{stamp}.png"
        await page.screenshot(path=str(path))
        return path
