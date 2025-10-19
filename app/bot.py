"""Asynchronous Telegram bot wrapper for MusicRoast."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass
from typing import Dict, Optional
from urllib.parse import parse_qsl

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message

from app.models import RoastRequest
from app.services import MusicRoastService
from app.streaming import StreamingProvider


OAUTH_URL = (
    "https://oauth.yandex.ru/authorize"
    "?response_type=token&client_id=1a6990aa636648e9b2ef855fa7bec2fb"
)


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




def _extract_access_token(raw: str) -> Optional[str]:
    payload = raw.strip()
    if not payload:
        return None

    if "access_token=" not in payload:
        return payload

    fragment = payload.split("#", maxsplit=1)[-1]
    params = dict(parse_qsl(fragment, keep_blank_values=True))
    token = params.get("access_token")
    return token or payload


def create_dispatcher(service: Optional[MusicRoastService] = None) -> Dispatcher:
    if service is None:
        service = MusicRoastService()

    router = Router()
    sessions = SessionStorage()

    @router.message(CommandStart())
    async def on_start(message: Message) -> None:
        greeting = (
            "🔥 Привет! Я MusicRoast бот. Готов прожарить твой музыкальный вкус."
            "\n\n"
            "1. Перейди по ссылке и авторизуйся в Яндекс Музыке."
            "\n2. Скопируй access_token из адресной строки."
            "\n3. Отправь его мне одним сообщением."
            f"\n\nСсылка: {OAUTH_URL}"
        )
        await message.answer(greeting)

    @router.message()
    async def on_token(message: Message) -> None:
        if isinstance(message.text, str):
            token = _extract_access_token(message.text)
        else:
            token = None

        if not token:
            await message.answer(
                "Не нашёл токен в сообщении. Пришли доступ в формате access_token."
            )
            return

        user = message.from_user
        if not user:
            await message.answer(
                "Не удалось определить пользователя."
            )
            return

        await sessions.set_token(user.id, token)
        await message.answer(
            "Токен принят! Жми кнопку, когда будешь готов к прожарке!",
            reply_markup=_roast_keyboard()
        )

    @router.callback_query(lambda c: c.data == "roast")
    async def on_roast(callback: CallbackQuery) -> None:
        user = callback.from_user
        if not user:
            await callback.answer("Не удалось определить пользователя.", show_alert=True)
            return

        token = await sessions.get_token(user.id)
        if not token:
            await callback.answer("Сначала пришли access_token.", show_alert=True)
            return

        await callback.answer()
        status_message = await callback.message.answer(
            "Готовлю прожарку..."
        )

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

        try:
            await status_message.edit_text(roast_text)
        except Exception as e:
            logging.error("Failed to send formatted message: %s", e)
            # Если форматирование не удалось, отправляем без форматирования
            await status_message.edit_text(
                f"{playlist_title}\n\n{roast_text}"
            )

    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    return dispatcher


def _roast_keyboard():
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    builder = InlineKeyboardBuilder()
    builder.button(text="🔥 Прожарить", callback_data="roast")
    return builder.as_markup()


async def run_bot() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN не установлен")

    dispatcher = create_dispatcher()
    bot = Bot(token=token)
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(run_bot())
