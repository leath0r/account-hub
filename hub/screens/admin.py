"""Админка: аккаунты, пользователи и доступы, журнал, добавление аккаунта и перелогин с кейпадом."""
from contextlib import suppress
from datetime import datetime
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from access import ROLE_LABEL, ROLES, AdminGate, Viewer
from auth import LoginError, LoginExpired, Logins
from screens.user import go, short
from tg import Account, Hub, HubError
from ui import UI, Card, Text, btn, kb, plural

router = Router()
router.message.middleware(AdminGate())
router.callback_query.middleware(AdminGate())


class Add(StatesGroup):
    phone = State()
    code = State()
    password = State()


# ─── Панель ─────────────────────────────────────────────────────────────────

async def admin_card(hub: Hub) -> Card:
    accs = hub.accounts()
    users = await hub.db.users()
    tiles = [
        dict(num=len(users), label=plural(len(users), "пользователь", "пользователя", "пользователей"), color="#F4F5F7"),
        dict(num=len(accs), label=plural(len(accs), "аккаунт", "аккаунта", "аккаунтов"), color="#F4F5F7"),
        dict(num=sum(a.status == "online" for a in accs), label="онлайн", color="#34D399"),
        dict(num=sum(a.status == "need_login" for a in accs), label="ждут входа", color="#F5A623"),
    ]
    caption = "Управление аккаунтами и доступами."
    if not hub.api_ready:
        caption += ("\n⚠️ <b>Не заданы API_ID и API_HASH</b> — добавлять аккаунты нельзя.\n"
                    "<i>my.telegram.org → API development tools → впишите в .env и перезапустите бота.</i>")
    recent = await hub.db.recent(1)
    if recent:
        caption += f"\n<i>Последнее: {escape(short(recent[0]['action'], 80))}</i>"
    return Card("admin.html", caption, kb(
        [btn("➕ Добавить аккаунт", "add", "primary")],
        [btn("📱 Аккаунты", "adm:a"), btn("👤 Пользователи", "adm:u")],
        [btn("📜 Журнал", "adm:l")],
        [btn("← Главная", "home")],
    ), dict(tiles=tiles))


def accounts_card(hub: Hub) -> Card:
    accs = hub.accounts()
    online = sum(a.status == "online" for a in accs)
    rows = [[btn(f"{a.status_emoji} #{a.id} {short(a.name, 20)} · {a.handle}", f"aa:{a.id}")] for a in accs]
    rows += [[btn("➕ Добавить аккаунт", "add", "primary")], [btn("← Назад", "adm")]]
    caption = "🟢 онлайн · 🟠 нужен вход · ⚫ отключён или нет связи" if accs else "Аккаунтов пока нет."
    return Card("list.html", caption, kb(*rows), dict(
        a=None, eyebrow="Админ-панель", title="Аккаунты",
        sub=f"{len(accs)} {plural(len(accs), 'аккаунт', 'аккаунта', 'аккаунтов')} · {online} онлайн",
        icon_name="accounts", tile_bg="#2A2212", icon_color="#F5A623", page_label=""))


async def manage_card(hub: Hub, a: Account, confirm: bool = False) -> Card:
    access = await hub.db.access_to(a.id)
    chats = unread = "—"
    if a.status == "online":
        with suppress(HubError):
            dl = await hub.dialogs(a.id)
            chats, unread = len(dl), sum(c.unread for c in dl if not c.tg_muted)
    tiles = [
        dict(num=chats, label="чатов", color="#F4F5F7"),
        dict(num=len(access), label="с доступом", color="#F4F5F7"),
        dict(num=unread, label="непрочитанных", color="#2DD4BF"),
    ]
    ctx = dict(a=a, eyebrow=f"Управление · #{a.id}", tiles=tiles, glow_color=a.status_color)
    if confirm:
        return Card("account.html", f"🗑 <b>Удалить аккаунт #{a.id} из панели?</b>\n"
                                    "Сессия будет завершена в Telegram и стёрта с сервера, у пользователей пропадёт доступ.",
                    kb([btn("Да, удалить", f"aadok:{a.id}", "danger")], [btn("Отмена", f"aa:{a.id}")]), ctx)
    who = ", ".join(f"{r['name']} ({ROLE_LABEL[r['role']].split()[0]})" for r in access) or "только админы"
    caption = f"Статус: {a.status_emoji} <b>{a.status_label}</b>\nДоступ: {escape(short(who, 300))}"
    toggle = ("▶ Включить", f"aat:{a.id}", "success") if not a.enabled else ("⏸ Отключить", f"aat:{a.id}", None)
    return Card("account.html", caption, kb(
        [btn("🔑 Перелогин", f"relog:{a.id}"), btn(*toggle)],
        [btn("👤 Открыть", f"acc:{a.id}"), btn("🗑 Удалить", f"aad:{a.id}", "danger")],
        [btn("← Назад", "adm:a")],
    ), ctx)


async def users_card(hub: Hub) -> Card:
    rows = []
    for u in await hub.db.users():
        name = short(u["name"], 24)
        if u["admin"] or u["id"] in hub.cfg.admin_ids:
            rows.append([btn(f"👑 {name} · все аккаунты", f"au:{u['id']}")])
        elif u["banned"]:
            rows.append([btn(f"🚫 {name} · заблокирован", f"au:{u['id']}")])
        else:
            n = len(await hub.db.roles(u["id"]))
            rows.append([btn(f"👤 {name} · {n} {plural(n, 'аккаунт', 'аккаунта', 'аккаунтов')}", f"au:{u['id']}")])
    rows.append([btn("← Назад", "adm")])
    return Card("list.html", "Пользователь появляется здесь, когда сам напишет боту /start.\n"
                             "Нажмите на него, чтобы выдать или забрать доступ.", kb(*rows[-60:]), dict(
        a=None, eyebrow="Админ-панель", title="Пользователи", sub="Кто к каким аккаунтам имеет доступ",
        icon_name="users", tile_bg="#241B3D", icon_color="#A78BFA", page_label=""))


async def user_screen(hub: Hub, me: Viewer, uid: int) -> Text:
    u = await hub.db.user(uid)
    if u is None:
        raise HubError("Пользователь не найден")
    head = f"<b>{escape(u['name'])}</b>" + (f" · @{escape(u['username'])}" if u["username"] else "") + f"\nID: <code>{uid}</code>"
    from_env = uid in hub.cfg.admin_ids
    back = [btn("← Назад", "adm:u")]
    if u["admin"] or from_env:
        text = f"👑 {head}\n━━━━━━━━━━━━━━━━\nАдминистратор: видит все аккаунты, управляет доступами и журналом."
        if from_env:
            text += "\n<i>Назначен в .env (ADMIN_IDS) — снять можно только там.</i>"
        rows = [[btn("Снять права админа", f"uadm:{uid}", "danger")]] if not from_env and uid != me.id else []
        return Text(text, kb(*rows, back))
    if u["banned"]:
        return Text(f"🚫 {head}\n━━━━━━━━━━━━━━━━\nЗаблокирован: бот его не слушает.",
                    kb([btn("Разблокировать", f"uban:{uid}", "success")], back))
    roles = await hub.db.roles(uid)
    accs = hub.accounts()
    lines = [f"#{a.id} {escape(a.name)} — {ROLE_LABEL[roles.get(a.id, 'none')]}" for a in accs] or ["<i>Аккаунтов пока нет</i>"]
    text = (f"👤 {head}\n━━━━━━━━━━━━━━━━\n" + "\n".join(lines) +
            "\n━━━━━━━━━━━━━━━━\n<i>Нажмите на аккаунт, чтобы сменить роль:\nнет доступа → чтение → чтение и ответы</i>")
    rows = [[btn(f"#{a.id} {short(a.name, 18)}: {ROLE_LABEL[roles.get(a.id, 'none')]}", f"ar:{uid}:{a.id}")] for a in accs]
    rows.append([btn("👑 Сделать админом", f"uadm:{uid}"), btn("🚫 Заблокировать", f"uban:{uid}", "danger")])
    return Text(text, kb(*rows, back))


async def log_screen(hub: Hub) -> Text:
    rows = await hub.db.recent(20)
    if rows:
        body = "\n".join(
            f"<code>{datetime.fromtimestamp(r['ts'], hub.cfg.tz):%d.%m %H:%M}</code>  "
            f"{escape(short(r['name'] or '—', 20))} · {escape(short(r['action'], 90))}" for r in rows)
    else:
        body = "<i>Пока пусто.</i>"
    return Text(f"📜 <b>Журнал действий</b>\n━━━━━━━━━━━━━━━━\n{body}",
                kb([btn("🔄 Обновить", "adm:l"), btn("← Назад", "adm")]))


# ─── Добавление аккаунта и перелогин ────────────────────────────────────────

def steps(stage: str, second: str = "Код") -> list[dict]:
    order = {"phone": 0, "code": 1, "password": 1, "done": 3}[stage]
    labels = ["Номер", second, "Готово"]
    return [dict(label=label, state="done" if i < order else "cur" if i == order else "todo") for i, label in enumerate(labels)]


def keypad() -> list:
    def d(x: str):
        return btn(x, f"kp:{x}")

    return [
        [d("1"), d("2"), d("3")],
        [d("4"), d("5"), d("6")],
        [d("7"), d("8"), d("9")],
        [btn("⌫", "kp:b"), d("0"), btn("✓ Готово", "kp:ok", "success")],
        [btn("↻ Отправить код заново", "kp:r")],
        [btn("✕ Отмена", "addx", "danger")],
    ]


DONE_TITLE = {"added": "Аккаунт #{id} добавлен", "updated": "Аккаунт #{id} обновлён", "relogin": "Вход выполнен"}


def add_card(stage: str, data: dict, error: str | None = None, a: Account | None = None, result: str = "") -> Card:
    card = _add_card(stage, data, error, a, result)
    if error:
        card.caption = f"❌ <b>{escape(error)}</b>\n\n{card.caption}"
    return card


def _add_card(stage: str, data: dict, error: str | None, a: Account | None, result: str) -> Card:
    relogin = data.get("mode") == "relogin"
    ctx = dict(title="Повторный вход" if relogin else "Добавить аккаунт", state=stage, error=error,
               steps=steps(stage, "2FA" if stage == "password" else "Код"),
               code=data.get("code", ""), code_len=data.get("code_len", 5), phone=data.get("phone", ""), a=a,
               done_title=DONE_TITLE.get(result, "").format(id=a.id if a else ""))
    cancel = [btn("✕ Отмена", "addx", "danger")]
    if stage == "phone":
        caption = ("<b>Шаг 1.</b> Отправьте номер телефона сообщением в международном формате (+7…) — "
                   "я сразу удалю его из чата.\n<i>Добавляйте только свои аккаунты или с согласия владельца.</i>")
        return Card("add.html", caption, kb(cancel), ctx)
    if stage == "code":
        caption = (f"Код отправлен {data.get('via', 'в Telegram')} на <b>{data.get('phone', '')}</b>.\n"
                   "Вводите кнопками: код, отправленный сообщением, Telegram сразу аннулирует.")
        return Card("add.html", caption, kb(*keypad()), ctx)
    if stage == "password":
        caption = ("<b>Включена двухэтапная аутентификация.</b>\n"
                   "Отправьте облачный пароль сообщением — удалю сразу, нигде не сохраняю.")
        return Card("add.html", caption, kb(cancel), ctx)
    caption = "✅ Сессия сохранена в зашифрованном виде."
    if result == "updated":
        caption = "✅ Этот аккаунт уже был в панели — сессия обновлена."
    return Card("add.html", caption, kb(
        [btn("👤 Открыть аккаунт", f"acc:{a.id}", "primary")],
        [btn("⚙️ Админ-панель", "adm")],
    ), ctx)


def mask(digits: str) -> str:
    return f"+{digits[0]} ••• ••• {digits[-4:-2]} {digits[-2:]}"


# ─── Хендлеры ───────────────────────────────────────────────────────────────

@router.message(Command("admin"))
async def admin_cmd(message: Message, state: FSMContext, ui: UI, hub: Hub, logins: Logins, me: Viewer) -> None:
    await state.clear()
    await logins.cancel(me.id)
    await ui.drop(me.id)
    await ui.show(me.id, message.chat.id, await admin_card(hub))
    with suppress(Exception):
        await message.delete()


@router.callback_query(F.data == "adm")
async def admin(cq: CallbackQuery, state: FSMContext, ui: UI, hub: Hub) -> None:
    await state.clear()
    await go(cq, ui, admin_card(hub))


@router.callback_query(F.data == "adm:a")
async def accounts(cq: CallbackQuery, ui: UI, hub: Hub) -> None:
    await go(cq, ui, accounts_card(hub))


@router.callback_query(F.data == "adm:u")
async def users(cq: CallbackQuery, ui: UI, hub: Hub) -> None:
    await go(cq, ui, users_card(hub))


@router.callback_query(F.data == "adm:l")
async def log(cq: CallbackQuery, ui: UI, hub: Hub) -> None:
    await go(cq, ui, log_screen(hub))


@router.callback_query(F.data.regexp(r"^aa(t|d|dok)?:\d+$"))
async def manage(cq: CallbackQuery, ui: UI, hub: Hub, me: Viewer) -> None:
    action, acc_id = cq.data.split(":")
    a = hub.accs.get(int(acc_id))
    if not a:
        return await go(cq, ui, accounts_card(hub), "Аккаунт уже удалён")
    if action == "aad":
        return await go(cq, ui, manage_card(hub, a, confirm=True))
    if action == "aadok":
        await hub.remove(a.id)
        await hub.db.log(me.id, a.id, f"удалил аккаунт #{a.id} {a.name}")
        return await go(cq, ui, accounts_card(hub), f"Аккаунт #{a.id} удалён")
    if action == "aat":
        await hub.set_enabled(a.id, not a.enabled)
        await hub.db.log(me.id, a.id, f"#{a.id} {'включил' if a.enabled else 'отключил'}")
    await go(cq, ui, manage_card(hub, a))


@router.callback_query(F.data.regexp(r"^au:\d+$"))
async def user(cq: CallbackQuery, ui: UI, hub: Hub, me: Viewer) -> None:
    await go(cq, ui, user_screen(hub, me, int(cq.data.split(":")[1])))


@router.callback_query(F.data.regexp(r"^ar:\d+:\d+$"))
async def role(cq: CallbackQuery, ui: UI, hub: Hub, me: Viewer) -> None:
    _, uid, acc_id = cq.data.split(":")
    uid, acc_id = int(uid), int(acc_id)
    u = await hub.db.user(uid)
    a = hub.accs.get(acc_id)
    if u is None or a is None or u["admin"] or u["banned"] or uid in hub.cfg.admin_ids:
        return await go(cq, ui, users_card(hub), "Роль уже не меняется — список обновлён")
    cur = (await hub.db.roles(uid)).get(acc_id, "none")
    new = ROLES[(ROLES.index(cur) + 1) % len(ROLES)]
    await hub.db.set_role(uid, acc_id, None if new == "none" else new)
    await hub.db.log(me.id, acc_id, f"{u['name']} → #{acc_id}: {ROLE_LABEL[new]}")
    await go(cq, ui, user_screen(hub, me, uid))


@router.callback_query(F.data.regexp(r"^u(ban|adm):\d+$"))
async def user_flags(cq: CallbackQuery, ui: UI, hub: Hub, me: Viewer) -> None:
    action, uid = cq.data.split(":")
    uid = int(uid)
    u = await hub.db.user(uid)
    if u is None or uid == me.id or uid in hub.cfg.admin_ids:
        return await cq.answer("Это действие недоступно", show_alert=True)
    if action == "uban":
        banned = not u["banned"]
        await hub.db.set_banned(uid, banned)
        await hub.db.log(me.id, None, f"{'заблокировал' if banned else 'разблокировал'} {u['name']}")
    else:
        admin = not u["admin"]
        await hub.db.set_admin(uid, admin)
        await hub.db.log(me.id, None, f"{'выдал' if admin else 'снял'} права админа: {u['name']}")
    await go(cq, ui, user_screen(hub, me, uid))


@router.callback_query(F.data == "add")
async def add_start(cq: CallbackQuery, state: FSMContext, ui: UI, hub: Hub, logins: Logins, me: Viewer) -> None:
    if not hub.api_ready:
        return await cq.answer("Сначала впишите API_ID и API_HASH в .env (my.telegram.org) и перезапустите бота.",
                               show_alert=True)
    await logins.cancel(me.id)
    await state.set_state(Add.phone)
    await state.set_data({"mode": "add", "code": ""})
    await go(cq, ui, add_card("phone", {}))


@router.callback_query(F.data.regexp(r"^relog:\d+$"))
async def relogin(cq: CallbackQuery, state: FSMContext, ui: UI, hub: Hub, logins: Logins, me: Viewer) -> None:
    a = hub.accs.get(int(cq.data.split(":")[1]))
    if not a:
        return await cq.answer("Этого аккаунта больше нет", show_alert=True)
    if not hub.api_ready:
        return await cq.answer("Не заданы API_ID и API_HASH в .env", show_alert=True)
    if not a.phone:
        return await cq.answer("Номер этого аккаунта не расшифровать (сменился SESSION_KEY?). "
                               "Удалите аккаунт и добавьте заново.", show_alert=True)

    async def build() -> Card:
        try:
            p = await logins.begin(me.id, a.phone, account_id=a.id)
        except LoginError as e:
            await state.clear()
            card = await manage_card(hub, a)
            card.caption = f"❌ <b>{escape(str(e))}</b>\n{card.caption}"
            return card
        data = {"mode": "relogin", "acc": a.id, "phone": a.phone_masked, "code": "", "code_len": p.code_len, "via": p.via}
        await state.set_state(Add.code)
        await state.set_data(data)
        return add_card("code", data)

    await go(cq, ui, build())


@router.callback_query(F.data == "addx")
async def add_cancel(cq: CallbackQuery, state: FSMContext, ui: UI, hub: Hub, logins: Logins, me: Viewer) -> None:
    await state.clear()
    await logins.cancel(me.id)
    await go(cq, ui, admin_card(hub), "Отменено")


@router.message(Add.phone, F.text)
async def add_phone(message: Message, state: FSMContext, ui: UI, logins: Logins, me: Viewer) -> None:
    with suppress(Exception):
        await message.delete()
    digits = "".join(c for c in message.text if c.isdigit())
    if not 10 <= len(digits) <= 15:
        return await ui.show(me.id, message.chat.id, add_card("phone", {}, error="Не похоже на номер"))
    try:
        p = await logins.begin(me.id, "+" + digits)
    except LoginError as e:
        return await ui.show(me.id, message.chat.id, add_card("phone", {}, error=str(e)))
    data = {"mode": "add", "phone": mask(digits), "code": "", "code_len": p.code_len, "via": p.via}
    await state.set_state(Add.code)
    await state.set_data(data)
    await ui.show(me.id, message.chat.id, add_card("code", data))


async def login_done(state: FSMContext, hub: Hub, me: Viewer, data: dict, result: tuple[Account, str]) -> Card:
    acc, kind = result
    await state.clear()
    what = {"added": "добавил аккаунт", "updated": "обновил сессию", "relogin": "перелогин"}[kind]
    await hub.db.log(me.id, acc.id, f"{what} #{acc.id} {acc.name}")
    return add_card("done", data, a=acc, result=kind)


async def login_failed(state: FSMContext, hub: Hub, data: dict, stage: str, e: LoginError) -> Card:
    if isinstance(e, LoginExpired):
        await state.clear()
        card = await admin_card(hub)
        card.caption = f"⌛ <b>{escape(str(e))}</b>\n{card.caption}"
        return card
    if stage == "code":
        data["code"] = ""
        await state.set_data(data)
    return add_card(stage, data, error=str(e))


@router.callback_query(Add.code, F.data.startswith("kp:"))
async def keypad_press(cq: CallbackQuery, state: FSMContext, ui: UI, hub: Hub, logins: Logins, me: Viewer) -> None:
    key = cq.data.split(":")[1]
    data = await state.get_data()
    code, need = data.get("code", ""), data.get("code_len", 5)

    async def resend() -> Card:
        try:
            p = await logins.resend(me.id)
        except LoginError as e:
            return await login_failed(state, hub, data, "code", e)
        data.update(code="", code_len=p.code_len, via=p.via)
        await state.set_data(data)
        return add_card("code", data)

    async def submit() -> Card:
        try:
            result = await logins.code(me.id, code)
        except LoginError as e:
            return await login_failed(state, hub, data, "code", e)
        if result is None:
            await state.set_state(Add.password)
            return add_card("password", data)
        return await login_done(state, hub, me, data, result)

    if key == "r":
        return await go(cq, ui, resend(), "Код отправлен заново")
    if key == "ok":
        if len(code) < need:
            return await cq.answer(f"Нужно {need} цифр")
        return await go(cq, ui, submit())
    if key == "b":
        data["code"] = code[:-1]
    elif key.isdigit() and len(code) < need:
        data["code"] = code + key
    await state.set_data(data)
    await go(cq, ui, add_card("code", data))


@router.callback_query(F.data.startswith("kp:"))
async def keypad_stale(cq: CallbackQuery) -> None:
    await cq.answer("Этот вход уже закрыт — начните заново из админ-панели.", show_alert=True)


@router.message(Add.password, F.text)
async def add_password(message: Message, state: FSMContext, ui: UI, hub: Hub, logins: Logins, me: Viewer) -> None:
    with suppress(Exception):
        await message.delete()
    data = await state.get_data()
    try:
        result = await logins.password(me.id, message.text)
    except LoginError as e:
        card = await login_failed(state, hub, data, "password", e)
    else:
        card = await login_done(state, hub, me, data, result)
    await ui.show(me.id, message.chat.id, card)
