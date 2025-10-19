from fastapi import HTTPException

from .apple import AppleMusicStreamingService
from .base import StreamingProvider, StreamingService
from .spotify import SpotifyStreamingService
from .yandex import YandexMusicStreamingService


_PROVIDER_MAP = {
    StreamingProvider.YANDEX: YandexMusicStreamingService,
    StreamingProvider.SPOTIFY: SpotifyStreamingService,
    StreamingProvider.APPLE: AppleMusicStreamingService,
}


def create_streaming_service(
    provider: StreamingProvider, token: str
) -> StreamingService:
    service_cls = _PROVIDER_MAP.get(provider)
    if not service_cls:
        raise HTTPException(
            status_code=400, detail=f"Неизвестный стриминговый провайдер: {provider}"
        )
    return service_cls(token)


__all__ = [
    "StreamingProvider",
    "StreamingService",
    "YandexMusicStreamingService",
    "SpotifyStreamingService",
    "AppleMusicStreamingService",
    "create_streaming_service",
]
