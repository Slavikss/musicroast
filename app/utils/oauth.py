"""Разбор пользовательского ввода с токеном Яндекс Музыки.

Принимает всё, что юзер мог прислать после OAuth-редиректа: полный URL с
фрагментом, сам фрагмент/query-строку или голый токен, в том числе внутри
произвольного текста. Мусор не считается токеном.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import parse_qsl

# Современные токены Яндекса: y0_..., y0__..., y1_... и т.п.
_YANDEX_TOKEN_RE = re.compile(r"\by[0-9][_-][A-Za-z0-9_-]{20,300}")
# Легаси-формат OAuth-токенов Яндекса
_LEGACY_TOKEN_RE = re.compile(r"\bAQAA[A-Za-z0-9_-]{20,200}")


@dataclass
class ParsedToken:
    access_token: str
    expires_in: Optional[int] = None


def _parse_params(chunk: str) -> dict:
    return dict(parse_qsl(chunk, keep_blank_values=True))


def parse_token_input(raw: str) -> Optional[ParsedToken]:
    """Извлекает access_token из произвольного пользовательского ввода."""
    payload = (raw or "").strip()
    if not payload:
        return None

    # 1) access_token=... в фрагменте или query (полный redirect-URL или его кусок)
    if "access_token=" in payload:
        fragment = payload.split("#", maxsplit=1)[-1]
        params = _parse_params(fragment)
        token = params.get("access_token")
        if not token and "?" in payload:
            params = _parse_params(payload.split("?", maxsplit=1)[-1])
            token = params.get("access_token")

        if token:
            expires_in: Optional[int] = None
            expires_raw = params.get("expires_in")
            try:
                expires_in = int(expires_raw) if expires_raw is not None else None
            except (TypeError, ValueError):
                expires_in = None
            return ParsedToken(access_token=token, expires_in=expires_in)

    # 2) Голый токен где-то в тексте
    for pattern in (_YANDEX_TOKEN_RE, _LEGACY_TOKEN_RE):
        match = pattern.search(payload)
        if match:
            return ParsedToken(access_token=match.group(0))

    return None


def extract_access_token(raw: str) -> Optional[str]:
    """Совместимая обёртка над :func:`parse_token_input`."""
    parsed = parse_token_input(raw)
    return parsed.access_token if parsed else None
