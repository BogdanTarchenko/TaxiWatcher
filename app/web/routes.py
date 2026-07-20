"""aiohttp-роуты: API для веб-страницы и push-подписок.

Единственная защита доступа - секретный путь (settings.app_secret_path),
под которым смонтированы все роуты в create_app(); сам URL и есть "пароль".
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from aiohttp import web

STATIC_DIR = Path(__file__).parent / "static"

from app.analysis import bucket_stats, evaluate
from app.config import Settings
from app.formatting import DIRECTION_LABELS
from app.pricing.manual import InvalidManualPrice, build_manual_price
from app.pricing.maps_scraper import RateLimitedError, ScrapeError
from app.pricing.source import Direction, TariffClass
from app.scheduler import PriceScheduler
from app.storage import models as storage_models
from app.webpush import VapidKeys

DIRECTION_TOKENS = {"office": Direction.TO_OFFICE, "home": Direction.TO_HOME}


async def _direction_status(app: web.Application, direction: Direction) -> dict:
    settings: Settings = app["settings"]
    conn: sqlite3.Connection = app["conn"]
    scheduler: PriceScheduler = app["scheduler"]
    now = datetime.now(settings.timezone)

    samples = storage_models.fetch_price_samples(conn, direction.value)
    latest = samples[-1] if samples else None
    freshness = timedelta(minutes=settings.poll_interval_min)

    if latest is None or (now - latest.ts) > freshness:
        try:
            price = await scheduler.fetch_price_respecting_pause(direction, now)
        except RateLimitedError:
            return {"label": DIRECTION_LABELS[direction], "error": "rate_limited"}
        except ScrapeError:
            return {"label": DIRECTION_LABELS[direction], "error": "scrape_failed"}
        await scheduler.record_and_maybe_notify(direction, price, now)
        samples = storage_models.fetch_price_samples(conn, direction.value)
        latest = samples[-1]

    history = samples[:-1]
    evaluation = evaluate(history, latest.price, now)

    return {
        "label": DIRECTION_LABELS[direction],
        "price": latest.price,
        "eta_min": latest.eta_min,
        "as_of": latest.ts.astimezone(settings.timezone).isoformat(),
        "status": evaluation.status.value,
        "bucket_median": evaluation.bucket_median,
        "bucket_p25": evaluation.bucket_p25,
        "error": None,
    }


async def handle_status(request: web.Request) -> web.Response:
    settings: Settings = request.app["settings"]
    conn: sqlite3.Connection = request.app["conn"]
    scheduler: PriceScheduler = request.app["scheduler"]
    now = datetime.now(settings.timezone)
    weekday = now.weekday()

    # Оба направления параллельно - тот же подход, что и в плановом опросе
    # (см. scheduler.PriceScheduler._poll_once).
    results = await asyncio.gather(
        *(_direction_status(request.app, direction) for direction in DIRECTION_TOKENS.values())
    )
    directions = dict(zip(DIRECTION_TOKENS.keys(), results))

    best_today = {}
    for key, direction in DIRECTION_TOKENS.items():
        history = storage_models.fetch_price_samples(conn, direction.value)
        best_today[key] = [
            {"hour": hour, "median": stats.median}
            for hour in range(24)
            if (stats := bucket_stats(history, weekday, hour, now)).sample_count > 0
        ]

    return web.json_response(
        {
            "directions": directions,
            "best_today": best_today,
            "settings": {
                "tariff": settings.tariff,
                "window": f"{settings.active_window_start:%H:%M}-{settings.active_window_end:%H:%M}",
                "poll_interval_min": settings.poll_interval_min,
                "timezone": str(settings.timezone),
            },
            "paused_until": scheduler.paused_until.isoformat() if scheduler.paused_until else None,
        }
    )


async def handle_report_price(request: web.Request) -> web.Response:
    settings: Settings = request.app["settings"]
    scheduler: PriceScheduler = request.app["scheduler"]

    try:
        body = await request.json()
        direction = DIRECTION_TOKENS[body["direction"]]
        amount = float(body["price"])
        eta_min = int(body["eta_min"]) if body.get("eta_min") is not None else None
    except Exception:
        return web.json_response(
            {"error": "Ожидал {direction: 'office'|'home', price: число, eta_min?: число}"}, status=400
        )

    try:
        price = build_manual_price(amount, TariffClass(settings.tariff), eta_min=eta_min)
    except InvalidManualPrice as exc:
        return web.json_response({"error": str(exc)}, status=400)

    now = datetime.now(settings.timezone)
    evaluation = await scheduler.record_and_maybe_notify(direction, price, now)

    return web.json_response({"status": evaluation.status.value, "price": amount})


async def handle_vapid_public_key(request: web.Request) -> web.Response:
    vapid_keys: VapidKeys = request.app["vapid_keys"]
    return web.json_response({"publicKey": vapid_keys.public_key_b64})


async def handle_push_subscribe(request: web.Request) -> web.Response:
    settings: Settings = request.app["settings"]
    conn: sqlite3.Connection = request.app["conn"]

    try:
        body = await request.json()
        endpoint = str(body["endpoint"])
        p256dh = str(body["keys"]["p256dh"])
        auth = str(body["keys"]["auth"])
    except Exception:
        return web.json_response({"error": "Некорректная push-подписка"}, status=400)

    device_name = body.get("device_name")
    now = datetime.now(settings.timezone)
    storage_models.upsert_push_subscription(conn, endpoint, p256dh, auth, device_name, now)
    return web.json_response({"ok": True})


async def handle_push_unsubscribe(request: web.Request) -> web.Response:
    conn: sqlite3.Connection = request.app["conn"]

    try:
        body = await request.json()
        endpoint = str(body["endpoint"])
    except Exception:
        return web.json_response({"error": "Ожидал {endpoint: '...'}"}, status=400)

    storage_models.deactivate_push_subscription(conn, endpoint)
    return web.json_response({"ok": True})


async def handle_index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC_DIR / "index.html")


async def handle_manifest(request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC_DIR / "manifest.json")


async def handle_service_worker(request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC_DIR / "sw.js")


def create_app(
    settings: Settings,
    conn: sqlite3.Connection,
    scheduler: PriceScheduler,
    vapid_keys: VapidKeys,
) -> web.Application:
    """Собирает aiohttp-приложение: все роуты живут в саб-приложении под секретным путём."""
    subapp = web.Application()
    subapp["settings"] = settings
    subapp["conn"] = conn
    subapp["scheduler"] = scheduler
    subapp["vapid_keys"] = vapid_keys

    subapp.router.add_get("/", handle_index)
    subapp.router.add_get("/manifest.json", handle_manifest)
    subapp.router.add_get("/sw.js", handle_service_worker)
    subapp.router.add_static("/static/", STATIC_DIR)

    subapp.router.add_get("/api/status", handle_status)
    subapp.router.add_post("/api/report_price", handle_report_price)
    subapp.router.add_get("/api/push/vapid-public-key", handle_vapid_public_key)
    subapp.router.add_post("/api/push/subscribe", handle_push_subscribe)
    subapp.router.add_post("/api/push/unsubscribe", handle_push_unsubscribe)

    app = web.Application()
    app.add_subapp(f"/{settings.app_secret_path}/", subapp)
    return app
