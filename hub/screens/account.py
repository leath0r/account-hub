"""Меню аккаунта и его настройки (уведомления, заглушённые чаты, завершение сессии)."""
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from access import Viewer
from screens.common import alert, card, go, gone, record, short, snippet, title, usable
from tg import Account, Hub, HubError
from ui import UI, Card, btn, kb

router = Router()

DASH_TILES = ("непрочитанных", "личных чатов", "групп и каналов")


async def account_card(hub: Hub, me: Viewer, a: Account) -> Card:
    ctx = dict(a=a, eyebrow=me.t("Аккаунт #{n}", n=a.id), glow_color="#7C5CFC")
    switch = [btn(me.t("⇄ Сменить аккаунт"), "home", "primary")]
    dash = [dict(num="—", label=me.t(label), color="#5B6273") for label in DASH_TILES]

    if a.status == "need_login":
        ctx.update(tiles=dash, glow_color="#F5A623")
        if me.admin:
            caption = me.t("⚠️ <b>Сессия слетела</b> — нужен повторный вход.\nПока аккаунт недоступен: чаты и отправка отключены.")
            return card(me, "account.html", caption, kb([btn(me.t("🔑 Войти заново"), f"relog:{a.id}", "primary")],
                                                        [btn(me.t("⇄ Сменить аккаунт"), "home")]), **ctx)
        caption = me.t("⚠️ <b>Сессия слетела</b> — администратор уже знает.\nПока аккаунт недоступен.")
        return card(me, "account.html", caption, kb(switch), **ctx)

    if a.status == "offline":
        ctx.update(tiles=dash, glow_color="#5B6273")
        rows = [[btn(me.t("⚙️ Открыть в админке"), f"aa:{a.id}")]] if me.admin else []
        return card(me, "account.html", me.t("⏸ Аккаунт отключён в админ-панели."), kb(*rows, switch), **ctx)

    try:
        chats = await hub.dialogs(a.id)
    except HubError as e:
        if a.status == "need_login":  # сессия слетела прямо сейчас
            return await account_card(hub, me, a)
        ctx.update(tiles=dash, glow_color="#5B6273")
        return card(me, "account.html", f"📡 {escape(me.err(e))}", kb([btn(me.t("🔄 Повторить"), f"acc:{a.id}")], switch), **ctx)

    muted = await hub.db.muted(me.id, a.id)
    live = [c for c in chats if c.unread and not c.tg_muted and c.id not in muted]
    unread = sum(c.unread for c in live)
    privates = sum(c.kind == "user" for c in chats)
    groups = len(chats) - privates
    ctx["tiles"] = [
        dict(num=unread, label=me.t("непрочитанных"), color="#2DD4BF"),
        dict(num=privates, label=me.pl(privates, "личный чат", "личных чата", "личных чатов"), color="#F4F5F7"),
        dict(num=groups, label=me.t("групп и каналов"), color="#F4F5F7"),
    ]
    if live:
        c = max(live, key=lambda c: c.last_ts)
        caption = (me.t("<b>Последнее:</b> {title} — «{text}»", title=escape(title(me, c)), text=snippet(c.last_text)) + "\n<i>"
                   + me.t("{time} · всего {n} {word}", time=c.last_time, n=unread,
                          word=me.pl(unread, "непрочитанное", "непрочитанных", "непрочитанных")) + "</i>")
    else:
        caption = me.t("Новых сообщений нет ✓")
    if me.role(a.id) == "viewer":
        caption += "\n<i>" + me.t("👁 У вас доступ только на чтение.") + "</i>"
    private_unread = sum(c.unread for c in live if c.kind == "user")
    markup = kb(
        [btn(me.t("🔎 Поиск"), f"srch:{a.id}"),
         btn(me.t("💬 Чаты · {n}", n=private_unread) if private_unread else me.t("💬 Чаты"), f"ls:{a.id}:c:0")],
        [btn(me.t("👥 Группы"), f"ls:{a.id}:g:0"), btn(me.t("📨 Непрочитанное"), f"ls:{a.id}:u:0")],
        [btn(me.t("👤 Контакты"), f"ls:{a.id}:k:0"), btn(me.t("⚙️ Настройки"), f"set:{a.id}")],
        switch,
    )
    return card(me, "account.html", caption, markup, **ctx)


async def settings_card(hub: Hub, me: Viewer, a: Account, confirm: bool = False) -> Card:
    notify = await hub.db.notify(me.id, a.id)
    muted = await hub.db.muted(me.id, a.id)
    ctx = dict(a=a, eyebrow=me.t("Аккаунт #{n}", n=a.id), title=me.t("Настройки"),
               sub=me.t("Уведомления: включены") if notify else me.t("Уведомления: выключены"),
               icon_name="settings", tile_bg="#1E222B", icon_color="#9AA1B2", page_label="")
    if confirm:
        return card(me, "list.html", me.t("🚪 <b>Завершить сессию?</b>\nВход будет закрыт в самом Telegram. "
                                          "Аккаунт станет 🟠 «Нужен вход», войти можно будет заново."),
                    kb([btn(me.t("Да, завершить"), f"setxok:{a.id}", "danger")], [btn(me.t("Отмена"), f"set:{a.id}")]), **ctx)
    rows = [
        [btn(me.t("🔔 Уведомления: вкл") if notify else me.t("🔕 Уведомления: выкл"), f"setn:{a.id}")],
        [btn(me.t("🔕 Заглушённые чаты · {n}", n=len(muted)), f"setm:{a.id}")],
    ]
    if me.admin:
        rows.append([btn(me.t("🚪 Завершить сессию"), f"setx:{a.id}", "danger")])
    rows.append([btn(me.t("← Назад"), f"acc:{a.id}")])
    return card(me, "list.html",
                me.t("Когда уведомления включены, новые сообщения этого аккаунта приходят вам сюда и попадают во «📥 Входящие».\n"
                     "Тихий режим ночью и группы — в «⚙️ Мои настройки» на главной."),
                kb(*rows), **ctx)


@router.callback_query(F.data.startswith("acc:"))
async def account(cq: CallbackQuery, state: FSMContext, ui: UI, hub: Hub, me: Viewer) -> None:
    await state.clear()
    a = hub.accs.get(int(cq.data.split(":")[1]))
    if not a:
        return await gone(cq, ui, hub, me)
    await go(cq, ui, me, account_card(hub, me, a))


@router.callback_query(F.data.regexp(r"^set(n|m|x|xok)?:"))
async def settings(cq: CallbackQuery, ui: UI, hub: Hub, me: Viewer) -> None:
    action, acc_id = cq.data.split(":")
    a = hub.accs.get(int(acc_id))
    if not usable(a):
        return await gone(cq, ui, hub, me) if not a else await go(cq, ui, me, account_card(hub, me, a))
    if action == "setn":
        on = not await hub.db.notify(me.id, a.id)
        await hub.db.set_notify(me.id, a.id, on)
    elif action == "setm":
        muted = await hub.db.muted(me.id, a.id)
        if not muted:
            return await alert(cq, me.t("Заглушённых чатов нет. Заглушить можно из переписки."))
        titles = {c.id: title(me, c) for c in hub.cached_dialogs(a.id)}
        names = [titles.get(i, me.t("чат вне списка")) for i in muted]
        return await alert(cq, short(me.t("Заглушены: {names}", names=", ".join(names)), 190))
    elif action == "setx":
        return await go(cq, ui, me, settings_card(hub, me, a, confirm=True))
    elif action == "setxok":
        await hub.logout(a.id)
        await record(hub, me, a.id, "logout", "#{n} завершил сессию", n=a.id)
        return await go(cq, ui, me, account_card(hub, me, a), me.t("Сессия завершена"))
    await go(cq, ui, me, settings_card(hub, me, a))
