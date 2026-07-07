from typing import Optional, Union

from pydantic import BaseModel, Field

from app.prompts import RoastLevel
from app.streaming import StreamingProvider


class StreamingCredentials(BaseModel):
    """Базовая модель с учётом конкретного стримингового провайдера."""

    provider: StreamingProvider = Field(
        default=StreamingProvider.YANDEX,
        description="Стриминговая платформа пользователя",
    )
    access_token: str = Field(
        ...,
        min_length=1,
        max_length=4096,
        repr=False,
        description="Персональный токен/ключ доступа к выбранной платформе",
    )


class PlaylistRequest(StreamingCredentials):
    """Запрос, содержащий идентификатор владельца плейлистов"""

    owner_id: Optional[Union[str, int]] = Field(
        default="me",
        description="Владелец плейлиста. По умолчанию текущий пользователь ('me')",
    )


class PlaylistInfoRequest(PlaylistRequest):
    """Запрос на получение информации по конкретному плейлисту"""

    playlist_kind: Union[str, int] = Field(
        ...,
        description="Идентификатор плейлиста. Используйте 'liked' для лайкнутых треков",
    )


class RoastRequest(PlaylistInfoRequest):
    """Запрос для эндпоинта /roast"""

    level: RoastLevel = Field(
        default=RoastLevel.MEDIUM,
        description="Уровень прожарки: light / medium / cremation",
    )
    prompt_version: Optional[str] = Field(
        default=None,
        description="Явная версия промпта (переопределяет level)",
    )
    generate_image: bool = Field(
        default=False, description="Признак необходимости генерации изображения"
    )
    display_name: Optional[str] = Field(
        default=None,
        max_length=128,
        description="Имя пользователя для шеринг-карточки",
    )


class BattleStartRequest(StreamingCredentials):
    """Создание батла (веб)."""

    display_name: str = Field(..., min_length=1, max_length=128)
    level: RoastLevel = Field(default=RoastLevel.MEDIUM)


class BattleJoinRequest(StreamingCredentials):
    """Присоединение к батлу по коду (веб)."""

    code: str = Field(..., min_length=4, max_length=12)
    display_name: str = Field(..., min_length=1, max_length=128)
