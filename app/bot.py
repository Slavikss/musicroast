"""Asynchronous Telegram bot wrapper for MusicRoast."""

from __future__ import annotations
import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import Dict, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx
from aiogram import Bot, Dispatcher, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.config import (
    BACKEND_API_BASE_URL,
    YANDEX_MINIAPP_URL,
    YANDEX_OAUTH_URL,
)
from app.models import RoastRequest
from app.services import MusicRoastService
from app.streaming import StreamingProvider
from app.utils import convert_markdown_to_html, extract_access_token


OAUTH_URL = YANDEX_OAUTH_URL
_backend_base = BACKEND_API_BASE_URL.rstrip("/") if BACKEND_API_BASE_URL else None
MINIAPP_BASE_URL = (
    YANDEX_MINIAPP_URL.rstrip("/")
    if YANDEX_MINIAPP_URL
    else (f"{_backend_base}/miniapp/yandex" if _backend_base else None)
)
TOKEN_ENDPOINT_BASE = _backend_base


@dataclass
class UserSession:
    """Stores token data for a user."""

    access_token: str


class SessionStorage:
    """Thread-safe storage for user sessions."""

    def __init__(self) -> None:
        self._sessions: Dict[int, UserSession] = {}
        self._lock = asyncio.Lock()

    async def set_token(self, user_id: int, token: str) -> None:
        async with self._lock:
            self._sessions[user_id] = UserSession(access_token=token)

    async def get_token(self, user_id: int) -> Optional[str]:
        async with self._lock:
            session = self._sessions.get(user_id)
            if not session:
                return None
            return session.access_token


def _miniapp_url_for_user(user_id: int) -> Optional[str]:
    if not MINIAPP_BASE_URL:
        return None
    parsed = urlparse(MINIAPP_BASE_URL)
    query_params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query_params["tg_id"] = str(user_id)
    new_query = urlencode(query_params)
    updated = parsed._replace(query=new_query)
    return urlunparse(updated)


def _main_keyboard(user_id: Optional[int] = None):
    builder = InlineKeyboardBuilder()
    miniapp_url = _miniapp_url_for_user(user_id) if user_id else MINIAPP_BASE_URL
    if miniapp_url:
        builder.button(text="🔑 Получить токен", web_app=WebAppInfo(url=miniapp_url))
    builder.button(text="🔥 Прожарить", callback_data="roast")
    builder.adjust(1, 1)
    return builder.as_markup()


async def _fetch_token_from_backend(user_id: int) -> Optional[str]:
    if not TOKEN_ENDPOINT_BASE:
        return None

    url = f"{TOKEN_ENDPOINT_BASE}/auth/yandex/token/{user_id}"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url)
    except httpx.HTTPError as exc:
        logging.error("Failed to query backend for token: %s", exc)
        return None

    if response.status_code == 200:
        try:
            data = response.json()
        except ValueError:
            logging.error("Backend returned malformed JSON for token request")
            return None
        token = extract_access_token(data.get("access_token", ""))
        return token

    if response.status_code not in (401, 404):
        logging.warning(
            "Unexpected backend token status %s for user %s",
            response.status_code,
            user_id,
        )
    return None


def create_dispatcher(service: Optional[MusicRoastService] = None) -> Dispatcher:
    if service is None:
        service = MusicRoastService()

    router = Router()
    sessions = SessionStorage()

    @router.message(CommandStart())
    async def on_start(message: Message) -> None:
        user = message.from_user
        user_id = user.id if user else None
        parts = [
            "🔥 Привет! Я MusicRoast бот. Готов прожарить твой музыкальный вкус.",
        ]
        if MINIAPP_BASE_URL:
            parts.append(
                "1. Нажми «🔑 Получить токен» и авторизуйся в Яндекс Музыке."
                "\n2. Вернись в чат и жми «🔥 Прожарить»."
            )
            parts.append(
                "Если кнопка недоступна, токен можно получить вручную по ссылке:"
                f"\n{OAUTH_URL}"
            )
        else:
            parts.append(
                "Mini App временно недоступен. Получи access_token по ссылке и пришли его одним сообщением:"
                f"\n{OAUTH_URL}"
            )

        await message.answer("\n\n".join(parts), reply_markup=_main_keyboard(user_id))

    @router.message(lambda msg: getattr(msg, "web_app_data", None) is not None)
    async def on_web_app_data(message: Message) -> None:
        user = message.from_user
        if not user:
            await message.answer("Не удалось определить пользователя.")
            return

        raw_payload = message.web_app_data.data if message.web_app_data else ""
        try:
            payload = json.loads(raw_payload) if raw_payload else {}
        except (TypeError, json.JSONDecodeError):
            payload = {}

        token_value = payload.get("access_token")
        token = extract_access_token(token_value) if token_value else None
        if not token:
            await message.answer(
                "Не удалось получить токен через Mini App. Попробуй ещё раз.",
                reply_markup=_main_keyboard(user.id),
            )
            return

        await sessions.set_token(user.id, token)
        await message.answer(
            "Токен получен автоматически! Теперь можно прожарить плейлист.",
            reply_markup=_main_keyboard(user.id),
        )

    @router.message()
    async def on_token(message: Message) -> None:
        if isinstance(message.text, str):
            token = extract_access_token(message.text)
        else:
            token = None

        if not token:
            advice = (
                "Не нашёл токен. Нажми «🔑 Получить токен» и авторизуйся."
                if MINIAPP_BASE_URL
                else "Не нашёл токен в сообщении. Пришли access_token."
            )
            await message.answer(
                advice,
                reply_markup=_main_keyboard(
                    message.from_user.id if message.from_user else None
                ),
            )
            return

        user = message.from_user
        if not user:
            await message.answer("Не удалось определить пользователя.")
            return

        await sessions.set_token(user.id, token)
        await message.answer(
            "Токен принят! Жми кнопку, когда будешь готов к прожарке!",
            reply_markup=_main_keyboard(user.id),
        )

    @router.callback_query(lambda c: c.data == "roast")
    async def on_roast(callback: CallbackQuery) -> None:
        user = callback.from_user
        if not user:
            await callback.answer(
                "Не удалось определить пользователя.", show_alert=True
            )
            return

        token = await sessions.get_token(user.id)
        if not token:
            backend_token = await _fetch_token_from_backend(user.id)
            if backend_token:
                token = backend_token
                await sessions.set_token(user.id, token)

        if not token:
            missing_text = (
                "Сначала авторизуйся через «🔑 Получить токен»."
                if MINIAPP_BASE_URL
                else "Сначала пришли access_token."
            )
            await callback.answer(missing_text, show_alert=True)
            return

        await callback.answer()
        status_message = await callback.message.answer("Готовлю прожарку...")

        request = RoastRequest(
            provider=StreamingProvider.YANDEX,
            access_token=token,
            owner_id="me",
            playlist_kind="liked",
            prompt_version=None,
            generate_image=False,
        )

        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(None, service.generate_roast, request)
        except Exception as exc:  # noqa: BLE001
            logging.exception("Failed to generate roast: %s", exc)
            await status_message.edit_text(
                "Не удалось получить прожарку. Попробуй позже."
            )
            return

        playlist_title = result.get("playlist", {}).get("title", "Плейлист")
        roast_text = result.get("roast", "Похоже, текста прожарки нет.")

        formatted_roast = convert_markdown_to_html(roast_text)

        try:
            await status_message.edit_text(
                formatted_roast,
                reply_markup=_main_keyboard(user.id),
                parse_mode="HTML",
            )
        except Exception as e:
            logging.error("Failed to send formatted message: %s", e)
            # Если форматирование не удалось, отправляем без форматирования
            await status_message.edit_text(
                f"{playlist_title}\n\n{roast_text}",
                reply_markup=_main_keyboard(user.id),
            )

    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    return dispatcher


async def run_bot() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN не установлен")

    dispatcher = create_dispatcher()
    bot = Bot(token=token)
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(run_bot())
