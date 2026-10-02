#!/usr/bin/env bash
# Подбор силы шумоподавления: сравниваем варианты afftdn по подавлению шума и влиянию на голос.
set -u
cd ~/FUnyDubY
docker compose exec -T worker-gpu python3 -c "
import sys, math, array, subprocess
from pathlib import Path

def seg_db(path, start_s, dur_s):
    raw = subprocess.run(['ffmpeg','-hide_banner','-v','error','-ss',str(start_s),'-t',str(dur_s),'-i',str(path),'-f','f32le','-'], capture_output=True).stdout
    d = array.array('f'); d.frombytes(raw[:len(raw)//4*4])
    s = sum(float(x)*float(x) for x in d)/max(1,len(d))
    return 10*math.log10(max(s,1e-12))

def measure(af, out):
    cmd = ['ffmpeg','-hide_banner','-v','error','-y','-i','/tmp/sig_then_noise.wav','-af',af,'-ar','48000','-ac','1','-c:a','pcm_s16le',out]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        return None
    sig, noise = seg_db(out, 0.2, 0.6), seg_db(out, 1.2, 0.6)
    return sig, noise

base = measure('anull', '/tmp/sweep_base.wav')
print('  без подавления:                 сигнал %.1f, шум %.1f, отношение %.1f дБ' % (base[0], base[1], base[0]-base[1]))
for nr in (12, 18, 24, 30):
    af = 'highpass=f=70,afftdn=nr=%d:nf=-45:tn=1' % nr
    res = measure(af, '/tmp/sweep_%d.wav' % nr)
    if res is None:
        print('  nr=%d: фильтр не принят' % nr); continue
    sig, noise = res
    print('  nr=%-3d: сигнал %.1f, шум %.1f, отношение %.1f дБ | шум тише на %.1f дБ, голос изменился на %+.1f дБ'
          % (nr, sig, noise, sig-noise, base[1]-noise, sig-base[0]))
" 2>&1 | tail -8
