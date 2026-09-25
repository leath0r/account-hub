"""Поиск: по одному аккаунту и сразу по всем. @username → переписка или «Написать первым»."""
import asyncio
from contextlib import suppress
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from access import Viewer
from screens.common import (GSEARCH, LIST_META, PAGE, PEOPLE, SEARCH, Input, alert, card, chat_label, err_line, go, gone,
                            handle, ints, pager, record, short, usable, visible)
from screens.dialog import dialog_screen
from screens.lists import Face, list_card
from tg import STYLES, Account, Hub, HubError, Person, parse_username, photo_uri
from ui import UI, Card, Text, btn, kb

router = Router()


# ─── Новый собеседник (по @username или из контактов) ───────────────────────

async def person_card(hub: Hub, me: Viewer, a: Account, p: Person, error: str = "") -> Card:
    rows = []
    if p.kind in ("group", "channel"):
        caption = (me.t("👥 Группа <b>{name}</b> — аккаунт #{n} в ней не состоит.", name=escape(p.name), n=a.id)
                   if p.kind == "group" else
                   me.t("📢 Канал <b>{name}</b> — аккаунт #{n} в нём не состоит.", name=escape(p.name), n=a.id))
        caption += "\n" + me.t("Вступать через бота пока нельзя.")
    else:
        caption = ((me.t("🤖 Бот") + " " if p.kind == "bot" else "") + f"<b>{escape(p.name)}</b> · {escape(handle(me, p))}\n"
                   + me.t("Переписки с ним у аккаунта #{n} ещё нет.", n=a.id))
        left = await hub.first_contacts_left(a.id)
        if not me.can_write(a.id):
            caption += "\n<i>" + me.t("👁 У вас доступ только на чтение — написать первым нельзя.") + "</i>"
        elif left == 0:
            caption += "\n⏳ " + me.t("Лимит новых переписок на сутки исчерпан ({n}). Продолжить можно завтра.",
                                      n=hub.cfg.new_chats_per_day)
        else:
            rows.append([btn(me.t("✍️ Написать первым"), f"new:{a.id}:{p.id}", "primary")])
            if left is not None:
                caption += "\n<i>" + me.t("Сегодня этот аккаунт может начать ещё {n} {word}.", n=left,
                                          word=me.pl(left, "новую переписку", "новые переписки", "новых переписок")) + "</i>"
    if error:
        caption = f"{err_line(me, error)}\n\n{caption}"
    rows.append([btn(me.t("🔎 Новый поиск"), f"srch:{a.id}"), btn(me.t("← Назад"), f"acc:{a.id}")])
    _, icon_name, tile_bg, icon_color = LIST_META["k"]
    photo = await hub.chat_photo(a.id, p.id) if p.kind in ("user", "bot") else None
    face = Face((p.name[:1] or "?").upper(), STYLES[abs(p.id) % len(STYLES)], photo_uri(photo))
    return card(me, "list.html", caption, kb(*rows), a=a,
                eyebrow=f"{me.t('Аккаунт #{n}', n=a.id)} · {me.t('новый собеседник')}", title=short(p.name, 22),
                sub=handle(me, p), person_face=face, icon_name=icon_name, tile_bg=tile_bg, icon_color=icon_color,
                page_label="")


async def find_person(hub: Hub, me: Viewer, a: Account, username: str) -> Card | Text:
    """@username: есть переписка — открыть её, нет — карточка человека с «Написать первым»."""
    p = await hub.resolve(a.id, username)
    if p.id in {c.id for c in await hub.dialogs(a.id)}:
        SEARCH[me.id] = (a.id, f"@{p.username}", [p.id])
        return await dialog_screen(hub, me, a, await hub.chat(a.id, p.id), "s")
    PEOPLE[me.id] = (a.id, p)
    return await person_card(hub, me, a, p)


def search_prompt(me: Viewer, a: Account | None) -> Card:
    title_key, icon_name, tile_bg, icon_color = LIST_META["s"]
    if a:
        caption = me.t("🔎 <b>Напишите запрос сообщением.</b>\nИщу по названиям чатов и тексту сообщений этого аккаунта.\n"
                       "Чтобы найти человека и написать ему — отправьте <code>@username</code> или ссылку <code>t.me/…</code>")
        back, eyebrow = f"acc:{a.id}", me.t("Аккаунт #{n}", n=a.id)
    else:
        caption = me.t("🔎 <b>Поиск по всем аккаунтам.</b>\nНапишите запрос — поищу в чатах и сообщениях каждого доступного аккаунта.\n"
                       "<code>@username</code> — покажу, в каких аккаунтах уже есть переписка с этим человеком.")
        back, eyebrow = "home", "Account Hub"
    return card(me, "list.html", caption, kb([btn(me.t("← Назад"), back)]), a=a, eyebrow=eyebrow,
                title=me.t("Поиск везде") if a is None else me.t(title_key), sub=me.t("Жду запрос…"),
                icon_name=icon_name, tile_bg=tile_bg, icon_color=icon_color, page_label="")


@router.callback_query(F.data.startswith("srch:"))
async def search_start(cq: CallbackQuery, state: FSMContext, ui: UI, hub: Hub, me: Viewer) -> None:
    from screens.account import account_card
    a = hub.accs.get(int(cq.data.split(":")[1]))
    if not usable(a):
        return await gone(cq, ui, hub, me) if not a else await go(cq, ui, me, account_card(hub, me, a))
    await state.set_state(Input.search)
    await state.update_data(acc=a.id)
    SEARCH.pop(me.id, None)
    await go(cq, ui, me, search_prompt(me, a))


@router.message(Input.search, F.text)
async def search_run(message: Message, state: FSMContext, ui: UI, hub: Hub, me: Viewer) -> None:
    from screens.home import home_card
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
        await record(hub, me, a.id, "search", "#{n} искал «{q}»", n=a.id, q=q)
        if username:
            screen = await find_person(hub, me, a, username)
        else:
            found = await hub.search(a.id, q)
            SEARCH[me.id] = (a.id, q, [c.id for c in found])
            screen = await list_card(hub, me, a, "s", 0)
    except HubError as e:
        SEARCH[me.id] = (a.id, q, [])
        screen = await list_card(hub, me, a, "s", 0)
        screen.caption = f"{err_line(me, e)}\n\n{screen.caption}"
    await ui.show(me.id, message.chat.id, screen)


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

    await go(cq, ui, me, build())


@router.callback_query(F.data.startswith("pp:"))
async def person_back(cq: CallbackQuery, state: FSMContext, ui: UI, hub: Hub, me: Viewer) -> None:
    from screens.account import account_card
    from screens.home import home_card
    await state.clear()
    nums = ints(*cq.data.split(":")[1:3])
    a = hub.accs.get(nums[0]) if nums and len(nums) == 2 else None
    found = PEOPLE.get(me.id)
    if not usable(a) or not found or found[0] != a.id or found[1].id != nums[1]:
        return await go(cq, ui, me, home_card(hub, me)) if not a else await go(cq, ui, me, account_card(hub, me, a))
    await go(cq, ui, me, person_card(hub, me, a, found[1]))


@router.callback_query(F.data.startswith("new:"))
async def first_start(cq: CallbackQuery, state: FSMContext, ui: UI, hub: Hub, me: Viewer) -> None:
    nums = ints(*cq.data.split(":")[1:3])
    a = hub.accs.get(nums[0]) if nums and len(nums) == 2 else None
    if not usable(a):
        return await gone(cq, ui, hub, me)
    found = PEOPLE.get(me.id)
    if not found or found[0] != a.id or found[1].id != nums[1]:
        return await alert(cq, me.t("Найдите человека заново через 🔎 Поиск"))
    p = found[1]
    if await hub.first_contacts_left(a.id) == 0:
        return await alert(cq, me.t("Лимит новых переписок для этого аккаунта на сутки исчерпан"))
    await state.set_state(Input.first)
    await state.update_data(acc=a.id, pid=p.id)
    text = (me.t("✍️ <b>Первое сообщение</b>") + "\n━━━━━━━━━━━━━━━━\n"
            + me.t("Кому: <b>{name}</b> · {handle}", name=escape(p.name), handle=escape(handle(me, p))) + "\n"
            + me.t("От: аккаунт #{n} · {name}", n=a.id, name=escape(a.name)) + "\n━━━━━━━━━━━━━━━━\n"
            + me.t("Напишите текст сообщением — отправлю сразу."))
    await go(cq, ui, me, Text(text, kb([btn(me.t("✕ Отмена"), f"pp:{a.id}:{p.id}", "danger")])))


@router.message(Input.first, F.text)
async def first_send(message: Message, state: FSMContext, ui: UI, hub: Hub, me: Viewer) -> None:
    from screens.account import account_card
    from screens.home import home_card
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
        return await ui.show(me.id, message.chat.id, await person_card(hub, me, a, p, error=me.err(e)))
    await hub.db.add_first_contact(a.id, p.id)
    await record(hub, me, a.id, "first", "#{n} написал первым {handle} ({name})", n=a.id, handle=f"@{p.username}" if p.username else "—",
                 name=p.name)
    PEOPLE.pop(me.id, None)
    try:
        c = await hub.chat(a.id, p.id)
        screen = await dialog_screen(hub, me, a, c, "c", note="✅ " + me.t("Отправлено от имени {name}", name=escape(a.name)))
    except HubError:
        screen = await account_card(hub, me, a)
    await ui.show(me.id, message.chat.id, screen)


# ─── Поиск по всем аккаунтам ────────────────────────────────────────────────

@router.callback_query(F.data == "gs")
async def global_start(cq: CallbackQuery, state: FSMContext, ui: UI, me: Viewer) -> None:
    await state.set_state(Input.gsearch)
    GSEARCH.pop(me.id, None)
    await go(cq, ui, me, search_prompt(me, None))


async def global_results(hub: Hub, me: Viewer, page: int, note: str = "") -> Card:
    q, found = GSEARCH.get(me.id, ("", []))
    accs = {a.id: a for a in visible(hub, me)}
    rows_found = []
    for acc_id, chat_id in found:
        c = next((x for x in hub.cached_dialogs(acc_id) if x.id == chat_id), None) if acc_id in accs else None
        if c:
            rows_found.append((c.last_ts, f"#{acc_id} · {chat_label(me, c, set())}", f"dlg:{acc_id}:{chat_id}:g"))
    # все аккаунты вперемешку, свежие сверху — как одна лента
    items = [(text, data) for _, text, data in sorted(rows_found, key=lambda r: -r[0])]
    pages = max(1, (len(items) + PAGE - 1) // PAGE)
    page = min(max(page, 0), pages - 1)
    rows = [[btn(text, data)] for text, data in items[page * PAGE:(page + 1) * PAGE]]
    rows.append(pager("gsl", page, pages))
    rows.append([btn(me.t("🔎 Новый поиск"), "gs"), btn(me.t("← Главная"), "home")])
    caption = me.t("Нажмите на чат, чтобы открыть переписку.") if items else me.t("Ничего не нашлось — попробуйте другой запрос.")
    if note:
        caption = f"{note}\n\n{caption}"
    title_key, icon_name, tile_bg, icon_color = LIST_META["s"]
    return card(me, "list.html", caption, kb(*rows), a=None, eyebrow=me.t("Поиск везде"), title=f"«{short(q, 22)}»",
                sub=me.t("Найдено: {n}", n=len(items)) + f" · {len({i[1].split(':')[1] for i in items})} "
                    + me.pl(len({i[1].split(':')[1] for i in items}), "аккаунт", "аккаунта", "аккаунтов"),
                icon_name=icon_name, tile_bg=tile_bg, icon_color=icon_color,
                page_label=me.t("стр. {page} / {pages}", page=page + 1, pages=pages) if pages > 1 else "")


@router.message(Input.gsearch, F.text)
async def global_run(message: Message, state: FSMContext, ui: UI, hub: Hub, me: Viewer) -> None:
    await state.clear()
    with suppress(Exception):
        await message.delete()
    q = message.text.strip()[:64]
    accs = [a for a in visible(hub, me) if usable(a)]
    username = parse_username(q)
    await record(hub, me, None, "search", "искал везде «{q}»", q=q)
    note = ""
    if username and accs:
        try:
            p = await hub.resolve(accs[0].id, username)
        except HubError as e:
            GSEARCH[me.id] = (q, [])
            return await ui.show(me.id, message.chat.id, await global_results(hub, me, 0, err_line(me, e)))
        res = await asyncio.gather(*(hub.dialogs(a.id) for a in accs), return_exceptions=True)
        found = [(a.id, p.id) for a, chats in zip(accs, res) if isinstance(chats, list) and any(c.id == p.id for c in chats)]
        if not found:  # переписки нет нигде — сразу к «Написать первым» от первого аккаунта, где можно писать
            target = next((a for a in accs if me.can_write(a.id)), accs[0])
            PEOPLE[me.id] = (target.id, p)
            return await ui.show(me.id, message.chat.id, await person_card(hub, me, target, p))
        GSEARCH[me.id] = (q, found)
        note = me.t("<b>{name}</b> — переписка есть в {n} {word}.", name=escape(p.name), n=len(found),
                    word=me.pl(len(found), "аккаунте", "аккаунтах", "аккаунтах"))
    else:
        res = await asyncio.gather(*(hub.search(a.id, q) for a in accs), return_exceptions=True)
        GSEARCH[me.id] = (q, [(a.id, c.id) for a, chats in zip(accs, res) if isinstance(chats, list) for c in chats])
    await ui.show(me.id, message.chat.id, await global_results(hub, me, 0, note))


@router.callback_query(F.data.startswith("gsl:"))
async def global_page(cq: CallbackQuery, ui: UI, hub: Hub, me: Viewer) -> None:
    page = cq.data.split(":")[1]
    if me.id not in GSEARCH:
        return await alert(cq, me.t("Поиск устарел — начните заново"))
    await go(cq, ui, me, global_results(hub, me, int(page) if page.isdigit() else 0))
