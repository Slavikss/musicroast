from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from app.models import Track


class TrackNormalizer:
    """Класс для нормализации данных треков."""

    @staticmethod
    def _extract_album_info(track_obj: Any) -> tuple[Optional[int], Optional[str]]:
        if not track_obj:
            return None, None

        albums = getattr(track_obj, "albums", None)
        if albums and len(albums) > 0:
            album = albums[0]
            return getattr(album, "year", None), getattr(album, "genre", None)
        return None, None

    @staticmethod
    def _coerce_added_date(raw_value: Any) -> Optional[str]:
        if not raw_value:
            return None

        if isinstance(raw_value, datetime):
            return raw_value.date().isoformat()

        raw_str = str(raw_value)
        try:
            return datetime.fromisoformat(raw_str).date().isoformat()
        except ValueError:
            pass

        try:
            timestamp = float(raw_str)
            return datetime.fromtimestamp(timestamp).date().isoformat()
        except (ValueError, OSError):
            return None

    @staticmethod
    def normalize_tracks(
        tracks: List, added_at: Dict[str, str] = None
    ) -> List[Track]:
        compact: List[Track] = []
        for t in tracks:
            try:
                track_id = getattr(t, "id", None) or (
                    t.track.id if hasattr(t, "track") and getattr(t, "track") else None
                )
                title = getattr(t, "title", None) or (
                    t.track.title
                    if hasattr(t, "track") and getattr(t, "track")
                    else None
                )
                src_artists = getattr(t, "artists", None) or (
                    getattr(t, "track", None).artists
                    if getattr(t, "track", None)
                    else None
                )
                artists = [
                    a.name for a in (src_artists or []) if getattr(a, "name", None)
                ]

                album_year, album_genre = TrackNormalizer._extract_album_info(t)
                if (
                    album_year is None
                    and album_genre is None
                    and getattr(t, "track", None)
                ):
                    album_year, album_genre = TrackNormalizer._extract_album_info(
                        getattr(t, "track", None)
                    )

                added_date = None
                if added_at and track_id and str(track_id) in added_at:
                    added_date = TrackNormalizer._coerce_added_date(
                        added_at[str(track_id)]
                    )

                track_model = Track(
                    title=title or "Unknown",
                    artists=artists or ["Unknown"],
                    year=album_year,
                    genre=album_genre,
                    added_at=added_date,
                )
                compact.append(track_model)

            except Exception as exc:
                raise HTTPException(
                    status_code=500, detail=f"Ошибка нормализации трека: {exc}"
                ) from exc

        if any(t.added_at for t in compact):
            compact.sort(key=lambda x: x.added_at or "9999-99-99")

        return compact
