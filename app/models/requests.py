from typing import Optional, Union

from pydantic import BaseModel, Field

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

    prompt_version: Optional[str] = Field(
        default=None,
        description="Версия промпта, зарегистрированная в PromptManager",
    )
    generate_image: bool = Field(
        default=False, description="Признак необходимости генерации изображения"
    )
