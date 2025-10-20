from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class YandexOAuthRequest(BaseModel):
    """Параметры для получения токена Яндекс Музыки через Mini App."""

    username: str = Field(..., min_length=3, max_length=255, description="Логин Яндекс ID")
    password: str = Field(..., min_length=3, max_length=255, description="Пароль Яндекс ID")
    telegram_user_id: int = Field(..., description="Идентификатор пользователя Telegram, инициировавшего запрос")
    otp: Optional[str] = Field(default=None, description="Код подтверждения (если требуется)")
    headless: Optional[bool] = Field(
        default=None,
        description="Переключение headless-режима браузера. По умолчанию берётся из конфигурации",
    )


class StoredTokenResponse(BaseModel):
    """Ответ с сохранённым токеном."""

    access_token: str = Field(..., description="Полученный access_token Yandex Music")
    expires_in: Optional[int] = Field(
        default=None,
        description="Время жизни токена в секундах, если удалось определить",
    )


class YandexInteractiveSessionRequest(BaseModel):
    """Запрос на создание интерактивной сессии авторизации."""

    telegram_user_id: int = Field(..., description="Идентификатор пользователя Telegram")


class YandexInteractiveSessionResponse(BaseModel):
    """Ответ при создании интерактивной сессии."""

    session_id: str = Field(..., description="Уникальный идентификатор сессии")
    viewport_width: int = Field(..., description="Ширина виртуального окна браузера")
    viewport_height: int = Field(..., description="Высота виртуального окна браузера")
