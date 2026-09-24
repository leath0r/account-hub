"""Экраны пользователя: главная, аккаунт, списки, переписка, поиск, входящие, настройки."""
import asyncio
import inspect
import logging
from contextlib import suppress
from html import escape

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from access import AccountGate, Viewer
from tg import Account, Chat, Hub, HubError, Msg, Person, parse_username
from ui import UI, Card, Text, btn, kb, plural

log = logging.getLogger("screens")
router = Router()
router.callback_query.middleware(AccountGate())

PAGE = 8          # кнопок-чатов на страницу
MSG_PAGE = 8      # сообщений в переписке за раз
MSG_MAX = 40
TEXT_BUDGET = 3600  # лимит сообщения Telegram — 4096


class Input(StatesGroup):
    reply = State()
    search = State()
    first = State()   # первое сообщение новому человеку


SEARCH: dict[int, tuple[int, str, list[int]]] = {}  # user → (аккаунт, запрос, id чатов)
PEOPLE: dict[int, tuple[int, Person]] = {}           # user → (аккаунт, найденный человек) — для «Написать первым»

LIST_META = {  # код: заголовок, иконка, фон плитки, цвет иконки
    "c": ("Чаты", "chats", "#132B27", "#2DD4BF"),
    "g": ("Группы и каналы", "groups", "#241B3D", "#A78BFA"),
    "u": ("Непрочитанное", "messages", "#132B27", "#2DD4BF"),
    "k": ("Контакты", "contacts", "#241B3D", "#A78BFA"),
    "s": ("Поиск", "search", "#241B3D", "#A78BFA"),
}
KIND_ICON = {"user": "", "group": "👥 ", "channel": "📢 "}
DASH_TILES = ("непрочитанных", "личных чатов", "групп и каналов")


def short(text: str, n: int = 40) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def snippet(text: str, n: int = 38) -> str:
    return escape(short(text.replace("\n", " "), n))


def chat_label(c: Chat, muted: set[int]) -> str:
    quiet = c.tg_muted or c.id in muted
    tail = f" · {c.unread}" if c.unread and not quiet else ""
    return f"{KIND_ICON[c.kind]}{short(c.title)}{' 🔕' if quiet else ''}{tail}"


def pager(prefix: str, page: int, pages: int) -> list:
    if pages <= 1:
        return []
    return [
        btn("◀", f"{prefix}:{page - 1}") if page > 0 else btn("·", "noop"),
        btn(f"{page + 1} / {pages}", "noop"),
        btn("▶", f"{prefix}:{page + 1}") if page < pages - 1 else btn("·", "noop"),
    ]


def visible(hub: Hub, me: Viewer) -> list[Account]:
    return [a for a in hub.accounts() if me.can_read(a.id)]


def usable(a: Account | None) -> bool:
    return a is not None and a.enabled and a.authorized


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


# ─── Главная ────────────────────────────────────────────────────────────────

async def home_card(hub: Hub, me: Viewer) -> Card:
    accs = visible(hub, me)
    online = sum(a.status == "online" for a in accs)
    need = sum(a.status == "need_login" for a in accs)
    rows, pair = [], []
    for a in accs:
        pair.append(btn(f"{a.status_emoji} #{a.id} {short(a.name, 18)}", f"acc:{a.id}"))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    rows.append(pair)
    if accs:
        unread = await unread_by_account(hub, me, accs)
        inbox = sum(c.unread for chats in unread.values() for c in chats)
        rows.append([btn(f"📥 Входящие · {inbox}" if inbox else "📥 Входящие", "inbox:0", "primary")])
    if me.admin:
        rows.append([btn("⚙️ Админ-панель", "adm")])

    if accs:
        caption = "Выберите аккаунт, с которым будете работать."
        if need:
            caption += f"\n🟠 <i>{need} {plural(need, 'аккаунт ждёт', 'аккаунта ждут', 'аккаунтов ждут')} повторного входа</i>"
    elif me.admin:
        caption = "Аккаунтов пока нет — добавьте первый в админ-панели."
        if not hub.api_ready:
            caption += "\n⚠️ <i>Сначала впишите API_ID и API_HASH в .env (my.telegram.org) и перезапустите бота.</i>"
    else:
        caption = ("У вас пока нет доступа ни к одному аккаунту.\n"
                   f"Попросите администратора выдать доступ — ваш ID: <code>{me.id}</code>")
    return Card("home.html", caption, kb(*rows), dict(
        accounts=accs, online=online, need=need,
        acc_word=plural(len(accs), "аккаунт", "аккаунта", "аккаунтов"),
        empty_hint="Добавьте первый в админ-панели" if me.admin else "Попросите админа выдать доступ"))


# ─── Аккаунт ────────────────────────────────────────────────────────────────

async def account_card(hub: Hub, me: Viewer, a: Account) -> Card:
    ctx = dict(a=a, eyebrow=f"Аккаунт #{a.id}", glow_color="#7C5CFC")
    switch = [btn("⇄ Сменить аккаунт", "home", "primary")]
    dash = [dict(num="—", label=label, color="#5B6273") for label in DASH_TILES]

    if a.status == "need_login":
        ctx.update(tiles=dash, glow_color="#F5A623")
        if me.admin:
            caption = "⚠️ <b>Сессия слетела</b> — нужен повторный вход.\nПока аккаунт недоступен: чаты и отправка отключены."
            return Card("account.html", caption, kb([btn("🔑 Войти заново", f"relog:{a.id}", "primary")],
                                                    [btn("⇄ Сменить аккаунт", "home")]), ctx)
        caption = "⚠️ <b>Сессия слетела</b> — администратор уже знает.\nПока аккаунт недоступен."
        return Card("account.html", caption, kb(switch), ctx)

    if a.status == "offline":
        ctx.update(tiles=dash, glow_color="#5B6273")
        rows = [[btn("⚙️ Открыть в админке", f"aa:{a.id}")]] if me.admin else []
        return Card("account.html", "⏸ Аккаунт отключён в админ-панели.", kb(*rows, switch), ctx)

    try:
        chats = await hub.dialogs(a.id)
    except HubError as e:
        if a.status == "need_login":  # сессия слетела прямо сейчас
            return await account_card(hub, me, a)
        ctx.update(tiles=dash, glow_color="#5B6273")
        return Card("account.html", f"📡 {escape(str(e))}", kb([btn("🔄 Повторить", f"acc:{a.id}")], switch), ctx)

    muted = await hub.db.muted(me.id, a.id)
    live = [c for c in chats if c.unread and not c.tg_muted and c.id not in muted]
    unread = sum(c.unread for c in live)
    privates = sum(c.kind == "user" for c in chats)
    groups = len(chats) - privates
    ctx["tiles"] = [
        dict(num=unread, label="непрочитанных", color="#2DD4BF"),
        dict(num=privates, label=plural(privates, "личный чат", "личных чата", "личных чатов"), color="#F4F5F7"),
        dict(num=groups, label="групп и каналов", color="#F4F5F7"),
    ]
    if live:
        c = max(live, key=lambda c: c.last_ts)
        caption = (f"<b>Последнее:</b> {escape(c.title)} — «{snippet(c.last_text)}»\n"
                   f"<i>{c.last_time} · всего {unread} {plural(unread, 'непрочитанное', 'непрочитанных', 'непрочитанных')}</i>")
    else:
        caption = "Новых сообщений нет ✓"
    if me.role(a.id) == "viewer":
        caption += "\n<i>👁 У вас доступ только на чтение.</i>"
    private_unread = sum(c.unread for c in live if c.kind == "user")
    markup = kb(
        [btn("🔎 Поиск", f"srch:{a.id}"),
         btn(f"💬 Чаты · {private_unread}" if private_unread else "💬 Чаты", f"ls:{a.id}:c:0")],
        [btn("👥 Группы", f"ls:{a.id}:g:0"), btn("📨 Непрочитанное", f"ls:{a.id}:u:0")],
        [btn("👤 Контакты", f"ls:{a.id}:k:0"), btn("⚙️ Настройки", f"set:{a.id}")],
        switch,
    )
    return Card("account.html", caption, markup, ctx)


# ─── Списки ─────────────────────────────────────────────────────────────────

async def list_items(hub: Hub, me: Viewer, a: Account, kind: str) -> list[tuple[str, str]]:
    chats = await hub.dialogs(a.id)
    muted = await hub.db.muted(me.id, a.id)
    if kind == "c":
        return [(chat_label(c, muted), f"dlg:{a.id}:{c.id}:c") for c in chats if c.kind == "user"]
    if kind == "g":
        return [(chat_label(c, muted), f"dlg:{a.id}:{c.id}:g") for c in chats if c.kind != "user"]
    if kind == "u":
        live = [c for c in chats if c.unread and not c.tg_muted and c.id not in muted]
        return [(chat_label(c, muted), f"dlg:{a.id}:{c.id}:u") for c in sorted(live, key=lambda c: -c.last_ts)]
    if kind == "k":
        dialog_ids = {c.id for c in chats if c.kind == "user"}
        return [(f"👤 {short(p.name)}", f"dlg:{a.id}:{p.id}:k") if p.id in dialog_ids else (f"✉️ {short(p.name)}", f"ct:{a.id}:{p.id}")
                for p in await hub.contacts(a.id)]
    by_id = {c.id: c for c in chats}
    found = SEARCH.get(me.id)
    ids = found[2] if found and found[0] == a.id else []
    return [(chat_label(by_id[i], muted), f"dlg:{a.id}:{i}:s") for i in ids if i in by_id]


def list_sub(chats: list[Chat], kind: str, n: int) -> str:
    if kind == "c":
        fresh = sum(1 for c in chats if c.kind == "user" and c.unread)
        return f"{n} {plural(n, 'диалог', 'диалога', 'диалогов')} · {fresh} с новыми"
    if kind == "g":
        g = sum(c.kind == "group" for c in chats)
        k = sum(c.kind == "channel" for c in chats)
        return f"{g} {plural(g, 'группа', 'группы', 'групп')} · {k} {plural(k, 'канал', 'канала', 'каналов')}"
    if kind == "u":
        return f"{n} {plural(n, 'чат', 'чата', 'чатов')} с новыми" if n else "Всё прочитано ✓"
    if kind == "k":
        return f"{n} {plural(n, 'контакт', 'контакта', 'контактов')}"
    return f"Найдено: {n}"


async def list_card(hub: Hub, me: Viewer, a: Account, kind: str, page: int) -> Card:
    title, icon_name, tile_bg, icon_color = LIST_META[kind]
    items = await list_items(hub, me, a, kind)
    pages = max(1, (len(items) + PAGE - 1) // PAGE)
    page = min(max(page, 0), pages - 1)
    if kind == "s" and me.id in SEARCH:
        title = f"«{short(SEARCH[me.id][1], 22)}»"
    rows = [[btn(text, data)] for text, data in items[page * PAGE:(page + 1) * PAGE]]
    rows.append(pager(f"ls:{a.id}:{kind}", page, pages))
    if kind == "s":
        rows.append([btn("🔎 Новый поиск", f"srch:{a.id}")])
    rows.append([btn("← Назад", f"acc:{a.id}")])
    if not items:
        caption = "Ничего не нашлось — попробуйте другой запрос." if kind == "s" else "Здесь пусто."
    elif kind == "k":
        caption = "✉️ — переписки ещё нет: нажмите, чтобы написать первым. 👤 — открыть переписку."
    else:
        caption = "Нажмите на чат, чтобы открыть переписку."
    return Card("list.html", caption, kb(*rows), dict(
        a=a, eyebrow=f"Аккаунт #{a.id}", title=title, sub=list_sub(await hub.dialogs(a.id), kind, len(items)),
        icon_name=icon_name, tile_bg=tile_bg, icon_color=icon_color,
        page_label=f"стр. {page + 1} / {pages}" if pages > 1 else ""))


# ─── Переписка ──────────────────────────────────────────────────────────────

def back_data(a: Account, back: str) -> str:
    return "inbox:0" if back == "i" else f"ls:{a.id}:{back}:0"


def dialog_head(a: Account, c: Chat) -> str:
    head = f"{KIND_ICON[c.kind] or '💬 '}<b>{escape(c.title)}</b>\n<i>через аккаунт #{a.id} · {escape(a.name)}"
    if c.kind != "user" and c.members:
        head += f" · {c.members} {plural(c.members, 'участник', 'участника', 'участников')}"
    return head + "</i>"


async def dialog_text(hub: Hub, a: Account, c: Chat, more: int = 0, note: str = "") -> tuple[str, bool, list[Msg]]:
    """(текст экрана, есть ли сообщения раньше, какие сообщения показаны)."""
    limit = min(MSG_PAGE * (more + 1), MSG_MAX)
    msgs = await hub.messages(a.id, c, limit + 1)
    has_more = len(msgs) > limit and limit < MSG_MAX
    msgs = msgs[-limit:]

    head = dialog_head(a, c)
    lines, shown, size, cut = [], [], len(head) + len(note) + 80, False
    for m in reversed(msgs):  # с новых — если не влезает, режем старые
        who = "Вы" if m.me else escape(short(m.who))
        line = f"<b>{who}</b>  <code>{m.time}</code>\n{escape(short(m.text, 700))}"
        if size + len(line) > TEXT_BUDGET and lines:
            cut = True
            break
        lines.append(line)
        shown.append(m)
        size += len(line) + 2
    lines.reverse()
    shown.reverse()
    if cut:
        lines.insert(0, "<i>…ранние сообщения не влезли</i>")
        has_more = False
    body = "\n\n".join(lines) if lines else "<i>Сообщений пока нет</i>"
    text = f"{head}\n━━━━━━━━━━━━━━━━\n{body}\n━━━━━━━━━━━━━━━━"
    if note:
        text += f"\n{note}"
    return text, has_more, shown


MEDIA_ICON = {"sticker": "💬", "photo": "🖼"}
MEDIA_BUTTONS = 6


async def dialog_screen(hub: Hub, me: Viewer, a: Account, c: Chat, back: str, more: int = 0, note: str = "") -> Text:
    # Прочитанным чат помечает только тот, кто может отвечать: зритель состояние аккаунта не меняет.
    if c.unread and me.can_write(a.id):
        with suppress(HubError):
            await hub.mark_read(a.id, c)
    text, has_more, shown = await dialog_text(hub, a, c, more, note)
    base = f"{a.id}:{c.id}:{back}"
    top = [btn("⬆ Раньше", f"dlg:{base}:{more + 1}")] if has_more else []
    top.append(btn("🔄 Обновить", f"dlg:{base}:{more}"))
    rows = [top]
    # стикеры и фото из показанных сообщений — по кнопке бот пришлёт их как есть
    media = [m for m in shown if m.media in MEDIA_ICON][-MEDIA_BUTTONS:]
    buttons = [btn(f"{MEDIA_ICON[m.media]} {m.time}", f"med:{a.id}:{c.id}:{m.id}") for m in media]
    rows += [buttons[i:i + 3] for i in range(0, len(buttons), 3)]
    if c.kind == "channel":
        rows.append([btn("📢 Канал — только чтение", "noop")])
    elif me.can_write(a.id):
        rows.append([btn("✍ Ответить", f"rep:{base}", "primary"), btn("🗑 Удалить…", f"dls:{base}")])
    else:
        rows.append([btn("👁 Доступ только на чтение", "noop")])
    muted = c.id in await hub.db.muted(me.id, a.id)
    rows.append([btn("🔔 Снова уведомлять" if muted else "🔕 Заглушить", f"mute:{base}")])
    rows.append([btn("← Назад", back_data(a, back))])
    return Text(text, kb(*rows))


# ─── Удаление сообщений ─────────────────────────────────────────────────────

DELETE_PICK = 8


async def delete_pick_screen(hub: Hub, a: Account, c: Chat, back: str) -> Text:
    """В личке можно удалять и свои, и чужие (Telegram разрешает удалить у обоих), в группах — только свои."""
    own_only = c.kind != "user"
    msgs = [m for m in await hub.messages(a.id, c, 20) if m.me or not own_only][-DELETE_PICK:]
    text = f"{dialog_head(a, c)}\n━━━━━━━━━━━━━━━━\n🗑 <b>Какое сообщение удалить?</b>"
    if own_only:
        text += "\n<i>В группах можно удалять только свои сообщения.</i>"
    if not msgs:
        text += "\n\n<i>Удалять нечего.</i>"
    rows = [[btn(f"{m.time} · {'Вы' if m.me else short(m.who, 12)}: {short(m.text.replace(chr(10), ' '), 28)}",
                 f"dlc:{a.id}:{c.id}:{m.id}:{back}")] for m in reversed(msgs)]
    rows.append([btn("← К переписке", f"dlg:{a.id}:{c.id}:{back}")])
    return Text(text, kb(*rows))


async def delete_confirm_screen(hub: Hub, a: Account, c: Chat, msg_id: int, back: str) -> Text:
    m = next((m for m in await hub.messages(a.id, c, 40) if m.id == msg_id), None)
    if m is None:
        raise HubError("Этого сообщения уже нет")
    who = "Вы" if m.me else escape(short(m.who))
    text = (f"{dialog_head(a, c)}\n━━━━━━━━━━━━━━━━\n🗑 <b>Удалить это сообщение?</b>\n\n"
            f"<b>{who}</b>  <code>{m.time}</code>\n{escape(short(m.text, 700))}")
    base = f"{a.id}:{c.id}:{msg_id}:{back}"
    return Text(text, kb(
        [btn("🗑 Удалить у всех", f"dlx:{base}:1", "danger")],
        [btn("Удалить только у себя", f"dlx:{base}:0")],
        [btn("Отмена", f"dls:{a.id}:{c.id}:{back}")],
    ))


# ─── Новый собеседник (по @username или из контактов) ───────────────────────

async def person_card(hub: Hub, me: Viewer, a: Account, p: Person, error: str = "") -> Card:
    rows = []
    if p.kind in ("group", "channel"):
        what = "Группа" if p.kind == "group" else "Канал"
        caption = (f"{'👥' if p.kind == 'group' else '📢'} {what} <b>{escape(p.name)}</b> — аккаунт #{a.id} "
                   f"в {'ней' if p.kind == 'group' else 'нём'} не состоит.\nВступать через бота пока нельзя.")
    else:
        caption = (f"{'🤖 Бот ' if p.kind == 'bot' else ''}<b>{escape(p.name)}</b> · {escape(p.handle)}\n"
                   f"Переписки с ним у аккаунта #{a.id} ещё нет.")
        left = await hub.first_contacts_left(a.id)
        if not me.can_write(a.id):
            caption += "\n<i>👁 У вас доступ только на чтение — написать первым нельзя.</i>"
        elif left == 0:
            caption += (f"\n⏳ Лимит новых переписок на сутки исчерпан ({hub.cfg.new_chats_per_day}). "
                        "Продолжить можно завтра.")
        else:
            rows.append([btn("✍️ Написать первым", f"new:{a.id}:{p.id}", "primary")])
            if left is not None:
                caption += (f"\n<i>Сегодня этот аккаунт может начать ещё {left} "
                            f"{plural(left, 'новую переписку', 'новые переписки', 'новых переписок')}.</i>")
    if error:
        caption = f"❌ <b>{escape(error)}</b>\n\n{caption}"
    rows.append([btn("🔎 Новый поиск", f"srch:{a.id}"), btn("← Назад", f"acc:{a.id}")])
    _, icon_name, tile_bg, icon_color = LIST_META["k"]
    return Card("list.html", caption, kb(*rows), dict(
        a=a, eyebrow=f"Аккаунт #{a.id} · новый собеседник", title=short(p.name, 22), sub=p.handle,
        icon_name=icon_name, tile_bg=tile_bg, icon_color=icon_color, page_label=""))


# ─── Входящие ───────────────────────────────────────────────────────────────

async def inbox_card(hub: Hub, me: Viewer, page: int) -> Card:
    accs = visible(hub, me)
    unread = await unread_by_account(hub, me, accs)
    items = sorted(((a, c) for a in accs for c in unread[a.id]), key=lambda x: -x[1].last_ts)
    per_account = [dict(a=a, count=sum(c.unread for c in unread[a.id])) for a in accs]
    total = sum(x["count"] for x in per_account)
    pages = max(1, (len(items) + PAGE - 1) // PAGE)
    page = min(max(page, 0), pages - 1)
    rows = [[btn(f"#{a.id} · {chat_label(c, set())}", f"dlg:{a.id}:{c.id}:i")] for a, c in items[page * PAGE:(page + 1) * PAGE]]
    rows.append(pager("inbox", page, pages))
    rows.append([btn("🔄 Обновить", f"inbox:{page}"), btn("← Главная", "home")])
    if items:
        top = [f"• <b>{escape(short(c.title))}</b> <i>(#{a.id})</i>: {snippet(c.last_text, 34)}" for a, c in items[:3]]
        caption = "\n".join(top) + "\n\n<i>Прочитанным станет только тот чат, который откроете.</i>"
    else:
        caption = "Новых сообщений нет ✓"
    return Card("inbox.html", caption, kb(*rows), dict(
        total=total, chats=len(items), chats_word=plural(len(items), "чате", "чатах", "чатах"),
        per_account=per_account))


# ─── Настройки аккаунта ─────────────────────────────────────────────────────

async def settings_card(hub: Hub, me: Viewer, a: Account, confirm: bool = False) -> Card:
    notify = await hub.db.notify(me.id, a.id)
    muted = await hub.db.muted(me.id, a.id)
    ctx = dict(a=a, eyebrow=f"Аккаунт #{a.id}", title="Настройки",
               sub=f"Уведомления: {'включены' if notify else 'выключены'}",
               icon_name="settings", tile_bg="#1E222B", icon_color="#9AA1B2", page_label="")
    if confirm:
        return Card("list.html", "🚪 <b>Завершить сессию?</b>\nВход будет закрыт в самом Telegram. "
                                 "Аккаунт станет 🟠 «Нужен вход», войти можно будет заново.",
                    kb([btn("Да, завершить", f"setxok:{a.id}", "danger")], [btn("Отмена", f"set:{a.id}")]), ctx)
    rows = [
        [btn("🔔 Уведомления: вкл" if notify else "🔕 Уведомления: выкл", f"setn:{a.id}")],
        [btn(f"🔕 Заглушённые чаты · {len(muted)}", f"setm:{a.id}")],
    ]
    if me.admin:
        rows.append([btn("🚪 Завершить сессию", f"setx:{a.id}", "danger")])
    rows.append([btn("← Назад", f"acc:{a.id}")])
    return Card("list.html", "Непрочитанное этого аккаунта попадает во «📥 Входящие», если уведомления включены.",
                kb(*rows), ctx)


# ─── Хендлеры ───────────────────────────────────────────────────────────────

async def go(cq: CallbackQuery, ui: UI, screen, alert: str | None = None) -> None:
    """screen — готовый экран или корутина, которая его строит. Ошибка Telegram → алерт, экран остаётся прежним."""
    try:
        if inspect.isawaitable(screen):
            screen = await screen
    except HubError as e:
        with suppress(TelegramBadRequest):
            await cq.answer(str(e), show_alert=True)
        return
    with suppress(TelegramBadRequest):  # если Telegram отвечал долго, кнопка уже «протухла» — не страшно
        await cq.answer(alert or None, show_alert=bool(alert))
    ui.attach(cq.from_user.id, cq.message)
    await ui.show(cq.from_user.id, cq.message.chat.id, screen)


async def gone(cq: CallbackQuery, ui: UI, hub: Hub, me: Viewer) -> None:
    await go(cq, ui, home_card(hub, me), "Этого аккаунта больше нет")


def ints(*parts: str) -> list[int] | None:
    try:
        return [int(p) for p in parts]
    except ValueError:
        return None


@router.message(CommandStart())
async def start(message: Message, state: FSMContext, ui: UI, hub: Hub, me: Viewer) -> None:
    await state.clear()
    await ui.drop(me.id)
    await ui.show(me.id, message.chat.id, await home_card(hub, me))
    with suppress(Exception):
        await message.delete()


@router.callback_query(F.data == "noop")
async def noop(cq: CallbackQuery) -> None:
    await cq.answer()


@router.callback_query(F.data == "home")
async def home(cq: CallbackQuery, state: FSMContext, ui: UI, hub: Hub, me: Viewer) -> None:
    await state.clear()
    await go(cq, ui, home_card(hub, me))


@router.callback_query(F.data.startswith("acc:"))
async def account(cq: CallbackQuery, state: FSMContext, ui: UI, hub: Hub, me: Viewer) -> None:
    await state.clear()
    a = hub.accs.get(int(cq.data.split(":")[1]))
    if not a:
        return await gone(cq, ui, hub, me)
    await go(cq, ui, account_card(hub, me, a))


@router.callback_query(F.data.startswith("ls:"))
async def lists(cq: CallbackQuery, ui: UI, hub: Hub, me: Viewer) -> None:
    _, acc_id, kind, page = cq.data.split(":")
    a = hub.accs.get(int(acc_id))
    if not a:
        return await gone(cq, ui, hub, me)
    if not usable(a) or kind not in LIST_META or not page.isdigit():
        return await go(cq, ui, account_card(hub, me, a))
    await go(cq, ui, list_card(hub, me, a, kind, int(page)))


@router.callback_query(F.data.startswith("ct:"))
async def contact_without_dialog(cq: CallbackQuery, ui: UI, hub: Hub, me: Viewer) -> None:
    nums = ints(*cq.data.split(":")[1:3])
    a = hub.accs.get(nums[0]) if nums and len(nums) == 2 else None
    if not usable(a):
        return await gone(cq, ui, hub, me)

    async def build() -> Card:
        p = next((p for p in await hub.contacts(a.id) if p.id == nums[1]), None)
        if p is None:
            raise HubError("Этого контакта больше нет")
        PEOPLE[me.id] = (a.id, p)
        return await person_card(hub, me, a, p)

    await go(cq, ui, build())


@router.callback_query(F.data.startswith("pp:"))
async def person_back(cq: CallbackQuery, state: FSMContext, ui: UI, hub: Hub, me: Viewer) -> None:
    await state.clear()
    nums = ints(*cq.data.split(":")[1:3])
    a = hub.accs.get(nums[0]) if nums and len(nums) == 2 else None
    found = PEOPLE.get(me.id)
    if not usable(a) or not found or found[0] != a.id or found[1].id != nums[1]:
        return await go(cq, ui, home_card(hub, me)) if not a else await go(cq, ui, account_card(hub, me, a))
    await go(cq, ui, person_card(hub, me, a, found[1]))


@router.callback_query(F.data.startswith("new:"))
async def first_start(cq: CallbackQuery, state: FSMContext, ui: UI, hub: Hub, me: Viewer) -> None:
    nums = ints(*cq.data.split(":")[1:3])
    a = hub.accs.get(nums[0]) if nums and len(nums) == 2 else None
    if not usable(a):
        return await gone(cq, ui, hub, me)
    found = PEOPLE.get(me.id)
    if not found or found[0] != a.id or found[1].id != nums[1]:
        return await cq.answer("Найдите человека заново через 🔎 Поиск", show_alert=True)
    p = found[1]
    if await hub.first_contacts_left(a.id) == 0:
        return await cq.answer("Лимит новых переписок для этого аккаунта на сутки исчерпан", show_alert=True)
    await state.set_state(Input.first)
    await state.update_data(acc=a.id, pid=p.id)
    text = (f"✍️ <b>Первое сообщение</b>\n━━━━━━━━━━━━━━━━\n"
            f"Кому: <b>{escape(p.name)}</b> · {escape(p.handle)}\n"
            f"От: аккаунт #{a.id} · {escape(a.name)}\n━━━━━━━━━━━━━━━━\n"
            "Напишите текст сообщением — отправлю сразу.")
    await go(cq, ui, Text(text, kb([btn("✕ Отмена", f"pp:{a.id}:{p.id}", "danger")])))


@router.message(Input.first, F.text)
async def first_send(message: Message, state: FSMContext, ui: UI, hub: Hub, me: Viewer) -> None:
    data = await state.get_data()
    await state.clear()
    with suppress(Exception):
        await message.delete()
    a = hub.accs.get(data.get("acc"))
    found = PEOPLE.get(me.id)
    if not usable(a) or not me.can_write(a.id) or not found or found[1].id != data.get("pid"):
        return await ui.show(me.id, message.chat.id, await home_card(hub, me))
    p = found[1]
    if await hub.first_contacts_left(a.id) == 0:
        return await ui.show(me.id, message.chat.id, await person_card(hub, me, a, p))
    try:
        await hub.send_first(a.id, p, message.text[:4000])
    except HubError as e:
        return await ui.show(me.id, message.chat.id, await person_card(hub, me, a, p, error=str(e)))
    await hub.db.add_first_contact(a.id, p.id)
    await hub.db.log(me.id, a.id, f"#{a.id} написал первым {p.handle} ({p.name})")
    PEOPLE.pop(me.id, None)
    try:
        c = await hub.chat(a.id, p.id)
        screen = await dialog_screen(hub, me, a, c, "c", note=f"✅ Отправлено от имени {escape(a.name)}")
    except HubError:
        screen = await account_card(hub, me, a)
    await ui.show(me.id, message.chat.id, screen)


@router.callback_query(F.data.startswith("dlg:"))
async def dialog(cq: CallbackQuery, state: FSMContext, ui: UI, hub: Hub, me: Viewer) -> None:
    await state.clear()
    parts = cq.data.split(":")  # dlg:<аккаунт>:<чат>:<откуда>[:<сколько раз «раньше»>]
    nums = ints(parts[1], parts[2], *parts[4:5]) if len(parts) >= 4 else None
    if not nums:
        return await cq.answer()
    a = hub.accs.get(nums[0])
    if not a:
        return await gone(cq, ui, hub, me)
    if not usable(a):
        return await go(cq, ui, account_card(hub, me, a))
    more = nums[2] if len(nums) > 2 else 0

    async def build() -> Text:
        c = await hub.chat(a.id, nums[1])
        if len(parts) == 4:  # открыл из списка, а не листает
            await hub.db.log(me.id, a.id, f"#{a.id} открыл «{c.title}»")
        return await dialog_screen(hub, me, a, c, parts[3], more)

    await go(cq, ui, build())


@router.callback_query(F.data.startswith("mute:"))
async def mute(cq: CallbackQuery, ui: UI, hub: Hub, me: Viewer) -> None:
    _, acc_id, chat_id, back = cq.data.split(":")
    nums = ints(acc_id, chat_id)
    a = hub.accs.get(nums[0]) if nums else None
    if not usable(a):
        return await gone(cq, ui, hub, me)

    async def build() -> Text:
        c = await hub.chat(a.id, nums[1])
        await hub.db.toggle_mute(me.id, a.id, c.id)
        return await dialog_screen(hub, me, a, c, back)

    await go(cq, ui, build())


@router.callback_query(F.data.startswith("rep:"))
async def reply_start(cq: CallbackQuery, state: FSMContext, ui: UI, hub: Hub, me: Viewer) -> None:
    _, acc_id, chat_id, back = cq.data.split(":")
    nums = ints(acc_id, chat_id)
    a = hub.accs.get(nums[0]) if nums else None
    if not usable(a):
        return await gone(cq, ui, hub, me)

    async def build() -> Text:
        c = await hub.chat(a.id, nums[1])
        await state.set_state(Input.reply)
        await state.update_data(acc=a.id, chat=c.id, back=back)
        text, _, _ = await dialog_text(hub, a, c, note=f"\n✍️ <b>Напишите ответ сообщением</b> — отправлю от имени {escape(a.name)}.")
        return Text(text, kb([btn("✕ Отмена", f"dlg:{a.id}:{c.id}:{back}", "danger")]))

    await go(cq, ui, build())


@router.message(Input.reply, F.text)
async def reply_send(message: Message, state: FSMContext, ui: UI, hub: Hub, me: Viewer) -> None:
    data = await state.get_data()
    await state.clear()
    with suppress(Exception):
        await message.delete()
    a = hub.accs.get(data.get("acc"))
    if not usable(a) or not me.can_write(a.id):
        return await ui.show(me.id, message.chat.id, await home_card(hub, me))
    try:
        c = await hub.chat(a.id, data["chat"])
    except HubError:
        return await ui.show(me.id, message.chat.id, await account_card(hub, me, a))
    try:
        await hub.send(a.id, c, message.text[:4000])
        await hub.db.log(me.id, a.id, f"#{a.id} ответил в «{c.title}»")
        note = f"✅ Отправлено от имени {escape(a.name)}"
    except HubError as e:
        note = f"❌ Не отправлено: {escape(str(e))}"
    try:
        screen = await dialog_screen(hub, me, a, c, data.get("back", "c"), note=note)
    except HubError:
        screen = await account_card(hub, me, a)
    await ui.show(me.id, message.chat.id, screen)


@router.callback_query(F.data.startswith("srch:"))
async def search_start(cq: CallbackQuery, state: FSMContext, ui: UI, hub: Hub, me: Viewer) -> None:
    a = hub.accs.get(int(cq.data.split(":")[1]))
    if not usable(a):
        return await gone(cq, ui, hub, me) if not a else await go(cq, ui, account_card(hub, me, a))
    await state.set_state(Input.search)
    await state.update_data(acc=a.id)
    SEARCH.pop(me.id, None)
    title, icon_name, tile_bg, icon_color = LIST_META["s"]
    card = Card("list.html",
                "🔎 <b>Напишите запрос сообщением.</b>\nИщу по названиям чатов и тексту сообщений этого аккаунта.\n"
                "Чтобы найти человека и написать ему — отправьте <code>@username</code> или ссылку <code>t.me/…</code>",
                kb([btn("← Назад", f"acc:{a.id}")]),
                dict(a=a, eyebrow=f"Аккаунт #{a.id}", title="Поиск", sub="Жду запрос…",
                     icon_name=icon_name, tile_bg=tile_bg, icon_color=icon_color, page_label=""))
    await go(cq, ui, card)


@router.message(Input.search, F.text)
async def search_run(message: Message, state: FSMContext, ui: UI, hub: Hub, me: Viewer) -> None:
    data = await state.get_data()
    await state.clear()
    with suppress(Exception):
        await message.delete()
    a = hub.accs.get(data.get("acc"))
    if not usable(a) or not me.can_read(a.id):
        return await ui.show(me.id, message.chat.id, await home_card(hub, me))
    q = message.text.strip()[:64]
    username = parse_username(q)
    try:
        await hub.db.log(me.id, a.id, f"#{a.id} искал «{q}»")
        if username:
            screen = await find_person(hub, me, a, username)
        else:
            found = await hub.search(a.id, q)
            SEARCH[me.id] = (a.id, q, [c.id for c in found])
            screen = await list_card(hub, me, a, "s", 0)
    except HubError as e:
        SEARCH[me.id] = (a.id, q, [])
        screen = await list_card(hub, me, a, "s", 0)
        screen.caption = f"❌ <b>{escape(str(e))}</b>\n\n{screen.caption}"
    await ui.show(me.id, message.chat.id, screen)


async def find_person(hub: Hub, me: Viewer, a: Account, username: str) -> Card | Text:
    """@username: есть переписка — открыть её, нет — карточка человека с «Написать первым»."""
    p = await hub.resolve(a.id, username)
    if p.id in {c.id for c in await hub.dialogs(a.id)}:
        SEARCH[me.id] = (a.id, f"@{p.username}", [p.id])
        return await dialog_screen(hub, me, a, await hub.chat(a.id, p.id), "s")
    PEOPLE[me.id] = (a.id, p)
    return await person_card(hub, me, a, p)


@router.callback_query(F.data.startswith("inbox:"))
async def inbox(cq: CallbackQuery, state: FSMContext, ui: UI, hub: Hub, me: Viewer) -> None:
    await state.clear()
    page = cq.data.split(":")[1]
    await go(cq, ui, inbox_card(hub, me, int(page) if page.isdigit() else 0))


@router.callback_query(F.data.regexp(r"^set(n|m|x|xok)?:"))
async def settings(cq: CallbackQuery, ui: UI, hub: Hub, me: Viewer) -> None:
    action, acc_id = cq.data.split(":")
    a = hub.accs.get(int(acc_id))
    if not usable(a):
        return await gone(cq, ui, hub, me) if not a else await go(cq, ui, account_card(hub, me, a))
    if action == "setn":
        on = not await hub.db.notify(me.id, a.id)
        await hub.db.set_notify(me.id, a.id, on)
    elif action == "setm":
        muted = await hub.db.muted(me.id, a.id)
        if not muted:
            return await cq.answer("Заглушённых чатов нет. Заглушить можно из переписки.", show_alert=True)
        titles = {c.id: c.title for c in hub.cached_dialogs(a.id)}
        names = [titles.get(i, "чат вне списка") for i in muted]
        return await cq.answer(short("Заглушены: " + ", ".join(names), 190), show_alert=True)
    elif action == "setx":
        return await go(cq, ui, settings_card(hub, me, a, confirm=True))
    elif action == "setxok":
        await hub.logout(a.id)
        await hub.db.log(me.id, a.id, f"#{a.id} завершил сессию")
        return await go(cq, ui, account_card(hub, me, a), "Сессия завершена")
    await go(cq, ui, settings_card(hub, me, a))


@router.callback_query(F.data.regexp(r"^dl[scx]:"))
async def delete_flow(cq: CallbackQuery, state: FSMContext, ui: UI, hub: Hub, me: Viewer) -> None:
    """dls:<акк>:<чат>:<откуда> — выбор · dlc:<акк>:<чат>:<msg>:<откуда> — подтверждение · dlx:…:<1|0> — удалить."""
    await state.clear()
    parts = cq.data.split(":")
    op = parts[0]
    nums = ints(*(parts[1:3] if op == "dls" else parts[1:4]))
    a = hub.accs.get(nums[0]) if nums else None
    if not usable(a):
        return await gone(cq, ui, hub, me)
    back = parts[3] if op == "dls" else parts[4]

    async def build() -> Text:
        c = await hub.chat(a.id, nums[1])
        if c.kind == "channel":
            raise HubError("В канале удалять нельзя")
        if op == "dls":
            return await delete_pick_screen(hub, a, c, back)
        if op == "dlc":
            return await delete_confirm_screen(hub, a, c, nums[2], back)
        revoke = parts[5] == "1"
        await hub.delete(a.id, c, nums[2], revoke)
        await hub.db.log(me.id, a.id, f"#{a.id} удалил сообщение в «{c.title}» ({'у всех' if revoke else 'у себя'})")
        return await dialog_screen(hub, me, a, c, back, note=f"🗑 Удалено {'у всех' if revoke else 'только у аккаунта'}")

    await go(cq, ui, build())


@router.callback_query(F.data.startswith("med:"))
async def show_media(cq: CallbackQuery, ui: UI, hub: Hub, me: Viewer) -> None:
    """Стикер или фото из переписки — отдельным сообщением под экраном, с кнопкой «Скрыть»."""
    nums = ints(*cq.data.split(":")[1:4])
    a = hub.accs.get(nums[0]) if nums and len(nums) == 3 else None
    if not usable(a):
        return await gone(cq, ui, hub, me)
    try:
        c = await hub.chat(a.id, nums[1])
        kind, data, filename = await hub.media(a.id, c, nums[2])
    except HubError as e:
        with suppress(TelegramBadRequest):
            await cq.answer(str(e), show_alert=True)
        return
    with suppress(TelegramBadRequest):
        await cq.answer()
    file = BufferedInputFile(data, filename)
    hide = kb([btn("✕ Скрыть", "hide")])
    try:
        if kind == "sticker":
            await ui.bot.send_sticker(cq.message.chat.id, file, reply_markup=hide)
        else:
            await ui.bot.send_photo(cq.message.chat.id, file, caption=f"{escape(c.title)} · через #{a.id}", reply_markup=hide)
    except TelegramBadRequest as e:
        log.warning("media #%d %s: %s", a.id, filename, e)
        await ui.bot.send_message(cq.message.chat.id, "Не получилось показать это вложение 😕", reply_markup=hide)


@router.callback_query(F.data == "hide")
async def hide_media(cq: CallbackQuery) -> None:
    with suppress(TelegramBadRequest):
        await cq.answer()
    with suppress(Exception):
        await cq.message.delete()


@router.callback_query()
async def unknown_button(cq: CallbackQuery) -> None:
    await cq.answer("Кнопка устарела — нажмите /start", show_alert=True)


@router.message()
async def stray(message: Message) -> None:
    """Экран — одно сообщение: всё лишнее из чата убираем."""
    with suppress(Exception):
        await message.delete()
