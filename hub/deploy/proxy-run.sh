#!/usr/bin/env bash
# Запускает клиент прокси под тот конфиг, что лежит в ~/.config/accounthub-proxy: Hysteria2 или xray.
D="$HOME/.config/accounthub-proxy"
if [ -f "$D/hysteria.yaml" ]; then
  exec "$HOME/.local/opt/hysteria/hysteria" client -c "$D/hysteria.yaml" --disable-update-check
elif [ -f "$D/config.json" ]; then
  exec "$HOME/.local/opt/xray/xray" run -c "$D/config.json"
fi
echo "Нет конфига прокси — hubctl proxy set" >&2
exit 1
