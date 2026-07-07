import os
from pathlib import Path


STATIC_DIR = Path("static")
IMAGE_DIR = STATIC_DIR / "images"
WEB_DIR = STATIC_DIR / "web"

# api | bot | both — в режиме both FastAPI поднимает поллинг бота в lifespan
APP_MODE = os.getenv("APP_MODE", "both").strip().lower()

YANDEX_OAUTH_CLIENT_ID = os.getenv(
    "YANDEX_OAUTH_CLIENT_ID", "23cabbbdc6cd418abb4b39c32c41195d"
)
YANDEX_OAUTH_URL = (
    f"https://oauth.yandex.ru/authorize?response_type=token&client_id={YANDEX_OAUTH_CLIENT_ID}"
)

TOKEN_STORAGE_DEFAULT_TTL = int(os.getenv("TOKEN_STORAGE_DEFAULT_TTL", "86400"))

GEMINI_TEXT_MODEL = os.getenv("GEMINI_TEXT_MODEL", "gemini-flash-latest")
GEMINI_IMAGE_MODEL = os.getenv("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")

# Время жизни батла (сек) с момента создания до джойна второго участника
BATTLE_TTL = int(os.getenv("BATTLE_TTL", "3600"))
# Время жизни записи прожарки для шеринг-ссылок и тизеров (сек)
ROAST_TTL = int(os.getenv("ROAST_TTL", str(7 * 86400)))
# Время жизни device-auth сессии (сек)
DEVICE_AUTH_TTL = int(os.getenv("DEVICE_AUTH_TTL", "600"))

# MOCK_STREAMING=1 — любой токен обслуживается моковой библиотекой (для e2e без Яндекса)
MOCK_STREAMING = os.getenv("MOCK_STREAMING", "").strip().lower() in {"1", "true", "yes"}

BACKEND_API_BASE_URL = os.getenv("BACKEND_API_BASE_URL")
# Необязательный оверрайд юзернейма бота (для тестов deep-link'ов)
TELEGRAM_BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME")
