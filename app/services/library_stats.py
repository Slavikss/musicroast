"""Вычисление честной статистики по библиотеке — топливо для фактовой прожарки.

Все функции чистые и детерминированные (``today`` инжектится для тестов).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime
from typing import Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel

from app.models import Track

# Жанры с долей ниже этого порога и <=2 треками считаем «залётными»
_OUTLIER_MAX_TRACKS = 2
# Запой: столько треков одного артиста за один день
_BINGE_ARTIST_MIN = 5
# Или просто столько треков за один день
_BINGE_DAY_MIN = 15
# Доминирующее окно: минимум столько библиотеки в 3-летнем окне
_DOMINANT_WINDOW_SHARE = 0.4


class BingeDay(BaseModel):
    date: str
    artist: Optional[str] = None
    count: int


class LibraryStats(BaseModel):
    total_tracks: int = 0
    first_added: Optional[str] = None
    last_added: Optional[str] = None
    top_artists: List[Tuple[str, int]] = []
    single_track_artist_count: int = 0
    genre_shares: List[Tuple[str, float]] = []
    outlier_genres: List[Tuple[str, int]] = []
    era_shares: List[Tuple[str, float]] = []
    median_year: Optional[int] = None
    dominant_window: Optional[Tuple[int, int, float]] = None
    binge_days: List[BingeDay] = []
    adds_last_90d: int = 0
    adds_last_365d: int = 0
    dormant_months: Optional[int] = None


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


def compute_library_stats(
    tracks: Sequence[Track], today: Optional[date] = None
) -> LibraryStats:
    stats = LibraryStats(total_tracks=len(tracks))
    if not tracks:
        return stats
    today = today or date.today()

    # --- Артисты ---
    artist_counts: Counter[str] = Counter()
    for track in tracks:
        for artist in track.artists:
            if artist and artist != "Unknown":
                artist_counts[artist] += 1
    stats.top_artists = artist_counts.most_common(10)
    stats.single_track_artist_count = sum(
        1 for count in artist_counts.values() if count == 1
    )

    # --- Жанры ---
    genre_counts: Counter[str] = Counter(
        track.genre for track in tracks if track.genre
    )
    genre_total = sum(genre_counts.values())
    if genre_total:
        stats.genre_shares = [
            (genre, round(count * 100.0 / genre_total, 1))
            for genre, count in genre_counts.most_common(8)
        ]
        main_genres = {genre for genre, _ in genre_counts.most_common(3)}
        stats.outlier_genres = [
            (genre, count)
            for genre, count in genre_counts.items()
            if count <= _OUTLIER_MAX_TRACKS and genre not in main_genres
        ][:5]

    # --- Года релизов ---
    years = sorted(track.year for track in tracks if track.year)
    if years:
        stats.median_year = years[len(years) // 2]
        decade_counts: Counter[str] = Counter(f"{(y // 10) * 10}-е" for y in years)
        stats.era_shares = [
            (decade, round(count * 100.0 / len(years), 1))
            for decade, count in decade_counts.most_common(5)
        ]

    # --- Даты добавления ---
    added_dates = sorted(
        d for d in (_parse_date(track.added_at) for track in tracks) if d
    )
    if added_dates:
        stats.first_added = added_dates[0].isoformat()
        stats.last_added = added_dates[-1].isoformat()
        stats.adds_last_90d = sum(1 for d in added_dates if (today - d).days <= 90)
        stats.adds_last_365d = sum(1 for d in added_dates if (today - d).days <= 365)
        months_quiet = (today - added_dates[-1]).days // 30
        if months_quiet >= 6:
            stats.dormant_months = months_quiet

        # Доминирующее 3-летнее окно по годам добавления
        add_years = Counter(d.year for d in added_dates)
        best: Optional[Tuple[int, int, float]] = None
        for start in range(min(add_years), max(add_years) + 1):
            share = sum(add_years.get(y, 0) for y in range(start, start + 3)) / len(
                added_dates
            )
            if best is None or share > best[2]:
                best = (start, start + 2, share)
        if best and best[2] >= _DOMINANT_WINDOW_SHARE:
            stats.dominant_window = (best[0], best[1], round(best[2] * 100, 1))

        # Запои
        by_day: Dict[str, List[Track]] = defaultdict(list)
        for track in tracks:
            parsed = _parse_date(track.added_at)
            if parsed:
                by_day[parsed.isoformat()].append(track)

        binges: List[BingeDay] = []
        for day, day_tracks in by_day.items():
            day_artists: Counter[str] = Counter()
            for track in day_tracks:
                for artist in track.artists:
                    if artist and artist != "Unknown":
                        day_artists[artist] += 1
            artist_binge = None
            if day_artists:
                top_artist, top_count = day_artists.most_common(1)[0]
                if top_count >= _BINGE_ARTIST_MIN:
                    artist_binge = BingeDay(date=day, artist=top_artist, count=top_count)
            if artist_binge:
                binges.append(artist_binge)
            elif len(day_tracks) >= _BINGE_DAY_MIN:
                binges.append(BingeDay(date=day, artist=None, count=len(day_tracks)))

        binges.sort(key=lambda b: b.count, reverse=True)
        stats.binge_days = binges[:3]

    return stats


def format_stats_block(stats: LibraryStats) -> str:
    """Формирует русский блок фактов для инжекта в промпт."""
    if not stats.total_tracks:
        return ""

    lines: List[str] = [
        "ФАКТЫ О БИБЛИОТЕКЕ (вычислено точно по данным — используй ИМЕННО эти цифры, имена и даты):"
    ]

    total_line = f"- Всего треков: {stats.total_tracks}."
    if stats.first_added and stats.last_added:
        total_line += f" Добавлялись с {stats.first_added} по {stats.last_added}."
    lines.append(total_line)

    if stats.top_artists:
        top = ", ".join(
            f"{name} — {count} тр. ({round(count * 100.0 / stats.total_tracks, 1)}%)"
            for name, count in stats.top_artists[:5]
        )
        lines.append(f"- Топ артистов: {top}.")

    if stats.single_track_artist_count:
        lines.append(
            f"- Артистов с одним-единственным треком: {stats.single_track_artist_count}."
        )

    if stats.genre_shares:
        genres = ", ".join(f"{g} — {p}%" for g, p in stats.genre_shares[:6])
        lines.append(f"- Жанры: {genres}.")

    if stats.outlier_genres:
        outliers = ", ".join(f"{g} ({c} тр.)" for g, c in stats.outlier_genres)
        lines.append(f"- Залётные жанры (внезапные гости): {outliers}.")

    if stats.era_shares:
        eras = ", ".join(f"{era} — {p}%" for era, p in stats.era_shares[:4])
        line = f"- Эры релизов: {eras}."
        if stats.median_year:
            line += f" Медианный год трека: {stats.median_year}."
        lines.append(line)

    if stats.dominant_window:
        start, end, share = stats.dominant_window
        lines.append(
            f"- {share}% библиотеки добавлено в {start}–{end} — человек застрял именно там."
        )

    for binge in stats.binge_days:
        if binge.artist:
            lines.append(
                f"- Запой: {binge.date} добавлено {binge.count} треков {binge.artist} за один день."
            )
        else:
            lines.append(
                f"- Запой: {binge.date} добавлено {binge.count} треков за один день."
            )

    if stats.last_added:
        activity = (
            f"- За последние 90 дней добавлено {stats.adds_last_90d}, "
            f"за год — {stats.adds_last_365d}. Последнее добавление: {stats.last_added}."
        )
        lines.append(activity)
        if stats.dormant_months:
            lines.append(
                f"- Библиотека заброшена уже ~{stats.dormant_months} мес. — вкус законсервирован."
            )

    return "\n".join(lines)


def select_tracks_for_prompt(
    tracks: Sequence[Track], limit: int = 120
) -> List[Track]:
    """Выборка треков для промпта: новые + старые + по чуть-чуть от топ-артистов."""
    if len(tracks) <= limit:
        return list(tracks)

    # tracks приходят отсортированными по added_at по возрастанию
    selected: List[Track] = []
    seen: set[int] = set()

    def take(items: Sequence[Track]) -> None:
        for track in items:
            key = id(track)
            if key not in seen and len(selected) < limit:
                seen.add(key)
                selected.append(track)

    take(tracks[-60:])  # свежие
    take(tracks[:20])  # древние

    artist_counts: Counter[str] = Counter()
    for track in tracks:
        for artist in track.artists:
            artist_counts[artist] += 1
    top_artists = {name for name, _ in artist_counts.most_common(10)}
    per_artist: Counter[str] = Counter()
    for track in tracks:
        for artist in track.artists:
            if artist in top_artists and per_artist[artist] < 3:
                per_artist[artist] += 1
                take([track])
                break

    return selected
