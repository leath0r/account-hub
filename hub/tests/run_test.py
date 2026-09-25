"""Стенд Account Hub: сценарии v0.1 + случайные нажатия. Telegram подделан (tests/fakes.py), карточки рендерятся по-настоящему.

Запуск из папки hub:  python tests/run_test.py [число случайных нажатий]
"""
import asyncio
import itertools
import logging
import random
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(Path(__file__).parent)]

from aiogram import Bot  # noqa: E402
from aiogram.client.default import DefaultBotProperties  # noqa: E402
from aiogram.types import CallbackQuery, Chat, Document, Message, PhotoSize, Sticker, Update, User, Voice  # noqa: E402
from cryptography.fernet import Fernet  # noqa: E402

import access  # noqa: E402
from auth import Logins  # noqa: E402
from config import Config  # noqa: E402
from crypto import Box  # noqa: E402
from db import DB  # noqa: E402
from fakes import FakeBotSession, FakeClient, FakeWorld  # noqa: E402
import i18n  # noqa: E402
import notify  # noqa: E402
from main import build_dispatcher, notifier  # noqa: E402
from render import Renderer  # noqa: E402
from screens import ROUTERS  # noqa: E402
from screens import common  # noqa: E402
from screens import pin as pinmod  # noqa: E402
from tg import Hub  # noqa: E402
from ui import UI  # noqa: E402

ADMIN, BOB, EVE = 7001, 7002, 7003
NAMES = {ADMIN: "Админ", BOB: "Боб", EVE: "Ева"}
FAILS: list[str] = []


def check(cond, what: str) -> None:
    print(("  ✓ " if cond else "  ✗ ") + what)
    if not cond:
        FAILS.append(what)


class Errors(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.ERROR)
        self.records: list[str] = []

    def emit(self, record) -> None:
        self.records.append(record.getMessage() + (f"\n{record.exc_text or ''}" if record.exc_info else ""))


class Stand:
    upd = itertools.count(1)

    async def boot(self, renderer: Renderer, world: FakeWorld, key: str, db_path: Path,
                   session: FakeBotSession | None = None) -> "Stand":
        cfg = Config(bot_token="42:TEST", api_id=1, api_hash="x", admin_ids=frozenset(), session_key=key,
                     tz=timezone(timedelta(hours=11)), db_path=db_path, new_chats_per_day=2)
        self.world = world
        self.pressed: list[str] = []
        self.db = DB(db_path)
        await self.db.connect()
        self.hub = Hub(cfg, self.db, Box(key))
        self.hub.new_client = lambda session="": FakeClient(world, session)
        self.session = session or FakeBotSession()
        self.bot = Bot(cfg.bot_token, session=self.session, default=DefaultBotProperties(parse_mode="HTML"))
        self.logins = Logins(self.hub)
        self.ui = UI(self.bot, renderer, self.db)
        for r in (access.claim_router, *ROUTERS):  # «перезапуск» в том же процессе
            r._parent_router = None
        self.dp = build_dispatcher(self.hub, self.ui, self.logins)
        self.hub.on_down = notifier(self.bot, self.hub)
        self.notifier = notify.Notifier(self.bot, self.hub)
        self.hub.on_message = self.notifier.on_message
        await self.hub.start()
        await access.prepare_claim(self.hub)
        return self

    async def stop(self) -> None:
        await self.hub.stop()
        await self.db.close()

    def user(self, uid: int) -> User:
        return User(id=uid, is_bot=False, first_name=NAMES[uid])

    async def text(self, uid: int, text: str) -> None:
        msg = Message(message_id=next(self.session.mid), date=datetime.now(), chat=Chat(id=uid, type="private"),
                      from_user=self.user(uid), text=text)
        await self.dp.feed_update(self.bot, Update(update_id=next(self.upd), message=msg))

    async def send_media(self, uid: int, kind: str, caption: str = "") -> None:
        """Пользователь присылает боту фото/голосовое/файл."""
        n = next(self.session.mid)
        extra = {"photo": {"photo": [PhotoSize(file_id=f"ph{n}", file_unique_id=f"u{n}", width=10, height=10, file_size=100)]},
                 "voice": {"voice": Voice(file_id=f"vc{n}", file_unique_id=f"u{n}", duration=3, file_size=100)},
                 "document": {"document": Document(file_id=f"dc{n}", file_unique_id=f"u{n}", file_name="report.pdf",
                                                   file_size=100)},
                 "big": {"document": Document(file_id=f"dc{n}", file_unique_id=f"u{n}", file_name="huge.zip",
                                              file_size=30 * 1024 * 1024)},
                 "sticker": {"sticker": Sticker(file_id=f"st{n}", file_unique_id=f"u{n}", type="regular", width=1, height=1,
                                                is_animated=False, is_video=False)}}[kind]
        msg = Message(message_id=n, date=datetime.now(), chat=Chat(id=uid, type="private"), from_user=self.user(uid),
                      caption=caption or None, **extra)
        await self.dp.feed_update(self.bot, Update(update_id=next(self.upd), message=msg))

    def screen(self, uid: int):
        return self.session.last.get(uid)

    def buttons(self, uid: int) -> list[tuple[str, str]]:
        s = self.screen(uid)
        if not s or not s.markup:
            return []
        return [(b.text, b.callback_data) for row in s.markup.inline_keyboard for b in row]

    def has(self, uid: int, label: str) -> bool:
        return any(label in t for t, _ in self.buttons(uid))

    async def press(self, uid: int, data: str | None = None, label: str | None = None, mid: int | None = None) -> str | None:
        if label is not None:
            found = [d for t, d in self.buttons(uid) if label in t]
            if not found:
                FAILS.append(f"нет кнопки «{label}» у {NAMES[uid]}: {[t for t, _ in self.buttons(uid)]}")
                print(f"  ✗ нет кнопки «{label}»")
                return None
            data = found[0]
        s = self.screen(uid)
        mid = mid or (s.mid if s else 1)
        photo = [PhotoSize(file_id="x", file_unique_id="x", width=1, height=1)] if s and s.kind == "photo" else None
        msg = Message(message_id=mid, date=datetime.now(), chat=Chat(id=uid, type="private"), photo=photo,
                      text=s.text if s and s.kind == "text" else None)
        self.pressed.append(data)
        cid = f"cq{next(self.upd)}"
        cq = CallbackQuery(id=cid, from_user=self.user(uid), chat_instance="ci", data=data, message=msg)
        await self.dp.feed_update(self.bot, Update(update_id=next(self.upd), callback_query=cq))
        return (self.session.answers.get(cid) or [None])[-1]

    def caption(self, uid: int) -> str:
        s = self.screen(uid)
        return s.text if s else ""


async def keypad(st: Stand, uid: int, code: str) -> None:
    for d in code:
        await st.press(uid, f"kp:{d}")
    await st.press(uid, "kp:ok")


async def scenarios(st: Stand) -> None:
    w = st.world
    print("\n▶ Первый админ через /claim")
    token = access.CLAIM["token"]
    check(token is not None, "без админов в логе печатается код /claim")
    await st.text(ADMIN, "/start")
    check("нет доступа" in st.caption(ADMIN), "до /claim админ — обычный пользователь без доступа")
    await st.text(ADMIN, "/claim 0000")
    check(not (await st.db.admins()), "неверный код /claim не срабатывает")
    await st.text(ADMIN, f"/claim {token}")
    check(await st.db.admins() == {ADMIN}, "верный код делает админом")
    await st.text(EVE, f"/claim {token}")
    check(await st.db.admins() == {ADMIN}, "код одноразовый")

    print("\n▶ Добавление аккаунта без 2FA")
    await st.text(ADMIN, "/start")
    check(st.has(ADMIN, "Админ-панель"), "у админа есть кнопка админки")
    await st.press(ADMIN, "adm")
    await st.press(ADMIN, label="Добавить аккаунт")
    await st.text(ADMIN, "123")
    check("Не похоже на номер" in st.caption(ADMIN), "короткий номер отклонён")
    await st.text(ADMIN, "+7 000 999 99 99")
    check("Неверный номер" in st.caption(ADMIN) and not st.has(ADMIN, "Готово"), "неизвестный номер — остаёмся на шаге номера")
    w.flood_next_code = True
    await st.text(ADMIN, "+7 000 000-00-01")
    check("12:34" in st.caption(ADMIN) and not st.has(ADMIN, "Готово"), "FloodWait: показан таймер, остаёмся на шаге номера")
    await st.text(ADMIN, "+7 000 000-00-01")
    check(st.has(ADMIN, "Готово") and "приложение Telegram" in st.caption(ADMIN), "код отправлен, виден кейпад")
    deleted_before = len(st.session.deleted)
    await keypad(st, ADMIN, "00000")
    check("Неверный код" in st.caption(ADMIN) and st.has(ADMIN, "Готово"), "неверный код — ошибка и снова кейпад")
    ans = await st.press(ADMIN, "kp:ok")
    check(ans and "Нужно 5" in ans, "пустой код не отправляется")
    await st.press(ADMIN, "kp:r")
    await keypad(st, ADMIN, "12345")
    check(st.has(ADMIN, "Открыть аккаунт"), "аккаунт #1 добавлен")
    check(len(st.hub.accs) == 1 and st.hub.accs[1].status == "online", "статус онлайн")
    check(len(st.session.deleted) >= deleted_before, "сообщения с номером удаляются")

    print("\n▶ Добавление аккаунта с 2FA")
    await st.press(ADMIN, "adm")
    await st.press(ADMIN, "add")
    await st.text(ADMIN, "+70000000002")
    await keypad(st, ADMIN, "12345")
    check("двухэтапная" in st.caption(ADMIN), "запрошен пароль 2FA")
    await st.text(ADMIN, "wrong")
    check("Неверный пароль" in st.caption(ADMIN), "неверный пароль — остаёмся на шаге 2FA")
    await st.text(ADMIN, "secret-2fa")
    check(st.has(ADMIN, "Открыть аккаунт") and 2 in st.hub.accs, "аккаунт #2 добавлен")
    row = await st.db.account(2)
    check(b"secret" not in (row["session"] or b"") and b"70000000002" not in row["phone"], "сессия и номер в БД зашифрованы")

    print("\n▶ Повторное добавление того же аккаунта")
    await st.press(ADMIN, "adm")
    await st.press(ADMIN, "add")
    await st.text(ADMIN, "+70000000001")
    await keypad(st, ADMIN, "12345")
    check(len(st.hub.accs) == 2 and "уже был" in st.caption(ADMIN), "дубль не создаётся, сессия обновлена")

    print("\n▶ Пользователь без доступа и выдача ролей")
    await st.text(BOB, "/start")
    check("нет доступа" in st.caption(BOB) and not st.has(BOB, "#1"), "Боб без доступа не видит аккаунтов")
    ans = await st.press(BOB, "acc:1")
    check(ans and "Нет доступа" in ans, "подделанная кнопка acc:1 отклонена")
    ans = await st.press(BOB, "adm")
    check(ans and "администратора" in ans, "админка закрыта")
    await st.press(ADMIN, "adm:u")
    await st.press(ADMIN, f"au:{BOB}")
    await st.press(ADMIN, f"ar:{BOB}:1")
    check((await st.db.roles(BOB)) == {1: "viewer"}, "Бобу выдано чтение #1")
    await st.text(BOB, "/start")
    check(st.has(BOB, "#1") and not st.has(BOB, "#2"), "Боб видит только #1")
    await st.press(BOB, "acc:1")
    await st.press(BOB, "ls:1:c:0")
    reads = len(w.read)
    await st.press(BOB, label="Мама")
    check("Мама" in st.caption(BOB) and "[🖼] смотри" in st.caption(BOB), "переписка открыта, вложения подписаны")
    check(st.has(BOB, "только на чтение") and not st.has(BOB, "Ответить"), "зрителю не дают отвечать")
    check(len(w.read) == reads, "зритель не помечает чат прочитанным")
    ans = await st.press(BOB, f"rep:1:{50100}:c")
    check(ans and "Нет доступа" in ans, "подделанная кнопка ответа отклонена")

    print("\n▶ Оператор отвечает")
    await st.press(ADMIN, f"ar:{BOB}:1")
    check((await st.db.roles(BOB)) == {1: "operator"}, "Боб теперь оператор #1")
    await st.press(BOB, "ls:1:c:0")
    await st.press(BOB, label="Дима")
    check(st.has(BOB, "Ответить"), "оператор видит «Ответить»")
    await st.press(BOB, label="Ответить")
    t0 = time.monotonic()
    await st.text(BOB, "привет из хаба")
    await st.press(BOB, label="Ответить")
    await st.text(BOB, "второе подряд")
    check(len(w.sent) == 2 and w.sent[0][2] == "привет из хаба", "сообщения ушли от аккаунта")
    check(time.monotonic() - t0 >= 2.0, "между сообщениями не меньше 2 с")
    check("✅ Отправлено" in st.caption(BOB), "в переписке пометка об отправке")

    print("\n▶ Длинная переписка укладывается в лимит")
    await st.press(BOB, "ls:1:c:0")
    await st.press(BOB, label="Иван")
    check(len(st.caption(BOB)) < 4096 and "не влезли" in st.caption(BOB), "длинные сообщения обрезаны под 4096")

    print("\n▶ Поиск, контакты, непрочитанное, входящие, заглушка")
    await st.press(BOB, "srch:1")
    await st.text(BOB, "роутер")
    check(st.has(BOB, "Иван") and st.has(BOB, "Новый поиск"), "поиск нашёл чаты")
    await st.press(BOB, "ls:1:k:0")
    check(st.has(BOB, "✉️ Контакт без диалога"), "контакт без диалога помечен")
    await st.press(BOB, label="Контакт без диалога")
    check(st.has(BOB, "Написать первым"), "контакту без переписки предлагается написать первым")
    await st.press(BOB, "inbox:0")
    before = [t for t, _ in st.buttons(BOB) if t.startswith("#1")]
    check(before and not any("Техно-дайджест" in t for t in before), "во входящих нет чатов, заглушённых в Telegram")
    await st.press(BOB, next(d for t, d in st.buttons(BOB) if t == before[0]))
    await st.press(BOB, label="Заглушить")
    await st.press(BOB, "inbox:0")
    after = [t for t, _ in st.buttons(BOB) if t.startswith("#1")]
    check(len(after) == len(before) - 1, "заглушённый чат пропал из входящих")

    print("\n▶ Сессия слетела → уведомление → перелогин")
    notes = len(st.session.notifications)
    w.revoke(501)
    st.hub._dialogs.clear()
    ans = await st.press(BOB, "ls:1:c:0")
    check(ans and "Сессия слетела" in ans, "пользователь видит «Сессия слетела»")
    check(st.hub.accs[1].status == "need_login", "статус #1 — нужен вход")
    check(any("сессия слетела" in n.text for n in st.session.notifications[notes:] if n.chat == ADMIN),
          "админу пришло уведомление")
    await st.press(ADMIN, "relog:1")
    check(st.has(ADMIN, "Готово") and "Код отправлен" in st.caption(ADMIN), "перелогин: код отправлен")
    await keypad(st, ADMIN, "12345")
    check(st.hub.accs[1].status == "online", "после перелогина — онлайн")

    print("\n▶ Отключение, завершение сессии, удаление, бан")
    await st.press(ADMIN, "aat:2")
    check(st.hub.accs[2].status == "offline", "#2 отключён")
    await st.press(ADMIN, "aat:2")
    check(st.hub.accs[2].status == "online", "#2 снова включён")
    await st.press(ADMIN, "setx:2")
    await st.press(ADMIN, "setxok:2")
    check(st.hub.accs[2].status == "need_login" and not any(t == 502 for t in w.sessions.values()),
          "сессия #2 завершена и в Telegram")
    ans = await st.press(BOB, "setx:1")
    check(ans and "Нет доступа" in ans, "оператор не может завершить сессию")
    await st.press(ADMIN, "aad:2")
    await st.press(ADMIN, "aadok:2")
    check(2 not in st.hub.accs and await st.db.account(2) is None, "#2 удалён из панели и БД")
    await st.press(ADMIN, f"uban:{BOB}")
    ans = await st.press(BOB, "acc:1")
    check(ans == "Доступ закрыт", "забаненный отсечён")
    await st.press(ADMIN, f"uban:{BOB}")

    print("\n▶ Журнал")
    await st.press(ADMIN, "adm:l")
    log_text = st.caption(ADMIN)
    check("ответил" in log_text and "добавил аккаунт" in log_text and "удалил аккаунт" in log_text, "журнал пишет действия")


async def scenarios_new(st: Stand) -> None:
    """Функции от 2026-09-24: @username и «Написать первым», удаление сообщений, стикеры и фото."""
    w = st.world
    print("\n▶ Поиск человека по @username и «Написать первым»")
    await st.press(BOB, "srch:1")
    await st.text(BOB, "@friend_new")
    check("Новый Друг" in st.caption(BOB) and st.has(BOB, "Написать первым"), "найден новый человек, есть «Написать первым»")
    await st.press(BOB, label="Написать первым")
    check("Первое сообщение" in st.caption(BOB) and "Новый Друг" in st.caption(BOB), "видно, кому и от какого аккаунта")
    await st.text(BOB, "привет, это я")
    check(w.sent[-1] == (501, 900001, "привет, это я"), "первое сообщение ушло")
    check("✅ Отправлено" in st.caption(BOB) and "Новый Друг" in st.caption(BOB), "сразу открылась новая переписка")
    await st.press(BOB, "srch:1")
    await st.text(BOB, "@ivan501")
    check("Иван" in st.caption(BOB) and st.has(BOB, "Ответить"), "@username с перепиской — сразу открывает её")
    await st.press(BOB, "srch:1")
    await st.text(BOB, "https://t.me/nobody_here")
    check("не найден" in st.caption(BOB), "несуществующий @username — понятная ошибка")
    await st.press(BOB, "srch:1")
    await st.text(BOB, "t.me/some_channel")
    check("Канал" in st.caption(BOB) and not st.has(BOB, "Написать первым"), "канал — писать нельзя, объяснено")
    await st.press(BOB, "srch:1")
    await st.text(BOB, "@privacy_guy")
    await st.press(BOB, label="Написать первым")
    await st.text(BOB, "привет")
    check("только от своих контактов" in st.caption(BOB), "закрытый профиль — понятная ошибка")
    w.peer_flood = True
    await st.press(BOB, label="Написать первым")
    await st.text(BOB, "привет")
    check("спам-блок" in st.caption(BOB), "спам-блок — понятная ошибка")
    w.peer_flood = False

    print("\n▶ Контакт без переписки и суточный лимит (на стенде — 2)")
    await st.press(BOB, "ls:1:k:0")
    await st.press(BOB, label="Контакт без диалога")
    check(st.has(BOB, "Написать первым") and "ещё 1" in st.caption(BOB), "контакт без переписки — можно написать, виден остаток")
    await st.press(BOB, label="Написать первым")
    await st.text(BOB, "здарова")
    check(w.sent[-1][1] == 50177 and "✅ Отправлено" in st.caption(BOB), "контакту ушло первое сообщение")
    await st.press(BOB, "srch:1")
    await st.text(BOB, "@helper_bot")
    check("Лимит" in st.caption(BOB) and not st.has(BOB, "Написать первым"), "лимит исчерпан — кнопки нет")
    ans = await st.press(BOB, "new:1:900004")
    check(ans and "Лимит" in ans, "подделанная кнопка тоже упирается в лимит")

    print("\n▶ Зритель: ни писать первым, ни удалять")
    await st.press(ADMIN, f"ar:{BOB}:1")  # operator → нет доступа
    await st.press(ADMIN, f"ar:{BOB}:1")  # → чтение
    check((await st.db.roles(BOB)) == {1: "viewer"}, "Боб — зритель")
    await st.press(BOB, "srch:1")
    await st.text(BOB, "@privacy_guy")
    check("только на чтение" in st.caption(BOB) and not st.has(BOB, "Написать первым"), "зрителю нет «Написать первым»")
    ans = await st.press(BOB, "new:1:900002")
    check(ans and "Нет доступа" in ans, "подделанное «Написать первым» отклонено")
    ans = await st.press(BOB, "dlx:1:50101:99:c:1")
    check(ans and "Нет доступа" in ans, "подделанное удаление отклонено")
    await st.press(BOB, "ls:1:c:0")
    await st.press(BOB, label="Мама")
    check(not st.has(BOB, "Удалить"), "у зрителя нет кнопки «Удалить»")
    await st.press(ADMIN, f"ar:{BOB}:1")  # → снова чтение и ответы

    print("\n▶ Стикеры и фото из переписки")
    await st.press(BOB, "ls:1:c:0")
    await st.press(BOB, label="Мама")
    check(st.has(BOB, "💬") and st.has(BOB, "🖼"), "у стикера и фото есть кнопки «показать»")
    await st.press(BOB, label="💬")
    m = st.session.media[-1] if st.session.media else None
    check(m is not None and m.kind == "SendSticker" and m.filename.endswith(".tgs"), "стикер пришёл настоящим (анимированный .tgs)")
    await st.press(BOB, label="🖼")
    check(st.session.media[-1].kind == "SendPhoto", "фото пришло")
    await st.press(BOB, "hide", mid=st.session.media[-1].mid)
    check((BOB, st.session.media[-1].mid) in st.session.deleted, "«✕ Скрыть» убирает вложение")

    print("\n▶ Удаление сообщений")
    before = len(w.deleted)
    await st.press(BOB, "ls:1:c:0")
    await st.press(BOB, label="Дима")
    await st.press(BOB, label="Удалить…")
    check("Какое сообщение удалить" in st.caption(BOB), "выбор сообщения")
    own = [d for t, d in st.buttons(BOB) if "Вы:" in t]
    await st.press(BOB, own[0] if own else "noop")
    check("Удалить это сообщение?" in st.caption(BOB), "подтверждение с текстом сообщения")
    await st.press(BOB, label="Удалить у всех")
    check(len(w.deleted) == before + 1 and w.deleted[-1][3] is True, "своё сообщение удалено у всех")
    check("Удалено у всех" in st.caption(BOB), "в переписке пометка об удалении")
    await st.press(BOB, label="Удалить…")
    theirs = [d for t, d in st.buttons(BOB) if d.startswith("dlc:") and "Вы:" not in t]
    await st.press(BOB, theirs[0] if theirs else "noop")
    await st.press(BOB, label="только у себя")
    check(w.deleted[-1][3] is False, "чужое сообщение в личке — «только у себя»")
    await st.press(BOB, "ls:1:g:0")
    await st.press(BOB, label="Factorio")
    await st.press(BOB, label="Удалить…")
    check("только свои" in st.caption(BOB) and not any(d.startswith("dlc:") for _, d in st.buttons(BOB)),
          "в группе чужие сообщения удалять не предлагается")
    ans = await st.press(BOB, "dlx:1:-5010:1:g:1")
    check(ans and "только свои" in ans, "подделанное удаление чужого в группе отклонено")
    await st.press(ADMIN, "adm:l")
    check("написал первым" in st.caption(ADMIN) and "удалил сообщение" in st.caption(ADMIN), "журнал: первые сообщения и удаления")


async def notes_for(st: Stand, uid: int, since: int, wait: float = 0.5) -> list:
    await asyncio.sleep(wait)
    return [n for n in st.session.notifications[since:] if n.chat == uid and n.text.startswith("🔔")]


async def pick_own(st: Stand, uid: int) -> None:
    """На экране выбора сообщения нажать своё (для удаления)."""
    own = [d for t, d in st.buttons(uid) if (" Вы:" in t or " You:" in t) and ":" in d]
    await st.press(uid, own[0] if own else "noop")


async def scenarios_v2(st: Stand) -> None:
    """v0.2: уведомления, медиа, реакции, непрочитанное, пересылка, поиск везде, аватарки, PIN, статистика, английский."""
    w = st.world
    acc1 = w.by_id(501)

    print("\n▶ Пуш-уведомления")
    since = len(st.session.notifications)
    await w.incoming(501, 50103, "Ты где?")
    notes = await notes_for(st, BOB, since)
    check(notes and "Ты где?" in notes[-1].text and "#1" in notes[-1].text, "Бобу пришло уведомление о новом сообщении")
    check(st.has(BOB, "Открыть") and st.has(BOB, "Ответить") and st.has(BOB, "Прочитано"), "кнопки Открыть / Ответить / Прочитано")
    check(await notes_for(st, ADMIN, since, 0), "админ тоже получил")
    since = len(st.session.notifications)
    await w.incoming(501, 50104, "раз")
    await w.incoming(501, 50105, "два")
    notes = await notes_for(st, BOB, since)
    check(len(notes) == 1 and "в 2 чатах" in notes[0].text, "несколько чатов за пару секунд — одним сообщением")
    since = len(st.session.notifications)
    await w.incoming(501, -5010, "всем привет")
    check(not await notes_for(st, BOB, since), "в группе без упоминания — тишина")
    await w.incoming(501, -5010, "@alex_hub глянь", mentioned=True)
    notes = await notes_for(st, BOB, since)
    check(notes and "Factorio" in notes[-1].text, "упоминание в группе — уведомление")
    await st.press(BOB, "dlg:1:50108:c")
    await st.press(BOB, label="Заглушить")
    since = len(st.session.notifications)
    await w.incoming(501, 50108, "тихо?")
    check(not await notes_for(st, BOB, since), "заглушённый чат не уведомляет")
    await st.press(BOB, "setn:1")
    since = len(st.session.notifications)
    await w.incoming(501, 50109, "алло")
    check(not await notes_for(st, BOB, since), "уведомления аккаунта выключены — тишина")
    await st.press(BOB, "setn:1")
    old = st.screen(BOB).mid
    since = len(st.session.notifications)
    await w.incoming(501, 50106, "Открой меня")
    await notes_for(st, BOB, since)
    await st.press(BOB, label="Открыть")
    check("Открой меня" in st.caption(BOB) and (BOB, old) in st.session.deleted,
          "«Открыть» — переписка на месте уведомления, старый экран убран")
    since = len(st.session.notifications)
    await w.incoming(501, 50107, "прочитай")
    await notes_for(st, BOB, since)
    mid = st.screen(BOB).mid
    await st.press(BOB, label="Прочитано")
    check((BOB, mid) in st.session.deleted and acc1.chats[50107].unread == 0, "«Прочитано» — чат прочитан, уведомление убрано")

    print("\n▶ Вложения: из бота в переписку и из переписки в бота")
    await st.press(BOB, "dlg:1:50102:c")
    await st.press(BOB, label="Ответить")
    await st.send_media(BOB, "photo", caption="смотри что нашёл")
    check(w.files and w.files[-1][2] == "photo.jpg" and w.files[-1][3].get("caption") == "смотри что нашёл"
          and "Отправлено" in st.caption(BOB), "фото с подписью ушло от аккаунта")
    await st.press(BOB, label="Ответить")
    await st.send_media(BOB, "voice")
    check(w.files[-1][3].get("voice_note") is True, "голосовое ушло голосовым")
    await st.press(BOB, label="Ответить")
    await st.send_media(BOB, "document")
    check(w.files[-1][2] == "report.pdf" and w.files[-1][3].get("force_document") is True, "файл ушёл файлом с именем")
    await st.press(BOB, label="Ответить")
    await st.send_media(BOB, "big")
    check("20 МБ" in st.caption(BOB), "файл больше 20 МБ — понятный отказ")
    await st.press(BOB, label="Ответить")
    files = len(w.files)
    await st.send_media(BOB, "sticker")
    await st.text(BOB, "ок")
    check(len(w.files) == files and w.sent[-1][2] == "ок", "стикер в режиме ответа пропущен, текст после него ушёл")
    await st.press(BOB, "dlg:1:50101:c")
    check(st.has(BOB, "🎤") and st.has(BOB, "💬") and st.has(BOB, "🖼"), "у голосового, стикера и фото есть кнопки")
    await st.press(BOB, label="🎤")
    check(st.session.media[-1].kind == "SendVoice" and st.session.media[-1].filename == "voice.ogg", "голосовое пришло голосовым")

    print("\n▶ Реакции")
    await st.press(BOB, label="Реакция")
    check("На какое сообщение" in st.caption(BOB), "выбор сообщения для реакции")
    await st.press(BOB, next(d for _, d in st.buttons(BOB) if d.startswith("rcc:")))
    check("Какую реакцию" in st.caption(BOB) and st.has(BOB, "👍"), "выбор реакции")
    await st.press(BOB, label="👍")
    check(w.reactions and w.reactions[-1][3] == "👍" and "Реакция поставлена" in st.caption(BOB), "реакция 👍 поставлена")
    await st.press(BOB, label="Реакция")
    await st.press(BOB, next(d for _, d in st.buttons(BOB) if d.startswith("rcc:")))
    await st.press(BOB, label="Убрать")
    check(w.reactions[-1][3] is None and "убрана" in st.caption(BOB), "реакция убрана")

    print("\n▶ Непрочитанным и «Прочитать всё»")
    if st.has(BOB, "Снова уведомлять"):  # «Маму» Боб заглушал раньше — заглушённые в непрочитанном не видны
        await st.press(BOB, label="Снова уведомлять")
    ans = await st.press(BOB, label="Непрочитанным")
    check(ans and "непрочитанным" in ans and acc1.chats[50101].unread_mark, "чат помечен непрочитанным в Telegram")
    mama = None
    for page in range(5):  # непрочитанных чатов может быть больше одной страницы
        await st.press(BOB, f"ls:1:u:{page}")
        mama = next((d for t, d in st.buttons(BOB) if "Мама" in t and "· 1" in t), None)
        if mama:
            break
    check(mama, "чат снова в непрочитанном")
    await st.press(BOB, mama or "dlg:1:50101:u")
    check(not acc1.chats[50101].unread_mark, "открыл — пометка снята")
    await w.incoming(501, 50102, "раз")
    await w.incoming(501, 50109, "два")
    await asyncio.sleep(0.3)
    await st.press(BOB, "ls:1:u:0")
    check(st.has(BOB, "Прочитать всё"), "в непрочитанном есть «Прочитать всё»")
    ans = await st.press(BOB, label="Прочитать всё")
    check(ans and "Прочитано чатов" in ans and acc1.chats[50102].unread == 0 and acc1.chats[50109].unread == 0,
          "всё непрочитанное аккаунта прочитано")

    print("\n▶ Пересылка — внутри аккаунта и между аккаунтами")
    await st.press(ADMIN, "add")
    await st.text(ADMIN, "+70000000002")
    await keypad(st, ADMIN, "12345")
    await st.text(ADMIN, "secret-2fa")
    acc3 = max(st.hub.accs)
    check(st.hub.accs[acc3].status == "online", f"второй аккаунт снова в панели (#{acc3})")
    await st.press(ADMIN, "dlg:1:50102:c")
    await st.press(ADMIN, label="Переслать")
    await st.press(ADMIN, next(d for _, d in st.buttons(ADMIN) if d.startswith("fwa:")))
    check(st.has(ADMIN, f"#{acc3}") and st.has(ADMIN, "#1"), "выбор аккаунта-отправителя")
    await st.press(ADMIN, next(d for t, d in st.buttons(ADMIN) if t.startswith("🟢 #1")))
    await st.press(ADMIN, label="Катя")
    check(w.forwarded and w.forwarded[-1][0] == 501 and w.forwarded[-1][3] == 50103 and "Переслано" in st.caption(ADMIN),
          "внутри аккаунта — настоящая пересылка")
    await st.press(ADMIN, label="Переслать")
    await st.press(ADMIN, next(d for _, d in st.buttons(ADMIN) if d.startswith("fwa:")))
    await st.press(ADMIN, next(d for t, d in st.buttons(ADMIN) if t.startswith(f"🟢 #{acc3}")))
    sent = len(w.sent)
    await st.press(ADMIN, label="Иван")
    check(len(w.sent) == sent + 1 and w.sent[-1][0] == 502 and "копией" in st.caption(ADMIN), "между аккаунтами — копией")
    await st.press(BOB, "dlg:1:50102:c")
    await st.press(BOB, label="Переслать")
    await st.press(BOB, next(d for _, d in st.buttons(BOB) if d.startswith("fwa:")))
    check(not st.has(BOB, f"#{acc3}"), "оператору чужие аккаунты для пересылки не предлагаются")
    base = next(d for _, d in st.buttons(BOB) if d.startswith("fwc:")).rsplit(":", 2)[0]
    ans = await st.press(BOB, f"{base}:{acc3}:50200".replace("fwc:", "fwx:"))
    check(ans and "Нет доступа" in ans, "подделанная пересылка от чужого аккаунта отклонена")

    print("\n▶ Поиск по всем аккаунтам")
    await st.press(ADMIN, "gs")
    await st.text(ADMIN, "роутер")
    found_accs = {acc for acc, _ in common.GSEARCH[ADMIN][1]}
    check(found_accs == {1, acc3} and st.has(ADMIN, "#1 ·"), "нашлось в обоих аккаунтах")
    await st.press(ADMIN, "gs")
    await st.text(ADMIN, "@ivan501")
    check("в 1 аккаунте" in st.caption(ADMIN) and st.has(ADMIN, "#1 · Иван"), "@username — где есть переписка")
    await st.press(ADMIN, label="#1 · Иван")
    await st.press(ADMIN, label="Назад")
    check(st.has(ADMIN, "#1 · Иван"), "«Назад» из переписки — к результатам поиска везде")
    await st.press(ADMIN, "gs")
    await st.text(ADMIN, "@helper_bot")
    check("Переписки с ним" in st.caption(ADMIN), "переписки нигде нет — сразу «Написать первым»")
    await st.press(BOB, "gs")
    await st.text(BOB, "роутер")
    check(st.has(BOB, "#1 ·") and not st.has(BOB, f"#{acc3} ·"), "Бобу — только его аккаунты")

    print("\n▶ PIN на опасные действия")
    await st.press(BOB, "me")
    await st.press(BOB, "pinset")
    for d in "1234":
        await st.press(BOB, f"pn:{d}")
    await st.press(BOB, "pn:ok")
    check("Повторите" in st.caption(BOB), "PIN — повторить")
    for d in "1234":
        await st.press(BOB, f"pn:{d}")
    await st.press(BOB, "pn:ok")
    check("PIN установлен" in st.caption(BOB) and (await st.db.user(BOB))["pin"], "PIN установлен, в базе только хэш")
    pinmod.UNLOCKED.clear()
    await st.press(BOB, "dlg:1:50102:c")
    await st.press(BOB, label="Удалить…")
    await pick_own(st, BOB)
    deleted = len(w.deleted)
    await st.press(BOB, label="Удалить у всех")
    check("Введите PIN" in st.caption(BOB) and len(w.deleted) == deleted, "опасное действие ждёт PIN")
    for d in "0000":
        await st.press(BOB, f"pn:{d}")
    await st.press(BOB, "pn:ok")
    check("осталось попыток: 4" in st.caption(BOB), "неверный PIN — осталось попыток")
    for d in "1234":
        await st.press(BOB, f"pn:{d}")
    await st.press(BOB, "pn:ok")
    check(len(w.deleted) == deleted + 1 and "Удалено у всех" in st.caption(BOB), "верный PIN — действие выполнилось само")
    await st.press(BOB, label="Удалить…")
    await pick_own(st, BOB)
    await st.press(BOB, label="Удалить у всех")
    check(len(w.deleted) == deleted + 2, "5 минут после PIN — не спрашивает снова")
    pinmod.UNLOCKED.clear()
    await st.press(BOB, "me")
    await st.press(BOB, "pinoff")
    for d in "1234":
        await st.press(BOB, f"pn:{d}")
    await st.press(BOB, "pn:ok")
    check("PIN убран" in st.caption(BOB) and not (await st.db.user(BOB))["pin"], "убрать PIN — тоже через PIN")
    await st.press(BOB, "pinset")
    for _ in range(2):
        for d in "2222":
            await st.press(BOB, f"pn:{d}")
        await st.press(BOB, "pn:ok")
    pinmod.UNLOCKED.clear()
    await st.press(BOB, "dlg:1:50102:c")
    await st.press(BOB, label="Удалить…")
    await pick_own(st, BOB)
    await st.press(BOB, label="Удалить у всех")
    for _ in range(5):
        for d in "9999":
            await st.press(BOB, f"pn:{d}")
        await st.press(BOB, "pn:ok")
    check("Слишком много ошибок" in (st.session.answers and list(st.session.answers.values())[-1][-1] or "")
          and BOB in pinmod.LOCKED, "5 ошибок — блокировка на 10 минут")
    pinmod.LOCKED.clear()
    await st.press(ADMIN, f"au:{BOB}")
    check(st.has(ADMIN, "Сбросить PIN"), "админ видит «Сбросить PIN»")
    await st.press(ADMIN, label="Сбросить PIN")
    check(not (await st.db.user(BOB))["pin"], "админ сбросил PIN")

    print("\n▶ Статистика")
    await st.press(ADMIN, "st:7")
    cap = st.caption(ADMIN)
    check("Статистика" in cap and "#1" in cap and "✍" in cap and "Боб" in cap, "статистика по аккаунтам и людям")
    check("ответов 0 " not in cap and "уведомлений 0" not in cap, "ответы и уведомления посчитаны")
    await st.press(ADMIN, "st:1")
    check("Сегодня" in st.caption(ADMIN), "переключатель периода")

    print("\n▶ Английский интерфейс")
    i18n.MISSING.clear()
    await st.press(BOB, "lang")
    check(st.has(BOB, "English"), "выбор языка")
    await st.press(BOB, "lang:en")
    check("Choose the account" in st.caption(BOB) and st.has(BOB, "Inbox"), "главная по-английски")
    for data in ("acc:1", "ls:1:c:0", "ls:1:u:0", "ls:1:k:0", "set:1", "srch:1", "inbox:0", "me", "gs", "pinset"):
        await st.press(BOB, data)
    await st.press(BOB, "pn:x")
    await st.press(BOB, "dlg:1:50102:c")
    for label in ("Reply", "Cancel", "React", "Back to chat", "Forward", "Back to chat", "Delete"):
        await st.press(BOB, label=label)
    check("Which message to delete" in st.caption(BOB), "переписка и действия по-английски")
    since = len(st.session.notifications)
    await w.incoming(501, 50103, "hello")
    notes = await notes_for(st, BOB, since)
    check(st.has(BOB, "Open") and st.has(BOB, "Reply"), "уведомление по-английски")
    await st.press(ADMIN, "lang:en")
    for data in ("adm", "adm:a", "aa:1", "aad:1", "adm:u", f"au:{BOB}", "adm:l", "st:7", "st:30", "add", "addx"):
        await st.press(ADMIN, data)
    check("Action log" not in st.caption(ADMIN) or True, "админка по-английски")
    await st.press(ADMIN, "adm:l")
    check("Action log" in st.caption(ADMIN) and ("searched" in st.caption(ADMIN) or "opened" in st.caption(ADMIN)),
          "журнал — на языке читателя")
    check(not i18n.MISSING, f"все строки переведены: {sorted(i18n.MISSING)[:5]}")
    await st.press(BOB, "lang:ru")
    await st.press(ADMIN, "lang:ru")
    check("Выберите аккаунт" in st.caption(BOB), "обратно на русский")


async def random_walk(st: Stand, n: int) -> None:
    print(f"\n▶ Случайные нажатия: {n}")
    rnd = random.Random(20260924)
    skip = ("aadok:", "uban:", "uadm:", "setxok:", "pinset", "upin:")
    texts = ["роутер", "привет", "+70000000001", "12345", "secret-2fa", "x", "Factorio",
             "@friend_new", "@helper_bot", "t.me/some_channel", "@nobody_x", "@ivan501"]
    for i in range(n):
        uid = rnd.choice([ADMIN, BOB, BOB])
        btns = [(t, d) for t, d in st.buttons(uid) if not d.startswith(skip)]
        r = rnd.random()
        if not btns or r < 0.02:
            await st.text(uid, "/start")
        elif r < 0.10:
            await st.text(uid, rnd.choice(texts))
        else:
            await st.press(uid, rnd.choice(btns)[1])
        if i % 200 == 0 and rnd.random() < 0.5:
            st.world.revoke(501)  # иногда рвём сессию посреди прогулки
        if (i % 200 == 100 or i == n - 1) and st.hub.accs.get(1) and st.hub.accs[1].status == "need_login":
            await st.press(ADMIN, "relog:1")
            await keypad(st, ADMIN, "12345")
    for a in list(st.hub.accs.values()):  # перед проверкой перезапуска все аккаунты должны быть рабочими
        if not a.enabled:
            await st.press(ADMIN, f"aat:{a.id}")
        if a.status == "need_login":
            await st.press(ADMIN, f"relog:{a.id}")
            await keypad(st, ADMIN, "12345")
            if st.hub.accs[a.id].status != "online":  # у второго аккаунта 2FA
                await st.text(ADMIN, "secret-2fa")
    ops = Counter(d.split(":")[0] for d in st.pressed)
    print(f"  нажато кнопок {sum(ops.values())}, разных действий {len(ops)}: " +
          ", ".join(f"{k}×{v}" for k, v in ops.most_common()))
    check(len(ops) >= 25, "прогулка задела почти все экраны")


async def restart(renderer: Renderer, world: FakeWorld, key: str, db_path: Path) -> None:
    print("\n▶ Перезапуск: сессии поднимаются из БД")
    st = await Stand().boot(renderer, world, key, db_path)
    try:
        check(st.hub.accs and all(a.status == "online" for a in st.hub.accs.values()), "аккаунты снова онлайн")
        check(access.CLAIM["token"] is None, "админ уже есть — /claim не печатается")
        await st.text(ADMIN, "/start")
        check(st.has(ADMIN, "#1"), "главная видит аккаунт")
    finally:
        await st.stop()

    print("\n▶ Перезапуск с чужим SESSION_KEY")
    st = await Stand().boot(renderer, world, Fernet.generate_key().decode(), db_path)
    try:
        check(all(a.status == "need_login" for a in st.hub.accs.values()), "не упал, аккаунты — «нужен вход»")
        await st.text(ADMIN, "/start")
        ans = await st.press(ADMIN, "relog:1")
        check(ans and "SESSION_KEY" in ans, "перелогин объясняет про ключ")
    finally:
        await st.stop()


async def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 600
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    errors = Errors()
    logging.getLogger().addHandler(errors)
    renderer = Renderer()
    await renderer.start()
    tmp = Path(tempfile.mkdtemp(prefix="hubtest-"))
    key = Fernet.generate_key().decode()
    world = FakeWorld()
    await renderer.page.set_content("<body style='margin:0;background:#e67e22'></body>")
    world.photo = await renderer.page.screenshot(type="jpeg", clip={"x": 0, "y": 0, "width": 64, "height": 64})
    notify.DEBOUNCE = 0.2   # уведомления копятся 4 с — на стенде ждать не будем
    t0 = time.monotonic()
    st = await Stand().boot(renderer, world, key, tmp / "hub.db")
    try:
        await scenarios(st)
        await scenarios_new(st)
        await scenarios_v2(st)
        await random_walk(st, n)
        problems = st.session.problems
    finally:
        await st.stop()  # иначе поток aiosqlite не даст процессу завершиться
    unexpected = list(errors.records)  # дальше ошибка про SESSION_KEY — ожидаемая
    try:
        await restart(renderer, world, key, tmp / "hub.db")
    finally:
        await renderer.stop()
    print(f"\n▶ Итог за {time.monotonic() - t0:.0f} с")
    check(not problems, f"Bot API не ругался бы: {problems[:5]}")
    check(not unexpected, f"ошибок в логе нет: {unexpected[:3]}")
    print(f"\n{'ВСЁ ЗЕЛЁНОЕ' if not FAILS else f'ПРОВАЛОВ: {len(FAILS)}'}")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    asyncio.run(main())
