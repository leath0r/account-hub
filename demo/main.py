"""Account Hub — демо интерфейса. Данные выдуманные, MTProto не подключён."""
import asyncio
import logging
import os
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.types import BotCommand, ErrorEvent

import screens_admin
import screens_user
from render import Renderer
from ui import UI

BASE = Path(__file__).parent
log = logging.getLogger("hub")


def load_env() -> None:
    env = BASE / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


async def setup_profile(bot: Bot, renderer: Renderer) -> None:
    await bot.set_my_commands([
        BotCommand(command="start", description="Главная — выбор аккаунта"),
        BotCommand(command="admin", description="Админ-панель"),
    ])
    await bot.set_my_short_description("Панель для нескольких Telegram-аккаунтов · демо")
    await bot.set_my_description(
        "Account Hub — демо интерфейса.\n\nВыбирайте аккаунт и работайте от его имени: чаты, группы, поиск, "
        "входящие. Все аккаунты и переписки здесь выдуманные.")
    avatar = BASE / "avatar.png"
    if not avatar.exists():
        _, png = await renderer.render("avatar.html", size=(640, 640))
        avatar.write_bytes(png)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    load_env()
    renderer = Renderer()
    await renderer.start()
    bot = Bot(os.environ["BOT_TOKEN"], default=DefaultBotProperties(parse_mode="HTML", link_preview_is_disabled=True))
    dp = Dispatcher()
    dp["ui"] = UI(bot, renderer)
    dp.include_routers(screens_admin.router, screens_user.router)

    @dp.errors()
    async def on_error(event: ErrorEvent) -> None:
        log.exception("update failed: %s", event.exception)

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        try:
            await setup_profile(bot, renderer)
        except Exception as e:
            log.warning("profile setup skipped: %s", e)
        me = await bot.get_me()
        log.info("started as @%s", me.username)
        await dp.start_polling(bot)
    finally:
        await renderer.stop()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
