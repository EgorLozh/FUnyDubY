#!/usr/bin/env bash
# Сквозная проверка: загрузка записи с «разгоном» — первые 3 секунды должны быть отрезаны.
set -u
cd ~/FUnyDubY
DC="docker compose"
API=http://127.0.0.1:8091/api
CLIP=~/FUnyDubY/data/rooms/vama4n5834jxf7y29zyf/original/source.mp4

echo "== создаю комнату и разбираю клип"
CREATED=$(curl -s -X POST "$API/rooms" -H 'Content-Type: application/json' \
  -d '{"title":"Проверка разгона","display_name":"Проверка"}')
ROOM=$(printf '%s' "$CREATED" | python3 -c 'import json,sys;print(json.load(sys.stdin)["room"]["id"])')
TOKEN=$(printf '%s' "$CREATED" | python3 -c 'import json,sys;print(json.load(sys.stdin)["token"])')
curl -s -X POST "$API/rooms/$ROOM/video?autostart=true" -H "X-Participant-Token: $TOKEN" -F "file=@$CLIP" -o /dev/null
for i in $(seq 1 70); do
  S=$(curl -s "$API/rooms/$ROOM" -H "X-Participant-Token: $TOKEN" | python3 -c 'import json,sys;print(json.load(sys.stdin)["status"])')
  [ "$S" = "READY" ] && break
  sleep 3
done
echo "  комната $ROOM: $S"

LINE_MS=$(curl -s "$API/rooms/$ROOM/lines" -H "X-Participant-Token: $TOKEN" | python3 -c '
import json,sys
lines = json.load(sys.stdin)
best = max(lines, key=lambda l: l["end_ms"] - l["start_ms"])
print(best["end_ms"] - best["start_ms"])')
LINE=$(curl -s "$API/rooms/$ROOM/lines" -H "X-Participant-Token: $TOKEN" | python3 -c '
import json,sys
lines = json.load(sys.stdin)
best = max(lines, key=lambda l: l["end_ms"] - l["start_ms"])
print(best["id"])')
echo "  реплика $LINE длиной $LINE_MS мс"

echo "== делаю запись: 3 с разгона (шум) + сама реплика (тон)"
SECONDS_TAKE=$(python3 -c "print($LINE_MS/1000)")
$DC exec -T worker-gpu ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i "anoisesrc=color=white:amplitude=0.004:duration=3" \
  -f lavfi -i "sine=frequency=440:duration=$SECONDS_TAKE" \
  -filter_complex "[1:a]volume=0.3[t];[0:a][t]concat=n=2:v=0:a=1" \
  -ar 48000 -ac 1 -c:a libopus /tmp/take_with_lead.webm
$DC cp worker-gpu:/tmp/take_with_lead.webm /tmp/take_with_lead.webm >/dev/null 2>&1
ls -la /tmp/take_with_lead.webm | sed 's/^/  /'

echo "== загружаю тейк с разгоном (lead_in_ms=3000)"
curl -s -o /tmp/take.json -w "  ответ: %{http_code}\n" -X POST \
  "$API/rooms/$ROOM/lines/$LINE/recordings?lead_in_ms=3000" \
  -H "X-Participant-Token: $TOKEN" -H 'Idempotency-Key: lead-check-1' \
  -F "file=@/tmp/take_with_lead.webm"

echo "== что лежит в хранилище"
$DC exec -T api python3 -c "
import subprocess, glob, math, array, os
files = sorted(glob.glob('/data/rooms/$ROOM/recordings/**/*.wav', recursive=True), key=os.path.getmtime)
print('  обработанные файлы:', [os.path.basename(f) for f in files])
if files:
    f = files[-1]
    dur = subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','csv=p=0',f], capture_output=True, text=True).stdout.strip()
    raw = subprocess.run(['ffmpeg','-hide_banner','-v','error','-i',f,'-f','f32le','-'], capture_output=True).stdout
    d = array.array('f'); d.frombytes(raw[:len(raw)//4*4])
    def rms(a):
        s = sum(float(x)*float(x) for x in a)/max(1,len(a)); return 10*math.log10(max(s,1e-12))
    print('  длительность: %s с (в реплике %.2f с)' % (dur, $LINE_MS/1000))
    print('  первые 100 мс: %.1f дБ (тон), последние 100 мс: %.1f дБ' % (rms(d[:4800]), rms(d[-4800:])))
    print('  если оба уровня близки — разгон срезан и в тейке только реплика')
"
echo "$ROOM" > ~/.lead_test_room
echo "  комната для уборки: $ROOM"
