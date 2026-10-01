#!/usr/bin/env bash
# Сквозная проверка правок диалога поверх ML-конвейера (этапы 6-7).
#
# Что проверяем:
#   1. полный цикл: комната -> загрузка видео -> обработка -> реплики с нарезками;
#   2. правка реплики человеком (is_edited) переживает повторную обработку;
#   3. повторная обработка не падает на уникальном индексе (room_id, idx) и не залипает;
#   4. нарезок на диске ровно столько, сколько реплик (чистка сирот работает).
#
# Запуск: bash scripts/e2e_stage7.sh [путь_к_клипу]
set -uo pipefail

BASE="${BASE:-http://127.0.0.1:8091/api}"
CLIP="${1:-/tmp/testclip/clip.mp4}"
PASS=0
FAIL=0

json() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)" 2>/dev/null; }
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
psql_() { sudo -u postgres psql -d dubbing -tAc "$1"; }

check() {
  if [ "$2" = "$3" ]; then
    printf '  \033[32mok\033[0m   %-46s %s\n' "$1" "$3"; PASS=$((PASS + 1))
  else
    printf '  \033[31mFAIL\033[0m %-46s ждали %s, получили %s\n' "$1" "$2" "$3"; FAIL=$((FAIL + 1))
  fi
}

wait_job() { # wait_job <room> <секунд>
  local room="$1" limit="${2:-180}" state=""
  for _ in $(seq 1 $((limit / 5))); do
    state=$(psql_ "select status::text from processing_jobs where room_id='$room' order by created_at desc limit 1")
    case "$state" in DONE|FAILED) echo "$state"; return 0;; esac
    sleep 5
  done
  echo "$state"
}

[ -f "$CLIP" ] || { echo "Нет тестового клипа $CLIP (сначала bash scripts/make_test_clip.sh)"; exit 1; }

echo "== создание комнаты и загрузка =="
RESP=$(curl -s -X POST "$BASE/rooms" -H 'Content-Type: application/json' -d '{"title":"E2E этап 7","display_name":"Егор"}')
ROOM=$(echo "$RESP" | json "d['room']['id']")
TOKEN=$(echo "$RESP" | json "d['token']")
check "комната создана" 20 "${#ROOM}"
check "видео загружено" 201 "$(code -X POST "$BASE/rooms/$ROOM/video" -H "X-Participant-Token: $TOKEN" -F "file=@$CLIP")"

echo "== обработка =="
curl -s -X POST "$BASE/rooms/$ROOM/jobs" -H "X-Participant-Token: $TOKEN" -H 'Content-Type: application/json' -d '{"scope":"all"}' -o /dev/null
printf '  ... ждём конвейер '
STATE=$(wait_job "$ROOM" 240)
echo " $STATE"
check "конвейер завершился успешно" DONE "$STATE"

LINES=$(curl -s "$BASE/rooms/$ROOM/lines" | json "len(d)")
check "реплики нарезаны (>=1)" 1 "$([ "${LINES:-0}" -ge 1 ] && echo 1 || echo 0)"
echo "  ..  реплик: ${LINES:-0}"
CHECK_ART=$(psql_ "select artifacts::text from job_stages where stage='MERGE_DIALOGUE' and job_id=(select id from processing_jobs where room_id='$ROOM' order by created_at desc limit 1)")
check "этап нарезки отчитался о чистке сирот" 1 "$(echo "$CHECK_ART" | grep -c stale_segments_removed || true)"

DIR="data/rooms/$ROOM/speech/segments"
check "нарезок ровно по числу реплик" "${LINES:-0}" "$(ls "$DIR" 2>/dev/null | wc -l)"

echo "== правка реплики человеком =="
LID=$(curl -s "$BASE/rooms/$ROOM/lines" | json "d[0]['id']")
VER=$(curl -s "$BASE/rooms/$ROOM/lines/$LID" | json "d['version']")
check "PATCH текста -> 200" 200 \
  "$(code -X PATCH "$BASE/rooms/$ROOM/lines/$LID" -H "X-Participant-Token: $TOKEN" -H 'Content-Type: application/json' -d "{\"text\":\"Правленый текст\",\"expected_version\":$VER}")"
check "правка сохранена" "Правленый текст" "$(curl -s "$BASE/rooms/$ROOM/lines/$LID" | json "d['text']")"
check "реплика помечена правленой" t "$(psql_ "select is_edited from dialogue_lines where id='$LID'")"

echo "== повторная обработка поверх правки =="
curl -s -X POST "$BASE/rooms/$ROOM/jobs" -H "X-Participant-Token: $TOKEN" -H 'Content-Type: application/json' -d '{"scope":"all","force":true}' -o /dev/null
printf '  ... ждём конвейер '
STATE=$(wait_job "$ROOM" 240)
echo " $STATE"
check "повторный прогон завершился успешно" DONE "$STATE"
check "правка человека пережила пересборку" 1 \
  "$(psql_ "select count(*) from dialogue_lines where id='$LID' and is_edited and text='Правленый текст'")"
NEW_LINES=$(curl -s "$BASE/rooms/$ROOM/lines" | json "len(d)")
check "нумерация реплик непрерывна" 1 \
  "$(psql_ "select case when count(*)=count(distinct idx) and min(idx)=0 and max(idx)=count(*)-1 then 1 else 0 end from dialogue_lines where room_id='$ROOM'")"
check "ни одна реплика не осталась без аудио" 0 \
  "$(curl -s "$BASE/rooms/$ROOM/lines" | json "sum(1 for l in d if not l['has_original_audio'])")"
check "сирот нарезок нет" "${NEW_LINES:-0}" "$(ls "$DIR" 2>/dev/null | wc -l)"

echo "== уборка =="
check "комната удалена" 204 "$(code -X DELETE "$BASE/rooms/$ROOM" -H "X-Participant-Token: $TOKEN")"

echo
echo "Итого: успешно $PASS, провалов $FAIL"
[ "$FAIL" -eq 0 ]
