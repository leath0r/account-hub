"""Пуш-уведомления о новых сообщениях.

Новое входящее в любом аккаунте → всем, кто видит аккаунт и не выключил уведомления, приходит короткое
сообщение с кнопками [Открыть] [Ответить] [Прочитано]. Сообщения копятся DEBOUNCE секунд и приходят пачкой.
Группы — только упоминания (или всё, если человек включил). Каналы — никогда. Тихие часы — без звука.
"""
import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from html import escape

from aiogram import Bot
from telethon import utils

from i18n import pl, tr
from tg import Account, Hub, describe
from ui import btn, kb

log = logging.getLogger("notify")

DEBOUNCE = 4.0          # сек — копим сообщения, чтобы не слать по одному
QUIET = (23, 8)         # тихие часы: с 23:00 до 08:00 — без звука
MAX_LINES = 6


@dataclass
class Item:
    acc_id: int
    chat_id: int
    title: str
    who: str
    text: str
    group: bool


class Notifier:
    def __init__(self, bot: Bot, hub: Hub) -> None:
        self.bot, self.hub = bot, hub
        self.queue: dict[int, list[Item]] = {}
        self.timers: dict[int, asyncio.Task] = {}
        self.sent = 0

    async def on_message(self, acc: Account, event) -> None:
        """Вызывается из Telethon на каждое входящее сообщение аккаунта."""
        if event.is_channel and not event.is_group:  # каналы не уведомляют
            return
        group = not event.is_private
        m = event.message
        sender = chat = None
        with suppress(Exception):
            sender = await asyncio.wait_for(event.get_sender(), 5)
        who = (utils.get_display_name(sender) if sender else "") or "—"
        if group:
            with suppress(Exception):
                chat = await asyncio.wait_for(event.get_chat(), 5)
        title = (utils.get_display_name(chat) if chat else "") if group else who
        tg_muted = next((c.tg_muted for c in self.hub.cached_dialogs(acc.id) if c.id == event.chat_id), False)
        if tg_muted:
            return
        item = Item(acc.id, event.chat_id, title or "—", who, describe(m) or "…", group)
        mentioned = bool(getattr(m, "mentioned", False))
        for u in await self.hub.db.audience(acc.id, set(self.hub.cfg.admin_ids)):
            uid = u["id"]
            if not await self.hub.db.notify(uid, acc.id) or event.chat_id in await self.hub.db.muted(uid, acc.id):
                continue
            if group and not mentioned and not u["groups"]:
                continue
            self.queue.setdefault(uid, []).append(item)
            if uid not in self.timers:
                self.timers[uid] = asyncio.create_task(self._flush_later(uid))

    async def _flush_later(self, uid: int) -> None:
        try:
            await asyncio.sleep(DEBOUNCE)
            items = self.queue.pop(uid, [])
            if items:
                await self._send(uid, items)
        except Exception:
            log.exception("уведомление %d не отправлено", uid)
        finally:
            self.timers.pop(uid, None)

    def _quiet_now(self) -> bool:
        h = datetime.now(self.hub.cfg.tz).hour
        return h >= QUIET[0] or h < QUIET[1]

    async def _send(self, uid: int, items: list[Item]) -> None:
        u = await self.hub.db.user(uid)
        if u is None or u["banned"]:
            return
        lang = u["lang"] or "ru"
        admin = bool(u["admin"]) or uid in self.hub.cfg.admin_ids
        can_write = admin or (await self.hub.db.roles(uid)).get(items[0].acc_id) == "operator"
        chats: dict[tuple[int, int], list[Item]] = {}
        for it in items:
            chats.setdefault((it.acc_id, it.chat_id), []).append(it)

        def t(text: str, /, **kw) -> str:
            return tr(lang, text, **kw)

        if len(chats) == 1:
            (acc_id, chat_id), msgs = next(iter(chats.items()))
            first = msgs[0]
            head = f"🔔 <b>#{acc_id} · {'👥 ' if first.group else ''}{escape(first.title[:40])}</b>"
            lines = [(f"<b>{escape(it.who[:24])}:</b> " if it.group else "") + escape(it.text[:160]) for it in msgs[-3:]]
            if len(msgs) > 3:
                lines.insert(0, "<i>" + t("…и ещё {n}", n=len(msgs) - 3) + "</i>")
            rows = [[btn(t("💬 Открыть"), f"nfo:{acc_id}:{chat_id}")]]
            if can_write:
                rows[0].append(btn(t("✍ Ответить"), f"nfp:{acc_id}:{chat_id}"))
                rows.append([btn(t("✓ Прочитано"), f"nfr:{acc_id}:{chat_id}")])
            text = head + "\n" + "\n".join(lines)
        else:
            n = len(items)
            head = "🔔 <b>" + t("{n} {word} в {k} чатах", n=n, k=len(chats),
                                word=pl(lang, n, "новое сообщение", "новых сообщения", "новых сообщений")) + "</b>"
            lines = [f"• #{it.acc_id} · {escape(it.title[:28])}: {escape(it.text[:80])}"
                     for msgs in chats.values() for it in msgs[-1:]][:MAX_LINES]
            rows = [[btn(t("📥 Открыть входящие"), "nfi")]]
            text = head + "\n" + "\n".join(lines)
        silent = bool(u["quiet"]) and self._quiet_now()
        with suppress(Exception):  # человек мог заблокировать бота — не повод падать
            await self.bot.send_message(uid, text, reply_markup=kb(*rows), disable_notification=silent)
            self.sent += 1
            await self.hub.db.event(uid, items[0].acc_id, "notify")
