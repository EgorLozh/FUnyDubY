#!/usr/bin/env bash
# Проверка шумодава на НАСТОЯЩЕЙ речи: голос не должен пострадать, а шум — уйти.
set -u
cd ~/FUnyDubY
docker compose exec -T worker-gpu python3 -c "
import math, array, subprocess
from pathlib import Path

def samples(path):
    raw = subprocess.run(['ffmpeg','-hide_banner','-v','error','-i',path,'-f','f32le','-'], capture_output=True).stdout
    d = array.array('f'); d.frombytes(raw[:len(raw)//4*4])
    return d

def rms_db(d):
    s = sum(float(x)*float(x) for x in d)/max(1,len(d))
    return 10*math.log10(max(s,1e-12))

def framewise(d, frame=4800):
    return [rms_db(d[i:i+frame]) for i in range(0, len(d)-frame, frame)]

speech_path = '/data/rooms/y45qx4g5zay8dctjbexf/speech/speech.wav'
if not Path(speech_path).exists():
    print('  нет настоящей речи для проверки'); raise SystemExit

# берём 10 с речи, подмешиваем шум микрофона на реалистичном уровне (-45 дБ)
subprocess.run(['ffmpeg','-hide_banner','-v','error','-y','-i',speech_path,'-t','10','-ar','48000','-ac','1','-c:a','pcm_s16le','/tmp/real_speech.wav'], check=True)
subprocess.run(['ffmpeg','-hide_banner','-v','error','-y','-f','lavfi','-i','anoisesrc=color=white:amplitude=0.006:duration=10','-ar','48000','-ac','1','-c:a','pcm_s16le','/tmp/real_noise.wav'], check=True)
subprocess.run(['ffmpeg','-hide_banner','-v','error','-y','-i','/tmp/real_speech.wav','-i','/tmp/real_noise.wav','-filter_complex','[0:a][1:a]amix=inputs=2:duration=first','-c:a','pcm_s16le','/tmp/real_noisy.wav'], check=True)

def analyze(name, path):
    d = samples(path)
    frames = sorted(framewise(d))
    quiet = sum(frames[:max(1,len(frames)//10)])/max(1,len(frames)//10)   # тиша между словами
    loud = sum(frames[-max(1,len(frames)//10):])/max(1,len(frames)//10)   # громкая речь
    print('  %-22s речь %.1f дБ, паузы %.1f дБ, разница %.1f дБ' % (name, loud, quiet, loud-quiet))
    return loud, quiet

analyze('чистая речь', '/tmp/real_speech.wav')
noisy = analyze('речь + шум', '/tmp/real_noisy.wav')
for name, af in (('anlmdn', 'highpass=f=70,anlmdn=s=7:p=0.002:r=0.002:m=15'),
                 ('anlmdn мягче', 'highpass=f=70,anlmdn=s=5:p=0.0015:r=0.0015:m=12'),
                 ('afftdn', 'highpass=f=70,afftdn=nr=12:nf=-45:tn=1')):
    out = '/tmp/real_%s.wav' % name.replace(' ', '_')
    r = subprocess.run(['ffmpeg','-hide_banner','-v','error','-y','-i','/tmp/real_noisy.wav','-af',af,'-ar','48000','-ac','1','-c:a','pcm_s16le',out], capture_output=True)
    if r.returncode != 0:
        print('  %-22s недоступен' % name); continue
    loud, quiet = analyze(name, out)
    print('      речь изменилась на %+.1f дБ, паузы тише на %.1f дБ' % (loud-noisy[0], noisy[1]-quiet))
" 2>&1 | tail -12
