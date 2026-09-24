"""Аккаунты = Telethon-клиенты. Живут в одном event loop с ботом.

Все обращения к Telegram идут через Hub._call: слетевшая сессия → статус «Нужен вход» и уведомление
админам, FloodWait и обрывы связи → HubError с понятным текстом для алерта.
"""
import asyncio
import base64
import logging
import re
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit

from cryptography.fernet import InvalidToken
from telethon import TelegramClient, events, types, utils
from telethon.errors import (AuthKeyDuplicatedError, ChatWriteForbiddenError, FloodWaitError, InputUserDeactivatedError,
                             PeerFloodError, RPCError, UnauthorizedError, UserIsBlockedError, UsernameInvalidError,
                             UsernameNotOccupiedError, UserPrivacyRestrictedError, YouBlockedUserError)
from telethon.sessions import StringSession
from telethon.tl.functions.contacts import GetContactsRequest

from config import Config
from crypto import Box
from db import DB

log = logging.getLogger("tg")

DIALOG_LIMIT = 200      # сколько диалогов тянуть на аккаунт
DIALOG_TTL = 30         # сек — кэш списка диалогов
STALE_AFTER = 4         # сек — после нового сообщения кэш обновится не раньше
SEND_GAP = 2.0          # сек — не больше 1 сообщения в 2 с на аккаунт

STATUS = {  # цвет на карточке, подпись, эмодзи в кнопках
    "online": ("#34D399", "Онлайн", "🟢"),
    "need_login": ("#F5A623", "Нужен вход", "🟠"),
    "offline": ("#5B6273", "Отключён", "⚫"),
    "nonet": ("#5B6273", "Нет связи", "⚫"),
}
STYLES = ["g1", "g2", "sv", "st"]


class HubError(Exception):
    """Ошибка, которую можно показать пользователю как есть."""


class AccountDown(HubError):
    """Сессия аккаунта слетела — нужен повторный вход."""


def telethon_proxy(url: str | None) -> dict | None:
    """socks5://[user:pass@]host:port → формат Telethon (нужен python-socks)."""
    if not url:
        return None
    u = urlsplit(url)
    return {"proxy_type": u.scheme.rstrip("h"), "addr": u.hostname, "port": u.port,
            "username": u.username, "password": u.password, "rdns": True}


def fmt_wait(seconds: int) -> str:
    if seconds >= 3600:
        return f"{seconds // 3600} ч {seconds % 3600 // 60:02d} мин"
    return f"{seconds // 60}:{seconds % 60:02d}"


@dataclass
class Account:
    id: int
    tg_id: int | None
    name: str
    username: str
    phone: str
    enabled: bool
    photo: bytes | None = None
    authorized: bool = False   # сессия есть и Telegram её принимает
    connected: bool = False    # клиент сейчас на связи

    @property
    def status(self) -> str:
        if not self.enabled:
            return "offline"
        if not self.authorized:
            return "need_login"
        return "online" if self.connected else "nonet"

    @property
    def status_color(self) -> str:
        return STATUS[self.status][0]

    @property
    def status_label(self) -> str:
        return STATUS[self.status][1]

    @property
    def status_emoji(self) -> str:
        return STATUS[self.status][2]

    @property
    def style(self) -> str:
        return STYLES[(self.id - 1) % len(STYLES)]

    @property
    def letter(self) -> str:
        return (self.name[:1] or "?").upper()

    @property
    def handle(self) -> str:
        return f"@{self.username}" if self.username else "без username"

    @property
    def phone_masked(self) -> str:
        d = "".join(c for c in self.phone if c.isdigit())
        if len(d) < 6:
            return "+•••"
        return f"+{d[0]} ••• ••• {d[-4:-2]} {d[-2:]}"

    @property
    def photo_uri(self) -> str:
        return f"data:image/jpeg;base64,{base64.b64encode(self.photo).decode()}" if self.photo else ""


@dataclass
class Chat:
    id: int
    title: str
    kind: str          # user | group | channel
    unread: int
    tg_muted: bool     # заглушён в самом Telegram
    members: int
    last_text: str
    last_time: str
    last_ts: float


@dataclass
class Person:
    """Человек (или группа/канал), найденный по @username или из контактов."""
    id: int
    name: str
    username: str
    kind: str          # user | bot | group | channel

    @property
    def handle(self) -> str:
        return f"@{self.username}" if self.username else "без username"


USERNAME_RE = re.compile(r"^(?:@|(?:https?://)?(?:t|telegram)\.me/)([A-Za-z][A-Za-z0-9_]{3,31})/?$")


def parse_username(query: str) -> str | None:
    """«@name», «t.me/name», «https://t.me/name» → name. Обычный текст → None (это поиск по чатам)."""
    m = USERNAME_RE.match(query.strip())
    return m.group(1) if m else None


# Ошибки отправки, которые надо объяснить человеческим языком (класс Telethon или код ошибки)
SEND_ERRORS = [
    (PeerFloodError, "Telegram ограничил аккаунт: сейчас писать новым людям нельзя (спам-блок). "
                     "Подробности — в @SpamBot с этого аккаунта"),
    (UserPrivacyRestrictedError, "Этот человек принимает сообщения только от своих контактов"),
    ("PRIVACY_PREMIUM_REQUIRED", "Этот человек принимает сообщения только от Premium-аккаунтов"),
    (UserIsBlockedError, "Этот человек заблокировал аккаунт"),
    (YouBlockedUserError, "Аккаунт сам заблокировал этого человека — сначала разблокируйте в Telegram"),
    (InputUserDeactivatedError, "Этот аккаунт удалён"),
    (ChatWriteForbiddenError, "Писать в этот чат нельзя"),
]


@dataclass
class Msg:
    me: bool
    who: str
    time: str
    text: str
    id: int = 0
    media: str = ""    # sticker | photo — такие вложения бот умеет показать


def describe(m) -> str:
    """Текст сообщения + пометка о вложении. Сами файлы — в v0.2."""
    media = ""
    if m.action is not None:
        return "ℹ️ служебное сообщение"
    if m.photo:
        media = "🖼 фото"
    elif m.sticker:
        emoji = getattr(m.file, "emoji", None) or ""
        media = f"стикер {emoji}".strip()
    elif m.voice:
        media = "🎤 голосовое"
    elif m.video_note:
        media = "⏺ видеосообщение"
    elif m.gif:
        media = "GIF"
    elif m.video:
        media = "🎬 видео"
    elif m.audio:
        media = "🎵 аудио"
    elif m.poll:
        q = m.poll.poll.question
        media = f"📊 опрос: {getattr(q, 'text', q)}"
    elif m.geo:
        media = "📍 геопозиция"
    elif m.contact:
        media = "👤 контакт"
    elif m.document:
        media = f"📎 {getattr(m.file, 'name', None) or 'файл'}"
    text = m.message or ""
    if media:
        return f"[{media}] {text}".strip()
    return text


class Hub:
    def __init__(self, cfg: Config, db: DB, box: Box) -> None:
        self.cfg, self.db, self.box = cfg, db, box
        self.accs: dict[int, Account] = {}
        self.clients: dict[int, TelegramClient] = {}
        self._dialogs: dict[int, tuple[float, list[Chat]]] = {}
        self._stale: set[int] = set()
        self._dlg_locks: dict[int, asyncio.Lock] = {}
        self._send_locks: dict[int, asyncio.Lock] = {}
        self._last_send: dict[int, float] = {}
        self.on_down = None  # async (Account) -> None: уведомить админов

    # ─── Жизненный цикл ────────────────────────────────────────────────────

    @property
    def api_ready(self) -> bool:
        return bool(self.cfg.api_id and self.cfg.api_hash)

    def new_client(self, session: str = "") -> TelegramClient:
        if not self.api_ready:
            raise HubError("Не заданы API_ID и API_HASH в .env — их выдают на my.telegram.org")
        return TelegramClient(StringSession(session), self.cfg.api_id, self.cfg.api_hash,
                              device_model="Account Hub", system_version="Account Hub", app_version="0.1",
                              lang_code="ru", system_lang_code="ru", proxy=telethon_proxy(self.cfg.proxy))

    async def start(self) -> None:
        for row in await self.db.accounts():
            try:
                phone, authorized = self.box.open(row["phone"]), row["session"] is not None
            except InvalidToken:
                log.error("#%d: не расшифровать номер и сессию — сменился SESSION_KEY? Удалите аккаунт и добавьте заново",
                          row["id"])
                phone, authorized = "", False
            self.accs[row["id"]] = Account(
                id=row["id"], tg_id=row["tg_id"], name=row["name"], username=row["username"] or "",
                phone=phone, enabled=bool(row["enabled"]), photo=row["photo"], authorized=authorized)
        todo = [a.id for a in self.accs.values() if a.enabled and a.authorized]
        await asyncio.gather(*(self._connect(i) for i in todo), return_exceptions=True)
        log.info("аккаунтов: %d, на связи: %d", len(self.accs), len(self.clients))

    async def stop(self) -> None:
        await asyncio.gather(*(c.disconnect() for c in self.clients.values()), return_exceptions=True)
        self.clients.clear()

    def accounts(self) -> list[Account]:
        return [self.accs[i] for i in sorted(self.accs)]

    async def admin_ids(self) -> set[int]:
        return set(self.cfg.admin_ids) | await self.db.admins()

    async def _connect(self, acc_id: int) -> None:
        acc = self.accs[acc_id]
        row = await self.db.account(acc_id)
        if not row or row["session"] is None or not self.api_ready:
            return
        try:
            session = self.box.open(row["session"])
        except InvalidToken:
            acc.authorized = False
            return
        client = self.new_client(session)
        try:
            await asyncio.wait_for(client.connect(), 30)
            if not await client.is_user_authorized():
                raise AccountDown("сессия не принята")
            await self._attach(acc, client)
        except (AccountDown, UnauthorizedError, AuthKeyDuplicatedError) as e:
            log.warning("#%d: сессия слетела: %r", acc_id, e)
            with suppress(Exception):
                await client.disconnect()
            await self._mark_down(acc)
        except Exception as e:
            log.warning("#%d: нет связи: %r", acc_id, e)
            with suppress(Exception):
                await client.disconnect()
            acc.connected = False

    async def _attach(self, acc: Account, client: TelegramClient) -> None:
        """Подключённый и авторизованный клиент → в работу: профиль, события, статус."""
        me = await client.get_me()
        acc.tg_id = me.id
        acc.name = utils.get_display_name(me) or "Без имени"
        acc.username = me.username or ""
        with suppress(Exception):
            acc.photo = await client.download_profile_photo(me, file=bytes, download_big=False) or None
        await self.db.update_account(acc.id, tg_id=acc.tg_id, name=acc.name, username=acc.username, photo=acc.photo)

        async def touched(event, acc_id=acc.id) -> None:
            self._stale.add(acc_id)

        client.add_event_handler(touched, events.NewMessage())
        client.add_event_handler(touched, events.MessageRead())
        self.clients[acc.id] = client
        self._dialogs.pop(acc.id, None)
        acc.authorized = acc.connected = True

    async def _mark_down(self, acc: Account) -> None:
        had_session = acc.authorized
        acc.authorized = acc.connected = False
        client = self.clients.pop(acc.id, None)
        if client:
            with suppress(Exception):
                await client.disconnect()
        self._dialogs.pop(acc.id, None)
        await self.db.update_account(acc.id, session=None)
        if had_session and self.on_down:
            with suppress(Exception):
                await self.on_down(acc)

    async def _client(self, acc_id: int) -> TelegramClient:
        acc = self.accs.get(acc_id)
        if acc is None:
            raise HubError("Этого аккаунта больше нет")
        if not acc.enabled:
            raise HubError("Аккаунт отключён в админ-панели")
        if not acc.authorized:
            raise AccountDown("Сессия слетела — нужен повторный вход")
        client = self.clients.get(acc_id)
        if client is None:
            await self._connect(acc_id)
            client = self.clients.get(acc_id)
        if client is None:
            raise HubError("Нет связи с Telegram — попробуйте позже")
        acc.connected = client.is_connected()
        return client

    async def _call(self, acc_id: int, fn):
        client = await self._client(acc_id)
        try:
            return await fn(client)
        except (UnauthorizedError, AuthKeyDuplicatedError) as e:
            await self._mark_down(self.accs[acc_id])
            raise AccountDown("Сессия слетела — нужен повторный вход") from e
        except FloodWaitError as e:
            raise HubError(f"Telegram просит подождать {fmt_wait(e.seconds)}") from e
        except RPCError as e:
            for known, text in SEND_ERRORS:
                if e.message == known if isinstance(known, str) else isinstance(e, known):
                    raise HubError(text) from e
            log.warning("#%d: %r", acc_id, e)
            raise HubError(f"Telegram ответил ошибкой: {e.message}") from e
        except (ConnectionError, OSError, asyncio.TimeoutError) as e:
            self.accs[acc_id].connected = False
            raise HubError("Нет связи с Telegram — попробуйте позже") from e

    # ─── Чтение ────────────────────────────────────────────────────────────

    def fmt_time(self, dt: datetime | None) -> str:
        if dt is None:
            return ""
        local = dt.astimezone(self.cfg.tz)
        if local.date() == datetime.now(self.cfg.tz).date():
            return local.strftime("%H:%M")
        return local.strftime("%d.%m %H:%M")

    def _chat(self, d) -> Chat:
        ent = d.entity
        if d.is_user:
            kind = "user"
            title = "Избранное" if getattr(ent, "is_self", False) else (d.name or "Удалённый аккаунт")
        else:
            kind = "group" if d.is_group else "channel"
            title = d.name or "Без названия"
        mute_until = getattr(getattr(getattr(d, "dialog", None), "notify_settings", None), "mute_until", None)
        tg_muted = isinstance(mute_until, datetime) and mute_until > datetime.now(timezone.utc)
        m = d.message
        return Chat(
            id=d.id, title=title, kind=kind, unread=d.unread_count or 0, tg_muted=tg_muted,
            members=getattr(ent, "participants_count", None) or 0,
            last_text=describe(m) if m else "", last_time=self.fmt_time(m.date) if m else "",
            last_ts=m.date.timestamp() if m and m.date else 0.0)

    async def dialogs(self, acc_id: int, force: bool = False) -> list[Chat]:
        lock = self._dlg_locks.setdefault(acc_id, asyncio.Lock())
        async with lock:
            cached = self._dialogs.get(acc_id)
            if cached and not force:
                age = time.monotonic() - cached[0]
                if age < DIALOG_TTL and not (acc_id in self._stale and age > STALE_AFTER):
                    return cached[1]
            self._stale.discard(acc_id)
            raw = await self._call(acc_id, lambda c: c.get_dialogs(limit=DIALOG_LIMIT))
            chats = [self._chat(d) for d in raw]
            self._dialogs[acc_id] = (time.monotonic(), chats)
            return chats

    def cached_dialogs(self, acc_id: int) -> list[Chat]:
        """Что уже лежит в кэше — без запроса в Telegram."""
        cached = self._dialogs.get(acc_id)
        return cached[1] if cached else []

    async def chat(self, acc_id: int, chat_id: int) -> Chat:
        for c in await self.dialogs(acc_id):
            if c.id == chat_id:
                return c
        raise HubError("Чат не найден — возможно, он удалён или ушёл далеко вниз списка")

    async def _peer(self, client: TelegramClient, chat_id: int):
        try:
            return await client.get_input_entity(chat_id)
        except ValueError:
            await client.get_dialogs(limit=DIALOG_LIMIT)  # заодно заполнит кэш сущностей
            try:
                return await client.get_input_entity(chat_id)
            except ValueError:
                raise HubError("Чат не найден — возможно, он удалён") from None

    async def messages(self, acc_id: int, chat: Chat, limit: int) -> list[Msg]:
        async def run(c: TelegramClient):
            return await c.get_messages(await self._peer(c, chat.id), limit=limit)

        out = []
        for m in reversed(await self._call(acc_id, run)):
            if m.out:
                who = "Вы"
            elif chat.kind == "channel":
                who = chat.title
            elif chat.kind == "user":
                who = chat.title
            else:
                who = (utils.get_display_name(m.sender) if m.sender else "") or m.post_author or "—"
            media = "sticker" if m.sticker else "photo" if m.photo else ""
            out.append(Msg(bool(m.out), who, self.fmt_time(m.date), describe(m) or "…", m.id, media))
        return out

    async def _message(self, c: TelegramClient, chat: Chat, msg_id: int):
        peer = await self._peer(c, chat.id)
        m = await c.get_messages(peer, ids=msg_id)
        if m is None:
            raise HubError("Этого сообщения уже нет")
        return peer, m

    async def media(self, acc_id: int, chat: Chat, msg_id: int) -> tuple[str, bytes, str]:
        """Скачать стикер или фото из сообщения → (вид, байты, имя файла для Bot API)."""
        async def run(c: TelegramClient):
            _, m = await self._message(c, chat, msg_id)
            if m.sticker:
                mime = getattr(m.file, "mime_type", "") or ""
                ext = {"application/x-tgsticker": "tgs", "video/webm": "webm"}.get(mime, "webp")
                kind = "sticker"
            elif m.photo:
                kind, ext = "photo", "jpg"
            else:
                raise HubError("Здесь нечего показать")
            data = await c.download_media(m, file=bytes)
            if not data:
                raise HubError("Не получилось скачать вложение")
            return kind, data, f"{kind}.{ext}"

        return await self._call(acc_id, run)

    async def delete(self, acc_id: int, chat: Chat, msg_id: int, revoke: bool) -> None:
        """revoke=True — удалить у всех. В группах — только свои сообщения (чужие может лишь админ группы)."""
        async def run(c: TelegramClient):
            peer, m = await self._message(c, chat, msg_id)
            if not m.out and chat.kind != "user":
                raise HubError("В группах можно удалять только свои сообщения")
            await c.delete_messages(peer, [msg_id], revoke=revoke)

        await self._call(acc_id, run)
        self._stale.add(acc_id)

    async def mark_read(self, acc_id: int, chat: Chat) -> None:
        async def run(c: TelegramClient):
            await c.send_read_acknowledge(await self._peer(c, chat.id))

        await self._call(acc_id, run)
        chat.unread = 0

    async def search(self, acc_id: int, query: str) -> list[Chat]:
        """По названиям чатов + глобальный поиск по тексту сообщений аккаунта."""
        chats = await self.dialogs(acc_id)
        ql = query.lower()
        found = [c.id for c in chats if ql in c.title.lower()]

        async def run(c: TelegramClient):
            return [m.chat_id async for m in c.iter_messages(None, search=query, limit=60)]

        for cid in await self._call(acc_id, run):
            if cid not in found:
                found.append(cid)
        by_id = {c.id: c for c in chats}
        return [by_id[i] for i in found if i in by_id][:48]

    async def contacts(self, acc_id: int) -> list[Person]:
        res = await self._call(acc_id, lambda c: c(GetContactsRequest(hash=0)))
        users = [u for u in getattr(res, "users", []) if not getattr(u, "deleted", False)]
        people = [Person(u.id, utils.get_display_name(u) or "Без имени", u.username or "", "bot" if u.bot else "user")
                  for u in users]
        return sorted(people, key=lambda p: p.name.lower())

    async def resolve(self, acc_id: int, username: str) -> Person:
        """@username → кто это. Запрос resolveUsername у Telegram строго лимитирован — только по явному вводу."""
        async def run(c: TelegramClient):
            try:
                return await c.get_entity(username)
            except (ValueError, UsernameNotOccupiedError, UsernameInvalidError):
                return None

        ent = await self._call(acc_id, run)
        if ent is None:
            raise HubError(f"@{username} не найден — проверьте, нет ли опечатки")
        if isinstance(ent, types.User):
            return Person(ent.id, utils.get_display_name(ent) or username, ent.username or username,
                          "bot" if ent.bot else "user")
        kind = "group" if isinstance(ent, types.Chat) or getattr(ent, "megagroup", False) else "channel"
        return Person(utils.get_peer_id(ent), utils.get_display_name(ent) or username,
                      getattr(ent, "username", None) or username, kind)

    async def first_contacts_left(self, acc_id: int) -> int | None:
        """Сколько новых переписок аккаунт ещё может начать за сутки. None — без лимита."""
        limit = self.cfg.new_chats_per_day
        if limit <= 0:
            return None
        return max(limit - await self.db.first_contacts_since(acc_id, time.time() - 86400), 0)

    # ─── Действия ──────────────────────────────────────────────────────────

    async def _send(self, acc_id: int, peer_of, text: str) -> None:
        """Не чаще 1 сообщения в 2 с на аккаунт. peer_of(client) → куда слать."""
        lock = self._send_locks.setdefault(acc_id, asyncio.Lock())
        async with lock:
            wait = self._last_send.get(acc_id, 0.0) + SEND_GAP - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)

            async def run(c: TelegramClient):
                await c.send_message(await peer_of(c), text)

            try:
                await self._call(acc_id, run)
            finally:
                self._last_send[acc_id] = time.monotonic()
        self._stale.add(acc_id)

    async def send(self, acc_id: int, chat: Chat, text: str) -> None:
        """Ответ в существующий диалог."""
        await self._send(acc_id, lambda c: self._peer(c, chat.id), text)

    async def send_first(self, acc_id: int, person: Person, text: str) -> None:
        """Первое сообщение человеку, с которым у аккаунта ещё нет переписки."""
        async def peer_of(c: TelegramClient):
            try:
                return await c.get_input_entity(person.id)
            except ValueError:
                if not person.username:
                    raise HubError("Не получилось найти этого человека заново — откройте его через поиск") from None
                return await c.get_input_entity(person.username)

        await self._send(acc_id, peer_of, text)
        self._dialogs.pop(acc_id, None)  # новая переписка должна сразу появиться в списках

    async def set_enabled(self, acc_id: int, enabled: bool) -> None:
        acc = self.accs[acc_id]
        acc.enabled = enabled
        await self.db.update_account(acc_id, enabled=int(enabled))
        if enabled:
            if acc.authorized and acc_id not in self.clients:
                await self._connect(acc_id)
        else:
            client = self.clients.pop(acc_id, None)
            acc.connected = False
            self._dialogs.pop(acc_id, None)
            if client:
                with suppress(Exception):
                    await client.disconnect()

    async def logout(self, acc_id: int) -> None:
        """Завершить сессию в самом Telegram. Аккаунт остаётся в панели со статусом «Нужен вход»."""
        acc = self.accs[acc_id]
        client = self.clients.pop(acc_id, None)
        if client:
            with suppress(Exception):
                await client.log_out()
        acc.authorized = acc.connected = False
        self._dialogs.pop(acc_id, None)
        await self.db.update_account(acc_id, session=None)

    async def remove(self, acc_id: int) -> None:
        await self.logout(acc_id)
        await self.db.delete_account(acc_id)
        self.accs.pop(acc_id, None)

    async def adopt(self, client: TelegramClient, phone: str, relogin_id: int | None) -> tuple[Account, str]:
        """Клиент только что вошёл — сохранить сессию и взять в работу.

        Возвращает (аккаунт, итог): "relogin" — перелогин, "updated" — такой аккаунт уже был, "added" — новый.
        """
        me = await client.get_me()
        same = next((a for a in self.accs.values() if a.tg_id == me.id), None)
        if relogin_id is not None:
            acc = self.accs.get(relogin_id)
            if acc is None or (same and same.id != relogin_id) or (acc.tg_id and acc.tg_id != me.id):
                with suppress(Exception):
                    await client.log_out()
                raise HubError("Вход выполнен в другой аккаунт — сессия закрыта, в панели ничего не изменилось")
            note = "relogin"
        elif same:
            acc, note = same, "updated"
        else:
            name = utils.get_display_name(me) or "Без имени"
            acc_id = await self.db.add_account(me.id, name, me.username, self.box.seal(phone))
            acc = Account(acc_id, me.id, name, me.username or "", phone, True)
            self.accs[acc_id] = acc
            note = "added"
        old = self.clients.pop(acc.id, None)
        if old and old is not client:
            with suppress(Exception):
                await old.disconnect()
        acc.phone, acc.enabled = phone, True
        await self.db.update_account(acc.id, session=self.box.seal(client.session.save()),
                                     phone=self.box.seal(phone), enabled=1)
        await self._attach(acc, client)
        return acc, note
