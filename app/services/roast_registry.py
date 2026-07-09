"""Реестр прожарок для viral-механик: deep-links, тизеры, авто-батлы.

In-memory, с TTL — по образцу TokenStorage. Хранит снапшоты результатов,
никаких токенов.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from app.config import ROAST_TTL


@dataclass
class RoastRecord:
    roast_id: str
    user_key: str  # telegram user id или "web:<uuid>"
    display_name: str
    text: str
    score: float
    diagnosis: str
    shame_facts: List[str]
    stats_block: str
    sample_lines: str
    playlist_title: str
    level: str
    diagnosis_name: str = ""
    severity: int = 0
    prescription: str = ""
    created_at: float = field(default_factory=time.time)
    expires_at: Optional[float] = None


class RoastRegistry:
    """Хранилище прожарок + карта рефералов (кто по чьей ссылке пришёл)."""

    def __init__(self, ttl: int = ROAST_TTL) -> None:
        self._records: Dict[str, RoastRecord] = {}
        self._latest_by_user: Dict[str, str] = {}
        self._pending_referrals: Dict[str, str] = {}  # user_key -> roast_id друга
        self._lock = asyncio.Lock()
        self._ttl = ttl

    @staticmethod
    def _new_id() -> str:
        # 8 url-safe символов: влезает в start-payload Telegram (лимит 64)
        return secrets.token_urlsafe(6)

    async def create(self, record: RoastRecord) -> str:
        async with self._lock:
            record.roast_id = record.roast_id or self._new_id()
            record.expires_at = time.time() + self._ttl
            self._records[record.roast_id] = record
            self._latest_by_user[record.user_key] = record.roast_id
            return record.roast_id

    async def get(self, roast_id: str) -> Optional[RoastRecord]:
        async with self._lock:
            return self._get_locked(roast_id)

    def _get_locked(self, roast_id: str) -> Optional[RoastRecord]:
        record = self._records.get(roast_id)
        if not record:
            return None
        if record.expires_at and record.expires_at < time.time():
            del self._records[roast_id]
            self._latest_by_user.pop(record.user_key, None)
            return None
        return record

    async def latest_for_user(self, user_key: str) -> Optional[RoastRecord]:
        async with self._lock:
            roast_id = self._latest_by_user.get(user_key)
            return self._get_locked(roast_id) if roast_id else None

    # --------------------------------------------------------------- referrals

    async def set_pending_referral(self, user_key: str, roast_id: str) -> None:
        async with self._lock:
            self._pending_referrals[user_key] = roast_id

    async def pop_pending_referral(self, user_key: str) -> Optional[RoastRecord]:
        async with self._lock:
            roast_id = self._pending_referrals.pop(user_key, None)
            return self._get_locked(roast_id) if roast_id else None


roast_registry = RoastRegistry()

__all__ = ["RoastRecord", "RoastRegistry", "roast_registry"]
