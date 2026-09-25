"""SQLite (WAL): пользователи, аккаунты, доступы, настройки, журнал, кэш карточек."""
import json
import time
from pathlib import Path

import aiosqlite

SCHEMA = """
PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS users (
    id       INTEGER PRIMARY KEY,           -- telegram user id
    name     TEXT    NOT NULL,
    username TEXT,
    admin    INTEGER NOT NULL DEFAULT 0,    -- выдан из бота; ADMIN_IDS из .env сюда не пишутся
    banned   INTEGER NOT NULL DEFAULT 0,
    created  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS accounts (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_id    INTEGER UNIQUE,
    name     TEXT    NOT NULL,
    username TEXT,
    phone    BLOB    NOT NULL,              -- зашифрован
    session  BLOB,                          -- зашифрован; NULL = нужен вход
    enabled  INTEGER NOT NULL DEFAULT 1,
    photo    BLOB,                          -- маленький jpeg аватарки
    created  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS access (
    user_id    INTEGER NOT NULL,
    account_id INTEGER NOT NULL,
    role       TEXT    NOT NULL,            -- viewer | operator
    PRIMARY KEY (user_id, account_id)
);
CREATE TABLE IF NOT EXISTS prefs (
    user_id    INTEGER NOT NULL,
    account_id INTEGER NOT NULL,
    notify     INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (user_id, account_id)
);
CREATE TABLE IF NOT EXISTS muted (
    user_id    INTEGER NOT NULL,
    account_id INTEGER NOT NULL,
    chat_id    INTEGER NOT NULL,
    PRIMARY KEY (user_id, account_id, chat_id)
);
CREATE TABLE IF NOT EXISTS audit (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         INTEGER NOT NULL,
    user_id    INTEGER,
    account_id INTEGER,
    action     TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS first_contacts (   -- кому аккаунт написал первым (для суточного лимита)
    account_id INTEGER NOT NULL,
    peer_id    INTEGER NOT NULL,
    ts         INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS card_cache (
    key     TEXT PRIMARY KEY,
    file_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (           -- для статистики: кто что сделал, по видам
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         INTEGER NOT NULL,
    user_id    INTEGER,
    account_id INTEGER,
    kind       TEXT    NOT NULL                -- open · reply · media · first · delete · react · forward · read_all · notify
);
CREATE INDEX IF NOT EXISTS events_ts ON events (ts);
"""

# Колонки, добавленные после первой версии: на уже живой базе их докидывает _migrate
USER_COLUMNS = {
    "lang": "TEXT",                                  # ru | en; NULL — ещё не выбран (берём из Telegram)
    "pin": "TEXT",                                   # соль$хэш PBKDF2 или NULL
    "quiet": "INTEGER NOT NULL DEFAULT 0",           # тихие уведомления 23:00–08:00
    "groups": "INTEGER NOT NULL DEFAULT 0",          # уведомлять обо всех сообщениях в группах, а не только об упоминаниях
}

ACCOUNT_FIELDS = {"tg_id", "name", "username", "phone", "session", "enabled", "photo"}


class DB:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.c: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.c = await aiosqlite.connect(self.path)
        self.c.row_factory = aiosqlite.Row
        await self.c.executescript(SCHEMA)
        await self._migrate()
        await self.c.commit()

    async def _migrate(self) -> None:
        have = {r["name"] for r in await self._all("PRAGMA table_info(users)")}
        for col, ddl in USER_COLUMNS.items():
            if col not in have:
                await self.c.execute(f"ALTER TABLE users ADD COLUMN {col} {ddl}")

    async def close(self) -> None:
        if self.c:
            await self.c.close()

    async def _one(self, sql: str, *args) -> aiosqlite.Row | None:
        async with self.c.execute(sql, args) as cur:
            return await cur.fetchone()

    async def _all(self, sql: str, *args) -> list[aiosqlite.Row]:
        async with self.c.execute(sql, args) as cur:
            return list(await cur.fetchall())

    async def _exec(self, sql: str, *args) -> int:
        async with self.c.execute(sql, args) as cur:
            rowid = cur.lastrowid
        await self.c.commit()
        return rowid

    # ─── Пользователи ──────────────────────────────────────────────────────

    async def touch_user(self, uid: int, name: str, username: str | None) -> aiosqlite.Row:
        row = await self._one("SELECT * FROM users WHERE id = ?", uid)
        if row is None:
            await self._exec("INSERT INTO users (id, name, username, created) VALUES (?, ?, ?, ?)",
                             uid, name, username, int(time.time()))
        elif (row["name"], row["username"]) != (name, username):
            await self._exec("UPDATE users SET name = ?, username = ? WHERE id = ?", name, username, uid)
        else:
            return row
        return await self._one("SELECT * FROM users WHERE id = ?", uid)

    async def user(self, uid: int) -> aiosqlite.Row | None:
        return await self._one("SELECT * FROM users WHERE id = ?", uid)

    async def users(self) -> list[aiosqlite.Row]:
        return await self._all("SELECT * FROM users ORDER BY admin DESC, created")

    async def admins(self) -> set[int]:
        return {r["id"] for r in await self._all("SELECT id FROM users WHERE admin = 1")}

    async def set_admin(self, uid: int, admin: bool) -> None:
        await self._exec("UPDATE users SET admin = ? WHERE id = ?", int(admin), uid)

    async def set_banned(self, uid: int, banned: bool) -> None:
        await self._exec("UPDATE users SET banned = ? WHERE id = ?", int(banned), uid)

    async def set_user(self, uid: int, **fields) -> None:
        """Личные настройки: lang, pin, quiet, groups."""
        bad = set(fields) - set(USER_COLUMNS)
        if bad:
            raise ValueError(f"unknown user fields: {bad}")
        sets = ", ".join(f"{k} = ?" for k in fields)
        await self._exec(f"UPDATE users SET {sets} WHERE id = ?", *fields.values(), uid)

    async def audience(self, acc_id: int, admin_ids: set[int]) -> list[aiosqlite.Row]:
        """Кто видит аккаунт: админы и люди с ролью на него (без забаненных) — для уведомлений."""
        ids = ",".join(str(int(i)) for i in admin_ids) or "0"
        return await self._all(
            f"SELECT * FROM users WHERE banned = 0 AND (admin = 1 OR id IN ({ids}) OR id IN "
            "(SELECT user_id FROM access WHERE account_id = ?))", acc_id)

    # ─── Аккаунты ──────────────────────────────────────────────────────────

    async def accounts(self) -> list[aiosqlite.Row]:
        return await self._all("SELECT * FROM accounts ORDER BY id")

    async def account(self, acc_id: int) -> aiosqlite.Row | None:
        return await self._one("SELECT * FROM accounts WHERE id = ?", acc_id)

    async def add_account(self, tg_id: int, name: str, username: str | None, phone: bytes) -> int:
        return await self._exec("INSERT INTO accounts (tg_id, name, username, phone, created) VALUES (?, ?, ?, ?, ?)",
                                tg_id, name, username, phone, int(time.time()))

    async def update_account(self, acc_id: int, **fields) -> None:
        bad = set(fields) - ACCOUNT_FIELDS
        if bad:
            raise ValueError(f"unknown account fields: {bad}")
        sets = ", ".join(f"{k} = ?" for k in fields)
        await self._exec(f"UPDATE accounts SET {sets} WHERE id = ?", *fields.values(), acc_id)

    async def delete_account(self, acc_id: int) -> None:
        for table in ("access", "prefs", "muted"):
            await self.c.execute(f"DELETE FROM {table} WHERE account_id = ?", (acc_id,))
        await self._exec("DELETE FROM accounts WHERE id = ?", acc_id)

    # ─── Доступы ───────────────────────────────────────────────────────────

    async def roles(self, uid: int) -> dict[int, str]:
        return {r["account_id"]: r["role"] for r in await self._all("SELECT account_id, role FROM access WHERE user_id = ?", uid)}

    async def set_role(self, uid: int, acc_id: int, role: str | None) -> None:
        if role is None:
            await self._exec("DELETE FROM access WHERE user_id = ? AND account_id = ?", uid, acc_id)
        else:
            await self._exec("INSERT INTO access (user_id, account_id, role) VALUES (?, ?, ?) "
                             "ON CONFLICT (user_id, account_id) DO UPDATE SET role = excluded.role", uid, acc_id, role)

    async def access_to(self, acc_id: int) -> list[aiosqlite.Row]:
        return await self._all("SELECT u.id, u.name, u.username, a.role FROM access a JOIN users u ON u.id = a.user_id "
                               "WHERE a.account_id = ? AND u.banned = 0 ORDER BY u.created", acc_id)

    # ─── Уведомления и заглушённые чаты ────────────────────────────────────

    async def notify(self, uid: int, acc_id: int) -> bool:
        row = await self._one("SELECT notify FROM prefs WHERE user_id = ? AND account_id = ?", uid, acc_id)
        return True if row is None else bool(row["notify"])

    async def set_notify(self, uid: int, acc_id: int, on: bool) -> None:
        await self._exec("INSERT INTO prefs (user_id, account_id, notify) VALUES (?, ?, ?) "
                         "ON CONFLICT (user_id, account_id) DO UPDATE SET notify = excluded.notify", uid, acc_id, int(on))

    async def muted(self, uid: int, acc_id: int) -> set[int]:
        rows = await self._all("SELECT chat_id FROM muted WHERE user_id = ? AND account_id = ?", uid, acc_id)
        return {r["chat_id"] for r in rows}

    async def toggle_mute(self, uid: int, acc_id: int, chat_id: int) -> bool:
        """True — чат теперь заглушён."""
        if chat_id in await self.muted(uid, acc_id):
            await self._exec("DELETE FROM muted WHERE user_id = ? AND account_id = ? AND chat_id = ?", uid, acc_id, chat_id)
            return False
        await self._exec("INSERT INTO muted (user_id, account_id, chat_id) VALUES (?, ?, ?)", uid, acc_id, chat_id)
        return True

    # ─── Журнал ────────────────────────────────────────────────────────────

    async def log(self, uid: int | None, acc_id: int | None, action: str, **kw) -> None:
        """action — русский шаблон из интерфейса, kw — подстановки. Хранится JSON-ом, чтобы журнал
        показывался на языке того, кто его открыл (старые записи — простым текстом)."""
        if kw:
            action = json.dumps({"t": action, "kw": kw}, ensure_ascii=False)
        await self._exec("INSERT INTO audit (ts, user_id, account_id, action) VALUES (?, ?, ?, ?)",
                         int(time.time()), uid, acc_id, action)

    async def recent(self, n: int = 15) -> list[aiosqlite.Row]:
        return await self._all("SELECT a.ts, a.action, u.name FROM audit a LEFT JOIN users u ON u.id = a.user_id "
                               "ORDER BY a.id DESC LIMIT ?", n)

    # ─── События и статистика ──────────────────────────────────────────────

    async def event(self, uid: int | None, acc_id: int | None, kind: str) -> None:
        await self._exec("INSERT INTO events (ts, user_id, account_id, kind) VALUES (?, ?, ?, ?)",
                         int(time.time()), uid, acc_id, kind)

    async def stats_accounts(self, since: float) -> list[aiosqlite.Row]:
        return await self._all("SELECT account_id, kind, COUNT(*) AS n FROM events WHERE ts >= ? AND account_id IS NOT NULL "
                               "GROUP BY account_id, kind", int(since))

    async def stats_users(self, since: float) -> list[aiosqlite.Row]:
        return await self._all("SELECT e.user_id, u.name, COUNT(*) AS n, MAX(e.ts) AS last FROM events e "
                               "LEFT JOIN users u ON u.id = e.user_id WHERE e.ts >= ? AND e.user_id IS NOT NULL "
                               "AND e.kind != 'notify' GROUP BY e.user_id ORDER BY n DESC LIMIT 15", int(since))

    async def stats_kinds(self, since: float) -> dict[str, int]:
        rows = await self._all("SELECT kind, COUNT(*) AS n FROM events WHERE ts >= ? GROUP BY kind", int(since))
        return {r["kind"]: r["n"] for r in rows}

    # ─── Первые сообщения новым людям ──────────────────────────────────────

    async def add_first_contact(self, acc_id: int, peer_id: int) -> None:
        await self._exec("INSERT INTO first_contacts (account_id, peer_id, ts) VALUES (?, ?, ?)",
                         acc_id, peer_id, int(time.time()))

    async def first_contacts_since(self, acc_id: int, since: float) -> int:
        row = await self._one("SELECT COUNT(*) AS n FROM first_contacts WHERE account_id = ? AND ts >= ?", acc_id, int(since))
        return row["n"]

    # ─── Кэш карточек: хэш(шаблон + данные) → file_id ──────────────────────

    async def file_id(self, key: str) -> str | None:
        row = await self._one("SELECT file_id FROM card_cache WHERE key = ?", key)
        return row["file_id"] if row else None

    async def put_file_id(self, key: str, file_id: str) -> None:
        await self._exec("INSERT OR REPLACE INTO card_cache (key, file_id) VALUES (?, ?)", key, file_id)

    async def drop_file_id(self, key: str) -> None:
        await self._exec("DELETE FROM card_cache WHERE key = ?", key)
