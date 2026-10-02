#!/usr/bin/env bash
# Сравнение шумодавов на настоящей речи: голос должен остаться как в чистой записи,
# а шум в паузах — уйти. Считаем и то, и другое.
set -u
cd ~/FUnyDubY
docker compose exec -T worker-gpu python3 -c "
import math, array, subprocess, os

def samples(path):
    raw = subprocess.run(['ffmpeg','-hide_banner','-v','error','-i',path,'-f','f32le','-'], capture_output=True).stdout
    d = array.array('f'); d.frombytes(raw[:len(raw)//4*4])
    return d

def rms_db(a):
    s = sum(float(x)*float(x) for x in a)/max(1,len(a))
    return 10*math.log10(max(s,1e-12))

def frames(d, size=4800):
    return [d[i:i+size] for i in range(0, len(d)-size, size)]

def parts(d):
    fs = sorted(frames(d), key=rms_db)
    k = max(1, len(fs)//10)
    quiet = sum(rms_db(f) for f in fs[:k])/k
    loud = sum(rms_db(f) for f in fs[-k:])/k
    return loud, quiet

clean = samples('/tmp/real_speech.wav')
noisy = samples('/tmp/real_noisy.wav')
if len(clean) != len(noisy):
    n = min(len(clean), len(noisy)); clean, noisy = clean[:n], noisy[:n]

def measure(name, af, needs_model=False):
    out = '/tmp/cmp_%s.wav' % name.replace(' ', '_').replace('=', '').replace(':', '').replace('/', '')[:40]
    r = subprocess.run(['ffmpeg','-hide_banner','-v','error','-y','-i','/tmp/real_noisy.wav','-af',af,'-ar','48000','-ac','1','-c:a','pcm_s16le',out], capture_output=True)
    if r.returncode != 0:
        print('  %-16s недоступен' % name); return
    got = samples(out)
    m = min(len(got), len(clean))
    err = sum((got[i]-clean[i])**2 for i in range(0, m, 7))/max(1, len(range(0,m,7)))
    sig_got, quiet_got = parts(got)
    sig_ref, quiet_ref = parts(clean)
    print('  %-16s голос %+.1f дБ, паузы: шум %+.1f дБ к идеалу, расхождение с чистым голосом %.1f дБ'
          % (name, sig_got - sig_ref, quiet_got - quiet_ref, 10*math.log10(max(err,1e-12))))

sig_n, quiet_n = parts(noisy)
sig_r, quiet_r = parts(clean)
print('  %-16s голос %+.1f дБ, паузы: шум %+.1f дБ к идеалу' % ('(без обработки)', sig_n - sig_r, quiet_n - quiet_r))
measure('anlmdn (сейчас)', 'highpass=f=70,anlmdn=s=7:p=0.002:r=0.002:m=15')
for model in ('sh', 'bd', 'cb', 'lq', 'mp'):
    measure('arnndn %s' % model, 'highpass=f=70,arnndn=m=/data/models/rnnoise/%s.rnnn' % model)
measure('anlmdn+arnndn sh', 'highpass=f=70,anlmdn=s=7:p=0.002:r=0.002:m=15,arnndn=m=/data/models/rnnoise/sh.rnnn')
measure('anlmdn сил.', 'highpass=f=70,anlmdn=s=9:p=0.004:r=0.004:m=21')
" 2>&1 | tail -12
