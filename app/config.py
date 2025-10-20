import os
from pathlib import Path


STATIC_DIR = Path("static")
IMAGE_DIR = STATIC_DIR / "images"

YANDEX_OAUTH_CLIENT_ID = os.getenv(
    "YANDEX_OAUTH_CLIENT_ID", "23cabbbdc6cd418abb4b39c32c41195d"
)
YANDEX_OAUTH_URL = (
    f"https://oauth.yandex.ru/authorize?response_type=token&client_id={YANDEX_OAUTH_CLIENT_ID}"
)

YANDEX_OAUTH_HEADLESS = os.getenv("YANDEX_OAUTH_HEADLESS", "true").lower() not in {
    "0",
    "false",
    "no",
}
YANDEX_OAUTH_INTERACTIVE_HEADLESS = (
    os.getenv("YANDEX_OAUTH_INTERACTIVE_HEADLESS", "false").lower()
    not in {"0", "false", "no"}
)
YANDEX_OAUTH_VIEWPORT_WIDTH = int(os.getenv("YANDEX_OAUTH_VIEWPORT_WIDTH", "1280"))
YANDEX_OAUTH_VIEWPORT_HEIGHT = int(os.getenv("YANDEX_OAUTH_VIEWPORT_HEIGHT", "720"))
YANDEX_OAUTH_TIMEOUT = int(os.getenv("YANDEX_OAUTH_TIMEOUT", "120"))
TOKEN_STORAGE_DEFAULT_TTL = int(os.getenv("TOKEN_STORAGE_DEFAULT_TTL", "86400"))

BACKEND_API_BASE_URL = os.getenv("BACKEND_API_BASE_URL")
YANDEX_MINIAPP_URL = os.getenv("YANDEX_MINIAPP_URL")
