#!/usr/bin/env bash
# Сравнение доступных в контейнере шумодавов на одном и том же сигнале.
set -u
cd ~/FUnyDubY
echo "== какие шумодавы есть в ffmpeg:"
docker compose exec -T worker-gpu bash -lc 'ffmpeg -hide_banner -filters 2>/dev/null | grep -E "arnndn|afftdn|anlmdn|highpass" | sed "s/^/  /"'

echo "== сравнение на синтетике (тон 1 с + чистый шум 1 с)"
docker compose exec -T worker-gpu python3 -c "
import math, array, subprocess

def seg_db(path, start_s, dur_s):
    raw = subprocess.run(['ffmpeg','-hide_banner','-v','error','-ss',str(start_s),'-t',str(dur_s),'-i',path,'-f','f32le','-'], capture_output=True).stdout
    d = array.array('f'); d.frombytes(raw[:len(raw)//4*4])
    s = sum(float(x)*float(x) for x in d)/max(1,len(d))
    return 10*math.log10(max(s,1e-12))

def measure(name, af):
    out = '/tmp/cmp_%s.wav' % name.replace(' ', '_').replace('=', '').replace(':','_')[:40]
    r = subprocess.run(['ffmpeg','-hide_banner','-v','error','-y','-i','/tmp/sig_then_noise.wav','-af',af,'-ar','48000','-ac','1','-c:a','pcm_s16le',out], capture_output=True)
    if r.returncode != 0:
        print('  %-34s недоступен' % name); return
    sig, noise = seg_db(out, 0.2, 0.6), seg_db(out, 1.2, 0.6)
    print('  %-34s голос %+.1f дБ, шум тише на %.1f дБ' % (name, sig - base[0], base[1] - noise))

base = (seg_db('/tmp/sig_then_noise.wav', 0.2, 0.6), seg_db('/tmp/sig_then_noise.wav', 1.2, 0.6))
print('  исходник: голос %.1f дБ, шум %.1f дБ' % base)
measure('highpass+afftdn nr=12', 'highpass=f=70,afftdn=nr=12:nf=-45:tn=1')
measure('highpass+afftdn nr=30', 'highpass=f=70,afftdn=nr=30:nf=-60')
measure('anlmdn (немного)', 'anlmdn=s=4:p=0.001:r=0.001:m=10')
measure('anlmdn (сильнее)', 'anlmdn=s=7:p=0.003:r=0.003:m=15')
measure('anlmdn + afftdn', 'anlmdn=s=7:p=0.002:r=0.002:m=15,afftdn=nr=12:nf=-45')
" 2>&1 | tail -10
