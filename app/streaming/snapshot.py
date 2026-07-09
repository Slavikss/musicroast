"""Сырой снапшот вкуса — всё, что extraction-слой отдаёт медкарте.

Никаких зависимостей от сервисов: модуль общий для реального Яндекса и мока.
Любое поле, кроме liked-треков, может быть None — соответствующая секция
медкарты просто опускается.
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
class HistoryDay:
    """Один день реальных прослушиваний из music_history."""

    date: str
    context_type: str  # wave | artist | album | playlist | unknown
    context_name: str
    track_titles: List[str] = field(default_factory=list)


@dataclass
class LikedArtist:
    name: str
    genres: List[str] = field(default_factory=list)
    timestamp: Optional[str] = None
    disclaimers: List[str] = field(default_factory=list)


@dataclass
class WaveStation:
    """Станция «Моей волны» с сохранёнными юзером настройками."""

    name: str
    mood_energy: Optional[str] = None  # fun | active | calm | sad | all
    diversity: Optional[str] = None  # favorite | popular | discover | default
    language: Optional[str] = None  # russian | not-russian | any


@dataclass
class AccountInfo:
    birthday: Optional[str] = None
    registered_at: Optional[str] = None
    region: Optional[int] = None
    has_plus: bool = False
    family_subscription: bool = False
    subeditor: bool = False  # модератор метаданных — задрот-сигнал
    child: bool = False


@dataclass
class UserSettingsInfo:
    theme: Optional[str] = None
    auto_play_radio: Optional[bool] = None
    scrobbling: Optional[bool] = None
    music_visibility: Optional[str] = None  # public | private
    shuffle: Optional[bool] = None


@dataclass
class OwnPlaylist:
    title: str
    visibility: Optional[str] = None
    collective: bool = False
    likes_count: int = 0
    track_count: int = 0


@dataclass
class RawTasteSnapshot:
    """Сырые материалы для медкарты (до нормализации треков)."""

    # 1. Спина: лайки (сырые объекты трека) + даты лайков
    liked_tracks: List[Any] = field(default_factory=list)
    liked_added: Dict[str, str] = field(default_factory=dict)
    # 2. Чарт: id трека -> позиция
    chart_positions: Optional[Dict[str, int]] = None
    # 3. Заявленное
    pins: Optional[List[DeclaredItem]] = None
    playlist_titles: Optional[List[str]] = None
    own_playlists: Optional[List[OwnPlaylist]] = None
    # 4. Граница вкуса
    disliked_artist_names: Optional[List[str]] = None
    disliked_track_artists: Optional[List[str]] = None
    disliked_genres: Optional[List[str]] = None
    # 5. Анамнез: реальные прослушивания (music_history)
    history_days: Optional[List[HistoryDay]] = None
    # 6. Привязанности
    liked_artists: Optional[List[LikedArtist]] = None
    liked_albums_count: Optional[int] = None
    liked_album_titles: Optional[List[str]] = None
    presaves_count: Optional[int] = None
    # 7. Моя волна
    wave_settings: Optional[List[WaveStation]] = None
    skips_per_hour: Optional[int] = None
    # 8. Личность
    account: Optional[AccountInfo] = None
    settings: Optional[UserSettingsInfo] = None
    # 9. Красные флаги с гидрации
    artist_disclaimers: Optional[Dict[str, List[str]]] = None  # имя -> пометки
