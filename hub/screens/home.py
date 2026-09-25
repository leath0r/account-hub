"""Главная, выбор языка и личные настройки (язык, тихие уведомления, группы, PIN)."""
from contextlib import suppress

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from access import Viewer
from i18n import LANGS
from screens.common import card, go, short, unread_by_account, visible
from tg import Hub
from ui import UI, Card, btn, kb

router = Router()


async def home_card(hub: Hub, me: Viewer) -> Card:
    accs = visible(hub, me)
    online = sum(a.status == "online" for a in accs)
    need = sum(a.status == "need_login" for a in accs)
    rows, pair = [], []
    for a in accs:
        pair.append(btn(f"{a.status_emoji} #{a.id} {short(a.name, 18)}", f"acc:{a.id}"))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    rows.append(pair)
    if accs:
        unread = await unread_by_account(hub, me, accs)
        inbox = sum(c.unread for chats in unread.values() for c in chats)
        rows.append([btn(me.t("📥 Входящие · {n}", n=inbox) if inbox else me.t("📥 Входящие"), "inbox:0", "primary")])
        rows.append([btn(me.t("🔎 Поиск везде"), "gs")])
    rows.append([btn(me.t("⚙️ Мои настройки"), "me"), btn("🌐 Язык / Language", "lang")])
    if me.admin:
        rows.append([btn(me.t("⚙️ Админ-панель"), "adm")])

    if accs:
        caption = me.t("Выберите аккаунт, с которым будете работать.")
        if need:
            caption += "\n🟠 <i>" + me.t("{n} {word} повторного входа", n=need,
                                          word=me.pl(need, "аккаунт ждёт", "аккаунта ждут", "аккаунтов ждут")) + "</i>"
    elif me.admin:
        caption = me.t("Аккаунтов пока нет — добавьте первый в админ-панели.")
        if not hub.api_ready:
            caption += "\n⚠️ <i>" + me.t("Сначала впишите API_ID и API_HASH в .env (my.telegram.org) и перезапустите бота.") + "</i>"
    else:
        caption = me.t("У вас пока нет доступа ни к одному аккаунту.\nПопросите администратора выдать доступ — ваш ID: <code>{id}</code>",
                       id=me.id)
    return card(me, "home.html", caption, kb(*rows),
                accounts=accs, online=online, need=need,
                acc_word=me.pl(len(accs), "аккаунт", "аккаунта", "аккаунтов"),
                empty_hint=me.t("Добавьте первый в админ-панели") if me.admin else me.t("Попросите админа выдать доступ"))


def lang_card(me: Viewer) -> Card:
    rows = [[btn(("✓ " if code == me.lang else "") + label, f"lang:{code}")] for code, label in LANGS.items()]
    rows.append([btn(me.t("← Главная"), "home")])
    return card(me, "list.html", "🌐 <b>Язык / Language</b>\nВыберите язык интерфейса.\nChoose the interface language.",
                kb(*rows), a=None, eyebrow="Account Hub", title="Язык · Language", sub=LANGS[me.lang],
                icon_name="settings", tile_bg="#1E222B", icon_color="#9AA1B2", page_label="")


def my_card(me: Viewer, note: str = "") -> Card:
    rows = [
        [btn(f"🌐 {me.t('Язык')}: {LANGS[me.lang]}", "lang")],
        [btn(me.t("🌙 Ночью без звука: вкл") if me.quiet else me.t("🔔 Ночью без звука: выкл"), "myq")],
        [btn(me.t("👥 Группы: все сообщения") if me.groups else me.t("👥 Группы: только упоминания"), "myg")],
    ]
    if me.pin:
        rows.append([btn(me.t("🔐 Сменить PIN"), "pinchg"), btn(me.t("🔓 Убрать PIN"), "pinoff")])
    else:
        rows.append([btn(me.t("🔐 Поставить PIN"), "pinset")])
    rows.append([btn(me.t("← Главная"), "home")])
    caption = me.t(
        "<b>Уведомления</b> о новых сообщениях приходят по аккаунтам, где они включены (⚙️ Настройки аккаунта).\n"
        "🌙 <b>Ночью без звука</b> — с 23:00 до 08:00 уведомления приходят тихо.\n"
        "👥 <b>Группы</b> — по умолчанию только упоминания, чтобы не заваливало.\n"
        "🔐 <b>PIN</b> — спрашивается перед удалением, «Написать первым», пересылкой и другими опасными действиями.")
    if note:
        caption = f"{note}\n\n{caption}"
    return card(me, "list.html", caption, kb(*rows), a=None, eyebrow="Account Hub", title=me.t("Мои настройки"),
                sub=me.t("PIN: стоит") if me.pin else me.t("PIN: не стоит"),
                icon_name="settings", tile_bg="#1E222B", icon_color="#9AA1B2", page_label="")


@router.message(CommandStart())
async def start(message: Message, state: FSMContext, ui: UI, hub: Hub, me: Viewer) -> None:
    await state.clear()
    await ui.drop(me.id)
    await ui.show(me.id, message.chat.id, await home_card(hub, me))
    with suppress(Exception):
        await message.delete()


@router.callback_query(F.data == "noop")
async def noop(cq: CallbackQuery) -> None:
    await cq.answer()


@router.callback_query(F.data == "home")
async def home(cq: CallbackQuery, state: FSMContext, ui: UI, hub: Hub, me: Viewer) -> None:
    await state.clear()
    await go(cq, ui, me, home_card(hub, me))


@router.callback_query(F.data == "lang")
async def lang_menu(cq: CallbackQuery, ui: UI, me: Viewer) -> None:
    await go(cq, ui, me, lang_card(me))


@router.callback_query(F.data.regexp(r"^lang:(ru|en)$"))
async def lang_set(cq: CallbackQuery, ui: UI, hub: Hub, me: Viewer) -> None:
    me.lang = cq.data.split(":")[1]
    await hub.db.set_user(me.id, lang=me.lang)
    await go(cq, ui, me, home_card(hub, me), me.t("Язык: русский"))


@router.callback_query(F.data == "me")
async def my_settings(cq: CallbackQuery, state: FSMContext, ui: UI, me: Viewer) -> None:
    await state.clear()
    await go(cq, ui, me, my_card(me))


@router.callback_query(F.data.in_({"myq", "myg"}))
async def my_toggle(cq: CallbackQuery, ui: UI, hub: Hub, me: Viewer) -> None:
    if cq.data == "myq":
        me.quiet = not me.quiet
        await hub.db.set_user(me.id, quiet=int(me.quiet))
    else:
        me.groups = not me.groups
        await hub.db.set_user(me.id, groups=int(me.groups))
    await go(cq, ui, me, my_card(me))
