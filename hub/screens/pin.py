"""PIN на опасные действия: удалить, «Написать первым», переслать, удалить аккаунт, завершить сессию.

PinGate перехватывает такую кнопку, если у человека стоит PIN, и показывает кейпад. PIN верный → 5 минут
не спрашиваем снова, а исходное нажатие выполняется само (повторно прогоняется через диспетчер).
Хранится только PBKDF2-хэш с солью. 5 ошибок подряд → блокировка на 10 минут.
"""
import hashlib
import hmac
import secrets
import time
from contextlib import suppress

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Update

from access import Viewer
from screens.common import LINE, alert, go
from tg import Hub
from ui import UI, Text, btn, kb

router = Router()

PIN_OPS = {"dlx", "new", "fwx", "aadok", "setxok", "pinchg", "pinoff", "upin"}
GRACE = 300          # сек — после верного PIN не спрашиваем
MAX_FAILS = 5
LOCK = 600           # сек — блокировка после 5 ошибок
PIN_LEN = (4, 6)

UNLOCKED: dict[int, float] = {}     # user → до какого времени PIN не нужен
PENDING: dict[int, str] = {}        # user → какое нажатие выполнить после PIN
PAD: dict[int, dict] = {}           # user → {"mode": verify|set1|set2, "digits": "", "first": ""}
FAILS: dict[int, int] = {}
LOCKED: dict[int, float] = {}


def hash_pin(pin: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, 200_000)
    return f"{salt.hex()}${digest.hex()}"


def check_pin(stored: str | None, pin: str) -> bool:
    if not stored or "$" not in stored:
        return False
    salt, digest = stored.split("$", 1)
    got = hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), 200_000)
    return hmac.compare_digest(got.hex(), digest)


def unlocked(uid: int) -> bool:
    return UNLOCKED.get(uid, 0) > time.monotonic()


def pad_screen(me: Viewer, error: str = "") -> Text:
    st = PAD.get(me.id, {"mode": "verify", "digits": ""})
    heads = {"verify": "🔐 <b>Введите PIN</b>", "set1": "🔐 <b>Придумайте PIN</b> — 4–6 цифр",
             "set2": "🔐 <b>Повторите PIN</b>"}
    dots = " ".join("●" for _ in st["digits"]) or "—"
    text = f"{me.t(heads[st['mode']])}\n{LINE}\n<code>{dots}</code>\n{LINE}"
    if st["mode"] == "verify":
        text += "\n<i>" + me.t("Это действие защищено PIN-кодом. Цифры не попадают в историю чата.") + "</i>"
    if error:
        text = f"❌ <b>{error}</b>\n\n{text}"

    def d(x: str):
        return btn(x, f"pn:{x}")

    return Text(text, kb(
        [d("1"), d("2"), d("3")],
        [d("4"), d("5"), d("6")],
        [d("7"), d("8"), d("9")],
        [btn("⌫", "pn:b"), d("0"), btn(me.t("✓ Готово"), "pn:ok", "success")],
        [btn(me.t("✕ Отмена"), "pn:x", "danger")],
    ))


class PinGate(BaseMiddleware):
    """Опасная кнопка + стоит PIN + не вводили последние 5 минут → сначала кейпад."""

    async def __call__(self, handler, event, data):
        if isinstance(event, CallbackQuery):
            op = (event.data or "").split(":")[0]
            me: Viewer | None = data.get("me")
            if op in PIN_OPS and me and me.pin and not unlocked(me.id):
                PENDING[me.id] = event.data
                PAD[me.id] = {"mode": "verify", "digits": ""}
                await go(event, data["ui"], me, pad_screen(me))
                return None
        return await handler(event, data)


async def redispatch(cq: CallbackQuery, bot: Bot, dp: Dispatcher, data: str) -> None:
    """Выполнить отложенное нажатие так, будто его сделали сейчас (экран — сообщение с кейпадом)."""
    again = cq.model_copy(update={"data": data}).as_(bot)
    await dp.feed_update(bot, Update(update_id=0, callback_query=again))


@router.callback_query(F.data.startswith("pn:"))
async def pad_press(cq: CallbackQuery, ui: UI, hub: Hub, me: Viewer, bot: Bot, dp: Dispatcher) -> None:
    from screens.home import home_card, my_card
    key = cq.data.split(":")[1]
    st = PAD.get(me.id)
    if st is None:
        return await alert(cq, me.t("Ввод PIN уже закрыт — начните заново"))
    if key == "x":
        PAD.pop(me.id, None)
        PENDING.pop(me.id, None)
        return await go(cq, ui, me, home_card(hub, me), me.t("Отменено"))
    if key == "b":
        st["digits"] = st["digits"][:-1]
        return await go(cq, ui, me, pad_screen(me))
    if key.isdigit():
        if len(st["digits"]) < PIN_LEN[1]:
            st["digits"] += key
        return await go(cq, ui, me, pad_screen(me))

    digits = st["digits"]  # key == "ok"
    if not PIN_LEN[0] <= len(digits) <= PIN_LEN[1]:
        return await alert(cq, me.t("PIN — от 4 до 6 цифр"))
    if st["mode"] == "verify":
        if LOCKED.get(me.id, 0) > time.monotonic():
            return await alert(cq, me.t("Слишком много ошибок — попробуйте через 10 минут"))
        if not check_pin(me.pin, digits):
            FAILS[me.id] = FAILS.get(me.id, 0) + 1
            st["digits"] = ""
            if FAILS[me.id] >= MAX_FAILS:
                FAILS.pop(me.id, None)
                LOCKED[me.id] = time.monotonic() + LOCK
                PAD.pop(me.id, None)
                PENDING.pop(me.id, None)
                await hub.db.log(me.id, None, "5 неверных PIN подряд — блокировка на 10 минут")
                return await go(cq, ui, me, home_card(hub, me), me.t("Слишком много ошибок — попробуйте через 10 минут"))
            return await go(cq, ui, me, pad_screen(me, me.t("Неверный PIN — осталось попыток: {n}", n=MAX_FAILS - FAILS[me.id])))
        FAILS.pop(me.id, None)
        UNLOCKED[me.id] = time.monotonic() + GRACE
        PAD.pop(me.id, None)
        pending = PENDING.pop(me.id, None)
        if pending:
            return await redispatch(cq, bot, dp, pending)  # ответ на кнопку даст сам отложенный хендлер
        return await go(cq, ui, me, my_card(me))
    if st["mode"] == "set1":
        PAD[me.id] = {"mode": "set2", "digits": "", "first": digits}
        return await go(cq, ui, me, pad_screen(me))
    if digits != st.get("first"):  # set2
        PAD[me.id] = {"mode": "set1", "digits": ""}
        return await go(cq, ui, me, pad_screen(me, me.t("PIN не совпал — придумайте заново")))
    PAD.pop(me.id, None)
    me.pin = hash_pin(digits)
    await hub.db.set_user(me.id, pin=me.pin)
    await hub.db.log(me.id, None, "поставил PIN")
    UNLOCKED[me.id] = time.monotonic() + GRACE
    await go(cq, ui, me, my_card(me, "✅ " + me.t("PIN установлен")))


@router.callback_query(F.data.in_({"pinset", "pinchg"}))
async def pin_set(cq: CallbackQuery, ui: UI, me: Viewer) -> None:
    """pinchg проходит через PinGate — сюда попадаем уже после проверки старого PIN."""
    PAD[me.id] = {"mode": "set1", "digits": ""}
    await go(cq, ui, me, pad_screen(me))


@router.callback_query(F.data == "pinoff")
async def pin_off(cq: CallbackQuery, ui: UI, hub: Hub, me: Viewer) -> None:
    from screens.home import my_card
    me.pin = None
    await hub.db.set_user(me.id, pin=None)
    await hub.db.log(me.id, None, "убрал PIN")
    with suppress(TelegramBadRequest):
        await go(cq, ui, me, my_card(me, "🔓 " + me.t("PIN убран")))
