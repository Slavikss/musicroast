"""Лирик-слой: единственное место глубины и единственное, где LLM видит тексты.

Строго за двумя гейтами: покрытие lyrics_available ≥ 60% И диагноз состоялся.
Head-выборка ≤ 10 треков, каждый текст обрезается, <3 текстов — слой молча
выключается. Результат — опциональная тема, она не влияет на argmax.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import List, Optional, Protocol, Sequence

from app.models import Track

logger = logging.getLogger(__name__)

MAX_TRACKS = 10
MAX_CHARS_PER_LYRIC = 1200
MIN_LYRICS = 3


class LyricsFetcher(Protocol):
    def fetch_lyrics(self, track_id: str) -> Optional[str]: ...


@dataclass
class LyricTheme:
    theme: str
    example_line: str


def pick_lyric_candidates(
    evidence: Sequence[Track], all_tracks: Sequence[Track], top_artist: Optional[str]
) -> List[Track]:
    """Head-выборка: улики финальной оси + треки топ-артиста, только с текстами."""
    seen: set[str] = set()
    candidates: List[Track] = []

    def take(tracks: Sequence[Track]) -> None:
        for track in tracks:
            key = track.track_id or track.title
            if (
                track.lyrics_available
                and track.track_id
                and key not in seen
                and len(candidates) < MAX_TRACKS
            ):
                seen.add(key)
                candidates.append(track)

    take(evidence)
    if top_artist:
        take([t for t in all_tracks if top_artist in t.artists])
    take(all_tracks[-20:])  # свежие как добивка
    return candidates


def collect_lyrics(fetcher: LyricsFetcher, candidates: Sequence[Track]) -> List[str]:
    """Скачивает тексты head-выборки; ошибки пропускаются per-track."""
    lyrics: List[str] = []
    for track in candidates:
        if not track.track_id:
            continue
        text = fetcher.fetch_lyrics(track.track_id)
        if text and text.strip():
            snippet = text.strip()[:MAX_CHARS_PER_LYRIC]
            lyrics.append(f"«{track.title}» — {', '.join(track.artists)}:\n{snippet}")
    return lyrics


def extract_lyric_theme(genai_client, model: str, lyrics: List[str]) -> Optional[LyricTheme]:
    """Один структурный вызов: доминирующая тема текстов. Провал = None."""
    if len(lyrics) < MIN_LYRICS:
        return None
    try:
        response = genai_client.models.generate_content(
            model=model,
            contents=(
                "Вот тексты песен из библиотеки одного человека. Определи ОДНУ "
                "доминирующую тему/настроение этих текстов (коротко, до 10 слов, "
                "по-русски, можно едко) и приведи одну характерную строчку.\n\n"
                + "\n\n---\n\n".join(lyrics)
            ),
            config={
                "response_mime_type": "application/json",
                "response_schema": {
                    "type": "object",
                    "properties": {
                        "theme": {"type": "string"},
                        "example_line": {"type": "string"},
                    },
                    "required": ["theme", "example_line"],
                },
            },
        )
        data = json.loads(response.text)
        theme = str(data.get("theme", "")).strip()
        if not theme:
            return None
        return LyricTheme(
            theme=theme, example_line=str(data.get("example_line", "")).strip()
        )
    except Exception:  # noqa: BLE001 — слой опциональный, не роняет прожарку
        logger.warning("lyric theme extraction failed", exc_info=True)
        return None
