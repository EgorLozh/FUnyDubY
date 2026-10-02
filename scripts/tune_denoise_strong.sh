#!/usr/bin/env bash
# Насколько сильно можно давить шум, прежде чем начнёт страдать голос.
# Меряем: шум в паузах (тише = лучше) и отклонение обработанного голоса от ЧИСТОЙ записи.
set -u
cd ~/FUnyDubY
docker compose exec -T worker-gpu python3 -c "
import math, array, subprocess, sys
sys.path.insert(0, '/srv/app')

def load(path):
    raw = subprocess.run(['ffmpeg','-hide_banner','-v','error','-i',path,'-f','f32le','-'], capture_output=True).stdout
    d = array.array('f'); d.frombytes(raw[:len(raw)//4*4]); return d

def rms(a):
    s = sum(float(x)*float(x) for x in a)/max(1,len(a)); return 10*math.log10(max(s,1e-12))

def frames(d, size=4800):
    return [d[i:i+size] for i in range(0, len(d)-size, size)]

def parts(d):
    fs = sorted(frames(d), key=rms); k = max(1, len(fs)//10)
    return sum(rms(f) for f in fs[-k:])/k, sum(rms(f) for f in fs[:k])/k

clean = load('/tmp/real_speech.wav')
noisy = load('/tmp/real_noisy.wav')
sig_ref, quiet_ref = parts(clean)
n = min(len(clean), len(noisy))

def run(name, af):
    out = '/tmp/strong_%s.wav' % name.replace(' ', '_').replace('=', '').replace(':', '').replace(',', '')[:36]
    r = subprocess.run(['ffmpeg','-hide_banner','-v','error','-y','-i','/tmp/real_noisy.wav','-af',af,'-ar','48000','-ac','1','-c:a','pcm_s16le',out], capture_output=True)
    if r.returncode != 0:
        print('  %-24s фильтр не принят' % name); return
    d = load(out); m = min(len(d), len(clean))
    # корреляция с чистой речью: показывает, сохранился ли голос как таковой
    import statistics
    xs, ys = d[:m], clean[:m]
    mx, my = sum(xs)/m, sum(ys)/m
    cov = sum((xs[i]-mx)*(ys[i]-my) for i in range(0, m, 11))
    vx = sum((xs[i]-mx)**2 for i in range(0, m, 11)); vy = sum((ys[i]-my)**2 for i in range(0, m, 11))
    corr = cov/((vx*vy) ** 0.5) if vx > 0 and vy > 0 else 0
    sig, quiet = parts(d)
    print('  %-24s голос %+.1f дБ, шум в паузах %+.1f дБ к идеалу, схожесть с чистым голосом %.3f'
          % (name, sig - sig_ref, quiet - quiet_ref, corr))

sig_n, quiet_n = parts(noisy)
print('  %-24s голос %+.1f дБ, шум в паузах %+.1f дБ к идеалу' % ('без обработки', sig_n - sig_ref, quiet_n - quiet_ref))
run('текущий', 'highpass=f=70,anlmdn=s=7:p=0.002:r=0.002:m=15')
run('сильнее', 'highpass=f=70,anlmdn=s=9:p=0.004:r=0.004:m=21')
run('очень сильный', 'highpass=f=70,anlmdn=s=10:p=0.01:r=0.01:m=27')
run('двойной проход', 'highpass=f=70,anlmdn=s=9:p=0.004:r=0.004:m=21,anlmdn=s=9:p=0.004:r=0.004:m=21')
run('сильнее + гейт', 'highpass=f=70,anlmdn=s=9:p=0.004:r=0.004:m=21,afftdn=nr=12:nf=-60')
run('rnnoise+сильнее', 'highpass=f=70,arnndn=m=/data/models/rnnoise/sh.rnnn,anlmdn=s=9:p=0.004:r=0.004:m=21')
" 2>&1 | tail -10
