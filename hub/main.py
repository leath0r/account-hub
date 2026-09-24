"""Account Hub v0.1 — бот-панель для нескольких своих Telegram-аккаунтов. Бот и все клиенты — в одном процессе."""
import asyncio
import logging
from contextlib import suppress
from html import escape
from logging.handlers import RotatingFileHandler

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import BotCommand, ErrorEvent

import config
from access import Access, claim_router, prepare_claim
from auth import Logins
from crypto import Box
from db import DB
from render import Renderer
from screens import admin, user
from tg import Account, Hub
from ui import UI, btn, kb

log = logging.getLogger("hub")


def build_dispatcher(hub: Hub, ui: UI, logins: Logins) -> Dispatcher:
    dp = Dispatcher()
    dp["hub"], dp["ui"], dp["logins"] = hub, ui, logins
    access = Access(hub)
    dp.message.outer_middleware(access)
    dp.callback_query.outer_middleware(access)
    dp.include_routers(claim_router, admin.router, user.router)

    @dp.errors()
    async def on_error(event: ErrorEvent) -> None:
        log.exception("update failed: %s", event.exception, exc_info=event.exception)

    return dp


async def setup_profile(bot: Bot, renderer: Renderer) -> None:
    await bot.set_my_commands([
        BotCommand(command="start", description="Главная — выбор аккаунта"),
        BotCommand(command="admin", description="Админ-панель"),
    ])
    await bot.set_my_short_description("Панель для нескольких своих Telegram-аккаунтов")
    await bot.set_my_description(
        "Account Hub — работайте с несколькими своими Telegram-аккаунтами из одного чата: "
        "чаты, группы, поиск, входящие. Доступ выдаёт администратор.")
    avatar = config.BASE / "avatar.png"
    if not avatar.exists():
        _, png = await renderer.render("avatar.html", size=(640, 640))
        avatar.write_bytes(png)


def notifier(bot: Bot, hub: Hub):
    async def account_down(acc: Account) -> None:
        text = f"🟠 <b>Аккаунт #{acc.id} {escape(acc.name)}</b> — сессия слетела, нужен повторный вход."
        for uid in await hub.admin_ids():
            with suppress(Exception):
                await bot.send_message(uid, text, reply_markup=kb([btn("🔑 Войти заново", f"relog:{acc.id}", "primary")]))
    return account_down


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.StreamHandler(),
                                  RotatingFileHandler(config.BASE / "hub.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")])
    logging.getLogger("telethon").setLevel(logging.WARNING)
    cfg = config.load()
    db = DB(cfg.db_path)
    await db.connect()
    hub = Hub(cfg, db, Box(cfg.session_key))
    renderer = Renderer()
    await renderer.start()
    if cfg.proxy:
        log.info("Telegram — через прокси %s", cfg.proxy.split("@")[-1])
    bot = Bot(cfg.bot_token, session=AiohttpSession(proxy=cfg.proxy) if cfg.proxy else None,
              default=DefaultBotProperties(parse_mode="HTML", link_preview_is_disabled=True))
    logins = Logins(hub)
    dp = build_dispatcher(hub, UI(bot, renderer, db), logins)
    hub.on_down = notifier(bot, hub)
    sweeper = asyncio.create_task(logins.sweep())
    try:
        if not hub.api_ready:
            log.warning("API_ID/API_HASH не заданы — бот работает, но добавить аккаунт нельзя (my.telegram.org)")
        await hub.start()
        await prepare_claim(hub)
        await bot.delete_webhook(drop_pending_updates=True)
        try:
            await setup_profile(bot, renderer)
        except Exception as e:
            log.warning("profile setup skipped: %s", e)
        me = await bot.get_me()
        log.info("started as @%s", me.username)
        await dp.start_polling(bot)
    finally:
        sweeper.cancel()
        for uid in list(logins.pending):
            await logins.cancel(uid)
        await hub.stop()
        await renderer.stop()
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
