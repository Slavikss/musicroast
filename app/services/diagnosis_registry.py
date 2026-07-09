"""Append-only реестр диагнозов: git-коммит вкуса, а не Grafana-панель.

Ключ — snapshot_hash: тот же снапшот → тот же record (первый приём у доктора).
Записи не мутируются и не удаляются (в пределах жизни процесса). Название
болезни у каждого пациента своё (его придумывает LLM), поэтому дифф во
времени идёт по стадии (severity) и скору, а не по названию.
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
    diagnosis_name: str
    severity: int
    verdict_phrase: str
    symptoms: tuple
    prescription: str
    evidence: tuple  # 3x "title — artists"
    score: float = 5.0
    coverage: Optional[Dict[str, Any]] = None
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
    previous: Optional[DiagnosisRecord],
    current_name: str,
    current_severity: int,
) -> Optional[str]:
    """Одна строка диффа между снапшотами, или None если сравнивать не с чем."""
    if previous is None:
        return None
    if current_severity > previous.severity:
        return (
            f"Болезнь прогрессирует: было «{previous.diagnosis_name}» "
            f"(стадия {previous.severity}), теперь «{current_name}» "
            f"(стадия {current_severity})."
        )
    if current_severity < previous.severity:
        return (
            f"Динамика положительная: со стадии {previous.severity} "
            f"(«{previous.diagnosis_name}») до {current_severity}. Лечение работает?"
        )
    return (
        f"Стадия держится на {current_severity}: было «{previous.diagnosis_name}», "
        f"стало «{current_name}» — форма новая, болезнь та же."
    )


diagnosis_registry = DiagnosisRegistry()

__all__ = [
    "DiagnosisRecord",
    "DiagnosisRegistry",
    "diagnosis_registry",
    "taste_diff",
]
