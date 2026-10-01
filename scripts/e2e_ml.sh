#!/usr/bin/env bash
# E2E-проверка ML-конвейера: создать комнату, загрузить тестовый клип, дождаться
# обработки, показать этапы, метрики качества разделения и получившиеся реплики.
#
# Запуск: bash scripts/e2e_ml.sh [/tmp/testclip/clip.mp4] [http://127.0.0.1:8091/api]
set -uo pipefail

CLIP="${1:-/tmp/testclip/clip.mp4}"
BASE="${2:-http://127.0.0.1:8091/api}"
TIMEOUT_S="${3:-900}"

json() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)" 2>/dev/null; }

[ -f "$CLIP" ] || { echo "Нет файла клипа: $CLIP (сначала bash scripts/make_test_clip.sh)"; exit 1; }

echo "== создаю комнату и загружаю $CLIP =="
RESP=$(curl -s -X POST "$BASE/rooms" -H 'Content-Type: application/json' \
  -d '{"title":"ML e2e","display_name":"Егор"}')
ROOM=$(echo "$RESP" | json "d['room']['id']")
TOKEN=$(echo "$RESP" | json "d['token']")
echo "комната: $ROOM"

UP=$(curl -s -X POST "$BASE/rooms/$ROOM/video" -H "X-Participant-Token: $TOKEN" \
  -F "file=@$CLIP")
echo "загрузка: duration_ms=$(echo "$UP" | json "d.get('duration_ms')") размер=$(echo "$UP" | json "d.get('size_bytes')")"

JOB=$(curl -s "$BASE/rooms/$ROOM/jobs" | json "d[0]['id']")
echo "джоб: $JOB"
echo "== ждём обработку (лимит ${TIMEOUT_S}s) =="

STARTED=$(date +%s)
STATUS=""
while :; do
  J=$(curl -s "$BASE/rooms/$ROOM/jobs/$JOB")
  STATUS=$(echo "$J" | json "d['status']")
  STAGE=$(echo "$J" | json "d.get('current_stage') or '-'")
  PROGRESS=$(echo "$J" | json "d['progress']")
  printf '\r   status=%-7s stage=%-16s %s%%' "$STATUS" "$STAGE" "$PROGRESS"
  [ "$STATUS" = "DONE" ] || [ "$STATUS" = "FAILED" ] || [ "$STATUS" = "CANCELED" ] && break
  [ $(( $(date +%s) - STARTED )) -gt "$TIMEOUT_S" ] && { echo; echo "ТАЙМАУТ"; break; }
  sleep 5
done
echo

echo "== этапы =="
echo "$J" | python3 -c "
import json,sys
d=json.load(sys.stdin)
for s in d['stages']:
    extra=''
    a=s.get('artifacts') or {}
    if 'quality' in a: extra=f\"  quality={a['quality']}\"
    if 'words' in a: extra+=f\"  words={a['words']} lang={a.get('language')}\"
    if 'speakers' in a: extra+=f\"  speakers={a['speakers']} degraded={a.get('degraded')}\"
    if 'lines' in a: extra+=f\"  lines={a['lines']}\"
    if s.get('error'): extra+=f\"  ERROR={s['error'].get('code')}: {str(s['error'].get('message'))[:120]}\"
    print(f\"  {s['stage']:<16} {s['status']:<8} {str(s.get('duration_ms'))+' мс':>10}{extra}\")
"
[ "$STATUS" = "FAILED" ] && echo "$J" | json "d['error']"

echo "== реплики =="
curl -s "$BASE/rooms/$ROOM/lines" | python3 -c "
import json,sys
lines=json.load(sys.stdin)
for line in lines[:12]:
    t0=line['start_ms']/1000; t1=line['end_ms']/1000
    print(f\"  [{t0:6.2f}-{t1:6.2f}] {line['speaker_label']:<10} {line['text'][:70]}\")
print(f'  всего реплик: {len(lines)}')
"
echo "== спикеры =="
curl -s "$BASE/rooms/$ROOM/speakers" | python3 -c "
import json,sys
for s in json.load(sys.stdin):
    print(f\"  {s['speaker_label']:<10} реплик={s['lines']:<4} суммарно={s['total_ms']/1000:.1f} c\")
"

echo
echo "== файлы комнаты на диске =="
ls -la "data/rooms/$ROOM/" "data/rooms/$ROOM/speech" "data/rooms/$ROOM/dialogue" 2>/dev/null | sed 's/^/  /'
echo "ROOM=$ROOM"
