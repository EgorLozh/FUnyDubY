"""Проверка корректности .env — защита от «склеенных» строк и битых секретов.

Повод: файл .env с токеном без завершающего перевода строки склеивается с следующей
строкой при любом `>>` (и при `tr -d '\\r'`, если в файле были CR-переводы). Внешне
всё работает — приложение стартует, БД подключается, — а HF_TOKEN уезжает 136-символьной
кашей, и gated-модели отвечают 401. Такая ошибка стоит часов отладки, поэтому
проверяем её явно и громко.

Запуск: `python -m app.scripts.check_env` (или `make check-env`).
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

GLUE_RE = re.compile(r"[A-Z][A-Z0-9_]{3,}=")
KNOWN_KEYS = {
    "HF_TOKEN",
    "DATABASE_URL",
    "APP_SECRET",
    "REDIS_URL",
    "STORAGE_ROOT",
    "PUBLIC_BASE_URL",
}


def validate_text(text: str) -> list[str]:
    problems: list[str] = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            problems.append(f"строка {number}: нет «=»: {line[:40]}")
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            problems.append(f"строка {number}: некорректное имя ключа «{key}»")
        glued = GLUE_RE.search(value)
        if glued:
            problems.append(
                f"строка {number}: внутри значения «{key}» найден ещё один ключ «{glued.group(0)}» — "
                f"файл склеен (нет перевода строки после значения)"
            )
        if key == "HF_TOKEN" and value:
            if not value.startswith("hf_"):
                problems.append(f"строка {number}: HF_TOKEN не начинается с hf_")
            elif len(value) > 60:
                problems.append(
                    f"строка {number}: HF_TOKEN длиной {len(value)} символов — похоже, к нему "
                    f"приклеено лишнее (нормальный токен ~37 символов)"
                )
    if not any(line.startswith("HF_TOKEN=") for line in text.splitlines()):
        problems.append("в файле нет HF_TOKEN (диаризация пойдёт по деградации: все Speaker 1)")
    return problems


def validate_runtime() -> list[str]:
    """Проверка уже загруженного окружения — то, что реально видит приложение."""
    problems: list[str] = []
    token = os.environ.get("HF_TOKEN", "")
    if token and (not token.startswith("hf_") or len(token) > 60):
        problems.append(f"HF_TOKEN в окружении подозрительный: длина {len(token)}")
    database_url = os.environ.get("DATABASE_URL", "")
    if database_url and GLUE_RE.search(database_url.split("://", 1)[-1]):
        problems.append("в DATABASE_URL найдено склеенное значение — проверьте .env")
    return problems


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    path = Path(argv[0] if argv else ".env")
    if not path.exists():
        print(f"Нет файла {path}")
        return 1

    problems = validate_text(path.read_text(encoding="utf-8")) + validate_runtime()
    for key in sorted({key for key in KNOWN_KEYS if re.search(rf"^{key}=", path.read_text('utf-8'), re.M)}):
        value = ""
        for line in path.read_text("utf-8").splitlines():
            if line.startswith(f"{key}="):
                value = line.split("=", 1)[1]
        masked = f"{value[:7]}…(len {len(value)})" if len(value) > 12 else ("(пусто)" if not value else "***")
        print(f"  {key:<16} {masked}")

    if problems:
        print("\nПРОБЛЕМЫ:")
        for problem in problems:
            print(" -", problem)
        return 1
    print("\n.env корректен: ключи по одному на строку, значения без склейки")
    return 0


if __name__ == "__main__":
    sys.exit(main())
