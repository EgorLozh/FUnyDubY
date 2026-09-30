SHELL := /bin/bash
COMPOSE := docker compose
API := $(COMPOSE) exec -T api
HOST ?= egor@155.212.24.77
SSH_PORT ?= 2222
SSH_KEY ?= $(HOME)/.ssh/hermes_kachek

.PHONY: help up down build logs ps migrate migration db-reset shell test test-ml health warmup fmt lint deploy smoke

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

up:            ## поднять весь стек
	$(COMPOSE) up -d --build
	@$(MAKE) health

down:          ## остановить стек
	$(COMPOSE) down

build:         ## пересобрать образы
	$(COMPOSE) build

logs:          ## логи всех сервисов
	$(COMPOSE) logs -f --tail=100

ps:            ## статус
	$(COMPOSE) ps

migrate:       ## применить миграции
	$(API) alembic upgrade head

migration:     ## сгенерировать миграцию: make migration m="add x"
	$(API) alembic revision --autogenerate -m "$(m)"

db-reset:      ## СНОС всех таблиц приложения (только dev!)
	$(API) python -m app.scripts.db_reset

health:        ## проверить готовность
	@curl -s -m 10 http://127.0.0.1:$${API_PORT:-8091}/api/ready | python -m json.tool || echo "api недоступен"

test:          ## быстрые тесты (без GPU)
	$(API) pytest -q -m "not ml"

test-ml:       ## тесты с моделями (нужен GPU)
	$(COMPOSE) run --rm worker-gpu pytest -q -m ml

warmup:        ## скачать веса моделей в том hf-cache
	$(COMPOSE) run --rm worker-gpu python -m app.ml.warmup

fmt:
	$(API) ruff format app tests

lint:
	$(API) ruff check app tests && $(API) mypy app --ignore-missing-imports

deploy:        ## выкладка: коммит -> bare-репо на сервере -> пересборка -> публикация в GitHub
	git push server main
	ssh -i $(SSH_KEY) -p $(SSH_PORT) $(HOST) 'cd ~/FUnyDubY && git pull -q ~/FUnyDubY.git main \
		&& docker compose up -d --build && docker compose exec -T api alembic upgrade head \
		&& git push -q origin main'

smoke:         ## smoke-тест API на сервере
	ssh -i $(SSH_KEY) -p $(SSH_PORT) $(HOST) 'cd ~/FUnyDubY && bash scripts/smoke_api.sh'
