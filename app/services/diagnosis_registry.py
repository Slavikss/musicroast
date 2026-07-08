"""Append-only реестр диагнозов: git-коммит вкуса, а не Grafana-панель.

Ключ — snapshot_hash: тот же снапшот → тот же record без пересчёта ядра.
Записи не мутируются и не удаляются (в пределах жизни процесса).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class DiagnosisRecord:
    snapshot_hash: str
    user_key: str
    archetype: str
    axis: str
    verdict_phrase: str
    evidence: tuple  # 3x (title, artists_str)
    axis_scores: Dict[str, float]
    gate_status: Dict[str, Any]
    lyric_theme: Optional[str] = None
    created_at: float = field(default_factory=time.time)


class DiagnosisRegistry:
    """In-memory append-only журнал диагнозов."""

    def __init__(self) -> None:
        self._by_hash: Dict[str, DiagnosisRecord] = {}
        self._by_user: Dict[str, List[str]] = {}  # user_key -> [hash, ...] по времени
        self._lock = asyncio.Lock()

    async def get_by_hash(self, snapshot_hash: str) -> Optional[DiagnosisRecord]:
        async with self._lock:
            return self._by_hash.get(snapshot_hash)

    async def append(self, record: DiagnosisRecord) -> DiagnosisRecord:
        """Добавляет запись; дубликат по хешу не перезаписывается."""
        async with self._lock:
            existing = self._by_hash.get(record.snapshot_hash)
            if existing:
                return existing
            self._by_hash[record.snapshot_hash] = record
            self._by_user.setdefault(record.user_key, []).append(
                record.snapshot_hash
            )
            return record

    async def history_for_user(self, user_key: str) -> List[DiagnosisRecord]:
        async with self._lock:
            return [
                self._by_hash[h]
                for h in self._by_user.get(user_key, [])
                if h in self._by_hash
            ]

    async def previous_for_user(
        self, user_key: str, current_hash: str
    ) -> Optional[DiagnosisRecord]:
        """Последний record юзера с ДРУГИМ хешем — для диффа во времени."""
        history = await self.history_for_user(user_key)
        for record in reversed(history):
            if record.snapshot_hash != current_hash:
                return record
        return None


def taste_diff(
    previous: Optional[DiagnosisRecord], current_archetype: str, current_axis: str,
    current_scores: Dict[str, float],
) -> Optional[str]:
    """Одна строка диффа между снапшотами, или None если сравнивать не с чем."""
    if previous is None:
        return None
    if previous.archetype != current_archetype:
        return (
            f"С прошлого снапшота диагноз сменился: был «{previous.archetype}», "
            f"стал «{current_archetype}»."
        )
    prev_z = abs(previous.axis_scores.get(current_axis, 0.0))
    cur_z = abs(current_scores.get(current_axis, 0.0))
    if cur_z > prev_z + 0.3:
        return f"Диагноз «{current_archetype}» с прошлого раза только усугубился."
    if cur_z < prev_z - 0.3:
        return f"Диагноз «{current_archetype}» держится, но динамика чуть лучше."
    return f"Диагноз «{current_archetype}» стабилен — без улучшений."


diagnosis_registry = DiagnosisRegistry()

__all__ = [
    "DiagnosisRecord",
    "DiagnosisRegistry",
    "diagnosis_registry",
    "taste_diff",
]
