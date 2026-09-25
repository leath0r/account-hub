"""Входящие со всех аккаунтов и кнопки из пуш-уведомлений (Открыть · Ответить · Прочитано)."""
from contextlib import suppress
from html import escape

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from access import Viewer
from screens.common import PAGE, alert, card, chat_label, go, gone, ints, pager, short, snippet, title, unread_by_account, usable, visible
from screens.dialog import dialog_screen, reply_screen
from screens.lists import read_all
from tg import Hub, HubError
from ui import UI, Card, btn, kb

router = Router()


async def inbox_card(hub: Hub, me: Viewer, page: int) -> Card:
    accs = visible(hub, me)
    unread = await unread_by_account(hub, me, accs)
    items = sorted(((a, c) for a in accs for c in unread[a.id]), key=lambda x: -x[1].last_ts)
    per_account = [dict(a=a, count=sum(c.unread for c in unread[a.id])) for a in accs]
    total = sum(x["count"] for x in per_account)
    pages = max(1, (len(items) + PAGE - 1) // PAGE)
    page = min(max(page, 0), pages - 1)
    rows = [[btn(f"#{a.id} · {chat_label(me, c, set())}", f"dlg:{a.id}:{c.id}:i")] for a, c in items[page * PAGE:(page + 1) * PAGE]]
    rows.append(pager("inbox", page, pages))
    if items and any(me.can_write(a.id) for a, _ in items):
        rows.append([btn(me.t("✓ Прочитать всё"), "rdi", "success")])
    rows.append([btn(me.t("🔄 Обновить"), f"inbox:{page}"), btn(me.t("← Главная"), "home")])
    if items:
        top = [f"• <b>{escape(short(title(me, c)))}</b> <i>(#{a.id})</i>: {snippet(c.last_text, 34)}" for a, c in items[:3]]
        caption = "\n".join(top) + "\n\n<i>" + me.t("Прочитанным станет только тот чат, который откроете.") + "</i>"
    else:
        caption = me.t("Новых сообщений нет ✓")
    return card(me, "inbox.html", caption, kb(*rows),
                total=total, chats=len(items), chats_word=me.pl(len(items), "чате", "чатах", "чатах"),
                per_account=per_account)


@router.callback_query(F.data.startswith("inbox:"))
async def inbox(cq: CallbackQuery, state: FSMContext, ui: UI, hub: Hub, me: Viewer) -> None:
    await state.clear()
    page = cq.data.split(":")[1]
    await go(cq, ui, me, inbox_card(hub, me, int(page) if page.isdigit() else 0))


@router.callback_query(F.data == "rdi")
async def read_all_inbox(cq: CallbackQuery, ui: UI, hub: Hub, me: Viewer) -> None:
    done = 0
    for a in visible(hub, me):
        if usable(a) and me.can_write(a.id):
            with suppress(HubError):
                done += await read_all(hub, me, a)
    await go(cq, ui, me, inbox_card(hub, me, 0), me.t("Прочитано чатов: {n}", n=done))


# ─── Кнопки из уведомлений ──────────────────────────────────────────────────

async def adopt(ui: UI, me: Viewer, cq: CallbackQuery) -> None:
    """Уведомление становится экраном, прежний экран — удаляется: в чате с ботом остаётся одно сообщение."""
    await ui.adopt(me.id, cq.message)


@router.callback_query(F.data.regexp(r"^nf[opr]:"))
async def from_notification(cq: CallbackQuery, state: FSMContext, ui: UI, hub: Hub, me: Viewer) -> None:
    op = cq.data[:3]
    nums = ints(*cq.data.split(":")[1:3])
    a = hub.accs.get(nums[0]) if nums and len(nums) == 2 else None
    if not usable(a):
        return await gone(cq, ui, hub, me)
    if op == "nfr":
        try:
            c = await hub.chat(a.id, nums[1])
            await hub.mark_read(a.id, c)
        except HubError as e:
            return await alert(cq, me.err(e))
        with suppress(TelegramBadRequest):
            await cq.answer(me.t("Прочитано ✓"))
        with suppress(Exception):
            await cq.message.delete()
        return
    await state.clear()
    await adopt(ui, me, cq)
    if op == "nfp":
        return await go(cq, ui, me, reply_screen(hub, me, state, a, nums[1], "i"))

    async def build():
        return await dialog_screen(hub, me, a, await hub.chat(a.id, nums[1]), "i")

    await go(cq, ui, me, build())


@router.callback_query(F.data == "nfi")
async def notification_inbox(cq: CallbackQuery, state: FSMContext, ui: UI, hub: Hub, me: Viewer) -> None:
    await state.clear()
    await adopt(ui, me, cq)
    await go(cq, ui, me, inbox_card(hub, me, 0))
