"""Подделки для стенда: «Telegram» для Telethon-клиентов и Bot API для aiogram. Сеть не нужна."""
import itertools
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from types import SimpleNamespace

from aiogram.client.session.base import BaseSession
from aiogram.types import Chat as BotChat
from aiogram.types import File, Message, PhotoSize, User as BotUser
from telethon import errors, types

NOW = datetime.now(timezone.utc)
ids = itertools.count(1000)


def fmsg(**kw):
    base = dict(action=None, photo=None, sticker=None, voice=None, video_note=None, gif=None, video=None,
                audio=None, poll=None, geo=None, contact=None, document=None, file=None, message="",
                out=False, sender=None, post_author=None, chat_id=0, date=NOW, id=next(ids))
    base.update(kw)
    return SimpleNamespace(**base)


def person(uid: int, name: str) -> types.User:
    return types.User(id=uid, first_name=name)


class FakeChat:
    def __init__(self, cid: int, title: str, kind: str, msgs: list, unread: int = 0, muted: bool = False, members: int = 0):
        self.id, self.title, self.kind, self.msgs, self.unread, self.members = cid, title, kind, msgs, unread, members
        self.mute_until = NOW + timedelta(days=365) if muted else None
        self.unread_mark = False


class FakeAccount:
    def __init__(self, tg_id: int, phone: str, name: str, username: str, password: str | None, chats: list[FakeChat],
                 contacts: list[tuple[int, str]]):
        self.tg_id, self.phone, self.name, self.username, self.password = tg_id, phone, name, username, password
        self.chats = {c.id: c for c in chats}
        self.contacts = contacts


def build_account(tg_id: int, phone: str, name: str, username: str, password: str | None, seed: int) -> FakeAccount:
    people = [(tg_id * 100 + i, n) for i, n in enumerate(["Иван", "Мама", "Дима", "Катя", "Никита", "Лёша", "Аня", "Паша", "Оля", "Егор"])]
    chats = []
    for i, (uid, n) in enumerate(people):
        sender = person(uid, n)
        lines = [(False, "Привет, как дела?"), (True, "Норм, ты как?"), (False, "Завтра в силе? Роутер возьми")]
        if i == 0:
            lines += [(False, "Очень длинное сообщение " + "бла " * 400)] * 12  # проверка лимита 4096
        msgs = [fmsg(id=k + 1, out=me, message=t, sender=None if me else sender, chat_id=uid,
                     date=NOW - timedelta(minutes=60 - k)) for k, (me, t) in enumerate(lines)]
        if i == 1:
            msgs.append(fmsg(id=99, photo=True, message="смотри", sender=sender, chat_id=uid))
            msgs.append(fmsg(id=100, voice=True, sender=sender, chat_id=uid))
            msgs.append(fmsg(id=101, sticker=True, file=SimpleNamespace(emoji="😀", mime_type="application/x-tgsticker"),
                             sender=sender, chat_id=uid))
        chats.append(FakeChat(uid, n, "user", msgs, unread=(i + seed) % 3))
    for j, title in enumerate(["Factorio", "11 «А»", "Developers"]):
        gid = -(tg_id * 10 + j)
        who = [person(p[0], p[1]) for p in people[:3]]
        msgs = [fmsg(id=k + 1, message=f"сообщение {k} в {title}", sender=who[k % 3], chat_id=gid,
                     date=NOW - timedelta(minutes=30 - k)) for k in range(25)]
        msgs.append(fmsg(id=50, poll=SimpleNamespace(poll=SimpleNamespace(question=SimpleNamespace(text="Кто идёт?"))),
                         sender=who[0], chat_id=gid))
        chats.append(FakeChat(gid, title, "group", msgs, unread=j * 2, members=10 + j))
    cid = -(1000000000000 + tg_id)
    chats.append(FakeChat(cid, "Техно-дайджест", "channel",
                          [fmsg(id=1, message="Вышла новая версия Python", chat_id=cid)], unread=4, muted=True, members=900))
    contacts = people[:6] + [(tg_id * 100 + 77, "Контакт без диалога")]  # у всех контактов username = n<id>
    return FakeAccount(tg_id, phone, name, username, password, chats, contacts)


class FakeWorld:
    CODE = "12345"

    def __init__(self) -> None:
        self.accounts = {
            "+70000000001": build_account(501, "+70000000001", "Алекс", "alex_hub", None, 0),
            "+70000000002": build_account(502, "+70000000002", "Макс", "", "secret-2fa", 1),
        }
        self.sessions: dict[str, int] = {}   # действующие сессии → tg_id
        self.sent: list[tuple[int, int, str]] = []
        self.read: list[tuple[int, int]] = []
        self.flood_next_code = False
        self.privacy = {900002}          # эти люди принимают сообщения только от контактов
        self.peer_flood = False          # аккаунт в спам-блоке
        self.deleted: list[tuple[int, int, int, bool]] = []   # (tg_id, чат, сообщение, у всех)
        self.files: list[tuple[int, int, str, dict]] = []      # (tg_id, чат, имя файла, флаги send_file)
        self.forwarded: list[tuple[int, int, int, int]] = []   # (tg_id, из чата, сообщение, в чат)
        self.reactions: list[tuple[int, int, int, str | None]] = []
        self.clients: list["FakeClient"] = []
        self.photo: bytes | None = None                         # аватарка для чётных id (проверка аватарок на карточках)
        # справочник @username → (id, имя, вид); плюс у каждого чата-человека username ivan<tg> и т.п.
        self.directory = {
            "friend_new": (900001, "Новый Друг", "user"),
            "privacy_guy": (900002, "Закрытый", "user"),
            "helper_bot": (900004, "Помощник", "bot"),
            "some_channel": (9000003, "Какой-то канал", "channel"),
        }

    def lookup(self, username: str, acc: "FakeAccount"):
        username = username.lstrip("@").lower()
        if username in self.directory:
            return self.directory[username]
        if username == f"ivan{acc.tg_id}":
            return (acc.tg_id * 100, "Иван", "user")
        for uid, name in acc.contacts:
            if username == f"n{uid}":
                return (uid, name, "user")
        return None

    async def incoming(self, tg_id: int, chat_id: int, text: str, mentioned: bool = False, sender_id: int | None = None):
        """Новое входящее сообщение: кладём в чат и будим обработчики NewMessage живых клиентов этого аккаунта."""
        acc = self.by_id(tg_id)
        chat = acc.chats[chat_id]
        uid = sender_id or (chat_id if chat.kind == "user" else tg_id * 100 + 2)
        sender = person(uid, chat.title if chat.kind == "user" else "Дима")
        m = fmsg(message=text, sender=sender, chat_id=chat_id, mentioned=mentioned, date=datetime.now(timezone.utc))
        chat.msgs.append(m)
        chat.unread += 1

        async def get_sender():
            return sender

        async def get_chat():
            return types.Chat(id=abs(chat_id), title=chat.title, photo=types.ChatPhotoEmpty(), participants_count=chat.members,
                              date=None, version=1)

        event = SimpleNamespace(out=False, is_private=chat.kind == "user", is_group=chat.kind == "group",
                                is_channel=chat.kind != "user", chat_id=chat_id, message=m,
                                get_sender=get_sender, get_chat=get_chat)
        for c in self.clients:
            if self.sessions.get(c.session.s) == tg_id:
                for cb, kind in c.handlers:
                    if kind == "NewMessage":
                        await cb(event)

    def by_id(self, tg_id: int) -> FakeAccount:
        return next(a for a in self.accounts.values() if a.tg_id == tg_id)

    def revoke(self, tg_id: int) -> None:
        for s in [s for s, t in self.sessions.items() if t == tg_id]:
            del self.sessions[s]


class FakeSession:
    def __init__(self, s: str) -> None:
        self.s = s

    def save(self) -> str:
        return self.s


class FakeClient:
    def __init__(self, world: FakeWorld, session: str = "") -> None:
        self.world = world
        self.session = FakeSession(session)
        self._connected = False
        self._pending: FakeAccount | None = None
        self._known: dict[int, str] = {}   # кого клиент уже «видел» (как кэш сущностей Telethon)
        self.handlers = []
        world.clients.append(self)

    # связь и вход
    async def connect(self):
        self._connected = True

    async def disconnect(self):
        self._connected = False

    def is_connected(self):
        return self._connected

    def _me(self) -> FakeAccount:
        tg_id = self.world.sessions.get(self.session.s)
        if tg_id is None:
            raise errors.AuthKeyUnregisteredError(request=None)
        return self.world.by_id(tg_id)

    async def is_user_authorized(self):
        return self.session.s in self.world.sessions

    async def send_code_request(self, phone, **kw):
        if self.world.flood_next_code:
            self.world.flood_next_code = False
            raise errors.FloodWaitError(request=None, capture=754)
        if phone not in self.world.accounts:
            raise errors.PhoneNumberInvalidError(request=None)
        self._pending = self.world.accounts[phone]
        return SimpleNamespace(type=types.auth.SentCodeTypeApp(length=5), phone_code_hash="hash")

    async def sign_in(self, phone=None, code=None, *, password=None, phone_code_hash=None):
        acc = self._pending
        if password is None:
            if str(code) != FakeWorld.CODE:
                raise errors.PhoneCodeInvalidError(request=None)
            if acc.password:
                raise errors.SessionPasswordNeededError(request=None)
        elif password != acc.password:
            raise errors.PasswordHashInvalidError(request=None)
        self.session.s = f"sess-{acc.tg_id}-{next(ids)}"
        self.world.sessions[self.session.s] = acc.tg_id

    async def log_out(self):
        self.world.sessions.pop(self.session.s, None)
        self._connected = False

    async def get_me(self):
        a = self._me()
        return types.User(id=a.tg_id, first_name=a.name, username=a.username or None, is_self=True)

    async def download_profile_photo(self, entity, file=None, download_big=True):
        if isinstance(entity, int) and entity % 2 == 0:
            return self.world.photo
        return None

    def add_event_handler(self, cb, event):
        self.handlers.append((cb, type(event).__name__))

    # чтение
    async def get_dialogs(self, limit=None):
        a = self._me()
        out = []
        for c in sorted(a.chats.values(), key=lambda c: -c.msgs[-1].date.timestamp()):
            ent = person(c.id, c.title) if c.kind == "user" else SimpleNamespace(participants_count=c.members)
            out.append(SimpleNamespace(
                id=c.id, name=c.title, entity=ent, is_user=c.kind == "user", is_group=c.kind == "group",
                is_channel=c.kind != "user", unread_count=c.unread, message=c.msgs[-1],
                dialog=SimpleNamespace(notify_settings=SimpleNamespace(mute_until=c.mute_until), unread_mark=c.unread_mark)))
        return out[:limit]

    async def get_entity(self, username):
        a = self._me()
        found = self.world.lookup(username, a)
        if not found:
            raise ValueError(f'No user has "{username}" as username')
        uid, name, kind = found
        self._known[uid] = name
        if kind == "channel":
            return types.Channel(id=uid, title=name, photo=types.ChatPhotoEmpty(), date=None, broadcast=True,
                                 username=username.lstrip("@"))
        return types.User(id=uid, first_name=name, username=username.lstrip("@"), bot=kind == "bot")

    async def get_input_entity(self, peer):
        a = self._me()
        if isinstance(peer, str):
            found = self.world.lookup(peer, a)
            if not found:
                raise ValueError("Could not find the input entity")
            self._known[found[0]] = found[1]
            return found[0]
        if peer not in a.chats and peer not in self._known:
            raise ValueError("Could not find the input entity")
        return peer

    async def get_messages(self, peer, limit=None, ids=None):
        msgs = self._me().chats[peer].msgs
        if ids is not None:
            return next((m for m in msgs if m.id == ids), None)
        return list(reversed(msgs))[:limit]

    async def delete_messages(self, peer, message_ids, revoke=True):
        a = self._me()
        chat = a.chats[peer]
        chat.msgs = [m for m in chat.msgs if m.id not in message_ids]
        if not chat.msgs:  # пустой чат в фейке не нужен — оставим служебную строку
            chat.msgs.append(fmsg(message="(история очищена)", chat_id=peer))
        self.world.deleted += [(a.tg_id, peer, i, revoke) for i in message_ids]

    async def download_media(self, message, file=None):
        kind = next(k for k in ("sticker", "photo", "voice", "video_note", "gif", "video", "audio", "document")
                    if getattr(message, k, None))
        return b"FAKE-" + kind.encode()

    async def send_file(self, peer, file, caption=None, **flags):
        a = self._me()
        name = getattr(file, "name", "file")
        kind = "voice" if flags.get("voice_note") else "photo" if name.endswith(".jpg") else "document"
        a.chats[peer].msgs.append(fmsg(out=True, message=caption or "", chat_id=peer, date=datetime.now(timezone.utc),
                                       **{kind: True}))
        self.world.files.append((a.tg_id, peer, name, {k: v for k, v in flags.items() if v} | {"caption": caption}))

    async def forward_messages(self, entity, messages, from_peer):
        a = self._me()
        src = next(m for m in a.chats[from_peer].msgs if m.id == messages)
        a.chats[entity].msgs.append(fmsg(out=True, message=src.message, chat_id=entity, date=datetime.now(timezone.utc)))
        self.world.forwarded.append((a.tg_id, from_peer, messages, entity))

    async def send_read_acknowledge(self, peer):
        a = self._me()
        a.chats[peer].unread = 0
        self.world.read.append((a.tg_id, peer))

    async def send_message(self, peer, text):
        a = self._me()
        if peer not in a.chats:  # первое сообщение новому человеку
            if self.world.peer_flood:
                raise errors.PeerFloodError(request=None)
            if peer in self.world.privacy:
                raise errors.UserPrivacyRestrictedError(request=None)
            a.chats[peer] = FakeChat(peer, self._known.get(peer, "Новый собеседник"), "user", [])
        a.chats[peer].msgs.append(fmsg(id=next(ids), out=True, message=text, chat_id=peer,
                                       date=datetime.now(timezone.utc)))
        self.world.sent.append((a.tg_id, peer, text))

    async def iter_messages(self, entity, search=None, limit=None):
        a = self._me()
        for c in a.chats.values():
            for m in c.msgs:
                if search and search.lower() in (m.message or "").lower():
                    yield m

    async def __call__(self, request):
        a = self._me()
        name = type(request).__name__
        if name == "SendReactionRequest":
            emoji = request.reaction[0].emoticon if request.reaction else None
            self.world.reactions.append((a.tg_id, request.peer, request.msg_id, emoji))
            return True
        if name == "MarkDialogUnreadRequest":
            a.chats[request.peer.peer].unread_mark = request.unread
            return True
        self._known.update(dict(a.contacts))  # GetContactsRequest
        return SimpleNamespace(users=[types.User(id=uid, first_name=name, username=f"n{uid}") for uid, name in a.contacts])


# ─── Bot API ────────────────────────────────────────────────────────────────

ALLOWED_TAGS = {"b", "i", "u", "s", "code", "pre", "a", "tg-spoiler", "blockquote"}


class TagCheck(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack, self.text, self.bad = [], [], []

    def handle_starttag(self, tag, attrs):
        if tag not in ALLOWED_TAGS:
            self.bad.append(f"<{tag}>")
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            self.bad.append(f"</{tag}>")

    def handle_data(self, data):
        self.text.append(data)


def check_html(s: str, limit: int) -> list[str]:
    p = TagCheck()
    p.feed(s)
    p.close()
    problems = list(p.bad) + [f"unclosed <{t}>" for t in p.stack]
    plain = "".join(p.text)
    if len(plain) > limit:
        problems.append(f"length {len(plain)} > {limit}")
    return problems


class FakeBotSession(BaseSession):
    """Отвечает как Bot API и проверяет то, на чём падает настоящий Telegram: длины, HTML, callback_data ≤ 64 байт."""

    def __init__(self) -> None:
        super().__init__()
        self.mid = itertools.count(1)
        self.last: dict[int, SimpleNamespace] = {}     # chat → последний экран
        self.notifications: list[SimpleNamespace] = []  # сообщения не-экраны (уведомления админам)
        self.answers: dict[str, list[str | None]] = {}
        self.problems: list[str] = []
        self.deleted: list[tuple[int, int]] = []
        self.media: list[SimpleNamespace] = []          # присланные стикеры/фото

    async def close(self):
        pass

    async def stream_content(self, *a, **kw):
        yield b"FAKE-UPLOAD"

    def _check_markup(self, markup):
        if not markup:
            return
        for row in markup.inline_keyboard:
            for b in row:
                if not b.text:
                    self.problems.append("empty button text")
                if b.callback_data is None or len(b.callback_data.encode()) > 64:
                    self.problems.append(f"bad callback_data {b.callback_data!r}")

    def _screen(self, chat_id, mid, kind, text, markup) -> Message:
        limit = 1024 if kind == "photo" else 4096
        for p in check_html(text or "", limit):
            self.problems.append(f"{kind}: {p}: {text[:80]!r}")
        self._check_markup(markup)
        self.last[chat_id] = SimpleNamespace(mid=mid, kind=kind, text=text, markup=markup)
        photo = [PhotoSize(file_id=f"file{mid}", file_unique_id=f"u{mid}", width=1280, height=640)] if kind == "photo" else None
        return Message(message_id=mid, date=datetime.now(), chat=BotChat(id=chat_id, type="private"),
                       photo=photo, caption=text if kind == "photo" else None, text=text if kind == "text" else None,
                       reply_markup=markup)

    async def make_request(self, bot, method, timeout=None):
        name = type(method).__name__
        markup = getattr(method, "reply_markup", None)
        is_media = name.startswith("Send") and name != "SendMessage" and markup and any(
            b.callback_data == "hide" for row in markup.inline_keyboard for b in row)
        if is_media:  # стикер/фото из переписки — отдельное сообщение под экраном, не сам экран
            mid = next(self.mid)
            field = next(f for f in ("sticker", "photo", "voice", "video_note", "video", "animation", "audio", "document")
                         if getattr(method, f, None) is not None)
            self.media.append(SimpleNamespace(chat=method.chat_id, mid=mid, kind=name,
                                              filename=getattr(getattr(method, field), "filename", "")))
            self._check_markup(markup)
            return Message(message_id=mid, date=datetime.now(), chat=BotChat(id=method.chat_id, type="private"))
        if name == "SendPhoto":
            return self._screen(method.chat_id, next(self.mid), "photo", method.caption, method.reply_markup)
        if name == "EditMessageMedia":
            return self._screen(method.chat_id, method.message_id, "photo", method.media.caption, method.reply_markup)
        if name == "SendMessage":
            self.notifications.append(SimpleNamespace(chat=method.chat_id, text=method.text))
            return self._screen(method.chat_id, next(self.mid), "text", method.text, method.reply_markup)
        if name == "EditMessageText":
            return self._screen(method.chat_id, method.message_id, "text", method.text, method.reply_markup)
        if name == "AnswerCallbackQuery":
            self.answers.setdefault(method.callback_query_id, []).append(method.text)
            if len(self.answers[method.callback_query_id]) > 1:
                self.problems.append(f"callback answered twice: {self.answers[method.callback_query_id]}")
            return True
        if name == "DeleteMessage":
            self.deleted.append((method.chat_id, method.message_id))
            return True
        if name == "GetFile":
            return File(file_id=method.file_id, file_unique_id="u" + method.file_id, file_size=11, file_path="docs/file.bin")
        if name == "GetMe":
            return BotUser(id=42, is_bot=True, first_name="Hub", username="hub_test_bot")
        return True
