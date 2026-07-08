import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from io import BytesIO
from typing import Dict, List, Optional

from fastapi import HTTPException
from google import genai
from PIL import Image

from app.config import GEMINI_IMAGE_MODEL, GEMINI_TEXT_MODEL, IMAGE_DIR
from app.models import Track
from app.prompts import BATTLE_VERSION, PromptManager, RoastLevel

logger = logging.getLogger(__name__)

IMAGE_DIR.mkdir(parents=True, exist_ok=True)

_VERDICT_MARKER = "===VERDICT==="
_SCORE_RE = re.compile(r"SCORE:\s*([0-9]+(?:[.,][0-9]+)?)", re.IGNORECASE)
_DIAGNOSIS_RE = re.compile(r"DIAGNOSIS:\s*(.+)", re.IGNORECASE)
_SHAME_RE = re.compile(r"SHAME:\s*(.+)", re.IGNORECASE)
_WINNER_RE = re.compile(r"^WINNER:\s*(.+)$", re.IGNORECASE | re.MULTILINE)

# Смягчаем только harassment: прожарка — это дружеская агрессия по договорённости
_SAFETY_SETTINGS = [
    genai.types.SafetySetting(
        category="HARM_CATEGORY_HARASSMENT",
        threshold="BLOCK_ONLY_HIGH",
    ),
]

_SOFTEN_SUFFIX = (
    "\n\nВАЖНО: сделай текст чуть мягче по формулировкам, сохрани сарказм, "
    "но без агрессии — это дружеская прожарка по взаимному согласию."
)


@dataclass
class RoastOutcome:
    """Структурированный результат прожарки."""

    text: str
    score: float = 5.0
    diagnosis: str = ""
    shame_facts: List[str] = field(default_factory=list)


def parse_verdict(raw_text: str) -> RoastOutcome:
    """Вырезает блок ===VERDICT=== из текста и парсит его поля."""
    text = (raw_text or "").strip()
    if _VERDICT_MARKER not in text:
        return RoastOutcome(text=text, diagnosis=_first_line(text))

    body, _, verdict_block = text.partition(_VERDICT_MARKER)
    outcome = RoastOutcome(text=body.strip())

    score_match = _SCORE_RE.search(verdict_block)
    if score_match:
        try:
            outcome.score = round(
                min(max(float(score_match.group(1).replace(",", ".")), 0.0), 10.0), 1
            )
        except ValueError:
            pass

    diagnosis_match = _DIAGNOSIS_RE.search(verdict_block)
    if diagnosis_match:
        outcome.diagnosis = diagnosis_match.group(1).strip().rstrip(".")
    if not outcome.diagnosis:
        outcome.diagnosis = _first_line(outcome.text)

    outcome.shame_facts = [
        m.group(1).strip() for m in _SHAME_RE.finditer(verdict_block)
    ][:3]
    return outcome


def parse_winner(raw_text: str) -> tuple[str, Optional[str]]:
    """Возвращает (текст без служебной строки, имя победителя или None)."""
    text = (raw_text or "").strip()
    match = _WINNER_RE.search(text)
    winner = match.group(1).strip() if match else None
    if match:
        text = (text[: match.start()] + text[match.end() :]).strip()
    return text, winner


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        cleaned = line.strip().strip("*").strip()
        if cleaned:
            return cleaned[:120]
    return "вкус не поддаётся диагностике"


class GeminiRoaster:
    """Класс для работы с Google Gemini API."""

    def __init__(self, api_key: str, prompt_manager: PromptManager):
        self.client = genai.Client(api_key=api_key)
        self.prompt_manager = prompt_manager

    # ------------------------------------------------------------------ text

    def _call_text_model(self, system_prompt: str, user_prompt: str) -> str:
        response = self.client.models.generate_content(
            model=GEMINI_TEXT_MODEL,
            contents=user_prompt,
            config=genai.types.GenerateContentConfig(
                system_instruction=system_prompt,
                safety_settings=_SAFETY_SETTINGS,
                temperature=1.0,
            ),
        )

        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            raise _safety_error(response)

        finish_reason = str(getattr(candidates[0], "finish_reason", "") or "")
        if "SAFETY" in finish_reason.upper():
            raise _SafetyBlocked()

        text = response.text
        if not text or not text.strip():
            raise _SafetyBlocked()
        return text

    def _generate_with_retry(self, system_prompt: str, user_prompt: str) -> str:
        try:
            return self._call_text_model(system_prompt, user_prompt)
        except _SafetyBlocked:
            logger.warning("Gemini safety block, retrying with softened prompt")
            try:
                return self._call_text_model(
                    system_prompt + _SOFTEN_SUFFIX, user_prompt
                )
            except _SafetyBlocked as exc:
                raise HTTPException(
                    status_code=502,
                    detail=(
                        "Gemini отказался жарить на этом уровне. "
                        "Попробуй уровень мягче."
                    ),
                ) from exc

    def _build_user_prompt(
        self,
        tracks: List[Track],
        stats_block: Optional[str],
        track_list_header: str,
        diagnosis_block: Optional[str] = None,
    ) -> str:
        lines: List[str] = []
        if diagnosis_block:
            lines.append(diagnosis_block)
            lines.append("")
        if stats_block:
            lines.append(stats_block)
            lines.append("")
        lines.append(track_list_header)
        for track in tracks:
            meta: List[str] = []
            if track.year:
                meta.append(str(track.year))
            if track.genre:
                meta.append(track.genre)
            if track.added_at:
                meta.append(f"добавлен {track.added_at}")
            meta_str = f" ({', '.join(meta)})" if meta else ""
            lines.append(f"- {track.title} — {', '.join(track.artists)}{meta_str}")
        return "\n".join(lines)

    def generate_roast(
        self,
        tracks: List[Track],
        stats_block: Optional[str] = None,
        level: RoastLevel = RoastLevel.MEDIUM,
        prompt_version: Optional[str] = None,
        diagnosis_block: Optional[str] = None,
    ) -> RoastOutcome:
        """Генерация прожарки: текст + структурированный вердикт."""
        template = self.prompt_manager.get_template(prompt_version or level.value)
        user_prompt = self._build_user_prompt(
            tracks, stats_block, template.track_list_header, diagnosis_block
        )

        try:
            raw_text = self._generate_with_retry(template.system_prompt, user_prompt)
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Gemini roast generation failed")
            raise HTTPException(
                status_code=502, detail=f"Ошибка Gemini API: {exc}"
            ) from exc

        outcome = parse_verdict(raw_text)
        if not outcome.shame_facts and stats_block:
            outcome.shame_facts = self._extract_verdict_fallback(outcome.text)
        return outcome

    def _extract_verdict_fallback(self, roast_text: str) -> List[str]:
        """Дешёвый структурный вызов, если модель не вернула блок вердикта."""
        try:
            response = self.client.models.generate_content(
                model=GEMINI_TEXT_MODEL,
                contents=(
                    "Вот текст прожарки музыкального вкуса. Выдели из него "
                    "3 самых позорных факта (коротко, с цифрами если есть):\n\n"
                    + roast_text
                ),
                config=genai.types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema={
                        "type": "object",
                        "properties": {
                            "facts": {
                                "type": "array",
                                "items": {"type": "string"},
                            }
                        },
                        "required": ["facts"],
                    },
                ),
            )
            data = json.loads(response.text)
            return [str(fact) for fact in data.get("facts", [])][:3]
        except Exception:  # noqa: BLE001 — вердикт не должен ронять прожарку
            logger.warning("verdict fallback extraction failed", exc_info=True)
            return []

    # ---------------------------------------------------------------- battle

    def generate_battle_verdict(self, dossier_a: str, dossier_b: str) -> str:
        """Судейский вердикт батла по двум досье (без токенов участников)."""
        template = self.prompt_manager.get_template(BATTLE_VERSION)
        user_prompt = (
            f"УЧАСТНИК A:\n{dossier_a}\n\nУЧАСТНИК B:\n{dossier_b}\n\nСуди бой."
        )
        try:
            return self._generate_with_retry(template.system_prompt, user_prompt)
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Gemini battle verdict failed")
            raise HTTPException(
                status_code=502, detail=f"Ошибка Gemini API: {exc}"
            ) from exc

    # ----------------------------------------------------------------- image

    def generate_image(self, roast_text: str) -> Dict[str, str]:
        """Генерация «обложки позора» на основе текста прожарки."""
        try:
            prompt = (
                "На вход: «прожарка» музыкального вкуса (жёсткий юмористический текст). "
                "На выход: ультрареалистичное фотореалистичное изображение без единого "
                "текста, переполненное деталями-отсылками к прожарке. Не изображай "
                "самого пользователя, пластинки или телефон — визуализируй образы из "
                "текста; известных артистов изображать можно.\n\nТекст прожарки:\n"
                + roast_text
            )

            response = self.client.models.generate_content(
                model=GEMINI_IMAGE_MODEL,
                contents=prompt,
            )

            image_parts = [
                part.inline_data.data
                for part in response.candidates[0].content.parts
                if part.inline_data
            ]

            if not image_parts:
                raise HTTPException(
                    status_code=502, detail="Не удалось сгенерировать изображение"
                )

            image_filename = f"{uuid.uuid4()}.png"
            image_path = IMAGE_DIR / image_filename
            image = Image.open(BytesIO(image_parts[0]))
            image.save(image_path)

            return {
                "image_url": f"/static/images/{image_filename}",
                "image_path": str(image_path),
            }
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Image generation failed")
            raise HTTPException(
                status_code=502, detail=f"Ошибка генерации изображения: {exc}"
            ) from exc


class _SafetyBlocked(Exception):
    """Модель отказалась отвечать из-за safety-фильтра."""


def _safety_error(response: object) -> Exception:
    feedback = getattr(response, "prompt_feedback", None)
    if feedback and getattr(feedback, "block_reason", None):
        return _SafetyBlocked()
    return _SafetyBlocked()
