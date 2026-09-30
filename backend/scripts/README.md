# Скрипты бэкенда

Каталог для утилит, которые запускаются руками (не через API):

* `warmup_models.py` — скачать веса всех моделей в `hf-cache` (появится на этапе 6;
  логика лежит в `app/ml/warmup.py`, доступна как `make warmup`).
* `bench_pipeline.py` — замер RTF и VRAM по этапам на тестовом ролике (этап 14).
* `make_fixture.py` — генерация тестового клипа «два спикера + музыка» для ML-тестов.

Запуск — внутри контейнера:

```bash
docker compose exec -T api python scripts/<script>.py
```
