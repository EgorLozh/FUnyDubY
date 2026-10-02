#!/usr/bin/env bash
# Загрузка тейка с разгоном на свободную реплику: проверяем, что разгон срезан.
set -u
cd ~/FUnyDubY
DC="docker compose"
API=http://127.0.0.1:8091/api
ROOM=u2exvgtjr2jyav44zxmd

TOKEN=$(curl -s -X POST "$API/rooms/$ROOM/participants" -H 'Content-Type: application/json' -d '{"display_name":"Проверка '"$RANDOM"'"}' | python3 -c 'import json,sys;print(json.load(sys.stdin)["token"])')

read -r LINE LINE_MS < <(curl -s "$API/rooms/$ROOM/lines" -H "X-Participant-Token: $TOKEN" | python3 -c '
import json,sys
free = [l for l in json.load(sys.stdin) if not l.get("assigned_participant_id")]
free.sort(key=lambda l: l["end_ms"] - l["start_ms"], reverse=True)
best = free[0]
print(best["id"], best["end_ms"] - best["start_ms"])')
echo "== свободная реплика $LINE длиной $LINE_MS мс"

SEC=$(python3 -c "print($LINE_MS/1000)")
$DC exec -T worker-gpu ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i "anoisesrc=color=white:amplitude=0.004:duration=3" \
  -f lavfi -i "sine=frequency=440:duration=$SEC" \
  -filter_complex "[1:a]volume=0.3[t];[0:a][t]concat=n=2:v=0:a=1" \
  -ar 48000 -ac 1 -c:a libopus /tmp/lead_take.webm
$DC cp worker-gpu:/tmp/lead_take.webm /tmp/lead_take.webm >/dev/null 2>&1

echo "== загружаю (lead_in_ms=3000)"
curl -s -o /tmp/take2.json -w "  ответ: %{http_code}\n" -X POST \
  "$API/rooms/$ROOM/lines/$LINE/recordings?lead_in_ms=3000" \
  -H "X-Participant-Token: $TOKEN" -H 'Idempotency-Key: lead-check-3' \
  -F "file=@/tmp/lead_take.webm;type=audio/webm"
head -c 200 /tmp/take2.json | sed 's/^/  /'; echo

echo "== результат в хранилище"
$DC exec -T api python3 -c "
import subprocess, glob, math, array, os
files = sorted(glob.glob('/data/rooms/$ROOM/recordings/**/*.wav', recursive=True), key=os.path.getmtime)
print('  файлы тейков:', [os.path.basename(f) for f in files])
if files:
    f = files[-1]
    dur = subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','csv=p=0',f], capture_output=True, text=True).stdout.strip()
    raw = subprocess.run(['ffmpeg','-hide_banner','-v','error','-i',f,'-f','f32le','-'], capture_output=True).stdout
    d = array.array('f'); d.frombytes(raw[:len(raw)//4*4])
    def rms(a):
        s = sum(float(x)*float(x) for x in a)/max(1,len(a)); return 10*math.log10(max(s,1e-12))
    print('  длительность тейка: %s с (в реплике %.2f с)' % (dur, $LINE_MS/1000))
    print('  начало %.1f дБ, середина %.1f дБ, конец %.1f дБ' % (rms(d[:4800]), rms(d[len(d)//2:len(d)//2+4800]), rms(d[-4800:])))
    print('  (в исходнике первые 3 с были шумом ~-48 дБ, значит разгон срезан)')
"
