"""Батлы двух пользователей: реестр с кодами-приглашениями.

Стороны хранят снапшоты статистики (досье), а не токены — токен одного
пользователя никогда не пересекает границу чужой сессии.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

from app.config import BATTLE_TTL

# Без похожих символов (0/O, 1/I)
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

WAITING = "waiting"
DONE = "done"


@dataclass
class BattleSide:
    display_name: str
    score: float
    diagnosis: str
    stats_block: str
    sample_lines: str
    chat_id: Optional[int] = None  # куда слать результат (None для веба)
    archetype: str = ""  # детерминированный архетип из диагноз-ядра

    def dossier(self) -> str:
        archetype_line = (
            f"Архетип (вычислен точно): {self.archetype}\n" if self.archetype else ""
        )
        return (
            f"Имя: {self.display_name}\n"
            f"Оценка вкуса: {self.score}/10\n"
            f"{archetype_line}"
            f"Диагноз: {self.diagnosis}\n"
            f"{self.stats_block}\n\n"
            f"Топ треков:\n{self.sample_lines}"
        )


@dataclass
class Battle:
    code: str
    level: str
    initiator: BattleSide
    opponent: Optional[BattleSide] = None
    result: Optional[str] = None
    winner: Optional[str] = None
    status: str = WAITING
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0


class BattleError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class BattleRegistry:
    """In-memory реестр батлов с TTL."""

    def __init__(self, ttl: int = BATTLE_TTL) -> None:
        self._battles: Dict[str, Battle] = {}
        self._lock = asyncio.Lock()
        self._ttl = ttl

    @staticmethod
    def _new_code() -> str:
        return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(6))

    async def create(self, initiator: BattleSide, level: str) -> Battle:
        async with self._lock:
            self._sweep_locked()
            code = self._new_code()
            while code in self._battles:
                code = self._new_code()
            battle = Battle(
                code=code,
                level=level,
                initiator=initiator,
                expires_at=time.time() + self._ttl,
            )
            self._battles[code] = battle
            return battle

    async def join(self, code: str, opponent: BattleSide) -> Battle:
        async with self._lock:
            self._sweep_locked()
            battle = self._battles.get(code.strip().upper())
            if not battle:
                raise BattleError("Батл не найден или уже сгорел")
            if battle.opponent is not None:
                raise BattleError("В этом батле уже два бойца")
            battle.opponent = opponent
            return battle

    async def set_result(self, code: str, result: str, winner: Optional[str]) -> None:
        async with self._lock:
            battle = self._battles.get(code)
            if battle:
                battle.result = result
                battle.winner = winner
                battle.status = DONE

    async def get(self, code: str) -> Optional[Battle]:
        async with self._lock:
            self._sweep_locked()
            return self._battles.get(code.strip().upper())

    def _sweep_locked(self) -> None:
        now = time.time()
        expired = [
            code
            for code, battle in self._battles.items()
            if battle.expires_at < now and battle.status != DONE
        ]
        for code in expired:
            del self._battles[code]


battle_registry = BattleRegistry()

__all__ = [
    "Battle",
    "BattleError",
    "BattleRegistry",
    "BattleSide",
    "battle_registry",
    "WAITING",
    "DONE",
]
