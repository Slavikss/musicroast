from .base import StreamingProvider, StreamingService


class SpotifyStreamingService(StreamingService):
    provider: StreamingProvider = StreamingProvider.SPOTIFY
    # Заглушка под будущую интеграцию
    pass
