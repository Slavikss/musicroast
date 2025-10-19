from typing import List, Optional

from pydantic import BaseModel


class Track(BaseModel):
    """DTO для представления трека потокового сервиса"""

    title: str
    artists: List[str]
    year: Optional[int] = None
    genre: Optional[str] = None
    added_at: Optional[str] = None
