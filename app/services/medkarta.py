"""Медкарта пациента: детерминированная агрегация всех сигналов для LLM-доктора.

Код здесь — лаборатория и регистратура: собирает анкету, анамнез, привязанности,
настройки «Моей волны», анализы (оси) и красные флаги в один русский документ.
Диагноз по этой медкарте ставит LLM. Пустые секции опускаются.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from app.services.diagnosis import (
    AXIS_TITLES,
    SMALL_LIBRARY,
    AxisScore,
    TasteSnapshot,
    lyrics_coverage,
)
from app.services.library_stats import LibraryStats, format_stats_block

_MOOD_RU = {
    "fun": "весёлое", "active": "бодрое", "calm": "спокойное",
    "sad": "грустное", "all": "любое",
}
_DIVERSITY_RU = {
    "favorite": "любимое (сидит в комфорте, нового не ищет)",
    "popular": "популярное",
    "discover": "незнакомое (ищет новое)",
    "default": "по умолчанию",
}


def _years_since(iso_value: Optional[str], today: date) -> Optional[int]:
    if not iso_value:
        return None
    try:
        parsed = datetime.fromisoformat(iso_value.replace("Z", "+00:00")).date()
        return max((today - parsed).days // 365, 0)
    except ValueError:
        return None


def _profile_section(snapshot: TasteSnapshot, today: date) -> List[str]:
    raw = snapshot.raw
    if raw is None:
        return []
    lines: List[str] = []
    account = raw.account
    if account:
        parts = []
        age = _years_since(account.birthday, today)
        if age:
            parts.append(f"возраст ~{age} лет")
        seniority = _years_since(account.registered_at, today)
        if seniority is not None:
            parts.append(f"на Яндекс.Музыке ~{seniority} лет")
        if account.family_subscription:
            parts.append("подписка семейная")
        elif account.has_plus:
            parts.append("есть Плюс")
        if account.subeditor:
            parts.append("модератор метаданных (задрот-сигнал)")
        if account.child:
            parts.append("детский аккаунт")
        if parts:
            lines.append("; ".join(parts))
    settings = raw.settings
    if settings:
        parts = []
        if settings.music_visibility:
            visibility = (
                "библиотека публичная (показывает вкус миру)"
                if settings.music_visibility == "public"
                else "библиотека приватная (стесняется)"
            )
            parts.append(visibility)
        if settings.scrobbling is not None:
            parts.append(
                "скробблинг включён (энтузиаст со стажем)"
                if settings.scrobbling
                else "скробблинг выключен"
            )
        if settings.auto_play_radio is not None:
            parts.append(
                "авто-радио вкл (пассивно кормится потоком)"
                if settings.auto_play_radio
                else "авто-радио выкл (курирует сам)"
            )
        if settings.theme:
            parts.append(f"тема {settings.theme}")
        if parts:
            lines.append("; ".join(parts))
    return lines


def _history_section(snapshot: TasteSnapshot) -> List[str]:
    raw = snapshot.raw
    if raw is None or not raw.history_days:
        return []
    days = raw.history_days
    context_counts = Counter(d.context_type for d in days)
    total = sum(context_counts.values()) or 1
    lines: List[str] = [f"зафиксировано сессий: {total} за {len({d.date for d in days})} дн."]

    wave_share = context_counts.get("wave", 0) / total
    if wave_share >= 0.5:
        lines.append(
            f"{round(wave_share * 100)}% сессий — «Моя волна»: "
            "пассивное кормление алгоритмом, сам ничего не выбирает"
        )
    contexts = ", ".join(
        f"{ctx}: {count}" for ctx, count in context_counts.most_common(4)
    )
    lines.append(f"контексты прослушиваний — {contexts}")

    replay_counts: Counter = Counter()
    for day in days:
        for title in day.track_titles:
            replay_counts[title] += 1
    repeats = [(t, c) for t, c in replay_counts.most_common(3) if c >= 2]
    if repeats:
        lines.append(
            "переслушивает по кругу: "
            + ", ".join(f"«{t}» ×{c}" for t, c in repeats)
        )
    last_date = max((d.date for d in days if d.date), default=None)
    if last_date:
        lines.append(f"последняя сессия: {last_date}")
    return lines


def _attachments_section(snapshot: TasteSnapshot) -> List[str]:
    raw = snapshot.raw
    if raw is None:
        return []
    lines: List[str] = []
    if raw.liked_artists:
        genre_counts: Counter = Counter()
        for artist in raw.liked_artists:
            for genre in artist.genres:
                genre_counts[genre] += 1
        top_genres = ", ".join(g for g, _ in genre_counts.most_common(4))
        line = f"осознанно лайкнутых артистов: {len(raw.liked_artists)}"
        if top_genres:
            line += f" (жанры: {top_genres})"
        lines.append(line)
    if raw.liked_albums_count is not None:
        if raw.liked_albums_count == 0:
            lines.append("лайкнутых альбомов: 0 — слушает синглами, альбом как формат умер")
        else:
            titles = ", ".join(f"«{t}»" for t in (raw.liked_album_titles or [])[:3])
            lines.append(
                f"лайкнутых альбомов: {raw.liked_albums_count}"
                + (f" ({titles})" if titles else "")
            )
    if raw.presaves_count:
        lines.append(
            f"пресейвов будущих релизов: {raw.presaves_count} — следит за календарём как за зарплатой"
        )
    if raw.disliked_artist_names:
        lines.append("дизлайкнутые артисты: " + ", ".join(raw.disliked_artist_names[:5]))
    if raw.own_playlists:
        public = [p for p in raw.own_playlists if p.visibility == "public"]
        collective = [p for p in raw.own_playlists if p.collective]
        parts = [f"своих плейлистов: {len(raw.own_playlists)}"]
        if public:
            parts.append(f"публичных: {len(public)}")
        if collective:
            parts.append(f"совместных: {len(collective)}")
        lines.append("; ".join(parts))
    return lines


def _wave_section(snapshot: TasteSnapshot) -> List[str]:
    raw = snapshot.raw
    if raw is None:
        return []
    lines: List[str] = []
    for station in raw.wave_settings or []:
        parts = [f"станция «{station.name}»"]
        if station.mood_energy:
            parts.append(f"настроение: {_MOOD_RU.get(station.mood_energy, station.mood_energy)}")
        if station.diversity:
            parts.append(
                f"разнообразие: {_DIVERSITY_RU.get(station.diversity, station.diversity)}"
            )
        if station.language:
            parts.append(f"язык: {station.language}")
        if len(parts) > 1:
            lines.append("; ".join(parts))
    if raw.skips_per_hour is not None:
        lines.append(f"лимит скипов в час: {raw.skips_per_hour}")
    return lines


def _analyses_section(axes: List[AxisScore]) -> List[str]:
    lines: List[str] = []
    for axis in axes:
        title = AXIS_TITLES.get(axis.axis, axis.axis)
        detail_parts: List[str] = []
        for key, value in axis.facts.items():
            if value in (None, [], "", False):
                continue
            if isinstance(value, list):
                detail_parts.append(f"{key}: " + "; ".join(str(v) for v in value))
            else:
                detail_parts.append(f"{key}={value}")
        details = ("; " + "; ".join(detail_parts)) if detail_parts else ""
        marker = "⚠ " if axis.z >= 1.0 else ""
        lines.append(f"{marker}{title}: z={round(axis.z, 2)}{details}")
    return lines


def _red_flags_section(snapshot: TasteSnapshot) -> List[str]:
    lines: List[str] = []
    tracks = snapshot.tracks
    if tracks:
        explicit_share = sum(1 for t in tracks if t.explicit) / len(tracks)
        if explicit_share >= 0.15:
            lines.append(f"доля explicit-треков: {round(explicit_share * 100)}%")
    raw = snapshot.raw
    if raw and raw.artist_disclaimers:
        for name, marks in list(raw.artist_disclaimers.items())[:3]:
            lines.append(f"артист {name} с пометкой: {', '.join(marks)}")
    if len(tracks) < SMALL_LIBRARY:
        lines.append(
            f"выборка маленькая ({len(tracks)} лайков) — диагноз предварительный, "
            "но доктору всё видно"
        )
    coverage = lyrics_coverage(tracks)
    if coverage and coverage < 0.6:
        lines.append(f"тексты доступны только у {round(coverage * 100)}% треков")
    return lines


def build_medkarta(
    snapshot: TasteSnapshot,
    axes: List[AxisScore],
    stats: LibraryStats,
    lyric_theme: Optional[str] = None,
    lyric_example: Optional[str] = None,
    today: Optional[date] = None,
) -> str:
    """Полная медкарта пациента — единственный вход LLM-доктора."""
    today = today or date.today()
    sections: List[tuple[str, List[str]]] = [
        ("Анкета", _profile_section(snapshot, today)),
        ("Анамнез (реальные прослушивания, не лайки)", _history_section(snapshot)),
        ("Привязанности", _attachments_section(snapshot)),
        ("Моя волна (сохранённые настройки пациента)", _wave_section(snapshot)),
        ("Анализы (вычислено кодом, цифры точные)", _analyses_section(axes)),
        ("Красные флаги", _red_flags_section(snapshot)),
    ]
    if lyric_theme:
        lyric_lines = [f"доминирующая тема текстов: {lyric_theme}"]
        if lyric_example:
            lyric_lines.append(f"характерная строчка: «{lyric_example}»")
        sections.append(("Лирика", lyric_lines))

    parts: List[str] = ["=== МЕДКАРТА ПАЦИЕНТА ==="]
    for title, lines in sections:
        if not lines:
            continue
        parts.append(f"[{title}]")
        parts.extend(f"- {line}" for line in lines)

    stats_block = format_stats_block(stats)
    if stats_block:
        parts.append("[Библиотека лайков]")
        parts.append(stats_block)
    parts.append("=== КОНЕЦ МЕДКАРТЫ ===")
    return "\n".join(parts)


def medkarta_coverage(snapshot: TasteSnapshot) -> Dict[str, Any]:
    """Что удалось собрать — для логов и API (не для юзера)."""
    raw = snapshot.raw
    return {
        "likes": len(snapshot.tracks),
        "chart": snapshot.chart_positions is not None,
        "pins": bool(snapshot.pins),
        "history": bool(raw and raw.history_days),
        "liked_artists": bool(raw and raw.liked_artists),
        "wave": bool(raw and raw.wave_settings),
        "account": bool(raw and raw.account),
        "settings": bool(raw and raw.settings),
        "dislikes": bool(snapshot.disliked_artist_names or snapshot.disliked_track_artists),
        "lyrics_coverage": round(lyrics_coverage(snapshot.tracks), 2),
    }
