"""Вложение из переписки — отдельным сообщением под экраном, тем же типом, что в оригинале, с «✕ Скрыть»."""
from contextlib import suppress
from html import escape

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile, CallbackQuery

from access import Viewer
from screens.common import alert, gone, ints, log, record, title, usable
from tg import Hub, HubError
from ui import UI, btn, kb

router = Router()


@router.callback_query(F.data.startswith("med:"))
async def show_media(cq: CallbackQuery, ui: UI, hub: Hub, me: Viewer) -> None:
    nums = ints(*cq.data.split(":")[1:4])
    a = hub.accs.get(nums[0]) if nums and len(nums) == 3 else None
    if not usable(a):
        return await gone(cq, ui, hub, me)
    try:
        c = await hub.chat(a.id, nums[1])
        kind, data, filename = await hub.media(a.id, c, nums[2])
    except HubError as e:
        return await alert(cq, me.err(e))
    with suppress(TelegramBadRequest):
        await cq.answer()
    file = BufferedInputFile(data, filename)
    hide = kb([btn(me.t("✕ Скрыть"), "hide")])
    caption = me.t("{chat} · через #{n}", chat=escape(title(me, c)), n=a.id)
    chat_id = cq.message.chat.id
    bot = ui.bot
    try:
        if kind == "sticker":
            await bot.send_sticker(chat_id, file, reply_markup=hide)
        elif kind == "photo":
            await bot.send_photo(chat_id, file, caption=caption, reply_markup=hide)
        elif kind == "voice":
            await bot.send_voice(chat_id, file, caption=caption, reply_markup=hide)
        elif kind == "video_note":
            await bot.send_video_note(chat_id, file, reply_markup=hide)
        elif kind == "video":
            await bot.send_video(chat_id, file, caption=caption, reply_markup=hide, supports_streaming=True)
        elif kind == "gif":
            await bot.send_animation(chat_id, file, caption=caption, reply_markup=hide)
        elif kind == "audio":
            await bot.send_audio(chat_id, file, caption=caption, reply_markup=hide)
        else:
            await bot.send_document(chat_id, file, caption=caption, reply_markup=hide)
        await record(hub, me, a.id, "view_media")
    except TelegramBadRequest as e:
        log.warning("media #%d %s: %s", a.id, filename, e)
        await bot.send_message(chat_id, me.t("Не получилось показать это вложение 😕"), reply_markup=hide)


@router.callback_query(F.data == "hide")
async def hide_media(cq: CallbackQuery) -> None:
    with suppress(TelegramBadRequest):
        await cq.answer()
    with suppress(Exception):
        await cq.message.delete()
