"""Действия над одним сообщением: удалить, поставить реакцию, переслать (в том числе с другого аккаунта).

Общая схема: «выбери сообщение» → «выбери вариант» → сделать и вернуться в переписку с пометкой.
  dls/dlc/dlx — удаление · rcs/rcc/rcx — реакция · fws/fwa/fwc/fwx — пересылка
"""
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from access import Viewer
from screens.common import LINE, PAGE, alert, go, gone, ints, pager, record, short, title, usable
from screens.dialog import dialog_head, dialog_screen, msg_who
from tg import Account, Chat, Hub, HubError, Msg
from ui import UI, Text, btn, kb

router = Router()
PICK = 8
REACTIONS = ["👍", "❤️", "🔥", "😂", "😮", "😢", "👎", "🎉", "🙏", "👌"]
PICK_META = {  # режим: (заголовок, код следующего шага)
    "d": ("🗑 <b>Какое сообщение удалить?</b>", "dlc"),
    "r": ("😊 <b>На какое сообщение поставить реакцию?</b>", "rcc"),
    "f": ("↪ <b>Какое сообщение переслать?</b>", "fwa"),
}


def quote(me: Viewer, m: Msg) -> str:
    return f"<b>{msg_who(me, m)}</b>  <code>{m.time}</code>\n{escape(short(m.text, 700))}"


async def find_msg(hub: Hub, a: Account, c: Chat, msg_id: int) -> Msg:
    m = next((m for m in await hub.messages(a.id, c, 40) if m.id == msg_id), None)
    if m is None:
        raise HubError("Этого сообщения уже нет")
    return m


async def pick_screen(hub: Hub, me: Viewer, a: Account, c: Chat, back: str, mode: str) -> Text:
    """В личке удалять можно и свои, и чужие (Telegram разрешает удалить у обоих), в группах — только свои."""
    own_only = mode == "d" and c.kind != "user"
    msgs = [m for m in await hub.messages(a.id, c, 20) if m.me or not own_only][-PICK:]
    head, nxt = PICK_META[mode]
    text = f"{dialog_head(me, a, c)}\n{LINE}\n{me.t(head)}"
    if own_only:
        text += "\n<i>" + me.t("В группах можно удалять только свои сообщения.") + "</i>"
    if not msgs:
        text += "\n\n<i>" + me.t("Выбирать не из чего.") + "</i>"
    rows = [[btn(f"{m.time} · {me.t('Вы') if m.me else short(m.who, 12)}: {short(m.text.replace(chr(10), ' '), 28)}",
                 f"{nxt}:{a.id}:{c.id}:{m.id}:{back}")] for m in reversed(msgs)]
    rows.append([btn(me.t("← К переписке"), f"dlg:{a.id}:{c.id}:{back}")])
    return Text(text, kb(*rows))


# ─── Удаление ───────────────────────────────────────────────────────────────

async def delete_confirm_screen(hub: Hub, me: Viewer, a: Account, c: Chat, msg_id: int, back: str) -> Text:
    m = await find_msg(hub, a, c, msg_id)
    text = f"{dialog_head(me, a, c)}\n{LINE}\n{me.t('🗑 <b>Удалить это сообщение?</b>')}\n\n{quote(me, m)}"
    base = f"{a.id}:{c.id}:{msg_id}:{back}"
    return Text(text, kb(
        [btn(me.t("🗑 Удалить у всех"), f"dlx:{base}:1", "danger")],
        [btn(me.t("Удалить только у себя"), f"dlx:{base}:0")],
        [btn(me.t("Отмена"), f"dls:{a.id}:{c.id}:{back}")],
    ))


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
            return await pick_screen(hub, me, a, c, back, "d")
        if op == "dlc":
            return await delete_confirm_screen(hub, me, a, c, nums[2], back)
        revoke = parts[5] == "1"
        await hub.delete(a.id, c, nums[2], revoke)
        await record(hub, me, a.id, "delete", "#{n} удалил сообщение в «{chat}» у всех" if revoke
                     else "#{n} удалил сообщение в «{chat}» у себя", n=a.id, chat=c.title)
        note = "🗑 " + (me.t("Удалено у всех") if revoke else me.t("Удалено только у аккаунта"))
        return await dialog_screen(hub, me, a, c, back, note=note)

    await go(cq, ui, me, build())


# ─── Реакции ────────────────────────────────────────────────────────────────

@router.callback_query(F.data.regexp(r"^rc[scx]:"))
async def react_flow(cq: CallbackQuery, state: FSMContext, ui: UI, hub: Hub, me: Viewer) -> None:
    """rcs:<акк>:<чат>:<откуда> — выбор сообщения · rcc:…:<msg>:<откуда> — выбор реакции · rcx:…:<откуда>:<номер|x>."""
    await state.clear()
    parts = cq.data.split(":")
    op = parts[0]
    nums = ints(*(parts[1:3] if op == "rcs" else parts[1:4]))
    a = hub.accs.get(nums[0]) if nums else None
    if not usable(a):
        return await gone(cq, ui, hub, me)
    back = parts[3] if op == "rcs" else parts[4]

    async def build() -> Text:
        c = await hub.chat(a.id, nums[1])
        if op == "rcs":
            return await pick_screen(hub, me, a, c, back, "r")
        base = f"{a.id}:{c.id}:{nums[2]}:{back}"
        if op == "rcc":
            m = await find_msg(hub, a, c, nums[2])
            buttons = [btn(e, f"rcx:{base}:{i}") for i, e in enumerate(REACTIONS)]
            rows = [buttons[i:i + 5] for i in range(0, len(buttons), 5)]
            rows += [[btn(me.t("✕ Убрать мою реакцию"), f"rcx:{base}:x")], [btn(me.t("Отмена"), f"rcs:{a.id}:{c.id}:{back}")]]
            return Text(f"{dialog_head(me, a, c)}\n{LINE}\n{me.t('😊 <b>Какую реакцию поставить?</b>')}\n\n{quote(me, m)}",
                        kb(*rows))
        choice = parts[5]
        emoji = None if choice == "x" else REACTIONS[int(choice)] if choice.isdigit() and int(choice) < len(REACTIONS) else None
        await hub.react(a.id, c, nums[2], emoji)
        await record(hub, me, a.id, "react", "#{n} поставил реакцию {emoji} в «{chat}»", n=a.id, emoji=emoji or "✕",
                     chat=c.title)
        note = me.t("😊 Реакция поставлена: {emoji}", emoji=emoji) if emoji else me.t("😊 Реакция убрана")
        return await dialog_screen(hub, me, a, c, back, note=note)

    await go(cq, ui, me, build())


# ─── Пересылка ──────────────────────────────────────────────────────────────

def writable(hub: Hub, me: Viewer) -> list[Account]:
    return [x for x in hub.accounts() if me.can_write(x.id) and usable(x)]


@router.callback_query(F.data.regexp(r"^fw[sacx]:"))
async def forward_flow(cq: CallbackQuery, state: FSMContext, ui: UI, hub: Hub, me: Viewer) -> None:
    """fws:<акк>:<чат>:<откуда> — выбор сообщения · fwa:…:<msg>:<откуда> — с какого аккаунта ·
    fwc:…:<откуда>:<акк-цель>:<стр> — в какой чат · fwx:…:<откуда>:<акк-цель>:<чат-цель> — переслать."""
    await state.clear()
    parts = cq.data.split(":")
    op = parts[0]
    nums = ints(*(parts[1:3] if op == "fws" else parts[1:4]))
    a = hub.accs.get(nums[0]) if nums else None
    if not usable(a):
        return await gone(cq, ui, hub, me)
    back = parts[3] if op == "fws" else parts[4]
    target = None
    if op in ("fwc", "fwx"):
        t_ids = ints(parts[5], parts[6]) if len(parts) > 6 else None
        target = hub.accs.get(t_ids[0]) if t_ids else None
        if not usable(target) or not me.can_write(target.id):
            return await alert(cq, me.t("Нет доступа к этому аккаунту"))

    async def build() -> Text:
        c = await hub.chat(a.id, nums[1])
        if op == "fws":
            return await pick_screen(hub, me, a, c, back, "f")
        m = await find_msg(hub, a, c, nums[2])
        base = f"{a.id}:{c.id}:{nums[2]}:{back}"
        head = f"{dialog_head(me, a, c)}\n{LINE}\n{quote(me, m)}\n{LINE}\n"
        if op == "fwa":
            rows = [[btn(f"{x.status_emoji} #{x.id} {short(x.name, 20)}" + (" ✓" if x.id == a.id else ""),
                         f"fwc:{base}:{x.id}:0")] for x in writable(hub, me)]
            rows.append([btn(me.t("Отмена"), f"fws:{a.id}:{c.id}:{back}")])
            return Text(head + me.t("↪ <b>С какого аккаунта переслать?</b>\n<i>Тот же аккаунт — обычная пересылка с подписью «Переслано». "
                                    "Другой аккаунт — сообщение отправится копией: чужой чат ему не виден.</i>"), kb(*rows))
        if op == "fwc":
            chats = [x for x in await hub.dialogs(target.id) if x.kind != "channel" and not (target.id == a.id and x.id == c.id)]
            page, pages = int(parts[6]) if parts[6].isdigit() else 0, max(1, (len(chats) + PAGE - 1) // PAGE)
            page = min(page, pages - 1)
            rows = [[btn(f"{'👥 ' if x.kind == 'group' else ''}{short(title(me, x))}", f"fwx:{base}:{target.id}:{x.id}")]
                    for x in chats[page * PAGE:(page + 1) * PAGE]]
            rows.append(pager(f"fwc:{base}:{target.id}", page, pages))
            rows.append([btn(me.t("← Другой аккаунт"), f"fwa:{base}")])
            return Text(head + me.t("↪ <b>В какой чат переслать от #{n} {name}?</b>", n=target.id, name=escape(target.name)),
                        kb(*rows))
        dst = await hub.chat(target.id, int(parts[6]))
        how = await hub.forward(a.id, c, nums[2], target.id, dst)
        await record(hub, me, a.id, "forward", "#{n} переслал сообщение из «{src}» в «{dst}» (#{t})",
                     n=a.id, src=c.title, dst=dst.title, t=target.id)
        note = me.t("↪ Переслано в «{chat}» от #{n}", chat=escape(title(me, dst)), n=target.id)
        if how == "copy":
            note += " " + me.t("(копией)")
        return await dialog_screen(hub, me, a, c, back, note=note)

    await go(cq, ui, me, build())
