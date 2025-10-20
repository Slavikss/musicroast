"""Helpers for working with OAuth-style URLs and fragments."""

from __future__ import annotations

from typing import Optional, Tuple
from urllib.parse import parse_qsl


def extract_access_token(raw: str) -> Optional[str]:
    """Extract ``access_token`` value from a raw message or URL fragment."""
    payload = (raw or "").strip()
    if not payload:
        return None

    if "access_token=" not in payload:
        return payload

    fragment = payload.split("#", maxsplit=1)[-1]
    params = dict(parse_qsl(fragment, keep_blank_values=True))
    token = params.get("access_token")
    return token or payload


def parse_token_fragment(url: str) -> Tuple[Optional[str], Optional[int]]:
    """Parse ``access_token`` and ``expires_in`` values from an OAuth redirect URL."""
    fragment = (url or "").split("#", maxsplit=1)[-1]
    params = dict(parse_qsl(fragment, keep_blank_values=True))
    token = params.get("access_token")
    expires_raw = params.get("expires_in")

    try:
        expires_in = int(expires_raw) if expires_raw is not None else None
    except (TypeError, ValueError):
        expires_in = None

    return token, expires_in

