"""Утилиты для отправки длинных текстов в Telegram."""

from __future__ import annotations

from typing import List

TELEGRAM_SAFE_LIMIT = 3500


def split_for_telegram(text: str, limit: int = TELEGRAM_SAFE_LIMIT) -> List[str]:
    """Режет сырой markdown-текст на куски по границам абзацев.

    Резать нужно ДО конвертации в HTML, чтобы не порвать теги. Один абзац
    длиннее лимита дорезается по предложениям/символам.
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    chunks: List[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        # Абзац сам по себе больше лимита — режем жёстко
        while len(paragraph) > limit:
            cut = paragraph.rfind(". ", 0, limit)
            cut = cut + 1 if cut > limit // 2 else limit
            chunks.append(paragraph[:cut].strip())
            paragraph = paragraph[cut:].strip()
        current = paragraph
    if current:
        chunks.append(current)
    return chunks
