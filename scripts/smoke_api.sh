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

echo "== удаление комнаты и purge (Dramatiq)"
check "DELETE создателем -> 204" 204 "$(code -X DELETE "$BASE/rooms/$ROOM" -H "X-Participant-Token: $TOKEN")"
check "GET удалённой комнаты -> 410" 410 "$(code "$BASE/rooms/$ROOM")"
printf '  ... ждём purge_room '
for _ in $(seq 1 15); do
  if ! curl -s -m 5 "http://127.0.0.1:8091/api/rooms/$ROOM" >/dev/null 2>&1; then :; fi
  LEFT=$(docker compose -f "$(dirname "$0")/../docker-compose.yml" logs --tail=200 worker-cpu 2>/dev/null \
    | grep -c "room_purged")
  [ "${LEFT:-0}" -ge 1 ] && break
  printf '.'
  sleep 2
done
echo
check "purge_room выполнился (лог воркера)" 1 "${LEFT:-0}"

echo
echo "Итого: успешно $PASS, провалов $FAIL"
[ "$FAIL" -eq 0 ]
