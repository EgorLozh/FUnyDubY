#!/usr/bin/env bash
# Собирает тестовый ролик «два говорящих поверх музыки» — проверка ML-конвейера
# на материале, близком к реальному (речь + музыкальный фон + наложение речи).
#
# Источники: образцы речи LibriSpeech (HF CDN) и оркестровая запись из Wikimedia Commons
# (свободная лицензия). Результат: /tmp/testclip/clip.mp4
set -euo pipefail

OUT_DIR="${1:-/tmp/testclip}"
mkdir -p "$OUT_DIR"
cd "$OUT_DIR"

if [ ! -f clip.mp4 ]; then
  echo "== источники =="
  curl -s -o spk1.flac https://cdn-media.huggingface.co/speech_samples/sample1.flac
  curl -s -o spk2.flac https://cdn-media.huggingface.co/speech_samples/sample2.flac
  curl -s -o spk3.flac https://cdn-media.huggingface.co/speech_samples/sample3.flac 2>/dev/null || cp spk2.flac spk3.flac

  MUSIC_URL=$(curl -s -A "FUnyDubY-e2e/1.0 (test fixture)" "https://commons.wikimedia.org/w/api.php?action=query&titles=File:PhiladelphiaSymphonyOrchestra-DanseMacabre.ogg&prop=imageinfo&iiprop=url&format=json" \
    | python3 -c "import json,sys; d=json.load(sys.stdin); print(list(d['query']['pages'].values())[0]['imageinfo'][0]['url'])")
  echo "музыка: ${MUSIC_URL%%\?*}"
  # Wikimedia отдаёт 403 без внятного User-Agent — заголовок обязателен
  MUSIC_CODE=$(curl -s -A "FUnyDubY-e2e/1.0 (test fixture; https://example.invalid)" -w '%{http_code}' -o music.ogg "$MUSIC_URL")
  echo "скачивание музыки: HTTP $MUSIC_CODE, $(stat -c%s music.ogg 2>/dev/null || echo 0) байт"
  [ "$MUSIC_CODE" = "200" ] && [ "$(stat -c%s music.ogg 2>/dev/null || echo 0)" -gt 100000 ] || {
    echo "Не удалось скачать музыкальный фон"; exit 1; }

  echo "== сборка: речь (эталон) + музыка (эталон) + микс 48 кГц =="
  # Эталонные дорожки нужны приёмочному тесту разделения: по ним считается истинная утечка
  # речи в фон. Собираются тем же графом фильтров, что и микс, поэтому сэмпл-в-сэмпл совпадают.
  SPK="[1:a]atrim=0:11.5,adelay=500,volume=1.8,aformat=channel_layouts=stereo[s1]; \
[2:a]atrim=0:12.5,adelay=11500,volume=1.8,aformat=channel_layouts=stereo[s2]; \
[3:a]atrim=0:6,adelay=26000,volume=1.8,aformat=channel_layouts=stereo[s3]"
  MUS="[3:a]atrim=0:33,volume=0.35,aformat=channel_layouts=stereo[mus]"

  ffmpeg -nostdin -v error -y -i spk1.flac -i spk2.flac -i spk3.flac -i music.ogg \
    -filter_complex "$SPK;[s1][s2][s3]amix=inputs=3:normalize=0:duration=longest[sp]; \
[sp]apad=whole_dur=33,atrim=0:33,aformat=sample_fmts=s16:sample_rates=48000[out]" \
    -map "[out]" -ac 2 speech_truth.wav

  ffmpeg -nostdin -v error -y -i spk1.flac -i spk2.flac -i spk3.flac -i music.ogg \
    -filter_complex "$MUS;[mus]apad=whole_dur=33,atrim=0:33,aformat=sample_fmts=s16:sample_rates=48000[out]" \
    -map "[out]" -ac 2 music_truth.wav

  ffmpeg -nostdin -v error -y -i spk1.flac -i spk2.flac -i spk3.flac -i music.ogg \
    -filter_complex "$SPK;$MUS;[s1][s2][s3][mus]amix=inputs=4:normalize=0:duration=longest[aud]; \
[aud]apad=whole_dur=33,atrim=0:33,aformat=sample_fmts=s16:sample_rates=48000[out]" \
    -map "[out]" -ac 2 mix.wav

  echo "== видео из готового микса (без перекодирования аудио в графе) =="
  ffmpeg -nostdin -v error -y \
    -f lavfi -i "testsrc=size=640x360:rate=10" -i mix.wav \
    -map 0:v -map 1:a -t 33 -shortest \
    -c:v libx264 -preset veryfast -pix_fmt yuv420p -c:a aac -b:a 128k \
    clip.mp4
fi

echo "== готово: $OUT_DIR =="
for f in clip.mp4 speech_truth.wav music_truth.wav mix.wav; do
  [ -f "$f" ] && printf '  %-18s %s байт\n' "$f" "$(stat -c%s "$f")"
done
ffprobe -v error -show_entries format=duration,size -of default=noprint_wrappers=1 clip.mp4
