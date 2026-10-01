#!/usr/bin/env bash
# Прогон конвейера на новой комнате с подробной печатью: артефакты этапов, реплики, нарезки, логи.
set -uo pipefail
cd ~/FUnyDubY

BASE=http://127.0.0.1:8091/api
CLIP="${1:-/tmp/testclip/clip.mp4}"
PSQL="sudo -u postgres psql -d dubbing -tAc"

RESP=$(curl -s -X POST "$BASE/rooms" -H 'Content-Type: application/json' -d '{"title":"Диагностика конвейера","display_name":"Егор"}')
ROOM=$(python3 -c "import json,sys;print(json.load(sys.stdin)['room']['id'])" <<<"$RESP")
TOKEN=$(python3 -c "import json,sys;print(json.load(sys.stdin)['token'])" <<<"$RESP")
echo "комната: $ROOM"
curl -s -X POST "$BASE/rooms/$ROOM/video" -H "X-Participant-Token: $TOKEN" -F "file=@$CLIP" -o /dev/null -w "загрузка: %{http_code}\n"
JOBSTART=$(date +%s)
curl -s -X POST "$BASE/rooms/$ROOM/jobs" -H "X-Participant-Token: $TOKEN" -H 'Content-Type: application/json' -d '{"scope":"all"}' -o /dev/null

for i in $(seq 1 48); do
  STATE=$($PSQL "select status::text || '/' || coalesce(current_stage::text,'-') || '/' || progress from processing_jobs where room_id='$ROOM'")
  printf '\r  %ss %s          ' "$(( $(date +%s) - JOBSTART ))" "$STATE"
  case "$STATE" in DONE*|FAILED*) break;; esac
  sleep 5
done
echo

echo "=== этапы ==="
$PSQL "select s.stage::text || ' | ' || s.attempt || ' | ' || s.status::text || ' | ' || coalesce(round(extract(epoch from (s.finished_at - s.started_at))::numeric,1)::text,'-') || 's | ' || left(coalesce(s.artifacts::text,'-'), 150)
       from job_stages s join processing_jobs j on j.id = s.job_id where j.room_id='$ROOM' order by s.created_at"

echo "=== реплики (БД) ==="
$PSQL "select count(*) || ' реплик, правленых: ' || sum(case when is_edited then 1 else 0 end) from dialogue_lines where room_id='$ROOM'"

echo "=== реплики (API) ==="
curl -s "$BASE/rooms/$ROOM/lines" -o /tmp/api_lines.json -w "код %{http_code}, " 
python3 -c "
import json
d = json.load(open('/tmp/api_lines.json'))
print(len(d), 'реплик')
for line in d[:6]:
    print(f\"  [{line['start_ms']/1000:6.2f}-{line['end_ms']/1000:6.2f}] {line['speaker_label']:<10} {line['text'][:58]}\")
" 2>&1 | head -10

echo "=== нарезки и сироты ==="
DIR="data/rooms/$ROOM/speech/segments"
FILES=$(ls "$DIR" 2>/dev/null | wc -l)
$PSQL "select regexp_replace(speech_path, '^.*/', '') from dialogue_lines where room_id='$ROOM' and speech_path is not null" | sort > /tmp/db_seg.txt
ls "$DIR" 2>/dev/null | sort > /tmp/fs_seg.txt
echo "файлов: $FILES, сирот: $(comm -13 /tmp/db_seg.txt /tmp/fs_seg.txt | grep -c wav || true)"
echo "=== логи нарезки ==="
docker compose logs --tail=1200 worker-gpu 2>/dev/null | grep -E "merge_lines_built|merge_inserting|merge_renumbering|merge_pruning|stale_segments_removed|stage_done" | tail -6
echo "=== прощальное состояние ==="
$PSQL "select 'комната ' || case when deleted_at is null then 'жива' else 'удалена' end from rooms where id='$ROOM'"
echo "$ROOM" > /tmp/diag_room_id.txt
