"""Account Hub — бот-панель для нескольких своих Telegram-аккаунтов. Бот и все клиенты — в одном процессе."""
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
from i18n import tr
from notify import Notifier
from render import Renderer
from screens import ROUTERS
from tg import Account, Hub
from ui import UI, btn, kb

log = logging.getLogger("hub")


def build_dispatcher(hub: Hub, ui: UI, logins: Logins) -> Dispatcher:
    dp = Dispatcher()
    dp["hub"], dp["ui"], dp["logins"] = hub, ui, logins
    dp["dp"] = dp  # PIN: после верного кода отложенное нажатие прогоняется через диспетчер ещё раз
    access = Access(hub)
    dp.message.outer_middleware(access)
    dp.callback_query.outer_middleware(access)
    dp.include_routers(claim_router, *ROUTERS)

    @dp.errors()
    async def on_error(event: ErrorEvent) -> None:
        log.exception("update failed: %s", event.exception, exc_info=event.exception)

    return dp


async def setup_profile(bot: Bot, renderer: Renderer) -> None:
    for lang in ("ru", "en"):
        await bot.set_my_commands([
            BotCommand(command="start", description=tr(lang, "Главная — выбор аккаунта")),
            BotCommand(command="admin", description=tr(lang, "Админ-панель")),
        ], language_code=None if lang == "ru" else lang)
        await bot.set_my_short_description(tr(lang, "Панель для нескольких своих Telegram-аккаунтов"),
                                           language_code=None if lang == "ru" else lang)
        await bot.set_my_description(
            tr(lang, "Account Hub — работайте с несколькими своими Telegram-аккаунтами из одного чата: "
                     "чаты, группы, поиск, входящие, уведомления. Доступ выдаёт администратор."),
            language_code=None if lang == "ru" else lang)
    avatar = config.BASE / "avatar.png"
    if not avatar.exists():
        _, png = await renderer.render("avatar.html", size=(640, 640))
        avatar.write_bytes(png)


def notifier_down(bot: Bot, hub: Hub):
    async def account_down(acc: Account) -> None:
        for uid in await hub.admin_ids():
            u = await hub.db.user(uid)
            lang = (u["lang"] if u else None) or "ru"
            text = tr(lang, "🟠 <b>Аккаунт #{n} {name}</b> — сессия слетела, нужен повторный вход.", n=acc.id, name=escape(acc.name))
            with suppress(Exception):
                await bot.send_message(uid, text, reply_markup=kb([btn(tr(lang, "🔑 Войти заново"), f"relog:{acc.id}", "primary")]))
    return account_down


notifier = notifier_down  # старое имя — для стенда


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.StreamHandler(),
                                  RotatingFileHandler(config.LOG_FILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8")])
    logging.getLogger("telethon").setLevel(logging.WARNING)
    cfg = config.load()
    if not cfg.bot_token:
        log.error("BOT_TOKEN не задан — впишите токен от @BotFather в %s и перезапустите", config.ENV)
        raise SystemExit(2)
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
    hub.on_down = notifier_down(bot, hub)
    hub.on_message = Notifier(bot, hub).on_message
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
