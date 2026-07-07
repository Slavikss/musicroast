"""Telegram-бот MusicRoast: прожарка, батлы и виральный шеринг."""

from __future__ import annotations

import asyncio
import html
import logging
import os
from dataclasses import dataclass
from typing import Dict, Optional, Tuple
from urllib.parse import quote

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import CommandStart
from aiogram.filters.command import CommandObject
from aiogram.types import CallbackQuery, FSInputFile, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from fastapi import HTTPException

from app.config import TELEGRAM_BOT_USERNAME, YANDEX_OAUTH_URL
from app.models import PlaylistRequest
from app.prompts import LEVEL_TITLES, RoastLevel
from app.services import MusicRoastService
from app.services.battle import BattleError, BattleSide, battle_registry
from app.services.device_auth import (
    CANCELLED,
    EXPIRED,
    PENDING,
    READY,
    DeviceSession,
    device_auth_manager,
)
from app.services.roast_registry import RoastRecord, roast_registry
from app.streaming import StreamingProvider
from app.token_storage import token_storage
from app.utils import convert_markdown_to_html, parse_token_input, split_for_telegram

logger = logging.getLogger(__name__)

_LEVEL_ORDER = [RoastLevel.LIGHT, RoastLevel.MEDIUM, RoastLevel.CREMATION]


@dataclass
class LastRoast:
    """Снапшот последней прожарки юзера для кнопок «ещё раз»/«обложка»/батла."""

    roast_id: str
    text: str
    score: float
    diagnosis: str
    stats_block: str
    sample_lines: str
    playlist_kind: str
    playlist_title: str
    level: RoastLevel


def _esc(value: object) -> str:
    return html.escape(str(value))


def create_dispatcher(
    service: MusicRoastService, bot_username: Optional[str] = None
) -> Dispatcher:
    router = Router()

    last_roasts: Dict[int, LastRoast] = {}
    # user_id -> ("referral", roast_id) | ("battle", code)
    pending_intents: Dict[int, Tuple[str, str]] = {}

    def _deep_link(payload: str) -> str:
        username = bot_username or "musicroast_bot"
        return f"https://t.me/{username}?start={payload}"

    # ------------------------------------------------------------ keyboards

    def _menu_keyboard(has_token: bool):
        builder = InlineKeyboardBuilder()
        if has_token:
            builder.button(text="🔥 Прожарить", callback_data="roast")
            builder.button(text="⚔️ Батл с другом", callback_data="battle_new")
            builder.adjust(1)
        else:
            builder.button(text="🔑 Войти в Яндекс", callback_data="login")
            builder.adjust(1)
        return builder.as_markup()

    def _share_keyboard(record: LastRoast):
        deep_link = _deep_link(f"r_{record.roast_id}")
        share_text = (
            f"Меня прожарили: вкус {record.score}/10, диагноз — "
            f"«{record.diagnosis}» 💀 Проверь свой:"
        )
        share_url = (
            f"https://t.me/share/url?url={quote(deep_link, safe='')}"
            f"&text={quote(share_text, safe='')}"
        )
        builder = InlineKeyboardBuilder()
        builder.button(text="😈 Отправить друзьям", url=share_url)
        builder.button(text="⚔️ Вызвать на батл", callback_data="battle_new")
        builder.button(text="🖼 Обложка позора", callback_data="cover")
        builder.button(text="🔁 Ещё раз", callback_data="again")
        builder.adjust(1, 2, 1)
        return builder.as_markup()

    async def _get_token(user_id: int) -> Optional[str]:
        return await token_storage.get(str(user_id))

    # -------------------------------------------------------------- helpers

    async def _send_roast_text(message: Message, text: str) -> None:
        chunks = split_for_telegram(text)
        for chunk in chunks:
            formatted = convert_markdown_to_html(chunk)
            try:
                await message.answer(formatted, parse_mode="HTML")
            except Exception:  # noqa: BLE001 — HTML мог не собраться
                await message.answer(chunk)

    # ------------------------------------------------------------- handlers

    @router.message(CommandStart())
    async def on_start(message: Message, command: CommandObject) -> None:
        user = message.from_user
        if not user:
            return
        args = (command.args or "").strip()

        if args.startswith("r_"):
            await _handle_referral_link(message, user.id, args[2:])
            return
        if args.startswith("battle_"):
            await _handle_battle_link(message, user.id, args[7:])
            return

        has_token = bool(await _get_token(user.id))
        if has_token:
            await message.answer(
                "С возвращением! Токен на месте. Что делаем?",
                reply_markup=_menu_keyboard(True),
            )
            return

        await message.answer(
            "🔥 Привет! Я MusicRoast — прожарю твой музыкальный вкус по фактам: "
            "цифры, даты, имена. Больно будет по делу.\n\n"
            "Жми «🔑 Войти в Яндекс» — я дам код, ты подтвердишь вход на странице "
            "Яндекса, и всё. Никаких паролей мне присылать не надо.\n\n"
            f"<i>Запасной путь: открой <a href=\"{YANDEX_OAUTH_URL}\">эту ссылку</a>, "
            "авторизуйся и пришли сюда адрес страницы, куда тебя перекинет "
            "(или сам токен).</i>",
            reply_markup=_menu_keyboard(False),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )

    async def _handle_referral_link(
        message: Message, user_id: int, roast_id: str
    ) -> None:
        record = await roast_registry.get(roast_id)
        if not record:
            await message.answer(
                "Прожарка друга уже сгорела 🔥 Но твоя ещё впереди.",
                reply_markup=_menu_keyboard(bool(await _get_token(user_id))),
            )
            return

        if record.user_key == str(user_id):
            await _send_roast_text(message, record.text)
            return

        first_punch = record.text.strip().splitlines()[0] if record.text else ""
        teaser = (
            f"👀 <b>{_esc(record.display_name)}</b> получил прожарку:\n\n"
            f"Вкус: <b>{record.score}/10</b>\n"
            f"Диагноз: <i>{_esc(record.diagnosis)}</i>\n\n"
            f"«{_esc(first_punch)}»\n"
            f"▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒\n"
            f"▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒\n"
            f"▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒\n\n"
            f"Полная прожарка {_esc(record.display_name)} и вердикт, чей вкус "
            f"хуже — после твоей. Жарься 👇"
        )
        pending_intents[user_id] = ("referral", roast_id)
        await roast_registry.set_pending_referral(str(user_id), roast_id)

        has_token = bool(await _get_token(user_id))
        await message.answer(
            teaser, parse_mode="HTML", reply_markup=_menu_keyboard(has_token)
        )

    async def _handle_battle_link(message: Message, user_id: int, code: str) -> None:
        battle = await battle_registry.get(code)
        if not battle:
            await message.answer(
                "Батл не найден или уже сгорел. Создай свой!",
                reply_markup=_menu_keyboard(bool(await _get_token(user_id))),
            )
            return

        pending_intents[user_id] = ("battle", battle.code)
        has_token = bool(await _get_token(user_id))
        text = (
            f"⚔️ <b>{_esc(battle.initiator.display_name)}</b> вызывает тебя на "
            f"музыкальный батл!\n\nЕго вкус уже оценён. Чтобы принять вызов, "
            f"нужна твоя прожарка."
        )
        if has_token:
            text += "\n\nЖми «🔥 Прожарить» — потом сразу объявлю вердикт."
        else:
            text += "\n\nСначала войди в Яндекс 👇"
        await message.answer(
            text, parse_mode="HTML", reply_markup=_menu_keyboard(has_token)
        )

    # ------------------------------------------------------------ device auth

    @router.callback_query(lambda c: c.data == "login")
    async def on_login(callback: CallbackQuery) -> None:
        user = callback.from_user
        await callback.answer()
        try:
            session = await device_auth_manager.start(owner_key=str(user.id))
        except Exception:  # noqa: BLE001
            logger.exception("device auth start failed")
            await callback.message.answer(
                "Яндекс не выдал код входа, попробуй ещё раз чуть позже.\n"
                f"Или запасной путь: {YANDEX_OAUTH_URL}\n"
                "— авторизуйся и пришли сюда адрес страницы после редиректа."
            )
            return

        builder = InlineKeyboardBuilder()
        builder.button(text="🌐 Открыть Яндекс", url=session.verification_url)
        builder.button(text="❌ Отмена", callback_data=f"cancel_auth:{session.id}")
        builder.adjust(1)

        status_msg = await callback.message.answer(
            f"🔐 Твой код входа: <code>{_esc(session.user_code)}</code>\n\n"
            "1. Жми «🌐 Открыть Яндекс»\n"
            "2. Войди в аккаунт и введи этот код (тапни по коду — он скопируется)\n"
            "3. Возвращайся — я замечу сам 😉",
            parse_mode="HTML",
            reply_markup=builder.as_markup(),
        )

        asyncio.create_task(_watch_auth_session(session, status_msg, user.id))

    async def _watch_auth_session(
        session: DeviceSession, status_msg: Message, user_id: int
    ) -> None:
        while session.status == PENDING:
            await asyncio.sleep(3)

        if session.status in (EXPIRED, CANCELLED):
            text = (
                "⌛ Код протух, а ты так и не вошёл. Жми «🔑 Войти в Яндекс» ещё раз."
                if session.status == EXPIRED
                else "Вход отменён."
            )
            try:
                await status_msg.edit_text(text, reply_markup=_menu_keyboard(False))
            except Exception:  # noqa: BLE001
                pass
            return

        if session.status != READY or not session.tokens:
            return

        loop = asyncio.get_running_loop()
        try:
            account = await loop.run_in_executor(
                None,
                lambda: service.validate_token(
                    StreamingProvider.YANDEX, session.tokens.access_token
                ),
            )
        except HTTPException as exc:
            await status_msg.edit_text(f"Токен получен, но не подошёл: {exc.detail}")
            return

        await token_storage.set(
            str(user_id),
            session.tokens.access_token,
            ttl=session.tokens.expires_in,
            refresh_token=session.tokens.refresh_token,
        )

        name = account.get("display_name", "меломан")
        liked = account.get("liked_count", 0)
        await status_msg.edit_text(
            f"✅ Привет, <b>{_esc(name)}</b>! Вижу <b>{liked}</b> лайкнутых "
            f"треков. Уже есть что предъявить.",
            parse_mode="HTML",
            reply_markup=_menu_keyboard(True),
        )
        await _maybe_continue_intent(status_msg, user_id)

    @router.callback_query(lambda c: c.data and c.data.startswith("cancel_auth:"))
    async def on_cancel_auth(callback: CallbackQuery) -> None:
        await callback.answer()
        session_id = callback.data.split(":", 1)[1]
        await device_auth_manager.cancel(session_id)
        try:
            await callback.message.edit_text(
                "Вход отменён.", reply_markup=_menu_keyboard(False)
            )
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------ text input

    @router.message()
    async def on_text(message: Message) -> None:
        user = message.from_user
        if not user or not isinstance(message.text, str):
            return

        parsed = parse_token_input(message.text)
        if not parsed:
            await message.answer(
                "Не нашёл токен в сообщении. Жми «🔑 Войти в Яндекс» — так проще "
                "всего. Или пришли ссылку, куда тебя перекинуло после авторизации.",
                reply_markup=_menu_keyboard(bool(await _get_token(user.id))),
            )
            return

        status_msg = await message.answer("🔎 Проверяю токен...")
        loop = asyncio.get_running_loop()
        try:
            account = await loop.run_in_executor(
                None,
                lambda: service.validate_token(
                    StreamingProvider.YANDEX, parsed.access_token
                ),
            )
        except HTTPException as exc:
            await status_msg.edit_text(
                f"Токен не подошёл: {exc.detail}\nПопробуй «🔑 Войти в Яндекс».",
                reply_markup=_menu_keyboard(False),
            )
            return

        await token_storage.set(
            str(user.id), parsed.access_token, ttl=parsed.expires_in
        )
        name = account.get("display_name", "меломан")
        liked = account.get("liked_count", 0)
        await status_msg.edit_text(
            f"✅ Привет, <b>{_esc(name)}</b>! Вижу <b>{liked}</b> лайкнутых "
            f"треков. Готов к прожарке?",
            parse_mode="HTML",
            reply_markup=_menu_keyboard(True),
        )
        await _maybe_continue_intent(status_msg, user.id)

    async def _maybe_continue_intent(message: Message, user_id: int) -> None:
        intent = pending_intents.get(user_id)
        if not intent:
            return
        kind, _payload = intent
        hint = (
            "Дожарим тебя — и я покажу полную прожарку друга и вердикт."
            if kind == "referral"
            else "Осталось прожариться — и я объявлю вердикт батла."
        )
        builder = InlineKeyboardBuilder()
        builder.button(text="🔥 Погнали", callback_data="roast")
        await message.answer(hint, reply_markup=builder.as_markup())

    # ----------------------------------------------------------------- roast

    @router.callback_query(lambda c: c.data == "roast")
    async def on_roast(callback: CallbackQuery) -> None:
        user = callback.from_user
        token = await _get_token(user.id)
        if not token:
            await callback.answer("Сначала войди в Яндекс 🔑", show_alert=True)
            return
        await callback.answer()

        loop = asyncio.get_running_loop()
        builder = InlineKeyboardBuilder()
        playlists = []
        try:
            result = await loop.run_in_executor(
                None,
                lambda: service.list_playlists(
                    PlaylistRequest(
                        provider=StreamingProvider.YANDEX, access_token=token
                    )
                ),
            )
            playlists = result.get("playlists", [])
        except HTTPException as exc:
            if exc.status_code == 401:
                await token_storage.delete(str(user.id))
                await callback.message.answer(
                    "Токен протух 😢 Войди в Яндекс заново.",
                    reply_markup=_menu_keyboard(False),
                )
                return
            playlists = []
        except Exception:  # noqa: BLE001
            logger.exception("list_playlists failed")
            playlists = []

        if not playlists:
            builder.button(text="❤️ Мне нравится", callback_data="pl:liked")
        else:
            for playlist in playlists[:9]:
                count = playlist.get("track_count") or 0
                if not count:
                    continue
                title = playlist.get("title", "Без названия")
                kind = playlist.get("kind", "liked")
                label = f"❤️ {title} ({count})" if playlist.get("is_liked") else f"💿 {title} ({count})"
                builder.button(text=label[:60], callback_data=f"pl:{kind}")
        builder.adjust(1)
        await callback.message.answer(
            "Что жарим?", reply_markup=builder.as_markup()
        )

    @router.callback_query(lambda c: c.data and c.data.startswith("pl:"))
    async def on_playlist_chosen(callback: CallbackQuery) -> None:
        await callback.answer()
        kind = callback.data.split(":", 1)[1]
        builder = InlineKeyboardBuilder()
        for level in _LEVEL_ORDER:
            builder.button(
                text=LEVEL_TITLES[level], callback_data=f"lvl:{kind}:{level.value}"
            )
        builder.adjust(1)
        await callback.message.edit_text(
            "Насколько больно?", reply_markup=builder.as_markup()
        )

    @router.callback_query(lambda c: c.data and c.data.startswith("lvl:"))
    async def on_level_chosen(callback: CallbackQuery) -> None:
        user = callback.from_user
        await callback.answer()
        _, kind, level_raw = callback.data.split(":", 2)
        try:
            level = RoastLevel(level_raw)
        except ValueError:
            level = RoastLevel.MEDIUM

        token = await _get_token(user.id)
        if not token:
            await callback.message.answer(
                "Токен протух 😢 Войди в Яндекс заново.",
                reply_markup=_menu_keyboard(False),
            )
            return

        display_name = user.first_name or user.username or "боец"
        try:
            await callback.message.edit_text("📀 Читаю плейлист...")
            status_msg = callback.message
        except Exception:  # noqa: BLE001
            status_msg = await callback.message.answer("📀 Читаю плейлист...")

        await _run_roast_flow(status_msg, user.id, display_name, token, kind, level)

    async def _run_roast_flow(
        status_msg: Message,
        user_id: int,
        display_name: str,
        token: str,
        playlist_kind: str,
        level: RoastLevel,
    ) -> None:
        loop = asyncio.get_running_loop()

        # Этап 1: библиотека + статистика
        try:
            prepared = await loop.run_in_executor(
                None,
                lambda: service.prepare_library(
                    StreamingProvider.YANDEX, token, playlist_kind=playlist_kind
                ),
            )
        except HTTPException as exc:
            if exc.status_code == 401:
                await token_storage.delete(str(user_id))
                await status_msg.edit_text(
                    "Токен протух 😢 Войди в Яндекс заново.",
                    reply_markup=_menu_keyboard(False),
                )
            else:
                await status_msg.edit_text(f"Не вышло: {exc.detail}")
            return
        except Exception:  # noqa: BLE001
            logger.exception("prepare_library failed")
            await status_msg.edit_text("Не смог прочитать плейлист. Попробуй позже.")
            return

        total = prepared.metadata.get("track_count", len(prepared.tracks))
        tease = f"🧮 Посчитал: {total} треков"
        if prepared.top_artist:
            tease += f", топ — {prepared.top_artist}"
        tease += ". Гемини уже ржёт, набирает текст..."
        try:
            await status_msg.edit_text(tease)
        except Exception:  # noqa: BLE001
            pass

        # Этап 2: прожарка
        try:
            outcome = await loop.run_in_executor(
                None, lambda: service.roast_library(prepared, level=level)
            )
        except HTTPException as exc:
            await status_msg.edit_text(f"🚫 {exc.detail}")
            return
        except Exception:  # noqa: BLE001
            logger.exception("roast_library failed")
            await status_msg.edit_text("Гемини подавился. Попробуй ещё раз.")
            return

        record = RoastRecord(
            roast_id="",
            user_key=str(user_id),
            display_name=display_name,
            text=outcome.text,
            score=outcome.score,
            diagnosis=outcome.diagnosis,
            shame_facts=outcome.shame_facts,
            stats_block=prepared.stats_block,
            sample_lines=prepared.sample_lines(),
            playlist_title=prepared.metadata.get("title", ""),
            level=level.value,
        )
        roast_id = await roast_registry.create(record)

        last = LastRoast(
            roast_id=roast_id,
            text=outcome.text,
            score=outcome.score,
            diagnosis=outcome.diagnosis,
            stats_block=prepared.stats_block,
            sample_lines=prepared.sample_lines(),
            playlist_kind=playlist_kind,
            playlist_title=prepared.metadata.get("title", ""),
            level=level,
        )
        last_roasts[user_id] = last

        try:
            await status_msg.delete()
        except Exception:  # noqa: BLE001
            pass
        await _send_roast_text(status_msg, outcome.text)

        # Шеринг-карточка отдельным форвардабельным сообщением
        card_lines = [
            f"🔥 <b>ПРОЖАРКА: {_esc(display_name)}</b>",
            f"Уровень: {_esc(LEVEL_TITLES[level])}",
            f"Вкус: <b>{outcome.score}/10</b>",
            f"Диагноз: <i>{_esc(outcome.diagnosis)}</i>",
        ]
        if outcome.shame_facts:
            card_lines.append("")
            card_lines.append("Топ позора:")
            card_lines.extend(f"• {_esc(fact)}" for fact in outcome.shame_facts)
        await status_msg.answer(
            "\n".join(card_lines),
            parse_mode="HTML",
            reply_markup=_share_keyboard(last),
        )

        await _process_intent_after_roast(status_msg, user_id, display_name, last)

    # --------------------------------------------------- intents after roast

    async def _process_intent_after_roast(
        message: Message, user_id: int, display_name: str, last: LastRoast
    ) -> None:
        intent = pending_intents.pop(user_id, None)
        if not intent:
            return
        kind, payload = intent

        if kind == "referral":
            friend = await roast_registry.pop_pending_referral(str(user_id))
            if not friend:
                friend = await roast_registry.get(payload)
            if not friend:
                return
            await message.answer(
                f"🤝 Как и обещал — полная прожарка "
                f"<b>{_esc(friend.display_name)}</b>:",
                parse_mode="HTML",
            )
            await _send_roast_text(message, friend.text)
            await _auto_battle(message, user_id, display_name, last, friend)
            return

        if kind == "battle":
            await _join_battle(message, user_id, display_name, last, payload)

    async def _auto_battle(
        message: Message,
        user_id: int,
        display_name: str,
        last: LastRoast,
        friend: RoastRecord,
    ) -> None:
        loop = asyncio.get_running_loop()
        status = await message.answer("⚖️ Судья Гемини сравнивает ваши библиотеки...")

        my_side = BattleSide(
            display_name=display_name,
            score=last.score,
            diagnosis=last.diagnosis,
            stats_block=last.stats_block,
            sample_lines=last.sample_lines,
        )
        friend_side = BattleSide(
            display_name=friend.display_name,
            score=friend.score,
            diagnosis=friend.diagnosis,
            stats_block=friend.stats_block,
            sample_lines=friend.sample_lines,
        )

        try:
            verdict = await loop.run_in_executor(
                None, lambda: service.generate_battle(friend_side, my_side)
            )
        except Exception:  # noqa: BLE001
            logger.exception("auto battle failed")
            await status.edit_text("Судья не смог определиться. Бывает.")
            return

        text = f"{verdict['header']}\n\n{verdict['text']}"
        try:
            await status.delete()
        except Exception:  # noqa: BLE001
            pass
        await _send_roast_text(message, text)

        # Уведомляем инициатора, если он из Telegram
        friend_key = friend.user_key
        if friend_key.isdigit():
            try:
                await message.bot.send_message(
                    int(friend_key),
                    f"😈 <b>{_esc(display_name)}</b> прожарился по твоей ссылке! "
                    f"Вердикт судьи:",
                    parse_mode="HTML",
                )
                for chunk in split_for_telegram(text):
                    await message.bot.send_message(
                        int(friend_key),
                        convert_markdown_to_html(chunk),
                        parse_mode="HTML",
                    )
            except Exception:  # noqa: BLE001
                logger.warning("failed to notify referral initiator", exc_info=True)

    async def _join_battle(
        message: Message,
        user_id: int,
        display_name: str,
        last: LastRoast,
        code: str,
    ) -> None:
        loop = asyncio.get_running_loop()
        side = BattleSide(
            display_name=display_name,
            score=last.score,
            diagnosis=last.diagnosis,
            stats_block=last.stats_block,
            sample_lines=last.sample_lines,
            chat_id=user_id,
        )
        try:
            battle = await battle_registry.join(code, side)
        except BattleError as exc:
            await message.answer(f"⚔️ {exc.message}")
            return

        status = await message.answer(
            "⚔️ Оба бойца на ринге, судья Гемини совещается..."
        )
        try:
            verdict = await loop.run_in_executor(
                None,
                lambda: service.generate_battle(battle.initiator, battle.opponent),
            )
        except Exception:  # noqa: BLE001
            logger.exception("battle verdict failed")
            await status.edit_text("Судья не смог определиться. Бывает.")
            return

        text = f"{verdict['header']}\n\n{verdict['text']}"
        await battle_registry.set_result(battle.code, text, verdict["winner"])

        try:
            await status.delete()
        except Exception:  # noqa: BLE001
            pass
        await _send_roast_text(message, text)

        initiator_chat = battle.initiator.chat_id
        if initiator_chat and initiator_chat != user_id:
            try:
                await message.bot.send_message(
                    initiator_chat,
                    f"⚔️ <b>{_esc(display_name)}</b> принял твой вызов! Вердикт:",
                    parse_mode="HTML",
                )
                for chunk in split_for_telegram(text):
                    await message.bot.send_message(
                        initiator_chat,
                        convert_markdown_to_html(chunk),
                        parse_mode="HTML",
                    )
            except Exception:  # noqa: BLE001
                logger.warning("failed to notify battle initiator", exc_info=True)

    # --------------------------------------------------------- post-roast UX

    @router.callback_query(lambda c: c.data == "again")
    async def on_again(callback: CallbackQuery) -> None:
        user = callback.from_user
        last = last_roasts.get(user.id)
        if not last:
            await callback.answer("Сначала прожарься 🔥", show_alert=True)
            return
        token = await _get_token(user.id)
        if not token:
            await callback.answer("Токен протух, войди заново 🔑", show_alert=True)
            return
        await callback.answer()
        status_msg = await callback.message.answer("📀 Читаю плейлист заново...")
        display_name = user.first_name or user.username or "боец"
        await _run_roast_flow(
            status_msg, user.id, display_name, token, last.playlist_kind, last.level
        )

    @router.callback_query(lambda c: c.data == "cover")
    async def on_cover(callback: CallbackQuery) -> None:
        user = callback.from_user
        last = last_roasts.get(user.id)
        if not last:
            await callback.answer("Сначала прожарься 🔥", show_alert=True)
            return
        await callback.answer()
        status_msg = await callback.message.answer(
            "🎨 Рисую обложку позора... это займёт полминуты"
        )
        loop = asyncio.get_running_loop()
        try:
            image_data = await loop.run_in_executor(
                None, lambda: service.roaster.generate_image(last.text)
            )
        except HTTPException as exc:
            await status_msg.edit_text(f"Художник забастовал: {exc.detail}")
            return
        except Exception:  # noqa: BLE001
            logger.exception("cover generation failed")
            await status_msg.edit_text("Художник забастовал. Попробуй позже.")
            return

        caption = f"🖼 Обложка позора · вкус {last.score}/10 · {last.diagnosis}"
        try:
            await status_msg.delete()
        except Exception:  # noqa: BLE001
            pass
        await callback.message.answer_photo(
            FSInputFile(image_data["image_path"]), caption=caption[:1024]
        )

    # ---------------------------------------------------------------- battle

    @router.callback_query(lambda c: c.data == "battle_new")
    async def on_battle_new(callback: CallbackQuery) -> None:
        user = callback.from_user
        last = last_roasts.get(user.id)
        if not last:
            await callback.answer(
                "Сначала прожарься — батл идёт по результатам 🔥", show_alert=True
            )
            return
        await callback.answer()

        display_name = user.first_name or user.username or "боец"
        side = BattleSide(
            display_name=display_name,
            score=last.score,
            diagnosis=last.diagnosis,
            stats_block=last.stats_block,
            sample_lines=last.sample_lines,
            chat_id=user.id,
        )
        battle = await battle_registry.create(side, last.level.value)
        link = _deep_link(f"battle_{battle.code}")
        share_text = (
            f"⚔️ Вызываю тебя на музыкальный батл! Мой вкус уже оценили на "
            f"{last.score}/10. Слабо?"
        )
        share_url = (
            f"https://t.me/share/url?url={quote(link, safe='')}"
            f"&text={quote(share_text, safe='')}"
        )
        builder = InlineKeyboardBuilder()
        builder.button(text="📨 Отправить вызов", url=share_url)
        builder.adjust(1)
        await callback.message.answer(
            f"⚔️ Батл создан!\n\nКод: <code>{battle.code}</code>\n"
            f"Ссылка для соперника:\n{link}\n\n"
            f"Как только друг прожарится — я объявлю вердикт обоим. "
            f"Вызов живёт час.",
            parse_mode="HTML",
            reply_markup=builder.as_markup(),
        )

    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    return dispatcher


async def run_bot(service: Optional[MusicRoastService] = None) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN не установлен")

    if service is None:
        service = MusicRoastService()

    bot = Bot(token=token)
    username = TELEGRAM_BOT_USERNAME
    if not username:
        me = await bot.get_me()
        username = me.username

    dispatcher = create_dispatcher(service, bot_username=username)
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(run_bot())
