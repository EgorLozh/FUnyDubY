#!/usr/bin/env bash
# Удаление комнаты: проверка, что не остаётся ни файлов, ни строк.
#
# Сценарий: комната с видео -> конвейер -> запись участника -> готовая сборка -> удаление.
# После удаления проверяем каждый след:
#   * каталог комнаты на диске;
#   * строки во ВСЕХ таблицах, где есть колонка room_id (обход по каталогу, а не по списку);
#   * дочерние таблицы по сохранённым идентификаторам (джоб, этапы, реплики, тейки, назначения).
#
# Запуск: bash scripts/check_room_deletion.sh [клип] [база API]
set -uo pipefail

CLIP="${1:-/tmp/testclip/clip.mp4}"
BASE="${2:-http://127.0.0.1:3001/dub/api}"   # публичный путь через хост-nginx
ADMIN_TOKEN="${ADMIN_TOKEN:-$(grep '^ADMIN_TOKEN=' ~/FUnyDubY/.env 2>/dev/null | cut -d= -f2)}"

json() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)" 2>/dev/null; }
psql_q() { sudo -u postgres psql -d dubbing -tAc "$1" 2>/dev/null; }
PASS=0
FAIL=0
ok() { PASS=$((PASS + 1)); printf '  ok   %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf '  FAIL %s: %s\n' "$1" "$2"; }

[ -f "$CLIP" ] || { echo "Нет клипа: $CLIP"; exit 1; }
[ -n "$ADMIN_TOKEN" ] || { echo "Не найден ADMIN_TOKEN (.env)"; exit 1; }

echo "== комната с содержимым"
RESP=$(curl -s -X POST "$BASE/rooms" -H 'Content-Type: application/json' \
  -d '{"title":"Проверка удаления","display_name":"Егор"}')
ROOM=$(echo "$RESP" | json "d['room']['id']")
TOKEN=$(echo "$RESP" | json "d['token']")
[ -n "$ROOM" ] || { echo "комната не создалась"; exit 1; }

curl -s -X POST "$BASE/rooms/$ROOM/video" -H "X-Participant-Token: $TOKEN" \
  -F "file=@$CLIP;type=video/mp4" > /dev/null
JOB=$(curl -s "$BASE/rooms/$ROOM/jobs" -H "X-Participant-Token: $TOKEN" | json "d[0]['id']")
for _ in $(seq 1 120); do
  sleep 5
  ST=$(curl -s "$BASE/rooms/$ROOM/jobs/$JOB" -H "X-Participant-Token: $TOKEN" | json "d['status']")
  case "$ST" in DONE|FAILED) break;; esac
done
LINE=$(curl -s "$BASE/rooms/$ROOM/lines" -H "X-Participant-Token: $TOKEN" | json "d[0]['id']")
DUR=$(( $(curl -s "$BASE/rooms/$ROOM/lines/$LINE" -H "X-Participant-Token: $TOKEN" | json "d['end_ms']") - $(curl -s "$BASE/rooms/$ROOM/lines/$LINE" -H "X-Participant-Token: $TOKEN" | json "d['start_ms']") ))
WORK=$(mktemp -d)
ffmpeg -nostdin -v error -y -f lavfi -i "sine=frequency=330:duration=$(awk -v ms="$DUR" 'BEGIN{printf "%.3f", ms/1000}')" -c:a libopus -f webm - > "$WORK/t.webm" 2>/dev/null
curl -s -X POST "$BASE/rooms/$ROOM/lines/$LINE/assign" -H "X-Participant-Token: $TOKEN" > /dev/null
curl -s -X POST "$BASE/rooms/$ROOM/lines/$LINE/recordings" -H "X-Participant-Token: $TOKEN" \
  -F "file=@$WORK/t.webm;type=audio/webm" > /dev/null
RID=$(curl -s -X POST "$BASE/rooms/$ROOM/renders" -H "X-Participant-Token: $TOKEN" \
  -H 'Content-Type: application/json' -d '{}' | json "d['id']")
for _ in $(seq 1 60); do
  sleep 3
  RSTATUS=$(curl -s "$BASE/rooms/$ROOM/renders/$RID" -H "X-Participant-Token: $TOKEN" | json "d['status']")
  case "$RSTATUS" in DONE|FAILED|CANCELED) break;; esac
done
printf '  ..  комната %s: конвейер %s, реплика %s, сборка %s\n' "$ROOM" "$ST" "$LINE" "$RSTATUS"
printf '  ..  занято на диске: %s МБ, файлов: %s\n' \
  "$(du -sm "data/rooms/$ROOM" 2>/dev/null | cut -f1)" \
  "$(find "data/rooms/$ROOM" -type f 2>/dev/null | wc -l)"

echo "== сохраняю все идентификаторы комнаты"
psql_q "select 'VIDEO='||id||' JOB='||job_id||' LINE='||line_id||' TAKES='||cnt from (
          select v.id, j.id as job_id, l.id as line_id, (select count(*) from recordings r where r.line_id=l.id) as cnt
          from rooms ro join videos v on v.id=ro.video_id
          left join processing_jobs j on j.video_id=v.id
          left join dialogue_lines l on l.room_id=ro.id
          where ro.id='$ROOM' limit 1) t" > /tmp/ids.txt
VIDEO=$(grep -o 'VIDEO=[^ ]*' /tmp/ids.txt | cut -d= -f2)
JOBID=$(grep -o 'JOB=[^ ]*' /tmp/ids.txt | cut -d= -f2)
LINEID=$(grep -o 'LINE=[^ ]*' /tmp/ids.txt | cut -d= -f2)
printf '  ..  video=%s job=%s line=%s\n' "${VIDEO:0:8}" "${JOBID:0:8}" "${LINEID:0:8}"

echo "== удаляю комнату через админку"
CODE=$(curl -s -o /tmp/del.json -w '%{http_code}' -X DELETE "$BASE/admin/rooms/$ROOM" -H "X-Admin-Token: $ADMIN_TOKEN")
printf '  ..  http %s, ответ: %s\n' "$CODE" "$(head -c 120 /tmp/del.json)"
sleep 12   # purge уносит файлы и строку

echo "== ищу следы"
if [ -d "data/rooms/$ROOM" ]; then
  bad "каталог комнаты удалён" "остался: $(du -sh "data/rooms/$ROOM" | cut -f1)"
else
  ok "каталог комнаты удалён с диска"
fi

if grep -q "$ROOM" /tmp/ids.txt 2>/dev/null; then :; fi
ROOM_ROW=$(psql_q "select count(*) from rooms where id='$ROOM'")
[ "$ROOM_ROW" = "0" ] && ok "строки комнаты нет" || bad "строки комнаты нет" "осталось $ROOM_ROW"

# Обход по каталогу: любая таблица с колонкой room_id
TABLES=$(psql_q "select table_name from information_schema.columns where column_name='room_id' and table_schema='public' order by table_name")
RESIDUE=""
for TABLE in $TABLES; do
  COUNT=$(psql_q "select count(*) from \"$TABLE\" where room_id='$ROOM'")
  [ "${COUNT:-0}" != "0" ] && RESIDUE="$RESIDUE $TABLE=$COUNT"
done
[ -z "$RESIDUE" ] && ok "ни в одной таблице с room_id нет строк ($(echo "$TABLES" | wc -w) таблиц)" || bad "нет строк по room_id" "$RESIDUE"

# Дочерние таблицы по идентификаторам (если где-то потерялся каскад)
for CHECK in "job_stages:job_id:$JOBID" "recordings:line_id:$LINEID" "assignments:line_id:$LINEID"; do
  TABLE="${CHECK%%:*}"; REST="${CHECK#*:}"; COLUMN="${REST%%:*}"; VALUE="${REST#*:}"
  [ -z "$VALUE" ] && continue
  COUNT=$(psql_q "select count(*) from \"$TABLE\" where $COLUMN='$VALUE'")
  [ "${COUNT:-0}" = "0" ] && ok "$TABLE: строк по $COLUMN нет" || bad "$TABLE" "осталось $COUNT"
done

echo
echo "Итого: успешно $PASS, провалов $FAIL"
rm -rf "$WORK"
[ "$FAIL" -eq 0 ]
