"""Interactive Selenium session manager for Yandex OAuth inside Telegram Mini App."""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional

from selenium.common.exceptions import WebDriverException

from app.services.yandex_oauth import (
    OAuthToken,
    YandexOAuthError,
    YandexOAuthFetcher,
    YandexOAuthTimeoutError,
)

logger = logging.getLogger(__name__)


TokenCallback = Callable[["InteractiveYandexSession", OAuthToken], Awaitable[None]]


def _current_ts() -> float:
    return time.monotonic()


@dataclass
class Viewport:
    width: int
    height: int


class InteractiveYandexSession:
    """Controls a single Selenium-driven browser session and exposes interactive hooks."""

    def __init__(
        self,
        *,
        session_id: str,
        telegram_user_id: int,
        auth_url: str,
        headless: bool,
        timeout: int,
        viewport: Viewport,
        loop: asyncio.AbstractEventLoop,
        on_token: TokenCallback,
    ) -> None:
        self.id = session_id
        self.telegram_user_id = telegram_user_id
        self._auth_url = auth_url
        self._headless = headless
        self._timeout = timeout
        self.viewport = viewport
        self._loop = loop
        self._on_token = on_token

        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix=f"yandex-session-{session_id[:8]}"
        )

        self._fetcher = YandexOAuthFetcher(
            auth_url,
            headless=headless,
            timeout=timeout,
        )

        self._driver = None
        self._initialized = False
        self._closed = False

        self._created_at = _current_ts()
        self._last_activity = self._created_at

        self._token: Optional[OAuthToken] = None
        self._token_event = asyncio.Event()

        self._monitor_task: Optional[asyncio.Task[None]] = None

    # --- lifecycle -----------------------------------------------------------------

    def _initialize_driver(self) -> None:
        try:
            driver = self._fetcher._build_driver()  # type: ignore[attr-defined]
        except WebDriverException as exc:  # pragma: no cover - environment specific
            raise YandexOAuthError(f"Не удалось инициализировать браузер: {exc}") from exc

        driver.set_window_size(self.viewport.width, self.viewport.height)
        driver.get(self._auth_url)
        self._driver = driver

    def _start_monitor(self) -> None:
        if self._monitor_task and not self._monitor_task.done():
            return
        self._monitor_task = self._loop.create_task(self._monitor_async())

    def _fetch_logs(self) -> list[Dict[str, Any]]:
        if not self._driver:
            return []
        try:
            return self._driver.get_log("performance")
        except Exception:
            return []

    def _fetch_current_url(self) -> str:
        if not self._driver:
            return ""
        try:
            return self._driver.current_url
        except Exception:
            return ""

    async def _monitor_async(self) -> None:
        deadline = time.time() + self._timeout
        try:
            while not self._closed:
                if self._timeout > 0 and time.time() > deadline:
                    logger.warning(
                        "Interactive session %s timed out while waiting for token",
                        self.id,
                    )
                    self._emit_error(
                        YandexOAuthTimeoutError("Время ожидания токена истекло")
                    )
                    break

                logs = await self._loop.run_in_executor(
                    self._executor, self._fetch_logs
                )
                token: Optional[OAuthToken] = None
                if logs:
                    token = self._fetcher._extract_token_from_logs(logs)  # type: ignore[attr-defined]

                if not token:
                    current_url = await self._loop.run_in_executor(
                        self._executor, self._fetch_current_url
                    )
                    if current_url and "access_token=" in current_url:
                        fragment = current_url.split("#")[-1]
                        token = self._fetcher._token_from_fragment(fragment)  # type: ignore[attr-defined]

                if token:
                    logger.info("Interactive session %s captured OAuth token", self.id)
                    self._emit_token(token)
                    break

                await asyncio.sleep(1.0)
        except asyncio.CancelledError:  # pragma: no cover - cancellation path
            pass

    def _emit_error(self, error: Exception) -> None:
        if isinstance(error, YandexOAuthTimeoutError):
            logger.error("Interactive session %s timed out: %s", self.id, error)
        else:
            logger.exception("Interactive session %s error: %s", self.id, error)

    def _emit_token(self, token: OAuthToken) -> None:
        self._token = token
        self._loop.call_soon_threadsafe(self._token_event.set)
        if self._on_token:
            asyncio.run_coroutine_threadsafe(self._on_token(self, token), self._loop)

    async def start(self) -> None:
        if self._initialized:
            return
        await self._loop.run_in_executor(self._executor, self._initialize_driver)
        self._initialized = True
        self._start_monitor()

    async def close(self) -> None:
        if self._closed:
            return

        self._closed = True
        if self._monitor_task:
            self._monitor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._monitor_task
            self._monitor_task = None

        def _shutdown() -> None:
            try:
                if self._driver:
                    self._driver.quit()
            except Exception:
                pass
            finally:
                self._driver = None

        await self._loop.run_in_executor(self._executor, _shutdown)
        self._executor.shutdown(wait=False)

    # --- helpers -------------------------------------------------------------------

    def touch(self) -> None:
        self._last_activity = _current_ts()

    def is_expired(self, now: float, ttl: float) -> bool:
        return (now - self._last_activity) > ttl

    async def wait_for_token(self) -> Optional[OAuthToken]:
        await self._token_event.wait()
        return self._token

    @property
    def token(self) -> Optional[OAuthToken]:
        return self._token

    # --- rendering -----------------------------------------------------------------

    def _capture_frame(self) -> Optional[str]:
        if self._closed or not self._driver:
            return None
        try:
            raw_base64 = self._driver.get_screenshot_as_base64()
        except Exception:
            return None
        return raw_base64

    async def capture_frame(self) -> Optional[str]:
        return await self._loop.run_in_executor(self._executor, self._capture_frame)

    # --- input events --------------------------------------------------------------

    @staticmethod
    def _modifier_mask(flags: Dict[str, Any]) -> int:
        mask = 0
        if flags.get("alt"):
            mask |= 1  # Alt
        if flags.get("ctrl"):
            mask |= 2  # Control
        if flags.get("meta"):
            mask |= 4  # Meta
        if flags.get("shift"):
            mask |= 8  # Shift
        return mask

    def _dispatch_mouse(self, payload: Dict[str, Any]) -> None:
        if not self._driver:
            return
        event = payload.get("event")
        if event == "move":
            event_type = "mouseMoved"
        elif event == "down":
            event_type = "mousePressed"
        elif event == "up":
            event_type = "mouseReleased"
        elif event == "wheel":
            event_type = "mouseWheel"
        else:
            logger.debug("Unsupported mouse event: %s", event)
            return

        params: Dict[str, Any] = {
            "type": event_type,
            "x": payload.get("x", 0),
            "y": payload.get("y", 0),
            "modifiers": self._modifier_mask(payload.get("modifiers", {})),
        }

        button_map = {0: "left", 1: "middle", 2: "right"}

        if event_type in {"mousePressed", "mouseReleased"}:
            button = button_map.get(payload.get("button", 0), "left")
            params["button"] = button
            params["clickCount"] = payload.get("clickCount", 1)

        buttons = payload.get("buttons")
        if buttons is not None:
            params["buttons"] = int(buttons)

        if event_type == "mouseWheel":
            params["deltaX"] = float(payload.get("deltaX", 0))
            params["deltaY"] = float(payload.get("deltaY", 0))

        try:
            self._driver.execute_cdp_cmd("Input.dispatchMouseEvent", params)
        except Exception:
            logger.debug("Failed to dispatch mouse event", exc_info=True)

    def _dispatch_keyboard(self, payload: Dict[str, Any]) -> None:
        if not self._driver:
            return

        raw_type = payload.get("event")
        if raw_type == "down":
            event_type = "keyDown"
        elif raw_type == "up":
            event_type = "keyUp"
        elif raw_type == "char":
            event_type = "char"
        else:
            logger.debug("Unsupported keyboard event: %s", raw_type)
            return

        key = payload.get("key") or ""
        text = payload.get("text")
        if not text and payload.get("isText", False) and len(key) == 1:
            text = key

        params: Dict[str, Any] = {
            "type": event_type,
            "key": key,
            "code": payload.get("code") or "",
            "text": text or "",
            "unmodifiedText": payload.get("unmodifiedText", text or ""),
            "windowsVirtualKeyCode": int(payload.get("keyCode", 0) or 0),
            "nativeVirtualKeyCode": int(payload.get("keyCode", 0) or 0),
            "modifiers": self._modifier_mask(payload.get("modifiers", {})),
            "autoRepeat": bool(payload.get("repeat", False)),
        }

        try:
            self._driver.execute_cdp_cmd("Input.dispatchKeyEvent", params)
        except Exception:
            logger.debug("Failed to dispatch keyboard event", exc_info=True)

    def _dispatch_scroll(self, payload: Dict[str, Any]) -> None:
        if not self._driver:
            return
        x = payload.get("x", 0)
        y = payload.get("y", 0)
        delta_x = payload.get("deltaX", 0)
        delta_y = payload.get("deltaY", 0)
        params = {
            "type": "mouseWheel",
            "x": x,
            "y": y,
            "deltaX": delta_x,
            "deltaY": delta_y,
            "modifiers": self._modifier_mask(payload.get("modifiers", {})),
        }
        try:
            self._driver.execute_cdp_cmd("Input.dispatchMouseEvent", params)
        except Exception:
            logger.debug("Failed to dispatch scroll event", exc_info=True)

    def _dispatch_event(self, payload: Dict[str, Any]) -> None:
        event_type = payload.get("type")
        if event_type == "mouse":
            self._dispatch_mouse(payload)
        elif event_type == "keyboard":
            self._dispatch_keyboard(payload)
        elif event_type == "scroll":
            self._dispatch_scroll(payload)
        else:
            logger.debug("Unsupported event payload: %s", event_type)

    async def dispatch_event(self, payload: Dict[str, Any]) -> None:
        await self._loop.run_in_executor(self._executor, self._dispatch_event, payload)


class YandexInteractiveSessionManager:
    """High-level registry for interactive OAuth sessions."""

    SESSION_TTL_SECONDS = 15 * 60

    def __init__(
        self,
        *,
        auth_url: str,
        headless: bool,
        timeout: int,
        viewport: Viewport,
        token_callback: Callable[[InteractiveYandexSession, OAuthToken], Awaitable[None]],
    ) -> None:
        self._auth_url = auth_url
        self._headless = headless
        self._timeout = timeout
        self._viewport = viewport
        self._token_callback = token_callback

        self._sessions: Dict[str, InteractiveYandexSession] = {}
        self._sessions_by_user: Dict[int, str] = {}
        self._lock = asyncio.Lock()
        self._cleanup_task: Optional[asyncio.Task[None]] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    async def start_session(self, telegram_user_id: int) -> InteractiveYandexSession:
        loop = asyncio.get_running_loop()
        self._loop = loop
        async with self._lock:
            existing_id = self._sessions_by_user.get(telegram_user_id)
            if existing_id:
                await self._close_session(existing_id)

            session_id = uuid.uuid4().hex
            session = InteractiveYandexSession(
                session_id=session_id,
                telegram_user_id=telegram_user_id,
                auth_url=self._auth_url,
                headless=self._headless,
                timeout=self._timeout,
                viewport=self._viewport,
                loop=loop,
                on_token=self._token_callback,
            )
            await session.start()
            self._sessions[session_id] = session
            self._sessions_by_user[telegram_user_id] = session_id
            return session

    async def get_session(self, session_id: str) -> Optional[InteractiveYandexSession]:
        async with self._lock:
            return self._sessions.get(session_id)

    async def get_session_for_user(self, telegram_user_id: int) -> Optional[InteractiveYandexSession]:
        async with self._lock:
            session_id = self._sessions_by_user.get(telegram_user_id)
            return self._sessions.get(session_id) if session_id else None

    async def _close_session(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if not session:
            return
        self._sessions_by_user.pop(session.telegram_user_id, None)
        try:
            await session.close()
        except Exception:  # pragma: no cover - best effort
            logger.debug("Failed to close session %s", session_id, exc_info=True)

    async def close_session(self, session_id: str) -> None:
        async with self._lock:
            await self._close_session(session_id)

    async def close_all(self) -> None:
        async with self._lock:
            ids = list(self._sessions.keys())
        for session_id in ids:
            await self.close_session(session_id)

    async def _cleanup_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(60)
                now = _current_ts()
                async with self._lock:
                    expired = [
                        session_id
                        for session_id, session in self._sessions.items()
                        if session.is_expired(now, self.SESSION_TTL_SECONDS)
                    ]
                for session_id in expired:
                    logger.info("Cleaning up inactive session %s", session_id)
                    await self.close_session(session_id)
            except asyncio.CancelledError:  # pragma: no cover - shutdown path
                break
            except Exception:
                logger.exception("Interactive session cleanup loop error")

    def ensure_cleanup_task(self) -> None:
        if self._cleanup_task is None or self._cleanup_task.done():
            loop = asyncio.get_running_loop()
            self._cleanup_task = loop.create_task(self._cleanup_loop())

    async def shutdown(self) -> None:
        if self._cleanup_task:
            self._cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._cleanup_task
        await self.close_all()
