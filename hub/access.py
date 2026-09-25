"""Права. Одна middleware на каждый апдейт + перепроверка account_id из кнопок (callback_data можно подделать).

Роли: admin — глобально (ADMIN_IDS из .env или выдан в боте); operator/viewer — на конкретный аккаунт.
Здесь же язык пользователя: Viewer.t() переводит строки интерфейса.
"""
import logging
import secrets
from contextlib import suppress
from dataclasses import dataclass, field

from aiogram import BaseMiddleware, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from i18n import detect, pl, tr
from tg import Hub, HubError

log = logging.getLogger("access")

ROLE_LABEL = {"none": "— нет доступа", "viewer": "👁 чтение", "operator": "✍️ чтение и ответы"}
ROLES = ["none", "viewer", "operator"]


@dataclass
class Viewer:
    id: int
    name: str
    admin: bool
    roles: dict[int, str] = field(default_factory=dict)
    lang: str = "ru"
    pin: str | None = None     # соль$хэш — если стоит PIN
    quiet: bool = False        # тихие уведомления ночью
    groups: bool = False       # уведомлять обо всех сообщениях в группах

    def role(self, acc_id: int) -> str:
        return "admin" if self.admin else self.roles.get(acc_id, "none")

    def can_read(self, acc_id: int) -> bool:
        return self.admin or self.roles.get(acc_id) in ("viewer", "operator")

    def can_write(self, acc_id: int) -> bool:
        return self.admin or self.roles.get(acc_id) == "operator"

    def t(self, text: str, /, **kw) -> str:
        return tr(self.lang, text, **kw)

    def pl(self, n: int, one: str, few: str, many: str) -> str:
        return pl(self.lang, n, one, few, many)

    def err(self, e: HubError) -> str:
        return tr(self.lang, e.msg, **e.kw)


class Access(BaseMiddleware):
    """Только личка. Каждого пишет в users, забаненных отсекает, кладёт в хендлер `me: Viewer`."""

    def __init__(self, hub: Hub) -> None:
        self.hub = hub

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        chat = data.get("event_chat")
        if user is None or user.is_bot or (chat is not None and chat.type != "private"):
            return None
        row = await self.hub.db.touch_user(user.id, user.full_name, user.username)
        lang = row["lang"]
        if not lang:  # первый раз — язык из настроек Telegram, дальше человек выбирает сам
            lang = detect(user.language_code)
            await self.hub.db.set_user(user.id, lang=lang)
        admin = bool(row["admin"]) or user.id in self.hub.cfg.admin_ids
        if row["banned"] and not admin:
            if isinstance(event, CallbackQuery):
                with suppress(Exception):
                    await event.answer(tr(lang, "Доступ закрыт"), show_alert=True)
            return None
        roles = {} if admin else await self.hub.db.roles(user.id)
        data["me"] = Viewer(user.id, row["name"], admin, roles, lang, row["pin"], bool(row["quiet"]), bool(row["groups"]))
        return await handler(event, data)


class AdminGate(BaseMiddleware):
    """Внутренняя middleware роутера админки: срабатывает, только когда нашёлся админский хендлер."""

    async def __call__(self, handler, event, data):
        me: Viewer | None = data.get("me")
        if me and me.admin:
            return await handler(event, data)
        if isinstance(event, CallbackQuery):
            await event.answer(me.t("Только для администратора") if me else "⛔", show_alert=True)
        elif isinstance(event, Message):
            with suppress(Exception):
                await event.delete()
        return None


READ_OPS = {"acc", "ls", "dlg", "mute", "srch", "set", "setn", "setm", "ct", "pp", "med", "nfo"}
WRITE_OPS = {"rep", "new", "dls", "dlc", "dlx", "rcs", "rcc", "rcx", "fws", "fwa", "fwc", "fwx",
             "unr", "rda", "nfr", "nfp"}   # ответить, написать первым, удалить, реакция, переслать, прочитать
ADMIN_OPS = {"setx", "setxok"}


class AccountGate(BaseMiddleware):
    """Кнопки вида `op:<account_id>:…` — роль на этот аккаунт проверяется по базе, а не по тому, что прислал клиент."""

    async def __call__(self, handler, event: CallbackQuery, data):
        parts = (event.data or "").split(":")
        op = parts[0]
        if op in READ_OPS or op in WRITE_OPS or op in ADMIN_OPS:
            me: Viewer = data["me"]
            try:
                acc_id = int(parts[1])
            except (IndexError, ValueError):
                await event.answer()
                return None
            if op in ADMIN_OPS:
                ok = me.admin
            elif op in WRITE_OPS:
                ok = me.can_write(acc_id)
            else:
                ok = me.can_read(acc_id)
            if not ok:
                await event.answer(me.t("Нет доступа к этому аккаунту"), show_alert=True)
                return None
        return await handler(event, data)


# ─── Первый админ: /claim <код из лога> ─────────────────────────────────────

claim_router = Router()
CLAIM: dict[str, str | None] = {"token": None}


async def prepare_claim(hub: Hub) -> None:
    """Если админов нет ни в .env, ни в базе — печатает в лог одноразовый код."""
    if await hub.admin_ids():
        return
    CLAIM["token"] = secrets.token_hex(4)
    log.warning("Админов нет. Отправь боту:  /claim %s", CLAIM["token"])


@claim_router.message(Command("claim"))
async def claim(message: Message, command: CommandObject, me: Viewer, hub: Hub) -> None:
    with suppress(Exception):
        await message.delete()
    token = CLAIM["token"]
    if me.admin or not token or not secrets.compare_digest((command.args or "").strip(), token):
        return
    CLAIM["token"] = None
    await hub.db.set_admin(me.id, True)
    await hub.db.log(me.id, None, "стал администратором через /claim")
    log.warning("администратор назначен: %s (%d)", me.name, me.id)
    await message.answer(me.t("👑 Вы администратор. Нажмите /start"))
