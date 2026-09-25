"""Экран = одно сообщение. Карточка (фото + подпись + кнопки) или текст (переписка)."""
import asyncio
import logging
from dataclasses import dataclass, field

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Message

from db import DB
from render import Renderer

log = logging.getLogger("ui")


def btn(text: str, data: str, style: str | None = None) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data, style=style)


def kb(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[r for r in rows if r])


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n) % 100
    if 10 < n < 20:
        return many
    if n % 10 == 1:
        return one
    if 2 <= n % 10 <= 4:
        return few
    return many


@dataclass
class Card:
    template: str
    caption: str
    markup: InlineKeyboardMarkup
    ctx: dict = field(default_factory=dict)


@dataclass
class Text:
    text: str
    markup: InlineKeyboardMarkup


class UI:
    def __init__(self, bot: Bot, renderer: Renderer, db: DB) -> None:
        self.bot = bot
        self.r = renderer
        self.db = db
        self.screens: dict[int, tuple[int, int, str]] = {}  # user → (chat, message, "photo"|"text")
        self.file_ids: dict[str, str] = {}
        self.locks: dict[int, asyncio.Lock] = {}

    def attach(self, uid: int, msg: Message | None) -> None:
        """Экран — это сообщение, под которым нажали кнопку. Старше 48 ч (InaccessibleMessage) — пришлём новый."""
        if isinstance(msg, Message):
            self.screens[uid] = (msg.chat.id, msg.message_id, "photo" if msg.photo else "text")
        else:
            self.screens.pop(uid, None)

    async def adopt(self, uid: int, msg: Message | None) -> None:
        """Сделать экраном другое сообщение (например, уведомление), а прежний экран удалить."""
        cur = self.screens.get(uid)
        if cur and isinstance(msg, Message) and (cur[0], cur[1]) != (msg.chat.id, msg.message_id):
            await self._delete(cur)
        self.attach(uid, msg)

    async def drop(self, uid: int) -> None:
        cur = self.screens.pop(uid, None)
        if cur:
            await self._delete(cur)

    async def _delete(self, cur: tuple[int, int, str]) -> None:
        try:
            await self.bot.delete_message(cur[0], cur[1])
        except TelegramBadRequest:
            pass

    async def show(self, uid: int, chat_id: int, screen: Card | Text) -> None:
        lock = self.locks.setdefault(uid, asyncio.Lock())
        async with lock:
            if isinstance(screen, Card):
                await self._show_card(uid, chat_id, screen)
            else:
                await self._show_text(uid, chat_id, screen)

    async def _upload(self, key: str, html: str) -> BufferedInputFile:
        return BufferedInputFile(await self.r.shot(key, html), "card.png")

    async def _show_card(self, uid: int, chat_id: int, s: Card) -> None:
        key, html = self.r.prepare(s.template, **s.ctx)
        fid = self.file_ids.get(key) or await self.db.file_id(key)
        src = fid or await self._upload(key, html)
        cur = self.screens.get(uid)
        if cur:
            try:
                msg = await self.bot.edit_message_media(
                    chat_id=cur[0], message_id=cur[1],
                    media=InputMediaPhoto(media=src, caption=s.caption), reply_markup=s.markup)
                if isinstance(msg, Message):
                    await self._remember(uid, msg, key)
                return
            except TelegramBadRequest as e:
                if "not modified" in str(e):
                    return
                log.info("edit media → resend: %s", e)
                await self._delete(cur)
        try:
            msg = await self.bot.send_photo(chat_id, src, caption=s.caption, reply_markup=s.markup)
        except TelegramBadRequest:
            if not fid:
                raise
            # file_id протух — забыть и залить картинку заново
            self.file_ids.pop(key, None)
            await self.db.drop_file_id(key)
            msg = await self.bot.send_photo(chat_id, await self._upload(key, html), caption=s.caption, reply_markup=s.markup)
        await self._remember(uid, msg, key)

    async def _show_text(self, uid: int, chat_id: int, s: Text) -> None:
        cur = self.screens.get(uid)
        if cur and cur[2] == "text":
            try:
                await self.bot.edit_message_text(s.text, chat_id=cur[0], message_id=cur[1], reply_markup=s.markup)
                return
            except TelegramBadRequest as e:
                if "not modified" in str(e):
                    return
                log.info("edit text → resend: %s", e)
        if cur:
            await self._delete(cur)  # фото в текст API не превращает — пересылаем
        msg = await self.bot.send_message(chat_id, s.text, reply_markup=s.markup)
        self.screens[uid] = (chat_id, msg.message_id, "text")

    async def _remember(self, uid: int, msg: Message, key: str) -> None:
        self.screens[uid] = (msg.chat.id, msg.message_id, "photo")
        if msg.photo and key not in self.file_ids:
            self.file_ids[key] = msg.photo[-1].file_id
            await self.db.put_file_id(key, msg.photo[-1].file_id)
