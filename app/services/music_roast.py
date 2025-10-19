import os
from typing import Any, Dict

from fastapi import HTTPException

from app.models import PlaylistInfoRequest, PlaylistRequest, RoastRequest
from app.prompts import PromptManager
from app.services.gemini import GeminiRoaster
from app.services.track_normalizer import TrackNormalizer
from app.streaming import StreamingProvider, create_streaming_service


class MusicRoastService:
    """Основной сервис приложения."""

    def __init__(
        self,
        prompt_config_path: str | None = None,
        google_api_key: str | None = None,
    ):
        prompt_config = prompt_config_path or os.getenv("PROMPT_CONFIG_PATH")
        self.prompt_manager = PromptManager(config_path=prompt_config)

        api_key = google_api_key or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("Не найден GOOGLE_API_KEY")

        self.normalizer = TrackNormalizer()
        self.roaster = GeminiRoaster(api_key, self.prompt_manager)

    def _create_streaming_service(
        self, provider: StreamingProvider, token: str
    ):
        return create_streaming_service(provider, token)

    def list_playlists(self, request: PlaylistRequest) -> Dict[str, Any]:
        service = self._create_streaming_service(
            request.provider, request.access_token
        )
        owner_id = request.owner_id or "me"
        playlists = service.list_playlists(owner_id=owner_id)
        return {
            "owner_id": owner_id,
            "playlists": playlists,
            "prompt_versions": self.prompt_manager.list_versions(),
            "provider": request.provider,
        }

    def get_playlist_info(self, request: PlaylistInfoRequest) -> Dict[str, Any]:
        service = self._create_streaming_service(
            request.provider, request.access_token
        )
        owner_id = request.owner_id or "me"
        tracks, added_dates, metadata = service.get_playlist_tracks(
            playlist_kind=request.playlist_kind, owner_id=owner_id
        )
        normalized_tracks = self.normalizer.normalize_tracks(tracks, added_dates)
        metadata = {**metadata, "track_count": len(normalized_tracks)}
        return {
            "playlist": metadata,
            "tracks": [track.model_dump() for track in normalized_tracks],
        }

    def generate_roast(self, request: RoastRequest) -> Dict[str, Any]:
        service = self._create_streaming_service(
            request.provider, request.access_token
        )
        owner_id = request.owner_id or "me"
        tracks, added_dates, metadata = service.get_playlist_tracks(
            playlist_kind=request.playlist_kind, owner_id=owner_id
        )
        normalized_tracks = self.normalizer.normalize_tracks(tracks, added_dates)

        if not normalized_tracks:
            raise HTTPException(
                status_code=404, detail="В плейлисте не найдено ни одного трека"
            )

        template = self.prompt_manager.get_template(request.prompt_version)
        roast_text = self.roaster.generate_roast(
            normalized_tracks, prompt_version=template.version
        )

        result: Dict[str, Any] = {
            "playlist": {**metadata, "track_count": len(normalized_tracks)},
            "roast": roast_text,
            "prompt_version": template.version,
        }

        if request.generate_image:
            image_data = self.roaster.generate_image(roast_text)
            result["image_url"] = image_data["image_url"]

        return result
