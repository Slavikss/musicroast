import asyncio
import contextlib
import logging
import os
import time
from contextlib import asynccontextmanager
from functools import partial
from typing import Any, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import APP_MODE, IMAGE_DIR, STATIC_DIR, WEB_DIR, YANDEX_OAUTH_URL
from app.models import (
    BattleJoinRequest,
    BattleStartRequest,
    DeviceAuthStartResponse,
    DeviceAuthStatusResponse,
    PlaylistInfoRequest,
    PlaylistRequest,
    RoastRequest,
    TokenValidationRequest,
    TokenValidationResponse,
)
from app.prompts import RoastLevel
from app.services import MusicRoastService
from app.services.battle import BattleError, BattleSide, battle_registry
from app.services.device_auth import READY, device_auth_manager
from app.services.roast_registry import RoastRecord, roast_registry
from app.streaming import StreamingProvider
from app.utils import parse_token_input

logger = logging.getLogger(__name__)

os.environ.setdefault("GRPC_TRACE", "none")
os.environ.setdefault("GRPC_VERBOSITY", "none")


def _ensure_static_dirs() -> None:
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)


async def _in_executor(func, *args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(func, *args, **kwargs))


def create_app() -> FastAPI:
    load_dotenv()
    service = MusicRoastService()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        bot_task: Optional[asyncio.Task] = None
        if APP_MODE in {"both", "bot"} and os.getenv("TELEGRAM_BOT_TOKEN"):
            from app.bot import run_bot

            bot_task = asyncio.create_task(run_bot(service))
            logger.info("Telegram bot polling started (APP_MODE=%s)", APP_MODE)
        yield
        if bot_task:
            bot_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await bot_task

    app = FastAPI(
        title="MusicRoast API",
        description="API для генерации прожарок на основе музыкальной библиотеки пользователя",
        version="2.0.0",
        lifespan=lifespan,
    )

    _ensure_static_dirs()
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # ------------------------------------------------------------------ web

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        index_path = WEB_DIR / "index.html"
        if not index_path.exists():
            raise HTTPException(status_code=404, detail="Веб-интерфейс не найден")
        return FileResponse(index_path)

    # ----------------------------------------------------------------- auth

    @app.get("/auth/yandex/url")
    async def get_auth_url() -> JSONResponse:
        """OAuth-ссылка для ручного (fallback) получения токена."""
        return JSONResponse(content={"auth_url": YANDEX_OAUTH_URL})

    @app.post("/auth/device/start", response_model=DeviceAuthStartResponse)
    async def start_device_auth() -> DeviceAuthStartResponse:
        """Запускает OAuth Device Flow: код + ссылка для подтверждения."""
        try:
            session = await device_auth_manager.start()
        except Exception as exc:  # noqa: BLE001
            logger.exception("device auth start failed")
            raise HTTPException(
                status_code=502,
                detail="Яндекс не выдал код устройства, попробуй позже",
            ) from exc
        return DeviceAuthStartResponse(
            session_id=session.id,
            verification_url=session.verification_url,
            user_code=session.user_code,
            expires_in=max(int(session.expires_at - time.time()), 0),
            interval=session.interval,
        )

    @app.get("/auth/device/{session_id}", response_model=DeviceAuthStatusResponse)
    async def device_auth_status(session_id: str) -> DeviceAuthStatusResponse:
        session = await device_auth_manager.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Сессия не найдена")

        if session.status == READY and session.tokens:
            if session.account is None:
                session.account = await _in_executor(
                    service.validate_token,
                    StreamingProvider.YANDEX,
                    session.tokens.access_token,
                )
            return DeviceAuthStatusResponse(
                status=session.status,
                access_token=session.tokens.access_token,
                expires_in=session.tokens.expires_in,
                account=session.account,
            )
        return DeviceAuthStatusResponse(status=session.status)

    @app.post("/auth/validate", response_model=TokenValidationResponse)
    async def validate_token(payload: TokenValidationRequest) -> TokenValidationResponse:
        """Принимает URL/фрагмент/голый токен, валидирует через API стриминга."""
        parsed = parse_token_input(payload.raw_input)
        if not parsed:
            raise HTTPException(
                status_code=422,
                detail="Не нашёл токен: пришли ссылку после редиректа или сам токен",
            )
        account = await _in_executor(
            service.validate_token, StreamingProvider.YANDEX, parsed.access_token
        )
        return TokenValidationResponse(
            access_token=parsed.access_token,
            expires_in=parsed.expires_in,
            account=account,
        )

    # ------------------------------------------------------------- streaming

    @app.post("/streaming/playlists")
    async def get_streaming_playlists(request: PlaylistRequest):
        """Возвращает список плейлистов выбранного стриминга"""
        try:
            result = await _in_executor(service.list_playlists, request)
            return JSONResponse(content=result)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Не удалось получить список плейлистов: {exc}",
            ) from exc

    @app.post("/streaming/playlist-info")
    async def get_streaming_playlist_info(request: PlaylistInfoRequest):
        """Возвращает подробную информацию по плейлисту из выбранного стриминга"""
        try:
            result = await _in_executor(service.get_playlist_info, request)
            return JSONResponse(content=result)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Не удалось получить информацию о плейлисте: {exc}",
            ) from exc

    # ----------------------------------------------------------------- roast

    @app.post("/roast")
    async def get_roast(request: RoastRequest):
        """Прожарка выбранного плейлиста + регистрация для шеринга."""
        try:
            result = await _in_executor(service.generate_roast, request)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail=f"Не удалось выполнить прожарку: {exc}"
            ) from exc

        prepared_stats = result.get("stats") or {}
        record = RoastRecord(
            roast_id="",
            user_key=f"web:{request.display_name or 'аноним'}",
            display_name=request.display_name or "аноним",
            text=result["roast"],
            score=result["score"],
            diagnosis=result["diagnosis"],
            shame_facts=result.get("shame_facts", []),
            stats_block="",
            sample_lines="",
            playlist_title=result.get("playlist", {}).get("title", ""),
            level=result.get("level", RoastLevel.MEDIUM.value),
        )
        result["roast_id"] = await roast_registry.create(record)
        return JSONResponse(content=result)

    @app.get("/roast/{roast_id}/teaser")
    async def roast_teaser(roast_id: str):
        """Тизер чужой прожарки: счёт + диагноз + первый панч."""
        record = await roast_registry.get(roast_id)
        if not record:
            raise HTTPException(status_code=404, detail="Прожарка уже сгорела")
        first_punch = record.text.strip().splitlines()[0] if record.text else ""
        return JSONResponse(
            content={
                "display_name": record.display_name,
                "score": record.score,
                "diagnosis": record.diagnosis,
                "first_punch": first_punch,
                "level": record.level,
            }
        )

    # ---------------------------------------------------------------- battle

    def _build_side(
        access_token: str, display_name: str, level: RoastLevel
    ) -> BattleSide:
        return service.make_battle_side(
            display_name=display_name,
            provider=StreamingProvider.YANDEX,
            access_token=access_token,
            level=level,
        )

    @app.post("/battle/start")
    async def battle_start(payload: BattleStartRequest):
        side = await _in_executor(
            _build_side, payload.access_token, payload.display_name, payload.level
        )
        battle = await battle_registry.create(side, payload.level.value)
        return JSONResponse(
            content={
                "code": battle.code,
                "status": battle.status,
                "initiator": {
                    "display_name": side.display_name,
                    "score": side.score,
                    "diagnosis": side.diagnosis,
                },
            }
        )

    @app.post("/battle/join")
    async def battle_join(payload: BattleJoinRequest):
        battle = await battle_registry.get(payload.code)
        if not battle:
            raise HTTPException(status_code=404, detail="Батл не найден или сгорел")

        level = RoastLevel(battle.level)
        side = await _in_executor(
            _build_side, payload.access_token, payload.display_name, level
        )
        try:
            battle = await battle_registry.join(payload.code, side)
        except BattleError as exc:
            raise HTTPException(status_code=409, detail=exc.message) from exc

        verdict = await _in_executor(
            service.generate_battle, battle.initiator, battle.opponent
        )
        await battle_registry.set_result(
            battle.code, f"{verdict['header']}\n\n{verdict['text']}", verdict["winner"]
        )
        refreshed = await battle_registry.get(battle.code)
        return JSONResponse(
            content={
                "code": battle.code,
                "status": refreshed.status if refreshed else "done",
                "result": refreshed.result if refreshed else None,
                "winner": refreshed.winner if refreshed else None,
            }
        )

    @app.get("/battle/{code}")
    async def battle_status(code: str):
        battle = await battle_registry.get(code)
        if not battle:
            raise HTTPException(status_code=404, detail="Батл не найден или сгорел")
        return JSONResponse(
            content={
                "code": battle.code,
                "status": battle.status,
                "level": battle.level,
                "initiator_name": battle.initiator.display_name,
                "result": battle.result,
                "winner": battle.winner,
            }
        )

    return app


app: Optional[Any] = None
if os.getenv("MUSICROAST_LAZY_APP", "").lower() not in {"1", "true"}:
    try:
        app = create_app()
    except ValueError as exc:
        logger.warning("App not created at import time: %s", exc)


__all__ = ["create_app", "app"]
