import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from fastapi import HTTPException

from app.config import (
    IMAGE_DIR,
    LLM_API_KEY,
    LLM_APP_TITLE,
    LLM_APP_URL,
    LLM_BASE_URL,
    LLM_IMAGE_MODEL,
    LLM_TEMPERATURE,
    LLM_TEXT_MODEL,
)
from app.models import Track
from app.prompts import BATTLE_VERSION, PromptManager, RoastLevel
from app.services.llm_client import LLMClient, LLMError, LLMRateLimited

logger = logging.getLogger(__name__)

IMAGE_DIR.mkdir(parents=True, exist_ok=True)

_VERDICT_MARKER = "===VERDICT==="
_SCORE_RE = re.compile(r"SCORE:\s*([0-9]+(?:[.,][0-9]+)?)", re.IGNORECASE)
_DIAGNOSIS_NAME_RE = re.compile(r"DIAGNOSIS_NAME:\s*(.+)", re.IGNORECASE)
_SEVERITY_RE = re.compile(r"SEVERITY:\s*([0-9]+)", re.IGNORECASE)
_DIAGNOSIS_RE = re.compile(r"DIAGNOSIS:\s*(.+)", re.IGNORECASE)
_SYMPTOM_RE = re.compile(r"SYMPTOM:\s*(.+)", re.IGNORECASE)
_SHAME_RE = re.compile(r"SHAME:\s*(.+)", re.IGNORECASE)
_PRESCRIPTION_RE = re.compile(r"PRESCRIPTION:\s*(.+)", re.IGNORECASE)
_WINNER_RE = re.compile(r"^WINNER:\s*(.+)$", re.IGNORECASE | re.MULTILINE)

DEFAULT_PRESCRIPTION = "две недели слушать тишину, потом начать с чистого листа"

# Сообщение при 429: доктор «уснул», а не «модель отказалась»
DOCTOR_NAP_MESSAGE = (
    "😴 Доктор прилёг вздремнуть прямо на кушетке — все кабинеты заняты, очередь. "
    "Ткни ещё разок через минуту, он проснётся и дожарит."
)

_SOFTEN_SUFFIX = (
    "\n\nВАЖНО: сделай текст чуть мягче по формулировкам, сохрани сарказм, "
    "но без агрессии — это дружеская прожарка по взаимному согласию."
)


@dataclass
class RoastOutcome:
    """Структурированный результат прожарки/осмотра."""

    text: str
    score: float = 5.0
    diagnosis: str = ""  # фраза-приговор
    diagnosis_name: str = ""  # название болезни, придуманное LLM
    severity: int = 5  # стадия 1-10
    symptoms: List[str] = field(default_factory=list)
    shame_facts: List[str] = field(default_factory=list)
    prescription: str = ""


def parse_verdict(raw_text: str) -> RoastOutcome:
    """Вырезает блок ===VERDICT=== из текста и парсит его поля с фоллбеками."""
    text = (raw_text or "").strip()
    if _VERDICT_MARKER not in text:
        outcome = RoastOutcome(text=text, diagnosis=_first_line(text))
        _apply_fallbacks(outcome)
        return outcome

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

    name_match = _DIAGNOSIS_NAME_RE.search(verdict_block)
    if name_match:
        outcome.diagnosis_name = name_match.group(1).strip().strip("«»\"'").rstrip(".")

    severity_match = _SEVERITY_RE.search(verdict_block)
    if severity_match:
        try:
            outcome.severity = min(max(int(severity_match.group(1)), 1), 10)
        except ValueError:
            pass
    else:
        outcome.severity = 0  # заполнится фоллбеком

    # «DIAGNOSIS_NAME:» не матчится на «DIAGNOSIS:» (после слова идёт «_», не «:»)
    diagnosis_match = _DIAGNOSIS_RE.search(verdict_block)
    if diagnosis_match:
        outcome.diagnosis = diagnosis_match.group(1).strip().rstrip(".")

    outcome.symptoms = [
        m.group(1).strip() for m in _SYMPTOM_RE.finditer(verdict_block)
    ][:3]
    outcome.shame_facts = [
        m.group(1).strip() for m in _SHAME_RE.finditer(verdict_block)
    ][:3]

    prescription_match = _PRESCRIPTION_RE.search(verdict_block)
    if prescription_match:
        outcome.prescription = prescription_match.group(1).strip().rstrip(".")

    _apply_fallbacks(outcome)
    return outcome


def _apply_fallbacks(outcome: RoastOutcome) -> None:
    """Приговор обязан существовать целиком — недостающее достраивается."""
    if not outcome.diagnosis:
        outcome.diagnosis = outcome.diagnosis_name or _first_line(outcome.text)
    if not outcome.diagnosis_name:
        outcome.diagnosis_name = outcome.diagnosis
    if not outcome.severity:
        outcome.severity = min(max(round(10 - outcome.score), 1), 10)
    if not outcome.symptoms:
        outcome.symptoms = list(outcome.shame_facts)
    if not outcome.prescription:
        outcome.prescription = DEFAULT_PRESCRIPTION


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


class LLMRoaster:
    """Провайдер-независимый роастер. Модель и провайдер берутся из env."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        prompt_manager: Optional[PromptManager] = None,
        *,
        base_url: Optional[str] = None,
        text_model: Optional[str] = None,
        image_model: Optional[str] = None,
        temperature: Optional[float] = None,
    ):
        self.prompt_manager = prompt_manager
        self.llm = LLMClient(
            api_key or LLM_API_KEY,
            base_url=base_url or LLM_BASE_URL,
            text_model=text_model or LLM_TEXT_MODEL,
            image_model=image_model or LLM_IMAGE_MODEL,
            temperature=LLM_TEMPERATURE if temperature is None else temperature,
            app_url=LLM_APP_URL,
            app_title=LLM_APP_TITLE,
        )

    # ------------------------------------------------------------------ text

    def _generate_with_retry(self, system_prompt: str, user_prompt: str) -> str:
        try:
            return self.llm.chat(user_prompt, system=system_prompt)
        except LLMRateLimited as exc:
            raise HTTPException(status_code=503, detail=DOCTOR_NAP_MESSAGE) from exc
        except LLMError:
            logger.warning("LLM refused/empty, retrying with softened prompt")
            try:
                return self.llm.chat(user_prompt, system=system_prompt + _SOFTEN_SUFFIX)
            except LLMRateLimited as exc:
                raise HTTPException(status_code=503, detail=DOCTOR_NAP_MESSAGE) from exc
            except LLMError as exc:
                raise HTTPException(
                    status_code=502,
                    detail=(
                        "Модель отказалась жарить на этом уровне. "
                        "Попробуй уровень мягче."
                    ),
                ) from exc

    def _build_user_prompt(
        self,
        tracks: List[Track],
        stats_block: Optional[str],
        track_list_header: str,
        medkarta: Optional[str] = None,
    ) -> str:
        lines: List[str] = []
        if medkarta:
            # Медкарта включает блок фактов библиотеки — stats_block не дублируем
            lines.append(medkarta)
            lines.append("")
        elif stats_block:
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
        medkarta: Optional[str] = None,
    ) -> RoastOutcome:
        """Осмотр пациента: текст прожарки + структурированный диагноз."""
        template = self.prompt_manager.get_template(prompt_version or level.value)
        user_prompt = self._build_user_prompt(
            tracks, stats_block, template.track_list_header, medkarta
        )

        try:
            raw_text = self._generate_with_retry(template.system_prompt, user_prompt)
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("LLM roast generation failed")
            raise HTTPException(
                status_code=502, detail=f"Ошибка LLM API: {exc}"
            ) from exc

        outcome = parse_verdict(raw_text)
        if not outcome.shame_facts and stats_block:
            outcome.shame_facts = self._extract_verdict_fallback(outcome.text)
        return outcome

    def _extract_verdict_fallback(self, roast_text: str) -> List[str]:
        """Дешёвый структурный вызов, если модель не вернула блок вердикта."""
        data = self.llm.chat_json(
            "Вот текст прожарки музыкального вкуса. Выдели из него 3 самых "
            "позорных факта (коротко, с цифрами если есть). Верни JSON вида "
            '{"facts": ["...", "...", "..."]}.\n\n' + roast_text
        )
        if not data:
            return []
        facts = data.get("facts", [])
        return [str(fact) for fact in facts][:3] if isinstance(facts, list) else []

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
            logger.exception("LLM battle verdict failed")
            raise HTTPException(
                status_code=502, detail=f"Ошибка LLM API: {exc}"
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

            image_bytes = self.llm.generate_image(prompt)

            image_filename = f"{uuid.uuid4()}.png"
            image_path = IMAGE_DIR / image_filename
            image_path.write_bytes(image_bytes)

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
