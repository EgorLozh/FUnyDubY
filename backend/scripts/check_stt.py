"""Диагностика STT прямо в контейнере воркера: версии зависимостей и пробная транскрипция.

Запуск (внутри worker-gpu):
    python scripts/check_stt.py [путь_к_wav]
Без аргумента берётся первый найденный /data/rooms/*/speech/speech.wav.

Зачем отдельный скрипт: цепочка «faster-whisper -> PyAV -> ffmpeg» ломается при
несовпадении версий (например, faster-whisper 1.1.1 вызывает av.open(metadata_errors=…),
которого нет в старых PyAV). Проверять это надо одной командой, а не через API.
"""

from __future__ import annotations

import sys
from pathlib import Path


def find_wav() -> Path | None:
    for candidate in sorted(Path("/data/rooms").glob("*/speech/speech.wav")):
        return candidate
    return None


def main() -> int:
    import av
    import ctranslate2
    import faster_whisper
    import huggingface_hub

    print(f"av:             {av.__version__}")
    print(f"faster-whisper: {faster_whisper.__version__}")
    print(f"ctranslate2:    {ctranslate2.__version__}")
    print(f"hub:            {huggingface_hub.__version__}")

    wav = Path(sys.argv[1]) if len(sys.argv) > 1 else find_wav()
    if wav is None or not wav.exists():
        print("нет тестового wav — сначала прогоните конвейер до этапа separation")
        return 2
    print(f"файл: {wav}")

    from app.ml.stt import transcribe_file

    result = transcribe_file(wav, Path("/tmp/words_check.json"))
    print(f"язык: {result.language} | слов: {len(result.words)} | backend: {result.backend}")
    print("фрагмент:", [(w["text"], w["start_ms"]) for w in result.words[:10]])
    return 0 if result.words else 1


if __name__ == "__main__":
    sys.exit(main())
