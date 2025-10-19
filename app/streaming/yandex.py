from typing import Any, Dict, List, Union

from fastapi import HTTPException
from yandex_music import Client as YandexMusicClient
from yandex_music.exceptions import YandexMusicError

from .base import StreamingProvider, StreamingService


class YandexMusicStreamingService(StreamingService):
    """Интеграция со стримингом Яндекс Музыка."""

    provider: StreamingProvider = StreamingProvider.YANDEX

    def __init__(self, token: str):
        super().__init__(token)
        try:
            client = YandexMusicClient(token)
        except YandexMusicError as exc:
            raise HTTPException(
                status_code=401, detail="Неверный или истёкший токен Yandex Music"
            ) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Не удалось инициализировать клиента Yandex Music: {str(exc)}",
            ) from exc
        self.client = client

    def list_playlists(
        self, owner_id: Union[str, int] = "me"
    ) -> List[Dict[str, Any]]:
        """Возвращает список плейлистов пользователя, включая «Мне нравится»."""
        try:
            playlists: List[Dict[str, Any]] = []

            likes_summary = self.client.users_likes_tracks()
            likes_count = len(getattr(likes_summary, "tracks", []) or [])
            playlists.append(
                {
                    "kind": "liked",
                    "title": "Мне нравится",
                    "track_count": likes_count,
                    "owner_uid": "me",
                    "visibility": "private",
                    "is_liked": True,
                    "description": "Лайкнутые треки пользователя",
                }
            )

            personal_playlists = self.client.users_playlists_list(
                user_id=str(owner_id or "me")
            )
            for playlist in personal_playlists or []:
                owner = getattr(playlist, "owner", None)
                playlists.append(
                    {
                        "kind": getattr(playlist, "kind", None),
                        "title": getattr(playlist, "title", "") or "Без названия",
                        "track_count": getattr(playlist, "track_count", 0) or 0,
                        "owner_uid": getattr(owner, "uid", "me"),
                        "visibility": getattr(playlist, "visibility", None),
                        "is_liked": False,
                        "description": getattr(playlist, "description", None),
                    }
                )

            return playlists
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Ошибка получения списка плейлистов: {str(exc)}",
            ) from exc

    def get_liked_tracks(self) -> tuple[List[Any], Dict[str, str], Dict[str, Any]]:
        """Получение лайкнутых треков."""
        try:
            liked_tracks_ids = self.client.users_likes_tracks()
            if not liked_tracks_ids or not liked_tracks_ids.tracks:
                return (
                    [],
                    {},
                    {
                        "kind": "liked",
                        "title": "Мне нравится",
                        "track_count": 0,
                        "owner_uid": "me",
                        "is_liked": True,
                    },
                )

            track_ids: List[str] = []
            added_dates: Dict[str, str] = {}
            for track_short in liked_tracks_ids.tracks:
                if getattr(track_short, "id", None) and getattr(
                    track_short, "album_id", None
                ):
                    track_ids.append(f"{track_short.id}:{track_short.album_id}")
                if hasattr(track_short, "timestamp") and track_short.timestamp:
                    added_dates[str(track_short.id)] = str(track_short.timestamp)

            full_tracks = self.client.tracks(track_ids) if track_ids else []
            return (
                full_tracks or [],
                added_dates,
                {
                    "kind": "liked",
                    "title": "Мне нравится",
                    "track_count": len(track_ids),
                    "owner_uid": "me",
                    "is_liked": True,
                },
            )

        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail=f"Ошибка Yandex Music API: {str(exc)}"
            ) from exc

    def get_playlist_tracks(
        self, playlist_kind: Union[int, str], owner_id: Union[str, int] = "me"
    ) -> tuple[List[Any], Dict[str, str], Dict[str, Any]]:
        """Получение треков выбранного плейлиста."""
        if str(playlist_kind).lower() in {"liked", "likes", "favorite"}:
            return self.get_liked_tracks()

        try:
            playlist = self.client.users_playlists(
                kind=playlist_kind, user_id=str(owner_id or "me")
            )

            if not playlist:
                raise HTTPException(status_code=404, detail="Плейлист не найден")

            tracks_brief = getattr(playlist, "tracks", []) or []
            track_ids: List[str] = []
            added_dates: Dict[str, str] = {}

            for playlist_track in tracks_brief:
                track_obj = getattr(playlist_track, "track", None)
                track_id = getattr(track_obj, "id", None) or getattr(
                    playlist_track, "id", None
                )
                album_id = None
                if track_obj and getattr(track_obj, "albums", None):
                    first_album = track_obj.albums[0]
                    album_id = getattr(first_album, "id", None)
                album_id = album_id or getattr(playlist_track, "album_id", None)

                if track_id and album_id:
                    track_ids.append(f"{track_id}:{album_id}")

                timestamp = getattr(playlist_track, "timestamp", None)
                if timestamp:
                    added_dates[str(track_id)] = str(timestamp)

            tracks = self.client.tracks(track_ids) if track_ids else []

            owner = getattr(playlist, "owner", None)
            metadata = {
                "kind": getattr(playlist, "kind", None),
                "title": getattr(playlist, "title", "") or "Без названия",
                "track_count": getattr(playlist, "track_count", len(track_ids)),
                "owner_uid": getattr(owner, "uid", str(owner_id or "me")),
                "is_liked": False,
                "visibility": getattr(playlist, "visibility", None),
                "description": getattr(playlist, "description", None),
            }

            return tracks or [], added_dates, metadata

        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail=f"Не удалось получить плейлист: {str(exc)}"
            ) from exc
