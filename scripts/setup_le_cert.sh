#!/usr/bin/env bash
# Выпуск настоящего сертификата Let's Encrypt через DuckDNS (DNS-01: входящие порты не нужны)
# и подмена его в https-блоке nginx. Токен читается из ~/.duckdns-token и не печатается.
set -u
DOMAIN=funydub.duckdns.org
SERVER_IP=155.212.24.77
TOKEN_FILE="$HOME/.duckdns-token"

echo "== 1. токен и поддомен"
[ -s "$TOKEN_FILE" ] || { echo "  нет файла $TOKEN_FILE"; exit 1; }
echo "  токен прочитан ($(wc -c < "$TOKEN_FILE" | tr -d " ") байт, сам не печатаю)"

echo "== 2. прописываю домену IP сервера $SERVER_IP"
RESP=$(curl -s --max-time 30 "https://www.duckdns.org/update?domains=funydub&token=$(cat "$TOKEN_FILE")&ip=$SERVER_IP")
echo "  ответ DuckDNS: $RESP"

echo "== 3. ждём, пока домен начнёт резолвиться в наш сервер"
for i in $(seq 1 12); do
  IP=$(getent hosts "$DOMAIN" | awk '{print $1}' | head -1)
  [ "$IP" = "$SERVER_IP" ] && break
  sleep 5
done
if [ "$IP" = "$SERVER_IP" ]; then echo "  $DOMAIN -> $IP"; else echo "  домен резолвится в '${IP:-пусто}' — продолжаю, acme.sh проверит сам"; fi

echo "== 4. выпуск сертификата (Let's Encrypt, проверка через DNS)"
export DuckDNS_Token="$(cat "$TOKEN_FILE")"
~/acme.sh/acme.sh --issue --dns dns_duckdns -d "$DOMAIN" -d "*.$DOMAIN" \
  --server letsencrypt --keylength ec-256 --accountemail "admin@$DOMAIN" 2>&1 | tail -14

echo "== 5. ставлю сертификат для nginx (с автопродлением)"
sudo ~/acme.sh/acme.sh --install-cert -d "$DOMAIN" --ecc \
  --key-file /etc/nginx/ssl/dub.key \
  --fullchain-file /etc/nginx/ssl/dub.crt \
  --reloadcmd "systemctl reload nginx" 2>&1 | tail -4

echo "== 6. переключаю https-блок на сертификат Let's Encrypt"
sudo sed -i 's#/etc/nginx/ssl/dub-server.crt#/etc/nginx/ssl/dub.crt#; s#/etc/nginx/ssl/dub-server.key#/etc/nginx/ssl/dub.key#' /etc/nginx/sites-available/dub-https
sudo grep -nE "ssl_certificate" /etc/nginx/sites-available/dub-https | sed 's/^/  /'
if sudo nginx -t 2>&1 | tail -1 | grep -q successful; then
  sudo systemctl reload nginx && echo "  nginx перезагружен"
else
  echo "  ОШИБКА конфигурации — возвращаю прежние пути"
  sudo sed -i 's#/etc/nginx/ssl/dub.crt#/etc/nginx/ssl/dub-server.crt#; s#/etc/nginx/ssl/dub.key#/etc/nginx/ssl/dub-server.key#' /etc/nginx/sites-available/dub-https
  sudo nginx -t 2>&1 | tail -1 | sed 's/^/  /'
  exit 1
fi

echo "== 7. проверки"
echo "  --- что за сертификат отдаётся:"
echo | timeout 15 openssl s_client -connect 127.0.0.1:3001 -servername "$DOMAIN" 2>/dev/null \
  | openssl x509 -noout -issuer -subject -dates 2>/dev/null | sed 's/^/    /'
printf "  https %s:3001/dub/ (проверка цепочки, без -k) -> %s\n" "$DOMAIN" \
  "$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 --resolve "$DOMAIN:3001:127.0.0.1" "https://$DOMAIN:3001/dub/")"
printf "  https api/health -> %s\n" \
  "$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 --resolve "$DOMAIN:3001:127.0.0.1" "https://$DOMAIN:3001/dub/api/health")"
printf "  http  /dub/ (как было) -> %s\n" "$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 http://127.0.0.1:3001/dub/)"
printf "  http  /kacheck/ -> %s\n" "$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 http://127.0.0.1:3001/kacheck/)"
echo "  --- автопродление:"
crontab -l 2>/dev/null | grep -c acme | sed 's/^/    заданий в cron: /'
