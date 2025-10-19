from .base import StreamingProvider, StreamingService


class AppleMusicStreamingService(StreamingService):
    provider: StreamingProvider = StreamingProvider.APPLE
    # Заглушка под будущую интеграцию
    pass
