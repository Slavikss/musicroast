from datetime import date

from app.models import Track
from app.services.diagnosis import (
    AXIS_TREND,
    TasteSnapshot,
    axis_concentration,
    axis_gap,
    axis_hypocrisy,
    axis_trend,
    build_snapshot,
    compute_axes,
    compute_snapshot_hash,
    evidence_lines,
    lyrics_coverage,
    pick_evidence,
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
    assert snapshot.raw is raw
    assert snapshot.snapshot_hash == compute_snapshot_hash(tracks)


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


def test_gap_mismatches_and_no_declaration():
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

    silent = axis_gap(_snapshot(tracks, pins=[], playlist_titles=["Новый плейлист"]))
    assert silent.z == 0.0
    assert silent.facts["no_declaration"] is True


def test_trend_decline_and_short_span():
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
    assert short.z == 0.0


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


# ------------------------------------------------------------- interface


def test_compute_axes_returns_all_five():
    axes = compute_axes(_snapshot(_diverse(50)), today=TODAY)
    assert len(axes) == 5
    assert {a.axis for a in axes} == {
        "concentration", "obscurity", "gap", "trend", "hypocrisy",
    }


def test_pick_evidence_prefers_firing_axis():
    tracks = [_track(i, artist="Кино", added=f"20{16 + i % 8}-03-01") for i in range(120)] + [
        _track(200 + i, artist=f"a{i}", added=f"20{16 + i % 8}-06-01") for i in range(100)
    ]
    snapshot = _snapshot(tracks)
    axes = compute_axes(snapshot, today=TODAY)
    evidence = pick_evidence(axes, snapshot.tracks)
    assert len(evidence) == 3
    assert all("Кино" in t.artists for t in evidence)  # концентрация выстрелила


def test_pick_evidence_fallback_to_recent():
    tracks = _diverse(10)
    axes = compute_axes(_snapshot(tracks), today=TODAY)
    evidence = pick_evidence(axes, tracks)
    assert len(evidence) == 3  # свежие лайки как фоллбек


def test_lyrics_coverage_and_lines():
    covered = _diverse(10)
    assert lyrics_coverage(covered) == 1.0
    uncovered = [_track(i, lyrics=False) for i in range(10)]
    assert lyrics_coverage(uncovered) == 0.0
    assert evidence_lines(covered[:1]) == ["t0 — a0"]
