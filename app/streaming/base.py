from enum import Enum
from typing import Any, Dict, List, Optional, Union

from fastapi import HTTPException

from .snapshot import RawTasteSnapshot


class StreamingProvider(str, Enum):
    YANDEX = "yandex"
    SPOTIFY = "spotify"
    APPLE = "apple"


class StreamingService:
    """Базовый сервис для работы с потоковыми платформами"""

    provider: StreamingProvider = StreamingProvider.YANDEX

    def __init__(self, token: str):
        self.token = token

    def list_playlists(
        self, owner_id: Union[str, int] = "me"
    ) -> List[Dict[str, Any]]:
        raise HTTPException(
            status_code=501,
            detail=f"Интеграция со стримингом '{self.provider.value}' пока не реализована",
        )

    def get_playlist_tracks(
        self, playlist_kind: Union[int, str], owner_id: Union[str, int] = "me"
    ) -> tuple[List[Any], Dict[str, str], Dict[str, Any]]:
        raise HTTPException(
            status_code=501,
            detail=f"Интеграция со стримингом '{self.provider.value}' пока не реализована",
        )

    def get_account_summary(self) -> Dict[str, Any]:
        """Возвращает {uid, display_name, liked_count} для валидации токена."""
        raise HTTPException(
            status_code=501,
            detail=f"Интеграция со стримингом '{self.provider.value}' пока не реализована",
        )

    def get_taste_snapshot(self) -> RawTasteSnapshot:
        """Сырой снапшот вкуса для диагноз-ядра (лайки + чарт + pins + дизлайки)."""
        raise HTTPException(
            status_code=501,
            detail=f"Интеграция со стримингом '{self.provider.value}' пока не реализована",
        )

    def get_artist_popularity(self, artist_ids: List[int]) -> Dict[int, Optional[int]]:
        """Слушатели за месяц по артистам (только для головы HHI, лениво)."""
        return {}

    def fetch_lyrics(self, track_id: str) -> Optional[str]:
        """Текст трека или None. Зовётся только за лирик-гейтом."""
        return None
