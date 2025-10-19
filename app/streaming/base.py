from enum import Enum
from typing import Any, Dict, List, Union

from fastapi import HTTPException


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
