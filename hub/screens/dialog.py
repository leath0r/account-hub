"""Переписка: просмотр, ответ (текстом или вложением), заглушить, пометить непрочитанным."""
from contextlib import suppress
from html import escape

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from access import Viewer
from screens.common import (KIND_ICON, LINE, MSG_MAX, MSG_PAGE, TEXT_BUDGET, Input, back_data, go, gone, ints, record, short,
                            title, usable)
from tg import MEDIA_ICON, SPECIAL_TITLES, Account, Chat, Hub, HubError, Msg
from ui import UI, Text, btn, kb

router = Router()
MEDIA_BUTTONS = 6
BOT_FILE_MAX = 20 * 1024 * 1024   # Bot API отдаёт боту файлы до 20 МБ


def dialog_head(me: Viewer, a: Account, c: Chat) -> str:
    head = f"{KIND_ICON[c.kind] or '💬 '}<b>{escape(title(me, c))}</b>\n<i>" + me.t("через аккаунт #{n} · {name}", n=a.id, name=escape(a.name))
    if c.kind != "user" and c.members:
        head += f" · {c.members} {me.pl(c.members, 'участник', 'участника', 'участников')}"
    return head + "</i>"


def msg_who(me: Viewer, m: Msg) -> str:
    if m.me:
        return me.t("Вы")
    return escape(short(me.t(m.who) if m.who in SPECIAL_TITLES else m.who))


async def dialog_text(hub: Hub, me: Viewer, a: Account, c: Chat, more: int = 0, note: str = "") -> tuple[str, bool, list[Msg]]:
    """(текст экрана, есть ли сообщения раньше, какие сообщения показаны)."""
    limit = min(MSG_PAGE * (more + 1), MSG_MAX)
    msgs = await hub.messages(a.id, c, limit + 1)
    has_more = len(msgs) > limit and limit < MSG_MAX
    msgs = msgs[-limit:]

    head = dialog_head(me, a, c)
    lines, shown, size, cut = [], [], len(head) + len(note) + 80, False
    for m in reversed(msgs):  # с новых — если не влезает, режем старые
        line = f"<b>{msg_who(me, m)}</b>  <code>{m.time}</code>\n{escape(short(m.text, 700))}"
        if size + len(line) > TEXT_BUDGET and lines:
            cut = True
            break
        lines.append(line)
        shown.append(m)
        size += len(line) + 2
    lines.reverse()
    shown.reverse()
    if cut:
        lines.insert(0, "<i>" + me.t("…ранние сообщения не влезли") + "</i>")
        has_more = False
    body = "\n\n".join(lines) if lines else "<i>" + me.t("Сообщений пока нет") + "</i>"
    text = f"{head}\n{LINE}\n{body}\n{LINE}"
    if note:
        text += f"\n{note}"
    return text, has_more, shown


async def dialog_screen(hub: Hub, me: Viewer, a: Account, c: Chat, back: str, more: int = 0, note: str = "") -> Text:
    # Прочитанным чат помечает только тот, кто может отвечать: зритель состояние аккаунта не меняет.
    if c.unread and me.can_write(a.id):
        with suppress(HubError):
            await hub.mark_read(a.id, c)
    text, has_more, shown = await dialog_text(hub, me, a, c, more, note)
    base = f"{a.id}:{c.id}:{back}"
    top = [btn(me.t("⬆ Раньше"), f"dlg:{base}:{more + 1}")] if has_more else []
    top.append(btn(me.t("🔄 Обновить"), f"dlg:{base}:{more}"))
    rows = [top]
    # вложения из показанных сообщений — по кнопке бот пришлёт их как есть
    media = [m for m in shown if m.media in MEDIA_ICON][-MEDIA_BUTTONS:]
    buttons = [btn(f"{MEDIA_ICON[m.media]} {m.time}", f"med:{a.id}:{c.id}:{m.id}") for m in media]
    rows += [buttons[i:i + 3] for i in range(0, len(buttons), 3)]
    if c.kind == "channel":
        rows.append([btn(me.t("📢 Канал — только чтение"), "noop")])
        if me.can_write(a.id):
            rows.append([btn(me.t("😊 Реакция…"), f"rcs:{base}"), btn(me.t("↪ Переслать…"), f"fws:{base}")])
    elif me.can_write(a.id):
        rows.append([btn(me.t("✍ Ответить"), f"rep:{base}", "primary"), btn(me.t("😊 Реакция…"), f"rcs:{base}")])
        rows.append([btn(me.t("↪ Переслать…"), f"fws:{base}"), btn(me.t("🗑 Удалить…"), f"dls:{base}")])
    else:
        rows.append([btn(me.t("👁 Доступ только на чтение"), "noop")])
    muted = c.id in await hub.db.muted(me.id, a.id)
    mute = btn(me.t("🔔 Снова уведомлять") if muted else me.t("🔕 Заглушить"), f"mute:{base}")
    rows.append([btn(me.t("📌 Непрочитанным"), f"unr:{base}"), mute] if me.can_write(a.id) else [mute])
    rows.append([btn(me.t("← Назад"), back_data(a, back))])
    return Text(text, kb(*rows))


@router.callback_query(F.data.startswith("dlg:"))
async def dialog(cq: CallbackQuery, state: FSMContext, ui: UI, hub: Hub, me: Viewer) -> None:
    from screens.account import account_card
    await state.clear()
    parts = cq.data.split(":")  # dlg:<аккаунт>:<чат>:<откуда>[:<сколько раз «раньше»>]
    nums = ints(parts[1], parts[2], *parts[4:5]) if len(parts) >= 4 else None
    if not nums:
        return await cq.answer()
    a = hub.accs.get(nums[0])
    if not a:
        return await gone(cq, ui, hub, me)
    if not usable(a):
        return await go(cq, ui, me, account_card(hub, me, a))
    more = nums[2] if len(nums) > 2 else 0

    async def build() -> Text:
        c = await hub.chat(a.id, nums[1])
        if len(parts) == 4:  # открыл из списка, а не листает
            await record(hub, me, a.id, "open", "#{n} открыл «{chat}»", n=a.id, chat=c.title)
        return await dialog_screen(hub, me, a, c, parts[3], more)

    await go(cq, ui, me, build())


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

    await go(cq, ui, me, build())


@router.callback_query(F.data.startswith("unr:"))
async def mark_unread(cq: CallbackQuery, ui: UI, hub: Hub, me: Viewer) -> None:
    """Вернуть чат в непрочитанные и уйти из него — иначе экран переписки сразу пометил бы его прочитанным."""
    from screens.lists import list_card
    from screens.inbox import inbox_card
    _, acc_id, chat_id, back = cq.data.split(":")
    nums = ints(acc_id, chat_id)
    a = hub.accs.get(nums[0]) if nums else None
    if not usable(a):
        return await gone(cq, ui, hub, me)

    async def build():
        c = await hub.chat(a.id, nums[1])
        await hub.mark_unread(a.id, c)
        if back == "i":
            return await inbox_card(hub, me, 0)
        return await list_card(hub, me, a, back if back in ("c", "g", "u", "k", "s") else "c", 0)

    await go(cq, ui, me, build(), me.t("Чат помечен непрочитанным"))


def reply_prompt(me: Viewer, a: Account) -> str:
    return ("\n✍️ <b>" + me.t("Напишите ответ сообщением") + "</b> — "
            + me.t("отправлю от имени {name}. Можно прислать фото, видео, голосовое, кружок или файл (до 20 МБ).",
                   name=escape(a.name)))


async def reply_screen(hub: Hub, me: Viewer, state: FSMContext, a: Account, chat_id: int, back: str) -> Text:
    c = await hub.chat(a.id, chat_id)
    await state.set_state(Input.reply)
    await state.update_data(acc=a.id, chat=c.id, back=back)
    text, _, _ = await dialog_text(hub, me, a, c, note=reply_prompt(me, a))
    return Text(text, kb([btn(me.t("✕ Отмена"), f"dlg:{a.id}:{c.id}:{back}", "danger")]))


@router.callback_query(F.data.startswith("rep:"))
async def reply_start(cq: CallbackQuery, state: FSMContext, ui: UI, hub: Hub, me: Viewer) -> None:
    _, acc_id, chat_id, back = cq.data.split(":")
    nums = ints(acc_id, chat_id)
    a = hub.accs.get(nums[0]) if nums else None
    if not usable(a):
        return await gone(cq, ui, hub, me)
    await go(cq, ui, me, reply_screen(hub, me, state, a, nums[1], back))


def incoming_file(message: Message) -> tuple[str, str, str, int] | None:
    """(вид, file_id, имя файла, размер) для вложения, которое прислали боту."""
    if message.photo:
        p = message.photo[-1]
        return "photo", p.file_id, "photo.jpg", p.file_size or 0
    for kind, obj, name in (("voice", message.voice, "voice.ogg"), ("video_note", message.video_note, "video_note.mp4"),
                            ("gif", message.animation, "animation.mp4"), ("video", message.video, "video.mp4"),
                            ("audio", message.audio, "audio.mp3"), ("document", message.document, "file")):
        if obj:
            return kind, obj.file_id, getattr(obj, "file_name", None) or name, obj.file_size or 0
    return None


@router.message(Input.reply)
async def reply_send(message: Message, state: FSMContext, ui: UI, hub: Hub, me: Viewer, bot: Bot) -> None:
    from screens.account import account_card
    from screens.home import home_card
    data = await state.get_data()
    file = None if message.text else incoming_file(message)
    if not message.text and not file:  # стикер, опрос и прочее — отправлять не умеем, ждём дальше
        with suppress(Exception):
            await message.delete()
        return
    await state.clear()
    a = hub.accs.get(data.get("acc"))
    if not usable(a) or not me.can_write(a.id):
        with suppress(Exception):
            await message.delete()
        return await ui.show(me.id, message.chat.id, await home_card(hub, me))
    try:
        c = await hub.chat(a.id, data["chat"])
    except HubError:
        return await ui.show(me.id, message.chat.id, await account_card(hub, me, a))
    try:
        if message.text:
            await hub.send(a.id, c, message.text[:4000])
            await record(hub, me, a.id, "reply", "#{n} ответил в «{chat}»", n=a.id, chat=c.title)
        else:
            kind, file_id, filename, size = file
            if size > BOT_FILE_MAX:
                raise HubError("Бот может взять файл только до 20 МБ — отправьте его с телефона")
            data_bytes = (await bot.download(file_id)).read()
            await hub.send_file(a.id, c, data_bytes, filename, kind, caption=(message.caption or "")[:1000])
            await record(hub, me, a.id, "media", "#{n} отправил вложение в «{chat}»", n=a.id, chat=c.title)
        note = "✅ " + me.t("Отправлено от имени {name}", name=escape(a.name))
    except HubError as e:
        note = "❌ " + me.t("Не отправлено: {why}", why=escape(me.err(e)))
    with suppress(Exception):
        await message.delete()
    try:
        screen = await dialog_screen(hub, me, a, c, data.get("back", "c"), note=note)
    except HubError:
        screen = await account_card(hub, me, a)
    await ui.show(me.id, message.chat.id, screen)
