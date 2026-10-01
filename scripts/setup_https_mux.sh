#!/usr/bin/env bash
# Порт 3001 обслуживает и http, и https: nginx смотрит на первый байт соединения (ssl_preread)
# и разводит трафик — TLS идёт на https-блок, обычный http остаётся как был.
#
# Зачем: микрофон браузер даёт только в защищённом контексте, а на 3001 живут ещё kacheck,
# ComfyUI (/imagegen/) и их клиенты ходят по http. Так https появляется, а старое не ломается.
set -u
cd /etc/nginx

backup() { sudo cp -a "$1" "$1.bak-$(date +%F-%H%M%S)"; }
echo "== 1. бэкапы"
backup /etc/nginx/nginx.conf
backup /etc/nginx/sites-available/kachek
backup /etc/nginx/sites-available/zz-comfyui-ui

echo "== 2. включаю stream в главном контексте"
sudo mkdir -p /etc/nginx/stream-enabled
if ! sudo grep -q "stream-enabled" /etc/nginx/nginx.conf; then
  sudo python3 - <<'PYINC'
import pathlib
p = pathlib.Path('/etc/nginx/nginx.conf')
t = p.read_text(encoding='utf-8')
anchor = 'include /etc/nginx/modules-enabled/*.conf;'
add = anchor + '
# Мультиплексор порта 3001: http и https на одном порту (см. stream-enabled/)
include /etc/nginx/stream-enabled/*.conf;'
p.write_text(t.replace(anchor, add, 1), encoding='utf-8')
print('  include добавлен в главный контекст')
PYINC
fi
tail -3 /etc/nginx/nginx.conf | sed 's/^/  /'

echo "== 3. обычный http переезжает на внутренний порт 3080 (реальный IP через PROXY-протокол)"
sudo python3 - <<'PY'
import pathlib
for name in ('kachek', 'zz-comfyui-ui'):
    p = pathlib.Path(f'/etc/nginx/sites-available/{name}')
    t = p.read_text(encoding='utf-8')
    t = t.replace('listen 3001 default_server;', 'listen 127.0.0.1:3080 proxy_protocol default_server;\n    real_ip_header proxy_protocol;\n    set_real_ip_from 127.0.0.1;')
    t = t.replace('listen [::]:3001 default_server;', 'listen [::1]:3080 proxy_protocol default_server;')
    t = t.replace('listen 3001;', 'listen 127.0.0.1:3080 proxy_protocol;\n    real_ip_header proxy_protocol;\n    set_real_ip_from 127.0.0.1;')
    t = t.replace('listen [::]:3001;', 'listen [::1]:3080 proxy_protocol;')
    p.write_text(t, encoding='utf-8')
    print(f'  {name}: слушает 127.0.0.1:3080')
PY

echo "== 4. https-блок на внутреннем 3443"
sudo tee /etc/nginx/sites-available/dub-https >/dev/null <<'CONF'
# HTTPS для порта 3001: сюда попадает трафик с TLS (разводит stream-мультиплексор).
# Сертификат сначала свой (dub-server), после выпуска Let's Encrypt пути подменяются на него.
server {
    listen 127.0.0.1:3443 ssl proxy_protocol;
    server_name _;

    ssl_certificate     /etc/nginx/ssl/dub-server.crt;
    ssl_certificate_key /etc/nginx/ssl/dub-server.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_cache shared:dub_tls:5m;

    real_ip_header proxy_protocol;
    set_real_ip_from 127.0.0.1;

    client_max_body_size 2048m;

    location = / { return 302 /dub/; }

    location ^~ /dub/ {
        proxy_pass http://127.0.0.1:8090/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade           $http_upgrade;
        proxy_set_header Connection        $http_upgrade;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        client_max_body_size 2048m;
        proxy_request_buffering off;
        proxy_buffering off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }

    # Соседние проекты по https тоже работают (у kacheck только относительные пути)
    location /kacheck/ {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 3600s;
        proxy_buffering off;
    }
    location = /kacheck { return 301 /kacheck/; }

    location / { return 404; }
}
CONF
sudo ln -sf /etc/nginx/sites-available/dub-https /etc/nginx/sites-enabled/dub-https
sudo rm -f /etc/nginx/sites-enabled/dub-https-8443 2>/dev/null

echo "== 5. мультиплексор"
sudo tee /etc/nginx/stream-enabled/dub-mux.conf >/dev/null <<'CONF'
# Порт 3001 обслуживает и http, и https: первый байт соединения отличает TLS (0x16) от обычного
# http, по нему и разводим трафик. Так https (озвучка с микрофоном) живёт на том же порту, что и
# привычный http (kacheck и ComfyUI API).
stream {
map $ssl_preread_protocol $dub_backend {
    ""      127.0.0.1:3080;   # не TLS — как было
    default 127.0.0.1:3443;   # TLS — https-блок
}

server {
    listen 3001;
    listen [::]:3001;
    ssl_preread on;
    proxy_pass $dub_backend;
    proxy_protocol on;        # чтобы бэкенды видели реальный адрес клиента
}
}
CONF

echo "== 6. проверка конфигурации"
if sudo nginx -t 2>&1 | tail -1 | grep -q successful; then
  sudo systemctl reload nginx && echo "  nginx перезагружен"
else
  sudo nginx -t 2>&1 | tail -3 | sed 's/^/  /'
  echo "  ОШИБКА: откатываю конфиги"
  for f in /etc/nginx/nginx.conf /etc/nginx/sites-available/kachek /etc/nginx/sites-available/zz-comfyui-ui; do
    last=$(ls -1t "$f".bak-* 2>/dev/null | head -1)
    [ -n "$last" ] && sudo cp -a "$last" "$f"
  done
  sudo rm -f /etc/nginx/sites-enabled/dub-https /etc/nginx/stream-enabled/dub-mux.conf
  sudo nginx -t 2>&1 | tail -1 | sed 's/^/  /'
  exit 1
fi

echo "== 7. проверки"
printf "  http  /dub/          -> %s\n" "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:3001/dub/)"
printf "  http  /kacheck/      -> %s\n" "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:3001/kacheck/)"
printf "  http  /imagegen/     -> %s\n" "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:3001/imagegen/)"
printf "  https /dub/          -> %s\n" "$(curl -s -o /dev/null -w '%{http_code}' --cacert /etc/nginx/ssl/dub-ca.crt https://155.212.24.77:3001/dub/ --resolve 155.212.24.77:3001:127.0.0.1)"
printf "  https /dub/api/health-> %s\n" "$(curl -s -o /dev/null -w '%{http_code}' --cacert /etc/nginx/ssl/dub-ca.crt https://155.212.24.77:3001/dub/api/health --resolve 155.212.24.77:3001:127.0.0.1)"
printf "  реальный IP в логах: %s\n" "$(curl -s -o /dev/null http://127.0.0.1:3001/dub/ ; sudo tail -1 /var/log/nginx/access.log | awk '{print $1}')"
