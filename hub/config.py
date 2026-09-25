"""Настройки из .env. SESSION_KEY создаётся сам при первом запуске и дописывается в .env."""
import os
from dataclasses import dataclass
from datetime import timedelta, timezone
from pathlib import Path

from cryptography.fernet import Fernet

BASE = Path(__file__).parent
# В Docker всё изменяемое лежит в томе: HUB_ENV_FILE=/data/.env, DB_PATH=/data/hub.db, HUB_LOG_FILE=/data/hub.log
ENV = Path(os.environ.get("HUB_ENV_FILE") or BASE / ".env")
LOG_FILE = Path(os.environ.get("HUB_LOG_FILE") or BASE / "hub.log")


@dataclass(frozen=True)
class Config:
    bot_token: str
    api_id: int | None
    api_hash: str | None
    admin_ids: frozenset[int]
    session_key: str
    tz: timezone
    db_path: Path
    proxy: str | None = None  # socks5://127.0.0.1:10808 — если Telegram у провайдера заблокирован
    new_chats_per_day: int = 20  # сколько новых переписок аккаунт может начать за сутки; 0 — без лимита


def _read_env() -> None:
    if ENV.exists():
        for line in ENV.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def _session_key() -> str:
    key = os.environ.get("SESSION_KEY", "").strip()
    if key:
        return key
    key = Fernet.generate_key().decode()
    ENV.parent.mkdir(parents=True, exist_ok=True)
    with ENV.open("a", encoding="utf-8") as f:
        f.write("\n# Ключ шифрования сессий. Потеряешь — все аккаунты придётся перелогинить. В git и бэкапы не класть.\n"
                f"SESSION_KEY={key}\n")
    os.environ["SESSION_KEY"] = key
    return key


def load() -> Config:
    _read_env()
    api_id = os.environ.get("API_ID", "").strip()
    return Config(
        bot_token=os.environ.get("BOT_TOKEN", "").strip(),
        api_id=int(api_id) if api_id.isdigit() else None,
        api_hash=os.environ.get("API_HASH", "").strip() or None,
        admin_ids=frozenset(int(x) for x in os.environ.get("ADMIN_IDS", "").replace(",", " ").split() if x.isdigit()),
        session_key=_session_key(),
        tz=timezone(timedelta(hours=float(os.environ.get("TZ_OFFSET", "3")))),
        db_path=BASE / os.environ.get("DB_PATH", "hub.db"),
        proxy=os.environ.get("PROXY", "").strip() or None,
        new_chats_per_day=int(os.environ.get("NEW_CHATS_PER_DAY", "20").strip() or 20),
    )
