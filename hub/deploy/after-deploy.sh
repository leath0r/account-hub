#!/usr/bin/env bash
# Запускается на сервере после каждой выкладки (от пользователя, без sudo).
# venv и зависимости → Chromium → hubctl → юниты systemd --user → перезапуск.
set -euo pipefail
APP="$HOME/accounthub"
cd "$APP"
[ -f .env ] && chmod 600 .env

if [ ! -x venv/bin/python ]; then
  if ! python3 -m venv venv 2>/dev/null; then
    rm -rf venv
    echo "!!! Нет python3-venv. Один раз под sudo:  sudo bash ~/accounthub/deploy/root-setup.sh"
    exit 2
  fi
fi

req_hash=$(sha1sum requirements.txt | cut -d' ' -f1)
if [ "$(cat venv/.req 2>/dev/null)" != "$req_hash" ]; then
  echo "── зависимости"
  venv/bin/pip install -q -U pip
  venv/bin/pip install -q -r requirements.txt
  echo "$req_hash" > venv/.req
fi
# Chromium: cdn.playwright.dev у провайдера не открывается — если не скачался, его докладывают с ПК (см. заметку)
venv/bin/python -m playwright install --only-shell chromium >/dev/null 2>&1 || \
  ls "$HOME"/.cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell >/dev/null 2>&1 || \
  echo "!!! Нет Chromium для карточек: cdn.playwright.dev недоступен — положить вручную (заметка → «Chromium»)"

XRAY="$HOME/.local/opt/xray/xray"
if [ ! -x "$XRAY" ]; then
  echo "── xray (клиент прокси до своего VPN)"
  mkdir -p "$(dirname "$XRAY")"
  if curl -fsSL -m 180 -o /tmp/xray.zip https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-64.zip; then
    venv/bin/python -m zipfile -e /tmp/xray.zip "$(dirname "$XRAY")" && rm -f /tmp/xray.zip && chmod +x "$XRAY"
  else
    echo "!!! xray не скачался с GitHub — положить вручную в $XRAY"
  fi
fi

mkdir -p "$HOME/.local/bin" "$HOME/.config/systemd/user"
install -m 755 deploy/hubctl "$HOME/.local/bin/hubctl"
cp deploy/accounthub.service deploy/accounthub-proxy.service deploy/accounthub-backup.service deploy/accounthub-backup.timer \
   "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user enable accounthub.service accounthub-proxy.service accounthub-backup.timer >/dev/null 2>&1
systemctl --user start accounthub-backup.timer
# прокси не дёргаем, если он уже работает: перезапуск рвёт связь бота на пару секунд
if [ -n "$(ls -A "$HOME/.config/accounthub-proxy" 2>/dev/null)" ] && ! systemctl --user is-active --quiet accounthub-proxy.service; then
  systemctl --user restart accounthub-proxy.service
fi

if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != "yes" ]; then
  # при открытой локальной сессии (tty1) polkit разрешает включить linger себе без sudo
  loginctl enable-linger 2>/dev/null || \
    echo "!!! Автозапуск после перезагрузки не включён. Один раз:  sudo bash ~/accounthub/deploy/root-setup.sh"
fi

if ! grep -q '^PROXY=' .env && ! curl -s -m 8 -o /dev/null https://api.telegram.org/; then
  echo "!!! Telegram с сервера недоступен, а прокси не настроен — бот не запускаю."
  echo "    Настроить:  ssh -t qwe@<сервер> '~/.local/bin/hubctl proxy set'"
  exit 0
fi
systemctl --user restart accounthub.service
sleep 4
echo "── accounthub: $(systemctl --user is-active accounthub.service || true)"
tail -n 6 hub.log 2>/dev/null || true
