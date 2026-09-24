# Account Hub

Telegram-бот — панель для нескольких своих Telegram-аккаунтов: чаты, группы, поиск, входящие, ответы от имени аккаунта, доступы для других людей. Бот — aiogram 3, аккаунты — Telethon, экраны — PNG-карточки из HTML (Playwright + Jinja2), без Mini App.

![Account Hub — главные экраны](docs/screenshots/00-overview.png)

## Как выглядит

Один экран — одно сообщение: карточка, подпись и кнопки. Бот не спамит чат, а перерисовывает это сообщение.

| | |
|:---:|:---:|
| **Главная — выбор аккаунта** | **Меню аккаунта** |
| <img src="docs/screenshots/01-home.png" width="360"> | <img src="docs/screenshots/02-account.png" width="360"> |
| **Чаты аккаунта** | **Переписка: ответ, стикеры и фото, удаление** |
| <img src="docs/screenshots/03-chats.png" width="360"> | <img src="docs/screenshots/04-dialog.png" width="360"> |
| **Удаление сообщения** | **Поиск по @username → написать первым** |
| <img src="docs/screenshots/05-delete.png" width="360"> | <img src="docs/screenshots/06-write-first.png" width="360"> |
| **Добавление аккаунта: код кнопками** | **Входящие со всех аккаунтов** |
| <img src="docs/screenshots/07-add-account.png" width="360"> | <img src="docs/screenshots/08-inbox.png" width="360"> |
| **Админ-панель** | **Доступы пользователя** |
| <img src="docs/screenshots/09-admin.png" width="360"> | <img src="docs/screenshots/10-access.png" width="360"> |

> Скриншоты сняты с настоящего кода бота на тестовом стенде — аккаунты и переписки выдуманные. Пересоздать: `cd hub && python tests/screenshots.py`.

| Папка | Что |
|---|---|
| `hub/` | **рабочий бот** (v0.1+, в проде) |
| `hub/deploy/` | выкладка на сервер: systemd-юниты, `hubctl`, прокси до Telegram, `deploy.ps1` / `hub.ps1` |
| `hub/tests/` | стенд: фейковый Telegram + фейковый Bot API, сценарии и случайные нажатия; `screenshots.py` — картинки для README |
| `docs/screenshots/` | скриншоты экранов |
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
