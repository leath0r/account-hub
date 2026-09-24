# Account Hub

Telegram-бот — панель для нескольких своих Telegram-аккаунтов: чаты, группы, поиск, входящие, ответы от имени аккаунта, доступы для других людей. Бот — aiogram 3, аккаунты — Telethon, экраны — PNG-карточки из HTML (Playwright + Jinja2), без Mini App.

| Папка | Что |
|---|---|
| `hub/` | **рабочий бот** (v0.1+, в проде) |
| `hub/deploy/` | выкладка на сервер: systemd-юниты, `hubctl`, прокси до Telegram, `deploy.ps1` / `hub.ps1` |
| `hub/tests/` | стенд: фейковый Telegram + фейковый Bot API, сценарии и случайные нажатия |
| `demo/` | первое демо интерфейса на выдуманных данных |
| `design/` | макеты карточек |

## Быстрый старт

```bash
cd hub
pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env        # BOT_TOKEN, API_ID, API_HASH (my.telegram.org), ADMIN_IDS
python main.py              # на Windows карточки рисует Edge, на Linux — Chromium
```

Первый админ без `ADMIN_IDS`: в логе будет `/claim <код>` — отправить боту.

## Проверка

```bash
cd hub
python tests/run_test.py 1500   # ~20 с, сеть не нужна
```

## Сервер

Выкладка и управление — `hub/deploy/`: `deploy.ps1` (код → сервер → перезапуск), `hub.ps1 status|logs|restart|proxy set`.
Если у провайдера заблокирован Telegram — `hubctl proxy set` (hy2:// · vless:// · trojan://).

Секреты (`.env`), база с сессиями и логи в репозиторий не попадают — см. `.gitignore`.
