"""Экраны пользователя: главная, аккаунт, списки, переписка, поиск, входящие, настройки."""
from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from mock import ACCOUNTS, Account, Chat, Msg, audit, now_hm
from ui import UI, Card, Text, btn, kb

router = Router()
PAGE = 8


class Input(StatesGroup):
    reply = State()
    search = State()


SEARCH: dict[int, tuple[int, str, list[str]]] = {}  # user → (аккаунт, запрос, id чатов)

LIST_META = {  # код: заголовок, иконка, фон плитки, цвет иконки
    "c": ("Чаты", "chats", "#132B27", "#2DD4BF"),
    "g": ("Группы и каналы", "groups", "#241B3D", "#A78BFA"),
    "u": ("Непрочитанное", "messages", "#132B27", "#2DD4BF"),
    "k": ("Контакты", "contacts", "#241B3D", "#A78BFA"),
    "s": ("Поиск", "search", "#241B3D", "#A78BFA"),
}
KIND_ICON = {"user": "", "group": "👥 ", "channel": "📢 "}


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n) % 100
    if 10 < n < 20:
        return many
    if n % 10 == 1:
        return one
    if 2 <= n % 10 <= 4:
        return few
    return many


def chat_label(c: Chat) -> str:
    tail = f" · {c.unread}" if c.unread and not c.muted else ""
    mute = " 🔕" if c.muted else ""
    return f"{KIND_ICON[c.kind]}{c.title}{mute}{tail}"


def snippet(text: str, n: int = 38) -> str:
    return escape(text if len(text) <= n else text[: n - 1] + "…")


def pager(prefix: str, page: int, pages: int) -> list:
    if pages <= 1:
        return []
    return [
        btn("◀", f"{prefix}:{page - 1}") if page > 0 else btn("·", "noop"),
        btn(f"{page + 1} / {pages}", "noop"),
        btn("▶", f"{prefix}:{page + 1}") if page < pages - 1 else btn("·", "noop"),
    ]


# ─── Главная ────────────────────────────────────────────────────────────────

def home_card() -> Card:
    accs = list(ACCOUNTS.values())
    online = sum(a.status == "online" for a in accs)
    need = sum(a.status == "need_login" for a in accs)
    rows, pair = [], []
    for a in accs:
        pair.append(btn(f"{a.status_emoji} #{a.id} {a.name}", f"acc:{a.id}"))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    rows.append(pair)
    inbox = sum(a.unread_total for a in accs if a.status == "online" and a.notify)
    rows.append([btn(f"📥 Входящие · {inbox}" if inbox else "📥 Входящие", "inbox:0", "primary")])
    rows.append([btn("⚙️ Админ-панель", "adm")])
    caption = "Выберите аккаунт, с которым будете работать."
    if need:
        caption += f"\n🟠 <i>{need} {plural(need, 'аккаунт ждёт', 'аккаунта ждут', 'аккаунтов ждут')} повторного входа</i>"
    caption += "\n\n<i>Демо: аккаунты и переписки выдуманные.</i>"
    return Card("home.html", caption, kb(*rows), dict(
        accounts=accs, online=online, need=need,
        acc_word=plural(len(accs), "аккаунт", "аккаунта", "аккаунтов")))


# ─── Аккаунт ────────────────────────────────────────────────────────────────

def account_card(a: Account) -> Card:
    privates = len(a.by_kind(("user",)))
    groups = len(a.by_kind(("group", "channel")))
    ctx = dict(a=a, eyebrow=f"Аккаунт #{a.id}", glow_color="#7C5CFC")
    switch = [btn("⇄ Сменить аккаунт", "home", "primary")]

    if a.status == "need_login":
        ctx["tiles"] = [dict(num="—", label=l, color="#5B6273") for l in ("непрочитанных", "личных чатов", "групп и каналов")]
        ctx["glow_color"] = "#F5A623"
        caption = "⚠️ <b>Сессия слетела</b> — нужен повторный вход.\nПока аккаунт недоступен: чаты и отправка отключены."
        return Card("account.html", caption, kb([btn("🔑 Войти заново", f"relog:{a.id}", "primary")], [btn("⇄ Сменить аккаунт", "home")]), ctx)

    if a.status == "offline":
        ctx["tiles"] = [dict(num="—", label=l, color="#5B6273") for l in ("непрочитанных", "личных чатов", "групп и каналов")]
        ctx["glow_color"] = "#5B6273"
        caption = "⏸ Аккаунт отключён в админ-панели."
        return Card("account.html", caption, kb([btn("⚙️ Открыть в админке", f"aa:{a.id}")], switch), ctx)

    unread = a.unread_total
    ctx["tiles"] = [
        dict(num=unread, label="непрочитанных", color="#2DD4BF"),
        dict(num=privates, label=plural(privates, "личный чат", "личных чата", "личных чатов"), color="#F4F5F7"),
        dict(num=groups, label="групп и каналов", color="#F4F5F7"),
    ]
    fresh = sorted((c for c in a.chats.values() if c.unread and not c.muted), key=lambda c: c.last.time, reverse=True)
    if fresh:
        c = fresh[0]
        caption = f"<b>Последнее:</b> {escape(c.title)} — «{snippet(c.last.text)}»\n<i>{c.last.time} · всего {unread} {plural(unread, 'непрочитанное', 'непрочитанных', 'непрочитанных')}</i>"
    else:
        caption = "Новых сообщений нет ✓"
    markup = kb(
        [btn("🔎 Поиск", f"srch:{a.id}"), btn(f"💬 Чаты · {sum(c.unread for c in a.by_kind(('user',)))}" if unread else "💬 Чаты", f"ls:{a.id}:c:0")],
        [btn("👥 Группы", f"ls:{a.id}:g:0"), btn("📨 Сообщения", f"ls:{a.id}:u:0")],
        [btn("👤 Контакты", f"ls:{a.id}:k:0"), btn("⚙️ Настройки", f"set:{a.id}")],
        switch,
    )
    return Card("account.html", caption, markup, ctx)


# ─── Списки ─────────────────────────────────────────────────────────────────

def list_items(a: Account, kind: str, uid: int) -> list[tuple[str, str]]:
    if kind == "c":
        return [(chat_label(c), f"dlg:{a.id}:{c.id}:c") for c in a.by_kind(("user",))]
    if kind == "g":
        return [(chat_label(c), f"dlg:{a.id}:{c.id}:g") for c in a.by_kind(("group", "channel"))]
    if kind == "u":
        chats = [c for c in a.chats.values() if c.unread and not c.muted]
        return [(chat_label(c), f"dlg:{a.id}:{c.id}:u") for c in sorted(chats, key=lambda c: -c.unread)]
    if kind == "k":
        by_title = {c.title: c for c in a.by_kind(("user",))}
        out = []
        for i, name in enumerate(a.contacts):
            c = by_title.get(name)
            out.append((f"👤 {name}", f"dlg:{a.id}:{c.id}:k") if c else (f"✉️ {name}", f"ct:{a.id}:{i}"))
        return out
    _, _, ids = SEARCH.get(uid, (a.id, "", []))
    return [(chat_label(a.chats[i]), f"dlg:{a.id}:{i}:s") for i in ids if i in a.chats]


def list_sub(a: Account, kind: str, items: list, uid: int) -> str:
    n = len(items)
    if kind == "c":
        u = sum(1 for c in a.by_kind(("user",)) if c.unread)
        return f"{n} {plural(n, 'диалог', 'диалога', 'диалогов')} · {u} с новыми"
    if kind == "g":
        g = len(a.by_kind(("group",)))
        k = len(a.by_kind(("channel",)))
        return f"{g} {plural(g, 'группа', 'группы', 'групп')} · {k} {plural(k, 'канал', 'канала', 'каналов')}"
    if kind == "u":
        return f"{n} {plural(n, 'чат', 'чата', 'чатов')} с новыми" if n else "Всё прочитано ✓"
    if kind == "k":
        return f"{n} {plural(n, 'контакт', 'контакта', 'контактов')}"
    return f"Найдено: {n}"


def list_card(a: Account, kind: str, page: int, uid: int) -> Card:
    title, icon_name, tile_bg, icon_color = LIST_META[kind]
    items = list_items(a, kind, uid)
    pages = max(1, (len(items) + PAGE - 1) // PAGE)
    page = min(max(page, 0), pages - 1)
    if kind == "s":
        title = f"«{SEARCH[uid][1]}»" if uid in SEARCH else "Поиск"
    rows = [[btn(text, data)] for text, data in items[page * PAGE:(page + 1) * PAGE]]
    rows.append(pager(f"ls:{a.id}:{kind}", page, pages))
    if kind == "s":
        rows.append([btn("🔎 Новый поиск", f"srch:{a.id}")])
    rows.append([btn("← Назад", f"acc:{a.id}")])
    caption = {
        "k": "✉️ — контакт без диалога. Нажмите на контакт, чтобы открыть переписку.",
        "s": "Нажмите на чат, чтобы открыть переписку.",
    }.get(kind, "Нажмите на чат, чтобы открыть переписку.")
    if not items:
        caption = "Ничего не нашлось — попробуйте другой запрос." if kind == "s" else "Здесь пусто."
    return Card("list.html", caption, kb(*rows), dict(
        a=a, eyebrow=f"Аккаунт #{a.id}", title=title, sub=list_sub(a, kind, items, uid),
        icon_name=icon_name, tile_bg=tile_bg, icon_color=icon_color,
        page_label=f"стр. {page + 1} / {pages}" if pages > 1 else ""))


# ─── Переписка ──────────────────────────────────────────────────────────────

def back_data(a: Account, back: str) -> str:
    return "inbox:0" if back == "i" else f"ls:{a.id}:{back}:0"


def dialog_text(a: Account, c: Chat, more: int = 0, note: str = "") -> tuple[str, bool]:
    shown = 8 + more * 8
    msgs = c.msgs[-shown:]
    head = f"{KIND_ICON[c.kind] or '💬 '}<b>{escape(c.title)}</b>\n<i>через аккаунт #{a.id} · {escape(a.name)}"
    if c.kind != "user":
        head += f" · {c.members} {plural(c.members, 'участник', 'участника', 'участников')}"
    head += "</i>"
    lines = []
    for m in msgs:
        who = "Вы" if m.me else escape(m.who)
        lines.append(f"<b>{who}</b>  <code>{m.time}</code>\n{escape(m.text)}")
    body = "\n\n".join(lines) if lines else "<i>Сообщений пока нет</i>"
    text = f"{head}\n━━━━━━━━━━━━━━━━\n{body}\n━━━━━━━━━━━━━━━━"
    if note:
        text += f"\n{note}"
    return text, len(c.msgs) > shown


def dialog_screen(a: Account, c: Chat, back: str, more: int = 0, note: str = "") -> Text:
    c.unread = 0
    text, has_more = dialog_text(a, c, more, note)
    base = f"{a.id}:{c.id}:{back}"
    rows = [[btn("⬆ Раньше", f"dlg:{base}:{more + 1}")] if has_more else [], ]
    rows[0].append(btn("🔄 Обновить", f"dlg:{base}:{more}"))
    if c.kind == "channel":
        rows.append([btn("📢 Канал — только чтение", "noop")])
    else:
        rows.append([btn("✍ Ответить", f"rep:{base}", "primary")])
    rows.append([btn("🔔 Включить звук" if c.muted else "🔕 Заглушить", f"mute:{base}")])
    rows.append([btn("← Назад", back_data(a, back))])
    return Text(text, kb(*rows))


# ─── Входящие ───────────────────────────────────────────────────────────────

def inbox_card(page: int) -> Card:
    items: list[tuple[Account, Chat]] = []
    per_account = []
    for a in ACCOUNTS.values():
        live = a.status == "online" and a.notify
        chats = [c for c in a.chats.values() if c.unread and not c.muted] if live else []
        items += [(a, c) for c in sorted(chats, key=lambda c: -c.unread)]
        per_account.append(dict(a=a, count=sum(c.unread for c in chats)))
    total = sum(x["count"] for x in per_account)
    pages = max(1, (len(items) + PAGE - 1) // PAGE)
    page = min(max(page, 0), pages - 1)
    rows = [[btn(f"#{a.id} · {chat_label(c)}", f"dlg:{a.id}:{c.id}:i")] for a, c in items[page * PAGE:(page + 1) * PAGE]]
    rows.append(pager("inbox", page, pages))
    rows.append([btn("← Главная", "home")])
    if items:
        top = [f"• <b>{escape(c.title)}</b> <i>(#{a.id})</i>: {snippet(c.last.text, 34)}" for a, c in items[:3]]
        caption = "\n".join(top) + "\n\n<i>Прочитанным станет только тот чат, который откроете.</i>"
    else:
        caption = "Новых сообщений нет ✓"
    return Card("inbox.html", caption, kb(*rows), dict(
        total=total, chats=len(items), chats_word=plural(len(items), "чате", "чатах", "чатах"),
        per_account=per_account))


# ─── Настройки аккаунта ─────────────────────────────────────────────────────

def settings_card(a: Account, confirm: bool = False) -> Card:
    muted = [c.title for c in a.chats.values() if c.muted]
    ctx = dict(a=a, eyebrow=f"Аккаунт #{a.id}", title="Настройки",
               sub=f"Уведомления: {'включены' if a.notify else 'выключены'}",
               icon_name="settings", tile_bg="#1E222B", icon_color="#9AA1B2", page_label="")
    if confirm:
        return Card("list.html", "🚪 <b>Завершить сессию?</b>\nАккаунт станет 🟠 «Нужен вход», войти можно будет заново из меню.",
                    kb([btn("Да, завершить", f"setxok:{a.id}", "danger")], [btn("Отмена", f"set:{a.id}")]), ctx)
    caption = "Уведомления о новых сообщениях этого аккаунта попадают во «📥 Входящие»."
    return Card("list.html", caption, kb(
        [btn("🔔 Уведомления: вкл" if a.notify else "🔕 Уведомления: выкл", f"setn:{a.id}")],
        [btn(f"🔕 Заглушённые чаты · {len(muted)}", f"setm:{a.id}")],
        [btn("🚪 Завершить сессию", f"setx:{a.id}", "danger")],
        [btn("← Назад", f"acc:{a.id}")],
    ), ctx)


# ─── Хендлеры ───────────────────────────────────────────────────────────────

def get_acc(acc_id: str) -> Account | None:
    return ACCOUNTS.get(int(acc_id))


async def go(cq: CallbackQuery, ui: UI, screen, alert: str | None = None) -> None:
    await cq.answer(alert or None, show_alert=bool(alert))
    ui.attach(cq.from_user.id, cq.message)
    await ui.show(cq.from_user.id, cq.message.chat.id, screen)


async def gone(cq: CallbackQuery, ui: UI) -> None:
    await go(cq, ui, home_card(), "Этого аккаунта больше нет")


@router.message(CommandStart())
async def start(message: Message, state: FSMContext, ui: UI) -> None:
    await state.clear()
    await ui.drop(message.from_user.id)
    await ui.show(message.from_user.id, message.chat.id, home_card())
    try:
        await message.delete()
    except Exception:
        pass


@router.callback_query(F.data == "noop")
async def noop(cq: CallbackQuery) -> None:
    await cq.answer()


@router.callback_query(F.data == "home")
async def home(cq: CallbackQuery, state: FSMContext, ui: UI) -> None:
    await state.clear()
    await go(cq, ui, home_card())


@router.callback_query(F.data.startswith("acc:"))
async def account(cq: CallbackQuery, state: FSMContext, ui: UI) -> None:
    await state.clear()
    a = get_acc(cq.data.split(":")[1])
    if not a:
        return await gone(cq, ui)
    await go(cq, ui, account_card(a))


def usable(a: Account | None) -> bool:
    return bool(a) and a.status == "online"


@router.callback_query(F.data.startswith("ls:"))
async def lists(cq: CallbackQuery, ui: UI) -> None:
    _, acc_id, kind, page = cq.data.split(":")
    a = get_acc(acc_id)
    if not usable(a):
        return await gone(cq, ui) if not a else await go(cq, ui, account_card(a))
    await go(cq, ui, list_card(a, kind, int(page), cq.from_user.id))


@router.callback_query(F.data.startswith("ct:"))
async def contact_without_dialog(cq: CallbackQuery) -> None:
    await cq.answer("С этим контактом ещё нет диалога. По плану v0.1 писать можно только в существующие диалоги — так аккаунты не улетят в бан за спам.", show_alert=True)


@router.callback_query(F.data.startswith("dlg:"))
async def dialog(cq: CallbackQuery, state: FSMContext, ui: UI) -> None:
    await state.clear()
    parts = cq.data.split(":")
    a = get_acc(parts[1])
    if not usable(a) or parts[2] not in a.chats:
        return await gone(cq, ui) if not a else await go(cq, ui, account_card(a))
    more = int(parts[4]) if len(parts) > 4 else 0
    c = a.chats[parts[2]]
    audit("Ты", f"#{a.id} открыл «{c.title}»")
    await go(cq, ui, dialog_screen(a, c, parts[3], more))


@router.callback_query(F.data.startswith("mute:"))
async def mute(cq: CallbackQuery, ui: UI) -> None:
    _, acc_id, cid, back = cq.data.split(":")
    a = get_acc(acc_id)
    if not usable(a) or cid not in a.chats:
        return await gone(cq, ui)
    c = a.chats[cid]
    c.muted = not c.muted
    await go(cq, ui, dialog_screen(a, c, back), None)


@router.callback_query(F.data.startswith("rep:"))
async def reply_start(cq: CallbackQuery, state: FSMContext, ui: UI) -> None:
    _, acc_id, cid, back = cq.data.split(":")
    a = get_acc(acc_id)
    if not usable(a) or cid not in a.chats:
        return await gone(cq, ui)
    await state.set_state(Input.reply)
    await state.update_data(acc=a.id, chat=cid, back=back)
    c = a.chats[cid]
    text, _ = dialog_text(a, c, note=f"\n✍️ <b>Напишите ответ сообщением</b> — отправлю от имени {escape(a.name)}.")
    await go(cq, ui, Text(text, kb([btn("✕ Отмена", f"dlg:{a.id}:{cid}:{back}", "danger")])))


@router.message(Input.reply, F.text)
async def reply_send(message: Message, state: FSMContext, ui: UI) -> None:
    data = await state.get_data()
    await state.clear()
    try:
        await message.delete()
    except Exception:
        pass
    a = ACCOUNTS.get(data["acc"])
    if not usable(a) or data["chat"] not in a.chats:
        return await ui.show(message.from_user.id, message.chat.id, home_card())
    c = a.chats[data["chat"]]
    c.msgs.append(Msg(True, "Вы", now_hm(), message.text[:1000]))
    audit("Ты", f"#{a.id} ответил в «{c.title}»")
    note = f"✅ Отправлено от имени {escape(a.name)} <i>(демо — никуда не ушло)</i>"
    await ui.show(message.from_user.id, message.chat.id, dialog_screen(a, c, data["back"], note=note))


@router.callback_query(F.data.startswith("srch:"))
async def search_start(cq: CallbackQuery, state: FSMContext, ui: UI) -> None:
    a = get_acc(cq.data.split(":")[1])
    if not usable(a):
        return await gone(cq, ui)
    await state.set_state(Input.search)
    await state.update_data(acc=a.id)
    SEARCH.pop(cq.from_user.id, None)
    title, icon_name, tile_bg, icon_color = LIST_META["s"]
    card = Card("list.html",
                "🔎 <b>Напишите запрос сообщением.</b>\nИщу по названиям чатов и тексту сообщений.\nПопробуйте: <code>сервер</code>, <code>завтра</code>, <code>роутер</code>",
                kb([btn("← Назад", f"acc:{a.id}")]),
                dict(a=a, eyebrow=f"Аккаунт #{a.id}", title="Поиск", sub="Жду запрос…",
                     icon_name=icon_name, tile_bg=tile_bg, icon_color=icon_color, page_label=""))
    await go(cq, ui, card)


@router.message(Input.search, F.text)
async def search_run(message: Message, state: FSMContext, ui: UI) -> None:
    data = await state.get_data()
    await state.clear()
    try:
        await message.delete()
    except Exception:
        pass
    a = ACCOUNTS.get(data["acc"])
    if not usable(a):
        return await ui.show(message.from_user.id, message.chat.id, home_card())
    q = message.text.strip()[:40]
    ql = q.lower()
    ids = [c.id for c in a.chats.values()
           if ql in c.title.lower() or any(ql in m.text.lower() for m in c.msgs)]
    SEARCH[message.from_user.id] = (a.id, q, ids)
    await ui.show(message.from_user.id, message.chat.id, list_card(a, "s", 0, message.from_user.id))


@router.callback_query(F.data.startswith("inbox:"))
async def inbox(cq: CallbackQuery, state: FSMContext, ui: UI) -> None:
    await state.clear()
    await go(cq, ui, inbox_card(int(cq.data.split(":")[1])))


@router.callback_query(F.data.regexp(r"^set[nmx]?(ok)?:"))
async def settings(cq: CallbackQuery, ui: UI) -> None:
    action, acc_id = cq.data.split(":")
    a = get_acc(acc_id)
    if not usable(a):
        return await gone(cq, ui) if not a else await go(cq, ui, account_card(a))
    if action == "setn":
        a.notify = not a.notify
        audit("Ты", f"#{a.id} уведомления {'вкл' if a.notify else 'выкл'}")
    elif action == "setm":
        muted = [c.title for c in a.chats.values() if c.muted]
        return await cq.answer("Заглушены: " + ", ".join(muted) if muted else "Заглушённых чатов нет. Заглушить можно из переписки.", show_alert=True)
    elif action == "setx":
        return await go(cq, ui, settings_card(a, confirm=True))
    elif action == "setxok":
        a.status = "need_login"
        audit("Ты", f"#{a.id} завершил сессию")
        return await go(cq, ui, account_card(a), "Сессия завершена (демо)")
    await go(cq, ui, settings_card(a))
