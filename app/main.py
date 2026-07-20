"""Точка входа: собирает бота, планировщик и БД, запускает polling."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher

from app.bot.handlers import router
from app.config import load_settings
from app.scheduler import PriceScheduler
from app.storage import db as storage_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    settings = load_settings()
    conn = storage_db.connect()

    bot = Bot(token=settings.bot_token)
    dp = Dispatcher()
    dp.include_router(router)

    scheduler = PriceScheduler(settings, conn, bot)
    dp["settings"] = settings
    dp["conn"] = conn
    dp["scheduler"] = scheduler

    await scheduler.start()
    try:
        logger.info("Taxi Watcher запущен")
        await dp.start_polling(bot)
    finally:
        await scheduler.stop()
        await bot.session.close()
        conn.close()


if __name__ == "__main__":
    asyncio.run(main())
