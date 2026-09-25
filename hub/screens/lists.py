"""Списки аккаунта: чаты, группы, непрочитанное, контакты, результаты поиска. На карточке — аватарки чатов страницы."""
import asyncio
from dataclasses import dataclass

from aiogram import F, Router
from aiogram.types import CallbackQuery

from access import Viewer
from screens.common import LIST_META, PAGE, SEARCH, alert, card, chat_label, go, gone, pager, record, short, usable
from tg import STYLES, Account, Hub, HubError, photo_uri
from ui import UI, Card, btn, kb

router = Router()
FACES = 6


@dataclass
class Face:
    """Аватарка чата для карточки — те же поля, что у аккаунта в макросе avatar()."""
    letter: str
    style: str
    photo_uri: str
    status_color: str = "#34D399"


@dataclass
class Item:
    label: str
    data: str
    peer: int       # id чата/человека — для аватарки
    name: str
    unread: int = 0


async def faces(hub: Hub, a: Account, items: list[Item]) -> list[dict]:
    """Аватарки первых чатов страницы: фото из Telegram (кэш 6 ч) или буква на градиенте."""
    items = items[:FACES]
    photos = await asyncio.gather(*(hub.chat_photo(a.id, i.peer) for i in items), return_exceptions=True)
    out = []
    for i, ph in zip(items, photos):
        ph = ph if isinstance(ph, bytes) else None
        out.append(dict(a=Face((i.name[:1] or "?").upper(), STYLES[abs(i.peer) % len(STYLES)], photo_uri(ph)),
                        count=i.unread or None))
    return out


async def list_items(hub: Hub, me: Viewer, a: Account, kind: str) -> list[Item]:
    chats = await hub.dialogs(a.id)
    muted = await hub.db.muted(me.id, a.id)

    def item(c, back: str) -> Item:
        quiet = c.tg_muted or c.id in muted
        return Item(chat_label(me, c, muted), f"dlg:{a.id}:{c.id}:{back}", c.id, c.title, 0 if quiet else c.unread)

    if kind == "c":
        return [item(c, "c") for c in chats if c.kind == "user"]
    if kind == "g":
        return [item(c, "g") for c in chats if c.kind != "user"]
    if kind == "u":
        live = [c for c in chats if c.unread and not c.tg_muted and c.id not in muted]
        return [item(c, "u") for c in sorted(live, key=lambda c: -c.last_ts)]
    if kind == "k":
        dialog_ids = {c.id for c in chats if c.kind == "user"}
        return [Item(f"👤 {short(p.name)}", f"dlg:{a.id}:{p.id}:k", p.id, p.name) if p.id in dialog_ids
                else Item(f"✉️ {short(p.name)}", f"ct:{a.id}:{p.id}", p.id, p.name)
                for p in await hub.contacts(a.id)]
    by_id = {c.id: c for c in chats}
    found = SEARCH.get(me.id)
    ids = found[2] if found and found[0] == a.id else []
    return [item(by_id[i], "s") for i in ids if i in by_id]


def list_sub(me: Viewer, chats, kind: str, n: int) -> str:
    if kind == "c":
        fresh = sum(1 for c in chats if c.kind == "user" and c.unread)
        return me.t("{n} {word} · {fresh} с новыми", n=n, word=me.pl(n, "диалог", "диалога", "диалогов"), fresh=fresh)
    if kind == "g":
        g = sum(c.kind == "group" for c in chats)
        k = sum(c.kind == "channel" for c in chats)
        return (f"{g} {me.pl(g, 'группа', 'группы', 'групп')} · "
                f"{k} {me.pl(k, 'канал', 'канала', 'каналов')}")
    if kind == "u":
        return me.t("{n} {word} с новыми", n=n, word=me.pl(n, "чат", "чата", "чатов")) if n else me.t("Всё прочитано ✓")
    if kind == "k":
        return f"{n} {me.pl(n, 'контакт', 'контакта', 'контактов')}"
    return me.t("Найдено: {n}", n=n)


async def list_card(hub: Hub, me: Viewer, a: Account, kind: str, page: int) -> Card:
    title_key, icon_name, tile_bg, icon_color = LIST_META[kind]
    items = await list_items(hub, me, a, kind)
    pages = max(1, (len(items) + PAGE - 1) // PAGE)
    page = min(max(page, 0), pages - 1)
    shown = items[page * PAGE:(page + 1) * PAGE]
    header = f"«{short(SEARCH[me.id][1], 22)}»" if kind == "s" and me.id in SEARCH else me.t(title_key)
    rows = [[btn(i.label, i.data)] for i in shown]
    rows.append(pager(f"ls:{a.id}:{kind}", page, pages))
    if kind == "u" and items and me.can_write(a.id):
        rows.append([btn(me.t("✓ Прочитать всё"), f"rda:{a.id}", "success")])
    if kind == "s":
        rows.append([btn(me.t("🔎 Новый поиск"), f"srch:{a.id}")])
    rows.append([btn(me.t("← Назад"), f"acc:{a.id}")])
    if not items:
        caption = me.t("Ничего не нашлось — попробуйте другой запрос.") if kind == "s" else me.t("Здесь пусто.")
    elif kind == "k":
        caption = me.t("✉️ — переписки ещё нет: нажмите, чтобы написать первым. 👤 — открыть переписку.")
    else:
        caption = me.t("Нажмите на чат, чтобы открыть переписку.")
    return card(me, "list.html", caption, kb(*rows),
                a=a, eyebrow=f"{me.t('Аккаунт #{n}', n=a.id)} · {a.name}", title=header,
                sub=list_sub(me, await hub.dialogs(a.id), kind, len(items)),
                icon_name=icon_name, tile_bg=tile_bg, icon_color=icon_color, faces=await faces(hub, a, shown),
                page_label=me.t("стр. {page} / {pages}", page=page + 1, pages=pages) if pages > 1 else "")


@router.callback_query(F.data.startswith("ls:"))
async def lists(cq: CallbackQuery, ui: UI, hub: Hub, me: Viewer) -> None:
    from screens.account import account_card
    _, acc_id, kind, page = cq.data.split(":")
    a = hub.accs.get(int(acc_id))
    if not a:
        return await gone(cq, ui, hub, me)
    if not usable(a) or kind not in LIST_META or not page.isdigit():
        return await go(cq, ui, me, account_card(hub, me, a))
    await go(cq, ui, me, list_card(hub, me, a, kind, int(page)))


async def read_all(hub: Hub, me: Viewer, a: Account) -> int:
    """Пометить прочитанными все чаты аккаунта, которые этот человек видит как непрочитанные."""
    muted = await hub.db.muted(me.id, a.id)
    # свежий список, а не кэш: только что пришедшие сообщения тоже должны прочитаться
    live = [c for c in await hub.dialogs(a.id, force=True) if c.unread and not c.tg_muted and c.id not in muted][:40]
    done = 0
    for c in live:
        try:
            await hub.mark_read(a.id, c)
            done += 1
        except HubError:
            break
    if done:
        await record(hub, me, a.id, "read_all", "#{n} прочитал всё ({k} чатов)", n=a.id, k=done)
    return done


@router.callback_query(F.data.startswith("rda:"))
async def read_all_account(cq: CallbackQuery, ui: UI, hub: Hub, me: Viewer) -> None:
    a = hub.accs.get(int(cq.data.split(":")[1]))
    if not usable(a):
        return await gone(cq, ui, hub, me)
    try:
        done = await read_all(hub, me, a)
    except HubError as e:
        return await alert(cq, me.err(e))
    await go(cq, ui, me, list_card(hub, me, a, "u", 0), me.t("Прочитано чатов: {n}", n=done))
