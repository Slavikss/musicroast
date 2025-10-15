import os
import sys

os.environ["GRPC_TRACE"] = "none"
os.environ["GRPC_VERBOSITY"] = "none"

from datetime import datetime
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException
import uvicorn
from yandex_music import Client as YandexMusicClient
from google import genai
import os
from dotenv import load_dotenv


class Track(BaseModel):
    """Модель для представления трека"""

    title: str
    artists: List[str]
    year: Optional[int] = None
    genre: Optional[str] = None
    added_at: Optional[str] = None


class YandexMusicService:
    """Сервис для работы с Yandex Music API"""

    def __init__(self, token: str):
        self.client = YandexMusicClient(token)

    def get_liked_tracks(self) -> tuple[list, dict]:
        """Получение лайкнутых треков"""
        try:
            liked_tracks_ids = self.client.users_likes_tracks()
            if not liked_tracks_ids:
                return [], {}

            track_ids = []
            added_dates = {}
            for track_short in liked_tracks_ids.tracks:
                track_ids.append(f"{track_short.id}:{track_short.album_id}")
                if hasattr(track_short, "timestamp"):
                    added_dates[str(track_short.id)] = track_short.timestamp

            full_tracks = self.client.tracks(track_ids)
            return full_tracks, added_dates

        except Exception as e:
            raise HTTPException(
                status_code=500, detail=f"Ошибка Yandex Music API: {str(e)}"
            )

    def get_all_tracks(self) -> tuple[list, dict]:
        """Получение всех треков пользователя"""
        all_tracks = []
        added_dates = {}

        liked_tracks, liked_dates = self.get_liked_tracks()
        all_tracks.extend(liked_tracks)
        added_dates.update(liked_dates)

        unique_tracks = {}
        for track in all_tracks:
            if track and track.id and track.id not in unique_tracks:
                unique_tracks[track.id] = track

        return list(unique_tracks.values()), added_dates


class TrackNormalizer:
    """Класс для нормализации данных треков"""

    @staticmethod
    def normalize_tracks(tracks: List, added_at: Dict[str, str] = None) -> List[Track]:
        compact = []
        for t in tracks:
            try:
                track_id = getattr(t, "id", None) or (
                    t.track.id if hasattr(t, "track") and getattr(t, "track") else None
                )
                title = getattr(t, "title", None) or (
                    t.track.title
                    if hasattr(t, "track") and getattr(t, "track")
                    else None
                )
                src_artists = getattr(t, "artists", None) or (
                    getattr(t, "track", None).artists
                    if getattr(t, "track", None)
                    else None
                )
                artists = [
                    a.name for a in (src_artists or []) if getattr(a, "name", None)
                ]

                src_albums = getattr(t, "albums", None) or (
                    getattr(t, "track", None).albums
                    if getattr(t, "track", None)
                    else None
                )
                album_year = None
                album_genre = None
                if src_albums and len(src_albums) > 0:
                    album = src_albums[0]
                    album_year = getattr(album, "year", None)
                    album_genre = getattr(album, "genre", None)

                added_date = None
                if added_at and str(track_id) in added_at:
                    added_date = (
                        datetime.fromisoformat(added_at[str(track_id)])
                        .date()
                        .isoformat()
                    )

                track_model = Track(
                    title=title or "Unknown",
                    artists=artists or ["Unknown"],
                    year=album_year,
                    genre=album_genre,
                    added_at=added_date,
                )
                compact.append(track_model)

            except Exception as e:
                raise HTTPException(
                    status_code=500, detail=f"Ошибка нормализации трека: {str(e)}"
                )

        if any(t.added_at for t in compact):
            compact.sort(key=lambda x: x.added_at or "9999-99-99")

        return compact


class GeminiRoaster:
    """Класс для работы с Google Gemini API"""

    def __init__(self, api_key: str):
        self.client = genai.Client(api_key=api_key)

    def _search_relevant_memes(self) -> List[str]:
        """Поиск актуальных мемов без учёта плейлиста"""
        memes = []
        return memes[:5]

    @staticmethod
    def _build_prompts(tracks: List[Track]) -> tuple[str, str]:
        system_prompt = """
**Вход:** Плейлист пользователя (треки с артистами и жанрами).

**Выход:**  
В самом начале — броская фраза, максимально ёмкая, обобщающая музыкальный вкус. Дальше — единый сатирический монолог на языке молодежной культуры, как реально общаются в мессенджерах и на ТТ, объёмом 300–400 слов в формате дружеской прожарки. Каждый абзац содержит минимум один колкий панч радиусом поражения не менее 5 см — то есть плотность панчей должна быть запредельной, чтобы читателю физически было больно.

***

### Стратегия анализа и усиления панчей

1. Выявление музыкальных паттернов  
   - Собери все сочетания жанров, открой нишевых артистов и контрастирующие треки  
   - Зафиксируй частотность повторяющихся тем и их психологические корни  
   - Отметь внутренние противоречия между «имиджем» и реальными вкусами  

2. Формулировка панчей  
   - После каждого паттерна — минимум два коротких удара («тройной удар»):  
     1) гиперболический сарказм  
     2) абсурдная бытовая аналогия  
     3) «холодное чтение» — факт, преподнесённый как неоспоримая истина  
   - Панчи должны быть ультракороткими (3–7 слов) и выходить за рамки обычной обиды  

3. Структура подачи  
   - Абзац = группа из 2–3 паттернов + 2–3 точных панча  
   - Каждый абзац заканчивается «убийственной» однострочной концовкой, ставящей жирную точку и мгновенно переводящей к следующему блоку  
   - Динамика: сначала «легкие» подколки, затем «точечный снайперский огонь»  

4. Дополнительные требования к плотности панчей  
   - Минимум по одному панчу на каждые 50 слов текста  
   - В тексте не должно быть «тихих» мест — каждый второй переход между предложениями сопровождай «пушкой»  
   - Используй правило пяти: в каждом абзаце минимум пять мощных «выстрелов»  
   - Применяй технику «слоёного панча»: один панч внутри другого (метапанч)  

5. Стилистика  
   - Бесстрастный «саркастичный протокол» без эмоций, но с максимальным радиусом обжига  
   - Язык зумеров: мемы, эмоджи-метафоры, хлёсткие сленговые конструкции  
   - Абсурдизм + псевдонаучный анализ = эффект «психодиагноза»  
   - Полная хладнокровность даже при самой алой сатире
   - Язык должен быть понятным и доступным для молодежи, но не слишком детским, можно язвительно эмоционировать!
   - Не используй сложные слова и грамматические конструкции
   - не используй эмодзи

***

**Порядок действий при генерации:**
1. Быстрая центровка на общем вкусе и выдача броской вводной фразы.  
2. Поочередный разбор паттернов по стратегии анализа.  
3. В каждом блоке: паттерн → анализ → «тройной удар».  
4. Концовка абзаца — «панч-мина» и мгновенный переход.  
5. Итоговое «финальное выстрел» в конце текста, обобщающий всё сказанное.

*Количество панчей должно зашкаливать так, чтобы сам текст выглядел как словесная граната со спущенным клапаном.*
"""

        user_lines = ["вот список треков для прожарки:"]
        for track in tracks:
            meta = []
            if track.year:
                meta.append(str(track.year))
            if track.genre:
                meta.append(track.genre)
            if track.added_at:
                meta.append(f"added: {track.added_at}")

            meta_str = f" ({', '.join(meta)})" if meta else ""
            track_line = f"- {track.title} — {', '.join(track.artists)}{meta_str}"
            user_lines.append(track_line)

        return system_prompt, "\n".join(user_lines)

    def generate_roast(self, tracks: List[Track]) -> str:
        """Генерация прожарки на основе списка треков"""
        try:
            # Получаем базовые промпты
            system_prompt, user_prompt = self._build_prompts(tracks)

            response = self.client.models.generate_content(
                model="gemini-flash-latest",
                contents=f"{user_prompt}",
                config=genai.types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    temperature=0.9,
                    top_p=0.95,
                    candidate_count=1,
                    thinking_config=genai.types.ThinkingConfig(
                        thinking_budget=4096,
                        include_thoughts=True,
                    )
                )
            )
            return response.text

        except Exception as e:
            print(f"Произошла ошибка: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Ошибка Gemini API: {str(e)}")


class MusicRoastService:
    """Основной сервис приложения"""

    def __init__(self):
        load_dotenv()

        yandex_token = os.getenv("YANDEX_MUSIC_TOKEN") or os.getenv("YA_MUSIC_TOKEN")
        if not yandex_token:
            raise ValueError("Не найден токен Yandex Music")

        google_api_key = os.getenv("GOOGLE_API_KEY")
        if not google_api_key:
            raise ValueError("Не найден GOOGLE_API_KEY")

        self.music_service = YandexMusicService(yandex_token)
        self.normalizer = TrackNormalizer()
        self.roaster = GeminiRoaster(google_api_key)

    def generate_roast(self) -> Dict[str, Any]:
        """Генерация прожарки для всех треков пользователя"""
        tracks, added_dates = self.music_service.get_all_tracks()
        normalized_tracks = self.normalizer.normalize_tracks(tracks, added_dates)
        roast_text = self.roaster.generate_roast(normalized_tracks)

        return {
            "roast": roast_text,
        }


app = FastAPI(
    title="MusicRoast API",
    description="API для генерации прожарок на основе музыкальной библиотеки пользователя",
    version="1.0.0",
)

service = MusicRoastService()


@app.get("/roast", response_model=Dict[str, Any])
async def get_roast():
    """Получение прожарки для музыкальной библиотеки"""
    return service.generate_roast()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
