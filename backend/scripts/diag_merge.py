"""Прогнать этап нарезки в текущем процессе с дампом стека через 25 секунд.

Нужен, чтобы увидеть, где именно встаёт этап: обычный воркер молчит, а faulthandler
печатает стек всех потоков и завершает процесс.

Запуск в контейнере:
    python scripts/diag_merge.py <job_id>
"""

from __future__ import annotations

import faulthandler
import sys
import time
import uuid

faulthandler.dump_traceback_later(25, exit=True)

from app.core.logging import configure_logging  # noqa: E402

configure_logging()

from app.workers.tasks import pipeline  # noqa: E402


def main() -> int:
    job_id = sys.argv[1]
    print(f"этап merge_dialogue для джоба {job_id}, старт", flush=True)
    started = time.perf_counter()
    pipeline.merge_dialogue.fn(str(uuid.UUID(job_id)))
    print(f"готово за {time.perf_counter() - started:.2f} с", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
