#!/usr/bin/env bash
# Проверка шумоподавления и обрезки «разгона»: сигнал и шум меряются раздельно.
# По абсолютной громкости разницу не видно — loudnorm выравнивает уровень, поэтому смотрим
# на отношение сигнал/шум: тон в первой половине файла, чистый шум микрофона во второй.
set -u
cd ~/FUnyDubY

echo "== готовлю сигналы"
docker compose exec -T worker-gpu bash -lc '
set -e
cd /tmp
ffmpeg -hide_banner -loglevel error -y -f lavfi -i "sine=frequency=440:duration=1:volume=0.3" -f lavfi -i "anoisesrc=color=white:amplitude=0.006:duration=1" -filter_complex "[0:a][1:a]concat=n=2:v=0:a=1" -ar 48000 -ac 1 sig_then_noise.wav
ffmpeg -hide_banner -loglevel error -y -f lavfi -i "anoisesrc=color=white:amplitude=0.004:duration=3" -f lavfi -i "sine=frequency=440:duration=1.3:volume=0.3" -filter_complex "[0:a][1:a]concat=n=2:v=0:a=1" -ar 48000 -ac 1 with_lead.wav
' 2>&1 | tail -3

echo "== шумоподавление: тон 1 с, затем чистый шум 1 с"
docker compose exec -T worker-gpu python3 -c "
import sys, math, array, subprocess
sys.path.insert(0, '/srv/app')
from pathlib import Path
from app.video import ffmpeg

def seg_db(path, start_s, dur_s):
    raw = subprocess.run(['ffmpeg','-hide_banner','-v','error','-ss',str(start_s),'-t',str(dur_s),'-i',str(path),'-f','f32le','-'], capture_output=True).stdout
    d = array.array('f'); d.frombytes(raw[:len(raw)//4*4])
    s = sum(float(x)*float(x) for x in d)/max(1,len(d))
    return 10*math.log10(max(s,1e-12))

src = Path('/tmp/sig_then_noise.wav')
ffmpeg.normalize_recording(src, Path('/tmp/dn_off.wav'), 2000, -16, denoise=False)
ffmpeg.normalize_recording(src, Path('/tmp/dn_on.wav'), 2000, -16, denoise=True)
print('  исходник:        сигнал %.1f дБ, шум %.1f дБ, отношение %.1f дБ' % (seg_db(src,0.2,0.6), seg_db(src,1.2,0.6), seg_db(src,0.2,0.6)-seg_db(src,1.2,0.6)))
for name, path in (('без обработки ', '/tmp/dn_off.wav'), ('с обработкой  ', '/tmp/dn_on.wav')):
    sig, noise = seg_db(path, 0.2, 0.6), seg_db(path, 1.2, 0.6)
    print('  %s: сигнал %.1f дБ, шум %.1f дБ, отношение %.1f дБ' % (name, sig, noise, sig-noise))
" 2>&1 | tail -5

echo "== обрезка разгона (3 с шума + 1.3 с тона)"
docker compose exec -T worker-gpu python3 -c "
import sys, array, subprocess, math
sys.path.insert(0, '/srv/app')
from pathlib import Path
from app.video import ffmpeg

def windows(path, count=4):
    raw = subprocess.run(['ffmpeg','-hide_banner','-v','error','-i',str(path),'-f','f32le','-'], capture_output=True).stdout
    d = array.array('f'); d.frombytes(raw[:len(raw)//4*4])
    n = max(1, len(d)//count)
    return [round(10*math.log10(max(sum(float(x)*float(x) for x in d[i*n:(i+1)*n])/n,1e-12)),1) for i in range(count)], round(len(d)/48000, 2)

src = Path('/tmp/with_lead.wav')
print('  исходник:              ', windows(src))
ffmpeg.normalize_recording(src, Path('/tmp/lead_cut.wav'), 1300, -16, skip_ms=3000, denoise=False)
print('  после обрезки разгона: ', windows(Path('/tmp/lead_cut.wav')))
ffmpeg.normalize_recording(src, Path('/tmp/lead_cut_dn.wav'), 1300, -16, skip_ms=3000, denoise=True)
print('  с шумоподавлением:     ', windows(Path('/tmp/lead_cut_dn.wav')))
" 2>&1 | tail -4
