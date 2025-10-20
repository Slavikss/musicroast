"""Thread-safe in-memory storage for user streaming tokens."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Dict, Optional

from app.config import TOKEN_STORAGE_DEFAULT_TTL


@dataclass
class TokenRecord:
    token: str
    created_at: float
    expires_at: Optional[float]


class TokenStorage:
    """Simple in-memory storage with optional TTL for tokens."""

    def __init__(self, default_ttl: Optional[int] = None) -> None:
        self._records: Dict[str, TokenRecord] = {}
        self._lock = asyncio.Lock()
        self._default_ttl = default_ttl

    async def set(self, key: str, token: str, ttl: Optional[int] = None) -> None:
        expires_at = None
        if ttl is None:
            ttl = self._default_ttl
        if ttl:
            expires_at = time.time() + ttl

        async with self._lock:
            self._records[key] = TokenRecord(
                token=token, created_at=time.time(), expires_at=expires_at
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

__all__ = ["TokenStorage", "token_storage"]
