"""Thread-safe in-memory storage for user streaming tokens."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Dict, Optional

import httpx

from app.config import TOKEN_STORAGE_DEFAULT_TTL

logger = logging.getLogger(__name__)

_OAUTH_TOKEN_URL = "https://oauth.yandex.ru/token"
# Публичные креды официального приложения (те же, что в yandex-music)
_OAUTH_CLIENT_ID = "23cabbbdc6cd418abb4b39c32c41195d"
_OAUTH_CLIENT_SECRET = "53bc75238f0c4d08a118e51fe9203300"  # noqa: S105


@dataclass
class TokenRecord:
    token: str
    created_at: float
    expires_at: Optional[float]
    refresh_token: Optional[str] = None


def refresh_access_token(refresh_token: str) -> Optional[dict]:
    """Обменивает refresh_token на новый access_token (синхронно).

    Возвращает {"access_token", "refresh_token", "expires_in"} или None.
    """
    try:
        response = httpx.post(
            _OAUTH_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": _OAUTH_CLIENT_ID,
                "client_secret": _OAUTH_CLIENT_SECRET,
            },
            timeout=15.0,
        )
        if response.status_code != 200:
            logger.warning("token refresh failed: HTTP %s", response.status_code)
            return None
        data = response.json()
        if "access_token" not in data:
            return None
        return {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token", refresh_token),
            "expires_in": data.get("expires_in"),
        }
    except httpx.HTTPError as exc:
        logger.warning("token refresh failed: %s", exc)
        return None


class TokenStorage:
    """Simple in-memory storage with optional TTL for tokens."""

    def __init__(self, default_ttl: Optional[int] = None) -> None:
        self._records: Dict[str, TokenRecord] = {}
        self._lock = asyncio.Lock()
        self._default_ttl = default_ttl

    async def set(
        self,
        key: str,
        token: str,
        ttl: Optional[int] = None,
        refresh_token: Optional[str] = None,
    ) -> None:
        expires_at = None
        if ttl is None:
            ttl = self._default_ttl
        if ttl:
            expires_at = time.time() + ttl

        async with self._lock:
            self._records[key] = TokenRecord(
                token=token,
                created_at=time.time(),
                expires_at=expires_at,
                refresh_token=refresh_token,
            )

    async def get(self, key: str) -> Optional[str]:
        record = await self.get_record(key)
        return record.token if record else None

    async def get_record(self, key: str) -> Optional[TokenRecord]:
        async with self._lock:
            record = self._records.get(key)
            if not record:
                return None
            if record.expires_at and record.expires_at < time.time():
                # Пробуем воскресить токен через refresh_token
                refreshed = None
                if record.refresh_token:
                    refreshed = await asyncio.get_running_loop().run_in_executor(
                        None, refresh_access_token, record.refresh_token
                    )
                if refreshed:
                    ttl = refreshed.get("expires_in") or self._default_ttl
                    record = TokenRecord(
                        token=refreshed["access_token"],
                        created_at=time.time(),
                        expires_at=time.time() + ttl if ttl else None,
                        refresh_token=refreshed.get("refresh_token"),
                    )
                    self._records[key] = record
                    return record
                del self._records[key]
                return None
            return record

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._records.pop(key, None)

    async def touch(self, key: str, ttl: Optional[int] = None) -> None:
        async with self._lock:
            record = self._records.get(key)
            if not record:
                return
            if ttl is None:
                ttl = self._default_ttl
            if ttl:
                record.expires_at = time.time() + ttl


token_storage = TokenStorage(default_ttl=TOKEN_STORAGE_DEFAULT_TTL)

__all__ = ["TokenStorage", "TokenRecord", "token_storage", "refresh_access_token"]
