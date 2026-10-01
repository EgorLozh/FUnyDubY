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

  MUSIC_URL=$(curl -s "https://commons.wikimedia.org/w/api.php?action=query&titles=File:PhiladelphiaSymphonyOrchestra-DanseMacabre.ogg&prop=imageinfo&iiprop=url&format=json" \
    | python3 -c "import json,sys; d=json.load(sys.stdin); print(list(d['query']['pages'].values())[0]['imageinfo'][0]['url'])")
  echo "музыка: $MUSIC_URL"
  curl -s -o music.ogg "$MUSIC_URL"

  echo "== сборка клипа: спикер 1 (0.5-12 c), спикер 2 (11.5-24 c, с наложением), музыка на фоне =="
  ffmpeg -nostdin -v error -y \
    -f lavfi -i "testsrc=size=640x360:rate=10" \
    -i spk1.flac -i spk2.flac -i spk3.flac -i music.ogg \
    -filter_complex "\
[1:a]atrim=0:11.5,adelay=500,volume=1.8,aformat=channel_layouts=stereo[s1]; \
[2:a]atrim=0:12.5,adelay=11500,volume=1.8,aformat=channel_layouts=stereo[s2]; \
[3:a]atrim=0:6,adelay=26000,volume=1.8,aformat=channel_layouts=stereo[s3]; \
[4:a]atrim=0:33,volume=0.35,aformat=channel_layouts=stereo[mus]; \
[s1][s2][s3][mus]amix=inputs=4:normalize=0:duration=longest[aud]" \
    -map 0:v -map "[aud]" -t 33 -shortest \
    -c:v libx264 -preset veryfast -pix_fmt yuv420p -c:a aac -b:a 128k -ar 44100 \
    clip.mp4
fi

echo "== готово: $OUT_DIR/clip.mp4 =="
ffprobe -v error -show_entries format=duration,size -of default=noprint_wrappers=1 clip.mp4
