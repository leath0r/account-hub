"""Язык интерфейса. Ключ перевода — сама русская строка из кода; английский — в i18n_en.py.

Нет перевода → показываем русский и запоминаем строку в MISSING (стенд проверяет, что там пусто).
"""
from i18n_en import EN, EN_PL

LANGS = {"ru": "🇷🇺 Русский", "en": "🇬🇧 English"}
RU_FAMILY = {"ru", "uk", "be", "kk", "uz", "ky", "tg"}   # кому по умолчанию удобнее русский
MISSING: set[str] = set()


def detect(language_code: str | None) -> str:
    """Язык Telegram-клиента пользователя → язык бота по умолчанию."""
    return "ru" if (language_code or "ru").split("-")[0].lower() in RU_FAMILY else "en"


def ru_plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n) % 100
    if 10 < n < 20:
        return many
    if n % 10 == 1:
        return one
    if 2 <= n % 10 <= 4:
        return few
    return many


def tr(lang: str, text: str, /, **kw) -> str:
    out = text
    if lang == "en":
        out = EN.get(text)
        if out is None:
            MISSING.add(text)
            out = text
    return out.format(**kw) if kw else out


def pl(lang: str, n: int, one: str, few: str, many: str) -> str:
    """Слово при числе: pl(lang, 5, "аккаунт", "аккаунта", "аккаунтов")."""
    if lang == "en":
        pair = EN_PL.get(many)
        if pair is None:
            MISSING.add(many)
            return ru_plural(n, one, few, many)
        return pair[0] if n == 1 else pair[1]
    return ru_plural(n, one, few, many)
