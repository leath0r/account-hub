#!/usr/bin/env bash
# ЗАПАСНОЙ вариант. На домашнем сервере 2026-09-24 оба пункта сделаны без root:
#   linger — `loginctl enable-linger` (polkit разрешает себе при открытой сессии на tty1),
#   emoji — `apt-get download fonts-noto-color-emoji` + `dpkg-deb -x` в ~/.local/share/fonts.
# Разовая настройка под root:  sudo bash ~/accounthub/deploy/root-setup.sh
# 1) linger — службы пользователя живут без открытой ssh-сессии и стартуют при загрузке сервера (без этого бот
#    остановится, как только все выйдут из ssh);  2) цветной emoji-шрифт — для имён с эмодзи на карточках.
set -euo pipefail
USER_NAME="${SUDO_USER:?запускать через sudo от своего пользователя}"

loginctl enable-linger "$USER_NAME"
echo "OK: linger для $USER_NAME включён."

if ! fc-list 2>/dev/null | grep -qi "Noto Color Emoji"; then
  apt-get install -y -q fonts-noto-color-emoji || echo "(emoji-шрифт не поставился — не критично)"
fi
echo "Готово. Проверка:  loginctl show-user $USER_NAME -p Linger"
