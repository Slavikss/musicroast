import uuid
from io import BytesIO
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from google import genai
from PIL import Image

from app.config import IMAGE_DIR
from app.models import Track
from app.prompts import PromptManager


IMAGE_DIR.mkdir(parents=True, exist_ok=True)


class GeminiRoaster:
    """Класс для работы с Google Gemini API"""

    def __init__(self, api_key: str, prompt_manager: PromptManager):
        self.client = genai.Client(api_key=api_key)
        self.prompt_manager = prompt_manager

    def _search_relevant_memes(self) -> List[str]:
        """Поиск актуальных мемов без учёта плейлиста"""
        memes: List[str] = []
        return memes[:5]

    def generate_image(self, roast_text: str) -> Dict[str, str]:
        """Генерация изображения на основе прожарки"""
        try:
            prompt = f"""На вход: произвольная «прожарка» музыкального вкуса пользователя (жёсткий юмористический текст).  
                        На выход: ультрареалистичное фотореалистичное изображение с максимальным кол-вом деталем, без единого текста, где каждая деталь настолько насыщена смыслом, что «отсылки на каждый квадратный сантиметр» буквально переполняют кадр. 
                        Нужно использовать все доступные детали и элементы, чтобы изображение было максимально реалистичным и насыщенным. НЕ надо изображать самого юзера, пластинки с треками, телефон и тд, надо изображать образы которые у него в голове исходя из описания прожарки, также можно генерировать артистов, если ты знаешь их
                        Нужно учитывать каждую детально и элемент, полученных в тексте прожарки ниже:
                        {roast_text}"""

            response = self.client.models.generate_content(
                model="gemini-2.5-flash-image",
                contents=prompt,
            )

            image_parts = [
                part.inline_data.data
                for part in response.candidates[0].content.parts
                if part.inline_data
            ]

            if image_parts:
                image_data = image_parts[0]
                image_filename = f"{uuid.uuid4()}.png"
                image_path = IMAGE_DIR / image_filename

                image = Image.open(BytesIO(image_data))
                image.save(image_path)

                image_url = f"/static/images/{image_filename}"
                return {"image_url": image_url}

            raise HTTPException(
                status_code=500, detail="Не удалось сгенерировать изображение"
            )

        except Exception as exc:
            print(f"Произошла ошибка при генерации изображения: {exc}")
            raise HTTPException(
                status_code=500, detail=f"Ошибка генерации изображения: {exc}"
            ) from exc

    def _build_prompts(
        self, tracks: List[Track], prompt_version: Optional[str] = None
    ) -> tuple[str, str]:
        template = self.prompt_manager.get_template(prompt_version)
        system_prompt = template.system_prompt.format(
            year=self._current_year(), month=self._current_month()
        )

        user_lines = [template.track_list_header]
        for track in tracks:
            meta: List[str] = []
            if track.year:
                meta.append(str(track.year))
            if track.genre:
                meta.append(track.genre)
            if track.added_at:
                meta.append(f"added: {track.added_at}")

            meta_str = f" ({', '.join(meta)})" if meta else ""
            user_lines.append(f"- {track.title} — {', '.join(track.artists)}{meta_str}")

        return system_prompt, "\n".join(user_lines)

    def _current_year(self) -> int:
        from datetime import datetime

        return datetime.now().year

    def _current_month(self) -> int:
        from datetime import datetime

        return datetime.now().month

    def generate_roast(
        self, tracks: List[Track], prompt_version: Optional[str] = None
    ) -> str:
        """Генерация прожарки на основе списка треков"""
        try:
            system_prompt, user_prompt = self._build_prompts(
                tracks, prompt_version=prompt_version
            )

            response = self.client.models.generate_content(
                model="gemini-flash-latest",
                contents=f"User input: {user_prompt}:",
                config=genai.types.GenerateContentConfig(
                    system_instruction=system_prompt
                ),
            )

            return response.text

        except Exception as exc:
            print(f"Произошла ошибка: {exc}")
            raise HTTPException(status_code=500, detail=f"Ошибка Gemini API: {exc}") from exc
