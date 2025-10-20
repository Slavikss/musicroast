import asyncio
import contextlib
import os
import time

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import (
    IMAGE_DIR,
    STATIC_DIR,
    YANDEX_OAUTH_HEADLESS,
    YANDEX_OAUTH_INTERACTIVE_HEADLESS,
    YANDEX_OAUTH_TIMEOUT,
    YANDEX_OAUTH_URL,
    YANDEX_OAUTH_VIEWPORT_HEIGHT,
    YANDEX_OAUTH_VIEWPORT_WIDTH,
)
from app.models import (
    PlaylistInfoRequest,
    PlaylistRequest,
    RoastRequest,
    StoredTokenResponse,
    YandexInteractiveSessionRequest,
    YandexInteractiveSessionResponse,
    YandexOAuthRequest,
)
from app.services import MusicRoastService
from app.services.yandex_interactive import (
    InteractiveYandexSession,
    Viewport,
    YandexInteractiveSessionManager,
)
from app.services.yandex_oauth import (
    OAuthToken,
    YandexOAuthAuthenticationError,
    YandexOAuthError,
    YandexOAuthFetcher,
    YandexOAuthTimeoutError,
)
from app.token_storage import token_storage


os.environ["GRPC_TRACE"] = "none"
os.environ["GRPC_VERBOSITY"] = "none"


def _ensure_static_dirs() -> None:
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)


def create_app() -> FastAPI:
    load_dotenv()
    service = MusicRoastService()
    miniapp_path = STATIC_DIR / "miniapp" / "yandex_oauth.html"
    miniapp_html = (
        miniapp_path.read_text(encoding="utf-8") if miniapp_path.exists() else ""
    )

    viewport = Viewport(
        width=YANDEX_OAUTH_VIEWPORT_WIDTH,
        height=YANDEX_OAUTH_VIEWPORT_HEIGHT,
    )

    async def _store_interactive_token(
        session: InteractiveYandexSession, token: OAuthToken
    ) -> None:
        await token_storage.set(
            str(session.telegram_user_id), token.access_token, ttl=token.expires_in
        )

    interactive_manager = YandexInteractiveSessionManager(
        auth_url=YANDEX_OAUTH_URL,
        headless=YANDEX_OAUTH_INTERACTIVE_HEADLESS,
        timeout=YANDEX_OAUTH_TIMEOUT,
        viewport=viewport,
        token_callback=_store_interactive_token,
    )

    app = FastAPI(
        title="MusicRoast API",
        description="API для генерации прожарок на основе музыкальной библиотеки пользователя",
        version="1.0.0",
    )

    _ensure_static_dirs()
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/miniapp/yandex", response_class=HTMLResponse)
    async def serve_yandex_miniapp() -> HTMLResponse:
        """Отдаёт HTML-страницу Telegram Mini App для авторизации в Яндекс Музыке."""
        if miniapp_path.exists():
            html_content = miniapp_path.read_text(encoding="utf-8")
        elif miniapp_html:
            html_content = miniapp_html
        else:
            raise HTTPException(
                status_code=503, detail="Mini app шаблон временно недоступен"
            )
        return HTMLResponse(content=html_content)

    @app.post(
        "/auth/yandex/session",
        response_model=YandexInteractiveSessionResponse,
    )
    async def create_yandex_interactive_session(
        payload: YandexInteractiveSessionRequest,
    ) -> YandexInteractiveSessionResponse:
        """Создаёт интерактивную сессию Selenium для авторизации пользователя."""
        try:
            session = await interactive_manager.start_session(
                payload.telegram_user_id
            )
        except YandexOAuthError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        interactive_manager.ensure_cleanup_task()

        return YandexInteractiveSessionResponse(
            session_id=session.id,
            viewport_width=session.viewport.width,
            viewport_height=session.viewport.height,
        )

    @app.post("/auth/yandex/session/{session_id}/close")
    async def close_yandex_interactive_session(session_id: str) -> JSONResponse:
        """Завершает интерактивную сессию."""
        await interactive_manager.close_session(session_id)
        return JSONResponse(content={"status": "closed", "session_id": session_id})

    @app.websocket("/ws/auth/yandex/session/{session_id}")
    async def yandex_interactive_ws(websocket: WebSocket, session_id: str) -> None:
        await websocket.accept()
        session = await interactive_manager.get_session(session_id)
        if not session:
            await websocket.send_json(
                {"type": "error", "message": "Сессия не найдена или устарела."}
            )
            await websocket.close()
            return

        session.touch()

        await websocket.send_json(
            {
                "type": "init",
                "session_id": session.id,
                "width": session.viewport.width,
                "height": session.viewport.height,
            }
        )

        async def stream_frames() -> None:
            try:
                while True:
                    frame = await session.capture_frame()
                    if frame is None:
                        await asyncio.sleep(0.5)
                        continue
                    await websocket.send_json(
                        {"type": "frame", "image": frame, "ts": time.time()}
                    )
                    session.touch()
                    await asyncio.sleep(0.4)
            except asyncio.CancelledError:
                pass

        async def forward_token() -> None:
            token = await session.wait_for_token()
            if token:
                await websocket.send_json(
                    {
                        "type": "token",
                        "access_token": token.access_token,
                        "expires_in": token.expires_in,
                    }
                )

        async def handle_messages() -> None:
            try:
                while True:
                    data = await websocket.receive_json()
                    msg_type = data.get("type")
                    if msg_type in {"mouse", "keyboard", "scroll"}:
                        session.touch()
                        await session.dispatch_event(data)
                    elif msg_type == "ping":
                        session.touch()
                        await websocket.send_json(
                            {"type": "pong", "ts": time.time()}
                        )
            except (WebSocketDisconnect, asyncio.CancelledError):
                pass

        tasks = [
            asyncio.create_task(stream_frames()),
            asyncio.create_task(forward_token()),
            asyncio.create_task(handle_messages()),
        ]

        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            with contextlib.suppress(Exception):
                task.result()
        for task in pending:
            task.cancel()
        for task in pending:
            with contextlib.suppress(Exception):
                await task

    @app.post("/auth/yandex/token", response_model=StoredTokenResponse)
    async def obtain_yandex_token(payload: YandexOAuthRequest) -> StoredTokenResponse:
        """Получает access_token из OAuth-редиректа Яндекс Музыки и сохраняет его в хранилище."""

        headless = (
            YANDEX_OAUTH_HEADLESS if payload.headless is None else payload.headless
        )
        fetcher = YandexOAuthFetcher(
            YANDEX_OAUTH_URL,
            headless=headless,
            timeout=YANDEX_OAUTH_TIMEOUT,
        )

        loop = asyncio.get_running_loop()

        def _fetch() -> OAuthToken:
            return fetcher.fetch_token(
                payload.username, payload.password, otp=payload.otp
            )

        try:
            token = await loop.run_in_executor(None, _fetch)
        except YandexOAuthAuthenticationError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except YandexOAuthTimeoutError as exc:
            raise HTTPException(status_code=504, detail=str(exc)) from exc
        except YandexOAuthError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        await token_storage.set(
            str(payload.telegram_user_id), token.access_token, ttl=token.expires_in
        )

        return StoredTokenResponse(
            access_token=token.access_token, expires_in=token.expires_in
        )

    @app.get(
        "/auth/yandex/token/{telegram_user_id}",
        response_model=StoredTokenResponse,
    )
    async def get_stored_yandex_token(telegram_user_id: int) -> StoredTokenResponse:
        """Возвращает ранее сохранённый токен пользователя, если он ещё валиден."""
        record = await token_storage.get_record(str(telegram_user_id))
        if not record:
            raise HTTPException(status_code=404, detail="Токен не найден")

        expires_in = None
        if record.expires_at:
            remaining = int(record.expires_at - time.time())
            if remaining > 0:
                expires_in = remaining

        return StoredTokenResponse(access_token=record.token, expires_in=expires_in)

    @app.post("/streaming/playlists")
    async def get_streaming_playlists(request: PlaylistRequest):
        """Возвращает список плейлистов выбранного стриминга"""
        try:
            result = service.list_playlists(request)
            return JSONResponse(content=result)
        except HTTPException as exc:
            raise exc
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Не удалось получить список плейлистов: {exc}",
            ) from exc

    @app.post("/streaming/playlist-info")
    async def get_streaming_playlist_info(request: PlaylistInfoRequest):
        """Возвращает подробную информацию по плейлисту из выбранного стриминга"""
        try:
            result = service.get_playlist_info(request)
            return JSONResponse(content=result)
        except HTTPException as exc:
            raise exc
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Не удалось получить информацию о плейлисте: {exc}",
            ) from exc

    @app.post("/roast")
    async def get_roast(request: RoastRequest):
        """Получение прожарки для выбранного плейлиста"""
        try:
            result = service.generate_roast(request)
            return JSONResponse(content=result)
        except HTTPException as exc:
            raise exc
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail=f"Не удалось выполнить прожарку: {exc}"
            ) from exc

    @app.on_event("startup")
    async def _interactive_startup() -> None:
        interactive_manager.ensure_cleanup_task()

    @app.on_event("shutdown")
    async def _interactive_shutdown() -> None:
        await interactive_manager.shutdown()

    return app


app = create_app()


__all__ = ["create_app", "app"]
