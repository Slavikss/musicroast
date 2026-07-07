from datetime import date

from app.services.library_stats import (
    compute_library_stats,
    format_stats_block,
    select_tracks_for_prompt,
)

TODAY = date(2026, 7, 1)


def test_empty_library():
    stats = compute_library_stats([], today=TODAY)
    assert stats.total_tracks == 0
    assert format_stats_block(stats) == ""


def test_top_artists_and_shares(make_track):
    tracks = [make_track(artists=("Кино",)) for _ in range(6)] + [
        make_track(artists=("Queen",))
    ]
    stats = compute_library_stats(tracks, today=TODAY)
    assert stats.top_artists[0] == ("Кино", 6)
    assert stats.single_track_artist_count == 1


def test_genre_shares_and_outliers(make_track):
    tracks = (
        [make_track(genre="rusrap") for _ in range(10)]
        + [make_track(genre="pop") for _ in range(5)]
        + [make_track(genre="rock") for _ in range(4)]
        + [make_track(genre="shanson"), make_track(genre="shanson")]
    )
    stats = compute_library_stats(tracks, today=TODAY)
    genres = dict(stats.genre_shares)
    assert genres["rusrap"] == 47.6
    assert ("shanson", 2) in stats.outlier_genres


def test_dominant_window(make_track):
    tracks = [
        make_track(added_at=f"2016-0{(i % 9) + 1}-01") for i in range(8)
    ] + [make_track(added_at="2024-01-01"), make_track(added_at="2010-01-01")]
    stats = compute_library_stats(tracks, today=TODAY)
    assert stats.dominant_window is not None
    start, end, share = stats.dominant_window
    assert start <= 2016 <= end
    assert share >= 40


def test_artist_binge_detection(make_track):
    tracks = [
        make_track(title=f"t{i}", artists=("Кино",), added_at="2021-11-03")
        for i in range(6)
    ] + [make_track(added_at="2021-12-01")]
    stats = compute_library_stats(tracks, today=TODAY)
    assert stats.binge_days
    binge = stats.binge_days[0]
    assert binge.artist == "Кино"
    assert binge.count == 6
    assert binge.date == "2021-11-03"


def test_day_binge_without_artist(make_track):
    tracks = [
        make_track(title=f"t{i}", artists=(f"a{i}",), added_at="2020-05-05")
        for i in range(16)
    ]
    stats = compute_library_stats(tracks, today=TODAY)
    assert stats.binge_days
    assert stats.binge_days[0].artist is None
    assert stats.binge_days[0].count == 16


def test_dormancy(make_track):
    tracks = [make_track(added_at="2024-01-01")]
    stats = compute_library_stats(tracks, today=TODAY)
    assert stats.dormant_months is not None
    assert stats.dormant_months >= 24
    assert stats.adds_last_365d == 0


def test_graceful_degradation_without_metadata(make_track):
    tracks = [
        make_track(year=None, genre=None, added_at=None),
        make_track(year=None, genre=None, added_at="кривая дата"),
    ]
    stats = compute_library_stats(tracks, today=TODAY)
    assert stats.total_tracks == 2
    assert stats.median_year is None
    assert stats.genre_shares == []
    assert stats.first_added is None
    block = format_stats_block(stats)
    assert "Всего треков: 2" in block


def test_stats_block_mentions_key_facts(make_track):
    tracks = [
        make_track(artists=("Кино",), added_at="2021-11-03") for _ in range(6)
    ] + [make_track(artists=("Queen",), genre="rock", added_at="2016-01-01")]
    block = format_stats_block(compute_library_stats(tracks, today=TODAY))
    assert "Кино" in block
    assert "2021-11-03" in block
    assert "ФАКТЫ О БИБЛИОТЕКЕ" in block


def test_select_tracks_cap(make_track):
    tracks = [
        make_track(title=f"t{i}", artists=(f"a{i % 20}",), added_at=f"20{10 + i % 15}-01-01")
        for i in range(300)
    ]
    selected = select_tracks_for_prompt(tracks, limit=100)
    assert len(selected) <= 100
    # без лимита — возвращает всё как есть
    assert len(select_tracks_for_prompt(tracks[:50], limit=100)) == 50
