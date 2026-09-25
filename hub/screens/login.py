"""Добавление аккаунта и перелогин: номер → код с кейпада → облачный пароль (2FA)."""
from contextlib import suppress
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from access import AdminGate, Viewer
from auth import LoginError, LoginExpired, Logins
from screens.admin import admin_card, manage_card
from screens.common import alert, card, err_line, go, record
from tg import Account, Hub
from ui import UI, Card, btn, kb

router = Router()
router.message.middleware(AdminGate())
router.callback_query.middleware(AdminGate())


class Add(StatesGroup):
    phone = State()
    code = State()
    password = State()


def steps(me: Viewer, stage: str, second: str) -> list[dict]:
    order = {"phone": 0, "code": 1, "password": 1, "done": 3}[stage]
    labels = [me.t("Номер"), second, me.t("Готово")]
    return [dict(label=label, state="done" if i < order else "cur" if i == order else "todo") for i, label in enumerate(labels)]


def keypad(me: Viewer) -> list:
    def d(x: str):
        return btn(x, f"kp:{x}")

    return [
        [d("1"), d("2"), d("3")],
        [d("4"), d("5"), d("6")],
        [d("7"), d("8"), d("9")],
        [btn("⌫", "kp:b"), d("0"), btn(me.t("✓ Готово"), "kp:ok", "success")],
        [btn(me.t("↻ Отправить код заново"), "kp:r")],
        [btn(me.t("✕ Отмена"), "addx", "danger")],
    ]


def add_card(me: Viewer, stage: str, data: dict, error: str | None = None, a: Account | None = None, result: str = "") -> Card:
    c = _add_card(me, stage, data, error, a, result)
    if error:
        c.caption = f"{err_line(me, error)}\n\n{c.caption}"
    return c


def _add_card(me: Viewer, stage: str, data: dict, error: str | None, a: Account | None, result: str) -> Card:
    relogin = data.get("mode") == "relogin"
    done = {"added": me.t("Аккаунт #{n} добавлен", n=a.id) if a else "",
            "updated": me.t("Аккаунт #{n} обновлён", n=a.id) if a else "",
            "relogin": me.t("Вход выполнен")}.get(result, "")
    ctx = dict(title=me.t("Повторный вход") if relogin else me.t("Добавить аккаунт"), state=stage, error=error,
               steps=steps(me, stage, "2FA" if stage == "password" else me.t("Код")),
               code=data.get("code", ""), code_len=data.get("code_len", 5), phone=data.get("phone", ""), a=a,
               done_title=done)
    cancel = [btn(me.t("✕ Отмена"), "addx", "danger")]
    if stage == "phone":
        caption = me.t("<b>Шаг 1.</b> Отправьте номер телефона сообщением в международном формате (+7…) — "
                       "я сразу удалю его из чата.\n<i>Добавляйте только свои аккаунты или с согласия владельца.</i>")
        return card(me, "add.html", caption, kb(cancel), **ctx)
    if stage == "code":
        caption = me.t("Код отправлен {via} на <b>{phone}</b>.\n"
                       "Вводите кнопками: код, отправленный сообщением, Telegram сразу аннулирует.",
                       via=me.t(data.get("via", "в Telegram")), phone=data.get("phone", ""))
        return card(me, "add.html", caption, kb(*keypad(me)), **ctx)
    if stage == "password":
        caption = me.t("<b>Включена двухэтапная аутентификация.</b>\n"
                       "Отправьте облачный пароль сообщением — удалю сразу, нигде не сохраняю.")
        return card(me, "add.html", caption, kb(cancel), **ctx)
    caption = "✅ " + (me.t("Этот аккаунт уже был в панели — сессия обновлена.") if result == "updated"
                      else me.t("Сессия сохранена в зашифрованном виде."))
    return card(me, "add.html", caption, kb(
        [btn(me.t("👤 Открыть аккаунт"), f"acc:{a.id}", "primary")],
        [btn(me.t("⚙️ Админ-панель"), "adm")],
    ), **ctx)


def mask(digits: str) -> str:
    return f"+{digits[0]} ••• ••• {digits[-4:-2]} {digits[-2:]}"


@router.callback_query(F.data == "add")
async def add_start(cq: CallbackQuery, state: FSMContext, ui: UI, hub: Hub, logins: Logins, me: Viewer) -> None:
    if not hub.api_ready:
        return await alert(cq, me.t("Сначала впишите API_ID и API_HASH в .env (my.telegram.org) и перезапустите бота."))
    await logins.cancel(me.id)
    await state.set_state(Add.phone)
    await state.set_data({"mode": "add", "code": ""})
    await go(cq, ui, me, add_card(me, "phone", {}))


@router.callback_query(F.data.regexp(r"^relog:\d+$"))
async def relogin(cq: CallbackQuery, state: FSMContext, ui: UI, hub: Hub, logins: Logins, me: Viewer) -> None:
    a = hub.accs.get(int(cq.data.split(":")[1]))
    if not a:
        return await alert(cq, me.t("Этого аккаунта больше нет"))
    if not hub.api_ready:
        return await alert(cq, me.t("Не заданы API_ID и API_HASH в .env"))
    if not a.phone:
        return await alert(cq, me.t("Номер этого аккаунта не расшифровать (сменился SESSION_KEY?). "
                                    "Удалите аккаунт и добавьте заново."))

    async def build() -> Card:
        try:
            p = await logins.begin(me.id, a.phone, account_id=a.id)
        except LoginError as e:
            await state.clear()
            c = await manage_card(hub, me, a)
            c.caption = f"{err_line(me, e)}\n{c.caption}"
            return c
        data = {"mode": "relogin", "acc": a.id, "phone": a.phone_masked, "code": "", "code_len": p.code_len, "via": p.via}
        await state.set_state(Add.code)
        await state.set_data(data)
        return add_card(me, "code", data)

    await go(cq, ui, me, build())


@router.callback_query(F.data == "addx")
async def add_cancel(cq: CallbackQuery, state: FSMContext, ui: UI, hub: Hub, logins: Logins, me: Viewer) -> None:
    await state.clear()
    await logins.cancel(me.id)
    await go(cq, ui, me, admin_card(hub, me), me.t("Отменено"))


@router.message(Add.phone, F.text)
async def add_phone(message: Message, state: FSMContext, ui: UI, logins: Logins, me: Viewer) -> None:
    with suppress(Exception):
        await message.delete()
    digits = "".join(c for c in message.text if c.isdigit())
    if not 10 <= len(digits) <= 15:
        return await ui.show(me.id, message.chat.id, add_card(me, "phone", {}, error=me.t("Не похоже на номер")))
    try:
        p = await logins.begin(me.id, "+" + digits)
    except LoginError as e:
        return await ui.show(me.id, message.chat.id, add_card(me, "phone", {}, error=me.err(e)))
    data = {"mode": "add", "phone": mask(digits), "code": "", "code_len": p.code_len, "via": p.via}
    await state.set_state(Add.code)
    await state.set_data(data)
    await ui.show(me.id, message.chat.id, add_card(me, "code", data))


async def login_done(state: FSMContext, hub: Hub, me: Viewer, data: dict, result: tuple[Account, str]) -> Card:
    acc, kind = result
    await state.clear()
    what = {"added": "добавил аккаунт #{n} {name}", "updated": "обновил сессию #{n} {name}", "relogin": "перелогин #{n} {name}"}[kind]
    await record(hub, me, acc.id, "login", what, n=acc.id, name=acc.name)
    return add_card(me, "done", data, a=acc, result=kind)


async def login_failed(state: FSMContext, hub: Hub, me: Viewer, data: dict, stage: str, e: LoginError) -> Card:
    if isinstance(e, LoginExpired):
        await state.clear()
        c = await admin_card(hub, me)
        c.caption = f"⌛ <b>{escape(me.err(e))}</b>\n{c.caption}"
        return c
    if stage == "code":
        data["code"] = ""
        await state.set_data(data)
    return add_card(me, stage, data, error=me.err(e))


@router.callback_query(Add.code, F.data.startswith("kp:"))
async def keypad_press(cq: CallbackQuery, state: FSMContext, ui: UI, hub: Hub, logins: Logins, me: Viewer) -> None:
    key = cq.data.split(":")[1]
    data = await state.get_data()
    code, need = data.get("code", ""), data.get("code_len", 5)

    async def resend() -> Card:
        try:
            p = await logins.resend(me.id)
        except LoginError as e:
            return await login_failed(state, hub, me, data, "code", e)
        data.update(code="", code_len=p.code_len, via=p.via)
        await state.set_data(data)
        return add_card(me, "code", data)

    async def submit() -> Card:
        try:
            result = await logins.code(me.id, code)
        except LoginError as e:
            return await login_failed(state, hub, me, data, "code", e)
        if result is None:
            await state.set_state(Add.password)
            return add_card(me, "password", data)
        return await login_done(state, hub, me, data, result)

    if key == "r":
        return await go(cq, ui, me, resend(), me.t("Код отправлен заново"))
    if key == "ok":
        if len(code) < need:
            return await cq.answer(me.t("Нужно {n} цифр", n=need))
        return await go(cq, ui, me, submit())
    if key == "b":
        data["code"] = code[:-1]
    elif key.isdigit() and len(code) < need:
        data["code"] = code + key
    await state.set_data(data)
    await go(cq, ui, me, add_card(me, "code", data))


@router.callback_query(F.data.startswith("kp:"))
async def keypad_stale(cq: CallbackQuery, me: Viewer) -> None:
    await alert(cq, me.t("Этот вход уже закрыт — начните заново из админ-панели."))


@router.message(Add.password, F.text)
async def add_password(message: Message, state: FSMContext, ui: UI, hub: Hub, logins: Logins, me: Viewer) -> None:
    with suppress(Exception):
        await message.delete()
    data = await state.get_data()
    try:
        result = await logins.password(me.id, message.text)
    except LoginError as e:
        c = await login_failed(state, hub, me, data, "password", e)
    else:
        c = await login_done(state, hub, me, data, result)
    await ui.show(me.id, message.chat.id, c)
