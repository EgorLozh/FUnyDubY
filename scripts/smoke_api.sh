#!/usr/bin/env bash
# Smoke-тест API: комнаты, участники, права, конфликты, purge.
# Запуск: bash scripts/smoke_api.sh [BASE_URL]   (по умолчанию http://127.0.0.1:8091/api)
set -uo pipefail

BASE="${1:-http://127.0.0.1:8091/api}"
PASS=0
FAIL=0

json() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)" 2>/dev/null; }

check() { # check <название> <ожидаемое> <фактическое>
  if [ "$2" = "$3" ]; then
    printf '  \033[32mok\033[0m   %-42s %s\n' "$1" "$3"; PASS=$((PASS + 1))
  else
    printf '  \033[31mFAIL\033[0m %-42s ждали %s, получили %s\n' "$1" "$2" "$3"; FAIL=$((FAIL + 1))
  fi
}

code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }

echo "== база: $BASE"
check "GET /health" 200 "$(code "$BASE/health")"
check "GET /ready" 200 "$(code "$BASE/ready")"

echo "== комната"
RESP=$(curl -s -X POST "$BASE/rooms" -H 'Content-Type: application/json' \
  -d '{"title":"Смоук-тест","display_name":"Егор"}')
ROOM=$(echo "$RESP" | json "d['room']['id']")
TOKEN=$(echo "$RESP" | json "d['token']")
PID=$(echo "$RESP" | json "d['participant_id']")
check "POST /rooms -> id длиной 20" 20 "${#ROOM}"
[ -n "$TOKEN" ] && { echo "  ok   токен участника выдан (${#TOKEN} символов)"; PASS=$((PASS + 1)); } \
                || { echo "  FAIL токен не выдан"; FAIL=$((FAIL + 1)); }
check "GET /rooms/{id}" 200 "$(code "$BASE/rooms/$ROOM")"
check "GET /rooms/{id} counters.participants" 1 "$(curl -s "$BASE/rooms/$ROOM" | json "d['counters']['participants']")"
check "GET неизвестной комнаты -> 404" 404 "$(code "$BASE/rooms/22222222222222222222")"
check "POST /rooms/.../participants без дублей имён -> 409" 409 \
  "$(code -X POST "$BASE/rooms/$ROOM/participants" -H 'Content-Type: application/json' -d '{"display_name":"Егор"}')"

echo "== участники"
SECOND=$(curl -s -X POST "$BASE/rooms/$ROOM/participants" -H 'Content-Type: application/json' \
  -d '{"display_name":"Аня"}')
check "POST участника" 1 "$(echo "$SECOND" | json "1 if d.get('token') else 0")"
check "GET участников" 2 "$(curl -s "$BASE/rooms/$ROOM/participants" | json "len(d)")"
check "PATCH своего имени" 200 \
  "$(code -X PATCH "$BASE/rooms/$ROOM/participants/$PID" -H "X-Participant-Token: $TOKEN" \
     -H 'Content-Type: application/json' -d '{"display_name":"Егор Л."}')"
check "PATCH чужого профиля -> 403" 403 \
  "$(code -X PATCH "$BASE/rooms/$ROOM/participants/$(echo "$SECOND" | json "d['participant_id']")" \
     -H "X-Participant-Token: $TOKEN" -H 'Content-Type: application/json' -d '{"display_name":"Хакер"}')"

echo "== настройки и права"
check "PATCH /rooms/{id} с токеном" 200 \
  "$(code -X PATCH "$BASE/rooms/$ROOM" -H "X-Participant-Token: $TOKEN" \
     -H 'Content-Type: application/json' -d '{"ducking_db":-9,"max_speakers":3}')"
check "ducking_db сохранился" -9 "$(curl -s "$BASE/rooms/$ROOM" | json "d['settings']['ducking_db']")"
check "PATCH с недопустимым значением -> 422" 422 \
  "$(code -X PATCH "$BASE/rooms/$ROOM" -H "X-Participant-Token: $TOKEN" \
     -H 'Content-Type: application/json' -d '{"ducking_db":5}')"
check "PATCH без токена -> 401" 401 \
  "$(code -X PATCH "$BASE/rooms/$ROOM" -H 'Content-Type: application/json' -d '{"ducking_db":-5}')"
check "DELETE без токена -> 401" 401 "$(code -X DELETE "$BASE/rooms/$ROOM")"
check "DELETE с чужим токеном (не создатель) -> 403" 403 \
  "$(code -X DELETE "$BASE/rooms/$ROOM" -H "X-Participant-Token: $(echo "$SECOND" | json "d['token']")")"

echo "== видео: подготовка фикстур (ffmpeg ливфи-источники)"
TMPD=$(mktemp -d)
gen() { ffmpeg -nostdin -v error -f lavfi -i "testsrc=size=320x240:rate=15" ${2:+-f lavfi -i "$2"} -t "$1" -c:v libx264 -preset ultrafast -pix_fmt yuv420p ${3:+-c:a aac} -shortest -y "$4"; }
gen 4 "sine=frequency=440" audio "$TMPD/ok.mp4"
gen 4 "" "" "$TMPD/noaudio.mp4"
gen 660 "sine=frequency=440" audio "$TMPD/toolong.mp4"
gen 4 "sine=frequency=440" audio "$TMPD/bad.avi"
ls -la "$TMPD" | tail -5

echo "== видео: валидации"
check "POST видео без токена -> 401" 401 \
  "$(code -X POST "$BASE/rooms/$ROOM/video" -F "file=@$TMPD/ok.mp4")"
check "загрузка MP4 -> 201" 201 \
  "$(code -X POST "$BASE/rooms/$ROOM/video" -H "X-Participant-Token: $TOKEN" -F "file=@$TMPD/ok.mp4")"
check "повторная загрузка без replace -> 409" 409 \
  "$(code -X POST "$BASE/rooms/$ROOM/video" -H "X-Participant-Token: $TOKEN" -F "file=@$TMPD/ok.mp4")"
check "GET метаданных видео" 200 "$(code "$BASE/rooms/$ROOM/video")"
check "duration_ms ≈ 4000" 1 "$(curl -s "$BASE/rooms/$ROOM/video" | json "1 if 3500 <= d['duration_ms'] <= 4500 else 0")"
check "has_audio = True" True "$(curl -s "$BASE/rooms/$ROOM/video" | json "d['has_audio']")"
check "sha256 посчитан" 64 "$(curl -s "$BASE/rooms/$ROOM/video" | json "len(d['sha256'])")"
check "Range-запрос -> 206" 206 \
  "$(code -H 'Range: bytes=0-99' "$BASE/rooms/$ROOM/video/file")"
check "Range вернул ровно 100 байт" 100 \
  "$(curl -s -H 'Range: bytes=0-99' "$BASE/rooms/$ROOM/video/file" | wc -c | tr -d ' ')"

echo "== видео: отказы"
check "видео без звука -> 422 no_audio" 422 \
  "$(code -X POST "$BASE/rooms/$ROOM/video?replace=1" -H "X-Participant-Token: $TOKEN" -F "file=@$TMPD/noaudio.mp4")"
check "11-минутное видео -> 422 video_too_long" 422 \
  "$(code -X POST "$BASE/rooms/$ROOM/video?replace=1" -H "X-Participant-Token: $TOKEN" -F "file=@$TMPD/toolong.mp4")"
check "AVI -> 415 unsupported_format" 415 \
  "$(code -X POST "$BASE/rooms/$ROOM/video?replace=1" -H "X-Participant-Token: $TOKEN" -F "file=@$TMPD/bad.avi")"
check "мусорный файл -> 415/422" 415 \
  "$(code -X POST "$BASE/rooms/$ROOM/video?replace=1" -H "X-Participant-Token: $TOKEN" -F "file=@/etc/hostname;filename=junk.mp4")"
check "видео всё ещё на месте после отказов" 200 "$(code "$BASE/rooms/$ROOM/video")"
rm -rf "$TMPD"

echo "== удаление комнаты и purge (Dramatiq)"
LOGS() { docker compose logs --tail=300 worker-cpu 2>/dev/null; }
PURGE_BEFORE=$(LOGS | grep -c "room_purged")
check "DELETE создателем -> 204" 204 "$(code -X DELETE "$BASE/rooms/$ROOM" -H "X-Participant-Token: $TOKEN")"
check "GET удалённой комнаты -> 410" 410 "$(code "$BASE/rooms/$ROOM")"
printf '  ... ждём purge_room '
PURGE_DELTA=0
for _ in $(seq 1 20); do
  PURGE_DELTA=$(( $(LOGS | grep -c "room_purged") - PURGE_BEFORE ))
  [ "$PURGE_DELTA" -ge 1 ] && break
  printf '.'
  sleep 2
done
echo
check "purge_room выполнился (новый лог воркера)" 1 "$PURGE_DELTA"
check "каталог комнаты удалён с диска" 1 \
  "$([ -d "data/rooms/$ROOM" ] && echo 0 || echo 1)"

echo
echo "Итого: успешно $PASS, провалов $FAIL"
[ "$FAIL" -eq 0 ]
