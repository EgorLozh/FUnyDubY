#!/usr/bin/env bash
# Диагностика ML-конвейера: состояние этапов, мусор в нарезках, логи чистки.
set -uo pipefail
cd ~/FUnyDubY

ROOM="${1:-f7x2xanr8mgy3z2v2exk}"
DIR="data/rooms/$ROOM/speech/segments"
PSQL="sudo -u postgres psql -d dubbing -tAc"

echo "=== комната $ROOM ==="
LINES=$($PSQL "select count(*) from dialogue_lines where room_id='$ROOM'")
FILES=$(ls "$DIR" 2>/dev/null | wc -l)
echo "реплик в БД: $LINES"
echo "нарезок на диске: $FILES ($(du -sh "$DIR" 2>/dev/null | cut -f1))"

echo "=== файлы без реплики (сироты) ==="
$PSQL "select regexp_replace(speech_path, '^.*/', '') from dialogue_lines where room_id='$ROOM'" | sort > /tmp/db_seg.txt
ls "$DIR" 2>/dev/null | sort > /tmp/fs_seg.txt
ORPHANS=$(comm -13 /tmp/db_seg.txt /tmp/fs_seg.txt)
echo "сирот: $(echo "$ORPHANS" | grep -c wav || true)"
echo "$ORPHANS" | head -5 | sed 's/^/  /'

echo "=== этапы последнего джоба ==="
$PSQL "select s.stage::text || ' | ' || s.attempt || ' | ' || s.status::text || ' | artifacts=' || left(coalesce(s.artifacts::text,'-'), 90)
       from job_stages s join processing_jobs j on j.id = s.job_id
       where j.room_id='$ROOM' order by s.started_at desc nulls last limit 6"

echo "=== статус джоба ==="
$PSQL "select status::text || ' / ' || coalesce(current_stage::text,'-') || ' / ' || progress from processing_jobs where room_id='$ROOM'"

echo "=== логи про чистку нарезок ==="
docker compose logs --tail=500 worker-gpu 2>/dev/null | grep -c "stale_segments_removed" || true
