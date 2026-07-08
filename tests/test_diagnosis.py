from datetime import date

from app.models import Track
from app.services.diagnosis import (
    AXIS_CONCENTRATION,
    AXIS_GAP,
    AXIS_HYPOCRISY,
    AXIS_TREND,
    AXIS_WAREHOUSE,
    TasteSnapshot,
    axis_concentration,
    axis_gap,
    axis_hypocrisy,
    axis_trend,
    build_snapshot,
    compute_snapshot_hash,
    diagnose,
    format_diagnosis_block,
)
from app.streaming.snapshot import DeclaredItem, RawTasteSnapshot

TODAY = date(2024, 1, 1)


def _track(i, artist="a", genre="rusrap", added="2023-01-01", lyrics=True):
    return Track(
        title=f"t{i}",
        artists=[artist],
        genre=genre,
        year=2020,
        added_at=added,
        track_id=str(i),
        artist_ids=[hash(artist) % 100000],
        lyrics_available=lyrics,
    )


def _snapshot(tracks, **kwargs) -> TasteSnapshot:
    defaults = dict(
        chart_positions=None,
        pins=None,
        playlist_titles=None,
        disliked_artist_names=None,
        disliked_track_artists=None,
        disliked_genres=None,
    )
    defaults.update(kwargs)
    return TasteSnapshot(
        tracks=tracks, snapshot_hash=compute_snapshot_hash(tracks), **defaults
    )


def _diverse(n, added="2023-01-01"):
    return [_track(i, artist=f"a{i}", added=added) for i in range(n)]


# ------------------------------------------------------------------- hash


def test_hash_deterministic_and_order_invariant():
    tracks = [_track(i) for i in range(10)]
    h1 = compute_snapshot_hash(tracks)
    h2 = compute_snapshot_hash(list(reversed(tracks)))
    assert h1 == h2
    h3 = compute_snapshot_hash(tracks + [_track(99)])
    assert h1 != h3


# ------------------------------------------------------------------- axes


def test_concentration_hhi():
    tracks = [_track(i, artist="Кино") for i in range(100)] + [
        _track(100 + i, artist=f"a{i}") for i in range(110)
    ]
    score = axis_concentration(_snapshot(tracks))
    assert score.z > 1.0
    assert score.facts["top_artist"] == "Кино"
    assert len(score.evidence) == 3
    diverse = axis_concentration(_snapshot(_diverse(210)))
    assert diverse.z == 0.0  # разнообразие — не патология


def test_gap_mismatches_and_warehouse():
    tracks = [_track(i, genre="rusrap") for i in range(50)]
    declared = _snapshot(
        tracks,
        pins=[DeclaredItem(kind="artist", name="Radiohead")],
        playlist_titles=["Джаз для чтения"],
    )
    score = axis_gap(declared)
    assert score.z > 1.0
    assert len(score.facts["mismatches"]) == 2
    assert len(score.evidence) == 3

    warehouse = axis_gap(_snapshot(tracks, pins=[], playlist_titles=["Новый плейлист"]))
    assert warehouse.z == 0.0
    assert warehouse.facts["warehouse"] is True


def test_trend_decline_and_short_span():
    # Линейное затухание 24→1 лайков в месяц за 2022-2023
    tracks = []
    i = 0
    for offset in range(24):
        year = 2022 + offset // 12
        month = offset % 12 + 1
        for _ in range(24 - offset):
            tracks.append(_track(i, artist=f"a{i}", added=f"{year}-{month:02d}-15"))
            i += 1
    score = axis_trend(_snapshot(tracks), today=TODAY)
    assert score.z > 0.5
    assert score.facts["span_months"] >= 12
    assert len(score.evidence) == 3

    short = axis_trend(
        _snapshot([_track(i, added="2023-10-01") for i in range(20)]), today=TODAY
    )
    assert short.facts["span_months"] < 12


def test_hypocrisy_artist_and_genre():
    tracks = [_track(i, artist="Егор Крид", genre="ruspop") for i in range(5)] + [
        _track(10 + i, artist=f"a{i}", genre="rusrap") for i in range(15)
    ]
    score = axis_hypocrisy(
        _snapshot(
            tracks,
            disliked_artist_names=["Егор Крид"],
            disliked_genres=["ruspop"],
        )
    )
    assert score.z > 1.0
    assert len(score.facts["hits"]) == 2
    assert len(score.evidence) == 3


# -------------------------------------------------------------- aggregator


def test_gate_min_likes():
    diagnosis, gates = diagnose(_snapshot(_diverse(50)), today=TODAY)
    assert diagnosis is None
    assert gates.likes_gate is False
    assert gates.likes_count == 50


def test_argmax_single_diagnosis_concentration():
    tracks = [_track(i, artist="Кино", added=f"20{16 + i % 8}-03-01") for i in range(120)] + [
        _track(200 + i, artist=f"a{i}", added=f"20{16 + i % 8}-06-01") for i in range(100)
    ]
    snapshot = _snapshot(
        tracks, pins=[DeclaredItem(kind="artist", name="Кино")]
    )
    diagnosis, gates = diagnose(snapshot, today=TODAY)
    assert diagnosis is not None
    assert diagnosis.axis == AXIS_CONCENTRATION
    assert diagnosis.archetype == "Крепостной одного артиста"
    assert len(diagnosis.evidence) == 3
    assert diagnosis.snapshot_hash == snapshot.snapshot_hash


def test_trend_dropped_when_span_short():
    tracks = [_track(i, artist=f"a{i}", added="2023-10-01") for i in range(210)]
    diagnosis, gates = diagnose(
        _snapshot(tracks, pins=[DeclaredItem(kind="artist", name="a0")]),
        today=TODAY,
    )
    assert gates.trend_gate is False
    assert AXIS_TREND in gates.dropped_axes
    if diagnosis:
        assert diagnosis.axis != AXIS_TREND


def test_warehouse_fallback_when_nothing_fires():
    # Разнообразная библиотека без деклараций: все оси ~0 → «склад»
    tracks = _diverse(210, added="2023-01-01")
    for idx, track in enumerate(tracks):
        track.added_at = f"20{16 + idx % 8}-0{idx % 9 + 1}-01"
    diagnosis, gates = diagnose(_snapshot(tracks), today=TODAY)
    assert gates.warehouse is True
    assert diagnosis is not None
    assert diagnosis.axis == AXIS_WAREHOUSE
    assert diagnosis.archetype == "Склад без вывески"


def test_evidence_gate_drops_axis():
    # Лицемерие с уликами из 2 треков → ось выброшена, уходим в склад
    tracks = _diverse(208)
    for idx, track in enumerate(tracks):
        track.added_at = f"20{16 + idx % 8}-0{idx % 9 + 1}-01"
    tracks += [_track(900, artist="Егор Крид"), _track(901, artist="Егор Крид")]
    diagnosis, gates = diagnose(
        _snapshot(tracks, disliked_artist_names=["Егор Крид"]), today=TODAY
    )
    assert AXIS_HYPOCRISY in gates.dropped_axes
    assert diagnosis is not None
    assert diagnosis.axis != AXIS_HYPOCRISY


def test_lyrics_gate_coverage():
    covered = _diverse(210)
    _, gates = diagnose(_snapshot(covered), today=TODAY)
    assert gates.lyrics_gate is True

    uncovered = [
        _track(i, artist=f"a{i}", lyrics=False) for i in range(210)
    ]
    _, gates = diagnose(_snapshot(uncovered), today=TODAY)
    assert gates.lyrics_gate is False


def test_build_snapshot_from_raw():
    raw = RawTasteSnapshot(
        liked_tracks=[],
        liked_added={},
        chart_positions={"1": 1},
        pins=[DeclaredItem(kind="artist", name="X")],
        playlist_titles=["Рок навсегда"],
        disliked_artist_names=["Y"],
    )
    tracks = _diverse(5)
    snapshot = build_snapshot(tracks, raw)
    assert snapshot.chart_positions == {"1": 1}
    assert snapshot.pins[0].name == "X"
    assert snapshot.snapshot_hash == compute_snapshot_hash(tracks)


def test_format_diagnosis_block_mentions_archetype():
    tracks = [_track(i, artist="Кино", added=f"20{16 + i % 8}-03-01") for i in range(120)] + [
        _track(200 + i, artist=f"a{i}", added=f"20{16 + i % 8}-06-01") for i in range(100)
    ]
    diagnosis, _ = diagnose(
        _snapshot(tracks, pins=[DeclaredItem(kind="artist", name="Кино")]),
        today=TODAY,
    )
    block = format_diagnosis_block(diagnosis)
    assert "Крепостной одного артиста" in block
    assert "Улики" in block
    assert "DIAGNOSIS" in block
