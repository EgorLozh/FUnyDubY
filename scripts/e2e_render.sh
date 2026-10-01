#!/usr/bin/env bash
# Сквозной сценарий сборки: комната -> клип -> конвейер -> реплики -> запись -> готовое видео.
#
# Проверяет не «эндпоинт ответил 200», а результат: итоговый файл той же длительности, что исходник,
# и в окне озвученной реплики звучит именно записанный тейк (корреляция), а не оригинальный голос.
#
# Запуск: bash scripts/e2e_render.sh [/tmp/testclip/clip.mp4] [http://127.0.0.1:8090/api]
set -uo pipefail

CLIP="${1:-/tmp/testclip/clip.mp4}"
BASE="${2:-http://127.0.0.1:8090/api}"
TIMEOUT_S="${3:-900}"
PASS=0
FAIL=0

json() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)" 2>/dev/null; }
ok() { PASS=$((PASS + 1)); printf '  ok   %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf '  FAIL %s (ждали «%s», получили «%s»)\n' "$1" "$2" "$3"; }
check() { [ "$2" = "$3" ] && ok "$1" || bad "$1" "$2" "$3"; }

[ -f "$CLIP" ] || { echo "Нет клипа: $CLIP (сначала bash scripts/make_test_clip.sh)"; exit 1; }

echo "== комната и загрузка клипа"
RESP=$(curl -s -X POST "$BASE/rooms" -H 'Content-Type: application/json' \
  -d '{"title":"E2E сборка","display_name":"Егор"}')
ROOM=$(echo "$RESP" | json "d['room']['id']")
TOKEN=$(echo "$RESP" | json "d['token']")
[ -n "$ROOM" ] || { echo "комната не создалась"; exit 1; }
echo "  ..  комната $ROOM"

curl -s -X POST "$BASE/rooms/$ROOM/video" -H "X-Participant-Token: $TOKEN" \
  -F "file=@$CLIP;type=video/mp4" > /dev/null
JOB=$(curl -s "$BASE/rooms/$ROOM/jobs" -H "X-Participant-Token: $TOKEN" | json "d[0]['id']")
echo "  ..  джоб конвейера $JOB — жду обработку"
for _ in $(seq 1 $((TIMEOUT_S / 5))); do
  sleep 5
  ST=$(curl -s "$BASE/rooms/$ROOM/jobs/$JOB" -H "X-Participant-Token: $TOKEN" | json "d['status']")
  case "$ST" in DONE|FAILED) break;; esac
done
check "конвейер дошёл до конца" DONE "$ST"

LINES=$(curl -s "$BASE/rooms/$ROOM/lines" -H "X-Participant-Token: $TOKEN")
COUNT=$(echo "$LINES" | json "len(d)")
check "реплики нарезаны" 1 "$([ "${COUNT:-0}" -gt 0 ] && echo 1 || echo 0)"
LINE=$(echo "$LINES" | json "d[0]['id']")
START=$(echo "$LINES" | json "d[0]['start_ms']")
END=$(echo "$LINES" | json "d[0]['end_ms']")
DUR=$((END - START))
echo "  ..  реплика ${START}-${END} мс (${DUR} мс), текст: $(echo "$LINES" | json "d[0]['text'][:60]")"

echo "== участник берёт реплику и записывает реплику голосом"
curl -s -X POST "$BASE/rooms/$ROOM/lines/$LINE/assign" -H "X-Participant-Token: $TOKEN" > /dev/null
WORK=$(mktemp -d)
# Тейк: тон 220 Гц длиной ровно в реплику (в pipe, без заголовка Duration — как отдаёт браузер)
ffmpeg -nostdin -v error -y -f lavfi -i "sine=frequency=220:duration=$(echo "$DUR" | awk '{printf "%.3f", $1/1000}')" \
  -c:a libopus -f webm - > "$WORK/take.webm" 2>/dev/null
check "запись принята" 201 \
  "$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/rooms/$ROOM/lines/$LINE/recordings" \
     -H "X-Participant-Token: $TOKEN" -F "file=@$WORK/take.webm;type=audio/webm")"

echo "== сборка итогового видео"
curl -s -X POST "$BASE/rooms/$ROOM/renders" -H "X-Participant-Token: $TOKEN" \
  -H 'Content-Type: application/json' -d '{"options":{"unrecorded":"silent"}}' > /dev/null
RID=$(curl -s "$BASE/rooms/$ROOM/renders" -H "X-Participant-Token: $TOKEN" | json "d[0]['id']")
for _ in $(seq 1 40); do
  sleep 3
  RST=$(curl -s "$BASE/rooms/$ROOM/renders/$RID" -H "X-Participant-Token: $TOKEN")
  RSTATUS=$(echo "$RST" | json "d['status']")
  case "$RSTATUS" in DONE|FAILED|CANCELED) break;; esac
done
check "сборка завершилась" DONE "$RSTATUS"
check "тейк попал в микс" "$(echo "$RST" | json "d['metrics']['used_takes']")" "1"

OUT="$WORK/final.mp4"
mkdir -p "$WORK/cmp"
curl -s -o "$OUT" "$BASE/rooms/$ROOM/renders/$RID/file" -H "X-Participant-Token: $TOKEN"
check "файл скачался непустым" 1 "$([ -s "$OUT" ] && echo 1 || echo 0)"
check "длительность файла = длительности исходника" \
  "$(ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "$OUT" | cut -d. -f1)" \
  "$(ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "$CLIP" | cut -d. -f1)"
check "картинка скопирована без перекодирования" h264 \
  "$(ffprobe -v error -select_streams v:0 -show_entries stream=codec_name -of default=noprint_wrappers=1:nokey=1 "$OUT")"

echo "== в окне реплики звучит записанный тейк, а не оригинальный голос"
SEC=$(awk -v ms="$START" 'BEGIN{printf "%.3f", ms/1000}')
LEN=$(awk -v ms="$DUR" 'BEGIN{printf "%.3f", ms/1000}')
ffmpeg -nostdin -v error -y -ss "$SEC" -t "$LEN" -i "$OUT" -ac 1 -ar 48000 "$WORK/cmp/win.wav"
ffmpeg -nostdin -v error -y -ss "$SEC" -t "$LEN" -i "$CLIP" -ac 1 -ar 48000 "$WORK/cmp/win_src.wav"
# Эталон — сам тейк, который загрузили (сервер отдаёт нормализованный файл реплики)
curl -s -o "$WORK/cmp/take.wav" "$BASE/rooms/$ROOM/media/recording/$LINE" -H "X-Participant-Token: $TOKEN"

# Считаем в том же контейнере, где лежат инструменты обработки
docker compose cp "$WORK/cmp" api:/tmp >/dev/null 2>&1
CORRS=$(docker compose exec -T api python -c "
import numpy as np, soundfile as sf
def load(p):
    d, r = sf.read(p, dtype='float32')
    return d.mean(axis=1) if d.ndim > 1 else d
def corr(a, b):
    n = min(len(a), len(b)); a, b = a[:n] - a[:n].mean(), b[:n] - b[:n].mean()
    d = np.linalg.norm(a) * np.linalg.norm(b)
    return float(np.dot(a, b) / d) if d else 0.0
win, src, take = load('/tmp/cmp/win.wav'), load('/tmp/cmp/win_src.wav'), load('/tmp/cmp/take.wav')
print(f'{corr(win, take):.3f} {corr(win, src):.3f}')
" 2>/dev/null | tr -d '')
WITH_TAKE=$(echo "$CORRS" | awk '{print $1}')
WITH_SRC=$(echo "$CORRS" | awk '{print $2}')
echo "  ..  корреляция окна: с моим тейком ${WITH_TAKE}, с оригинальной речью ${WITH_SRC}"
check "в окне реплики звучит записанный тейк" 1   "$(awk -v c="${WITH_TAKE:-0}" 'BEGIN{print (c+0 > 0.9) ? 1 : 0}')"
check "оригинальный голос в этом окне не слышен" 1   "$(awk -v c="${WITH_SRC:-1}" 'BEGIN{print (c+0 < 0.2) ? 1 : 0}')"

echo "== уборка"
curl -s -X DELETE "$BASE/rooms/$ROOM" -H "X-Participant-Token: $TOKEN" > /dev/null
rm -rf "$WORK"

echo
echo "Итого: успешно $PASS, провалов $FAIL"
[ "$FAIL" -eq 0 ]
