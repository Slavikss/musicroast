"""Helpers for cleaning up Gemini markdown output for Telegram."""

from __future__ import annotations

import html
import re


_BOLD_PATTERN = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_ITALIC_PATTERN = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", re.DOTALL)
_UNDERSCORE_PATTERN = re.compile(r"(?<!_)_(?!_)(.+?)(?<!_)_(?!_)", re.DOTALL)


def convert_markdown_to_html(text: str) -> str:
    """Transform Gemini markdown into Telegram-friendly HTML."""
    if not text:
        return ""

    escaped = html.escape(text)
    result = _BOLD_PATTERN.sub(lambda m: f"<b>{m.group(1)}</b>", escaped)
    result = _ITALIC_PATTERN.sub(lambda m: f"<i>{m.group(1)}</i>", result)
    result = _UNDERSCORE_PATTERN.sub(lambda m: f"<i>{m.group(1)}</i>", result)
    return result

