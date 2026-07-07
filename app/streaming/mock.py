"""Моковый стриминг для e2e-прогонов без реального аккаунта Яндекса.

Библиотека намеренно «прожариваемая»: перекос по одному артисту, кластер
2016–2017, запойный день с Кино, залётный шансон и k-pop, полтора свежих
добавления за год.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List, Tuple, Union

from .base import StreamingProvider, StreamingService


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
        artists=[SimpleNamespace(name=artist)],
        albums=[SimpleNamespace(id=track_id * 10, year=year, genre=genre)],
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
