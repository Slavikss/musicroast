import os
from pathlib import Path

from dotenv import load_dotenv

# Грузим .env как можно раньше: конфиг читается на импорте, до create_app()
load_dotenv()

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

# --- LLM (OpenAI-совместимый провайдер, по умолчанию OpenRouter) ---------
# Ключ и модели задаются через env, чтобы менять провайдера/модель без правки кода.
LLM_API_KEY = os.getenv("OPENROUTER_API_KEY") or os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")
LLM_TEXT_MODEL = os.getenv("LLM_TEXT_MODEL", "google/gemma-4-31b-it:free")
LLM_IMAGE_MODEL = os.getenv("LLM_IMAGE_MODEL", "google/gemini-2.5-flash-image-preview")
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "1.0"))
# Необязательные заголовки ранжирования OpenRouter
LLM_APP_URL = os.getenv("LLM_APP_URL", "https://github.com/musicroast")
LLM_APP_TITLE = os.getenv("LLM_APP_TITLE", "MusicRoast")

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
