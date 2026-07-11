"""Generic LLM-клиент поверх OpenAI-совместимого API (по умолчанию OpenRouter).

Провайдер и модели задаются через env (см. app/config.py), поэтому сменить
модель — это правка `LLM_TEXT_MODEL`, а не кода. Транспорт синхронный (httpx),
как и вызывающий его сервис.
"""

import base64
import json
import logging
import re
from typing import Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


class LLMError(RuntimeError):
    """Провайдер вернул ошибку или пустой ответ."""


class LLMRateLimited(LLMError):
    """Провайдер вернул 429 — все доступные модели временно перегружены."""


class LLMClient:
    """Тонкая обёртка над chat/completions OpenAI-совместимого провайдера."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str,
        text_model: str,
        image_model: str,
        temperature: float = 1.0,
        app_url: str = "",
        app_title: str = "",
        timeout: float = 90.0,
    ):
        self.text_model = text_model
        self.image_model = image_model
        self.temperature = temperature
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        # OpenRouter использует эти заголовки для ранжирования; другим провайдерам не мешают
        if app_url:
            headers["HTTP-Referer"] = app_url
        if app_title:
            headers["X-Title"] = app_title
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"), headers=headers, timeout=timeout
        )

    # ------------------------------------------------------------------ core

    def _post(self, payload: Dict) -> Dict:
        try:
            response = self._client.post("/chat/completions", json=payload)
        except httpx.HTTPError as exc:
            raise LLMError(f"сеть недоступна: {exc}") from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise LLMError(
                f"невалидный ответ ({response.status_code}): {response.text[:200]}"
            ) from exc

        # Провайдер может вернуть ошибку и в теле при 200, и при не-2xx
        if isinstance(data, dict) and data.get("error"):
            err = data["error"]
            code = None
            if isinstance(err, dict):
                message = err.get("message") or "ошибка провайдера"
                code = err.get("code")
                raw = (err.get("metadata") or {}).get("raw")
                if raw and raw not in message:
                    message = f"{message}: {raw}"
            else:
                message = str(err)
            if _is_rate_limit(code, response.status_code, message):
                raise LLMRateLimited(message)
            raise LLMError(message)
        if response.status_code == 429:
            raise LLMRateLimited(f"HTTP 429: {response.text[:200]}")
        if response.status_code >= 400:
            raise LLMError(f"HTTP {response.status_code}: {response.text[:200]}")
        return data

    @staticmethod
    def _first_message(data: Dict) -> Dict:
        choices = data.get("choices") or []
        if not choices:
            raise LLMError("ответ без choices")
        return choices[0].get("message") or {}

    # ------------------------------------------------------------------ text

    def chat(
        self,
        user: str,
        *,
        system: Optional[str] = None,
        temperature: Optional[float] = None,
        model: Optional[str] = None,
    ) -> str:
        """Один текстовый вызов. Пустой ответ трактуется как отказ модели."""
        messages: List[Dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})

        payload = {
            "model": model or self.text_model,
            "messages": messages,
            "temperature": self.temperature if temperature is None else temperature,
        }
        message = self._first_message(self._post(payload))
        text = (message.get("content") or "").strip()
        if not text:
            raise LLMError("пустой ответ модели")
        return text

    def chat_json(
        self, user: str, *, model: Optional[str] = None
    ) -> Optional[Dict]:
        """Структурный вызов: просим строгий JSON и парсим его (best-effort)."""
        payload = {
            "model": model or self.text_model,
            "messages": [
                {
                    "role": "system",
                    "content": "Отвечай ТОЛЬКО валидным JSON без пояснений и без обёрток.",
                },
                {"role": "user", "content": user},
            ],
            "temperature": 0.4,
            "response_format": {"type": "json_object"},
        }
        try:
            message = self._first_message(self._post(payload))
        except LLMError:
            logger.warning("llm json call failed", exc_info=True)
            return None
        return _parse_json_blob(message.get("content") or "")

    # ----------------------------------------------------------------- image

    def generate_image(self, prompt: str, *, model: Optional[str] = None) -> bytes:
        """Генерация изображения через мультимодальную модель (modalities=image)."""
        payload = {
            "model": model or self.image_model,
            "messages": [{"role": "user", "content": prompt}],
            "modalities": ["image", "text"],
        }
        message = self._first_message(self._post(payload))
        for image in message.get("images") or []:
            url = (image.get("image_url") or {}).get("url", "")
            if url.startswith("data:"):
                _, _, b64 = url.partition(",")
                return base64.b64decode(b64)
        raise LLMError("модель не вернула изображение")

    def close(self) -> None:
        self._client.close()


def _is_rate_limit(code: object, status_code: int, message: str) -> bool:
    if code == 429 or status_code == 429:
        return True
    low = (message or "").lower()
    return "rate-limit" in low or "rate limit" in low or "too many requests" in low


def _parse_json_blob(raw: str) -> Optional[Dict]:
    """Достаёт JSON из ответа, снимая ```-обёртки, если они есть."""
    text = (raw or "").strip()
    if not text:
        return None
    fence = _FENCE_RE.search(text)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Иногда модель добавляет текст до/после объекта — берём первый {...}
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None
