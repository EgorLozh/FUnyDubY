# Collaborative Video Dubbing Platform — архитектурный документ

**Статус:** черновик на утверждение (код не пишется до явного подтверждения)
**Дата:** 2026-09-30
**Репозиторий:** `D:\FUnyDubY` (пустой, ветка `main` без коммитов)

---

## 1. Executive Summary

Приложение: одна ссылка `/room/{room_id}` → загрузка видео → автоматический ML-разбор (отделение речи, STT, диаризация, нарезка на реплики) → каждый участник без регистрации озвучивает свои реплики с микрофона → сборка финального видео (музыка и SFX сохранены, оригинальный голос удалён) → скачивание.

Ключевая архитектурная идея, определяющая весь проект: **оригинальная речь не «заменяется», а вычитается.**

```
background = original_mix − estimated_speech      (поэлементное вычитание, не сумма стемов)
final      = background + Σ(user_recordings @ line_timestamps)
```

Почему так: любая модель разделения неидеальна. Если собирать фон как сумму стемов `music + effects`, всё, что модель отнесла к «речи» (а туда попадают крики, шёпот, вокал в песне, часть SFX), безвозвратно исчезнет из финала. При вычитании гарантируется, что сохранено **всё, кроме оценки речи**, а ошибка проявляется в виде остаточного «призрака» голоса — проблема, которая лечится дуккингом (см. §16) и, при необходимости, вторым проходом подавления речи, но не восстанавливается из ничего.

Второй по важности вывод: **ML ошибается всегда, а продукт должен оставаться рабочим.** Значит, весь артефакт ML — редактируемый: пользователь может переименовывать спикеров, менять текст, склеивать/резать реплики, переназначать их. Ошибка диаризации не должна быть блокером, она должна стоить двух кликов.

Третий: **никакой realtime-синхронизации.** Каждый участник работает со своим списком реплик независимо; единственное, что синхронизируется — состояние назначений (через БД, а не через сокеты).

Оценка объёма MVP: backend ~4-5 тыс. строк, frontend ~3-4 тыс., ML-обвязка ~1.5 тыс.

---

## 2. Функциональные требования

| № | Требование | Приоритет MVP | Примечание |
|---|---|---|---|
| F1 | Создание комнаты, ссылка `/room/{room_id}` | MVP | ID — криптостойкий, 22+ символа |
| F2 | Вход по ссылке без регистрации | MVP | участник получает `participant_id` + `token` |
| F3 | Загрузка видео MP4/WebM/MOV, ≤ 10 мин | MVP | пофайлово (один ролик = одна комната) |
| F4 | Автоматическая обработка: разделение, STT, диаризация, нарезка | MVP | асинхронно, с прогрессом |
| F5 | Список реплик с текстом, спикером, таймкодами | MVP | + субтитры в плеере |
| F6 | Прослушивание оригинала реплики | MVP | из отдельной speech-дорожки |
| F7 | Назначение реплик себе / снятие | MVP | атомарно, без гонок |
| F8 | Запись с микрофона, ≤ длительности оригинала | MVP | жёсткий стоп + серверная обрезка |
| F9 | Многократная перезапись, актуальна последняя | MVP | история тейков хранится |
| F10 | Редактирование ML-результата (минимум — спикер) | MVP | + текст, склейка/резка реплик |
| F11 | Рендер финала: фон (music/SFX) + новые голоса | MVP | изображение не трогается |
| F12 | Скачивание финального видео | MVP | Range-запросы, докачка |
| F13 | Отображение субтитров/реплик в UI | MVP | |
| F14 | Экспорт SRT/WebVTT | Post-MVP | модель данных готова, эндпоинт дешёвый |
| F15 | Управление числом спикеров (подсказка диаризатору) | MVP | снимает половину ошибок диаризации |
| F16 | Отображение статуса обработки с этапами и % | MVP | |
| F17 | Повторный запуск обработки / этапа | MVP | идемпотентно |
| F18 | Удаление комнаты (вручную и по TTL) | MVP | каскад БД + файлы |
| F19 | Индикация «сколько реплик ещё не озвучено», блокировка рендера по политике | MVP | |

Осознанно **вне** MVP: аккаунты, OAuth, роли, чаты, realtime-синхронный просмотр, загрузка готового аудио вместо записи, голосовые эффекты/питч/реверб, клонирование голоса, перевод на другие языки, монтаж видеоизображения, несколько видео в комнате.

---

## 3. Нефункциональные требования

| Категория | Требование | Как обеспечивается |
|---|---|---|
| Производительность | 10-мин видео обрабатывается ≤ 6 мин на одной GPU | батчевый STT, один GPU-джоб за раз, ffmpeg для I/O |
| Отзывчивость UI | отклик API p95 < 300 мс (не считая ML/рендер) | ML только в воркерах, API не блокируется |
| Загрузка | до 2 ГБ файла, потоковая, без буферизации в памяти | multipart-стрим на диск, `client_max_body_size` в nginx |
| Надёжность | краш воркера не теряет комнату; повтор этапа возможен | состояние этапов в PostgreSQL, атомарная запись файлов |
| Идемпотентность | повторный POST upload/render/recording не создаёт дублей | idempotency key + уникальные индексы |
| Консистентность | одна реплика — не более одного держателя | уникальный индекс на `assignments.line_id` + транзакция |
| Приватность | доступ к медиа только по роли комнаты/участника | токен участника + проверка владения комнатой |
| Безопасность | невозможность перебора ID и path traversal | 128-битный ID, только относительные пути, проверка префикса |
| Наблюдаемость | structured logs, длительности этапов, артефакты этапов | structlog + `job_stages.metrics` |
| Переносимость | запуск на одном сервере с GPU через docker compose | отдельные образы backend / worker-gpu / worker-cpu |
| Расширяемость | SRT-экспорт, Prometheus, S3-хранилище — без переделки модели | нормализованная схема, слой `storage` за интерфейсом |

Ограничения окружения, влияющие на дизайн:
- PostgreSQL **вне** compose → подключение по `DATABASE_URL`, миграции через Alembic, схема фиксируется на старте.
- ML на «своём GPU-сервисе» → нужен образ с CUDA и `nvidia-container-toolkit`; интернет-доступ к Hugging Face/ModelScope нужен на этапе прогрева моделей (иначе — предзагрузка весов в volume).
- Срок обработки «несколько минут» приемлем → нет необходимости в сложной оптимизации, можно брать более качественные и медленные модели.

---

## 4. Системная архитектура

```
                            Browser (React SPA)
                                    │  HTTPS, SSE, Range
                                    ▼
                    ┌───────────────────────────────┐
                    │  nginx (frontend container)   │
                    │  /            → static SPA    │
                    │  /api, /media → proxy backend │
                    └───────────────┬───────────────┘
                                    │
                    ┌───────────────▼───────────────┐
                    │  FastAPI (api)                │
                    │  REST + SSE + стриминг файлов │
                    └───┬───────┬───────────┬───────┘
                        │       │           │
        ┌───────────────┘       │           └────────────┐
        ▼                       ▼                        ▼
┌───────────────┐      ┌────────────────┐       ┌─────────────────┐
│ PostgreSQL    │      │ Redis          │       │ /data (volume)  │
│ (вне compose) │      │ брокер + локи  │       │ оригиналы,      │
│ истина о      │      │ + heartbeat    │       │ стемы, дорожки, │
│ сущностях     │      │ воркеров       │       │ записи, рендеры │
└───────────────┘      └───────┬────────┘       └─────────────────┘
                               │ Dramatiq
              ┌────────────────┼──────────────────┐
              ▼                                   ▼
   ┌──────────────────────┐            ┌────────────────────────┐
   │ worker-gpu (1 слот)  │            │ worker-cpu (N слотов)  │
   │ separation           │            │ ffmpeg: probe/extract  │
   │ STT + alignment      │            │ нормализация записей   │
   │ diarization          │            │ микширование и рендер  │
   └──────────────────────┘            └────────────────────────┘
```

Изменения против предложенной в задании схемы (§27) и почему:

1. **Разделены worker-gpu и worker-cpu.** Рендер и ffmpeg-операции на GPU не нужны, а держать GPU-слот занятым копированием контейнеров бессмысленно. CPU-воркеров можно масштабировать независимо.
2. **Redis — брокер и координация, PostgreSQL — источник истины.** Статусы этапов, прогресс, назначения хранятся в БД: перезапуск Redis/воркера не должен «терять» комнату. Redis не используется как хранилище состояния, кроме локов и heartbeat.
3. **Медиа отдаёт backend, а не nginx напрямую из тома.** Файлы доступны только после проверки прав комнаты/участника; статическая раздача тома = утечка по угадыванию пути.
4. **Прогресс — через SSE**, а не WebSocket: нужен один поток «сервер → клиент», встречный канал уже покрыт REST.
5. **Микширование — в Python (numpy), а не в ffmpeg-графе.** При 200+ репликах ffmpeg-граф с 200 входами и `adelay` превращается в хрупкий генератор строк и медленную сборку; сборка PCM в numpy + финальный `ffmpeg -c:v copy` для мультиплексирования проще, тестируемо и переносимо.

---

## 5. Компонентная диаграмма

```mermaid
graph LR
  subgraph Frontend
    HOME[HomePage] --> UPL[Uploader]
    HOME --> ROOM[RoomPage]
    ROOM --> PLAY[VideoPlayer + Subtitles]
    ROOM --> LINES[DialogueEditor]
    ROOM --> PART[ParticipantsPanel]
    ROOM --> MY[MyLinesPage]
    MY --> REC[LineRecorder]
    ROOM --> FIN[FinalPage]
  end

  subgraph Backend API
    R[/rooms/] --- U[/upload/] --- D[/dialogue/] --- A[/assignments/]
    RC[/recordings/] --- RN[/renders/] --- M[/media/] --- E[/events SSE/]
  end

  subgraph Services
    INV[InventoryService] --- JOB[JobService] --- ASG[AssignmentService]
    STO[StorageService] --- REN[RenderService] --- SEC[SecurityService]
  end

  subgraph Workers
    W1[separate_speech] --> W2[transcribe] --> W3[diarize] --> W4[merge_dialogue]
    W5[extract_audio] --> W1
    W6[normalize_recording] --- W7[render_final]
  end

  subgraph ML
    M1[Bandit v2 / Demucs] --- M2[faster-whisper / Parakeet] --- M3[pyannote community-1]
  end
```

---

## 6. Поток данных

**A. Загрузка и обработка**

```
POST /video (multipart)      →  /data/rooms/{id}/original/source.mp4
ffprobe                      →  videos{ duration, w, h, fps, codecs, sha256 }
processing_jobs (QUEUED)     →  enqueue extract_audio
extract_audio                →  audio/mix.wav (48k stereo) + audio/mix_mono16k.wav
separate_speech              →  speech/speech.wav, speech/background.wav (= mix − speech)
transcribe                   →  dialogue/words.json  (word-level, речь-дорожка)
diarize                      →  dialogue/diarization.json (RTTM-подобный, речь-дорожка)
merge_dialogue               →  dialogue/lines.json → строки в dialogue_lines
                              →  speech/segments/line_{idx}.wav (нарезка для прослушивания)
READY
```

**B. Озвучка**

```
GET  /lines?assigned_to=me        → список моих реплик
GET  /media/speech/line_{id}      → прослушивание оригинала (Range)
POST /lines/{id}/recording (webm) → recordings/{line_id}/take_{n}.webm (raw)
normalize_recording               → 48k mono, обрезка по длительности реплики, loudnorm,
                                    recordings/{line_id}/take_{n}.wav
UPDATE is_current                → актуальный тейк для строки (уникальный частичный индекс)
```

**C. Рендер**

```
POST /renders                    → render_jobs (QUEUED) → enqueue render_final
render_final:
   numpy:  background.wav (stereo, 48k)
         + для каждой озвученной реплики: take.wav с оффсетом start_ms (микширование с суммированием)
         + опционально: динамический ducker фона по огибающей голоса
         → final/mix.wav (float32 → int16)
   ffmpeg: -i source.mp4 -i final/mix.wav -map 0:v -map 1:a -c:v copy -c:a aac -b:a 192k
         → rendered/{render_id}.mp4
POST /renders/{id}/finalize      (atomics: temp → rename) → render_jobs.DONE
```

---

## 7. ML pipeline

Полный граф с указанием моделей, входов/выходов и точек отказа:

```
source.mp4
   │  ffmpeg -vn -ac 2 -ar 48000                     [worker-cpu]
   ▼
mix.wav (48k stereo) ────────────────► mix_mono16k.wav   (для STT и диаризации)
   │  Bandit v2 (speech/music/effects)                [worker-gpu]
   ▼
speech_est.wav
   │  background = mix − speech_est  (поэлементно, с нормализацией усиления)
   ▼
background.wav  ── играет роль «музыки и SFX, которые надо сохранить»
   │
   │  speech_est → VAD-тримминг тишины
   ▼
   ├─► faster-whisper large-v3 (+ WhisperX alignment) ──► words[{t0,t1,word,prob}]
   │
   └─► pyannote community-1 (exclusive) ──► turns[{t0,t1,speaker}]

words + turns
   │  merge_dialogue (алгоритм §7.3)
   ▼
lines[{idx,start_ms,end_ms,speaker,text,words[]}]
   │  нарезка speech-дорожки по границам реплик
   ▼
speech/segments/line_{idx}.wav   →  отдаётся клиенту для прослушивания оригинала

...
background.wav + recordings/*.wav
   │  render (§18)
   ▼
final.mp4  (видео без изменений, новая аудиодорожка)
```

### 7.1 Speech separation

Задача специфична: нужен не «вокал из песни», а **диалог из фильма**. Это отдельный подвид source separation (cinematic audio source separation), который как раз и решает Bandit: три стема `speech / music / effects`, обучен на Divide and Remaster (DnR), в v2 добавлена мультиязычность.

Практические следствия для нашего конвейера:
- Разделяем на 3 стема, но в фон идём **вычитанием**, а не суммой `music+effects` (см. §1). Стемы нужны для контроля качества и для отладки.
- Если `speech_est` получился с заниженным усилением, вычитание оставит «призрака» — поэтому перед вычитанием делаем выравнивание усиления по корреляции (оптимальный масштаб α: `background = mix − α·speech_est`, α из least-squares по сегментам с высокой уверенностью VAD). Это дешёвая операция, дающая заметный прирост.
- Для моно-материала разделение делаем в моно, затем фон дублируем в стерео — так меньше артефактов панорамы.

### 7.2 STT и выравнивание

- Основной вариант: `faster-whisper` (`large-v3`, при необходимости `large-v3-turbo`) + VAD (Silero) через WhisperX-подход, плюс wav2vec2-alignment для точных word-timestamps. Word-level нужен не для красоты, а для корректной нарезки: границы реплик берём по словам, а не по «сегментам Whisper», которые часто рвут фразу.
- Промпт-контекст: в Whisper можно передать `initial_prompt` (например, «Диалог из фильма. Имена: …») — помогает с именами собственными. Храним в `rooms.settings.stt_prompt`.
- Язык: автоопределение с возможностью задать вручную в настройках комнаты (пользователь знает язык видео лучше модели).
- Параллельный (быстрый) вариант для европейских языков: Parakeet-TDT-0.6B-v3 — 25 языков (включая ru/uk), нативные word/char/segment timestamps, до 24 минут в один проход, RTFx ~2940, лицензия CC-BY-4.0. Держим как переключаемый бэкенд (`STT_BACKEND=whisper|parakeet`), потому что он в разы быстрее и не требует alignment-модели.

### 7.3 Merge: слова + спикеры → реплики

Алгоритм (детерминированный, покрывается unit-тестами без ML):

1. Взять **exclusive**-диаризацию (pyannote community-1, `exclusive_speaker_diarization`) — в ней в каждый момент активен один спикер, что снимает проблему наложения и делает сопоставление со словами тривиальным.
2. Каждому слову присвоить спикера по перекрытию с turn (при неоднозначности — по центру слова).
3. Сгруппировать подряд идущие слова: новая реплика начинается при смене спикера **или** паузе > `merge_gap_ms` (по умолчанию 350 мс) **или** конце предложения (`.!?…`) с паузой > 150 мс.
4. Склеить слишком короткие куски: реплика короче `min_line_ms` (по умолчанию 600 мс) присоединяется к соседней, если это тот же спикер и разрыв < 400 мс; иначе остаётся (это может быть «Да.»).
5. Разрезать слишком длинные: реплика длиннее `max_line_ms` (по умолчанию 12 с) режется по ближайшей паузе между словами; если пауз нет — по границе предложения. Это критично для UX: озвучивать 40-секундный монолог в один дубль невозможно, а лимит длительности записи привязан к реплике.
6. Мелкие реплики-поддакивания (< 400 мс, одно слово) помечаются `is_short`, в UI не назначаются автоматически (спорный приоритет) — их можно озвучить вручную.
7. Нарезка `speech_est` по итоговым границам с паддингом ±80 мс.

Дополнительно: **реплики с наложением** (в exclusive-режиме они всё равно видны как overlap) получают флаг `overlaps=true` и в UI помечаются «в кадре говорят одновременно — озвучка может конфликтовать»; в финальном миксе при пересечении двух записей применяется понижение уровня (или `policy=later_wins`).

### 7.4 Что считается артефактом этапа

Каждый этап пишет JSON-артефакт в `dialogue/` / `speech/`: сырые слова, сырые turns, итоговые реплики, метрики (RTF, длительности). Это позволяет отлаживать «плохие реплики» без повторного прогона GPU.

---

## 8. Рекомендуемые open-source модели

Кандидаты проверены по актуальным карточкам моделей/репозиториям (см. ссылки в §8.2).

### 8.1 Таблица выбора

| Компонент | Кандидат | Качество | Скорость | GPU | Лицензия | Рекомендация |
|---|---|---|---|---|---|---|
| Speech separation | **Bandit v2** (speech/music/effects, DnR v3) | целевое: диалог/музыка/SFX, SOTA на DnR, выше ideal ratio mask для dialogue | средняя (band-split RNN ~0.2-0.5 RTF на GPU) | 2-4 ГБ VRAM | permissive (репо bandit-v2; веса на Zenodo — проверить перед коммерцией) | **ВЫБРАНО (D1)** |
| Speech separation | Demucs v4 `htdemucs` / `htdemucs_ft` | отличное для музыки (SDR ~9.0), диалог из фильма трактует как «vocals» | средняя | 2-3 ГБ | MIT (репозиторий **архивирован**) | **ВЫБРАНО как fallback (D2)** — MIT, ставится одной командой, базовая линия сравнения |
| Speech separation | Mel-Band / BS-RoFormer (UVR-чекпоинты через `python-audio-separator`, MIT) | лучший SDR на вокале музыки (11.6 дБ в статье) | средняя-медленная, есть ONNX | 2-4 ГБ | код MIT, **веса чекпоинтов сообщества с неясными условиями** (в UVR есть открытый вопрос к авторам) | **ОТКЛОНЕНО (D3)** — веса сообщества с неясными условиями |
| STT | **faster-whisper large-v3** (+ WhisperX alignment) | высокая, 99 языков, word timestamps после alignment | batched, ~10-20× realtime на GPU | 4-6 ГБ | MIT (код и веса Whisper) | **ВЫБРАНО (D4)** — основной бэкенд |
| STT | faster-whisper `large-v3-turbo` | близко к large-v3, слабее на редких языках | ~4-8× быстрее | 2-3 ГБ | MIT | **ВЫБРАНО (D4)** — режим «быстро» и fallback при OOM |
| STT | NVIDIA Parakeet-TDT-0.6B-v3 | очень высокая для 25 европейских языков (ru/uk включены), нативные word/char/segment timestamps | RTFx ~2940 | 1-2 ГБ | CC-BY-4.0 | опциональный бэкенд (`STT_BACKEND=parakeet`), не дефолт |
| STT | WhisperX как оркестратор | VAD + alignment + склейка с диаризацией «из коробки» | — | — | BSD-2 | использовать его утилиты/идеи |
| Diarization | **pyannote `speaker-diarization-community-1`** (pyannote.audio 4.x) | лучший открытый: AMI-IHM 17.0, DIHARD3 20.2, CALLHOME-p2 26.7, VoxConverse 11.2 DER | ~20-100× realtime | 1-2 ГБ | **CC-BY-4.0**, но репозиторий **gated**: нужен HF-аккаунт, принятие условий, токен | **ВЫБРАНО (D5)** — основной (exclusive-режим важен для merge) |
| Diarization | NVIDIA Sortformer streaming 4spk v2.1 | 4 спикера максимум, деградирует на 5+ | очень быстро (~214× RTF) | ~0.5 ГБ | NVIDIA Open Model License | **ВЫБРАНО как fallback (D5)** — если доступ к gated community-1 не получен |
| Diarization | DiariZen (WavLM-large, s80-md) | DER 13.3% среднии, лучший на 5+ спикерах (7.1%) | средняя | 2-3 ГБ | код MIT, **веса CC BY-NC 4.0 → некоммерческая** | **ОТКЛОНЕНО (D3)** — веса CC BY-NC (некоммерческие) |

### 8.2 Основания и ссылки

- Bandit v2 (cinematic 3-stem separation, DnR v3, мультиязычный): https://github.com/kwatcharasupat/bandit-v2, веса — Zenodo (records/12701995); обзорная страница: https://github.com/kwatcharasupat/source-separation-landing
- Demucs v4 (`htdemucs`, MIT, репозиторий в статусе ARCHIVED): https://github.com/facebookresearch/demucs
- UVR-экосистема / запуск чекпоинтов Mel-Band RoFormer: https://github.com/nomadkaraoke/python-audio-separator (MIT); Mel-Band RoFormer paper: arXiv 2310.01809
- Parakeet-TDT-0.6B-v3 (CC-BY-4.0, 25 языков, word/char/segment timestamps, 24 мин/проход): https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3
- pyannote community-1 (CC-BY-4.0, gated, exclusive-diarization, таблица DER против 3.1): https://huggingface.co/pyannote/speaker-diarization-community-1 ; релизный блог: https://www.pyannote.ai/blog/community-1
- Streaming Sortformer v2.1 (117M параметров, максимум 4 спикера, NVIDIA Open Model License): https://huggingface.co/nvidia/diar_streaming_sortformer_4spk-v2.1
- DiariZen (код MIT, веса CC BY-NC 4.0): https://huggingface.co/BUT-FIT/diarizen-wavlm-large-s80-mlc
- Сравнительный бенчмарк диаризаторов (PyannoteAI 11.2% / DiariZen 13.3% / Sortformer v2 214× RTF): arXiv 2509.26177

### 8.3 Оговорка про лицензии

Три пункта, которые надо подтвердить до продакшена (в §30 — как решение на утверждение):
1. `community-1` — gated, при этом **коммерчески допустимая** CC-BY-4.0; нужен HF-токен на этапе сборки/прогрева, значит требуется сетевой доступ и хранение токена в секретах.
2. DiariZen и большинство UVR-чекпоинтов — NC-веса: в коммерческом продукте не использовать.
3. Bandit-веса — Zenodo; автор просит (не требует) поддержать некоммерческие музыкальные организации при коммерческом использовании. Формально permissive, юридически — проверить.

### 8.4 Финальные решения по моделям (ADR, зафиксировано 2026-09-30)

Выбор сделан **без A/B-бенчмарка на реальном материале** — осознанно: прогон сравнения моделей это отдельная работа, а не условие для старта. Основание — назначение моделей и лицензии. Для каждого решения указан триггер пересмотра, чтобы выбор не превратился в догму.

| ID | Решение | Почему именно так | Триггер пересмотра |
|---|---|---|---|
| **D1** | **Speech separation = Bandit v2** (`SEPARATION_MODEL=bandit_v2`) | Единственный кандидат, обученный на *cinematic* separation: три стема `speech / music / effects` на датасете DnR, а не «вокал/инструментал» из музыки. Нам нужен лучший **speech estimate**, потому что фон строится вычитанием (§16.2) — качество финала определяется точностью именно этой оценки. Demucs свою «vocals»-голову обучал на песнях: реплику он отделит, но вокал в песне и крик/SFX будет путать с речью одинаково. | Если на реальном ролике метрика «остаток речи в фоне» выйдет хуже −15 дБ или модель не заведётся в контейнере (research-код) → D2 **без изменения архитектуры** |
| **D2** | **Fallback separation = Demucs v4 `htdemucs`** (`SEPARATION_MODEL=demucs`, 2-stem vocals) | MIT, ставится одной командой, веса качаются автоматически, предсказуемо живёт на CUDA. Нужна страховка на случай проблем с зависимостями Bandit, а также базовая линия для будущего сравнения качества. | — |
| **D3** | **Отклонены:** UVR/Mel-Band-RoFormer-чекпоинты, DiariZen | Модели вокала оптимизированы под пение, а веса сообщества имеют неясные условия использования; DiariZen — веса CC BY-NC. Юридический риск не оправдан выигрышем в качестве. | Если проект станет строго некоммерческим — DiariZen возвращается как опция |
| **D4** | **STT = faster-whisper `large-v3`** + WhisperX-alignment; `large-v3-turbo` — fallback при OOM; Parakeet-v3 — опциональный бэкенд | Whisper покрывает 99 языков против 25 европейских у Parakeet, веса MIT, word-timestamps получаются выравниванием. Parakeet оставляем быстрым переключателем для англоязычного контента. | Если контент только английский и скорость критична — дефолт меняется на `parakeet` одной переменной окружения |
| **D5** | **Диаризация = pyannote `speaker-diarization-community-1`** (exclusive-режим); Sortformer v2.1 — fallback | Лучший открытый диаризатор с коммерчески допустимой лицензией; exclusive-режим («в каждый момент один спикер») снимает основную сложность склейки слов со спикерами (§7.3). Sortformer ограничен 4 спикерами, поэтому основным для фильмов быть не может. | Нет HF-токена/условий → Sortformer (только комнаты с ≤4 спикерами) → иначе деградация «все Speaker 1» |

Практическая ценность этого решения — в изоляции: **смена модели = одна переменная окружения**. `ml/separation.py`, `ml/stt.py`, `ml/diarization.py` прячут конкретную модель за адаптерами с общим выходным форматом (моно-дорожка 48 кГц; список слов с таймстемпами; список turns), поэтому D1↔D2 и D4↔Parakeet переключаются без изменений в сервисах, схеме БД и frontend.

---

## 9. Требования к GPU

Базовый профиль (рекомендуемый): **1× NVIDIA GPU, ≥ 12 ГБ VRAM** (комфортно 16 ГБ); например RTX 3060 12G / 4060 Ti 16G / A4000 / L4 / 4090. Диск: ≥ 20 ГБ под образы и веса, ≥ 2× исходник под рабочее место комнаты.

### 9.1 Подтверждённое железо (проверено 2026-09-30)

| Параметр | Факт | Следствие |
|---|---|---|
| Хост | `egorserver` — Ubuntu, SSH на порту 2222 | ML-стенд определён; рекомендации ниже рассчитаны на него |
| GPU | **NVIDIA GeForce RTX 3060, 12 ГБ VRAM** (драйвер 595.91.07, CUDA 13.2) | рекомендованный минимум выполнен: пик по этапам (4-6 ГБ на STT) помещается с запасом |
| CPU / RAM | 4 vCPU, 15 ГБ RAM | хватает на CPU-воркер (ffmpeg, микширование) параллельно с GPU-этапами |
| Диск | 115 ГБ, свободно 39 ГБ | хватает на образы + веса (~10 ГБ) + рабочие данные; **том `hf-cache` обязателен**, иначе каждая пересборка образа качает 5-10 ГБ весов |
| Docker | установлен, но `docker info` показывает `Runtimes: runc` — **nvidia-container-toolkit не установлен** | перед этапом 1: `sudo apt install -y nvidia-container-toolkit && sudo systemctl restart docker`. Без этого `deploy.resources.reservations.devices` не пробросит GPU в контейнер. Альтернатива для MVP: `worker-gpu` в venv прямо на хосте (быстрее отладка, теряется воспроизводимость compose) |
| Python на хосте | системный 3.14; `ffmpeg` 8.0.1 доступен (apt) | ML-код **не** ставить в системный Python: только контейнер или отдельный venv на 3.12 — под 3.14 колёс torch/ctranslate2 может не быть |
| Сеть с хоста | PyPI, GitHub, Hugging Face, Zenodo, Wikimedia — доступны; `astral.sh` — **заблокирован** | веса греются на хосте; `uv` ставить через `pip install uv`, а не install-скриптом с astral.sh |

Единственное, что требуется от владельца хоста: **HF-токен и принятие условий на `pyannote/speaker-diarization-community-1`** — без этого D5 работает только в fallback-режиме (Sortformer, ≤4 спикера) или с деградацией «все Speaker 1».

Примечание о состоянии хоста после подготовки стенда: установлен пакет `ffmpeg` (`apt`), временный каталог `~/dubtest` удалён. Если ffmpeg на хосте не нужен — `sudo apt-get remove ffmpeg`.

Пиковое потребление по этапам:

| Этап | VRAM | Замечание |
|---|---|---|
| extract_audio (ffmpeg) | 0 | worker-cpu |
| separate_speech (Bandit v2) | 2-4 ГБ | чанками по 10-30 с, чтобы не ловить OOM на длинном видео |
| transcribe (large-v3, batched) | 4-6 ГБ | batch_size подбирается по VRAM, при OOM → turbo |
| align (wav2vec2) | 1-2 ГБ | |
| diarize (community-1) | 1-2 ГБ | |
| render | 0 | worker-cpu |

Правила управления GPU:
- **Одна GPU-задача одновременно** (`worker-gpu` запускается с `--processes 1 --threads 1`, отдельная очередь `gpu`). Никакого параллелизма ML-этапов: экономия «пары секунд» не стоит OOM-крашей.
- **Модели загружаются лениво и держатся в кэше процесса**, между большими этапами допускается `del model; torch.cuda.empty_cache()`, если наблюдается фрагментация. Последовательная загрузка (не держать Whisper и pyannote одновременно) — включено по умолчанию, отключается флагом.
- Планировщик — по этапам, а не по комнатам. Один долгий рендер на CPU не блокирует GPU-очередь.
- Деградация при OOM: `large-v3` → `large-v3-turbo`; Bandit 64-band → 48-band вариант; при повторном OOM этап помечается FAILED с внятной ошибкой, комната остаётся в состоянии с возможностью retry.
- Оценка времени для 10-минутного ролика (одна GPU среднего класса): extract 5-10 с, separation 60-180 с, STT 30-60 с (turbo) / 90-180 с (large-v3), alignment 10-20 с, diarization 30-90 с, merge <5 с, рендер 20-60 с → **итого ~3-7 минут**. Цифры — оценка по типовым RTF, подлежат подтверждению бенчмарком на этапе 0 roadmap.

Параллелизм между GPU и CPU-очередями разрешён: пока GPU считает separation, CPU-воркер может нормализовать чужие записи.

---

## 10. Архитектура backend

Слои (тонкие, без избыточных абстракций):

```
app/
├── main.py                # FastAPI app, lifespan (пул БД, Redis, прогрев storage)
├── core/
│   ├── config.py          # pydantic-settings, все ENV
│   ├── db.py              # async engine + sessionmaker (SQLAlchemy 2.0)
│   ├── redis.py           # клиент + лок-хелперы
│   ├── logging.py         # structlog, request_id middleware
│   ├── errors.py          # доменные исключения → problem+json
│   └── security.py        # room_id, participant tokens, проверки путей
├── api/
│   ├── deps.py            # зависимости: сессия, текущий участник, комната
│   └── routes/
│       ├── rooms.py  video.py  jobs.py  lines.py
│       ├── assignments.py  recordings.py  renders.py
│       ├── media.py  events.py  subtitles.py  health.py
├── models/                # SQLAlchemy ORM (одна таблица — один файл)
├── schemas/               # Pydantic v2: request/response
├── services/              # бизнес-логика (единственное место, где пишется в БД)
│   ├── rooms.py video.py jobs.py lines.py assignments.py
│   ├── recordings.py renders.py storage.py events.py
├── ml/                    # чистые функции + адаптеры моделей (импортируются только воркерами)
│   ├── separation.py stt.py diarization.py merge.py audio_io.py
├── workers/
│   ├── broker.py          # Dramatiq + Redis
│   └── tasks/  extract_audio.py separate_speech.py transcribe.py
│                diarize.py merge_dialogue.py normalize_recording.py render_final.py
└── video/
    ├── ffmpeg.py          # безопасные обёртки (списки аргументов, таймауты)
    └── mix.py             # numpy-микширование, дуккинг, loudness
```

Принципы:
- Маршруты не содержат логики — только валидация, вызов сервиса, ответ. Всё, что пишет в БД, живёт в `services/` (одна транзакция = один сервисный вызов).
- `ml/` не импортируется процессом API вообще: torch/ctranslate2 не тащатся в API-образ, аптайм API не зависит от ML-зависимостей.
- FastAPI async для I/O (БД, Redis, стриминг), ML-этапы синхронные в воркерах (никакого `async def` вокруг torch).
- Все внешние вызовы (ffmpeg, модели) — через адаптеры с таймаутами, ретраями на уровне Dramatiq и метриками длительности.
- Запись файлов: всегда «temp → fsync → rename» в пределах одной ФС; каталог комнаты — единственная точка правды для артефактов.
- Прогресс этапа: `job_stages.progress` 0-100 + публикация события в Redis Pub/Sub (`room:{id}:events`), которое API раздаёт по SSE.
---

## 11. Архитектура frontend

Стек: **React 19 + TypeScript + Vite**, роутер — `react-router`, серверное состояние — **TanStack Query**, локальное состояние записи — хуки + `useReducer` (без Redux/Zustand, масштаб не требует), стили — Tailwind CSS с тёмной темой по умолчанию, HTTP — тонкий клиент на `fetch` (без axios), SSE — `EventSource`. Никаких тяжёлых UI-китов: плеер и полоса реплик — собственные компоненты.

```
src/
├── api/            client.ts, rooms.ts, lines.ts, recordings.ts, renders.ts, types.ts (из openapi)
├── hooks/
│   ├── useParticipant.ts   # токен участника в localStorage (ключ `dub:{roomId}`)
│   ├── useRoomEvents.ts    # SSE-подписка, инвалидирует кэши Query
│   ├── useLineRecorder.ts  # запись с микрофона (см. §17)
│   └── useMediaDuration.ts
├── pages/
│   ├── HomePage.tsx        # создать комнату
│   ├── RoomPage.tsx        # плеер + субтитры + панели
│   ├── ProcessingPage.tsx  # этапы и прогресс
│   ├── MyLinesPage.tsx     # мои реплики (основной рабочий экран)
│   └── FinalPage.tsx       # финальное видео + скачивание
├── components/
│   ├── room/ ParticipantsPanel, LinesEditor, LineRow, SpeakerBadge,
│   │         AssignmentButton, SubtitleOverlay, RoomHeader, UnrecordedWarning
│   ├── recorder/ RecordButton, RecordingWaveform, LevelMeter, CountdownBar,
│   │             TakeList, OriginalPlayer
│   ├── processing/ StageStepper, ProgressBar, ErrorPanel
│   └── ui/  Button, Modal, Toast, Slider, Timecode
└── lib/  time.ts (mm:ss.mmm), audio.ts (Web Audio helpers), durationLimit.ts
```

Состояние и его владельцы:

| Состояние | Владелец | Синхронизация |
|---|---|---|
| Комната, видео-метаданные | TanStack Query (`['room', id]`) | по SSE-событиям |
| Реплики, назначения | TanStack Query (`['lines', id]`, `['lines', id, filter]`) | invalidate по SSE |
| Статус обработки | TanStack Query + SSE с этапами | SSE, fallback — polling 2 с |
| Запись (идёт/пауза/таймер) | локальный `useReducer` в `LineRecorder` | никогда не синхронизируется |
| Мои тейки | TanStack Query (`['recordings', lineId]`) | invalidate после загрузки |
| Токен участника | localStorage | — |

Ключевые UX-решения:
- **MyLines — главный экран**, а не «страница комнаты»: сессия озвучки состоит из повторов «послушал оригинал → записал → послушал себя». Всё остальное — второстепенно.
- **Клавиатура**: `Space` — play/pause оригинала, `R` — начать/остановить запись, `N` — следующая реплика. Это то, что превращает инструмент из «веб-формочки» в рабочее место.
- **Автопереход к следующей неозвученной реплике** после успешной записи (опция).
- **Прогресс-бар сбора**: «12 из 34 реплик озвучено», с фильтром «только мои».
- **Плеер и субтитры**: клик по субтитру = seek; активная реплика подсвечена; при воспроизведении оригинала реплики играет именно она.
- Мониторинг через `AnalyserNode` (уровень) без вывода себя в наушники — иначе акустическая обратная связь.
- Ошибки и предупреждения — инлайн: «видео 12:30 — больше лимита 10:00», «этап диаризации упал, повторить», «в этой реплике пересечение с репликой другого участника».

Оптимизации рендера: виртуализация списка реплик (ролик на 10 минут ≈ 100-250 реплик), мемоизация строк, отказ от перерисовки всего списка при воспроизведении (прогресс плеера — отдельный контекст/`requestAnimationFrame`, а не state всего дерева).

---

## 12. Схема базы данных

PostgreSQL 14+. Все временные метки — `timestamptz`, идентификаторы — `uuid` (`gen_random_uuid()`), кроме `rooms.id` (публичный короткий текст). Файлы не хранятся в БД — только **относительные** пути от `STORAGE_ROOT`.

### 12.1 `rooms`

| Поле | Тип | Ограничения | Комментарий |
|---|---|---|---|
| id | text | PK, ~22 символа base32 | публичный, криптостойкий (`secrets`) |
| title | text | not null, ≤120 | |
| status | room_status | not null, default `CREATED` | см. §22 |
| settings | jsonb | not null, default `{}` | `stt_backend`, `stt_language`, `stt_prompt`, `max_speakers`, `ducking_db`, `unrecorded_policy`, `record_tolerance_ms` |
| video_id | uuid | FK videos(id) on delete set null | активное видео комнаты |
| owner_participant_id | uuid | FK participants(id) on delete set null | создатель (для дефолтных прав, не роль) |
| expires_at | timestamptz | — | TTL неактивной комнаты (по умолчанию +7 дней) |
| deleted_at | timestamptz | — | soft-delete, garbage collector удаляет файлы |
| created_at / updated_at | timestamptz | not null default now() | |

Индексы: PK; `(status, expires_at)` для реапера.

### 12.2 `participants`

| Поле | Тип | Ограничения |
|---|---|---|
| id | uuid | PK |
| room_id | text | FK rooms(id) **on delete cascade**, not null |
| display_name | text | not null, ≤40 |
| color | text | not null (hex, из палитры по кругу) |
| token_hash | text | not null — sha256 от выданного секрета (сам токен не хранится) |
| is_creator | bool | not null default false |
| last_seen_at | timestamptz | |
| created_at | timestamptz | not null default now() |

Индексы: `(room_id)`, `unique (room_id, lower(display_name))` — чтобы в комнате не было двух «Егор» (иначе путаница в назначениях).
Каскад: удаление комнаты удаляет участников; удаление участника снимает его назначения (`assignments` FK cascade) и **не** удаляет записи — записи сохраняются, но помечаются `orphaned=true` (голос не должен исчезать из-за удаления участника).

### 12.3 `videos`

| Поле | Тип | Ограничения |
|---|---|---|
| id | uuid | PK |
| room_id | text | FK rooms(id) on delete cascade |
| storage_dir | text | относительный путь `rooms/{room_id}/original` |
| original_filename | text | санитизированное, только для отображения |
| container / video_codec / audio_codec | text | из ffprobe |
| size_bytes | bigint | check > 0 |
| duration_ms | int | check 0 < duration_ms ≤ 600000 (10 мин) |
| width / height | int | |
| fps | numeric(6,3) | |
| has_audio | bool | not null — если false, комната сразу FAILED с понятной ошибкой |
| sha256 | text | для дедупликации повторной загрузки |
| created_at | timestamptz | |

Индексы: `(room_id)`, `(sha256)`.

### 12.4 `processing_jobs` и `job_stages`

`processing_jobs`: `id uuid PK`, `room_id text FK cascade`, `video_id uuid FK cascade`, `status job_status` (`QUEUED|RUNNING|FAILED|CANCELED|DONE`), `current_stage stage_name`, `progress smallint 0..100`, `attempt smallint`, `error jsonb` (`{code,message,stage,detail}`), `created_at/started_at/finished_at`, **`unique (video_id)`** — повторный запуск не создаёт второй активный джоб (идемпотентность), а обновляет существующий.

`job_stages`: `id uuid PK`, `job_id uuid FK cascade`, `stage stage_name not null`, `status stage_status`, `attempt smallint not null default 1`, `started_at/finished_at`, `duration_ms int`, `metrics jsonb` (RTF, длительность аудио, число реплик, VRAM peak), `artifacts jsonb` (относительные пути), `error jsonb`, **`unique (job_id, stage, attempt)`**.

Зачем отдельная таблица этапов: retry одного этапа, отображение прогресса по шагам, метрики производительности, «перезапустить только диаризацию» без повторного разделения.

### 12.5 `dialogue_lines`

| Поле | Тип | Ограничения |
|---|---|---|
| id | uuid | PK |
| room_id | text | FK rooms(id) on delete cascade |
| idx | int | not null — порядковый номер, `unique (room_id, idx)` (deferrable для склейки/резки) |
| start_ms | int | not null, check ≥ 0 |
| end_ms | int | not null, check end_ms > start_ms |
| speaker_label | text | not null default 'Speaker 1' |
| speaker_key | text | not null — стабильный ключ спикера (`spk_0`), нужен для группировки/переименования |
| text | text | not null default '' |
| words | jsonb | word-level `[{w,t0,t1,p}]` |
| is_short | bool | default false |
| overlaps | bool | default false — в интервале есть наложение речи |
| is_edited | bool | default false — текст/границы менял человек |
| speech_path | text | нарезка оригинала реплики (относительный путь) |
| version | int | not null default 1 — оптимистическая блокировка |
| created_at / updated_at | timestamptz | |

Индексы: `(room_id, idx)`, `(room_id, start_ms)`, `(room_id, speaker_key)`.
Проверки на уровне БД: `unique (room_id, idx)`, `check (end_ms - start_ms between 100 and 60000)`.
Ограничение `duration_ms` не хранится — вычисляется (`end_ms - start_ms`), чтобы не было рассинхрона.

### 12.6 `assignments`

| Поле | Тип | Ограничения |
|---|---|---|
| line_id | uuid | **PK**, FK dialogue_lines(id) on delete cascade |
| participant_id | uuid | FK participants(id) on delete cascade, not null |
| assigned_at | timestamptz | not null default now() |
| expires_at | timestamptz | — claim TTL (по умолчанию +15 мин, продлевается heartbeat) |
| version | int | not null default 1 |

`line_id` как PRIMARY KEY — и есть механизм конкурентности: одна реплика физически не может иметь двух держателей. Захват: `INSERT ... ON CONFLICT (line_id) DO NOTHING` → `rowcount=0` означает «уже занято». Освобождение = `DELETE`. Захват чужого «просроченного» claim: `DELETE ... WHERE line_id=$1 AND expires_at < now()` и повторный `INSERT` в одной транзакции.

Индексы: `(participant_id)`, `(expires_at)` для реапера просроченных.

### 12.7 `recordings`

| Поле | Тип | Ограничения |
|---|---|---|
| id | uuid | PK |
| line_id | uuid | FK dialogue_lines(id) on delete cascade |
| participant_id | uuid | FK participants(id) on delete set null |
| take_number | int | not null — порядковый номер дубля, `unique (line_id, take_number)` |
| status | recording_status | `UPLOADED|PROCESSING|READY|REJECTED|FAILED` |
| is_current | bool | not null default false — **уникальный частичный индекс** `unique (line_id) where is_current` гарантирует ровно один актуальный тейк |
| raw_path | text | как пришло из браузера (webm/ogg/mp4) |
| processed_path | text | 48k mono WAV, обрезан по длительности реплики, нормализован |
| duration_ms | int | измерено ffprobe после нормализации |
| sample_rate / channels | int | |
| loudness_lufs | numeric(5,2) | измерено EBU R128 (для аудита нормализации) |
| size_bytes | bigint | |
| rejected_reason | text | например `duration_exceeded` |
| orphaned | bool | default false — участник удалён, но тейк сохраняем |
| created_at | timestamptz | |

Политика хранения тейков (ответ на §17 задания): **храним все тейки до удаления комнаты**, актуальный помечается `is_current`. Диск: 10-мин ролик ≈ 150 реплик × ~20 с × 30-80 КБ (opus) ≈ 5-15 МБ на участника — незначительно на фоне исходного видео. Плюс это даёт бесплатную функцию «откатиться к предыдущему дублю», которая почти наверняка понадобится. Опционально (настройка комнаты) — удалять неактуальные тейки кроме последних N (по умолчанию не удалять).

### 12.8 `render_jobs`

| Поле | Тип | Ограничения |
|---|---|---|
| id | uuid | PK |
| room_id | text | FK rooms(id) on delete cascade |
| status | render_status | `QUEUED|MIXING|ENCODING|DONE|FAILED|CANCELED` |
| options | jsonb | `{include_unrecorded:'silent'|'original', ducking_db, loudness_target}` |
| total_lines / recorded_lines / used_lines | int | для отчёта «что попало в микс» |
| video_path / audio_mix_path / output_path | text | относительные |
| size_bytes / duration_ms | bigint/int | |
| progress | smallint | |
| error | jsonb | |
| is_current | bool | уникальный частичный индекс `unique (room_id) where is_current` — последний успешный рендер |
| created_at/started_at/finished_at | timestamptz | |

### 12.9 `room_events` (опционально, но дёшево и полезно)

`id bigserial PK`, `room_id text FK cascade`, `type text`, `payload jsonb`, `created_at timestamptz`. Используется как журнал («кто взял реплику 12», «загружен тейк 3»), для SSE-ресинхронизации (`?since_event_id=`) и для отладки. Хранить 7 дней, чистить джобом.

### 12.10 Жизненный цикл и удаление

- **Создание комнаты** → `rooms(status=CREATED)`, каталог `rooms/{id}/`.
- **Удаление комнаты** (вручную или по `expires_at`): `rooms.deleted_at = now()` → API немедленно отвечает 410 на всех эндпоинтах → джоб `purge_room` удаляет каталог целиком (`shutil.rmtree` только внутри `STORAGE_ROOT/rooms/{id}`, с проверкой реального пути) → `DELETE FROM rooms WHERE id=...` (каскад уносит детей) → удаление ключей Redis (`room:{id}:*`).
- **Повторная обработка**: `POST /jobs` c `stage=all|separate|transcribe|diarize|merge`; существующие `dialogue_lines` с `is_edited=false` пересоздаются, с `is_edited=true` — сохраняются (правки людей не затираются), назначения и записи не трогаются. Джоб получает новый `video_id`-независимый путь: старые артефакты складываются в `jobs/{job_id}/` внутри комнаты, чтобы старое и новое не смешивались.
- **Повторная запись**: новый тейк → `is_current=false` до подтверждения обработки, затем транзакционно `UPDATE recordings SET is_current=false WHERE line_id=$1` + `UPDATE ... SET is_current=true WHERE id=$2`. Никогда не бывает момента с двумя `is_current`.
- **Идемпотентность джобов**: `unique (video_id)` на `processing_jobs` + `unique (job_id, stage, attempt)` + маркер «этап завершён» проверяется до старта (если артефакт этапа есть и валиден — этап пропускается с записью `SKIPPED`). Повторная доставка задачи Dramatiq не создаёт вторую обработку.
- **Рассинхрон Redis↔БД** (задача потерялась): `reconcile`-джоб раз в минуту берёт `QUEUED`-джобы старше N секунд без активной задачи и переотправляет их (уникальные индексы не дадут задвоить).

---

## 13. Дизайн API

Базово — REST, JSON, идентификатор комнаты в пути, аутентификация участника — заголовок `X-Participant-Token` (для операций чтения достаточно `X-Room-Key` = `room_id`, то есть доступа по ссылке; для записи — токен). Ошибки — `application/problem+json` (`{type, title, status, code, detail, stage?, line_id?}`).

Что изменено против предложенного в задании варианта и почему:
1. Все соревновательные сущности (реплики, записи, назначения) **вложены в комнату**: `/api/rooms/{room_id}/lines/{line_id}`. Плоский `/api/dialogue/{line_id}` не позволяет проверить принадлежность без второго запроса и повышает риск IDOR (утечка между комнатами).
2. **Добавлен `/media/*`** — без него фронтенд не может прослушать оригинал реплики и скачать тейк, не выставляя том наружу.
3. **Добавлен `/events` (SSE)** — прогресс обработки и изменения назначений; альтернатива (поллинг всего) хуже по нагрузке и по UX.
4. **Добавлены `PATCH /lines/{id}` полем `expected_version`** и `PUT /assignment` с `expected_version` — оптимистическая блокировка вместо «последний победил».
5. `/renders` — с идентификатором рендера, а не одиночный `/render`: несколько рендеров (после дозаписи реплик) — норма, нужна история.

### 13.1 Сводная таблица эндпоинтов

| Метод | Путь | Назначение | Идемпотентность / защита |
|---|---|---|---|
| POST | `/api/rooms` | создать комнату | idempotency-key опц.; rate limit по IP |
| GET | `/api/rooms/{room_id}` | состояние комнаты (статус, настройки, счётчики) | доступ по ссылке |
| PATCH | `/api/rooms/{room_id}` | настройки (`stt_language`, `max_speakers`, `ducking`, `unrecorded_policy`) | токен участника |
| DELETE | `/api/rooms/{room_id}` | мягкое удаление (`?confirm=1`) | токен создателя |
| POST | `/api/rooms/{room_id}/participants` | регистрация участника → `{participant_id, token}` | идемпотентность по `client_id` |
| GET | `/api/rooms/{room_id}/participants` | список | |
| PATCH | `/api/participants/{participant_id}` | сменить имя/цвет | только свой |
| POST | `/api/rooms/{room_id}/video` | загрузка (multipart, потоковая) | 409 если видео уже есть и не подтверждён replace |
| GET | `/api/rooms/{room_id}/video` | метаданные | |
| GET | `/api/rooms/{room_id}/video/file` | Range-стрим видео для плеера | доступ по роли комнаты |
| POST | `/api/rooms/{room_id}/jobs` | запуск/перезапуск обработки, `{scope:'all'|'separate'|'transcribe'|'diarize'|'merge'}` | `unique(video_id)`, повтор → 200 с текущим джобом |
| GET | `/api/rooms/{room_id}/jobs/{job_id}` | статус, этапы, прогресс, ошибка | |
| POST | `/api/rooms/{room_id}/jobs/{job_id}/cancel` | отмена | |
| GET | `/api/rooms/{room_id}/events` | SSE: `stage`, `progress`, `line.updated`, `assignment.changed`, `recording.ready`, `render.updated` | keep-alive 15 с |
| GET | `/api/rooms/{room_id}/lines` | список реплик (`?speaker=&assigned_to=me|unassigned|all&since_version=`) | |
| GET | `/api/rooms/{room_id}/lines/{line_id}` | одна реплика с деталями и тейками | |
| PATCH | `/api/rooms/{room_id}/lines/{line_id}` | `{text?, speaker_label?, start_ms?, end_ms?, expected_version}` | 409 при конфликте версий |
| POST | `/api/rooms/{room_id}/lines/{line_id}/split` | разделить реплику по `at_ms` | |
| POST | `/api/rooms/{room_id}/lines/{line_id}/merge` | склеить со следующей | |
| GET | `/api/rooms/{room_id}/speakers` | агрегированные спикеры (label, key, число реплик, длительность) | |
| PATCH | `/api/rooms/{room_id}/speakers/{speaker_key}` | переименовать/объединить спикера (`merge_into`) | массовая операция в одной транзакции |
| PUT | `/api/rooms/{room_id}/lines/{line_id}/assignment` | взять/переназначить: `{participant_id, expected_version?}` | `ON CONFLICT DO NOTHING` → 409 «уже занято» |
| DELETE | `/api/rooms/{room_id}/lines/{line_id}/assignment` | освободить | |
| POST | `/api/rooms/{room_id}/lines/{line_id}/assignment/heartbeat` | продлить claim | продлевает `expires_at` |
| POST | `/api/rooms/{room_id}/assignments/bulk` | массово: `{participant_id, speaker_key?, line_ids?, scope:'my-speaker'|'all-unassigned'}` | атомарно, возвращает захваченные/занятые |
| POST | `/api/rooms/{room_id}/lines/{line_id}/recordings` | загрузить тейк (multipart, `audio/webm|ogg|mp4|wav`) | `unique(line_id, take_number)`, `Idempotency-Key` |
| GET | `/api/rooms/{room_id}/lines/{line_id}/recordings` | список тейков | |
| POST | `/api/rooms/{room_id}/lines/{line_id}/recordings/{rec_id}/current` | сделать актуальным | транзакционно |
| DELETE | `/api/rooms/{room_id}/lines/{line_id}/recordings/{rec_id}` | удалить тейк | |
| GET | `/api/rooms/{room_id}/media/{kind}/{ref}` | `kind ∈ {speech, background, original, recording}`, `ref = line_id|recording_id` | Range, `Content-Disposition: inline` |
| GET | `/api/rooms/{room_id}/subtitles.{srt|vtt}` | экспорт субтитров (post-MVP, стабильный контракт) | |
| POST | `/api/rooms/{room_id}/renders` | запуск рендера: `{include_unrecorded, ducking_db, expected_lines_version?}` | 409 если ничего не озвучено |
| GET | `/api/rooms/{room_id}/renders` | история рендеров | |
| GET | `/api/rooms/{room_id}/renders/{render_id}` | статус, `recorded/total` | |
| GET | `/api/rooms/{room_id}/renders/{render_id}/file` | скачивание (`Content-Disposition: attachment`) | Range |
| POST | `/api/rooms/{room_id}/renders/{render_id}/cancel` | отмена | |
| GET | `/api/health` / `/api/ready` | liveness/readiness (БД, Redis, дисковое место, GPU-видимость) | без авторизации |

Форматы ответов (примеры):

```json
// GET /api/rooms/{room_id}
{ "id": "k7fq3m2xbw9t4n8v6d1zsa", "title": "Ocean's Eleven, сцена 3",
  "status": "READY", "participants_count": 3, "lines_count": 142,
  "recorded_lines": 87, "settings": {"stt_language": "en", "ducking_db": -7,
  "unrecorded_policy": "silent", "record_tolerance_ms": 250} }

// GET /api/rooms/{room_id}/lines?assigned_to=me
[ { "id": "2a4f…", "idx": 12, "start_ms": 12430, "end_ms": 15820, "duration_ms": 3390,
    "speaker_label": "Speaker 2", "speaker_key": "spk_1", "text": "Where are you going?",
    "overlaps": false, "version": 1,
    "assignment": {"participant_id": "9c1e…", "display_name": "Егор", "expires_at": "…"},
    "current_recording": {"id": "77b0…", "take_number": 2, "duration_ms": 3210} } ]

// 409 при гонке за реплику
{ "type":"about:blank", "title":"Line already assigned", "status":409,
  "code":"assignment_conflict",
  "detail":"Реплику взял участник «Аня» 12 секунд назад.",
  "occupied_by": {"participant_id":"…","display_name":"Аня"} }
```

Ограничения и лимиты: тело `PATCH` ≤ 64 КБ; аудио-тейк ≤ 25 МБ; видео ≤ `MAX_UPLOAD_MB` (по умолчанию 2048); rate limit на создание комнат и загрузок (nginx `limit_req` + простой Redis-счётчик на токен/IP); таймаут запросов к ffmpeg — 10 мин на рендер.

---

## 14. Архитектура фоновых задач

**Выбор: Redis + Dramatiq.** Альтернативы и почему не они:

| Кандидат | Плюсы | Минусы для этой задачи | Вердикт |
|---|---|---|---|
| **Dramatiq** | Redis/RabbitMQ брокер, простое API, надёжные ack/retry, middleware, prefetch, `rate_limit`, аккуратные акторы, малый вес | меньше «батареек» чем у Celery (нет canvas из коробки, нет beat — расписания решаем отдельным cron/APScheduler) | **выбран** |
| Celery | гигантская экосистема, canvas, beat, знаний много | тяжёлый, конфигурация из 30 параметров, redis-транспорт исторически капризен на потерях брокера, избыточен для 7 задач | нет |
| arq | asyncio-нативный, лёгкий, знаком сообществу FastAPI | меньше возможностей по retry/приоритетам, меньше «боевых» кейсов, привязан к asyncio-миру (а ML-код синхронный) | нет |
| RQ | самый простой, Redis-only | слабее по retry-политикам/приоритетам и наблюдаемости долгих задач, синхронный | нет |

Конфигурация:
- Очереди (queue_name): `gpu` (separation, stt, diarize), `cpu` (extract, normalize, render), `system` (purge_room, reconcile, cleanup). Отдельные пулы воркеров: `worker-gpu --queues gpu,system --processes 1 --threads 1`, `worker-cpu --queues cpu --processes 2 --threads 4`.
- Политика повторов: `max_retries=2`, backoff с джиттером (30 с, 120 с), `on_failure` пишет `error` в БД и `event` в `room_events`; невозвратные ошибки (`unsupported_format`, `video_too_long`, `no_audio`) помечаются `retry=False` и сразу переводят джоб в `FAILED`.
- Таймауты: `time_limit` 15 мин, `actor per-stage` (каждый этап — отдельный актор с понятным `time_limit`).
- Прогресс: ML-этап вызывает `progress_cb(percent)` → `UPDATE job_stages` + publish в Redis Pub/Sub `room:{id}:events` (не чаще 1 события в 500 мс, чтобы не флудить).
- Идемпотентность: до старта — проверка «артефакт этапа уже есть и валиден» (hash + размер), после — запись в `job_stages`. Повторная доставка сообщения безопасна.
- Межпроцессная блокировка GPU: Redis-лок `lock:gpu:{device}` с TTL (не вместо `processes=1`, а как защита от двух контейнеров случайно на одной GPU).
- Канселирование: кооперативное — этап проверяет флаг `canceled` в Redis между чанками (для разделения/STT это реально, для отдельных неделимых вызовов — только между чанками).
- Наблюдаемость: Dramatiq middleware пишет `stage`, `duration_ms`, `queue_wait_ms`, `worker_id`, `attempt` в структурированный лог и в `job_stages.metrics`.
- Реапер: `system`-актор `reconcile_jobs` (раз в 60 с) — переотправляет потерянные задачи и снимает зависшие `RUNNING` (`heartbeat_at < now() - 3*timeout` → `FAILED` с кодом `worker_lost`).

Почему не «всё в одном джобе»: единый актор «обработай видео» не даёт per-stage retry, per-stage прогресса и переиспользования дорогих этапов (повторный запуск диаризации не должен повторять STT).

---

## 15. Архитектура хранилища файлов

```
$STORAGE_ROOT/rooms/{room_id}/
├── original/
│   ├── source.mp4                # исходник, read-only после загрузки
│   └── probe.json                # полный ffprobe
├── audio/
│   ├── mix.wav                   # 48k stereo PCM16 — извлечённая дорожка
│   └── mix_mono16k.wav           # для STT/диаризации
├── speech/
│   ├── speech.wav                # оценка речи (после разделения)
│   ├── background.wav            # mix − α·speech  ← идёт в финальный микс
│   ├── stems/                    # (опционально) music.wav, effects.wav — для отладки/A-B
│   └── segments/{line_id}.wav    # нарезка речи под реплику (прослушивание оригинала)
├── dialogue/
│   ├── words.json                # выход STT (word-level)
│   ├── diarization.json          # выход диаризации (turns, exclusive)
│   ├── lines.json                # итог merge (артефакт + отладочный снимок)
│   └── metrics.json              # RTF, длительности, версии моделей
├── recordings/
│   └── {line_id}/
│       ├── take_1.raw.webm       # как пришло из браузера
│       └── take_1.wav            # нормализованный (то, что микшируется)
├── jobs/{job_id}/…               # артефакты повторных прогонов (не перетирают текущие)
├── rendered/
│   ├── mix_{render_id}.wav       # собранная аудиодорожка
│   └── final_{render_id}.mp4     # итог
├── tmp/                          # временные файлы (внутри той же ФС → быстрый rename)
└── manifest.json                 # инвентарь: пути, размеры, sha256, версии моделей
```

Правила:
- **Никаких абсолютных путей в БД.** Только относительные; сборка — `(STORAGE_ROOT / rel).resolve()` с обязательной проверкой `str(resolved).startswith(str(root))` — это одновременно защита от path traversal и от смены корня хранилища.
- Атомарность: любой файл сначала пишется в `tmp/` (внутри каталога комнаты, на той же ФС), затем `os.replace`. Никогда не бывает «половины файла» под рабочим именем.
- Разделение метаданных и бинарников соблюдено полностью: в PostgreSQL нет ни одного BLOB.
- `manifest.json` — для инвентаризации/бэкапов/реапера (если БД и файлы разъехались, видно, что лишнее).
- Квоты: `MAX_ROOM_BYTES` (по умолчанию 6 ГБ) и проверка свободного места в `/api/ready`; при доборе > 85% — отказ в новых загрузках (503 с понятным кодом). Размер комнаты считается как `sum(files)` в `rooms.stats` (обновляется на этапе/загрузке), а не `du` на каждый запрос.
- TTL: `purge_room` удаляет каталог целиком; для неактивных комнат — по `expires_at`; осиротевшие каталоги (нет строки в БД) удаляет еженедельный `gc_orphans`.
- Хранилище спрятано за интерфейсом `StorageService` (`path_for()`, `write_stream()`, `delete_room()`, `size_of()`), чтобы в будущем заменить локальный том на S3/MinIO без переписывания сервисов.

---

## 16. Pipeline обработки видео/аудио

### 16.1 Извлечение и нормализация входа

1. `ffprobe` (JSON) → контейнер, длительность, потоки, наличие аудио, fps, поворот. Отсутствие аудио — терминальная ошибка `no_audio`.
2. Валидация: длительность ≤ 600 с, размер ≤ лимита, контейнер ∈ {mp4, webm, mov} (по `format_name`, а не по расширению), видеокодек декодируемый (проверка `ffprobe` + пробный `ffmpeg -f null` на первых секундах).
3. `ffmpeg -i source -vn -ac 2 -ar 48000 -c:a pcm_s16le mix.wav`; из него `mix_mono16k.wav` (`-ac 1 -ar 16000`).
4. Все ffmpeg-вызовы — через `subprocess.run([...], shell=False)` со списком аргументов (никогда не строка), с `-nostdin -hide_banner -loglevel error`, таймаутом и захватом stderr в лог. Имена файлов в аргументах — только сгенерированные нами (никогда из пользовательского ввода).

### 16.2 Speech separation и формирование фона

```
speech_est = Bandit_v2(mix).speech          # 48k, стерео
α          = argmin ‖ mix − α·speech_est ‖²  # по 0.5-секундным окнам с высокой энергией речи
background = mix − α·speech_est             # в диапазоне, с мягким клиппингом по пикам
```
- Обработка чанками по 20-30 с с перекрытием 1 с и кроссфейдом — иначе OOM на длинном видео и щелчки на стыках.
- Контроль качества (сохраняется в `metrics.json`): доля энергии, оставшейся в речевых окнах фона (target: −18 дБ и ниже); если выше — флаг `quality:degraded`, UI показывает предупреждение «удаление голоса вышло неполным, проверьте фон».
- **Подавление остаточной речи.** Даже хорошее разделение оставляет «призрака». Два взаимодополняющих механизма:
  1. **Дуккинг**: во временных окнах активной речи фон умножается на `10^(ducking_db/20)` (по умолчанию −7 дБ), с огибающей атаки 20 мс / релиза 120 мс. Речь заменяется новым голосом, поэтому приглушённый фон маскирует остаток.
  2. **Спектральный гейт по остатку**: опционально, для окон с высоким остатком речи — мягкое вычитание спектральной огибающей речи (Wiener-подобный гейт) вместо простого подавления. Включается только если `quality:degraded` (лишние артефакты «подводного» звука при обычном материале).
- Политика выбирается в настройках комнаты: `separation_mode = subtract_and_duck | subtract_only | stems_sum` (последний — для A/B; по умолчанию первый).

### 16.3 Порядок этапов и почему он именно такой

```
extract_audio → separate_speech → {transcribe, diarize} → merge_dialogue → READY
```
- STT и диаризация идут **по дорожке речи**, а не по миксу: на музыке и SFX Whisper галлюцинирует значительно чаще, а диаризатор путает вокал с говорящими. Это важно и часто упускается.
- STT и диаризация независимы друг от друга и могли бы идти параллельно, но на одной GPU смысла нет — запускаем последовательно, сохраняя возможность позже развести по разным GPU (этап «расширение»).
- Merge — чистая функция от двух JSON-артефактов, без модели: дешёвый повтор при смене параметров нарезки (пользователь может перезапустить нарезку с другими `max_line_ms`/`merge_gap_ms` мгновенно, без GPU).

---

## 17. Архитектура записи

### 17.1 Захват в браузере

- `navigator.mediaDevices.getUserMedia({audio: {echoCancellation:true, noiseSuppression:true, autoGainControl:true, channelCount:1, sampleRate:48000}})`.
- `MediaRecorder` с выбором mime по поддержке: `audio/webm;codecs=opus` → `audio/ogg;codecs=opus` → `audio/mp4` (Safari) → `audio/wav`.
- **Ограничение длительности:** таймер на `AudioContext.currentTime` (а не `Date.now()`, чтобы не соврал при дрейфе) + `setTimeout` подстраховкой; при достижении `line.duration_ms + tolerance` (по умолчанию **250 мс** — технический допуск под задержку `MediaRecorder`) — `recorder.stop()`.
- UI: полоса обратного отсчёта, цвет от зелёного к красному, мягкий «тик» на последней секунде; по достижении лимита запись останавливается автоматически, тейк сохраняется (не отбрасывается).
- Предзапись: `MediaRecorder` запускается за 300 мс до «нуля» и первые 300 мс обрезаются в Web Audio (`decodeAudioData` → `AudioBuffer` → точный срез) — так «первый слог» не теряется из-за задержки старта. То же делается при сохранении: клиент передаёт на сервер **точные границы** (`offset_ms`, `duration_ms`) в метаданных multipart-поля.
- Клиентская валидация: длительность, непустой сигнал (RMS > порога; иначе «микрофон, похоже, не записал звук»), отсутствие клиппинга > 3% сэмплов (предупреждение, не блокировка).

### 17.2 Загрузка и обработка на сервере

1. `POST .../recordings` multipart: поле `file` + `offset_ms` + `duration_ms_captured` + `Idempotency-Key`.
2. Сервер: предел размера, проверка mime по сигнатуре файла (первые байты) и декодируемости, `ffprobe` → длительность.
3. Валидации длительности: `captured ≤ line.duration_ms + record_tolerance_ms` → иначе `REJECTED` с кодом `duration_exceeded` (тейк всё равно сохраняем, поле `rejected_reason`, UI показывает «перезапишите короче»). Никогда не «молча обрезаем до неузнаваемости» — но и не теряем данные.
4. Нормализация (`worker-cpu`): `ffmpeg -i raw -ac 1 -ar 48000 -af "silenceremove=start...,atrim=0:{line_duration},apad=whole_dur={line_duration},loudnorm=I=-16:TP=-1.5:LRA=11" processed.wav`.
   - `atrim`+`apad` гарантируют, что длина тейка **точно** равна длительности реплики (короче — добираем тишиной, длиннее — обрезаем). Это снимает весь класс проблем при микшировании.
   - `loudnorm` (EBU R128, `I=-16 LUFS`, TP −1.5) — чтобы голоса разных участников и микрофонов звучали соизмеримо. Значение в `recordings.loudness_lufs` для аудита.
   - Моно: у всех одинаковый формат, панорама не нужна (пользователи пишут микрофоном, стерео там мнимое).
5. Транзакция: `is_current=true` для нового тейка, `false` для предыдущих; событие `recording.ready` в SSE.

### 17.3 Почему не «стриминг записи на сервер»

WebSocket-загрузка чанками во время записи даёт экономию трафика ~100 КБ на тейк и добавляет сложность (сборка чанков, восстановление после обрыва, синхронизация таймера). Для 10-минутного видео с ~150 репликами выгоды нет. Отвергнуто осознанно.

---

## 18. Rendering pipeline

```
шаг 1 (worker-cpu, numpy): собрать PCM
   out = background (float32, stereo, 48k)                       # из §16.2
   for line in lines:
       take = current_recording(line)  or  (original_speech if policy=original and none)
       if take: out[pos : pos+len] += gain(take)                 # аддитивное микширование
       при пересечении двух записей: линейный кросс-фейд/понижение второго на -6 дБ
   применение ducker-огибающей к фону (если не применён ранее)
   нормализация: peak −1 дБ, затем loudnorm I=−16 LUFS (двухпроходный)
   запись rendered/mix_{id}.wav (PCM16)

шаг 2 (worker-cpu, ffmpeg): мультиплексирование
   ffmpeg -i source.mp4 -i mix_{id}.wav
          -map 0:v:0 -map 1:a:0 -c:v copy -c:a aac -b:a 192k -ar 48000
          -movflags +faststart -shortest rendered/final_{id}.mp4
   если исходник WebM с VP9/Opus → собираем MP4 (VP9 в MP4 поддержан) либо, при сбое, MKV;
   видео НИКОГДА не ре-энкодится (кроме случая, когда контейнер несовместим → тогда fallback
   на -c:v libx264 -crf 18 с явным уведомлением в UI и в логе).
```
- Отчёт о рендере: `used_lines/recorded_lines/total_lines`, список неозвученных реплик с их таймкодами — пользователь сразу видит «12 реплик остались без голоса».
- Политика неозвученных: `silent` (по умолчанию — просто фон) либо `original` (в микс попадает оригинальная речь реплики; удобно для проверки «как звучит остальное», но не для релиза — UI подписывает это явно). Значение в `rooms.settings.unrecorded_policy`.
- Почему numpy, а не ffmpeg: при 150-250 репликах `-filter_complex` с 250 `adelay`/`amix` = медленный старт, риск `shell`-инъекции через имена файлов, невозможность unit-тестов. numpy-сборка тестируется на синтетике за миллисекунды, ffmpeg остаётся только для демультиплексирования/мультиплексирования.
- Рендер идемпотентен: `render_jobs` уникальный по `(room_id, options_hash, lines_version)` — повторный запуск при неизменных данных возвращает существующий результат (можно переключать `is_current` вручную).
- Масштаб: 10 мин ± stereo 48k float32 ≈ 230 МБ RAM — безопасно; при будущих более длинных роликах — потоковая сборка блоками по 30 с.

---

## 19. Модель комнаты и участника

- **Комната** — это «сессия проекта»: один ролик, один набор реплик, один набор участников, один итог. Идентификатор комнаты — одновременно и ссылка, и ключ доступа: `room_id` = 20 символов base32 из `secrets.token_bytes(16)` (алфавит без `0/O/1/I`, чтобы не ошибались, диктуя ссылку голосом), ≈118 бит энтропии. Перебор исключён; дополнительно nginx `limit_req` 20 r/s на IP и 404 без деталей.
- **Участник** — это не аккаунт, а «устройство+имя в комнате». При первом входе `POST /participants` возвращает `{participant_id, token}`; фронтенд кладёт в `localStorage['dub:{room_id}']`. При последующих входах — `PATCH /participants/{id}` без токена запрещён; при отсутствии токена создаётся новый участник (сообщив «привет, ты новый участник, введи имя»). Ролей нет: любой участник может взять любую реплику, редактировать любую реплику, запустить рендер. Это соответствует требованию §5 задания; право на удаление комнаты — только у создателя (`rooms.owner_participant_id`), что не роль, а защита от «случайно снёс общий результат».
- **Состояние реплики** (вычисляется, а не хранится):
  ```
  EMPTY        = нет назначения, нет тейка
  ASSIGNED     = есть назначение, нет is_current тейка
  RECORDED     = есть is_current тейк
  LOCKED       = есть is_current тейк и участник-держатель решил «готово» (опциональный флаг)
  ```
  Плюс производные признаки: `overlaps`, `is_short`, `unassigned`, `stale` (реплика отредактирована после записи → тейк может не совпадать по длине; UI предупреждает «реплика изменилась после записи, проверьте»). Поле `recordings.line_version_at_record` позволяет это отследить.
- **Права участника** (упрощённые, но необходимые): свой профиль — всегда; чужие тейки — только чтение (кроме создателя комнаты); удаление чужого claim — только он сам или создатель комнаты; рендер — любой; удаление комнаты — только создатель.
- **Присутствие**: `last_seen_at` обновляется при любом запросе (middleware), панель участников показывает «онлайн/оффлайн» по порогу 60 с. Никаких WebSocket-presence: SSE-событий комнаты достаточно.

---

## 20. Безопасность

| Угроза | Мера |
|---|---|
| Перебор `room_id` | 118 бит энтропии; nginx `limit_req` на `/api/rooms`; единый ответ 404 без деталей; опционально — фраза-пароль на вход (решение на утверждение) |
| Кража/подмена участника | токен 256 бит, в БД только `sha256`; токены сравниваются в постоянном времени; заголовок `X-Participant-Token` |
| Path traversal | в БД только относительные пути; `resolve()` + проверка префикса `STORAGE_ROOT` на каждый доступ; имя файла в `original_filename` не используется в ФС; пользовательские имена файлов не попадают в аргументы ffmpeg |
| Command injection | `subprocess` только со списком аргументов, `shell=False`; аргументы — из белого списка (числа, enum, наши сгенерированные пути); никаких «шаблонов ffmpeg» из настроек комнаты |
| Загрузка вредоносного медиа | лимит размера; проверка контейнера по `format_name`; пробное декодирование; запись под сгенерированным именем; никаких распаковок архивов; отдача файлов с `Content-Type` по типу, `X-Content-Type-Options: nosniff`, `Content-Disposition: attachment` для скачиваний |
| Утечка медиа | все медиа — через API с проверкой комнаты/участника; том не раздаётся nginx'ом; Range-запросы только внутри файла; TTL/удаление комнаты |
| DoS на ML/рендер | лимит активных джобов на комнату (1 processing, 1 render); очередь + rate limit; отказ при заполнении диска/очереди более N задач |
| Инъекции в БД | SQLAlchemy с параметрами; никакого конкатенирования SQL; Pydantic-валидация всех входов |
| XSS | React-экранирование; `dangerouslySetInnerHTML` запрещён (текст реплик — пользовательский!); `Content-Security-Policy` в nginx |
| SSRF | внешних URL от пользователя нет вообще (никаких «вставите ссылку на видео») |
| Утечка секретов | всё через ENV; `.env` не в git; секреты не логируются; HF-токен — только в образе/секрете воркера, не в API |
| Гонки/целостность | транзакции, уникальные индексы, оптимистические версии (§12.6, §13) |
| Абьюз ресурсов | rate limit на создание комнат (по IP), глобальный лимит активных комнат, `MAX_ROOM_BYTES`, TTL |
| Ошибки ffmpeg с раскрытием путей | traceback никогда не возвращается; наружу — `problem+json` с кодом; детали — только в лог |

Осознанно **не делаем**: сессии/OAuth/пароли, CSRF-токены (нет cookie-аутентификации — токен в заголовке, cookie не используется), TLS-терминацию в приложении (это уровень reverse proxy).
---

## 21. Обработка ошибок

Единый принцип: **пользователь видит код и человеческое объяснение и всегда имеет выход (повторить / изменить настройки / продолжить с тем, что есть)**, traceback уходит только в лог.

| Ситуация | Где ловится | Что видит пользователь | Что делает система |
|---|---|---|---|
| Неподдерживаемый формат | ffprobe, до постановки в очередь | «Формат не поддерживается. Нужны MP4, WebM или MOV» | 415 `unsupported_format`, файл удаляется |
| Видео > 10 мин | ffprobe | «Видео 12:30 — лимит 10:00» + фактические значения | 422 `video_too_long`, комната остаётся, можно загрузить другое |
| Файл больше лимита | nginx + backend при стриме | «Файл больше 2 ГБ» | 413 `file_too_large`, частичный файл удаляется |
| Повреждённое видео | пробное декодирование | «Файл повреждён или обрезан» | 422 `corrupt_video` |
| Нет аудиодорожки | ffprobe | «В видео нет звука — озвучивать нечего» | 422 `no_audio`, `videos.has_audio=false` |
| Речь не выделяется (музыка/тишина/документальный фон) | метрики separation | «Речь не найдена. Возможно, в видео нет диалогов» + предложение «продолжить без разделения» | флаг `quality:no_speech`, режим `separation_mode=subtract_only` |
| Ошибка STT | воркер | «Не удалось распознать речь (этап: транскрипция)» [Повторить] | `FAILED` на этапе, retry 2 раза, затем кнопка retry |
| Ошибка диаризации | воркер | «Не удалось определить спикеров, но текст распознан» [Продолжить без спикеров] | деградация: `speaker_label='Speaker 1'` для всех, джоб идёт дальше |
| Ошибка записи (браузер) | фронтенд | «Микрофон недоступен / звук слишком тихий» | тейк не отправляется; подсказки по разрешениям |
| Тейк длиннее реплики | сервер | «Запись длиннее реплики на 0.4 с — перезапишите» | тейк сохраняется с `REJECTED`, `is_current` не меняется |
| Ошибка ffmpeg (рендер) | воркер | «Не удалось собрать видео (этап: кодирование)» + код | stderr в лог, `render_jobs.error`, повтор |
| Не хватает VRAM | torch | «Не хватило памяти GPU, пробуем облегчённую модель» | авто-fallback large-v3 → turbo / band 64 → band 48 |
| Worker crash / потеря задачи | `reconcile_jobs` | «Обработка прервалась, перезапускаем» | задача переотправляется; после 2 попыток — FAILED |
| Redis недоступен | API | «Сервис временно недоступен» | 503, `/api/ready` краснеет, загрузка не принимается |
| PostgreSQL недоступен | API | 503 | никакие операции, честный отказ |
| Диск заполнен | перед загрузкой/рендером | «Нет места на диске» | 507, блокировка новых загрузок |
| Конфликт версий реплики | PATCH | «Реплику изменил другой участник, обновите страницу» | 409 + текущее состояние в теле ответа |
| Гонка за реплику | PUT assignment | «Реплику уже взял Егор» | 409 `assignment_conflict` + кто держит |
| Реплика удалена, тейк остался | FK cascade | — | тейк удаляется каскадом вместе с репликой (осознанно) |

Артефакты для разбора: `error.detail` содержит `stage`, `attempt`, `video_id`, `line_id`, `render_id`, `ffmpeg_exit_code`, `stderr_tail` (последние 20 строк). Уровни: `WARN` — деградация (диаризация без спикеров, облегчённая модель), `ERROR` — этап упал, `CRITICAL` — комната неработоспособна.

---

## 22. Конечные автоматы

**Комната**
```
CREATED ──upload──► UPLOADED ──job──► PROCESSING ──┬──► READY ──► (озвучка/рендер, статус остаётся READY)
                                                   ├──► PARTIAL  (READY с деградацией: спикеры не определены)
                                                   └──► FAILED ──retry──► PROCESSING
любое ──delete/TTL──► DELETING ──purge_room──► (строка удалена)
```

**Джоб обработки**
```
QUEUED → RUNNING → DONE
             │
             ├→ FAILED (attempt < max) → retry → QUEUED
             ├→ FAILED (terminal: unsupported_format|no_audio|too_long) 
             └→ CANCELED
```

**Этап**
```
PENDING → RUNNING → DONE | SKIPPED | FAILED | CANCELED
progress: 0..100, пишется не чаще 2 раз/сек
```
| Этап | Вес в прогрессе | Возможен ли SKIPPED |
|---|---|---|
| extract_audio | 5% | нет |
| separate_speech | 35% | да (если успешный артефакт есть) |
| transcribe | 35% | да |
| diarize | 20% | да (при отключённой диаризации) |
| merge_dialogue | 5% | нет |

**Реплика** — `EMPTY → ASSIGNED → RECORDED`, плюс переходы `assignment expires → ASSIGNED→EMPTY`, `released → ASSIGNED→EMPTY`, `line edited after recording → RECORDED(stale)`.

**Рендер** — `QUEUED → MIXING → ENCODING → DONE | FAILED | CANCELED`; при повторном рендере без изменений — возврат существующего `DONE`.

Правила переходов проверяются в сервисном слое (не в БД-триггерах — триггеры сложнее отлаживать), но дополнительно защищены ограничениями целостности (уникальные индексы, check-констрейнты), чтобы никакая ошибка в коде не могла создать неконсистентное состояние.

---

## 23. Docker-архитектура

```
docker-compose.yml
├── frontend      (nginx + собранная SPA + reverse proxy на api)     ports: 8080:80
├── api           (python:3.11-slim, fastapi/uvicorn, без torch)     ports: 8000 (внутр.)
├── worker-gpu    (тот же образ что api + nvidia runtime, torch/cuda)  queues: gpu,system
├── worker-cpu    (образ с ffmpeg/libsndfile, без torch)              queues: cpu,system
└── redis         (redis:7-alpine, appendonly yes, volume redis-data)
PostgreSQL — ВНЕ compose (внешний хост/инстанс), подключается через DATABASE_URL.
```

Детали:
- **Два образа**: `backend` (тонкий: fastapi, sqlalchemy, asyncpg, redis, pillow не нужен) и `mlworker` (CUDA base: `nvidia/cuda:12.4-runtime` + torch + ctranslate2 + pyannote + demucs/bandit + ffmpeg). API-образ не тянет 6 ГБ CUDA — деплой и рестарт API остаются быстрыми.
- **ffmpeg** обязателен в `worker-cpu` и `mlworker` (в api можно без — но проще поставить, отдельные эндпоинты могут проверять метаданные).
- **GPU**: `nvidia-container-toolkit` на хосте + в compose
  ```yaml
  worker-gpu:
    deploy:
      resources:
        reservations:
          devices: [{driver: nvidia, count: 1, capabilities: [gpu]}]
    environment: [ NVIDIA_VISIBLE_DEVICES=0 ]
    command: dramatiq app.workers.broker --queues gpu,system --processes 1 --threads 1 --prefetch 1
  ```
- **Тома**: `./data:/data` (единый STORAGE_ROOT, монтируется и в api, и в воркеры — иначе пути не совпадут), `hf-cache:/root/.cache/huggingface` (веса моделей, отдельный том — иначе каждая пересборка качает 5-10 ГБ), `redis-data`.
- **Прогрев моделей**: отдельный профиль `docker compose --profile warmup run --rm worker-gpu python -m app.ml.warmup` (скачивает и кэширует веса, требует `HF_TOKEN` для gated community-1). Продовый запуск возможен без интернета после прогрева.
- **Здоровье**: `healthcheck` у redis и api (`/api/health`), у воркеров — heartbeat в Redis; nginx: `proxy_buffering off` для `/api/rooms/*/events` (иначе SSE не течёт), `client_max_body_size 2048m`, `proxy_read_timeout 600s` на `/api/rooms/*/video`, `limit_req_zone` на создание комнат.
- **Прод-замечание**: если GPU-сервис один и на нём уже что-то крутится — ограничить VRAM нечем (нет MIG), поэтому `worker-gpu` держим с `--processes 1` и явным `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`.
- **Dev-режим**: `docker-compose.override.yml` — hot-reload api/frontend, `worker-cpu` c 1 слотом, отдельный профиль `--profile cpu-only` (ML на CPU с крошечной моделью — для e2e-тестов без GPU).

---

## 24. Среда разработки

Обязательные файлы репозитория: `docker-compose.yml`, `docker-compose.override.yml`, `.env.example`, `README.md`, `Makefile` (или `justfile`), `backend/pyproject.toml`, `frontend/package.json`, `docs/ARCHITECTURE.md`, `db/migrations/` (Alembic).

`.env.example` (полный список, ничего не хардкодим):
```env
APP_ENV=development
APP_SECRET=<64 hex>
DATABASE_URL=postgresql+asyncpg://dub:pass@postgres-host:5432/dubbing
DB_POOL_SIZE=10
REDIS_URL=redis://redis:6379/0
STORAGE_ROOT=/data
MAX_UPLOAD_MB=2048
MAX_VIDEO_MINUTES=10
MAX_ROOM_BYTES=6442450944
ROOM_TTL_DAYS=7
RECORD_TOLERANCE_MS=250
RECORD_LOUDNESS_TARGET=-16
DEFAULT_DUCKING_DB=-7
SEPARATION_MODEL=bandit_v2
SEPARATION_MODE=subtract_and_duck
STT_BACKEND=whisper           # whisper | parakeet
STT_MODEL=large-v3            # large-v3 | large-v3-turbo
STT_FALLBACK_MODEL=large-v3-turbo
DIARIZATION_MODEL=pyannote/speaker-diarization-community-1
HF_TOKEN=                     # требуется для gated моделей
MAX_SPEAKERS_DEFAULT=0        # 0 = автоопределение
MODEL_CACHE_DIR=/root/.cache/huggingface
FFMPEG_BIN=ffmpeg
FFMPEG_TIMEOUT_S=900
GPU_DEVICE=0
GPU_JOB_CONCURRENCY=1
LOG_LEVEL=INFO
LOG_FORMAT=json
ENABLE_PROMETHEUS=false
SENTRY_DSN=
```

README (структура обязательных разделов): prerequisites (Docker, nvidia-container-toolkit, внешний PostgreSQL ≥14, Python 3.11, Node 20) → quickstart → конфигурация PostgreSQL → переменные окружения → прогрева моделей (включая `HF_TOKEN` и принятие условий community-1) → запуск frontend/backend/worker → типичные проблемы (GPU не видна, gated-модель 401, ffmpeg отсутствует, порт занят) → e2e-тест → development workflow (миграции, тесты, линтеры).

Makefile-цели: `make up`, `make down`, `make logs`, `make migrate`, `make test`, `make test-ml`, `make e2e`, `make warmup`, `make fmt`, `make lint`, `make reset-db`.

Зафиксированный тулинг: Python 3.11, `ruff` + `mypy` (strict для `services/` и `ml/`), `pytest` + `pytest-asyncio`, React 19 + TS 5 + Vite, ESLint + Prettier, Vitest + Playwright.

---

## 25. Стратегия тестирования

| Уровень | Инструмент | Что обязательно покрыто | Критерий прохождения |
|---|---|---|---|
| Backend unit | pytest | алгоритм `merge_dialogue` (все правила §7.3), расчёт длительности/лимитов, инвентаризация путей (traversal-атаки), нормализация имён, генерация ID, loudness-логика | 100% ветвей алгоритма merge |
| Backend API | pytest + httpx.AsyncClient | все эндпоинты §13: happy path + 401/403/404/409/413/415/422, идемпотентность (два одинаковых POST), конфликты назначений (два параллельных захвата через `asyncio.gather`) | ни одного 500 в негативных тестах |
| База данных | pytest + реальный PostgreSQL (testcontainers или отдельная схема) | миграции вверх/вниз, каскады, уникальные частичные индексы (`is_current`, `assignments.line_id`), check-констрейнты, гонка двух `INSERT` | тесты, которые падали бы без индексов |
| Состояния обработки | pytest | переходы автомата §22, retry этапа, SKIPPED при наличии артефакта, `reconcile` потерянной задачи, terminal-ошибки | полное покрытие переходов |
| ML integration | pytest `-m ml` с фикстурой | 15-секундный клип (речь двух «спикеров» + музыка) в репозитории: разделение даёт фон приглушённым на репликах; STT даёт ≥2 сегмента с вменяемым текстом; диаризация даёт ≥2 спикера; merge даёт ≥2 реплики с корректными границами | проходит на GPU-раннере, на CPU — skip с явным сообщением |
| Frontend unit | Vitest + Testing Library | `useLineRecorder` (стоп ровно по лимиту, mime-fallback, обработка permission denied), таймкод-утилиты, фильтрация «мои реплики», логика «stale» тейка | таймерные тесты с fake timers |
| Frontend E2E | Playwright | Chrome-флаги `--use-fake-ui-for-media-stream --use-fake-device-for-media-stream --use-file-for-fake-audio-capture=fixtures/voice.wav` дают синтетический микрофон → полноценный прогон записи без железа | сценарий проходит headless |
| End-to-end | Playwright + docker compose (профиль cpu-only или GPU-раннер) | Create Room → Upload → (обработка на тестовом клипе) → Dialogue → Assign → Record → Render → Download; проверяется, что скачанный файл — валидный MP4 с длительностью исходника и **двумя** аудиодорожками-источниками (оригинальная речь удалена: проверяем, что в интервале реплики энергия фона ниже порога) | обязательный gate перед мержем в main |
| Регрессия на реальных данных | ручной чек-лист + скрипт | 3 реальных ролика разных типов (диалог в тишине, диалог под музыкой, экшен с SFX) — фиксируем метрики в `dialogue/metrics.json` и сравниваем при смене моделей | метрики не хуже предыдущего прогона |
| Нагрузка (пост-MVP) | locust | 20 одновременных участников на комнате, 5 комнат, параллельные загрузки тейков | p95 < 500 мс, без 5xx |

Принципы: тесты пишутся вместе с функциональностью (не «потом»), для каждого бага — сначала падающий тест; ML-тесты отделены маркером, чтобы CI без GPU не падал; тестовое мультимедиа-фикстуры ≤ 2 МБ и лежат в репозитории.

---

## 26. Структура проекта

```
FUnyDubY/
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── core/          config.py db.py redis.py logging.py errors.py security.py
│   │   ├── api/           deps.py  routes/{rooms,video,jobs,lines,assignments,
│   │   │                          recordings,renders,media,events,subtitles,health}.py
│   │   ├── models/        base.py room.py participant.py video.py job.py
│   │   │                  line.py assignment.py recording.py render.py event.py
│   │   ├── schemas/       room.py line.py recording.py render.py job.py common.py
│   │   ├── services/      rooms.py video.py jobs.py lines.py assignments.py
│   │   │                  recordings.py renders.py storage.py events.py
│   │   ├── workers/       broker.py tasks/{extract_audio,separate_speech,transcribe,
│   │   │                  diarize,merge_dialogue,normalize_recording,render_final,
│   │   │                  purge_room,reconcile_jobs}.py
│   │   ├── ml/            separation.py stt.py diarization.py merge.py
│   │   │                  audio_io.py warmup.py models.py
│   │   └── video/         ffmpeg.py probe.py mix.py loudness.py
│   ├── tests/             unit/ api/ db/ ml/ fixtures/voice_2spk_music.wav
│   ├── migrations/        (Alembic)
│   ├── Dockerfile         (thin: api)
│   ├── Dockerfile.ml      (CUDA: worker-gpu)
│   ├── pyproject.toml
│   └── requirements.txt
├── frontend/
│   ├── src/               (см. §11)
│   ├── e2e/               dubbing.spec.ts
│   ├── Dockerfile         (build → nginx)
│   ├── nginx.conf
│   ├── package.json
│   └── vite.config.ts
├── docs/                  ARCHITECTURE.md  API.md  ML_NOTES.md  RUNBOOK.md
├── scripts/               warmup.sh  make_fixture.py  bench_pipeline.py
├── docker-compose.yml
├── docker-compose.override.yml
├── .env.example
├── Makefile
└── README.md
```

Отличия от предложенного в задании каркаса: `video/` (ffmpeg-обёртки) вынесен отдельно от `ml/`; добавлены `workers/tasks/` (по файлу на этап — читаемо и удобно тестировать), `scripts/` (прогрев, бенчмарк, генерация фикстур), `docs/RUNBOOK.md` (что делать, когда упал воркер/заполнился диск).

---

## 27. Границы MVP

**Входит:** всё из §2 (F1-F19) — комната, загрузка MP4/WebM/MOV ≤10 мин, полный ML-конвейер с деградацией, нарезка на реплики, редактирование (текст, спикер, склейка/резка), назначение с claim/TTL, запись с микрофона с жёстким лимитом длительности, многократные тейки, рендер, скачивание, субтитры в UI, удаление/TTL комнаты, прогресс по этапам, retry.

**Не входит (и не должно «случайно появиться»):** аккаунты и роли; realtime-синхронный просмотр; загрузка готового аудио; эффекты голоса; клонирование голоса; машинный перевод; монтаж изображения; несколько видео в комнате; мобильная адаптация (desktop-first); SRT/VTT-экспорт (контракт эндпоинта есть, реализации нет); Prometheus/Grafana/Sentry.

**Критерий готовности MVP:** e2e-сценарий §25 проходит автоматически, а на реальном 10-минутном ролике с диалогом под музыкой участник может за 20 минут озвучить 30 реплик втроём и получить MP4, в котором оригинального голоса не слышно, музыка и SFX на месте, а новые голоса синхронны картинке.

---

## 28. Будущие расширения

Ближайшие (архитектура готова):
1. Экспорт SRT/WebVTT и «субтитры на языке оригинала/перевода».
2. Перевод реплик (NLLB/M2M-100/GPT-подобный) → озвучка на другом языке, тот же pipeline.
3. Голосовые пресеты и мягкая обработка вокала (EQ/де-эссер) на этапе нормализации.
4. Предпросмотр «черновой микс» в браузере (Web Audio) без полного рендера.
5. Автораспределение реплик по участникам («тебе все реплики Speaker 2»).
6. Публичная read-only ссылка на результат.
7. Голосование за лучший дубль (несколько тейков на реплику от разных людей).

Средние (требуют инфраструктурных изменений):
8. S3/MinIO вместо локального тома (интерфейс `StorageService` уже готов).
9. Промышленные диаризаторы: pyannote precision-2 через API — как «premium» режим качества.
10. Клонирование голоса / TTS-заполнение неозвученных реплик (с явной маркировкой).
11. Несколько GPU: отдельные очереди `gpu:0`, `gpu:1`, выбор по свободной VRAM.
12. Prometheus + Grafana, Sentry, OpenTelemetry-трейсы по стадиям.
13. Шардирование/файловая БД не нужны, но нужен autoscaling worker-cpu по длине очереди.

Дальние:
14. Realtime-совместный просмотр (WebRTC/WebSocket) — сознательно вне текущей архитектуры, потребует отдельного сервиса.
15. Аккаунты и истории проектов (тогда токены участников заменяются на нормальную auth без ломки схемы — `participants.user_id` nullable).

---

## 29. Технические риски

| # | Риск | Вероятность | Влияние | Митигация |
|---|---|---|---|---|
| R1 | **Остаточный «призрак» оригинального голоса** в финальном миксе — главный риск качества | высокая | высокое | вычитание с выравниванием α; дуккинг фона под речью; опциональный спектральный гейт; метрика «энергия остатка в речевых окнах» в `metrics.json`; обязательный прогон 3 типов роликов (§25) |
| R2 | Разделение режет вокал в песнях/SFX (крики, объявления) | средняя | среднее | режим `stems_sum` для A/B; UI-предупреждение в зонах с музыкой+вокалом; возможность оставить оригинал для конкретной реплики (флаг `keep_original`) |
| R3 | Диаризация «схлопывает» спикеров или путает их (фильмы: >4 спикеров, наложение, шёпот) | высокая | среднее | подсказка числа спикеров; ручное переименование/слияние спикеров; массовое переназначение; флаг `overlaps`; деградация «Speaker 1 для всех» вместо падения |
| R4 | Нарезка реплик неудобна (слишком длинные/короткие) | средняя | высокое (это UX-ядро) | `max_line_ms=12 с`, `min_line_ms=600 мс`, резание по паузам; ручные split/merge; мгновенный перепрогон merge без GPU |
| R5 | Лимит записи мешает естественной речи (запись обрывается на последнем слове) | средняя | среднее | tolerance 250 мс, предзапись 300 мс с обрезкой, обратный отсчёт, автопродолжение «второй дубль на ту же реплику»; рассмотреть опцию «реплика дублируется в следующую» |
| R6 | Различие браузеров (`MediaRecorder`, Safari/MP4) | средняя | среднее | mime-фоллбэк, отказ от WAV в пользу opus, серверная нормализация «всё в 48k mono WAV», тесты в Chromium и WebKit |
| R7 | GPU-сервис занят/недоступен/мало VRAM | средняя | высокое | одна GPU-задача, ленивая загрузка, авто-фоллбэк на turbo/меньший бэнд, чёткий `FAILED` + retry; `reconcile` |
| R8 | Gated-модель `community-1` (нужен HF-токен и принятие условий) | средняя | высокое (блокирует диаризацию) | заранее подтвердить доступ; кэш весов в томе; альтернатива — Sortformer (4 спикера) или отключение диаризации как деградация |
| R9 | Лицензии весов (NC-веса UVR-чекпоинтов/DiariZen) | средняя | среднее (юридическое) | не включать NC-модели; Bandit + MIT/CC-BY-BY варианты; фиксировать лицензии в `docs/ML_NOTES.md` |
| R10 | Долгая обработка/таймауты прокси (10-мин видео) | средняя | среднее | ML в очереди (не в HTTP-запросе), SSE вместо длинных запросов; nginx `proxy_read_timeout` |
| R11 | Рост диска (исходники + тейки + рендеры) | высокая | среднее | лимит размера комнаты, TTL 7 дней, `gc_orphans`, проверка свободного места в `/api/ready`, счётчик размера комнаты |
| R12 | Гонки при назначении реплик | средняя | среднее | уникальный индекс + `ON CONFLICT DO NOTHING` + TTL claim + тесты на параллельный захват |
| R13 | Ошибки ffmpeg на «экзотических» MOV (ProRes, переменный fps, поворот) | средняя | среднее | `-c:v copy` с фоллбэком на перекодирование только видео; сохранение метаданных поворота; пробное декодирование на загрузке |
| R14 | Рассинхрон видео и новой дорожки при переменном fps | низкая | высокое (видно глазами) | опора на таймстемпы, а не на номера кадров; все сдвиги кратны миллисекундам; контрольный тест «хлопок» на фикстуре |
| R15 | Стоимость поддержки: 7 ML-зависимостей с ломающими обновлениями | высокая | среднее | пины версий, отдельный ML-образ, `docs/ML_NOTES.md` с проверенными версиями, прогрев-скрипт, метрики качества на регрессии |

---

## 30. План реализации (roadmap)

| Этап | Содержание | Артефакт/критерий готовности |
|---|---|---|
| **0. Спайк-проверка ML (½-1 день)** | на одном реальном 3-минутном фрагменте прогнать: Bandit v2 → вычитание → faster-whisper → community-1 → merge; замерить RTF, VRAM, качество остатка голоса | `docs/ML_NOTES.md` с цифрами и 2-3 аудиопримерами; подтверждение/корректировка выбора моделей и бюджета времени |
| **1. Инфраструктура** | compose, два Dockerfile, .env.example, Makefile, прогрев весов, health-эндпоинты, structlog | `docker compose up` поднимает всё; `/api/ready` зелёный; GPU виден в worker-gpu |
| **2. Схема БД и миграции** | все таблицы §12, индексы, каскады, Alembic | `make migrate` на чистой БД; тесты каскадов и уникальных индексов проходят |
| **3. Backend core + комнаты/участники** | модели, сервисы, роуты комнат и участников, токены, rate limit | API-тесты: создание комнаты, вход, 404/401, IDOR-невозможен |
| **4. Загрузка видео** | потоковая загрузка, ffprobe, валидации, статусы | загрузка 1 ГБ файла проходит без роста RSS API; ошибки формата/длительности отдаются корректно |
| **5. Очередь и этапы-заглушки** | Dramatiq, `processing_jobs`/`job_stages`, 7 акторов с реальными ffmpeg-этапами и заглушками ML, SSE-прогресс, retry/reconcile | прогресс течёт в UI, отмена работает, повтор этапа не дублирует работу |
| **6. ML-конвейер по-настоящему** | separation + вычитание + α-выравнивание + дуккинг, STT, alignment, диаризация, merge, нарезка сегментов, метрики | ML-тест §25 на фикстуре; на реальном ролике получаются осмысленные реплики; записаны метрики |
| **7. Редактирование диалога (API)** | PATCH реплик, split/merge, спикеры, массовые операции, версии | API-тесты, включая 409 при конфликте версий |
| **8. Frontend: комната и диалог** | плеер + субтитры, редактор реплик, панель участников, состояния обработки | можно открыть комнату, увидеть реплики, отредактировать спикера, увидеть прогресс |
| **9. Назначения и «My Lines»** | claim/TTL/heartbeat, массовое назначение, экран моих реплик | два браузера: гонка за реплику даёт понятный 409; назначения видны обоим |
| **10. Запись с микрофона** | `useLineRecorder` (лимит, предзапись, mime-фоллбэк), загрузка тейка, серверная нормализация, тейки | запись останавливается точно на лимите; сервер обрезает/добирает; слышно «мой тейк»; перезапись делает актуальным последний |
| **11. Рендер и скачивание** | numpy-микс, дуккинг, loudnorm, ffmpeg-мультиплекс, отчёт о неозвученных, Range-скачивание | скачанное видео играется, оригинального голоса не слышно, музыка на месте, синхрон сохранён |
| **12. Обработка ошибок, TTL, чистка** | все коды из §21, `purge_room`, `gc_orphans`, квоты | негативные тесты; удаление комнаты чистит БД и файлы |
| **13. Тесты и e2e** | полный набор §25, Playwright-сценарий с фейковым микрофоном | e2e зелёный в CI, негативные тесты не дают 500 |
| **14. Прогон на реальном контенте и правки** | 3 типа роликов, замер качества остатка, тюнинг дуккинга/параметров нарезки | финальный отчёт с метриками; MVP объявлен готовым |

Правило дисциплины: после каждого этапа — прогон всех предыдущих тестов (CI), а функциональность не считается сделанной без теста. Этапы 1-5 дают запускаемое приложение-скелет, этапы 6-11 — рабочую озвучку; всё после 11 — доведение до «не стыдно показать».

---

# Architecture Decisions Requiring Approval

Утверждение нужно по каждому пункту. Для каждого указана рекомендация и цена альтернативы.

| # | Решение | Рекомендую | Альтернатива и её цена |
|---|---|---|---|
| ~~A1~~ **D1** | Модель разделения речи | **РЕШЕНО: Bandit v2**, fallback Demucs v4 (§8.4) | пересмотр — по триггеру из D1 (метрика остатка речи в фоне) |
| A2 | Как формируется фон | **`background = mix − α·speech`** (вычитание) | Сумма стемов music+effects: «чище» артефакты разделения, но безвозвратно теряет всё, что модель ошибочно отнесла к речи |
| A3 | Подавление остаточной речи | **Дуккинг −7 дБ под речью** + опциональный спектральный гейт при деградации | Без дуккинга: риск слышимого «призрака»; сильнее дуккинг: слышно «ныряние» музыки |
| A4 | Неозвученные реплики в финале | **Тишина (только фон)** + отчёт со списком; опция `original` для проверки | Оригинальная речь по умолчанию: финал звучит «как было», но требование «удалить оригинальный голос» не выполняется |
| ~~A5~~ **D4** | STT | **РЕШЕНО: faster-whisper large-v3 + WhisperX alignment**, turbo — fallback, Parakeet — опциональный бэкенд (§8.4) | — |
| ~~A6~~ **D5** | Диаризация | **РЕШЕНО: pyannote community-1** (exclusive), Sortformer v2.1 — fallback (§8.4) | остаётся внешняя зависимость: HF-токен и принятые условия на HF |
| A7 | Очередь задач | **Dramatiq + Redis** | Celery: больше возможностей, тяжелее; arq: asyncio-нативный, но менее «боевой»; RQ: проще, слабее retry/приоритеты |
| A8 | Микширование финала | **numpy-сборка PCM + ffmpeg только для мультиплекса (`-c:v copy`)** | ffmpeg `filter_complex` с N входами: привычнее, но хрупко и медленно на 150+ репликах |
| A9 | Хранение тейков | **Хранить все до удаления комнаты**, актуальный помечен `is_current` | Удалять старые: экономит ~50 МБ на комнату, но лишает отката к предыдущему дублю — экономия не стоит функции |
| A10 | Ограничение записи | **Жёсткий стоп на `duration + 250 мс`, серверная обрезка/добивка тишиной точно в длительность реплики**, тейк не отбрасывается | Мягкое предупреждение без обрезки: длительность мика «поплывёт»; отбрасывание длинных тейков: теряется дубль из-за 100 мс |
| A11 | Модель назначения реплик | **Claim с TTL 15 мин + heartbeat**, любой может снять свой или просроченный чужой; создатель может снять любой | Постоянное назначение до ручного снятия: чаще «зависшие» реплики; строгие роли: противоречит требованию равноправности |
| A12 | Нормализация голоса | **`loudnorm` −16 LUFS + TP −1.5, моно 48k для всех тейков** | Без нормализации: разница громкости микрофонов участников; нормализация «по пикам»: тише и непредсказуемо |
| A13 | Редактирование чужих реплик и тейков | **Любой участник правит любую реплику; чужие тейки — только чтение; удалять тейки может автор и создатель комнаты** | Только автор: ломается совместная правка диалога; все правят всё: случайная потеря чужой работы |
| A14 | Прогресс и обновления | **SSE + Redis Pub/Sub**, fallback — поллинг | WebSocket: избыточный двусторонний канал, лишняя инфраструктура |
| A15 | Стек frontend | **React 19 + TS + Vite + Tailwind + TanStack Query**, без UI-китов | Готовый UI-кит (MUI/AntD): быстрее старт, тяжелее и «не похоже на dubbing-студию» |
| A16 | Лимиты | **видео ≤ 10 мин, ≤ 2 ГБ, комната ≤ 6 ГБ, TTL 7 дней, аудио-тейк ≤ 25 МБ** | Другие значения — назовите свои |
| A17 | Доступ к комнате | **Только по ссылке (`room_id` = 20 символов base32, ~118 бит)**, без пароля | Добавить опциональную фразу-пароль на вход комнаты (дешево, но требует ещё одного состояния в UI) |
| A18 | Возможные деградации вместо отказа | **Отключение диаризации при её падении (все «Speaker 1»), fallback на turbo, A/B-режимы** | Строгий FAILED: честнее, но пользователь теряет весь результат из-за одного этапа |
| ~~A19~~ | ML-стенд | **РЕШЕНО (≈): хост найден — `egorserver`, RTX 3060 12 ГБ, CUDA 13.2, 4 vCPU, 15 ГБ RAM, 39 ГБ свободно (§9.1)** | Осталось от тебя: `nvidia-container-toolkit` + перезапуск docker, HF-токен и принятие условий `community-1`. Если docker-путь не нужен — `worker-gpu` в venv на хосте |
| A20 | Объём MVP | **F1-F19 как в §2, без SRT-экспорта, без Sentry/Prometheus, desktop-first** | Подтвердить или вычеркнуть пункты |

---

**Остановка.** Код не пишется. Жду явного подтверждения — «Архитектура утверждена, начинай реализацию».

Уже решено без твоего участия и ответа не требует: ~~A1~~, ~~A5~~, ~~A6~~ (выбор моделей, §8.4) и ~~A19~~ (GPU-хост найден, §9.1). Остальные пункты — с рекомендациями по умолчанию: если промолчишь, беру их как есть. Правки жду списком («A7 — Celery, A16 — лимит 1 ГБ, остальное ок»).
