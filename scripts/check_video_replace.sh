#!/usr/bin/env bash
# Проверка замены видео в существующей комнате: реплики и тейки старого монтажа должны исчезнуть.
set -u
API=http://127.0.0.1:8091/api
ROOM=sptbz7jhcs8t4d3syms4
CLIP=~/FUnyDubY/data/rooms/vama4n5834jxf7y29zyf/original/source.mp4

echo "== до замены"
curl -s -X POST "$API/rooms/$ROOM/participants" -H 'Content-Type: application/json' \
  -d '{"display_name":"Замена '"$RANDOM"'"}' -o /tmp/part.json
TOKEN=$(python3 -c 'import json;print(json.load(open("/tmp/part.json"))["token"])')
curl -s "$API/rooms/$ROOM" -H "X-Participant-Token: $TOKEN" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print("  статус:", d["status"], "| реплик:", d["counters"]["lines"], "| озвучено:", d["counters"]["recorded_lines"])'

echo "== загрузка без replace (ожидаем отказ)"
curl -s -o /tmp/no.txt -w "  HTTP %{http_code} " -X POST "$API/rooms/$ROOM/video" -H "X-Participant-Token: $TOKEN" -F "file=@$CLIP"
python3 -c 'import json;d=json.load(open("/tmp/no.txt"));print("код:", d.get("code","?"))' 2>/dev/null || echo

echo "== загрузка с replace=1"
curl -s -o /tmp/yes.json -w "  HTTP %{http_code}\n" -X POST "$API/rooms/$ROOM/video?replace=1&autostart=true" \
  -H "X-Participant-Token: $TOKEN" -F "file=@$CLIP"
sleep 3
curl -s "$API/rooms/$ROOM" -H "X-Participant-Token: $TOKEN" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print("  сразу после замены — статус:", d["status"], "| реплик:", d["counters"]["lines"], "| озвучено:", d["counters"]["recorded_lines"])'

echo "== ждём разбор нового видео"
for i in $(seq 1 60); do
  STATUS=$(curl -s "$API/rooms/$ROOM" -H "X-Participant-Token: $TOKEN" | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])')
  [ "$STATUS" = "READY" ] && break
  sleep 3
done
curl -s "$API/rooms/$ROOM" -H "X-Participant-Token: $TOKEN" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print("  после обработки — статус:", d["status"], "| реплик:", d["counters"]["lines"], "| озвучено:", d["counters"]["recorded_lines"])'

echo "== тейки старого монтажа (должно быть пусто)"
curl -s "$API/rooms/$ROOM/lines" -H "X-Participant-Token: $TOKEN" | python3 -c '
import json, sys
lines = json.load(sys.stdin)
recorded = [l for l in lines if l.get("has_recording")]
print("  реплик:", len(lines), "| с записями:", len(recorded))'
