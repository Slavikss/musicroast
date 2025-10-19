import os
from typing import Callable

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import IMAGE_DIR, STATIC_DIR
from app.models import PlaylistInfoRequest, PlaylistRequest, RoastRequest
from app.services import MusicRoastService


os.environ["GRPC_TRACE"] = "none"
os.environ["GRPC_VERBOSITY"] = "none"


def _ensure_static_dirs() -> None:
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)


def create_app() -> FastAPI:
    load_dotenv()
    service = MusicRoastService()

    app = FastAPI(
        title="MusicRoast API",
        description="API для генерации прожарок на основе музыкальной библиотеки пользователя",
        version="1.0.0",
    )

    _ensure_static_dirs()
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

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

    return app


app = create_app()


__all__ = ["create_app", "app"]
