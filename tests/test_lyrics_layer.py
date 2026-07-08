from app.models import Track
from app.services.lyrics_layer import (
    MAX_CHARS_PER_LYRIC,
    MAX_TRACKS,
    collect_lyrics,
    pick_lyric_candidates,
)


def _track(i, artist="a", lyrics=True):
    return Track(
        title=f"t{i}",
        artists=[artist],
        track_id=str(i),
        lyrics_available=lyrics,
    )


class FakeFetcher:
    def __init__(self, text="строчка\n" * 400, fail_ids=()):
        self.text = text
        self.fail_ids = set(fail_ids)
        self.calls = []

    def fetch_lyrics(self, track_id):
        self.calls.append(track_id)
        if track_id in self.fail_ids:
            return None
        return self.text


def test_pick_candidates_respects_availability_and_cap():
    evidence = [_track(i) for i in range(3)]
    library = [_track(10 + i, artist="Кино", lyrics=(i % 2 == 0)) for i in range(30)]
    candidates = pick_lyric_candidates(evidence, library, top_artist="Кино")
    assert len(candidates) <= MAX_TRACKS
    assert all(t.lyrics_available for t in candidates)
    # улики идут первыми
    assert candidates[0].title == "t0"


def test_collect_lyrics_truncates_and_skips_failures():
    fetcher = FakeFetcher(fail_ids={"1"})
    tracks = [_track(0), _track(1), _track(2)]
    lyrics = collect_lyrics(fetcher, tracks)
    assert len(lyrics) == 2  # трек 1 упал и молча пропущен
    assert all(len(text) <= MAX_CHARS_PER_LYRIC + 100 for text in lyrics)
