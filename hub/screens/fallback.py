"""Последний роутер: устаревшие кнопки и лишние сообщения. Подключается строго последним."""
from contextlib import suppress

from aiogram import Router
from aiogram.types import CallbackQuery, Message

from access import Viewer
from screens.common import alert

router = Router()


@router.callback_query()
async def unknown_button(cq: CallbackQuery, me: Viewer) -> None:
    await alert(cq, me.t("Кнопка устарела — нажмите /start"))


@router.message()
async def stray(message: Message) -> None:
    """Экран — одно сообщение: всё лишнее из чата убираем."""
    with suppress(Exception):
        await message.delete()
