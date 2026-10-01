"""Merge слова STT + диаризация -> реплики (§7.3 архитектуры).

Чистая функция без ML: покрывается unit-тестами, перезапускается без GPU.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.config import settings


@dataclass
class Word:
    text: str
    start_ms: int
    end_ms: int
    prob: float | None = None

    @property
    def mid_ms(self) -> int:
        return (self.start_ms + self.end_ms) // 2


@dataclass
class Turn:
    speaker: str  # "spk_0", "spk_1", ...
    start_ms: int
    end_ms: int


@dataclass
class Line:
    speaker_key: str
    start_ms: int
    end_ms: int
    text: str
    words: list[dict] = field(default_factory=list)
    overlaps: bool = False
    is_short: bool = False

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


def assign_speakers(words: list[Word], turns: list[Turn]) -> list[str]:
    """Спикер для каждого слова — по перекрытию с turns.

    В exclusive-диаризации pyannote в каждый момент активен один спикер, поэтому
    перекрытия почти всегда однозначны; при ничьей берём того, чей turn длиннее
    в зоне слова, а если turns вообще нет — spk_0.
    """
    if not turns:
        return ["spk_0"] * len(words)

    ordered = sorted(turns, key=lambda t: t.start_ms)
    result: list[str] = []
    for word in words:
        best: Turn | None = None
        best_overlap = 0
        for turn in ordered:
            if turn.start_ms > word.end_ms:
                break
            overlap = min(turn.end_ms, word.end_ms) - max(turn.start_ms, word.start_ms)
            if overlap > best_overlap:
                best, best_overlap = turn, overlap
        if best is None:
            # нет перекрытия — ближайший turn по середине слова
            best = min(ordered, key=lambda t: abs((t.start_ms + t.end_ms) // 2 - word.mid_ms))
        result.append(best.speaker)
    return result


def _sentence_end(text: str) -> bool:
    return text.strip().endswith((".", "!", "?", "…", "。", "！", "？"))


def build_lines(
    words: list[Word],
    turns: list[Turn],
    *,
    merge_gap_ms: int | None = None,
    sentence_gap_ms: int | None = None,
    min_line_ms: int | None = None,
    max_line_ms: int | None = None,
    short_line_ms: int | None = None,
) -> list[Line]:
    """Собрать реплики из слов и turns.

    Правила (§7.3):
      1. новый спикер -> новая реплика;
      2. пауза > merge_gap_ms -> новая реплика;
      3. конец предложения + пауза > sentence_gap_ms -> новая реплика;
      4. слишком длинную реплику режем по ближайшей паузе между словами (лимит max_line_ms);
      5. слишком короткие склеиваем с соседней, если тот же спикер и разрыв < 400 мс;
      6. реплики короче short_line_ms помечаются is_short.
    """
    gap = settings.merge_gap_ms if merge_gap_ms is None else merge_gap_ms
    sent_gap = settings.sentence_gap_ms if sentence_gap_ms is None else sentence_gap_ms
    min_ms = settings.min_line_ms if min_line_ms is None else min_line_ms
    max_ms = settings.max_line_ms if max_line_ms is None else max_line_ms
    short_ms = settings.short_line_ms if short_line_ms is None else short_line_ms

    if not words:
        return []

    words = sorted(words, key=lambda w: (w.start_ms, w.end_ms))
    speakers = assign_speakers(words, turns)

    lines: list[Line] = []
    current: list[Word] = [words[0]]
    current_speaker = speakers[0]

    def flush() -> None:
        if not current:
            return
        line = Line(
            speaker_key=current_speaker,
            start_ms=current[0].start_ms,
            end_ms=max(w.end_ms for w in current),
            text=_join_words([w.text for w in current]),
            words=[{"w": w.text, "t0": w.start_ms, "t1": w.end_ms, "p": w.prob} for w in current],
        )
        lines.append(line)

    for index in range(1, len(words)):
        word = words[index]
        previous = current[-1]
        speaker = speakers[index]
        pause = word.start_ms - previous.end_ms
        new_speaker = speaker != current_speaker
        long_pause = pause > gap
        sentence_break = pause > sent_gap and _sentence_end(previous.text)
        too_long = (word.end_ms - current[0].start_ms) > max_ms

        if new_speaker or long_pause or sentence_break or too_long:
            flush()
            current = [word]
            current_speaker = speaker
        else:
            current.append(word)
    flush()

    lines = _merge_short(lines, min_ms)
    lines = _enforce_duration(lines, max_ms)
    for line in lines:
        line.is_short = line.duration_ms < short_ms
    lines = _mark_overlaps(lines, turns)
    return lines


# Границы из БД (`ck_lines_duration_range`): реплика короче 100 мс или длиннее 60 с не влезает.
MIN_DB_MS = 100
MAX_DB_MS = 60_000


def _enforce_duration(lines: list["Line"], max_ms: int) -> list["Line"]:
    """Привести длительности реплик к допустимым — независимо от того, что вернул STT.

    На музыке и шуме whisper выдаёт «слова» длиной в десятки секунд: реплика, собранная из
    такого слова, вылезает за лимит БД, и вставка падает на check-constraint — из-за этого
    падал весь этап на реальном киноролике (10 минут диалога с музыкой). Поэтому режем
    слишком длинные реплики по словам, а вырожденные растягиваем до минимума.
    """
    limit = max(MIN_DB_MS + 1, min(max_ms, MAX_DB_MS))
    result: list[Line] = []
    for line in lines:
        if line.duration_ms <= limit:
            result.append(line)
            continue
        words = list(line.words or [])
        if not words:
            # Слов нет — просто обрезаем: содержимое важнее длины, а таймлайн остаётся валидным
            line.end_ms = line.start_ms + limit
            result.append(line)
            continue
        chunk: list[dict] = []
        for word in words:
            if chunk and word["t1"] - chunk[0]["t0"] > limit:
                result.append(_line_from_words(line.speaker_key, chunk))
                chunk = []
            chunk.append(word)
        if chunk:
            result.append(_line_from_words(line.speaker_key, chunk))

    for line in result:
        if line.end_ms - line.start_ms < MIN_DB_MS:
            line.end_ms = line.start_ms + MIN_DB_MS
        elif line.end_ms - line.start_ms > MAX_DB_MS:
            # Одно «слово» длиннее лимита: подрезаем его, иначе вставка снова упадёт
            line.end_ms = line.start_ms + MAX_DB_MS
            line.words = [
                {**word, "t1": min(word["t1"], line.end_ms)} for word in (line.words or [])
            ]
    return result


def _line_from_words(speaker_key: str, words: list[dict]) -> "Line":
    """Собрать реплику из нарезки слов (при разрезании длинной реплики)."""
    start = words[0]["t0"]
    end = max(word["t1"] for word in words)
    if end - start > MAX_DB_MS:
        end = start + MAX_DB_MS
    return Line(
        speaker_key=speaker_key,
        start_ms=start,
        end_ms=end,
        text=_join_words([word["w"] for word in words]),
        words=list(words),
    )


def _join_words(parts: list[str]) -> str:
    """Склейка слов в текст: без пробела перед знаками препинания, с заглавной в начале."""
    out = ""
    for index, part in enumerate(parts):
        if index == 0:
            out = part
        elif part[:1] in ",.!?;:…»)%":
            out += part
        elif out.endswith(("—", "-", "«", "(")):
            out += part
        else:
            out += " " + part
    if out and out[0].islower():
        out = out[0].upper() + out[1:]
    return out


def _merge_short(lines: list[Line], min_ms: int) -> list[Line]:
    """Склеить слишком короткие куски с соседом того же спикера, если разрыв мал."""
    if len(lines) < 2:
        return lines
    result: list[Line] = [lines[0]]
    for line in lines[1:]:
        previous = result[-1]
        small = previous.duration_ms < min_ms
        gap = line.start_ms - previous.end_ms
        if small and line.speaker_key == previous.speaker_key and gap < 400:
            previous.end_ms = line.end_ms
            previous.text = _join_words([previous.text, line.text])
            previous.words = [*previous.words, *line.words]
        else:
            result.append(line)
    return result


def _mark_overlaps(lines: list[Line], turns: list[Turn]) -> list[Line]:
    """Пометить реплики, попадающие в зону наложения речи разных спикеров."""
    if not turns:
        return lines
    overlaps: list[tuple[int, int]] = []
    ordered = sorted(turns, key=lambda t: t.start_ms)
    for i, first in enumerate(ordered):
        for second in ordered[i + 1 :]:
            if second.start_ms >= first.end_ms:
                break
            if second.speaker != first.speaker:
                start = max(first.start_ms, second.start_ms)
                end = min(first.end_ms, second.end_ms)
                if end > start:
                    overlaps.append((start, end))
    for line in lines:
        line.overlaps = any(
            min(line.end_ms, end) - max(line.start_ms, start) > 100 for start, end in overlaps
        )
    return lines
