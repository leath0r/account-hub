"""Скриншоты для README: настоящий код бота на стенде (фейковый Telegram, выдуманные аккаунты) → PNG «как в Telegram».

Запуск из папки hub:  python tests/screenshots.py      → ../docs/screenshots/*.png
"""
import asyncio
import base64
import html
import sys
import tempfile
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parent.parent), str(Path(__file__).parent)]

from aiogram.types import BufferedInputFile, Message  # noqa: E402
from cryptography.fernet import Fernet  # noqa: E402

import access  # noqa: E402
from fakes import FakeBotSession, FakeWorld  # noqa: E402
from render import Renderer, _inline_fonts  # noqa: E402
from run_test import ADMIN, BOB, Stand, keypad  # noqa: E402

OUT = Path(__file__).resolve().parents[2] / "docs" / "screenshots"

LOGO = ('<svg width="22" height="22" viewBox="0 0 120 120" fill="none"><circle cx="60" cy="60" r="46" stroke="#7C5CFC" '
        'stroke-width="8"/><circle cx="60" cy="60" r="30" stroke="#2DD4BF" stroke-width="8"/>'
        '<circle cx="60" cy="60" r="10" fill="#F4F5F7"/></svg>')

CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0B0D11;font-family:'Manrope','Segoe UI Emoji','Noto Color Emoji','Segoe UI',sans-serif;color:#F4F5F7;padding:28px;width:468px}
.cap{font-weight:800;font-size:13px;letter-spacing:2.5px;text-transform:uppercase;color:#2DD4BF;margin:0 0 14px 4px;max-width:410px;min-height:36px;line-height:18px}
.phone{width:410px;min-height:780px;border-radius:34px;background:#0E1621;border:1px solid #232732;overflow:hidden;display:flex;flex-direction:column}
.top{height:62px;flex:none;display:flex;align-items:center;gap:12px;padding:0 16px;background:#17212B;border-bottom:1px solid #0B1219}
.top .ava{width:38px;height:38px;border-radius:50%;background:#14161C;border:1px solid #232732;display:flex;align-items:center;justify-content:center}
.top .n{font-weight:700;font-size:15px}.top .s{font-weight:500;font-size:12px;color:#6D7F8F}
.chat{flex:1;padding:16px 12px;display:flex;flex-direction:column;justify-content:flex-end;gap:6px}
.msg{background:#182533;border-radius:16px 16px 16px 6px;overflow:hidden;max-width:386px}
.msg img{display:block;width:386px;height:193px}
.msg .t{padding:10px 13px 11px;font-size:13.5px;line-height:1.45;color:#E9EDF1;white-space:pre-wrap;word-wrap:break-word}
.msg .t b{font-weight:700}.msg .t i{color:#9FB0BF}
.msg .t code{font-family:Consolas,monospace;font-size:12.5px;color:#7FD8F7}
.kb{display:flex;flex-direction:column;gap:5px;max-width:386px}
.row{display:flex;gap:5px}
.b{flex:1;min-width:0;height:40px;border-radius:10px;background:rgba(24,37,51,.92);display:flex;align-items:center;justify-content:center;
   font-weight:600;font-size:13px;color:#F4F5F7;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;padding:0 8px}
.b.primary{background:#3E7FE0}.b.success{background:#2F9E5B}.b.danger{background:#C9434B}
.input{height:52px;flex:none;background:#17212B;display:flex;align-items:center;padding:0 16px;color:#6D7F8F;font-size:14px;border-top:1px solid #0B1219}
"""


class ShotSession(FakeBotSession):
    """Как FakeBotSession, но запоминает картинку карточки текущего экрана."""

    def __init__(self) -> None:
        super().__init__()
        self.image: dict[int, bytes | None] = {}

    async def make_request(self, bot, method, timeout=None):
        name = type(method).__name__
        src = method.photo if name == "SendPhoto" else method.media.media if name == "EditMessageMedia" else None
        res = await super().make_request(bot, method, timeout)
        if isinstance(src, BufferedInputFile) and isinstance(res, Message) and res.photo:
            self.image[method.chat_id] = src.data
        elif name in ("SendMessage", "EditMessageText"):
            self.image[method.chat_id] = None
        return res


def phone(st: Stand, uid: int, caption: str) -> str:
    s = st.screen(uid)
    img = st.session.image.get(uid)
    pic = f'<img src="data:image/png;base64,{base64.b64encode(img).decode()}">' if img else ""
    rows = []
    for row in (s.markup.inline_keyboard if s.markup else []):
        cells = "".join(f'<div class="b {b.style or ""}">{html.escape(b.text)}</div>' for b in row)
        rows.append(f'<div class="row">{cells}</div>')
    return (f'<div class="cap">{html.escape(caption)}</div><div class="phone">'
            f'<div class="top"><div class="ava">{LOGO}</div><div><div class="n">Account Hub</div><div class="s">бот</div></div></div>'
            f'<div class="chat"><div class="msg">{pic}<div class="t">{s.text or ""}</div></div>'
            f'<div class="kb">{"".join(rows)}</div></div><div class="input">Сообщение</div></div>')


async def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    OUT.mkdir(parents=True, exist_ok=True)
    renderer = Renderer()
    await renderer.start()
    page = await renderer.browser.new_page(device_scale_factor=2, viewport={"width": 520, "height": 900})
    fonts = _inline_fonts()
    shots: list[tuple[str, str]] = []

    async def shot(uid: int, name: str, caption: str) -> None:
        body = phone(st, uid, caption)
        await page.set_content(f"<!DOCTYPE html><html><head><meta charset='utf-8'><style>{fonts}{CSS}</style></head>"
                               f"<body>{body}</body></html>", wait_until="load")
        await page.evaluate("document.fonts.ready.then(() => true)")
        await page.locator("body").screenshot(path=str(OUT / f"{name}.png"))
        shots.append((name, caption))
        print("  ✓", name)

    tmp = Path(tempfile.mkdtemp(prefix="hubshots-"))
    st = await Stand().boot(renderer, FakeWorld(), Fernet.generate_key().decode(), tmp / "hub.db", session=ShotSession())
    # кэш file_id выключаем — каждая карточка приходит картинкой, её и снимаем
    st.ui.file_ids = type("NoCache", (dict,), {"__setitem__": lambda *a: None})()

    async def no_file_id(key: str) -> None:
        return None
    st.db.file_id = no_file_id
    try:
        # подготовка: админ, два аккаунта, второй пользователь с доступом
        await st.text(ADMIN, f"/claim {access.CLAIM['token']}")
        for phone_no, pwd in (("+79140000001", None), ("+79140000002", "secret-2fa")):
            await st.press(ADMIN, "add")
            await st.text(ADMIN, phone_no)
            if phone_no.endswith("1"):
                for d in "123":
                    await st.press(ADMIN, f"kp:{d}")
                await shot(ADMIN, "07-add-account", "Админ · добавление аккаунта: код кнопками")
                for d in "45":
                    await st.press(ADMIN, f"kp:{d}")
                await st.press(ADMIN, "kp:ok")
            else:
                await keypad(st, ADMIN, "12345")
                await st.text(ADMIN, pwd)
        await st.text(BOB, "/start")
        await st.press(ADMIN, f"ar:{BOB}:1")
        await st.press(ADMIN, f"ar:{BOB}:1")

        await st.text(ADMIN, "/start")
        await shot(ADMIN, "01-home", "Главная — /start")
        await st.press(ADMIN, "acc:1")
        await shot(ADMIN, "02-account", "Меню аккаунта")
        await st.press(ADMIN, "ls:1:c:0")
        await shot(ADMIN, "03-chats", "Чаты аккаунта")
        await st.press(ADMIN, label="Мама")
        await shot(ADMIN, "04-dialog", "Переписка: ответ, стикеры и фото, удаление")
        await st.press(ADMIN, label="Удалить…")
        await shot(ADMIN, "05-delete", "Удаление сообщения")
        await st.press(ADMIN, "srch:1")
        await st.text(ADMIN, "@friend_new")
        await shot(ADMIN, "06-write-first", "Поиск по @username → написать первым")
        await st.press(ADMIN, "inbox:0")
        await shot(ADMIN, "08-inbox", "Входящие со всех аккаунтов")
        await st.press(ADMIN, "adm")
        await shot(ADMIN, "09-admin", "Админ-панель")
        await st.press(ADMIN, f"au:{BOB}")
        await shot(ADMIN, "10-access", "Доступы пользователя")
    finally:
        await st.stop()

    # обзорная картинка для шапки README: 4 экрана в ряд
    row = "".join(f'<div style="display:inline-block;vertical-align:top;margin-right:12px"><img style="width:440px" src="{n}.png"></div>'
                  for n in ("01-home", "02-account", "04-dialog", "07-add-account"))
    overview = OUT / "_overview.html"
    overview.write_text(f"<html><body style='margin:0;background:#0B0D11;white-space:nowrap;display:inline-block;"
                        f"padding:8px'>{row}</body></html>", encoding="utf-8")
    await page.set_viewport_size({"width": 1900, "height": 1000})
    await page.goto(overview.as_uri())
    await page.locator("body").screenshot(path=str(OUT / "00-overview.png"))
    overview.unlink()
    await page.close()
    await renderer.stop()
    print(f"готово: {len(shots) + 1} картинок → {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
