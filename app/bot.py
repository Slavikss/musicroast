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
from aiogram.filters import Command, CommandStart
from aiogram.filters.command import CommandObject
from aiogram.types import BotCommand, CallbackQuery, FSInputFile, Message
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
from app.services.diagnosis import evidence_lines
from app.services.diagnosis_registry import (
    DiagnosisRecord,
    diagnosis_registry,
    taste_diff,
)
from app.services.roast_registry import RoastRecord, roast_registry
from app.streaming import StreamingProvider
from app.token_storage import token_storage
from app.utils import convert_markdown_to_html, parse_token_input, split_for_telegram

logger = logging.getLogger(__name__)

_LEVEL_ORDER = [RoastLevel.LIGHT, RoastLevel.MEDIUM, RoastLevel.CREMATION]

HELP_TEXT = (
    "🩺 <b>Я — музыкальный доктор.</b> Кладу твой плейлист на стол и ставлю "
    "диагноз по фактам: сколько лет ты слушаешь одно и то же, где залип и чем "
    "позоришься. Без анестезии.\n\n"
    "<b>Как проходит приём:</b>\n"
    "1. «🔑 Войти в Яндекс» — открой мне свою карту (лайки и плейлисты).\n"
    "2. «🔥 Прожарить» — выбери плейлист и уровень боли, я проведу осмотр.\n"
    "3. «⚔️ Батл с другом» — сверим карты, чей вкус безнадёжнее.\n"
    "4. «🖼 Обложка позора» — портрет твоего диагноза.\n\n"
    "/start — регистратура · /help — этот листок\n"
    "<i>Диагноз — не приговор… хотя в твоём случае возможны варианты.</i>"
)


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
    diagnosis_name: str = ""
    severity: int = 0
    prescription: str = ""


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
        if record.diagnosis_name:
            share_text = (
                f"Мне поставили «{record.diagnosis_name}», стадия "
                f"{record.severity}/10 💀 Проверь свой диагноз:"
            )
        else:
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
                "🩺 О, снова ты. Карта на месте — ложись, посмотрим, что там "
                "выросло с прошлого раза.",
                reply_markup=_menu_keyboard(True),
            )
            return

        await message.answer(
            "🩺 Здравствуй, пациент. Я — музыкальный доктор. Открывай карту: "
            "положу твой плейлист на стол и поставлю диагноз по фактам — цифры, "
            "даты, имена. Будет больно, но честно.\n\n"
            "Жми «🔑 Войти в Яндекс» — выдам талончик (код), подтвердишь вход на "
            "странице Яндекса, и всё. Пароли мне не неси, я не регистратура.\n\n"
            f"<i>Запасной путь: открой <a href=\"{YANDEX_OAUTH_URL}\">эту ссылку</a>, "
            "авторизуйся и пришли сюда адрес страницы, куда тебя перекинет "
            "(или сам токен).</i>",
            reply_markup=_menu_keyboard(False),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )

    @router.message(Command("help"))
    async def on_help(message: Message) -> None:
        user = message.from_user
        has_token = bool(await _get_token(user.id)) if user else False
        await message.answer(
            HELP_TEXT,
            parse_mode="HTML",
            reply_markup=_menu_keyboard(has_token),
            disable_web_page_preview=True,
        )

    async def _handle_referral_link(
        message: Message, user_id: int, roast_id: str
    ) -> None:
        record = await roast_registry.get(roast_id)
        if not record:
            await message.answer(
                "Карта твоего друга уже в архиве 🔥 Но твоя — свежачок, идём на осмотр.",
                reply_markup=_menu_keyboard(bool(await _get_token(user_id))),
            )
            return

        if record.user_key == str(user_id):
            await _send_roast_text(message, record.text)
            return

        first_punch = record.text.strip().splitlines()[0] if record.text else ""
        diagnosis_line = (
            f"🩺 Диагноз: <b>{_esc(record.diagnosis_name)}</b> "
            f"(стадия {record.severity}/10)\n"
            if record.diagnosis_name
            else ""
        )
        teaser = (
            f"👀 <b>{_esc(record.display_name)}</b> получил приём у музыкального доктора:\n\n"
            f"Вкус: <b>{record.score}/10</b>\n"
            f"{diagnosis_line}"
            f"Приговор: <i>{_esc(record.diagnosis)}</i>\n\n"
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
            f"музыкальный батл!\n\nЕго карту уже осмотрели. Чтобы принять вызов, "
            f"нужен твой осмотр."
        )
        if has_token:
            text += "\n\nЖми «🔥 Прожарить» — потом сразу объявлю вердикт консилиума."
        else:
            text += "\n\nСначала открой карту 👇"
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
                "Регистратура Яндекса не выдала талончик, попробуй ещё раз чуть позже.\n"
                f"Или запасной путь: {YANDEX_OAUTH_URL}\n"
                "— авторизуйся и пришли сюда адрес страницы после редиректа."
            )
            return

        builder = InlineKeyboardBuilder()
        builder.button(text="🌐 Открыть Яндекс", url=session.verification_url)
        builder.button(text="❌ Отмена", callback_data=f"cancel_auth:{session.id}")
        builder.adjust(1)

        status_msg = await callback.message.answer(
            f"🔐 Держи талончик: <code>{_esc(session.user_code)}</code>\n\n"
            "1. Жми «🌐 Открыть Яндекс»\n"
            "2. Войди в аккаунт и введи этот номер (тапни — он скопируется)\n"
            "3. Возвращайся в кабинет — я замечу сам 😉",
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
                "⌛ Талончик просрочен, а ты так и не дошёл до кабинета. "
                "Жми «🔑 Войти в Яндекс» ещё раз."
                if session.status == EXPIRED
                else "Приём отменён."
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
            await status_msg.edit_text(f"Талончик получил, но карта не открылась: {exc.detail}")
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
            f"✅ Карта открыта, <b>{_esc(name)}</b>. Вижу <b>{liked}</b> лайков — "
            f"есть на что посмотреть. На осмотр?",
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
                "Приём отменён.", reply_markup=_menu_keyboard(False)
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
                "Токена в этом сообщении не нашёл, пациент. Проще всего — «🔑 Войти "
                "в Яндекс». Или пришли ссылку, куда тебя перекинуло после авторизации.",
                reply_markup=_menu_keyboard(bool(await _get_token(user.id))),
            )
            return

        status_msg = await message.answer("🔎 Открываю твою карту...")
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
                f"Карта не открылась: {exc.detail}\nДавай через «🔑 Войти в Яндекс».",
                reply_markup=_menu_keyboard(False),
            )
            return

        await token_storage.set(
            str(user.id), parsed.access_token, ttl=parsed.expires_in
        )
        name = account.get("display_name", "меломан")
        liked = account.get("liked_count", 0)
        await status_msg.edit_text(
            f"✅ Открыл твою карту, <b>{_esc(name)}</b>: <b>{liked}</b> лайков. "
            f"Ну что, приступим к осмотру?",
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
            "Сначала осмотрю тебя — потом покажу карту друга и кто из вас безнадёжнее."
            if kind == "referral"
            else "Осталось пройти осмотр — и объявлю вердикт батла."
        )
        builder = InlineKeyboardBuilder()
        builder.button(text="🔥 На осмотр", callback_data="roast")
        await message.answer(hint, reply_markup=builder.as_markup())

    # ----------------------------------------------------------------- roast

    @router.callback_query(lambda c: c.data == "roast")
    async def on_roast(callback: CallbackQuery) -> None:
        user = callback.from_user
        token = await _get_token(user.id)
        if not token:
            await callback.answer("Сначала открой карту — «🔑 Войти в Яндекс»", show_alert=True)
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
                    "Карта устарела 😢 Войди в Яндекс заново.",
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
            "Что кладём на стол?", reply_markup=builder.as_markup()
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
                "Карта устарела 😢 Войди в Яндекс заново.",
                reply_markup=_menu_keyboard(False),
            )
            return

        display_name = user.first_name or user.username or "боец"
        try:
            await callback.message.edit_text("📀 Открываю твою карту...")
            status_msg = callback.message
        except Exception:  # noqa: BLE001
            status_msg = await callback.message.answer("📀 Открываю твою карту...")

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
                    "Карта устарела 😢 Войди в Яндекс заново.",
                    reply_markup=_menu_keyboard(False),
                )
            else:
                await status_msg.edit_text(f"Осмотр сорвался: {exc.detail}")
            return
        except Exception:  # noqa: BLE001
            logger.exception("prepare_library failed")
            await status_msg.edit_text("Не смог открыть твою карту. Попробуй позже.")
            return

        total = prepared.metadata.get("track_count", len(prepared.tracks))
        tease = f"🧮 В карте {total} треков"
        if prepared.top_artist:
            tease += f", лидер — {prepared.top_artist}"
        tease += ". Доктор надевает перчатки и берёт скальпель..."
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
            # 503 = «доктор прилёг вздремнуть» (429): показываем как есть, без 🚫
            prefix = "" if exc.status_code == 503 else "🚫 "
            await status_msg.edit_text(f"{prefix}{exc.detail}")
            return
        except Exception:  # noqa: BLE001
            logger.exception("roast_library failed")
            await status_msg.edit_text("Доктор поперхнулся кофе. Попробуй ещё раз.")
            return

        bundle = prepared.bundle
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
            diagnosis_name=outcome.diagnosis_name if bundle else "",
            severity=outcome.severity if bundle else 0,
            prescription=outcome.prescription if bundle else "",
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
            diagnosis_name=outcome.diagnosis_name if bundle else "",
            severity=outcome.severity if bundle else 0,
            prescription=outcome.prescription if bundle else "",
        )
        last_roasts[user_id] = last

        # Журнал доктора: append-only запись + дифф стадии с прошлым снапшотом
        diff_line = None
        if bundle is not None:
            previous = await diagnosis_registry.previous_for_user(
                str(user_id), bundle.snapshot_hash
            )
            diff_line = taste_diff(previous, outcome.diagnosis_name, outcome.severity)
            await diagnosis_registry.append(
                DiagnosisRecord(
                    snapshot_hash=bundle.snapshot_hash,
                    user_key=str(user_id),
                    diagnosis_name=outcome.diagnosis_name,
                    severity=outcome.severity,
                    verdict_phrase=outcome.diagnosis,
                    symptoms=tuple(outcome.symptoms),
                    prescription=outcome.prescription,
                    evidence=tuple(evidence_lines(bundle.evidence)),
                    score=outcome.score,
                    coverage=bundle.coverage,
                    lyric_theme=bundle.lyric_theme,
                )
            )

        try:
            await status_msg.delete()
        except Exception:  # noqa: BLE001
            pass

        # Полный вердикт ОДНИМ сообщением: длинный осмотр + карта-выписка для шера
        card_lines = [
            f"🩺 <b>ВЫПИСКА · {_esc(display_name)}</b>",
            f"Уровень боли: {_esc(LEVEL_TITLES[level])}",
            f"Здоровье вкуса: <b>{outcome.score}/10</b>",
        ]
        if bundle is not None:
            card_lines.append(
                f"Диагноз: <b>{_esc(outcome.diagnosis_name)}</b> "
                f"(стадия {outcome.severity}/10)"
            )
        card_lines.append(f"Приговор: <i>{_esc(outcome.diagnosis)}</i>")
        if outcome.symptoms:
            card_lines.append("")
            card_lines.append("Симптомы:")
            card_lines.extend(f"• {_esc(s)}" for s in outcome.symptoms)
        if bundle is not None and bundle.evidence:
            card_lines.append(
                "Улики: "
                + " · ".join(_esc(line) for line in evidence_lines(bundle.evidence))
            )
        if bundle is not None and outcome.prescription:
            card_lines.append(f"💊 Рецепт: {_esc(outcome.prescription)}")
        if diff_line:
            card_lines.append("")
            card_lines.append(f"📈 {_esc(diff_line)}")
        card = "\n".join(card_lines)

        chunks = split_for_telegram(outcome.text)
        for idx, chunk in enumerate(chunks):
            is_last = idx == len(chunks) - 1
            body = convert_markdown_to_html(chunk)
            if is_last:
                body = f"{body}\n\n➖➖➖➖➖\n{card}"
            try:
                await status_msg.answer(
                    body,
                    parse_mode="HTML",
                    reply_markup=_share_keyboard(last) if is_last else None,
                )
            except Exception:  # noqa: BLE001 — HTML мог не собраться
                await status_msg.answer(chunk + (f"\n\n{card}" if is_last else ""))

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
                f"🤝 Как и обещал — вот полная карта "
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
        status = await message.answer("⚖️ Консилиум сравнивает ваши карты...")

        my_side = BattleSide(
            display_name=display_name,
            score=last.score,
            diagnosis=last.diagnosis,
            stats_block=last.stats_block,
            sample_lines=last.sample_lines,
            diagnosis_name=last.diagnosis_name,
        )
        friend_side = BattleSide(
            display_name=friend.display_name,
            score=friend.score,
            diagnosis=friend.diagnosis,
            stats_block=friend.stats_block,
            sample_lines=friend.sample_lines,
            diagnosis_name=friend.diagnosis_name,
        )

        try:
            verdict = await loop.run_in_executor(
                None, lambda: service.generate_battle(friend_side, my_side)
            )
        except Exception:  # noqa: BLE001
            logger.exception("auto battle failed")
            await status.edit_text("Консилиум так и не договорился. Бывает.")
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
                    f"😈 <b>{_esc(display_name)}</b> лёг на осмотр по твоей ссылке! "
                    f"Вердикт консилиума:",
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
            diagnosis_name=last.diagnosis_name,
        )
        try:
            battle = await battle_registry.join(code, side)
        except BattleError as exc:
            await message.answer(f"⚔️ {exc.message}")
            return

        status = await message.answer(
            "⚔️ Оба пациента на столе, консилиум совещается..."
        )
        try:
            verdict = await loop.run_in_executor(
                None,
                lambda: service.generate_battle(battle.initiator, battle.opponent),
            )
        except Exception:  # noqa: BLE001
            logger.exception("battle verdict failed")
            await status.edit_text("Консилиум так и не договорился. Бывает.")
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
                    f"⚔️ <b>{_esc(display_name)}</b> принял вызов! Вердикт консилиума:",
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
            await callback.answer("Сначала загляни на осмотр 🔥", show_alert=True)
            return
        token = await _get_token(user.id)
        if not token:
            await callback.answer("Карта устарела, войди заново 🔑", show_alert=True)
            return
        await callback.answer()
        status_msg = await callback.message.answer("📀 Открываю карту заново...")
        display_name = user.first_name or user.username or "боец"
        await _run_roast_flow(
            status_msg, user.id, display_name, token, last.playlist_kind, last.level
        )

    @router.callback_query(lambda c: c.data == "cover")
    async def on_cover(callback: CallbackQuery) -> None:
        user = callback.from_user
        last = last_roasts.get(user.id)
        if not last:
            await callback.answer("Сначала загляни на осмотр 🔥", show_alert=True)
            return
        await callback.answer()
        status_msg = await callback.message.answer(
            "🎨 Штатный художник рисует твою обложку позора... полминуты терпения"
        )
        loop = asyncio.get_running_loop()
        try:
            image_data = await loop.run_in_executor(
                None, lambda: service.roaster.generate_image(last.text)
            )
        except HTTPException as exc:
            await status_msg.edit_text(f"Штатный художник запил: {exc.detail}")
            return
        except Exception:  # noqa: BLE001
            logger.exception("cover generation failed")
            await status_msg.edit_text("Штатный художник запил. Попробуй позже.")
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
                "Сначала пройди осмотр — батл идёт по диагнозам 🔥", show_alert=True
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
            diagnosis_name=last.diagnosis_name,
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
            f"⚔️ Батл назначен!\n\nКод: <code>{battle.code}</code>\n"
            f"Ссылка для соперника:\n{link}\n\n"
            f"Как только друг ляжет на осмотр — объявлю вердикт обоим. "
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
    # Снимаем возможный webhook: при активном webhook long-polling ловит
    # 409 Conflict и молча умирает. drop_pending_updates — чистим очередь.
    await bot.delete_webhook(drop_pending_updates=True)
    await _setup_bot_profile(bot)
    logger.info("Bot @%s: webhook снят, стартую long-polling", username)
    await dispatcher.start_polling(bot)


async def _setup_bot_profile(bot: Bot) -> None:
    """Меню команд и описание бота — в докторском стиле. Сбои не критичны."""
    try:
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="🩺 Регистратура"),
                BotCommand(command="help", description="📋 Как проходит осмотр"),
            ]
        )
        await bot.set_my_short_description(
            "Музыкальный доктор: ставит диагноз твоему вкусу по фактам. Будет больно."
        )
        await bot.set_my_description(
            "🩺 Кладу твой плейлист на стол и ставлю диагноз: где ты застрял, чем "
            "позоришься и чем это лечить. Жми /start — и на осмотр."
        )
    except Exception:  # noqa: BLE001 — профиль не должен ронять запуск
        logger.warning("не удалось выставить профиль бота", exc_info=True)


if __name__ == "__main__":
    asyncio.run(run_bot())
