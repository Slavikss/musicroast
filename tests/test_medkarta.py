from datetime import date

from app.models import Track
from app.services.diagnosis import build_snapshot, compute_axes
from app.services.library_stats import compute_library_stats
from app.services.medkarta import build_medkarta, medkarta_coverage
from app.streaming.mock import MockStreamingService
from app.services.track_normalizer import TrackNormalizer
from app.streaming.snapshot import RawTasteSnapshot

TODAY = date(2025, 7, 1)


def _mock_snapshot():
    svc = MockStreamingService("x")
    raw = svc.get_taste_snapshot()
    tracks = TrackNormalizer.normalize_tracks(raw.liked_tracks, raw.liked_added)
    snapshot = build_snapshot(tracks, raw)
    axes = compute_axes(snapshot, popularity_provider=svc.get_artist_popularity, today=TODAY)
    stats = compute_library_stats(tracks, today=TODAY)
    return snapshot, axes, stats


def test_medkarta_full_sections():
    snapshot, axes, stats = _mock_snapshot()
    medkarta = build_medkarta(
        snapshot, axes, stats, lyric_theme="перемены и тоска",
        lyric_example="Перемен требуют наши сердца", today=TODAY,
    )
    assert "МЕДКАРТА ПАЦИЕНТА" in medkarta
    assert "[Анкета]" in medkarta
    assert "[Анамнез" in medkarta
    assert "Моя волна" in medkarta
    assert "[Анализы" in medkarta
    assert "[Лирика]" in medkarta
    assert "[Библиотека лайков]" in medkarta
    # Ключевые сигналы попали в документ
    assert "скробблинг выключен" in medkarta
    assert "грустное" in medkarta  # mood_energy=sad
    assert "сидит в комфорте" in medkarta  # diversity=favorite
    assert "foreignAgent" in medkarta
    assert "Моя волна" in medkarta


def test_medkarta_sections_omitted_when_empty():
    tracks = [
        Track(title=f"t{i}", artists=["a"], track_id=str(i), added_at="2023-01-01")
        for i in range(30)
    ]
    raw = RawTasteSnapshot(liked_tracks=[], liked_added={})
    snapshot = build_snapshot(tracks, raw)
    axes = compute_axes(snapshot, today=TODAY)
    stats = compute_library_stats(tracks, today=TODAY)
    medkarta = build_medkarta(snapshot, axes, stats, today=TODAY)
    assert "[Анкета]" not in medkarta
    assert "[Анамнез" not in medkarta
    assert "[Лирика]" not in medkarta
    # маленькая выборка — предупреждение в красных флагах
    assert "выборка маленькая" in medkarta
    assert "[Анализы" in medkarta


def test_medkarta_coverage():
    snapshot, _, _ = _mock_snapshot()
    coverage = medkarta_coverage(snapshot)
    assert coverage["likes"] >= 200
    assert coverage["history"] is True
    assert coverage["wave"] is True
    assert coverage["account"] is True
    assert coverage["lyrics_coverage"] >= 0.6
