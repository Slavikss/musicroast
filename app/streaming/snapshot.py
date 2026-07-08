"""Сырой снапшот вкуса — всё, что extraction-слой отдаёт диагноз-ядру.

Никаких зависимостей от сервисов: модуль общий для реального Яндекса и мока.
Любое поле, кроме liked-треков, может быть None — соответствующая ось ядра
просто не сыграет.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class DeclaredItem:
    """Запиненный/заявленный элемент: артист, альбом, плейлист."""

    kind: str  # artist | album | playlist | wave
    name: str


@dataclass
class RawTasteSnapshot:
    """Сырые материалы для диагноза (до нормализации треков)."""

    # 1. Спина: лайки (сырые объекты трека) + даты лайков
    liked_tracks: List[Any] = field(default_factory=list)
    liked_added: Dict[str, str] = field(default_factory=dict)
    # 2. Чарт: id трека -> позиция
    chart_positions: Optional[Dict[str, int]] = None
    # 3. Заявленное
    pins: Optional[List[DeclaredItem]] = None
    playlist_titles: Optional[List[str]] = None
    # 4. Граница вкуса
    disliked_artist_names: Optional[List[str]] = None
    disliked_track_artists: Optional[List[str]] = None  # имена артистов дизлайкнутых треков
    disliked_genres: Optional[List[str]] = None
