#!/usr/bin/env bash
# Проверка «сильного» шумодава на настоящей речи (цепочка берётся из кода, не из скрипта).
set -u
cd ~/FUnyDubY
docker compose exec -T worker-gpu python3 - <<'PY'
import sys, math, array, subprocess
sys.path.insert(0, '/srv/app')
from app.video.ffmpeg import _denoise_chain

def load(path):
    raw = subprocess.run(['ffmpeg','-hide_banner','-v','error','-i',path,'-f','f32le','-'], capture_output=True).stdout
    d = array.array('f'); d.frombytes(raw[:len(raw)//4*4]); return d

def rms(a):
    s = sum(float(x)*float(x) for x in a)/max(1,len(a)); return 10*math.log10(max(s,1e-12))

def parts(d, size=4800):
    fs = sorted([d[i:i+size] for i in range(0, len(d)-size, size)], key=rms)
    k = max(1, len(fs)//10)
    return sum(rms(f) for f in fs[-k:])/k, sum(rms(f) for f in fs[:k])/k

chain = _denoise_chain('strong')
print('  цепочка:', ', '.join(chain))
subprocess.run(['ffmpeg','-hide_banner','-v','error','-y','-i','/tmp/real_noisy.wav','-af',','.join(chain),'-ar','48000','-ac','1','-c:a','pcm_s16le','/tmp/strong_check.wav'], check=True)

c = parts(load('/tmp/real_speech.wav'))
n = parts(load('/tmp/real_noisy.wav'))
s = parts(load('/tmp/strong_check.wav'))
print('  чистая речь:    голос %.1f дБ, паузы %.1f дБ' % c)
print('  с шумом:        голос %.1f дБ, паузы %.1f дБ' % n)
print('  после сильного: голос %.1f дБ, паузы %.1f дБ' % s)
print('  шум в паузах убран на %.1f дБ, голос изменился на %+.1f дБ' % (n[1]-s[1], s[0]-n[0]))
PY
