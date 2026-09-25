"""Админка: аккаунты, пользователи и доступы, журнал, статистика."""
import time
from collections import defaultdict
from contextlib import suppress
from datetime import datetime
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from access import ROLES, AdminGate, Viewer
from auth import Logins
from screens.common import LINE, alert, audit_text, card, go, record, short, tr_role
from tg import Account, Hub, HubError
from ui import UI, Card, Text, btn, kb

router = Router()
router.message.middleware(AdminGate())
router.callback_query.middleware(AdminGate())


# ─── Панель ─────────────────────────────────────────────────────────────────

async def admin_card(hub: Hub, me: Viewer) -> Card:
    accs = hub.accounts()
    users = await hub.db.users()
    tiles = [
        dict(num=len(users), label=me.pl(len(users), "пользователь", "пользователя", "пользователей"), color="#F4F5F7"),
        dict(num=len(accs), label=me.pl(len(accs), "аккаунт", "аккаунта", "аккаунтов"), color="#F4F5F7"),
        dict(num=sum(a.status == "online" for a in accs), label=me.t("онлайн"), color="#34D399"),
        dict(num=sum(a.status == "need_login" for a in accs), label=me.t("ждут входа"), color="#F5A623"),
    ]
    caption = me.t("Управление аккаунтами и доступами.")
    if not hub.api_ready:
        caption += ("\n⚠️ " + me.t("<b>Не заданы API_ID и API_HASH</b> — добавлять аккаунты нельзя.") + "\n<i>"
                    + me.t("my.telegram.org → API development tools → впишите в .env и перезапустите бота.") + "</i>")
    recent = await hub.db.recent(1)
    if recent:
        caption += "\n<i>" + me.t("Последнее: {what}", what=escape(short(audit_text(me, recent[0]["action"]), 80))) + "</i>"
    return card(me, "admin.html", caption, kb(
        [btn(me.t("➕ Добавить аккаунт"), "add", "primary")],
        [btn(me.t("📱 Аккаунты"), "adm:a"), btn(me.t("👤 Пользователи"), "adm:u")],
        [btn(me.t("📊 Статистика"), "st:7"), btn(me.t("📜 Журнал"), "adm:l")],
        [btn(me.t("← Главная"), "home")],
    ), tiles=tiles)


def accounts_card(hub: Hub, me: Viewer) -> Card:
    accs = hub.accounts()
    online = sum(a.status == "online" for a in accs)
    rows = [[btn(f"{a.status_emoji} #{a.id} {short(a.name, 20)} · "
                 f"{'@' + a.username if a.username else me.t('без username')}", f"aa:{a.id}")] for a in accs]
    rows += [[btn(me.t("➕ Добавить аккаунт"), "add", "primary")], [btn(me.t("← Назад"), "adm")]]
    caption = me.t("🟢 онлайн · 🟠 нужен вход · ⚫ отключён или нет связи") if accs else me.t("Аккаунтов пока нет.")
    return card(me, "list.html", caption, kb(*rows),
                a=None, eyebrow=me.t("Админ-панель"), title=me.t("Аккаунты"),
                sub=f"{len(accs)} {me.pl(len(accs), 'аккаунт', 'аккаунта', 'аккаунтов')} · {online} {me.t('онлайн')}",
                icon_name="accounts", tile_bg="#2A2212", icon_color="#F5A623", page_label="")


async def manage_card(hub: Hub, me: Viewer, a: Account, confirm: bool = False) -> Card:
    access = await hub.db.access_to(a.id)
    chats = unread = "—"
    if a.status == "online":
        with suppress(HubError):
            dl = await hub.dialogs(a.id)
            chats, unread = len(dl), sum(c.unread for c in dl if not c.tg_muted)
    tiles = [
        dict(num=chats, label=me.t("чатов"), color="#F4F5F7"),
        dict(num=len(access), label=me.t("с доступом"), color="#F4F5F7"),
        dict(num=unread, label=me.t("непрочитанных"), color="#2DD4BF"),
    ]
    ctx = dict(a=a, eyebrow=me.t("Управление · #{n}", n=a.id), tiles=tiles, glow_color=a.status_color)
    if confirm:
        return card(me, "account.html", me.t("🗑 <b>Удалить аккаунт #{n} из панели?</b>\n"
                                             "Сессия будет завершена в Telegram и стёрта с сервера, у пользователей пропадёт доступ.", n=a.id),
                    kb([btn(me.t("Да, удалить"), f"aadok:{a.id}", "danger")], [btn(me.t("Отмена"), f"aa:{a.id}")]), **ctx)
    who = ", ".join(f"{r['name']} ({tr_role(me, r['role']).split()[0]})" for r in access) or me.t("только админы")
    caption = (me.t("Статус: {emoji} <b>{status}</b>", emoji=a.status_emoji, status=me.t(a.status_label)) + "\n"
               + me.t("Доступ: {who}", who=escape(short(who, 300))))
    toggle = (me.t("▶ Включить"), f"aat:{a.id}", "success") if not a.enabled else (me.t("⏸ Отключить"), f"aat:{a.id}", None)
    return card(me, "account.html", caption, kb(
        [btn(me.t("🔑 Перелогин"), f"relog:{a.id}"), btn(*toggle)],
        [btn(me.t("👤 Открыть"), f"acc:{a.id}"), btn(me.t("🗑 Удалить"), f"aad:{a.id}", "danger")],
        [btn(me.t("← Назад"), "adm:a")],
    ), **ctx)


async def users_card(hub: Hub, me: Viewer) -> Card:
    rows = []
    for u in await hub.db.users():
        name = short(u["name"], 24)
        if u["admin"] or u["id"] in hub.cfg.admin_ids:
            rows.append([btn(me.t("👑 {name} · все аккаунты", name=name), f"au:{u['id']}")])
        elif u["banned"]:
            rows.append([btn(me.t("🚫 {name} · заблокирован", name=name), f"au:{u['id']}")])
        else:
            n = len(await hub.db.roles(u["id"]))
            rows.append([btn(f"👤 {name} · {n} {me.pl(n, 'аккаунт', 'аккаунта', 'аккаунтов')}", f"au:{u['id']}")])
    rows.append([btn(me.t("← Назад"), "adm")])
    return card(me, "list.html", me.t("Пользователь появляется здесь, когда сам напишет боту /start.\n"
                                      "Нажмите на него, чтобы выдать или забрать доступ."), kb(*rows[-60:]),
                a=None, eyebrow=me.t("Админ-панель"), title=me.t("Пользователи"), sub=me.t("Кто к каким аккаунтам имеет доступ"),
                icon_name="users", tile_bg="#241B3D", icon_color="#A78BFA", page_label="")


async def user_screen(hub: Hub, me: Viewer, uid: int) -> Text:
    u = await hub.db.user(uid)
    if u is None:
        raise HubError("Пользователь не найден")
    head = f"<b>{escape(u['name'])}</b>" + (f" · @{escape(u['username'])}" if u["username"] else "") + f"\nID: <code>{uid}</code>"
    from_env = uid in hub.cfg.admin_ids
    back = [btn(me.t("← Назад"), "adm:u")]
    pin_row = [btn(me.t("🔓 Сбросить PIN"), f"upin:{uid}")] if u["pin"] else []
    if u["admin"] or from_env:
        text = f"👑 {head}\n{LINE}\n" + me.t("Администратор: видит все аккаунты, управляет доступами и журналом.")
        if from_env:
            text += "\n<i>" + me.t("Назначен в .env (ADMIN_IDS) — снять можно только там.") + "</i>"
        rows = [[btn(me.t("Снять права админа"), f"uadm:{uid}", "danger")]] if not from_env and uid != me.id else []
        return Text(text, kb(*rows, pin_row, back))
    if u["banned"]:
        return Text(f"🚫 {head}\n{LINE}\n" + me.t("Заблокирован: бот его не слушает."),
                    kb([btn(me.t("Разблокировать"), f"uban:{uid}", "success")], back))
    roles = await hub.db.roles(uid)
    accs = hub.accounts()
    lines = [f"#{a.id} {escape(a.name)} — {tr_role(me, roles.get(a.id, 'none'))}" for a in accs] or \
        ["<i>" + me.t("Аккаунтов пока нет") + "</i>"]
    text = (f"👤 {head}\n{LINE}\n" + "\n".join(lines) + f"\n{LINE}\n<i>"
            + me.t("Нажмите на аккаунт, чтобы сменить роль:\nнет доступа → чтение → чтение и ответы") + "</i>")
    rows = [[btn(f"#{a.id} {short(a.name, 18)}: {tr_role(me, roles.get(a.id, 'none'))}", f"ar:{uid}:{a.id}")] for a in accs]
    rows.append([btn(me.t("👑 Сделать админом"), f"uadm:{uid}"), btn(me.t("🚫 Заблокировать"), f"uban:{uid}", "danger")])
    return Text(text, kb(*rows, pin_row, back))


async def log_screen(hub: Hub, me: Viewer) -> Text:
    rows = await hub.db.recent(20)
    if rows:
        body = "\n".join(
            f"<code>{datetime.fromtimestamp(r['ts'], hub.cfg.tz):%d.%m %H:%M}</code>  "
            f"{escape(short(r['name'] or '—', 20))} · {escape(short(audit_text(me, r['action']), 90))}" for r in rows)
    else:
        body = "<i>" + me.t("Пока пусто.") + "</i>"
    return Text(me.t("📜 <b>Журнал действий</b>") + f"\n{LINE}\n{body}",
                kb([btn(me.t("🔄 Обновить"), "adm:l"), btn(me.t("← Назад"), "adm")]))


# ─── Статистика ─────────────────────────────────────────────────────────────

STAT_KINDS = [  # вид события, значок
    ("reply", "✍"), ("media", "📎"), ("first", "🆕"), ("forward", "↪"), ("react", "😊"), ("delete", "🗑"), ("open", "👁"),
]
PERIODS = {1: "Сегодня", 7: "7 дней", 30: "30 дней"}


async def stats_screen(hub: Hub, me: Viewer, days: int) -> Text:
    since = time.time() - days * 86400
    per_acc: dict[int, dict[str, int]] = defaultdict(dict)
    for r in await hub.db.stats_accounts(since):
        per_acc[r["account_id"]][r["kind"]] = r["n"]
    kinds = await hub.db.stats_kinds(since)
    lines = [me.t("📊 <b>Статистика · {period}</b>", period=me.t(PERIODS[days])), LINE, "<b>" + me.t("Аккаунты") + "</b>"]
    accs = hub.accounts()
    for a in accs:
        counts = per_acc.get(a.id, {})
        parts = [f"{icon} {counts[k]}" for k, icon in STAT_KINDS if counts.get(k)]
        lines.append(f"{a.status_emoji} #{a.id} {escape(short(a.name, 18))} — {' · '.join(parts) if parts else me.t('тихо')}")
    if not accs:
        lines.append("<i>" + me.t("Аккаунтов пока нет") + "</i>")
    lines += [LINE, "<b>" + me.t("Люди") + "</b>"]
    people = await hub.db.stats_users(since)
    for r in people:
        last = datetime.fromtimestamp(r["last"], hub.cfg.tz).strftime("%d.%m %H:%M")
        lines.append(me.t("{name} — {n} {word} · последний раз {last}", name=escape(short(r["name"] or "—", 20)), n=r["n"],
                          word=me.pl(r["n"], "действие", "действия", "действий"), last=last))
    if not people:
        lines.append("<i>" + me.t("Никто ничего не делал") + "</i>")
    lines += [LINE, me.t("✍ ответов {r} · 📎 вложений {m} · 🆕 новых переписок {f} · ↪ пересылок {w}\n"
                         "😊 реакций {x} · 🗑 удалений {d} · 🔔 уведомлений {u}",
                         r=kinds.get("reply", 0), m=kinds.get("media", 0), f=kinds.get("first", 0), w=kinds.get("forward", 0),
                         x=kinds.get("react", 0), d=kinds.get("delete", 0), u=kinds.get("notify", 0))]
    rows = [[btn(("• " if d == days else "") + me.t(label), f"st:{d}") for d, label in PERIODS.items()],
            [btn(me.t("← Назад"), "adm")]]
    return Text("\n".join(lines), kb(*rows))


# ─── Хендлеры ───────────────────────────────────────────────────────────────

@router.message(Command("admin"))
async def admin_cmd(message: Message, state: FSMContext, ui: UI, hub: Hub, logins: Logins, me: Viewer) -> None:
    await state.clear()
    await logins.cancel(me.id)
    await ui.drop(me.id)
    await ui.show(me.id, message.chat.id, await admin_card(hub, me))
    with suppress(Exception):
        await message.delete()


@router.callback_query(F.data == "adm")
async def admin(cq: CallbackQuery, state: FSMContext, ui: UI, hub: Hub, me: Viewer) -> None:
    await state.clear()
    await go(cq, ui, me, admin_card(hub, me))


@router.callback_query(F.data == "adm:a")
async def accounts(cq: CallbackQuery, ui: UI, hub: Hub, me: Viewer) -> None:
    await go(cq, ui, me, accounts_card(hub, me))


@router.callback_query(F.data == "adm:u")
async def users(cq: CallbackQuery, ui: UI, hub: Hub, me: Viewer) -> None:
    await go(cq, ui, me, users_card(hub, me))


@router.callback_query(F.data == "adm:l")
async def log(cq: CallbackQuery, ui: UI, hub: Hub, me: Viewer) -> None:
    await go(cq, ui, me, log_screen(hub, me))


@router.callback_query(F.data.regexp(r"^st:(1|7|30)$"))
async def stats(cq: CallbackQuery, ui: UI, hub: Hub, me: Viewer) -> None:
    await go(cq, ui, me, stats_screen(hub, me, int(cq.data.split(":")[1])))


@router.callback_query(F.data.regexp(r"^aa(t|d|dok)?:\d+$"))
async def manage(cq: CallbackQuery, ui: UI, hub: Hub, me: Viewer) -> None:
    action, acc_id = cq.data.split(":")
    a = hub.accs.get(int(acc_id))
    if not a:
        return await go(cq, ui, me, accounts_card(hub, me), me.t("Аккаунт уже удалён"))
    if action == "aad":
        return await go(cq, ui, me, manage_card(hub, me, a, confirm=True))
    if action == "aadok":
        await hub.remove(a.id)
        await record(hub, me, a.id, "remove", "удалил аккаунт #{n} {name}", n=a.id, name=a.name)
        return await go(cq, ui, me, accounts_card(hub, me), me.t("Аккаунт #{n} удалён", n=a.id))
    if action == "aat":
        await hub.set_enabled(a.id, not a.enabled)
        await hub.db.log(me.id, a.id, "#{n} включил" if a.enabled else "#{n} отключил", n=a.id)
    await go(cq, ui, me, manage_card(hub, me, a))


@router.callback_query(F.data.regexp(r"^au:\d+$"))
async def user(cq: CallbackQuery, ui: UI, hub: Hub, me: Viewer) -> None:
    await go(cq, ui, me, user_screen(hub, me, int(cq.data.split(":")[1])))


@router.callback_query(F.data.regexp(r"^ar:\d+:\d+$"))
async def role(cq: CallbackQuery, ui: UI, hub: Hub, me: Viewer) -> None:
    _, uid, acc_id = cq.data.split(":")
    uid, acc_id = int(uid), int(acc_id)
    u = await hub.db.user(uid)
    a = hub.accs.get(acc_id)
    if u is None or a is None or u["admin"] or u["banned"] or uid in hub.cfg.admin_ids:
        return await go(cq, ui, me, users_card(hub, me), me.t("Роль уже не меняется — список обновлён"))
    cur = (await hub.db.roles(uid)).get(acc_id, "none")
    new = ROLES[(ROLES.index(cur) + 1) % len(ROLES)]
    await hub.db.set_role(uid, acc_id, None if new == "none" else new)
    await hub.db.log(me.id, acc_id, {"none": "{name} → #{n}: нет доступа", "viewer": "{name} → #{n}: чтение",
                                     "operator": "{name} → #{n}: чтение и ответы"}[new], name=u["name"], n=acc_id)
    await go(cq, ui, me, user_screen(hub, me, uid))


@router.callback_query(F.data.regexp(r"^u(ban|adm|pin):\d+$"))
async def user_flags(cq: CallbackQuery, ui: UI, hub: Hub, me: Viewer) -> None:
    action, uid = cq.data.split(":")
    uid = int(uid)
    u = await hub.db.user(uid)
    if u is None or (action != "upin" and (uid == me.id or uid in hub.cfg.admin_ids)):
        return await alert(cq, me.t("Это действие недоступно"))
    if action == "uban":
        banned = not u["banned"]
        await hub.db.set_banned(uid, banned)
        await hub.db.log(me.id, None, "заблокировал {name}" if banned else "разблокировал {name}", name=u["name"])
    elif action == "uadm":
        admin_now = not u["admin"]
        await hub.db.set_admin(uid, admin_now)
        await hub.db.log(me.id, None, "выдал права админа: {name}" if admin_now else "снял права админа: {name}", name=u["name"])
    else:
        await hub.db.set_user(uid, pin=None)
        await hub.db.log(me.id, None, "сбросил PIN: {name}", name=u["name"])
    await go(cq, ui, me, user_screen(hub, me, uid))
