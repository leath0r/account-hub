"""Вход в аккаунт: номер → код с кейпада → 2FA. Клиент живёт в памяти, пока идёт вход (до 10 минут).

Номер и пароль сюда приходят уже удалёнными из чата с ботом; пароль нигде не сохраняется.
"""
import asyncio
import logging
import time
from contextlib import suppress
from dataclasses import dataclass, field

from telethon import TelegramClient, errors

from tg import Account, Hub, HubError, fmt_wait

log = logging.getLogger("auth")

TTL = 600

ERRORS = [
    (errors.PhoneNumberInvalidError, "Неверный номер — проверьте и отправьте ещё раз"),
    (errors.PhoneNumberBannedError, "Этот номер заблокирован в Telegram"),
    (errors.PhoneNumberUnoccupiedError, "На этот номер нет аккаунта Telegram"),
    (errors.PhoneNumberFloodError, "Слишком много попыток входа с этим номером — попробуйте завтра"),
    (errors.PhoneCodeInvalidError, "Неверный код — попробуйте ещё раз"),
    (errors.PhoneCodeEmptyError, "Введите код"),
    (errors.PhoneCodeExpiredError, "Код истёк — нажмите «Отправить код заново»"),
    (errors.PasswordHashInvalidError, "Неверный пароль — отправьте ещё раз"),
    (errors.ApiIdInvalidError, "API_ID / API_HASH не подходят — проверьте .env"),
    (errors.SendCodeUnavailableError, "Telegram больше не может отправить код — попробуйте позже"),
]

VIA = {
    "SentCodeTypeApp": "в приложение Telegram",
    "SentCodeTypeSms": "по SMS",
    "SentCodeTypeCall": "звонком",
    "SentCodeTypeFlashCall": "звонком",
    "SentCodeTypeMissedCall": "пропущенным звонком",
    "SentCodeTypeFragmentSms": "через Fragment",
    "SentCodeTypeEmailCode": "на почту",
}


class LoginError(HubError):
    pass


class LoginExpired(LoginError):
    """Вход брошен дольше 10 минут или уже закрыт — начинать заново."""


def explain(e: Exception) -> str:
    if isinstance(e, errors.FloodWaitError):
        return f"Слишком много попыток. Повторить через {fmt_wait(e.seconds)}"
    for cls, text in ERRORS:
        if isinstance(e, cls):
            return text
    if isinstance(e, HubError):
        return str(e)
    if isinstance(e, (ConnectionError, OSError, asyncio.TimeoutError)):
        return "Нет связи с Telegram — попробуйте позже"
    if isinstance(e, errors.RPCError):
        return f"Telegram: {e.message}"
    log.exception("login failed", exc_info=e)
    return "Что-то пошло не так — попробуйте ещё раз"


@dataclass
class Pending:
    client: TelegramClient
    phone: str
    code_hash: str
    code_len: int
    via: str
    account_id: int | None             # None — новый аккаунт, иначе перелогин
    started: float = field(default_factory=time.monotonic)


class Logins:
    def __init__(self, hub: Hub) -> None:
        self.hub = hub
        self.pending: dict[int, Pending] = {}   # админ → его незавершённый вход

    async def begin(self, uid: int, phone: str, account_id: int | None = None) -> Pending:
        await self.cancel(uid)
        client = self.hub.new_client()
        try:
            await asyncio.wait_for(client.connect(), 30)
            sent = await client.send_code_request(phone)
        except Exception as e:
            await self._close(client)
            raise LoginError(explain(e)) from e
        kind = type(sent.type).__name__
        if "SetUpEmail" in kind:
            await self._close(client)
            raise LoginError("Telegram требует сначала привязать почту — войдите один раз в официальном приложении")
        p = Pending(client, phone, sent.phone_code_hash, getattr(sent.type, "length", None) or 5,
                    VIA.get(kind, "в Telegram"), account_id)
        self.pending[uid] = p
        return p

    def get(self, uid: int) -> Pending:
        p = self.pending.get(uid)
        if p is None or time.monotonic() - p.started > TTL:
            raise LoginExpired("Вход устарел — начните заново")
        return p

    async def resend(self, uid: int) -> Pending:
        p = self.get(uid)
        try:
            sent = await p.client.send_code_request(p.phone)
        except Exception as e:
            raise LoginError(explain(e)) from e
        p.code_hash = sent.phone_code_hash
        p.code_len = getattr(sent.type, "length", None) or p.code_len
        p.via = VIA.get(type(sent.type).__name__, p.via)
        p.started = time.monotonic()
        return p

    async def code(self, uid: int, code: str) -> tuple[Account, str] | None:
        """None — включена 2FA, дальше нужен пароль."""
        p = self.get(uid)
        try:
            await p.client.sign_in(p.phone, code, phone_code_hash=p.code_hash)
        except errors.SessionPasswordNeededError:
            return None
        except Exception as e:
            raise LoginError(explain(e)) from e
        return await self._finish(uid, p)

    async def password(self, uid: int, password: str) -> tuple[Account, str]:
        p = self.get(uid)
        try:
            await p.client.sign_in(password=password)
        except Exception as e:
            raise LoginError(explain(e)) from e
        return await self._finish(uid, p)

    async def _finish(self, uid: int, p: Pending) -> tuple[Account, str]:
        self.pending.pop(uid, None)
        try:
            return await self.hub.adopt(p.client, p.phone, p.account_id)
        except Exception as e:
            await self._close(p.client)
            raise LoginError(explain(e)) from e

    async def cancel(self, uid: int) -> None:
        p = self.pending.pop(uid, None)
        if p:
            await self._close(p.client)

    async def sweep(self) -> None:
        """Раз в минуту закрывает брошенные входы."""
        while True:
            await asyncio.sleep(60)
            now = time.monotonic()
            for uid in [u for u, p in self.pending.items() if now - p.started > TTL]:
                await self.cancel(uid)

    @staticmethod
    async def _close(client: TelegramClient) -> None:
        with suppress(Exception):
            await client.disconnect()
