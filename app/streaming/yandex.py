import logging
from typing import Any, Dict, List, Optional, Union

from fastapi import HTTPException
from yandex_music import Client as YandexMusicClient
from yandex_music.exceptions import YandexMusicError

from .base import StreamingProvider, StreamingService
from .snapshot import DeclaredItem, RawTasteSnapshot

logger = logging.getLogger(__name__)


_TRACKS_CHUNK_SIZE = 250


class YandexMusicStreamingService(StreamingService):
    """Интеграция со стримингом Яндекс Музыка."""

    provider: StreamingProvider = StreamingProvider.YANDEX

    @staticmethod
    def _is_self_owner(owner_id: Union[str, int, None]) -> bool:
        if owner_id is None:
            return True
        if isinstance(owner_id, int):
            return False
        return str(owner_id).strip().lower() in {"", "me", "self"}

    def __init__(self, token: str):
        super().__init__(token)
        try:
            # init() подтягивает статус аккаунта (client.me) и сразу
            # отсеивает невалидные токены
            client = YandexMusicClient(token).init()
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

    def _fetch_tracks_chunked(self, track_ids: List[str]) -> List[Any]:
        """Забирает полные объекты треков порциями, чтобы не переполнить запрос."""
        full_tracks: List[Any] = []
        for offset in range(0, len(track_ids), _TRACKS_CHUNK_SIZE):
            chunk = track_ids[offset : offset + _TRACKS_CHUNK_SIZE]
            full_tracks.extend(self.client.tracks(chunk) or [])
        return full_tracks

    def get_account_summary(self) -> Dict[str, Any]:
        """Возвращает имя/uid аккаунта и число лайкнутых треков для валидации токена."""
        try:
            profile = self.client.me
            account = getattr(profile, "account", None)
            display_name = None
            uid = None
            if account is not None:
                display_name = (
                    getattr(account, "display_name", None)
                    or getattr(account, "first_name", None)
                    or getattr(account, "login", None)
                )
                uid = getattr(account, "uid", None)

            likes_summary = self.client.users_likes_tracks()
            liked_count = len(getattr(likes_summary, "tracks", []) or [])

            return {
                "uid": str(uid) if uid else None,
                "display_name": display_name or "меломан",
                "liked_count": liked_count,
            }
        except HTTPException:
            raise
        except YandexMusicError as exc:
            raise HTTPException(
                status_code=401,
                detail="Токен не подошёл: Яндекс Музыка его не приняла",
            ) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Не удалось проверить токен: {str(exc)}",
            ) from exc

    def _get_current_user_uid(self) -> str:
        """Возвращает UID текущего пользователя из профиля."""
        try:
            profile = self.client.me
        except YandexMusicError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Ошибка Yandex Music API при запросе профиля: {str(exc)}",
            ) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Не удалось получить профиль пользователя: {str(exc)}",
            ) from exc

        uid: Union[str, int, None]
        account = getattr(profile, "account", None)
        if account is not None:
            uid = getattr(account, "uid", None)
        else:
            uid = getattr(profile, "uid", None)

        if not uid:
            raise HTTPException(
                status_code=500,
                detail="Не удалось определить идентификатор пользователя Yandex Music",
            )

        return str(uid)

    def list_playlists(
        self, owner_id: Union[str, int] = "me"
    ) -> List[Dict[str, Any]]:
        """Возвращает список плейлистов пользователя, включая «Мне нравится»."""
        try:
            current_user_uid = self._get_current_user_uid()
            target_user_id = (
                None if self._is_self_owner(owner_id) else str(owner_id)
            )
            playlists: List[Dict[str, Any]] = []

            likes_summary = self.client.users_likes_tracks()
            likes_count = len(getattr(likes_summary, "tracks", []) or [])
            playlists.append(
                {
                    "kind": "liked",
                    "title": "Мне нравится",
                    "track_count": likes_count,
                    "owner_uid": current_user_uid,
                    "visibility": "private",
                    "is_liked": True,
                    "description": "Лайкнутые треки пользователя",
                }
            )

            personal_playlists = self.client.users_playlists_list(
                user_id=target_user_id
            )
            for playlist in personal_playlists or []:
                owner = getattr(playlist, "owner", None)
                playlists.append(
                    {
                        "kind": getattr(playlist, "kind", None),
                        "title": getattr(playlist, "title", "") or "Без названия",
                        "track_count": getattr(playlist, "track_count", 0) or 0,
                        "owner_uid": getattr(owner, "uid", None) or current_user_uid,
                        "visibility": getattr(playlist, "visibility", None),
                        "is_liked": False,
                        "description": getattr(playlist, "description", None),
                    }
                )

            return playlists
        except HTTPException:
            raise
        except YandexMusicError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Ошибка Yandex Music API при получении плейлистов: {str(exc)}",
            ) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Ошибка получения списка плейлистов: {str(exc)}",
            ) from exc

    def get_liked_tracks(self) -> tuple[List[Any], Dict[str, str], Dict[str, Any]]:
        """Получение лайкнутых треков."""
        try:
            owner_uid = self._get_current_user_uid()
            liked_tracks_ids = self.client.users_likes_tracks()
            if not liked_tracks_ids or not liked_tracks_ids.tracks:
                return (
                    [],
                    {},
                    {
                        "kind": "liked",
                        "title": "Мне нравится",
                        "track_count": 0,
                        "owner_uid": owner_uid,
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

            full_tracks = self._fetch_tracks_chunked(track_ids) if track_ids else []
            return (
                full_tracks or [],
                added_dates,
                {
                    "kind": "liked",
                    "title": "Мне нравится",
                    "track_count": len(track_ids),
                    "owner_uid": owner_uid,
                    "is_liked": True,
                },
            )

        except HTTPException:
            raise
        except YandexMusicError as exc:
            raise HTTPException(
                status_code=502, detail=f"Ошибка Yandex Music API: {str(exc)}"
            ) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail=f"Ошибка Yandex Music API: {str(exc)}"
            ) from exc

    # ------------------------------------------------------ taste snapshot

    def get_taste_snapshot(self) -> RawTasteSnapshot:
        """Сырой снапшот вкуса: лайки (спина) + чарт + заявленное + дизлайки.

        Каждая группа вызовов независима: отказ одной оставляет поле None,
        соответствующая ось диагноза просто не сыграет.
        """
        liked_tracks, liked_added, _ = self.get_liked_tracks()
        snapshot = RawTasteSnapshot(
            liked_tracks=liked_tracks, liked_added=liked_added
        )

        # Чарт — ось обскурности
        try:
            chart_info = self.client.chart()
            positions: Dict[str, int] = {}
            chart_playlist = getattr(chart_info, "chart", None)
            for short in getattr(chart_playlist, "tracks", None) or []:
                track = getattr(short, "track", None)
                chart_meta = getattr(short, "chart", None)
                if track is not None and getattr(track, "id", None) is not None:
                    positions[str(track.id)] = getattr(chart_meta, "position", 0) or 0
            snapshot.chart_positions = positions
        except Exception:  # noqa: BLE001
            logger.warning("taste snapshot: chart() failed", exc_info=True)

        # Заявленное — pins и имена плейлистов (ось разрыва)
        try:
            pins_list = self.client.pins()
            declared: List[DeclaredItem] = []
            for pin in getattr(pins_list, "pins", None) or []:
                data = getattr(pin, "data", None)
                name = getattr(data, "name", None) or getattr(data, "title", None)
                kind = (getattr(pin, "type", "") or "").replace("_item", "")
                if name:
                    declared.append(DeclaredItem(kind=kind or "unknown", name=str(name)))
            snapshot.pins = declared
        except Exception:  # noqa: BLE001
            logger.warning("taste snapshot: pins() failed", exc_info=True)

        try:
            playlists = self.client.users_playlists_list()
            snapshot.playlist_titles = [
                str(pl.title) for pl in playlists or [] if getattr(pl, "title", None)
            ]
        except Exception:  # noqa: BLE001
            logger.warning("taste snapshot: users_playlists_list() failed", exc_info=True)

        # Граница вкуса — дизлайки (ось лицемерия)
        try:
            disliked_artists = self.client.users_dislikes_artists()
            snapshot.disliked_artist_names = [
                str(a.name) for a in disliked_artists or [] if getattr(a, "name", None)
            ]
        except Exception:  # noqa: BLE001
            logger.warning("taste snapshot: users_dislikes_artists() failed", exc_info=True)

        try:
            disliked = self.client.users_dislikes_tracks()
            short_ids = list(getattr(disliked, "tracks_ids", None) or [])
            if short_ids and len(short_ids) <= 300:
                artists: List[str] = []
                genres: List[str] = []
                for track in self._fetch_tracks_chunked(short_ids):
                    for artist in getattr(track, "artists", None) or []:
                        if getattr(artist, "name", None):
                            artists.append(str(artist.name))
                    for album in getattr(track, "albums", None) or []:
                        if getattr(album, "genre", None):
                            genres.append(str(album.genre))
                snapshot.disliked_track_artists = artists
                snapshot.disliked_genres = genres
        except Exception:  # noqa: BLE001
            logger.warning("taste snapshot: users_dislikes_tracks() failed", exc_info=True)

        return snapshot

    def get_artist_popularity(self, artist_ids: List[int]) -> Dict[int, Optional[int]]:
        """last_month_listeners по top-K артистам головы. Лениво и дозированно."""
        popularity: Dict[int, Optional[int]] = {}
        for artist_id in artist_ids[:5]:
            try:
                info = self.client.artists_brief_info(artist_id)
                stats = getattr(info, "stats", None)
                popularity[artist_id] = getattr(stats, "last_month_listeners", None)
            except Exception:  # noqa: BLE001
                popularity[artist_id] = None
        return popularity

    def fetch_lyrics(self, track_id: str) -> Optional[str]:
        """Текст трека (формат TEXT) или None. Зовётся только за гейтом."""
        try:
            plain_id = str(track_id).split(":", 1)[0]
            lyrics = self.client.tracks_lyrics(plain_id, format_="TEXT")
            if lyrics is None:
                return None
            return lyrics.fetch_lyrics()
        except Exception:  # noqa: BLE001
            return None

    def get_playlist_tracks(
        self, playlist_kind: Union[int, str], owner_id: Union[str, int] = "me"
    ) -> tuple[List[Any], Dict[str, str], Dict[str, Any]]:
        """Получение треков выбранного плейлиста."""
        if str(playlist_kind).lower() in {"liked", "likes", "favorite"}:
            return self.get_liked_tracks()

        try:
            current_user_uid = self._get_current_user_uid()
            target_user_id = (
                None if self._is_self_owner(owner_id) else str(owner_id)
            )
            playlist = self.client.users_playlists(
                kind=playlist_kind, user_id=target_user_id
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

            tracks = self._fetch_tracks_chunked(track_ids) if track_ids else []

            owner = getattr(playlist, "owner", None)
            metadata = {
                "kind": getattr(playlist, "kind", None),
                "title": getattr(playlist, "title", "") or "Без названия",
                "track_count": getattr(playlist, "track_count", len(track_ids)),
                "owner_uid": getattr(owner, "uid", None)
                or (str(owner_id) if not self._is_self_owner(owner_id) else current_user_uid),
                "is_liked": False,
                "visibility": getattr(playlist, "visibility", None),
                "description": getattr(playlist, "description", None),
            }

            return tracks or [], added_dates, metadata

        except HTTPException:
            raise
        except YandexMusicError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Ошибка Yandex Music API при получении плейлиста: {str(exc)}",
            ) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail=f"Не удалось получить плейлист: {str(exc)}"
            ) from exc
