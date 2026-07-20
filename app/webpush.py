"""Web Push: VAPID-ключи и отправка уведомлений в браузер/PWA."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from pathlib import Path

from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from py_vapid import Vapid02
from pywebpush import WebPushException, webpush

from app.storage.models import PushSubscription

logger = logging.getLogger(__name__)

DEFAULT_KEY_PATH = Path("data/vapid_private.pem")


class DeadSubscription(Exception):
    """Push-сервис ответил 404/410 - подписки больше не существует, нужно деактивировать."""


class VapidKeys:
    """Загружает VAPID-ключи с диска или генерирует новые при первом запуске.

    Ключи должны быть стабильны между перезапусками - если они поменяются,
    все существующие подписки браузеров/устройств станут недействительны
    и подписываться придётся заново.
    """

    def __init__(self, key_path: Path | str = DEFAULT_KEY_PATH) -> None:
        self._path = Path(key_path)
        self._vapid = self._load_or_generate()

    def _load_or_generate(self) -> Vapid02:
        if self._path.exists():
            return Vapid02.from_file(str(self._path))
        self._path.parent.mkdir(parents=True, exist_ok=True)
        vapid = Vapid02()
        vapid.generate_keys()
        vapid.save_key(str(self._path))
        logger.info("Сгенерировал новую пару VAPID-ключей: %s", self._path)
        return vapid

    @property
    def public_key_b64(self) -> str:
        """Формат для applicationServerKey на фронтенде: raw uncompressed point, urlsafe base64 без паддинга."""
        raw = self._vapid.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    @property
    def vapid(self) -> Vapid02:
        return self._vapid


async def send_push(
    keys: VapidKeys,
    subscription: PushSubscription,
    payload: dict,
    contact_email: str,
) -> None:
    """Отправляет один push. Бросает DeadSubscription на 404/410, остальные ошибки просто логирует.

    pywebpush синхронный (requests, не aiohttp) - гоняем через to_thread,
    чтобы медленный ответ push-сервиса не подвешивал остальной event loop.
    """
    subscription_info = {
        "endpoint": subscription.endpoint,
        "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
    }

    def _send() -> None:
        webpush(
            subscription_info=subscription_info,
            data=json.dumps(payload),
            vapid_private_key=keys.vapid,
            vapid_claims={"sub": f"mailto:{contact_email}"},
        )

    try:
        await asyncio.to_thread(_send)
    except WebPushException as exc:
        status = exc.response.status_code if exc.response is not None else None
        if status in (404, 410):
            raise DeadSubscription(subscription.endpoint) from exc
        logger.warning(
            "Push не доставлен (%s): %s", subscription.device_name or subscription.endpoint, exc
        )
