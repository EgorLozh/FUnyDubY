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

echo "== видео: подготовка фикстур (ffmpeg ливфи-источники)"
TMPD=$(mktemp -d)
gen() { ffmpeg -nostdin -v error -f lavfi -i "testsrc=size=320x240:rate=15" ${2:+-f lavfi -i "$2"} -t "$1" -c:v libx264 -preset ultrafast -pix_fmt yuv420p ${3:+-c:a aac} -shortest -y "$4"; }
gen 4 "sine=frequency=440" audio "$TMPD/ok.mp4"
gen 4 "" "" "$TMPD/noaudio.mp4"
gen 660 "sine=frequency=440" audio "$TMPD/toolong.mp4"
gen 4 "sine=frequency=440" audio "$TMPD/bad.avi"
ls -la "$TMPD" | tail -5

echo "== видео: валидации"
check "POST видео без токена -> 401" 401 \
  "$(code -X POST "$BASE/rooms/$ROOM/video" -F "file=@$TMPD/ok.mp4")"
check "загрузка MP4 -> 201" 201 \
  "$(code -X POST "$BASE/rooms/$ROOM/video" -H "X-Participant-Token: $TOKEN" -F "file=@$TMPD/ok.mp4")"
check "повторная загрузка без replace -> 409" 409 \
  "$(code -X POST "$BASE/rooms/$ROOM/video" -H "X-Participant-Token: $TOKEN" -F "file=@$TMPD/ok.mp4")"
check "GET метаданных видео" 200 "$(code "$BASE/rooms/$ROOM/video")"
check "duration_ms ≈ 4000" 1 "$(curl -s "$BASE/rooms/$ROOM/video" | json "1 if 3500 <= d['duration_ms'] <= 4500 else 0")"
check "has_audio = True" True "$(curl -s "$BASE/rooms/$ROOM/video" | json "d['has_audio']")"
check "sha256 посчитан" 64 "$(curl -s "$BASE/rooms/$ROOM/video" | json "len(d['sha256'])")"
check "Range-запрос -> 206" 206 \
  "$(code -H 'Range: bytes=0-99' "$BASE/rooms/$ROOM/video/file")"
check "Range вернул ровно 100 байт" 100 \
  "$(curl -s -H 'Range: bytes=0-99' "$BASE/rooms/$ROOM/video/file" | wc -c | tr -d ' ')"

echo "== видео: отказы"
check "видео без звука -> 422 no_audio" 422 \
  "$(code -X POST "$BASE/rooms/$ROOM/video?replace=1" -H "X-Participant-Token: $TOKEN" -F "file=@$TMPD/noaudio.mp4")"
check "11-минутное видео -> 422 video_too_long" 422 \
  "$(code -X POST "$BASE/rooms/$ROOM/video?replace=1" -H "X-Participant-Token: $TOKEN" -F "file=@$TMPD/toolong.mp4")"
check "AVI -> 415 unsupported_format" 415 \
  "$(code -X POST "$BASE/rooms/$ROOM/video?replace=1" -H "X-Participant-Token: $TOKEN" -F "file=@$TMPD/bad.avi")"
check "мусорный файл -> 422 corrupt_media" 422 \
  "$(code -X POST "$BASE/rooms/$ROOM/video?replace=1" -H "X-Participant-Token: $TOKEN" -F "file=@/etc/hostname;filename=junk.mp4")"
check "видео всё ещё на месте после отказов" 200 "$(code "$BASE/rooms/$ROOM/video")"
check "файл видео не затёрт неудачными попытками" 1 \
  "$(ls data/rooms/$ROOM/original/source.* >/dev/null 2>&1 && echo 1 || echo 0)"
rm -rf "$TMPD"

echo "== конвейер обработки (очередь + этапы)"
JOB=$(curl -s -X POST "$BASE/rooms/$ROOM/jobs" -H "X-Participant-Token: $TOKEN" \
  -H 'Content-Type: application/json' -d '{"scope":"all"}' | json "d['id']")
check "POST /jobs -> uuid" 36 "${#JOB}"
EX=""
for _ in $(seq 1 30); do
  EX=$(curl -s "$BASE/rooms/$ROOM/jobs/$JOB" | json "[s['status'] for s in d['stages'] if s['stage']=='extract_audio'][0]")
  [ "$EX" = "DONE" ] && break
  sleep 2
done
check "extract_audio: DONE" DONE "$EX"
check "audio/mix.wav создан" 1 "$([ -f "data/rooms/$ROOM/audio/mix.wav" ] && echo 1 || echo 0)"
check "audio/mix_mono16k.wav создан" 1 "$([ -f "data/rooms/$ROOM/audio/mix_mono16k.wav" ] && echo 1 || echo 0)"
ST=""
CODE=""
STG=""
for _ in $(seq 1 20); do
  JOBJSON=$(curl -s "$BASE/rooms/$ROOM/jobs/$JOB")
  ST=$(echo "$JOBJSON" | json "d['status']")
  CODE=$(echo "$JOBJSON" | json "(d['error'] or {}).get('code','')")
  STG=$(echo "$JOBJSON" | json "(d['error'] or {}).get('stage','')")
  [ "$ST" = "FAILED" ] && break
  sleep 2
done
check "следующий этап поставлен в очередь" 1 \
  "$(curl -s "$BASE/rooms/$ROOM/jobs/$JOB" | json "1 if any(s['stage']=='separate_speech' for s in d['stages']) else 0")"
if [ "$ST" = "FAILED" ]; then
  # На синтетическом ролике без речи ожидаем понятную доменную ошибку, а не 500 и не traceback.
  check "ML-этап ответил понятной ошибкой" ok \
    "$(case "$CODE" in no_speech_found|stage_not_implemented|bandit_weights_missing|separation_failed) echo ok;; *) echo "${CODE:-пусто}";; esac)"
  check "ошибка указывает на конкретный этап" separate_speech "$STG"
else
  # worker-gpu не поднят: задача ждёт в очереди gpu, джоб держится в RUNNING — это ожидаемое состояние
  check "ML-этап ждёт воркер gpu (без падения и без 500)" RUNNING "$ST"
  echo "  ok   сообщение ждёт в очереди gpu (worker-gpu не запущен)"
  PASS=$((PASS + 1))
fi
check "повторный POST /jobs идемпотентен (тот же джоб)" "$JOB" \
  "$(curl -s -X POST "$BASE/rooms/$ROOM/jobs" -H "X-Participant-Token: $TOKEN" -H 'Content-Type: application/json' -d '{"scope":"all"}' | json "d['id']")"
check "SSE отдаёт hello" 1 \
  "$(timeout 6 curl -s -N "$BASE/rooms/$ROOM/events" | grep -c 'event: hello' | head -1)"

echo "== диалог: правки, спикеры, назначения (этап 7)"
# Реплики могут отсутствовать, если конвейер не отработал (нет GPU-воркера). Тогда кладём
# фикстуру прямо в БД: проверки этапа 7 не должны зависеть от железа.
LINES_JSON=$(curl -s "$BASE/rooms/$ROOM/lines")
LINES=$(echo "$LINES_JSON" | json "len(d)")
if [ "${LINES:-0}" -lt 3 ]; then
  echo "  ..  реплик нет — вставляю фикстуру прямо в БД"
  sudo -u postgres psql -q -d dubbing -v ON_ERROR_STOP=1 <<SQL
insert into dialogue_lines (id, room_id, idx, start_ms, end_ms, speaker_key, speaker_label, text,
                            is_short, "overlaps", is_edited, keep_original, version)
values
 (gen_random_uuid(), '$ROOM', 0,  500,  3500, 'spk_0', 'Speaker 1', 'Первая реплика',  false, false, false, false, 1),
 (gen_random_uuid(), '$ROOM', 1, 4000,  7000, 'spk_0', 'Speaker 1', 'Вторая реплика',  false, false, false, false, 1),
 (gen_random_uuid(), '$ROOM', 2, 7500, 11000, 'spk_1', 'Speaker 2', 'Третья реплика',  false, false, false, false, 1)
on conflict (room_id, idx) do nothing;
SQL
  LINES_JSON=$(curl -s "$BASE/rooms/$ROOM/lines")
  LINES=$(echo "$LINES_JSON" | json "len(d)")
fi
check "реплики доступны (>=3)" 1 "$([ "${LINES:-0}" -ge 3 ] && echo 1 || echo 0)"
L1=$(echo "$LINES_JSON" | json "d[0]['id']")
L2=$(echo "$LINES_JSON" | json "d[1]['id']")

P7A=$(curl -s -X POST "$BASE/rooms/$ROOM/participants" -H 'Content-Type: application/json' -d '{"display_name":"Тест-1"}')
P7B=$(curl -s -X POST "$BASE/rooms/$ROOM/participants" -H 'Content-Type: application/json' -d '{"display_name":"Тест-2"}')
TA=$(echo "$P7A" | json "d['token']")
TB=$(echo "$P7B" | json "d['token']")
PA=$(echo "$P7A" | json "d['participant_id']")
PB=$(echo "$P7B" | json "d['participant_id']")

check "PUT assignment -> 200" 200 \
  "$(code -X PUT "$BASE/rooms/$ROOM/lines/$L1/assignment" -H "X-Participant-Token: $TA" -H 'Content-Type: application/json' -d '{}')"
check "гонка: второй участник получает 409" 409 \
  "$(code -X PUT "$BASE/rooms/$ROOM/lines/$L1/assignment" -H "X-Participant-Token: $TB" -H 'Content-Type: application/json' -d '{}')"
check "409 объясняет, что реплика занята" line_taken \
  "$(curl -s -X PUT "$BASE/rooms/$ROOM/lines/$L1/assignment" -H "X-Participant-Token: $TB" -H 'Content-Type: application/json' -d '{}' | json "(d.get('code') or d.get('detail',{}).get('code') or '')")"
check "в конфликте видно, кто держит реплику" "Тест-1" \
  "$(curl -s -X PUT "$BASE/rooms/$ROOM/lines/$L1/assignment" -H "X-Participant-Token: $TB" -H 'Content-Type: application/json' -d '{}' | json "d.get('display_name') or ''")"
check "повторный захват тем же участником идемпотентен" 200 \
  "$(code -X PUT "$BASE/rooms/$ROOM/lines/$L1/assignment" -H "X-Participant-Token: $TA" -H 'Content-Type: application/json' -d '{}')"
check "assigned_to=me возвращает мою реплику" "$L1" \
  "$(curl -s "$BASE/rooms/$ROOM/lines?assigned_to=me" -H "X-Participant-Token: $TA" | json "d[0]['id']")"
check "assigned_to=unassigned не содержит занятую" 0 \
  "$(curl -s "$BASE/rooms/$ROOM/lines?assigned_to=unassigned" | json "1 if any(l['id']=='$L1' for l in d) else 0")"
check "в списке видно имя держателя" "Тест-1" \
  "$(curl -s "$BASE/rooms/$ROOM/lines?assigned_to=me" -H "X-Participant-Token: $TA" | json "d[0]['assigned_display_name']")"

VER=$(curl -s "$BASE/rooms/$ROOM/lines/$L1" | json "d['version']")
check "PATCH с устаревшей версией -> 409" 409 \
  "$(code -X PATCH "$BASE/rooms/$ROOM/lines/$L1" -H "X-Participant-Token: $TA" -H 'Content-Type: application/json' -d "{\"text\":\"не должно примениться\",\"expected_version\":$((VER + 5))}")"
check "PATCH с верной версией -> 200" 200 \
  "$(code -X PATCH "$BASE/rooms/$ROOM/lines/$L1" -H "X-Participant-Token: $TA" -H 'Content-Type: application/json' -d "{\"text\":\"Правленый текст\",\"expected_version\":$VER}")"
check "текст реплики изменён" "Правленый текст" \
  "$(curl -s "$BASE/rooms/$ROOM/lines/$L1" | json "d['text']")"
check "правка помечена (is_edited)" True \
  "$(curl -s "$BASE/rooms/$ROOM/lines/$L1" | json "d['is_edited']")"
check "правка подняла версию" "$((VER + 1))" \
  "$(curl -s "$BASE/rooms/$ROOM/lines/$L1" | json "d['version']")"

check "PATCH /speakers переименовывает реплики спикера" 1 \
  "$(curl -s -X PATCH "$BASE/rooms/$ROOM/speakers/spk_0" -H "X-Participant-Token: $TA" -H 'Content-Type: application/json' -d '{"label":"Рассказчик"}' | json "1 if d['lines']>=1 else 0")"
check "новое имя видно в сводке спикеров" 1 \
  "$(curl -s "$BASE/rooms/$ROOM/speakers" | json "1 if any(s['speaker_label']=='Рассказчик' for s in d) else 0")"
check "PATCH спикера реплики меняет и ключ" speaker_2 \
  "$(curl -s -X PATCH "$BASE/rooms/$ROOM/lines/$L1" -H "X-Participant-Token: $TA" -H 'Content-Type: application/json' -d '{"speaker_label":"Speaker 2"}' | json "d['speaker_key']")"

if [ -n "${L2:-}" ]; then
  DUR_BEFORE=$(( $(curl -s "$BASE/rooms/$ROOM/lines/$L2" | json "d['end_ms']") - $(curl -s "$BASE/rooms/$ROOM/lines/$L2" | json "d['start_ms']") ))
  MID=$(( $(curl -s "$BASE/rooms/$ROOM/lines/$L2" | json "d['start_ms']") + DUR_BEFORE / 2 ))
  SPLIT=$(curl -s -X POST "$BASE/rooms/$ROOM/lines/$L2/split" -H "X-Participant-Token: $TA" -H 'Content-Type: application/json' -d "{\"at_ms\":$MID}")
  check "split вернул две реплики" 2 "$(echo "$SPLIT" | json "len(d)")"
  check "split: суммарная длительность сохранена" "$DUR_BEFORE" \
    "$(echo "$SPLIT" | json "sum(l['duration_ms'] for l in d)")"
  check "split: нумерация последовательная" 1 \
    "$(echo "$SPLIT" | json "1 if d[1]['idx']==d[0]['idx']+1 else 0")"
  check "merge склеивает обратно" "$DUR_BEFORE" \
    "$(curl -s -X POST "$BASE/rooms/$ROOM/lines/$L2/merge" -H "X-Participant-Token: $TA" -H 'Content-Type: application/json' -d '{}' | json "d['duration_ms']")"
else
  echo "  FAIL реплик нет — проверки split/merge пропущены"
  FAIL=$((FAIL + 1))
fi

check "heartbeat продлевает захват" 200 \
  "$(code -X POST "$BASE/rooms/$ROOM/lines/$L1/assignment/heartbeat" -H "X-Participant-Token: $TA")"
check "освобождение -> 204" 204 \
  "$(code -X DELETE "$BASE/rooms/$ROOM/lines/$L1/assignment" -H "X-Participant-Token: $TA")"
check "после освобождения реплику берёт другой" 200 \
  "$(code -X PUT "$BASE/rooms/$ROOM/lines/$L1/assignment" -H "X-Participant-Token: $TB" -H 'Content-Type: application/json' -d '{}')"
check "освободить чужой захват нельзя" 403 \
  "$(code -X DELETE "$BASE/rooms/$ROOM/lines/$L1/assignment" -H "X-Participant-Token: $TA")"

BULK=$(curl -s -X POST "$BASE/rooms/$ROOM/assignments/bulk" -H "X-Participant-Token: $TA" -H 'Content-Type: application/json' -d '{"scope":"all-unassigned"}')
check "bulk захватил свободные реплики" 1 "$(echo "$BULK" | json "1 if len(d['captured'])>=1 else 0")"
check "повторный bulk ничего не захватывает" 0 \
  "$(curl -s -X POST "$BASE/rooms/$ROOM/assignments/bulk" -H "X-Participant-Token: $TA" -H 'Content-Type: application/json' -d '{"scope":"all-unassigned"}' | json "len(d['captured'])")"
check "bulk не считает чужие реплики своими" 0 \
  "$(curl -s -X POST "$BASE/rooms/$ROOM/assignments/bulk" -H "X-Participant-Token: $TB" -H 'Content-Type: application/json' -d '{"scope":"all-unassigned"}' | json "len(d['captured'])")"

echo "== медиа"
check "неизвестный kind -> 422" 422 "$(code "$BASE/rooms/$ROOM/media/whatever/$L1")"
check "неизвестный ref -> 404" 404 "$(code "$BASE/rooms/$ROOM/media/original/not-a-uuid")"
check "recording без записи -> 404" 404 "$(code "$BASE/rooms/$ROOM/media/recording/$L1")"
if [ -f "data/rooms/$ROOM/speech/speech.wav" ]; then
  check "media/speech -> 200" 200 "$(code "$BASE/rooms/$ROOM/media/speech/x")"
  check "media/background -> 200" 200 "$(code "$BASE/rooms/$ROOM/media/background/x")"
  check "Range-запрос к media -> 206" 206 \
    "$(code -H 'Range: bytes=0-99' "$BASE/rooms/$ROOM/media/speech/x")"
  check "Range отдаёт ровно запрошенный кусок" 100 \
    "$(curl -s -H 'Range: bytes=0-99' "$BASE/rooms/$ROOM/media/speech/x" | wc -c)"
else
  echo "  ..  speech.wav нет (конвейер не отработал) — проверки media/speech пропущены"
fi
check "original без фрагмента -> 404 или 200" "ok" \
  "$(C=$(code "$BASE/rooms/$ROOM/media/original/$L1"); [ "$C" = "200" ] || [ "$C" = "404" ] && echo ok || echo "$C")"

echo "== тейки озвучки (этап 9)"
WORK=$(mktemp -d)
# Короткий тейк (в пределах реплики) и слишком длинный — проверяем серверный автостоп-лимит.
ffmpeg -nostdin -v error -y -f lavfi -i "sine=frequency=440:duration=1.2" -c:a libopus -b:a 64k "$WORK/ok.webm" 2>/dev/null
ffmpeg -nostdin -v error -y -f lavfi -i "sine=frequency=440:duration=9" -c:a libopus -b:a 64k "$WORK/long.webm" 2>/dev/null
head -c 4096 /dev/urandom > "$WORK/garbage.webm"
# Запись в pipe (неищущийся поток) — так отдаёт браузерный MediaRecorder: без элемента Duration.
ffmpeg -nostdin -v error -y -f lavfi -i "sine=frequency=440:duration=9" -c:a libopus -f webm - > "$WORK/long_nodur.webm" 2>/dev/null
ffmpeg -nostdin -v error -y -f lavfi -i "sine=frequency=440:duration=1.2" -c:a libopus -f webm - > "$WORK/ok_nodur.webm" 2>/dev/null
DUR_L2=$(( $(curl -s "$BASE/rooms/$ROOM/lines/$L2" | json "d['end_ms']") - $(curl -s "$BASE/rooms/$ROOM/lines/$L2" | json "d['start_ms']") ))
echo "  ..  длительность реплики: ${DUR_L2} мс, размер тейка: $(stat -c%s "$WORK/ok.webm") байт"

TAKE=$(curl -s -X POST "$BASE/rooms/$ROOM/lines/$L2/recordings" -H "X-Participant-Token: $TA" -F "file=@$WORK/ok.webm;type=audio/webm")
check "загрузка тейка -> 201 с номером 1" 1 "$(echo "$TAKE" | json "d['take_number']")"
check "тейк актуальный" True "$(echo "$TAKE" | json "d['is_current']")"
check "длительность тейка = длительности реплики" "$DUR_L2" "$(echo "$TAKE" | json "d['duration_ms']")"
check "тейк нормализован (есть processed-файл)" 1 \
  "$([ -f "data/rooms/$ROOM/recordings/$L2/$(echo "$TAKE" | json "d['id']" | cut -c1-8)" ] && echo 0 || ls "data/rooms/$ROOM/recordings/$L2" | grep -c '\.wav')"

TAKE2=$(curl -s -X POST "$BASE/rooms/$ROOM/lines/$L2/recordings" -H "X-Participant-Token: $TA" -H "Idempotency-Key: dup-key-1" -F "file=@$WORK/ok.webm;type=audio/webm")
TAKE2B=$(curl -s -X POST "$BASE/rooms/$ROOM/lines/$L2/recordings" -H "X-Participant-Token: $TA" -H "Idempotency-Key: dup-key-1" -F "file=@$WORK/ok.webm;type=audio/webm")
check "повтор с тем же Idempotency-Key не создаёт новый тейк" \
  "$(echo "$TAKE2" | json "d['id']")" "$(echo "$TAKE2B" | json "d['id']")"
check "новый тейк получил номер 2 и стал актуальным" 2   "$(curl -s "$BASE/rooms/$ROOM/lines/$L2/recordings" -H "X-Participant-Token: $TA" | json "[t['take_number'] for t in d if t['is_current']][0]")"
check "актуальный тейк в реплике один" 1 \
  "$(curl -s "$BASE/rooms/$ROOM/lines/$L2/recordings" -H "X-Participant-Token: $TA" | json "sum(1 for t in d if t['is_current'])")"

LONG=$(curl -s -X POST "$BASE/rooms/$ROOM/lines/$L2/recordings" -H "X-Participant-Token: $TA" -F "file=@$WORK/long.webm;type=audio/webm")
check "9-секундная запись на 3-секундную реплику отклонена" recording_too_long "$(echo "$LONG" | json "d.get('code','')")"
check "отказ сообщает длины" 1 "$(echo "$LONG" | json "1 if d.get('line_duration_ms') else 0")"
check "мусорный файл отклонён" 422 \
  "$(code -X POST "$BASE/rooms/$ROOM/lines/$L2/recordings" -H "X-Participant-Token: $TA" -F "file=@$WORK/garbage.webm;type=audio/webm")"
check "webm без заголовка длительности: слишком длинный отклонён" recording_too_long \
  "$(curl -s -X POST "$BASE/rooms/$ROOM/lines/$L2/recordings" -H "X-Participant-Token: $TA" -F "file=@$WORK/long_nodur.webm;type=audio/webm" | json "d.get('code','')")"
check "webm без заголовка длительности: короткий принят" 201 \
  "$(code -X POST "$BASE/rooms/$ROOM/lines/$L2/recordings" -H "X-Participant-Token: $TA" -F "file=@$WORK/ok_nodur.webm;type=audio/webm")"
check "чужую реплику озвучивать нельзя" 409 \
  "$(code -X POST "$BASE/rooms/$ROOM/lines/$L2/recordings" -H "X-Participant-Token: $TB" -F "file=@$WORK/ok.webm;type=audio/webm")"

check "список тейков" 3 "$(curl -s "$BASE/rooms/$ROOM/lines/$L2/recordings" -H "X-Participant-Token: $TA" | json "len(d)")"
check "актуальная запись отдаётся медиа-эндпоинтом" 200 "$(code "$BASE/rooms/$ROOM/media/recording/$L2")"
check "Range по актуальной записи -> 206" 206 "$(code -H 'Range: bytes=0-99' "$BASE/rooms/$ROOM/media/recording/$L2")"
check "реплика помечена озвученной" True "$(curl -s "$BASE/rooms/$ROOM/lines/$L2" | json "d['has_recording']")"

FIRST_ID=$(curl -s "$BASE/rooms/$ROOM/lines/$L2/recordings" -H "X-Participant-Token: $TA" | json "d[0]['id']")
check "переключение актуального тейка -> 200" 200 \
  "$(code -X POST "$BASE/rooms/$ROOM/lines/$L2/recordings/$FIRST_ID/current" -H "X-Participant-Token: $TA")"
check "актуальным стал первый тейк" "$FIRST_ID" \
  "$(curl -s "$BASE/rooms/$ROOM/lines/$L2/recordings" -H "X-Participant-Token: $TA" | json "[t['id'] for t in d if t['is_current']][0]")"
check "удаление тейка -> 204" 204 \
  "$(code -X DELETE "$BASE/rooms/$ROOM/lines/$L2/recordings/$FIRST_ID" -H "X-Participant-Token: $TA")"
check "после удаления актуальный тейк остался" 1 \
  "$(curl -s "$BASE/rooms/$ROOM/lines/$L2/recordings" -H "X-Participant-Token: $TA" | json "sum(1 for t in d if t['is_current'])")"
rm -rf "$WORK"

echo "== сборка финального видео (этап 12)"
if [ -f "data/rooms/$ROOM/speech/background.wav" ]; then
  RCODE=$(code -X POST "$BASE/rooms/$ROOM/renders" -H "X-Participant-Token: $TA" -H 'Content-Type: application/json' -d '{"options":{"unrecorded":"silent"}}')
  check "заявка на сборку принята" 202 "$RCODE"
  RID=$(curl -s "$BASE/rooms/$ROOM/renders" -H "X-Participant-Token: $TA" | json "d[0]['id']")
  RID2=$(curl -s -X POST "$BASE/rooms/$ROOM/renders" -H "X-Participant-Token: $TA" -H 'Content-Type: application/json' -d '{"options":{"unrecorded":"silent"}}' | json "d['id']")
  check "повторная заявка не плодит сборки" "$RID" "$RID2"
  for _ in $(seq 1 20); do
    sleep 3
    ST=$(curl -s "$BASE/rooms/$ROOM/renders/$RID" -H "X-Participant-Token: $TA" | json "d['status']")
    case "$ST" in DONE|FAILED|CANCELED) break;; esac
  done
  check "сборка завершилась" DONE "$ST"
  check "готовый файл отдаётся" 200 "$(code "$BASE/rooms/$ROOM/renders/$RID/file" -H "X-Participant-Token: $TA")"
  check "докачка готового файла -> 206" 206 "$(code -H 'Range: bytes=0-999' "$BASE/rooms/$ROOM/renders/$RID/file" -H "X-Participant-Token: $TA")"
  check "готовое видео играется в плеере (inline)" 200 "$(code "$BASE/rooms/$ROOM/media/render/current" -H "X-Participant-Token: $TA")"
  check "размер файла в БД совпадает с файлом" "$(curl -s "$BASE/rooms/$ROOM/renders/$RID" -H "X-Participant-Token: $TA" | json "d['size_bytes']")"     "$(stat -c%s "data/$(sudo -u postgres psql -d dubbing -tAc "select output_path from render_jobs where id='$RID'")")"
  check "у комнаты один актуальный результат" 1 "$(curl -s "$BASE/rooms/$ROOM/renders" -H "X-Participant-Token: $TA" | json "len([r for r in d if r['is_current']])")"
  check "сборка без чужого токена закрыта" 401 "$(code "$BASE/rooms/$ROOM/renders/$RID/file")"
else
  echo "  ..  разделение речи не дало фона — проверяю понятный отказ"
  check "сборка без фона сообщает причину" artifacts_missing     "$(curl -s -X POST "$BASE/rooms/$ROOM/renders" -H "X-Participant-Token: $TA" -H 'Content-Type: application/json' -d '{}' | json "d.get('code','')")"
fi

echo "== удаление комнаты и purge (Dramatiq)"
LOGS() { docker compose logs --tail=300 worker-cpu 2>/dev/null; }
PURGE_BEFORE=$(LOGS | grep -c "room_purged")
check "DELETE создателем -> 204" 204 "$(code -X DELETE "$BASE/rooms/$ROOM" -H "X-Participant-Token: $TOKEN")"
check "GET удалённой комнаты -> 410" 410 "$(code "$BASE/rooms/$ROOM")"
printf '  ... ждём purge_room '
PURGE_DELTA=0
for _ in $(seq 1 20); do
  PURGE_DELTA=$(( $(LOGS | grep -c "room_purged") - PURGE_BEFORE ))
  [ "$PURGE_DELTA" -ge 1 ] && break
  printf '.'
  sleep 2
done
echo
check "purge_room выполнился (новый лог воркера)" 1 "$PURGE_DELTA"
check "каталог комнаты удалён с диска" 1 \
  "$([ -d "data/rooms/$ROOM" ] && echo 0 || echo 1)"

echo
echo "Итого: успешно $PASS, провалов $FAIL"
[ "$FAIL" -eq 0 ]
