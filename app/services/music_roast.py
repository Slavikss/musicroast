import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from app.config import GEMINI_TEXT_MODEL
from app.models import PlaylistInfoRequest, PlaylistRequest, RoastRequest, Track
from app.prompts import PromptManager, RoastLevel
from app.services.battle import BattleSide
from app.services.diagnosis import (
    LYRICS_COVERAGE_GATE,
    AxisScore,
    TasteSnapshot,
    build_snapshot,
    compute_axes,
    evidence_lines,
    lyrics_coverage,
    pick_evidence,
)
from app.services.gemini import GeminiRoaster, RoastOutcome, parse_winner
from app.services.library_stats import (
    LibraryStats,
    compute_library_stats,
    format_stats_block,
    select_tracks_for_prompt,
)
from app.services.lyrics_layer import (
    collect_lyrics,
    extract_lyric_theme,
    pick_lyric_candidates,
)
from app.services.medkarta import build_medkarta, medkarta_coverage
from app.services.track_normalizer import TrackNormalizer
from app.streaming import StreamingProvider, create_streaming_service

logger = logging.getLogger(__name__)

_SAMPLE_LINES_FOR_BATTLE = 15

_LIKED_KINDS = {"liked", "likes", "favorite"}


def _top_artist_name(tracks: List[Track]) -> Optional[str]:
    from collections import Counter

    counts: Counter = Counter()
    for track in tracks:
        for artist in track.artists:
            if artist and artist != "Unknown":
                counts[artist] += 1
    return counts.most_common(1)[0][0] if counts else None


@dataclass
class MedkartaBundle:
    """Результат работы лаборатории: медкарта + улики + метаданные."""

    medkarta: str
    snapshot_hash: str
    evidence: List[Track]
    coverage: Dict[str, Any]
    lyric_theme: Optional[str] = None


@dataclass
class PreparedLibrary:
    """Загруженная и посчитанная библиотека — вход для осмотра."""

    provider: StreamingProvider
    tracks: List[Track]
    stats: LibraryStats
    stats_block: str
    metadata: Dict[str, Any]
    bundle: Optional[MedkartaBundle] = None  # только для «Мне нравится»

    @property
    def top_artist(self) -> Optional[str]:
        return self.stats.top_artists[0][0] if self.stats.top_artists else None

    def sample_lines(self, limit: int = _SAMPLE_LINES_FOR_BATTLE) -> str:
        lines = []
        for track in select_tracks_for_prompt(self.tracks, limit=limit):
            lines.append(f"- {track.title} — {', '.join(track.artists)}")
        return "\n".join(lines[:limit])


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
        # Кэш лаборатории по snapshot_hash: тот же снапшот → медкарта бесплатно
        self._medkarta_cache: Dict[str, MedkartaBundle] = {}

    def _create_streaming_service(self, provider: StreamingProvider, token: str):
        return create_streaming_service(provider, token)

    # ------------------------------------------------------------------ auth

    def validate_token(
        self, provider: StreamingProvider, access_token: str
    ) -> Dict[str, Any]:
        """Проверяет токен и возвращает {uid, display_name, liked_count}."""
        service = self._create_streaming_service(provider, access_token)
        return service.get_account_summary()

    # ------------------------------------------------------------- playlists

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

    # ----------------------------------------------------------------- roast

    def prepare_library(
        self,
        provider: StreamingProvider,
        access_token: str,
        playlist_kind: Any = "liked",
        owner_id: Any = "me",
    ) -> PreparedLibrary:
        """Этап 1: загрузка треков + статистика + диагноз-ядро (для лайков)."""
        service = self._create_streaming_service(provider, access_token)

        raw_snapshot = None
        is_liked = str(playlist_kind).lower() in _LIKED_KINDS
        if is_liked:
            # Для лайков снапшот — единственный фетч: лайки в нём уже есть
            try:
                raw_snapshot = service.get_taste_snapshot()
            except HTTPException:
                raw_snapshot = None

        if raw_snapshot is not None:
            tracks = raw_snapshot.liked_tracks
            added_dates = raw_snapshot.liked_added
            metadata: Dict[str, Any] = {
                "kind": "liked",
                "title": "Мне нравится",
                "is_liked": True,
            }
        else:
            tracks, added_dates, metadata = service.get_playlist_tracks(
                playlist_kind=playlist_kind, owner_id=owner_id or "me"
            )

        normalized = self.normalizer.normalize_tracks(tracks, added_dates)
        if not normalized:
            raise HTTPException(
                status_code=404, detail="В плейлисте не найдено ни одного трека"
            )

        stats = compute_library_stats(normalized)

        bundle = None
        if raw_snapshot is not None:
            bundle = self._build_medkarta_bundle(service, normalized, raw_snapshot, stats)

        return PreparedLibrary(
            provider=provider,
            tracks=normalized,
            stats=stats,
            stats_block=format_stats_block(stats),
            metadata={**metadata, "track_count": len(normalized)},
            bundle=bundle,
        )

    def _build_medkarta_bundle(
        self, service, normalized: List[Track], raw_snapshot, stats: LibraryStats
    ) -> MedkartaBundle:
        """Лаборатория: оси + лирика + медкарта, с кэшем по snapshot_hash."""
        snapshot = build_snapshot(normalized, raw_snapshot)

        cached = self._medkarta_cache.get(snapshot.snapshot_hash)
        if cached is not None:
            logger.info("medkarta cache hit: %s", snapshot.snapshot_hash)
            return cached

        axes = compute_axes(
            snapshot, popularity_provider=service.get_artist_popularity
        )
        evidence = pick_evidence(axes, snapshot.tracks)

        # Лирик-слой: строго за гейтом покрытия, провал = молча без темы
        lyric_theme = None
        lyric_example = None
        if lyrics_coverage(snapshot.tracks) >= LYRICS_COVERAGE_GATE:
            top_artist = _top_artist_name(snapshot.tracks)
            candidates = pick_lyric_candidates(evidence, snapshot.tracks, top_artist)
            lyrics = collect_lyrics(service, candidates)
            theme = extract_lyric_theme(self.roaster.client, GEMINI_TEXT_MODEL, lyrics)
            if theme:
                lyric_theme = theme.theme
                lyric_example = theme.example_line

        medkarta = build_medkarta(
            snapshot, axes, stats, lyric_theme=lyric_theme, lyric_example=lyric_example
        )
        bundle = MedkartaBundle(
            medkarta=medkarta,
            snapshot_hash=snapshot.snapshot_hash,
            evidence=evidence,
            coverage=medkarta_coverage(snapshot),
            lyric_theme=lyric_theme,
        )
        logger.info(
            "medkarta built for %s: coverage=%s",
            snapshot.snapshot_hash,
            bundle.coverage,
        )
        self._medkarta_cache[snapshot.snapshot_hash] = bundle
        return bundle

    def roast_library(
        self,
        prepared: PreparedLibrary,
        level: RoastLevel = RoastLevel.MEDIUM,
        prompt_version: Optional[str] = None,
    ) -> RoastOutcome:
        """Этап 2: генерация прожарки по подготовленной библиотеке."""
        sampled = select_tracks_for_prompt(prepared.tracks)
        return self.roaster.generate_roast(
            sampled,
            stats_block=prepared.stats_block,
            level=level,
            prompt_version=prompt_version,
            medkarta=prepared.bundle.medkarta if prepared.bundle else None,
        )

    def generate_roast(self, request: RoastRequest) -> Dict[str, Any]:
        """Полный цикл для REST API."""
        prepared = self.prepare_library(
            request.provider,
            request.access_token,
            playlist_kind=request.playlist_kind,
            owner_id=request.owner_id,
        )
        outcome = self.roast_library(
            prepared, level=request.level, prompt_version=request.prompt_version
        )

        result: Dict[str, Any] = {
            "playlist": prepared.metadata,
            "roast": outcome.text,
            "score": outcome.score,
            "diagnosis": outcome.diagnosis,
            "shame_facts": outcome.shame_facts,
            "level": request.level.value,
            "prompt_version": request.prompt_version or request.level.value,
            "stats": prepared.stats.model_dump(),
            "medkarta_coverage": prepared.bundle.coverage if prepared.bundle else None,
            "taste_diagnosis": self._diagnosis_payload(prepared, outcome),
        }

        if request.generate_image:
            image_data = self.roaster.generate_image(outcome.text)
            result["image_url"] = image_data["image_url"]

        return result

    @staticmethod
    def _diagnosis_payload(
        prepared: PreparedLibrary, outcome: RoastOutcome
    ) -> Optional[Dict[str, Any]]:
        """Публичная часть диагноза доктора + улики лаборатории."""
        bundle = prepared.bundle
        if bundle is None:
            return None
        return {
            "snapshot_hash": bundle.snapshot_hash,
            "diagnosis_name": outcome.diagnosis_name,
            "severity": outcome.severity,
            "symptoms": outcome.symptoms,
            "prescription": outcome.prescription,
            "verdict_phrase": outcome.diagnosis,
            "evidence": evidence_lines(bundle.evidence),
            "lyric_theme": bundle.lyric_theme,
        }

    # ---------------------------------------------------------------- battle

    def make_battle_side(
        self,
        display_name: str,
        provider: StreamingProvider,
        access_token: str,
        playlist_kind: Any = "liked",
        level: RoastLevel = RoastLevel.MEDIUM,
        chat_id: Optional[int] = None,
        prepared: Optional[PreparedLibrary] = None,
        outcome: Optional[RoastOutcome] = None,
    ) -> BattleSide:
        """Собирает досье участника. Токен наружу не выходит."""
        prepared = prepared or self.prepare_library(
            provider, access_token, playlist_kind=playlist_kind
        )
        outcome = outcome or self.roast_library(prepared, level=level)
        return BattleSide(
            display_name=display_name,
            score=outcome.score,
            diagnosis=outcome.diagnosis,
            stats_block=prepared.stats_block,
            sample_lines=prepared.sample_lines(),
            chat_id=chat_id,
            diagnosis_name=outcome.diagnosis_name,
        )

    def generate_battle(
        self, side_a: BattleSide, side_b: BattleSide
    ) -> Dict[str, Any]:
        """Судейский вердикт. При отказе LLM — резолв по очкам."""
        header = (
            f"⚔️ {side_a.display_name} {side_a.score} — "
            f"{side_b.score} {side_b.display_name}"
        )
        try:
            raw = self.roaster.generate_battle_verdict(
                side_a.dossier(), side_b.dossier()
            )
            text, winner = parse_winner(raw)
        except HTTPException:
            logger.warning("battle verdict LLM failed, falling back to score compare")
            text, winner = "", None

        if not winner:
            winner = (
                side_a.display_name
                if side_a.score >= side_b.score
                else side_b.display_name
            )
        if not text:
            loser = (
                side_b.display_name
                if winner == side_a.display_name
                else side_a.display_name
            )
            text = (
                f"Судья Гемини воздержался от комментариев — счёт говорит сам "
                f"за себя.\n\n🏆 ПОБЕДИТЕЛЬ: {winner} — у {loser} вкус оказался "
                f"позорнее по очкам."
            )

        return {"header": header, "text": text, "winner": winner}
