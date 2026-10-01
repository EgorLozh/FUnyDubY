# RUNBOOK: эксплуатация и типовые проблемы

Проект: Collaborative Video Dubbing Platform. Хост: `egorserver` (Ubuntu, RTX 3060 12 ГБ,
PostgreSQL 16 вне compose на порту 15432, Docker 29.x).

## Раскладка

| Что | Где |
|---|---|
| Код (источник истины) | локально `D:\FUnyDubY` → GitHub `EgorLozh/FUnyDubY` |
| Деплой | `~/FUnyDubY` на `egorserver` |
| Bare-репозиторий для переноса | `~/FUnyDubY.git` на `egorserver` |
| Данные комнат | `~/FUnyDubY/data/rooms/{room_id}/…` (том `/data` в контейнерах) |
| Веса моделей | docker-volume `dubbing_hf-cache` |
| БД | `dubbing` (роль `dubbing`), внешняя для compose |

## Цикл деплоя

Локальный SSH-ключ к GitHub запаролен и ssh-agent на машине не работает, поэтому локальный
`git push origin` висит на запросе пароля. Рабочий маршрут — через сервер (его ключ GitHub принимает):

```bash
# 1. локально
cd /d/FUnyDubY && git commit -am "..." && GIT_SSH_COMMAND='ssh -i ~/.ssh/hermes_kachek -o BatchMode=yes' git push server main

# 2. на сервере: подтянуть, пересобрать, мигрировать, опубликовать
ssh -i ~/.ssh/hermes_kachek -p 2222 egor@155.212.24.77 \
  'cd ~/FUnyDubY && git pull -q ~/FUnyDubY.git main && docker compose up -d --build \
   && docker compose exec -T api alembic upgrade head && git push -q origin main'
```

Если локальный `git push origin` всё же нужен — включить агент от администратора:
`Set-Service ssh-agent -StartupType Automatic; Start-Service ssh-agent`, затем `ssh-add`.

## Проверки

```bash
bash scripts/smoke_api.sh                       # 21 проверка API + purge
curl -s localhost:8091/api/ready | python3 -m json.tool
docker compose ps
docker compose logs --tail=50 worker-cpu
```

## Грабли, на которые уже наступали

| Симптом | Причина | Что делать |
|---|---|---|
| `/api/ready` → `database: Connection refused` (порт 5432) | в `.env` нет строки `DATABASE_URL` — приложение ушло на дефолт `localhost:5432` | проверить `.env` (`awk -F= '{print $1}' .env`), `docker compose up -d api` после правки |
| Сообщения Dramatiq копятся в Redis, задачи не выполняются, ошибок нет | воркер не импортирует модуль с акторами | команда воркера обязана быть `dramatiq … app.workers.broker app.workers.tasks` |
| То же, но команда выглядит правильно | `--queues cpu,system` — dramatiq ждёт имена **через пробел**, а не через запятую, и слушает несуществующую очередь «cpu,system» | `--queues cpu system` |
| `DELETE /api/rooms/{id}` → 500, хотя комната удаляется | брокер Dramatiq не сконфигурирован в процессе API (актор `send()` падал с BrokerNotFound) | импорт пакета `app.workers` настраивает брокер (реализовано) |
| FastAPI: `AssertionError: Status code 204 must not have a response body` | у 204-эндпоинта нет `response_class=Response` | добавить `response_class=Response` и вернуть `Response(status_code=204)` |
| `alembic upgrade` падает на `CREATE TABLE … FOREIGN KEY` несуществующей таблицы | циклические ссылки `rooms ↔ videos`, `rooms ↔ participants` | `ForeignKey(..., use_alter=True, name=...)` — FK создаётся отдельным ALTER |
| `alembic revision --autogenerate` не может записать файл | каталог `migrations` смонтирован в api-контейнер как `:ro` | генерировать разовым контейнером с `-v $PWD/backend/migrations_out:/out` и копировать файл в репозиторий |
| `git pull` на сервере: `untracked working tree files would be overwritten` | сгенерированная миграция уже лежит в рабочем дереве | `rm -f backend/migrations/versions/<файл>.py && git pull ~/FUnyDubY.git main` |
| GPU-воркер не стартует: `could not select device driver` | на хосте нет `nvidia-container-toolkit` | `sudo apt install -y nvidia-container-toolkit && sudo systemctl restart docker`; до этого worker-gpu запускать не нужно (`docker compose up -d redis api worker-cpu frontend`) |
| `.env` с CRLF | значения приезжают с `\r` (например, сломанный `HF_TOKEN`) | `tr -d '\r' < .env > .env.lf && mv .env.lf .env`; в репозитории лежит `.gitattributes` с `eol=lf` |
| `alembic upgrade` → `type "room_status" already exists` | PG-типы переживают `drop_table` | `downgrade` в `initial_schema` теперь чистит типы; вручную: `DROP TYPE IF EXISTS room_status, job_status, stage_status, stage_name, render_status, recording_status CASCADE` |
| `alembic downgrade base` оставил таблицы/FK в неполном виде, `upgrade` падает | оборванная транзакция DDL | `make db-rebuild` (downgrade base + upgrade head) или жёстко: `DROP SCHEMA public CASCADE; CREATE SCHEMA public;` затем `alembic upgrade head` |
| FK `rooms -> videos/participants` отсутствуют после чистой схемы | автогенерация пропускает связи, помеченные `use_alter=True` | они добавлены в миграцию руками (`op.create_foreign_key` в конце `initial_schema`) — если правите модели, проверяйте наличие этих двух FK после пересборки |
| HF-модели отвечают 401 `Invalid username or password` / gated-репозиторий недоступен, хотя токен «точно есть» | `.env` склеен: значение без завершающего перевода строки, и следующая строка приклеилась к нему (`HF_TOKEN=hf_…DATABASE_URL=postgresql://…`). Тот же эффект даёт `tr -d '\r'` в файле с CR-переводами: строки сливаются в одну | `make check-env` (или `python -m app.scripts.check_env .env`) покажет склейку и подозрительную длину токена; править файл целиком (write, не `>>`), каждую пару «ключ=значение» — отдельной строкой |
| `asyncpg ... no pg_hba.conf entry for host "172.1x.0.x", user "dubbing", database "dubbing", no encryption` | в `DATABASE_URL` нет `?ssl=require`, а `pg_hba.conf` этого Postgres разрешает внешним адресам только `hostssl` | добавить `?ssl=require` (проверяется в `make check-env`); после пересоздания docker-сети контейнер получает другой адрес из диапазона 172.x, поэтому правило не «сломалось» — дело в TLS |
| GPU-воркер: `UnpicklingError: Weights only load failed` при загрузке pyannote | torch ≥ 2.6 грузит чекпоинты с `weights_only=True`, а у pyannote старый pickle-формат | переменная `TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD=1` в окружении воркера (уже задана в compose и Dockerfile.ml); чекпоинты берутся из доверенных источников (HF) |
| Нужно нормализовать переводы строк в `.env` | `tr -d '\r'` удаляет CR **вместе с разделителем**, если файл был с CR-переводами | `python -c "import pathlib;p=pathlib.Path('.env');p.write_text(p.read_text().replace('\r\n','\n').replace('\r','\n'))"` |
| `git pull` на сервере падает: `untracked working tree files would be overwritten by merge` | в рабочем дереве лежит сгенерированный файл (миграция, дамп) | `git status --short`, затем `rm` лишнего или `git checkout -- .` перед pull |

## Известные ограничения текущего состояния

* Модель разделения речи по решению D1 — Bandit v2, но её веса (Zenodo) и код автора ещё не подключены:
  адаптер честно сообщает `bandit_weights_missing`, и конвейер автоматически идёт по fallback D2 (Demucs v4).
* Диаризация требует `HF_TOKEN` и принятых условий на `pyannote/speaker-diarization-community-1`;
  при отсутствии токена этап деградирует до «все Speaker 1» вместо падения.
* Frontend — заглушка (статика + прокси), React-приложение появится на этапе 8.
* Диск сервера близок к заполнению (~5 ГБ): тяжёлые тесты гонять на коротких клипах;
  объём занимает образ ML-воркера (19 ГБ) и чужие каталоги (`~/comfyui`, 32 ГБ — не трогать).
