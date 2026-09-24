"""Ссылка своего VPN → конфиг локального SOCKS5 127.0.0.1:10808, через который бот ходит в Telegram.

Нужен, потому что у домашнего провайдера Telegram заблокирован. Ходит через прокси только бот.
    vless:// · trojan://  → config.json для xray
    hy2:// · hysteria2:// → hysteria.yaml для официального клиента Hysteria2
    printf '%s' "<ссылка>" | python proxy_link.py <папка конфига>
"""
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlencode, urlsplit, urlunsplit

PORT = 10808
XRAY_CONF = "config.json"
HY2_CONF = "hysteria.yaml"


def stream(q: dict, address: str) -> dict:
    def g(key: str, default: str = "") -> str:
        return q.get(key, [default])[0]

    net = {"splithttp": "xhttp", "raw": "tcp"}.get(g("type", "tcp"), g("type", "tcp"))
    sec = g("security", "none")
    s: dict = {"network": net, "security": sec}
    if sec == "tls":
        s["tlsSettings"] = {"serverName": g("sni") or g("host") or address, "fingerprint": g("fp", "chrome")}
        if g("alpn"):
            s["tlsSettings"]["alpn"] = g("alpn").split(",")
        if g("allowInsecure") in ("1", "true"):
            s["tlsSettings"]["allowInsecure"] = True
    elif sec == "reality":
        s["realitySettings"] = {"serverName": g("sni"), "fingerprint": g("fp", "chrome"), "publicKey": g("pbk"),
                                "shortId": g("sid"), "spiderX": g("spx")}
    path, host = g("path", "/"), g("host")
    if net == "ws":
        s["wsSettings"] = {"path": path, "host": host}
    elif net == "grpc":
        s["grpcSettings"] = {"serviceName": g("serviceName"), "multiMode": g("mode") == "multi"}
    elif net == "xhttp":
        s["xhttpSettings"] = {"path": path, "host": host, "mode": g("mode", "auto")}
    elif net == "httpupgrade":
        s["httpupgradeSettings"] = {"path": path, "host": host}
    elif net == "tcp" and g("headerType") == "http":
        s["tcpSettings"] = {"header": {"type": "http", "request": {"path": [path], "headers": {"Host": [host]}}}}
    return s


def outbound(link: str) -> dict:
    u = urlsplit(link.strip())
    q = parse_qs(u.query)
    if u.scheme not in ("vless", "trojan"):
        raise SystemExit("Поддерживаются ссылки vless://, trojan:// и hy2:// "
                         "(подписка https://… не подойдёт — возьми одну ссылку из неё)")
    if not u.hostname or not u.username:
        raise SystemExit("Не похоже на ссылку: нужен формат vless://…@host:port?… или trojan://…@host:port?…")
    address, port, cred = u.hostname, u.port or 443, unquote(u.username)
    if u.scheme == "vless":
        user = {"id": cred, "encryption": q.get("encryption", ["none"])[0]}
        if q.get("flow", [""])[0]:
            user["flow"] = q["flow"][0]
        settings = {"vnext": [{"address": address, "port": port, "users": [user]}]}
    else:
        settings = {"servers": [{"address": address, "port": port, "password": cred}]}
    return {"tag": "vpn", "protocol": u.scheme, "settings": settings, "streamSettings": stream(q, address)}


def main() -> None:
    link = sys.stdin.read().strip()
    out = Path(sys.argv[1])
    for name in (XRAY_CONF, HY2_CONF):
        (out / name).unlink(missing_ok=True)
    u = urlsplit(link)
    if u.scheme in ("hy2", "hysteria2"):
        if not u.hostname or not u.port:
            raise SystemExit("Не похоже на ссылку: нужен формат hy2://пароль@host:port?…")
        q = parse_qs(u.query, keep_blank_values=True)
        note = ""
        if q.get("pinSHA256", [""])[0] and q.get("insecure", ["0"])[0] != "1":
            # Сертификат по IP (без IP SAN) или самоподписанный: при insecure=0 клиент сперва делает обычную
            # проверку и падает раньше, чем дойдёт до отпечатка. insecure=1 отключает только её — отпечаток
            # pinSHA256 сверяется всё равно, это даже строже проверки через CA.
            q["insecure"] = ["1"]
            link = urlunsplit(u._replace(query=urlencode(q, doseq=True)))
            note = " (сертификат проверяется по отпечатку pinSHA256)"
        # официальный клиент Hysteria2 понимает ссылку целиком (auth, tls, pinSHA256, obfs)
        (out / HY2_CONF).write_text(f"server: {json.dumps(link)}\nsocks5:\n  listen: 127.0.0.1:{PORT}\n", encoding="utf-8")
        print(f"Конфиг записан: hysteria2 -> {u.hostname}:{u.port}{note}")
        return
    config = {
        "log": {"loglevel": "warning"},
        "inbounds": [{"tag": "socks", "listen": "127.0.0.1", "port": PORT, "protocol": "socks", "settings": {"udp": False}}],
        "outbounds": [outbound(link), {"tag": "direct", "protocol": "freedom"}],
    }
    (out / XRAY_CONF).write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    ob = config["outbounds"][0]
    s = ob["streamSettings"]
    print(f"Конфиг записан: {ob['protocol']} / {s['network']} / {s['security']} -> "
          f"{(ob['settings'].get('vnext') or ob['settings'].get('servers'))[0]['address']}")


if __name__ == "__main__":
    main()
