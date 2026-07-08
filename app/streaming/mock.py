"""Моковый стриминг для e2e-прогонов без реального аккаунта Яндекса.

Библиотека намеренно «прожариваемая»: перекос по одному артисту, кластер
2016–2017, запойный день с Кино, залётный шансон и k-pop, полтора свежих
добавления за год.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple, Union

from .base import StreamingProvider, StreamingService
from .snapshot import DeclaredItem, RawTasteSnapshot

# Стабильные id артистов для оси обскурности
_ARTIST_IDS = {
    "Кино": 41075,
    "Oxxxymiron": 41097,
    "Скриптонит": 4331779,
    "Егор Крид": 3504094,
    "JONY": 6826935,
}

# Фиксированная популярность (слушатели/мес) для мок-артистов
_ARTIST_LISTENERS = {
    41075: 3_200_000,
    41097: 1_800_000,
    4331779: 2_500_000,
    3504094: 4_100_000,
    6826935: 3_900_000,
}

_MOCK_LYRICS = (
    "Перемен требуют наши сердца\nПеремен требуют наши глаза\n"
    "В нашем смехе и в наших слезах\nПеремен, мы ждём перемен"
)


def _track(
    track_id: int,
    title: str,
    artist: str,
    year: int,
    genre: str,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=track_id,
        title=title,
        artists=[
            SimpleNamespace(name=artist, id=_ARTIST_IDS.get(artist, 100000 + track_id))
        ],
        albums=[SimpleNamespace(id=track_id * 10, year=year, genre=genre)],
        duration_ms=180000 + (track_id % 60) * 1000,
        explicit=(track_id % 7 == 0),
        lyrics_available=(track_id % 10 < 7),  # покрытие ~70% — гейт проходит
    )


def _build_library() -> Tuple[List[SimpleNamespace], Dict[str, str]]:
    tracks: List[SimpleNamespace] = []
    added: Dict[str, str] = {}
    next_id = 1

    def add(title: str, artist: str, year: int, genre: str, added_at: str) -> None:
        nonlocal next_id
        tracks.append(_track(next_id, title, artist, year, genre))
        added[str(next_id)] = added_at
        next_id += 1

    # Кластер 2016–2017: русский рэп, добавлялся в 2016–2017
    oxxy_titles = [
        "Городская магия", "Неваляшка", "Признаки жизни", "Девочка Пи*дец",
        "Переплетено", "Полигон", "Колыбельная", "Накануне", "Башня из слоновой кости",
        "Тентакли", "Жук в муравейнике", "Не от мира сего",
    ]
    for i, title in enumerate(oxxy_titles):
        add(title, "Oxxxymiron", 2015 + (i % 3), "rusrap", f"2016-{(i % 12) + 1:02d}-11")

    skr_titles = [
        "Это любовь", "Положение", "Мультибрендовый", "Танцуй сама",
        "Космос", "Стиль", "Балаклава", "Животные",
    ]
    for i, title in enumerate(skr_titles):
        add(title, "Скриптонит", 2016 + (i % 2), "rusrap", f"2017-{(i % 9) + 1:02d}-03")

    # Запойный день: 24 трека Кино за один вечер
    kino_titles = [
        "Группа крови", "Звезда по имени Солнце", "Пачка сигарет", "Кукушка",
        "Спокойная ночь", "Перемен", "Восьмиклассница", "Бошетунмай",
        "Стук", "Закрой за мной дверь", "Место для шага вперёд", "Война",
        "Последний герой", "Красно-жёлтые дни", "Апрель", "Троллейбус",
        "Дальше действовать будем мы", "Мама, мы все тяжело больны",
        "Легенда", "Пески", "Транквилизатор", "Муравейник", "Сосны", "Дождь",
    ]
    for title in kino_titles:
        add(title, "Кино", 1988, "rusrock", "2021-11-03")

    # Поп-фон разных лет
    pop = [
        ("Юность", "Dabro", 2020, "ruspop", "2020-09-15"),
        ("Комета", "JONY", 2019, "ruspop", "2020-02-10"),
        ("Лали", "JONY", 2019, "ruspop", "2020-02-10"),
        ("Девочка-война", "Егор Крид", 2017, "ruspop", "2017-06-21"),
        ("Самая Самая", "Егор Крид", 2014, "ruspop", "2016-03-02"),
        ("Розовое вино", "Feduk", 2017, "ruspop", "2017-11-30"),
        ("Ламбада", "T-Fest", 2017, "rusrap", "2017-11-30"),
        ("Вите надо выйти", "Estradarada", 2016, "ruspop", "2017-04-01"),
        ("Между нами тает лёд", "Грибы", 2017, "rusrap", "2017-05-09"),
        ("Тает лёд (remix)", "Грибы", 2017, "rusrap", "2017-05-09"),
    ]
    for row in pop:
        add(*row)

    # Западная классика для образа
    west = [
        ("Bohemian Rhapsody", "Queen", 1975, "rock", "2018-01-15"),
        ("Numb", "Linkin Park", 2003, "alternative", "2016-08-08"),
        ("In the End", "Linkin Park", 2000, "alternative", "2016-08-08"),
        ("Lose Yourself", "Eminem", 2002, "foreignrap", "2016-09-01"),
        ("Blinding Lights", "The Weeknd", 2019, "pop", "2020-05-20"),
    ]
    for row in west:
        add(*row)

    # Залётные жанры — guilty pleasures
    add("Владимирский централ", "Михаил Круг", 1998, "shanson", "2019-05-12")
    add("Кольщик", "Михаил Круг", 1999, "shanson", "2019-05-12")
    add("Dynamite", "BTS", 2020, "kpop", "2020-08-25")

    # Полтора свежих добавления — библиотека почти заброшена
    add("вау", "Мирон Фёдоров", 2024, "rusrap", "2025-04-02")
    add("Поезда", "Скриптонит", 2023, "rusrap", "2025-01-15")

    # Программный хвост до N>=200: гейт диагноза требует мощности выборки.
    # Волны добавлений 2016-2022, угасающие к концу — кормит ось тренда.
    filler_artists = [
        ("Баста", "rusrap"), ("Noize MC", "rusrap"), ("Монеточка", "ruspop"),
        ("Face", "rusrap"), ("Пошлая Молли", "alternative"), ("Три дня дождя", "rusrock"),
        ("Макс Корж", "rusrap"), ("Мумий Тролль", "rusrock"), ("Сплин", "rusrock"),
        ("Земфира", "rusrock"), ("ЛСП", "rusrap"), ("Элджей", "rusrap"),
    ]
    year_waves = [
        (2016, 4), (2017, 4), (2018, 3), (2019, 3),
        (2020, 2), (2021, 2), (2022, 1),
    ]
    i = 0
    while len(tracks) < 205:
        artist, genre = filler_artists[i % len(filler_artists)]
        year, per_month = year_waves[i % len(year_waves)]
        month = (i % 12) + 1
        day = (i % per_month) * 7 + 1
        add(f"Трек {i}", artist, year - 1, genre, f"{year}-{month:02d}-{day:02d}")
        i += 1

    return tracks, added


class MockStreamingService(StreamingService):
    """Отдаёт фиксированную библиотеку для любого токена."""

    provider: StreamingProvider = StreamingProvider.YANDEX

    def __init__(self, token: str):
        super().__init__(token)
        self._tracks, self._added = _build_library()

    def get_account_summary(self) -> Dict[str, Any]:
        return {
            "uid": "0",
            "display_name": "Тестовый Меломан",
            "liked_count": len(self._tracks),
        }

    def list_playlists(self, owner_id: Union[str, int] = "me") -> List[Dict[str, Any]]:
        return [
            {
                "kind": "liked",
                "title": "Мне нравится",
                "track_count": len(self._tracks),
                "owner_uid": "0",
                "visibility": "private",
                "is_liked": True,
                "description": "Лайкнутые треки пользователя",
            }
        ]

    def get_playlist_tracks(
        self, playlist_kind: Union[int, str], owner_id: Union[str, int] = "me"
    ) -> tuple[List[Any], Dict[str, str], Dict[str, Any]]:
        return (
            list(self._tracks),
            dict(self._added),
            {
                "kind": "liked",
                "title": "Мне нравится",
                "track_count": len(self._tracks),
                "owner_uid": "0",
                "is_liked": True,
            },
        )

    # ------------------------------------------------------ taste snapshot

    def get_taste_snapshot(self) -> RawTasteSnapshot:
        # Чарт пересекается с лайками на 3 трека (Юность, Комета, Blinding Lights)
        chart_ids = {}
        for track in self._tracks:
            if track.title in {"Юность", "Комета", "Blinding Lights"}:
                chart_ids[str(track.id)] = len(chart_ids) + 1
        for fake_id in range(9000, 9100):
            chart_ids[str(fake_id)] = len(chart_ids) + 1

        return RawTasteSnapshot(
            liked_tracks=list(self._tracks),
            liked_added=dict(self._added),
            chart_positions=chart_ids,
            # Заявлен «инди-рок» при фактическом рэпе — кормит ось разрыва
            pins=[
                DeclaredItem(kind="artist", name="Radiohead"),
                DeclaredItem(kind="playlist", name="Тонкий инди-рок"),
            ],
            playlist_titles=["Тонкий инди-рок", "Джаз для чтения", "Новый плейлист"],
            # Дизлайкнут Егор Крид при 2 его треках в лайках — ось лицемерия
            disliked_artist_names=["Егор Крид", "Клава Кока"],
            disliked_track_artists=["Инстасамка"],
            disliked_genres=["ruspop"],
        )

    def get_artist_popularity(self, artist_ids: List[int]) -> Dict[int, Optional[int]]:
        return {aid: _ARTIST_LISTENERS.get(aid, 50_000) for aid in artist_ids}

    def fetch_lyrics(self, track_id: str) -> Optional[str]:
        return _MOCK_LYRICS
