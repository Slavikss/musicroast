"""OAuth Device Flow для Яндекс Музыки: общий менеджер сессий для бота и веба.

Использует примитивы yandex-music v3 (`request_device_code` /
`poll_device_token`). Пользователь открывает `verification_url`, вводит
`user_code`, менеджер в фоне опрашивает Яндекс и по успеху отдаёт токен.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Dict, Optional

from yandex_music import Client as YandexMusicClient
from yandex_music.exceptions import DeviceAuthError, YandexMusicError

from app.config import DEVICE_AUTH_TTL

logger = logging.getLogger(__name__)

DEVICE_NAME = "MusicRoast"

# Статусы сессии
PENDING = "pending"
READY = "ready"
EXPIRED = "expired"
CANCELLED = "cancelled"


@dataclass
class DeviceTokens:
    access_token: str
    refresh_token: Optional[str] = None
    expires_in: Optional[int] = None


@dataclass
class DeviceSession:
    id: str
    device_code: str
    verification_url: str
    user_code: str
    interval: int
    expires_at: float
    owner_key: Optional[str] = None
    status: str = PENDING
    tokens: Optional[DeviceTokens] = None
    account: Optional[dict] = None  # кэш валидации токена (для веба)
    error: Optional[str] = None
    _poll_task: Optional[asyncio.Task] = field(default=None, repr=False)


OnReadyCallback = Callable[[DeviceSession], Awaitable[None]]


class DeviceAuthManager:
    """Держит активные device-auth сессии и фоновые поллеры."""

    def __init__(self, ttl: int = DEVICE_AUTH_TTL) -> None:
        self._sessions: Dict[str, DeviceSession] = {}
        self._by_owner: Dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._ttl = ttl

    async def start(
        self,
        owner_key: Optional[str] = None,
        on_ready: Optional[OnReadyCallback] = None,
    ) -> DeviceSession:
        """Запрашивает код устройства и запускает фоновый поллинг."""
        loop = asyncio.get_running_loop()
        code = await loop.run_in_executor(
            None,
            lambda: YandexMusicClient().request_device_code(device_name=DEVICE_NAME),
        )

        ttl = min(self._ttl, int(code.expires_in or self._ttl))
        session = DeviceSession(
            id=uuid.uuid4().hex[:16],
            device_code=code.device_code,
            verification_url=code.verification_url,
            user_code=code.user_code,
            interval=max(int(code.interval or 5), 2),
            expires_at=time.time() + ttl,
            owner_key=owner_key,
        )

        async with self._lock:
            if owner_key and owner_key in self._by_owner:
                old = self._sessions.get(self._by_owner[owner_key])
                if old:
                    self._cancel_locked(old)
            self._sessions[session.id] = session
            if owner_key:
                self._by_owner[owner_key] = session.id

        session._poll_task = asyncio.create_task(self._poll_loop(session, on_ready))
        return session

    async def get(self, session_id: str) -> Optional[DeviceSession]:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session and session.status == PENDING and time.time() > session.expires_at:
                session.status = EXPIRED
            return session

    async def cancel(self, session_id: str) -> None:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session:
                self._cancel_locked(session)

    def _cancel_locked(self, session: DeviceSession) -> None:
        if session.status == PENDING:
            session.status = CANCELLED
        if session._poll_task and not session._poll_task.done():
            session._poll_task.cancel()

    async def _poll_loop(
        self, session: DeviceSession, on_ready: Optional[OnReadyCallback]
    ) -> None:
        loop = asyncio.get_running_loop()
        client = YandexMusicClient()
        try:
            while time.time() < session.expires_at:
                await asyncio.sleep(session.interval)
                if session.status != PENDING:
                    return
                try:
                    token = await loop.run_in_executor(
                        None,
                        lambda: client.poll_device_token(session.device_code),
                    )
                except DeviceAuthError as exc:
                    session.status = EXPIRED
                    session.error = str(exc)
                    return
                except YandexMusicError as exc:  # сетевые/прочие сбои — пробуем дальше
                    logger.warning("device auth poll error: %s", exc)
                    continue

                if token is not None:
                    session.tokens = DeviceTokens(
                        access_token=token.access_token,
                        refresh_token=getattr(token, "refresh_token", None),
                        expires_in=getattr(token, "expires_in", None),
                    )
                    session.status = READY
                    if on_ready:
                        try:
                            await on_ready(session)
                        except Exception:  # noqa: BLE001
                            logger.exception("device auth on_ready callback failed")
                    return

            if session.status == PENDING:
                session.status = EXPIRED
        except asyncio.CancelledError:
            pass


device_auth_manager = DeviceAuthManager()

__all__ = [
    "DeviceAuthManager",
    "DeviceSession",
    "DeviceTokens",
    "device_auth_manager",
    "PENDING",
    "READY",
    "EXPIRED",
    "CANCELLED",
]
