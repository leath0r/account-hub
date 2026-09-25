"""Общее для экранов: состояния ввода, помощники, показ экрана и ошибок на языке пользователя."""
import asyncio
import inspect
import json
import logging
from contextlib import suppress
from html import escape

from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup

from access import Viewer
from i18n import tr
from i18n_en import EN
from tg import SPECIAL_TITLES, Account, Chat, Hub, HubError, Person
from ui import UI, Card, btn

log = logging.getLogger("screens")

PAGE = 8            # кнопок-чатов на страницу
MSG_PAGE = 8        # сообщений в переписке за раз
MSG_MAX = 40
TEXT_BUDGET = 3600  # лимит сообщения Telegram — 4096
LINE = "━━━━━━━━━━━━━━━━"


class Input(StatesGroup):
    reply = State()     # ответ в переписку: текст или вложение
    search = State()    # поиск по аккаунту
    first = State()     # первое сообщение новому человеку
    gsearch = State()   # поиск по всем аккаунтам


SEARCH: dict[int, tuple[int, str, list[int]]] = {}         # user → (аккаунт, запрос, id чатов)
GSEARCH: dict[int, tuple[str, list[tuple[int, int]]]] = {}  # user → (запрос, [(аккаунт, чат)])
PEOPLE: dict[int, tuple[int, Person]] = {}                 # user → (аккаунт, найденный человек) — для «Написать первым»

LIST_META = {  # код: заголовок (ключ перевода), иконка, фон плитки, цвет иконки
    "c": ("Чаты", "chats", "#132B27", "#2DD4BF"),
    "g": ("Группы и каналы", "groups", "#241B3D", "#A78BFA"),
    "u": ("Непрочитанное", "messages", "#132B27", "#2DD4BF"),
    "k": ("Контакты", "contacts", "#241B3D", "#A78BFA"),
    "s": ("Поиск", "search", "#241B3D", "#A78BFA"),
}
KIND_ICON = {"user": "", "group": "👥 ", "channel": "📢 "}


def short(text: str, n: int = 40) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def snippet(text: str, n: int = 38) -> str:
    return escape(short(text.replace("\n", " "), n))


def title(me: Viewer, c: Chat | Person | Account) -> str:
    """Название чата/имя. Заглушки вроде «Избранное» — на языке пользователя."""
    name = getattr(c, "title", None) or getattr(c, "name", "")
    return me.t(name) if name in SPECIAL_TITLES else name


def handle(me: Viewer, obj) -> str:
    return f"@{obj.username}" if obj.username else me.t("без username")


def chat_label(me: Viewer, c: Chat, muted: set[int]) -> str:
    quiet = c.tg_muted or c.id in muted
    tail = f" · {c.unread}" if c.unread and not quiet else ""
    return f"{KIND_ICON[c.kind]}{short(title(me, c))}{' 🔕' if quiet else ''}{tail}"


def pager(prefix: str, page: int, pages: int) -> list:
    if pages <= 1:
        return []
    return [
        btn("◀", f"{prefix}:{page - 1}") if page > 0 else btn("·", "noop"),
        btn(f"{page + 1} / {pages}", "noop"),
        btn("▶", f"{prefix}:{page + 1}") if page < pages - 1 else btn("·", "noop"),
    ]


def ints(*parts: str) -> list[int] | None:
    try:
        return [int(p) for p in parts]
    except ValueError:
        return None


def visible(hub: Hub, me: Viewer) -> list[Account]:
    return [a for a in hub.accounts() if me.can_read(a.id)]


def usable(a: Account | None) -> bool:
    return a is not None and a.enabled and a.authorized


def back_data(a: Account, back: str) -> str:
    """Куда ведёт «← Назад» из переписки: i — входящие, g — поиск везде, иначе список аккаунта."""
    if back == "i":
        return "inbox:0"
    if back == "g":
        return "gsl:0"
    return f"ls:{a.id}:{back}:0"


def card(me: Viewer, template: str, caption: str, markup: InlineKeyboardMarkup, **ctx) -> Card:
    """Карточка с переводчиком в шаблоне: {{ t('…') }}."""
    return Card(template, caption, markup, dict(ctx, t=me.t, lang=me.lang))


async def live_unread(hub: Hub, me: Viewer, a: Account) -> list[Chat]:
    """Непрочитанные чаты аккаунта, о которых этот пользователь хочет знать."""
    if a.status != "online" or not await hub.db.notify(me.id, a.id):
        return []
    chats = await hub.dialogs(a.id)
    muted = await hub.db.muted(me.id, a.id)
    return [c for c in chats if c.unread and not c.tg_muted and c.id not in muted]


async def unread_by_account(hub: Hub, me: Viewer, accs: list[Account]) -> dict[int, list[Chat]]:
    res = await asyncio.gather(*(live_unread(hub, me, a) for a in accs), return_exceptions=True)
    out = {}
    for a, r in zip(accs, res):
        if isinstance(r, BaseException):
            if not isinstance(r, HubError):
                log.error("unread #%d: %r", a.id, r)
            r = []
        out[a.id] = r
    return out


async def record(hub: Hub, me: Viewer, acc_id: int | None, kind: str, action: str | None = None, **kw) -> None:
    """Событие для статистики + (если есть action) запись в журнал."""
    await hub.db.event(me.id, acc_id, kind)
    if action:
        await hub.db.log(me.id, acc_id, action, **kw)


def audit_text(me: Viewer, action: str) -> str:
    """Строка журнала на языке читателя. Новые записи — JSON {t, kw}, старые — простой русский текст."""
    if action.startswith("{"):
        with suppress(ValueError, KeyError, TypeError):
            data = json.loads(action)
            return me.t(data["t"], **data.get("kw", {}))
    return EN.get(action, action) if me.lang == "en" else action


async def go(cq: CallbackQuery, ui: UI, me: Viewer, screen, alert: str | None = None) -> None:
    """screen — готовый экран или корутина, которая его строит. Ошибка Telegram → алерт, экран остаётся прежним.
    alert — уже переведённый текст."""
    try:
        if inspect.isawaitable(screen):
            screen = await screen
    except HubError as e:
        with suppress(TelegramBadRequest):
            await cq.answer(short(me.err(e), 190), show_alert=True)
        return
    with suppress(TelegramBadRequest):  # если Telegram отвечал долго, кнопка уже «протухла» — не страшно
        await cq.answer(alert or None, show_alert=bool(alert))
    ui.attach(cq.from_user.id, cq.message)
    await ui.show(cq.from_user.id, cq.message.chat.id, screen)


async def alert(cq: CallbackQuery, text: str) -> None:
    with suppress(TelegramBadRequest):
        await cq.answer(short(text, 190), show_alert=True)


async def gone(cq: CallbackQuery, ui: UI, hub: Hub, me: Viewer) -> None:
    from screens.home import home_card
    await go(cq, ui, me, home_card(hub, me), me.t("Этого аккаунта больше нет"))


def err_line(me: Viewer, e: HubError | str) -> str:
    text = me.err(e) if isinstance(e, HubError) else e
    return f"❌ <b>{escape(text)}</b>"


def tr_role(me: Viewer, role: str) -> str:
    from access import ROLE_LABEL
    return me.t(ROLE_LABEL[role])


def lang_t(lang: str):
    """Переводчик без Viewer — для уведомлений."""
    return lambda text, /, **kw: tr(lang, text, **kw)
