#!/usr/bin/env bash
# Приёмка на РЕАЛЬНОМ материале: фрагмент киноролика (~10 минут, речь + музыка + несколько голосов)
# проходит весь путь: конвейер -> правки -> запись участника -> сборка итогового видео.
#
# Что здесь проверяется (не «200 OK», а результат):
#   * сколько времени занимает каждый этап на живом железе;
#   * качество разделения: насколько фон «чист» в окнах речи (проекция фона на стем речи);
#   * диаризация на настоящем многоголосом диалоге, а не на трёх фрагментах одного голоса;
#   * итоговое видео: длительность исходника, картинка без перекодирования, в озвученной реплике
#     звучит запись участника, а не оригинальный голос.
#
# Запуск: bash scripts/acceptance_real_clip.sh [исходник] [смещение с] [длина с]
set -uo pipefail

SRC="${1:-/tmp/testclip/hgf.mp4}"
OFFSET="${2:-180}"
LENGTH="${3:-600}"
BASE="${4:-http://127.0.0.1:8090/api}"
# Если передан уже готовый фрагмент — работаем с ним как есть (иначе режем со смещением)
if [ -n "${1:-}" ] && [ "${1#/tmp/testclip/hgf}" != "$1" ]; then
  CLIP="/tmp/testclip/movie${LENGTH}.mp4"
else
  CLIP="${1:-/tmp/testclip/movie${LENGTH}.mp4}"
fi
PASS=0
FAIL=0

json() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)" 2>/dev/null; }
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
ok() { PASS=$((PASS + 1)); printf '  ok   %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf '  FAIL %s (ждали «%s», получили «%s»)\n' "$1" "$2" "$3"; }
check() { [ "$2" = "$3" ] && ok "$1" || bad "$1" "$2" "$3"; }
say() { printf '\n== %s\n' "$1"; }

say "фрагмент реального ролика"
if [ ! -f "$CLIP" ]; then
  [ -f "$SRC" ] || { echo "нет исходника $SRC"; exit 1; }
  ffmpeg -nostdin -v error -y -ss "$OFFSET" -t "$LENGTH" -i "$SRC" -c copy -avoid_negative_ts make_zero "$CLIP" || exit 1
fi
CLIP_DUR=$(ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "$CLIP" | cut -d. -f1)
CLIP_MB=$(du -m "$CLIP" | cut -f1)
CLIP_CODEC=$(ffprobe -v error -select_streams v:0 -show_entries stream=codec_name -of default=noprint_wrappers=1:nokey=1 "$CLIP")
printf '  ..  %s: %s с, %s МБ, видео %s\n' "$(basename "$CLIP")" "$CLIP_DUR" "$CLIP_MB" "$CLIP_CODEC"

say "комната и загрузка"
RESP=$(curl -s -X POST "$BASE/rooms" -H 'Content-Type: application/json' \
  -d '{"title":"Приёмка: реальный ролик","display_name":"Егор"}')
ROOM=$(echo "$RESP" | json "d['room']['id']")
TOKEN=$(echo "$RESP" | json "d['token']")
[ -n "$ROOM" ] || { echo "комната не создалась"; exit 1; }
printf '  ..  комната %s\n' "$ROOM"

UP=$(curl -s -o /dev/null -w '%{http_code}:%{time_total}' -X POST "$BASE/rooms/$ROOM/video" \
  -H "X-Participant-Token: $TOKEN" -F "file=@$CLIP;type=video/mp4")
printf '  ..  загрузка %s МБ: %s\n' "$CLIP_MB" "$UP"
JOB=$(curl -s "$BASE/rooms/$ROOM/jobs" -H "X-Participant-Token: $TOKEN" | json "d[0]['id']")

say "конвейер на живом железе (ожидание обработки)"
STARTED=$(date +%s)
for _ in $(seq 1 240); do
  sleep 5
  J=$(curl -s "$BASE/rooms/$ROOM/jobs/$JOB" -H "X-Participant-Token: $TOKEN")
  ST=$(echo "$J" | json "d['status']")
  case "$ST" in DONE|FAILED) break;; esac
  printf '\r  ..  %s, этап %s (%s%%)   ' "$ST" "$(echo "$J" | json "d.get('current_stage','-')")" "$(echo "$J" | json "d.get('progress',0)")"
done
TOTAL=$(( $(date +%s) - STARTED ))
echo
check "конвейер прошёл целиком" DONE "$ST"
printf '  ..  всего обработка: %s с на %s с видео\n' "$TOTAL" "$CLIP_DUR"

echo "  этапы:"
sudo -u postgres psql -d dubbing -tAc "
  select '    ' || s.stage || ': ' || coalesce(s.status,'') || ' ' || coalesce(s.duration_ms,0) || ' мс' ||
         coalesce(' | гейт ' || (s.metrics->>'speech_window_gain_db') || ' дБ', '') ||
         coalesce(' | утечка ' || (s.metrics->>'speech_leak_db') || ' дБ', '')
  from job_stages s where s.job_id='$JOB' order by s.started_at" 2>/dev/null

say "качество разделения и диаризация"
sudo -u postgres psql -d dubbing -tAc "
  select '  спикеров: ' || count(distinct speaker_key) || ' | реплик: ' || count(*) ||
         ' | озвучиваемых секунд: ' || round(sum(end_ms-start_ms)/1000.0, 1)
  from dialogue_lines where room_id='$ROOM'" 2>/dev/null
sudo -u postgres psql -d dubbing -tAc "
  select '  по голосам: ' || speaker_label || ' — ' || count(*) || ' реплик'
  from dialogue_lines where room_id='$ROOM' group by speaker_label order by count(*) desc" 2>/dev/null

docker compose exec -T api python -c "
import numpy as np, soundfile as sf, sys
sys.path.insert(0, '/srv/app')
from app.ml.audio_io import read
from app.ml.separation import speech_activity_mask
from pathlib import Path
base = Path('/data/rooms/$ROOM/speech')
mix = read(Path('/data/rooms/$ROOM/audio/mix_stereo.wav')) if Path('/data/rooms/$ROOM/audio/mix_stereo.wav').exists() else None
speech = read(base / 'speech.wav')
back = read(base / 'background.wav')
if mix is None:
    print('  (исходный микс не найден — гейт по стему)')
    mix = speech
mask = speech_activity_mask(mix, speech)
s = speech.mono().samples.astype(np.float64)
b = back.mono().samples.astype(np.float64)
n = min(len(s), len(b))
m = mask[:n] if len(mask) >= n else np.resize(mask, n)
sp, bg = s[:n][m], b[:n][m]
if len(sp) > 1000:
    proj = float(np.dot(bg, sp) / (np.dot(sp, sp) + 1e-12))
    leak = 20 * np.log10(abs(proj) + 1e-12)
    rms_s = 20 * np.log10(np.sqrt((sp ** 2).mean()) + 1e-12)
    rms_b = 20 * np.log10(np.sqrt((bg ** 2).mean()) + 1e-12)
    print(f'  окон речи: {100*m.sum()/len(m):.0f}% | речь {rms_s:.1f} дБ | фон в этих окнах {rms_b:.1f} дБ')
    print(f'  проекция фона на стем речи (утечка голоса): {leak:.1f} дБ')
" 2>&1 | tail -4

say "запись участника и сборка итогового видео"
LINE=$(curl -s "$BASE/rooms/$ROOM/lines" -H "X-Participant-Token: $TOKEN" | json "sorted([l for l in d if not l['is_short']], key=lambda l: -(l['end_ms']-l['start_ms']))[0]['id']")
LINEDATA=$(curl -s "$BASE/rooms/$ROOM/lines/$LINE" -H "X-Participant-Token: $TOKEN")
START_MS=$(echo "$LINEDATA" | json "d['start_ms']")
DUR_MS=$(( $(echo "$LINEDATA" | json "d['end_ms']") - START_MS ))
printf '  ..  озвучиваю реплику %s-%s мс (%s с)\n' "$START_MS" "$((START_MS + DUR_MS))" "$(awk -v ms="$DUR_MS" 'BEGIN{printf "%.1f", ms/1000}')"
WORK=$(mktemp -d)
ffmpeg -nostdin -v error -y -f lavfi -i "sine=frequency=440:duration=$(awk -v ms="$DUR_MS" 'BEGIN{printf "%.3f", ms/1000}')" \
  -c:a libopus -f webm - > "$WORK/take.webm" 2>/dev/null
curl -s -X POST "$BASE/rooms/$ROOM/lines/$LINE/assign" -H "X-Participant-Token: $TOKEN" > /dev/null
check "запись принята" 201 \
  "$(code -X POST "$BASE/rooms/$ROOM/lines/$LINE/recordings" -H "X-Participant-Token: $TOKEN" -F "file=@$WORK/take.webm;type=audio/webm")"

RSTART=$(date +%s)
curl -s -X POST "$BASE/rooms/$ROOM/renders" -H "X-Participant-Token: $TOKEN" \
  -H 'Content-Type: application/json' -d '{"options":{"unrecorded":"silent"}}' > /dev/null
RID=$(curl -s "$BASE/rooms/$ROOM/renders" -H "X-Participant-Token: $TOKEN" | json "d[0]['id']")
for _ in $(seq 1 120); do
  sleep 5
  RST=$(curl -s "$BASE/rooms/$ROOM/renders/$RID" -H "X-Participant-Token: $TOKEN")
  case "$(echo "$RST" | json "d['status']")" in DONE|FAILED|CANCELED) break;; esac
done
RTOTAL=$(( $(date +%s) - RSTART ))
check "сборка прошла" DONE "$(echo "$RST" | json "d['status']")"
printf '  ..  сборка заняла %s с (микс + кодирование)\n' "$RTOTAL"

OUT="$WORK/final.mp4"
curl -s -o "$OUT" "$BASE/rooms/$ROOM/renders/$RID/file" -H "X-Participant-Token: $TOKEN"
check "длительность итогового файла = исходнику" "$CLIP_DUR" \
  "$(ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "$OUT" | cut -d. -f1)"
check "видеокодек сохранён" "$CLIP_CODEC" \
  "$(ffprobe -v error -select_streams v:0 -show_entries stream=codec_name -of default=noprint_wrappers=1:nokey=1 "$OUT")"
printf '  ..  итог: %s МБ (исходник %s МБ)\n' "$(du -m "$OUT" | cut -f1)" "$CLIP_MB"

say "в озвученной реплике звучит запись участника"
mkdir -p "$WORK/cmp"
SEC=$(awk -v ms="$START_MS" 'BEGIN{printf "%.3f", ms/1000}')
LEN=$(awk -v ms="$DUR_MS" 'BEGIN{printf "%.3f", ms/1000}')
ffmpeg -nostdin -v error -y -ss "$SEC" -t "$LEN" -i "$OUT" -ac 1 -ar 48000 "$WORK/cmp/win.wav"
ffmpeg -nostdin -v error -y -ss "$SEC" -t "$LEN" -i "$CLIP" -ac 1 -ar 48000 "$WORK/cmp/win_src.wav"
curl -s -o "$WORK/cmp/take.wav" "$BASE/rooms/$ROOM/media/recording/$LINE" -H "X-Participant-Token: $TOKEN"
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
" 2>/dev/null | tr -d '\r')
WITH_TAKE=$(echo "$CORRS" | awk '{print $1}')
WITH_SRC=$(echo "$CORRS" | awk '{print $2}')
printf '  ..  корреляция: с записью участника %s, с оригиналом %s\n' "$WITH_TAKE" "$WITH_SRC"
check "речь участника на месте" 1 "$(awk -v c="${WITH_TAKE:-0}" 'BEGIN{print (c+0 > 0.9) ? 1 : 0}')"
check "оригинальный голос заменён" 1 "$(awk -v c="${WITH_SRC:-1}" 'BEGIN{print (c+0 < 0.2) ? 1 : 0}')"

say "диск и уборка"
docker compose exec -T api bash -lc "du -sh /data/rooms/$ROOM; du -sh /data/rooms/$ROOM/* 2>/dev/null | sort -h | tail -5" 2>/dev/null
rm -rf "$WORK"

echo
echo "Итого: успешно $PASS, провалов $FAIL"
[ "$FAIL" -eq 0 ]
