from typing import List, Optional

from pydantic import BaseModel


class Track(BaseModel):
    """DTO для представления трека потокового сервиса"""

    title: str
    artists: List[str]
    year: Optional[int] = None
    genre: Optional[str] = None
    added_at: Optional[str] = None
    # Поля для диагноз-ядра (все опциональны — обратная совместимость)
    track_id: Optional[str] = None
    artist_ids: List[int] = []
    duration_ms: Optional[int] = None
    explicit: bool = False
    lyrics_available: bool = False
