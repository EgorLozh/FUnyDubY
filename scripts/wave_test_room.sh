#!/usr/bin/env bash
# Служебная комната для проверки волны: тот же клип, что у пользователя, отдельная комната.
set -u
API=http://127.0.0.1:8091/api
cd ~/FUnyDubY

echo "== беру клип из комнаты пользователя (только чтение)"
SRC=~/FUnyDubY/data/rooms/vama4n5834jxf7y29zyf/original/source.mp4
cp "$SRC" /tmp/wavetest.mp4 && ls -la /tmp/wavetest.mp4 | sed 's/^/  /'

echo "== создаю комнату с участником (одним запросом)"
CREATED=$(curl -s -X POST "$API/rooms" -H 'Content-Type: application/json' \
  -d '{"title":"Проверка волны (служебная)","display_name":"Проверка волны"}')
ROOM=$(printf '%s' "$CREATED" | python3 -c 'import json,sys; print(json.load(sys.stdin)["room"]["id"])')
TOKEN=$(printf '%s' "$CREATED" | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')
echo "  комната: $ROOM, токен длиной ${#TOKEN}"

echo "== загружаю видео и запускаю обработку"
curl -s -X POST "$API/rooms/$ROOM/video?autostart=true" -H "X-Participant-Token: $TOKEN" \
  -F "file=@/tmp/wavetest.mp4" -o /tmp/upload.json -w "  HTTP %{http_code}\n"

echo "== жду готовности"
STATUS=""
for i in $(seq 1 90); do
  STATUS=$(curl -s "$API/rooms/$ROOM" -H "X-Participant-Token: $TOKEN" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("status",""))' 2>/dev/null)
  [ "$STATUS" = "READY" ] && break
  sleep 2
done
echo "  статус: $STATUS"

echo "== реплики комнаты:"
curl -s "$API/rooms/$ROOM/lines" -H "X-Participant-Token: $TOKEN" | python3 -c '
import json, sys
for line in json.load(sys.stdin):
    print("   {} .. {} мс | {}".format(line["start_ms"], line["end_ms"], (line.get("text") or "")[:44]))'

echo "  https://egrigorich.duckdns.org:3001/dub/room/$ROOM"
echo "$ROOM" > ~/.wave_test_room
