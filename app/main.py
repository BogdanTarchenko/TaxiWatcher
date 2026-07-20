"""Точка входа: собирает веб-сервер, планировщик и БД, запускает aiohttp."""

from __future__ import annotations

import logging

from aiohttp import web

from app.config import load_settings
from app.scheduler import PriceScheduler
from app.storage import db as storage_db
from app.web.routes import create_app
from app.webpush import VapidKeys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

PORT = 8080


def build_app() -> web.Application:
    settings = load_settings()
    conn = storage_db.connect()
    vapid_keys = VapidKeys()
    scheduler = PriceScheduler(settings, conn, vapid_keys)

    app = create_app(settings, conn, scheduler, vapid_keys)

    async def on_startup(_: web.Application) -> None:
        await scheduler.start()
        logger.info("Taxi Watcher запущен")

    async def on_cleanup(_: web.Application) -> None:
        await scheduler.stop()
        conn.close()

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


if __name__ == "__main__":
    web.run_app(build_app(), host="0.0.0.0", port=PORT)
