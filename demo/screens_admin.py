"""Админка: аккаунты, пользователи и доступы, журнал, добавление аккаунта с кейпадом."""
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from mock import ACCOUNTS, AUDIT, ROLE_LABEL, ROLES, USERS, Account, add_account, audit
from screens_user import account_card, go, gone, plural
from ui import UI, Card, Text, btn, kb

router = Router()


class Add(StatesGroup):
    phone = State()
    code = State()
    password = State()


# ─── Панель ─────────────────────────────────────────────────────────────────

def admin_card() -> Card:
    accs = list(ACCOUNTS.values())
    tiles = [
        dict(num=len(USERS), label="пользователя", color="#F4F5F7"),
        dict(num=len(accs), label=plural(len(accs), "аккаунт", "аккаунта", "аккаунтов"), color="#F4F5F7"),
        dict(num=sum(a.status == "online" for a in accs), label="онлайн", color="#34D399"),
        dict(num=sum(a.status == "need_login" for a in accs), label="ждут входа", color="#F5A623"),
    ]
    caption = "Управление аккаунтами и доступами."
    if AUDIT:
        caption += f"\n<i>Последнее: {escape(AUDIT[-1][1])}</i>"
    return Card("admin.html", caption, kb(
        [btn("➕ Добавить аккаунт", "add", "primary")],
        [btn("📱 Аккаунты", "adm:a"), btn("👤 Пользователи", "adm:u")],
        [btn("📜 Журнал", "adm:l")],
        [btn("← Главная", "home")],
    ), dict(tiles=tiles))


def accounts_card() -> Card:
    accs = list(ACCOUNTS.values())
    online = sum(a.status == "online" for a in accs)
    rows = [[btn(f"{a.status_emoji} #{a.id} {a.name} · @{a.username}", f"aa:{a.id}")] for a in accs]
    rows += [[btn("➕ Добавить аккаунт", "add", "primary")], [btn("← Назад", "adm")]]
    return Card("list.html", "🟢 онлайн · 🟠 нужен вход · ⚫ отключён", kb(*rows), dict(
        a=None, eyebrow="Админ-панель", title="Аккаунты",
        sub=f"{len(accs)} {plural(len(accs), 'аккаунт', 'аккаунта', 'аккаунтов')} · {online} онлайн",
        icon_name="accounts", tile_bg="#2A2212", icon_color="#F5A623", page_label=""))


def manage_card(a: Account, confirm: bool = False) -> Card:
    users = [u for u in USERS.values() if u["admin"] or u["roles"].get(a.id, "none") != "none"]
    tiles = [
        dict(num=len(a.chats), label="чатов", color="#F4F5F7"),
        dict(num=len(users), label="с доступом", color="#F4F5F7"),
        dict(num=a.unread_total if a.status == "online" else "—", label="непрочитанных", color="#2DD4BF"),
    ]
    ctx = dict(a=a, eyebrow=f"Управление · #{a.id}", tiles=tiles, glow_color=a.status_color)
    if confirm:
        return Card("account.html", f"🗑 <b>Удалить аккаунт #{a.id} из панели?</b>\nСессия будет стёрта с сервера, у пользователей пропадёт доступ.",
                    kb([btn("Да, удалить", f"aadok:{a.id}", "danger")], [btn("Отмена", f"aa:{a.id}")]), ctx)
    access = ", ".join(
        f"{u['name']} ({'admin' if u['admin'] else ROLE_LABEL[u['roles'][a.id]].split()[0]})" for u in users)
    caption = f"Статус: {a.status_emoji} <b>{a.status_label}</b>\nДоступ: {escape(access)}"
    toggle = ("▶ Включить", "success") if a.status == "offline" else ("⏸ Отключить", None)
    return Card("account.html", caption, kb(
        [btn("🔑 Перелогин", f"relog:{a.id}"), btn(toggle[0], f"aat:{a.id}", toggle[1])],
        [btn("🗑 Удалить", f"aad:{a.id}", "danger")],
        [btn("← Назад", "adm:a")],
    ), ctx)


def users_card() -> Card:
    rows = []
    for uid, u in USERS.items():
        if u["admin"]:
            rows.append([btn(f"👑 {u['name']} · все аккаунты", f"au:{uid}")])
        else:
            n = sum(r != "none" for r in u["roles"].values())
            rows.append([btn(f"👤 {u['name']} · {n} {plural(n, 'аккаунт', 'аккаунта', 'аккаунтов')}", f"au:{uid}")])
    rows.append([btn("← Назад", "adm")])
    return Card("list.html", "Нажмите на пользователя, чтобы выдать или забрать доступ.", kb(*rows), dict(
        a=None, eyebrow="Админ-панель", title="Пользователи", sub="Кто к каким аккаунтам имеет доступ",
        icon_name="users", tile_bg="#241B3D", icon_color="#A78BFA", page_label=""))


def user_screen(uid: int) -> Text:
    u = USERS[uid]
    if u["admin"]:
        return Text(f"👑 <b>{escape(u['name'])}</b> — администратор\n\nВидит все аккаунты, управляет доступами и журналом.",
                    kb([btn("← Назад", "adm:u")]))
    lines = [f"#{a.id} {escape(a.name)} — {ROLE_LABEL[u['roles'].get(a.id, 'none')]}" for a in ACCOUNTS.values()]
    text = (f"👤 <b>{escape(u['name'])}</b>\n━━━━━━━━━━━━━━━━\n" + "\n".join(lines) +
            "\n━━━━━━━━━━━━━━━━\n<i>Нажмите на аккаунт, чтобы сменить роль:\nнет доступа → чтение → чтение и ответы</i>")
    rows = [[btn(f"#{a.id} {a.name}: {ROLE_LABEL[u['roles'].get(a.id, 'none')]}", f"ar:{uid}:{a.id}")] for a in ACCOUNTS.values()]
    rows.append([btn("← Назад", "adm:u")])
    return Text(text, kb(*rows))


def log_screen() -> Text:
    if AUDIT:
        body = "\n".join(f"<code>{t}</code>  {escape(line)}" for t, line in reversed(AUDIT[-15:]))
    else:
        body = "<i>Пока пусто — потыкайте кнопки, и здесь появятся действия.</i>"
    return Text(f"📜 <b>Журнал действий</b>\n━━━━━━━━━━━━━━━━\n{body}",
                kb([btn("🔄 Обновить", "adm:l"), btn("← Назад", "adm")]))


# ─── Добавление аккаунта ────────────────────────────────────────────────────

def mask(phone_digits: str) -> str:
    return f"+{phone_digits[0]} ••• ••• {phone_digits[-4:-2]} {phone_digits[-2:]}"


def steps(stage: str, second: str = "Код") -> list[dict]:
    order = {"phone": 0, "code": 1, "password": 1, "done": 3}[stage]
    labels = ["Номер", second, "Готово"]
    return [dict(label=l, state="done" if i < order else "cur" if i == order else "todo") for i, l in enumerate(labels)]


def keypad() -> list:
    d = lambda x: btn(x, f"kp:{x}")
    return [
        [d("1"), d("2"), d("3")],
        [d("4"), d("5"), d("6")],
        [d("7"), d("8"), d("9")],
        [btn("⌫", "kp:b"), d("0"), btn("✓ Готово", "kp:ok", "success")],
        [btn("↻ Отправить код заново", "kp:r")],
        [btn("✕ Отмена", "addx", "danger")],
    ]


def add_card(stage: str, data: dict, error: str | None = None, a: Account | None = None) -> Card:
    relogin = data.get("mode") == "relogin"
    ctx = dict(title="Повторный вход" if relogin else "Добавить аккаунт", state=stage, error=error,
               steps=steps(stage, "2FA" if stage == "password" else "Код"),
               code=data.get("code", ""), phone=data.get("phone", ""), a=a,
               done_title="Вход выполнен" if relogin else (f"Аккаунт #{a.id} добавлен" if a else ""))
    cancel = [btn("✕ Отмена", "addx", "danger")]
    if stage == "phone":
        caption = ("<b>Шаг 1.</b> Отправьте номер телефона сообщением — я сразу удалю его из чата."
                   "\n<i>Демо: подойдёт любой номер, настоящий вход не выполняется.</i>")
        return Card("add.html", caption, kb(cancel), ctx)
    if stage == "code":
        caption = (f"Код отправлен в Telegram на <b>{data.get('phone', '')}</b>.\n"
                   "Вводите кнопками: код, отправленный сообщением, Telegram сразу аннулирует."
                   "\n<i>Демо: любой код, 00000 — покажет ошибку.</i>")
        return Card("add.html", caption, kb(*keypad()), ctx)
    if stage == "password":
        caption = ("<b>Включена двухэтапная аутентификация.</b>\nОтправьте пароль сообщением — удалю сразу, нигде не сохраняю."
                   "\n<i>Демо: подойдёт любой.</i>")
        return Card("add.html", caption, kb(cancel), ctx)
    caption = "✅ Сессия сохранена в зашифрованном виде.\n<i>Демо: ничего не сохранено, аккаунт выдуманный.</i>"
    return Card("add.html", caption, kb(
        [btn("👤 Открыть аккаунт", f"acc:{a.id}", "primary")],
        [btn("⚙️ Админ-панель", "adm")],
    ), ctx)


# ─── Хендлеры ───────────────────────────────────────────────────────────────

@router.message(Command("admin"))
async def admin_cmd(message: Message, state: FSMContext, ui: UI) -> None:
    await state.clear()
    await ui.drop(message.from_user.id)
    await ui.show(message.from_user.id, message.chat.id, admin_card())
    try:
        await message.delete()
    except Exception:
        pass


@router.callback_query(F.data == "adm")
async def admin(cq: CallbackQuery, state: FSMContext, ui: UI) -> None:
    await state.clear()
    await go(cq, ui, admin_card())


@router.callback_query(F.data == "adm:a")
async def accounts(cq: CallbackQuery, ui: UI) -> None:
    await go(cq, ui, accounts_card())


@router.callback_query(F.data == "adm:u")
async def users(cq: CallbackQuery, ui: UI) -> None:
    await go(cq, ui, users_card())


@router.callback_query(F.data == "adm:l")
async def log(cq: CallbackQuery, ui: UI) -> None:
    await go(cq, ui, log_screen())


@router.callback_query(F.data.regexp(r"^aa(t|d|dok)?:\d+$"))
async def manage(cq: CallbackQuery, ui: UI) -> None:
    action, acc_id = cq.data.split(":")
    a = ACCOUNTS.get(int(acc_id))
    if not a:
        return await go(cq, ui, accounts_card(), "Аккаунт уже удалён")
    if action == "aad":
        return await go(cq, ui, manage_card(a, confirm=True))
    if action == "aadok":
        del ACCOUNTS[a.id]
        audit("Ты", f"удалил аккаунт #{a.id} {a.name}")
        return await go(cq, ui, accounts_card(), f"Аккаунт #{a.id} удалён")
    if action == "aat":
        if a.status == "need_login":
            return await cq.answer("Сначала нужен повторный вход — кнопка «🔑 Перелогин».", show_alert=True)
        a.status = "offline" if a.status == "online" else "online"
        audit("Ты", f"#{a.id} {'включил' if a.status == 'online' else 'отключил'}")
    await go(cq, ui, manage_card(a))


@router.callback_query(F.data.startswith("au:"))
async def user(cq: CallbackQuery, ui: UI) -> None:
    await go(cq, ui, user_screen(int(cq.data.split(":")[1])))


@router.callback_query(F.data.startswith("ar:"))
async def role(cq: CallbackQuery, ui: UI) -> None:
    _, uid, acc_id = cq.data.split(":")
    u, acc_id = USERS[int(uid)], int(acc_id)
    cur = u["roles"].get(acc_id, "none")
    u["roles"][acc_id] = ROLES[(ROLES.index(cur) + 1) % len(ROLES)]
    audit("Ты", f"{u['name']} → #{acc_id}: {ROLE_LABEL[u['roles'][acc_id]]}")
    await go(cq, ui, user_screen(int(uid)))


@router.callback_query(F.data == "add")
async def add_start(cq: CallbackQuery, state: FSMContext, ui: UI) -> None:
    await state.set_state(Add.phone)
    await state.set_data({"mode": "add", "code": ""})
    await go(cq, ui, add_card("phone", {}))


@router.callback_query(F.data.startswith("relog:"))
async def relogin(cq: CallbackQuery, state: FSMContext, ui: UI) -> None:
    a = ACCOUNTS.get(int(cq.data.split(":")[1]))
    if not a:
        return await gone(cq, ui)
    data = {"mode": "relogin", "acc": a.id, "phone": a.phone_masked, "code": ""}
    await state.set_state(Add.code)
    await state.set_data(data)
    await go(cq, ui, add_card("code", data), "Код отправлен (демо)")


@router.callback_query(F.data == "addx")
async def add_cancel(cq: CallbackQuery, state: FSMContext, ui: UI) -> None:
    await state.clear()
    await go(cq, ui, admin_card(), "Отменено")


@router.message(Add.phone, F.text)
async def add_phone(message: Message, state: FSMContext, ui: UI) -> None:
    try:
        await message.delete()
    except Exception:
        pass
    digits = "".join(c for c in message.text if c.isdigit())
    if not 10 <= len(digits) <= 15:
        return await ui.show(message.from_user.id, message.chat.id,
                             add_card("phone", {}, error="Не похоже на номер"))
    await state.update_data(phone=mask(digits), phone_raw="+" + digits, code="")
    await state.set_state(Add.code)
    await ui.show(message.from_user.id, message.chat.id, add_card("code", await state.get_data()))


@router.callback_query(Add.code, F.data.startswith("kp:"))
async def keypad_press(cq: CallbackQuery, state: FSMContext, ui: UI) -> None:
    key = cq.data.split(":")[1]
    data = await state.get_data()
    code = data.get("code", "")
    if key == "r":
        return await cq.answer("Код отправлен заново (демо)", show_alert=False)
    if key == "b":
        code = code[:-1]
    elif key == "ok":
        if len(code) < 5:
            return await cq.answer("Нужно 5 цифр")
        if code == "00000":
            await state.update_data(code="")
            return await go(cq, ui, add_card("code", {**data, "code": ""}, error="Неверный код — попробуйте ещё раз"))
        await state.set_state(Add.password)
        return await go(cq, ui, add_card("password", data))
    elif len(code) < 5:
        code += key
    await state.update_data(code=code)
    await go(cq, ui, add_card("code", {**data, "code": code}))


@router.callback_query(F.data.startswith("kp:"))
async def keypad_stale(cq: CallbackQuery) -> None:
    await cq.answer("Этот вход уже закрыт — начните заново из админ-панели.", show_alert=True)


@router.message(Add.password, F.text)
async def add_password(message: Message, state: FSMContext, ui: UI) -> None:
    try:
        await message.delete()
    except Exception:
        pass
    data = await state.get_data()
    await state.clear()
    if data.get("mode") == "relogin":
        a = ACCOUNTS.get(data["acc"])
        if not a:
            return
        a.status = "online"
        audit("Ты", f"#{a.id} повторный вход")
    else:
        a = add_account(data.get("phone_raw", "+79000000000"))
        audit("Ты", f"добавил аккаунт #{a.id} {a.name}")
    await ui.show(message.from_user.id, message.chat.id, add_card("done", data, a=a))
