from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class StoredTokenResponse(BaseModel):
    """Ответ с сохранённым токеном."""

    access_token: str = Field(..., description="Полученный access_token Yandex Music")
    expires_in: Optional[int] = Field(
        default=None,
        description="Время жизни токена в секундах, если удалось определить",
    )


class AccountSummary(BaseModel):
    """Краткая информация об аккаунте стриминга после валидации токена."""

    uid: Optional[str] = None
    display_name: str = "меломан"
    liked_count: int = 0


class TokenValidationRequest(BaseModel):
    """Сырое сообщение пользователя: URL после редиректа, фрагмент или голый токен."""

    provider: str = Field(default="yandex")
    raw_input: str = Field(..., min_length=1, max_length=8192, repr=False)


class TokenValidationResponse(BaseModel):
    access_token: str = Field(..., repr=False)
    expires_in: Optional[int] = None
    account: AccountSummary


class DeviceAuthStartResponse(BaseModel):
    """Ответ на старт OAuth Device Flow."""

    session_id: str
    verification_url: str
    user_code: str
    expires_in: int
    interval: int


class DeviceAuthStatusResponse(BaseModel):
    """Статус device-auth сессии."""

    status: str = Field(..., description="pending | ready | expired | cancelled")
    access_token: Optional[str] = Field(default=None, repr=False)
    expires_in: Optional[int] = None
    account: Optional[AccountSummary] = None
