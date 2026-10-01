# Collaborative Video Dubbing Platform

Совместная озвучка видео: одна ссылка на комнату, автоматический разбор аудиодорожки
(разделение речи, STT, диаризация), запись реплик с микрофона участниками и финальная
сборка видео с сохранением музыки и SFX.

* Архитектура и все решения: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
* Эксплуатация и разбор проблем: [`docs/RUNBOOK.md`](docs/RUNBOOK.md)
* Проверка API: `bash scripts/smoke_api.sh`

## Статус

| Этап roadmap | Состояние |
|---|---|
| 1. Инфраструктура (compose, образы, health) | готово — GPU-контейнеры работают (`nvidia-container-toolkit 1.20.1`) |
| 2. Схема БД и миграции | готово — 11 таблиц, 2 миграции |
| 3. Backend core: комнаты, участники, права | готово |
| 4. Загрузка видео (ffprobe, лимиты, Range) | готово |
| 5. Очередь, этапы конвейера, SSE, реконсилятор | готово |
| 6. ML-конвейер: отделение речи, STT, диаризация, нарезка реплик | работает end-to-end, включая **Bandit v2** (D1) с весами Zenodo |
| 7. Редактирование диалога (API) | частично: чтение реплик/спикеров, аудио оригинала |
| 8-14. Назначения, запись, рендер, e2e | не начато |

Результат прогона ML-конвейера на тестовом клипе (33 с, речь поверх музыки), бэкенд Bandit v2:
`extract_audio` 0.5 с → `separate_speech` 13.5 с (Bandit, `α=1.003`) → `transcribe` 10.4 с
(large-v3, word-timestamps) → `diarize` 5.9 с (pyannote community-1) → `merge_dialogue` 0.6 с
(5 реплик с текстом и таймкодами + нарезки речи под каждую).

Проверка качества разделения с эталонной дорожкой речи (Bandit vs Demucs на одном аудио):

```bash
bash scripts/make_test_clip.sh /tmp/testclip                       # фикстура + эталоны
docker compose run --rm -v /tmp/testclip:/tmp/testclip:ro \
  -v "$PWD/backend/scripts:/srv/app/scripts:ro" worker-gpu \
  python scripts/check_separation.py /tmp/testclip/mix.wav \
  --truth /tmp/testclip/speech_truth.wav --compare
```

Проверка состояния одной командой: `bash scripts/smoke_api.sh` (сейчас 46 проверок: комнаты, участники,
права, загрузка и отказы медиа, очередь и этапы, SSE, purge).

## Проверки

```bash
bash scripts/smoke_api.sh                       # 46 проверок API + очередь + purge
curl -s localhost:8091/api/ready | python3 -m json.tool
docker compose ps
```

## Требования

* Docker Engine + Compose v2
* **PostgreSQL 16 — вне compose** (на `egorserver` слушает порт 15432, из контейнеров доступен по `172.17.0.1`)
* Для ML-воркера: NVIDIA GPU + `nvidia-container-toolkit` на хосте
* `HF_TOKEN` в `.env` — для gated-модели диаризации `pyannote/speaker-diarization-community-1`
  (нужно принять условия на странице модели)

## Быстрый старт (на сервере)

```bash
git clone git@github.com:EgorLozh/FUnyDubY.git && cd FUnyDubY
cp .env.example .env      # заполнить DATABASE_URL, APP_SECRET, HF_TOKEN
docker compose up -d --build redis api worker-cpu frontend
docker compose exec -T api alembic upgrade head
bash scripts/smoke_api.sh
```

Стек: фронтенд на `127.0.0.1:8090`, API на `127.0.0.1:8091` (наружу выводится через nginx).

### Создание БД (один раз)

```bash
sudo -u postgres psql -c "CREATE ROLE dubbing LOGIN PASSWORD '<пароль>'"
sudo -u postgres createdb -O dubbing dubbing
```

`pg_hba.conf` требует SSL для соединений с внешних адресов → в `DATABASE_URL` обязателен `?ssl=require`.

## Разработка

```bash
make up          # поднять стек
make migrate     # применить миграции
make migration m="add table"   # сгенерировать миграцию
make test        # тесты без GPU
make health      # /api/ready
make logs
```

## Деплой

Код живёт в GitHub (`EgorLozh/FUnyDubY`), но локальный SSH-ключ на этой машине
запаролен, поэтому рабочий цикл такой (см. `docs/RUNBOOK.md`):

```bash
git commit -am "..." && git push server main          # локально -> bare-репозиторий на egorserver
ssh ... 'cd ~/FUnyDubY && git pull ~/FUnyDubY.git main \
         && docker compose up -d --build \
         && docker compose exec -T api alembic upgrade head'
ssh ... 'cd ~/FUnyDubY && git push origin main'       # сервер -> GitHub
```

## Переменные окружения

Все настройки — в `.env` (шаблон: `.env.example`): подключение к БД, Redis, лимиты загрузки,
параметры нарезки реплик, выбор ML-моделей (`SEPARATION_MODEL`, `STT_MODEL`, `DIARIZATION_MODEL`),
пороги дуккинга и политика неозвученных реплик. Ничего не хардкодится в коде.
