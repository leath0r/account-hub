"""HTML-шаблон → PNG-карточка. Один браузер (Edge через Playwright) на весь процесс."""
import asyncio
import base64
import hashlib
import os
import re
import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from playwright.async_api import async_playwright

BASE = Path(__file__).parent
TEMPLATES = BASE / "templates"
FONTS = BASE / "fonts"


def _inline_fonts() -> str:
    """Шрифты дизайна (Unbounded, Manrope) прямо в CSS — кириллица и латиница."""
    css = (FONTS / "fonts.css").read_text(encoding="utf-8")
    blocks = []
    for block in re.findall(r"@font-face\s*\{[^}]*\}", css):
        m = re.search(r'url\("fonts/([^"]+)"\)', block)
        if not m or not re.search(r"-400-(cyrillic|latin)\.woff2$", m.group(1)):
            continue
        data = base64.b64encode((FONTS / m.group(1)).read_bytes()).decode()
        block = block.replace(m.group(0), f"url(data:font/woff2;base64,{data})")
        blocks.append(re.sub(r"font-weight:\s*\d+;", "font-weight: 100 900;", block))
    return "\n".join(blocks)


class Renderer:
    def __init__(self) -> None:
        self.env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=True)
        self.head = _inline_fonts() + (TEMPLATES / "base.css").read_text(encoding="utf-8")
        self.cache: dict[str, bytes] = {}
        self.lock = asyncio.Lock()

    async def start(self) -> None:
        # Windows — установленный Edge; Linux-сервер — Chromium из `playwright install chromium`
        channel = os.environ.get("BROWSER_CHANNEL", "msedge" if sys.platform == "win32" else "")
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(channel=channel or None)
        self.page = await self.browser.new_page(viewport={"width": 1280, "height": 640})

    async def stop(self) -> None:
        await self.browser.close()
        await self.pw.stop()

    def prepare(self, template: str, **ctx) -> tuple[str, str]:
        """(ключ, html). Ключ — хэш итогового HTML: по нему ищется готовый file_id."""
        body = self.env.get_template(template).render(**ctx)
        html = f"<!DOCTYPE html><html lang='ru'><head><meta charset='utf-8'><style>{self.head}</style></head><body>{body}</body></html>"
        return hashlib.sha1(html.encode()).hexdigest(), html

    async def shot(self, key: str, html: str, size: tuple[int, int] = (1280, 640)) -> bytes:
        if key in self.cache:
            return self.cache[key]
        async with self.lock:
            w, h = size
            await self.page.set_viewport_size({"width": w, "height": h})
            await self.page.set_content(html, wait_until="load")
            await self.page.evaluate("document.fonts.ready.then(() => true)")
            png = await self.page.screenshot(type="png", clip={"x": 0, "y": 0, "width": w, "height": h})
        if len(self.cache) > 150:
            self.cache.clear()
        self.cache[key] = png
        return png

    async def render(self, template: str, size: tuple[int, int] = (1280, 640), **ctx) -> tuple[str, bytes]:
        key, html = self.prepare(template, **ctx)
        return key, await self.shot(key, html, size)
