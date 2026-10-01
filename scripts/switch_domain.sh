#!/usr/bin/env bash
# Переключение основного домена: прописать IP, выпустить сертификат на оба имени, поставить в nginx.
# Оба имени в одном сертификате — чтобы уже выданные ссылки продолжали открываться без предупреждений.
set -u
PRIMARY=egrigorich.duckdns.org
LEGACY=funydub.duckdns.org
SERVER_IP=155.212.24.77
TOKEN_FILE="$HOME/.duckdns-token"

[ -s "$TOKEN_FILE" ] || { echo "нет файла токена"; exit 1; }
TOKEN="$(cat "$TOKEN_FILE")"

echo "== 1. прописываю домену IP $SERVER_IP"
SHORT="${PRIMARY%%.*}"
RESP=$(curl -s --max-time 30 "https://www.duckdns.org/update?domains=$SHORT&token=$TOKEN&ip=$SERVER_IP")
echo "  ответ DuckDNS: $RESP"
[ "$RESP" = "OK" ] || { echo "  DuckDNS не принял домен — проверьте, что он добавлен в панели"; exit 1; }

echo "== 2. жду, пока домен начнёт резолвиться в сервер"
for i in $(seq 1 24); do
  IP=$(getent hosts "$PRIMARY" | awk '{print $1}' | head -1)
  [ "$IP" = "$SERVER_IP" ] && break
  sleep 5
done
echo "  $PRIMARY -> ${IP:-не резолвится}"

echo "== 3. выпускаю сертификат на два имени"
export DuckDNS_Token="$TOKEN"
timeout 600 ~/acme.sh/acme.sh --issue --dns dns_duckdns \
  -d "$PRIMARY" -d "$LEGACY" \
  --server letsencrypt --keylength ec-256 --dnssleep 30 > ~/acme_switch.log 2>&1
CODE=$?
if [ "$CODE" != "0" ]; then echo "  выпуск не удался (код $CODE), последние строки:"; tail -8 ~/acme_switch.log | sed 's/^/    /'; exit 1; fi
grep -E "Cert success|Your cert is in" ~/acme_switch.log | tail -2 | sed 's/^/  /'

echo "== 4. ставлю сертификат для nginx (автопродление уже настроено)"
sudo env HOME=/home/egor /home/egor/acme.sh/acme.sh --install-cert -d "$PRIMARY" --ecc \
  --key-file /etc/nginx/ssl/dub.key --fullchain-file /etc/nginx/ssl/dub.crt \
  --reloadcmd "systemctl reload nginx" 2>&1 | tail -2 | sed 's/^/  /'

echo "== 5. проверки"
sudo nginx -t 2>&1 | tail -1 | sed 's/^/  nginx: /'
sudo openssl x509 -in /etc/nginx/ssl/dub.crt -noout -subject -enddate -ext subjectAltName | sed 's/^/  /'
for D in "$PRIMARY" "$LEGACY"; do
  printf "  https://%s:3001/dub/ -> %s\n" "$D" \
    "$(curl -s -o /dev/null -w '%{http_code}' --max-time 25 "https://$D:3001/dub/")"
  printf "  https://%s:3001/dub/api/health -> %s\n" "$D" \
    "$(curl -s -o /dev/null -w '%{http_code}' --max-time 25 "https://$D:3001/dub/api/health")"
done
printf "  http  /dub/ (как было) -> %s\n" "$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 http://127.0.0.1:3001/dub/)"
printf "  http  /kacheck/        -> %s\n" "$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 http://127.0.0.1:3001/kacheck/)"
