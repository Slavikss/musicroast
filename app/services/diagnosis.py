"""Лаборатория: детерминированные «анализы» вкуса.

Пять чистых осевых функций над снапшотом, каждая возвращает z-score и факты.
Диагноз по анализам ставит LLM-доктор (см. medkarta.py + промпты) — здесь
только вычисления. z-скоры считаются от зашитых калибровочных констант
(_CALIBRATION) — ручная калибровка, подстраивается по логам.
"""

from __future__ import annotations

import hashlib
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable, Dict, List, Optional, Sequence

from app.models import Track
from app.streaming.snapshot import DeclaredItem, RawTasteSnapshot

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- constants

SMALL_LIBRARY = 200        # ниже — в медкарте пометка «выборка маленькая»
MIN_EVIDENCE = 3
LYRICS_COVERAGE_GATE = 0.6
HEAD_ARTISTS_K = 5         # сколько артистов головы обогащать популярностью

# Калибровка (mean, sigma) для перевода сырых метрик в псевдо-z.
_CALIBRATION = {
    "hhi": (0.035, 0.04),            # HHI по артистам
    "chart_share": (0.02, 0.03),     # доля лайков, сидящих в чарте
    "gap_mismatches": (0.4, 0.8),    # число расхождений заявленное/фактическое
    "trend_decline": (0.0, 0.12),    # относительный наклон вниз
    "hypocrisy_hits": (0.3, 0.7),    # число пересечений дизлайк/лайк
}

AXIS_CONCENTRATION = "concentration"
AXIS_OBSCURITY = "obscurity"
AXIS_GAP = "gap"
AXIS_TREND = "trend"
AXIS_HYPOCRISY = "hypocrisy"

AXIS_TITLES = {
    AXIS_CONCENTRATION: "Концентрация (зависимость от одного артиста)",
    AXIS_OBSCURITY: "Чартовость/обскурность",
    AXIS_GAP: "Разрыв «заявленное vs фактическое»",
    AXIS_TREND: "Затухание библиотеки",
    AXIS_HYPOCRISY: "Лицемерие (дизлайкнутое ∩ лайкнутое)",
}

# Словарь жанр-слов для оси разрыва: ключевое слово в названии → жанры Яндекса
_GENRE_KEYWORDS = {
    "рок": {"rock", "rusrock", "alternative", "punk", "metal", "indie"},
    "rock": {"rock", "rusrock", "alternative", "punk", "metal", "indie"},
    "метал": {"metal"},
    "metal": {"metal"},
    "джаз": {"jazz"},
    "jazz": {"jazz"},
    "инди": {"indie", "alternative", "indierock"},
    "indie": {"indie", "alternative", "indierock"},
    "техно": {"techno", "electronic", "electronics", "dance", "house"},
    "techno": {"techno", "electronic", "electronics", "dance", "house"},
    "электрон": {"electronic", "electronics", "techno", "house", "dance"},
    "хаус": {"house"},
    "рэп": {"rusrap", "rap", "foreignrap", "hiphop"},
    "rap": {"rusrap", "rap", "foreignrap", "hiphop"},
    "хип-хоп": {"rusrap", "rap", "foreignrap", "hiphop"},
    "hip-hop": {"rusrap", "rap", "foreignrap", "hiphop"},
    "классик": {"classical", "classicalmusic"},
    "classical": {"classical", "classicalmusic"},
    "джангл": {"dnb", "jungle"},
    "lo-fi": {"lofi", "chill"},
    "лофай": {"lofi", "chill"},
    "фонк": {"phonk"},
    "phonk": {"phonk"},
    "шансон": {"shanson"},
    "панк": {"punk"},
    "punk": {"punk"},
}

# Названия плейлистов, не несущие декларации
_MEANINGLESS_TITLES = {"мне нравится", "новый плейлист", "new playlist", "плейлист"}


# ------------------------------------------------------------------- types


@dataclass
class TasteSnapshot:
    """Нормализованный снапшот: единица работы лаборатории, ключ — hash."""

    tracks: List[Track]
    snapshot_hash: str
    chart_positions: Optional[Dict[str, int]] = None
    pins: Optional[List[DeclaredItem]] = None
    playlist_titles: Optional[List[str]] = None
    disliked_artist_names: Optional[List[str]] = None
    disliked_track_artists: Optional[List[str]] = None
    disliked_genres: Optional[List[str]] = None
    raw: Optional[RawTasteSnapshot] = None  # весь сырой снапшот для медкарты


@dataclass
class AxisScore:
    axis: str
    z: float
    evidence: List[Track] = field(default_factory=list)
    facts: Dict[str, Any] = field(default_factory=dict)
    direction: str = ""  # для двунаправленных осей (обскурность)


PopularityProvider = Callable[[List[int]], Dict[int, Optional[int]]]


# ---------------------------------------------------------------- snapshot


def _track_key(track: Track) -> str:
    return track.track_id or f"{track.title}|{','.join(track.artists)}"


def compute_snapshot_hash(tracks: Sequence[Track]) -> str:
    keys = sorted(_track_key(t) for t in tracks)
    return hashlib.sha256("\n".join(keys).encode("utf-8")).hexdigest()[:16]


def build_snapshot(
    normalized_tracks: Sequence[Track], raw: Optional[RawTasteSnapshot]
) -> TasteSnapshot:
    """Собирает единицу работы лаборатории из нормализованных лайков и сырья."""
    return TasteSnapshot(
        tracks=list(normalized_tracks),
        snapshot_hash=compute_snapshot_hash(normalized_tracks),
        chart_positions=raw.chart_positions if raw else None,
        pins=raw.pins if raw else None,
        playlist_titles=raw.playlist_titles if raw else None,
        disliked_artist_names=raw.disliked_artist_names if raw else None,
        disliked_track_artists=raw.disliked_track_artists if raw else None,
        disliked_genres=raw.disliked_genres if raw else None,
        raw=raw,
    )


# ------------------------------------------------------------ axis helpers


def _z(metric: str, value: float) -> float:
    """Псевдо-z с клампом снизу: оси однонаправленные, отрицательное
    отклонение = «патологии нет»."""
    mean, sigma = _CALIBRATION[metric]
    return max(0.0, (value - mean) / sigma) if sigma else 0.0


def _artist_counts(tracks: Sequence[Track]) -> Counter:
    counts: Counter = Counter()
    for track in tracks:
        for artist in track.artists:
            if artist and artist != "Unknown":
                counts[artist] += 1
    return counts


def _parse_month(added_at: Optional[str]) -> Optional[date]:
    if not added_at:
        return None
    try:
        parsed = datetime.fromisoformat(added_at).date()
        return parsed.replace(day=1)
    except ValueError:
        return None


def _months_between(start: date, end: date) -> int:
    return (end.year - start.year) * 12 + (end.month - start.month)


def lyrics_coverage(tracks: Sequence[Track]) -> float:
    if not tracks:
        return 0.0
    return sum(1 for t in tracks if t.lyrics_available) / len(tracks)


# ------------------------------------------------------------------- axes


def axis_concentration(snapshot: TasteSnapshot) -> AxisScore:
    """HHI по артистам: во сколько библиотека — крепость одного артиста."""
    counts = _artist_counts(snapshot.tracks)
    total = sum(counts.values())
    if not total:
        return AxisScore(axis=AXIS_CONCENTRATION, z=0.0)

    hhi = sum((count / total) ** 2 for count in counts.values())
    top_artist, top_count = counts.most_common(1)[0]
    evidence = [t for t in snapshot.tracks if top_artist in t.artists][:MIN_EVIDENCE]
    return AxisScore(
        axis=AXIS_CONCENTRATION,
        z=_z("hhi", hhi),
        evidence=evidence,
        facts={
            "hhi": round(hhi, 4),
            "top_artist": top_artist,
            "top_count": top_count,
            "top_share_pct": round(top_count * 100.0 / total, 1),
        },
    )


def axis_obscurity(
    snapshot: TasteSnapshot,
    popularity_provider: Optional[PopularityProvider] = None,
) -> AxisScore:
    """Двунаправленная ось: чартовый мейнстрим ↔ глухой андеграунд."""
    tracks = snapshot.tracks
    if not tracks:
        return AxisScore(axis=AXIS_OBSCURITY, z=0.0)

    chart_hits: List[Track] = []
    if snapshot.chart_positions:
        chart_ids = set(snapshot.chart_positions)
        chart_hits = [
            t
            for t in tracks
            if t.track_id and t.track_id.split(":", 1)[0] in chart_ids
        ]
    chart_share = len(chart_hits) / len(tracks)
    z_mainstream = _z("chart_share", chart_share)

    z_underground = 0.0
    median_listeners: Optional[int] = None
    head_artist_ids: List[int] = []
    counts = _artist_counts(tracks)
    top_names = {name for name, _ in counts.most_common(HEAD_ARTISTS_K)}
    seen: set[int] = set()
    for track in tracks:
        if top_names.intersection(track.artists):
            for artist_id in track.artist_ids:
                if artist_id not in seen:
                    seen.add(artist_id)
                    head_artist_ids.append(artist_id)
    if popularity_provider and head_artist_ids:
        listeners = [
            v
            for v in popularity_provider(head_artist_ids[:HEAD_ARTISTS_K]).values()
            if isinstance(v, int)
        ]
        if listeners:
            listeners.sort()
            median_listeners = listeners[len(listeners) // 2]
            if median_listeners < 5_000:
                z_underground = 2.0
            elif median_listeners < 30_000:
                z_underground = 1.2
            elif median_listeners > 2_000_000:
                z_mainstream = max(z_mainstream, 1.0 + z_mainstream * 0.5)

    if z_underground > abs(z_mainstream):
        head_tracks = [t for t in tracks if top_names.intersection(t.artists)]
        return AxisScore(
            axis=AXIS_OBSCURITY,
            z=z_underground,
            direction="underground",
            evidence=head_tracks[:MIN_EVIDENCE],
            facts={
                "median_listeners": median_listeners,
                "chart_hits": len(chart_hits),
            },
        )
    return AxisScore(
        axis=AXIS_OBSCURITY,
        z=z_mainstream,
        direction="mainstream",
        evidence=chart_hits[:MIN_EVIDENCE],
        facts={
            "chart_hits": len(chart_hits),
            "chart_share_pct": round(chart_share * 100, 1),
            "median_listeners": median_listeners,
        },
    )


def _meaningful_titles(titles: Optional[List[str]]) -> List[str]:
    result = []
    for title in titles or []:
        lowered = title.strip().lower()
        if lowered and not any(lowered.startswith(m) for m in _MEANINGLESS_TITLES):
            result.append(title.strip())
    return result


def axis_gap(snapshot: TasteSnapshot) -> AxisScore:
    """Разрыв «заявленное vs фактическое». Без декларации разрыв не синтезируем."""
    pins = snapshot.pins or []
    titles = _meaningful_titles(snapshot.playlist_titles)
    if not pins and not titles:
        return AxisScore(axis=AXIS_GAP, z=0.0, facts={"no_declaration": True})

    counts = _artist_counts(snapshot.tracks)
    total = len(snapshot.tracks) or 1
    genre_counts = Counter(t.genre for t in snapshot.tracks if t.genre)
    genre_share = {g: c / total for g, c in genre_counts.items()}
    top_genres = [g for g, _ in genre_counts.most_common(3)]

    mismatches: List[str] = []
    evidence: List[Track] = []

    for pin in pins:
        if pin.kind == "artist" and counts.get(pin.name, 0) <= 1:
            mismatches.append(f"закрепил артиста «{pin.name}», а в лайках его нет")

    for title in titles:
        lowered = title.lower()
        for keyword, genres in _GENRE_KEYWORDS.items():
            if keyword in lowered:
                if all(genre_share.get(g, 0.0) < 0.05 for g in genres):
                    mismatches.append(
                        f"плейлист «{title}» заявляет {keyword}, "
                        f"а в библиотеке этого жанра <5%"
                    )

    if mismatches:
        evidence = [t for t in snapshot.tracks if t.genre in top_genres[:1]][
            :MIN_EVIDENCE
        ]

    return AxisScore(
        axis=AXIS_GAP,
        z=_z("gap_mismatches", float(len(mismatches))),
        evidence=evidence,
        facts={
            "mismatches": mismatches[:4],
            "declared": [p.name for p in pins][:5] + titles[:5],
            "actual_top_genres": top_genres,
        },
    )


def axis_trend(snapshot: TasteSnapshot, today: Optional[date] = None) -> AxisScore:
    """Наклон месячных лайков: резкое затухание = вкус умирает."""
    today = today or date.today()
    months = [m for m in (_parse_month(t.added_at) for t in snapshot.tracks) if m]
    if not months:
        return AxisScore(axis=AXIS_TREND, z=0.0, facts={"span_months": 0})

    first, last = min(months), max(months)
    span = _months_between(first, last)
    if span < 12:
        return AxisScore(axis=AXIS_TREND, z=0.0, facts={"span_months": span})

    window = min(_months_between(first, today.replace(day=1)) + 1, 36)
    counts_by_offset = Counter(_months_between(m, today.replace(day=1)) for m in months)
    series = [counts_by_offset.get(offset, 0) for offset in range(window - 1, -1, -1)]

    n = len(series)
    mean_x = (n - 1) / 2
    mean_y = sum(series) / n
    denom = sum((i - mean_x) ** 2 for i in range(n)) or 1
    slope = sum((i - mean_x) * (y - mean_y) for i, y in enumerate(series)) / denom
    rel_decline = -slope / max(mean_y, 0.5)  # >0 = затухание

    recent = sum(series[-3:])
    peak = max(series) if series else 0
    quiet_months = 0
    for value in reversed(series):
        if value:
            break
        quiet_months += 1

    last_tracks = [t for t in snapshot.tracks if t.added_at]
    last_tracks.sort(key=lambda t: t.added_at or "")
    return AxisScore(
        axis=AXIS_TREND,
        z=_z("trend_decline", rel_decline),
        evidence=last_tracks[-MIN_EVIDENCE:][::-1],
        facts={
            "span_months": span,
            "adds_last_3m": recent,
            "peak_month_adds": peak,
            "quiet_months": quiet_months,
        },
    )


def axis_hypocrisy(snapshot: TasteSnapshot) -> AxisScore:
    """Пересечение отвергнутого с лайкнутым: граница вкуса, нарушенная им самим."""
    counts = _artist_counts(snapshot.tracks)
    genre_counts = Counter(t.genre for t in snapshot.tracks if t.genre)
    total = len(snapshot.tracks) or 1

    hits: List[str] = []
    evidence: List[Track] = []

    disliked_artists = set(snapshot.disliked_artist_names or []) | set(
        snapshot.disliked_track_artists or []
    )
    for artist in disliked_artists:
        liked_count = counts.get(artist, 0)
        if liked_count >= 2:
            hits.append(f"дизлайкнул {artist}, но держит {liked_count} его треков в лайках")
            evidence.extend(t for t in snapshot.tracks if artist in t.artists)

    for genre in set(snapshot.disliked_genres or []):
        share = genre_counts.get(genre, 0) / total
        if share >= 0.1:
            hits.append(
                f"дизлайкает {genre}, при этом {round(share * 100)}% лайков — этот же жанр"
            )
            evidence.extend(t for t in snapshot.tracks if t.genre == genre)

    return AxisScore(
        axis=AXIS_HYPOCRISY,
        z=_z("hypocrisy_hits", float(len(hits))),
        evidence=evidence[:MIN_EVIDENCE],
        facts={"hits": hits[:4]},
    )


# --------------------------------------------------------------- interface


def compute_axes(
    snapshot: TasteSnapshot,
    popularity_provider: Optional[PopularityProvider] = None,
    today: Optional[date] = None,
) -> List[AxisScore]:
    """Все анализы разом. Диагноз по ним ставит LLM-доктор, не код."""
    axes = [
        axis_concentration(snapshot),
        axis_obscurity(snapshot, popularity_provider),
        axis_gap(snapshot),
        axis_trend(snapshot, today=today),
        axis_hypocrisy(snapshot),
    ]
    logger.info(
        "axes for %s: %s",
        snapshot.snapshot_hash,
        {a.axis: round(a.z, 2) for a in axes},
    )
    return axes


def pick_evidence(axes: List[AxisScore], tracks: Sequence[Track]) -> List[Track]:
    """Улики для карточки: треки самой выстрелившей оси, иначе свежие лайки."""
    for axis in sorted(axes, key=lambda a: a.z, reverse=True):
        if axis.z > 0 and len(axis.evidence) >= MIN_EVIDENCE:
            return axis.evidence[:MIN_EVIDENCE]
    return list(tracks[-MIN_EVIDENCE:])[::-1]


def evidence_lines(tracks: Sequence[Track]) -> List[str]:
    return [f"{t.title} — {', '.join(t.artists)}" for t in tracks]
