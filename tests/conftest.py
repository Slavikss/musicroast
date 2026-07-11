import os

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")
os.environ.setdefault("MOCK_STREAMING", "1")
os.environ.setdefault("APP_MODE", "api")

import pytest  # noqa: E402

from app.models import Track  # noqa: E402


@pytest.fixture
def make_track():
    def _make(
        title="Трек",
        artists=("Артист",),
        year=2020,
        genre="pop",
        added_at="2021-06-01",
    ):
        return Track(
            title=title,
            artists=list(artists),
            year=year,
            genre=genre,
            added_at=added_at,
        )

    return _make
