import logging
from typing import Any, Dict, List, Optional, Union

from fastapi import HTTPException
from yandex_music import Client as YandexMusicClient
from yandex_music.exceptions import YandexMusicError

from .base import StreamingProvider, StreamingService
from .snapshot import (
    AccountInfo,
    DeclaredItem,
    HistoryDay,
    LikedArtist,
    OwnPlaylist,
    RawTasteSnapshot,
    UserSettingsInfo,
    WaveStation,
)

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
            snapshot.own_playlists = [
                OwnPlaylist(
                    title=str(getattr(pl, "title", "") or "Без названия"),
                    visibility=getattr(pl, "visibility", None),
                    collective=bool(getattr(pl, "collective", False)),
                    likes_count=getattr(pl, "likes_count", 0) or 0,
                    track_count=getattr(pl, "track_count", 0) or 0,
                )
                for pl in playlists or []
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

        # Анамнез: реальная история прослушиваний (music_history)
        try:
            snapshot.history_days = self._collect_history(liked_tracks)
        except Exception:  # noqa: BLE001
            logger.warning("taste snapshot: music_history() failed", exc_info=True)

        # Привязанности: лайкнутые артисты и альбомы
        try:
            likes = self.client.users_likes_artists(with_timestamps=True) or []
            liked_artists: List[LikedArtist] = []
            for like in likes[:200]:
                artist = getattr(like, "artist", None)
                if artist is None or not getattr(artist, "name", None):
                    continue
                liked_artists.append(
                    LikedArtist(
                        name=str(artist.name),
                        genres=list(getattr(artist, "genres", None) or []),
                        timestamp=str(getattr(like, "timestamp", "") or "") or None,
                        disclaimers=[
                            str(d) for d in getattr(artist, "disclaimers", None) or []
                        ],
                    )
                )
            snapshot.liked_artists = liked_artists
        except Exception:  # noqa: BLE001
            logger.warning("taste snapshot: users_likes_artists() failed", exc_info=True)

        try:
            album_likes = self.client.users_likes_albums(rich=True) or []
            snapshot.liked_albums_count = len(album_likes)
            titles = []
            for like in album_likes[:10]:
                album = getattr(like, "album", None)
                if album is not None and getattr(album, "title", None):
                    titles.append(str(album.title))
            snapshot.liked_album_titles = titles
        except Exception:  # noqa: BLE001
            logger.warning("taste snapshot: users_likes_albums() failed", exc_info=True)

        # Моя волна: сохранённые юзером настройки станций
        try:
            stations = self.client.rotor_stations_list() or []
            waves: List[WaveStation] = []
            for result in stations:
                settings = getattr(result, "settings2", None) or getattr(
                    result, "settings", None
                )
                custom_name = getattr(result, "custom_name", None)
                if settings is None and not custom_name:
                    continue
                station = getattr(result, "station", None)
                name = custom_name or getattr(station, "name", None) or "Станция"
                waves.append(
                    WaveStation(
                        name=str(name),
                        mood_energy=getattr(settings, "mood_energy", None),
                        diversity=getattr(settings, "diversity", None),
                        language=getattr(settings, "language", None),
                    )
                )
            snapshot.wave_settings = waves[:8]
        except Exception:  # noqa: BLE001
            logger.warning("taste snapshot: rotor_stations_list() failed", exc_info=True)

        try:
            rotor_status = self.client.rotor_account_status()
            snapshot.skips_per_hour = getattr(rotor_status, "skips_per_hour", None)
        except Exception:  # noqa: BLE001
            logger.warning("taste snapshot: rotor_account_status() failed", exc_info=True)

        # Личность: аккаунт (client.me уже загружен) и настройки
        try:
            snapshot.account = self._collect_account()
        except Exception:  # noqa: BLE001
            logger.warning("taste snapshot: account info failed", exc_info=True)

        try:
            user_settings = self.client.account_settings()
            visibility = getattr(user_settings, "user_music_visibility", None)
            snapshot.settings = UserSettingsInfo(
                theme=getattr(user_settings, "theme", None),
                auto_play_radio=getattr(user_settings, "auto_play_radio", None),
                scrobbling=getattr(
                    user_settings, "last_fm_scrobbling_enabled", None
                ),
                music_visibility=str(visibility) if visibility else None,
                shuffle=getattr(user_settings, "shuffle_enabled", None),
            )
        except Exception:  # noqa: BLE001
            logger.warning("taste snapshot: account_settings() failed", exc_info=True)

        try:
            presaves = self.client.users_presaves()
            items = (
                getattr(presaves, "presaves", None)
                or getattr(presaves, "albums", None)
                or []
            )
            snapshot.presaves_count = len(items)
        except Exception:  # noqa: BLE001
            logger.warning("taste snapshot: users_presaves() failed", exc_info=True)

        # Красные флаги с уже гидрированных лайков: пометки артистов
        try:
            disclaimers: Dict[str, List[str]] = {}
            for track in liked_tracks:
                for artist in getattr(track, "artists", None) or []:
                    marks = getattr(artist, "disclaimers", None) or []
                    if marks and getattr(artist, "name", None):
                        disclaimers[str(artist.name)] = [str(m) for m in marks]
            snapshot.artist_disclaimers = disclaimers or None
        except Exception:  # noqa: BLE001
            pass

        return snapshot

    def _collect_history(self, liked_tracks: List[Any]) -> List[HistoryDay]:
        """music_history → по дням: контекст + названия реально слушанных треков."""
        title_by_id: Dict[str, str] = {}
        for track in liked_tracks:
            track_id = getattr(track, "id", None)
            title = getattr(track, "title", None)
            if track_id is not None and title:
                title_by_id[str(track_id)] = str(title)

        history = self.client.music_history(full_models_count=10)
        days: List[HistoryDay] = []
        for tab in getattr(history, "history_tabs", None) or []:
            date = str(getattr(tab, "date", "") or "")
            for group in getattr(tab, "items", None) or []:
                context_type, context_name = self._history_context(group)
                titles: List[str] = []
                for item in getattr(group, "tracks", None) or []:
                    data = getattr(item, "data", None)
                    full = getattr(data, "full_model", None)
                    title = getattr(full, "title", None)
                    if not title:
                        item_id = getattr(data, "item_id", None)
                        raw_id = getattr(item_id, "id", None)
                        title = title_by_id.get(str(raw_id)) if raw_id else None
                    if title:
                        titles.append(str(title))
                days.append(
                    HistoryDay(
                        date=date,
                        context_type=context_type,
                        context_name=context_name,
                        track_titles=titles[:8],
                    )
                )
            if len(days) >= 20:
                break
        return days

    @staticmethod
    def _history_context(group: Any) -> tuple[str, str]:
        context = getattr(group, "context", None)
        data = getattr(context, "data", None)
        full = getattr(data, "full_model", None)
        if full is not None:
            if getattr(full, "wave", None) is not None:
                return "wave", "Моя волна"
            artist = getattr(full, "artist", None)
            if artist is not None:
                return "artist", str(getattr(artist, "name", "") or "артист")
            playlist = getattr(full, "playlist", None)
            if playlist is not None:
                return "playlist", str(getattr(playlist, "title", "") or "плейлист")
            album = getattr(full, "album", None)
            if album is not None:
                return "album", str(getattr(album, "title", "") or "альбом")
        return str(getattr(context, "type", "") or "unknown"), ""

    def _collect_account(self) -> AccountInfo:
        me = self.client.me
        account = getattr(me, "account", None)
        subscription = getattr(me, "subscription", None)
        plus = getattr(me, "plus", None)
        return AccountInfo(
            birthday=str(getattr(account, "birthday", "") or "") or None,
            registered_at=str(getattr(account, "registered_at", "") or "") or None,
            region=getattr(account, "region", None),
            has_plus=bool(getattr(plus, "has_plus", False)),
            family_subscription=bool(
                getattr(subscription, "family_auto_renewable", None)
            ),
            subeditor=bool(getattr(me, "subeditor", False)),
            child=bool(getattr(account, "child", False)),
        )

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
