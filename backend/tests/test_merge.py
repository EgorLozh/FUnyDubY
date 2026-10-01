"""Тесты алгоритма нарезки диалога (§7.3): чистые функции, без GPU и БД."""

from __future__ import annotations

from app.ml.merge import Line, Turn, Word, assign_speakers, build_lines


def w(text: str, start: int, end: int) -> Word:
    return Word(text=text, start_ms=start, end_ms=end)


def turn(speaker: str, start: int, end: int) -> Turn:
    return Turn(speaker=speaker, start_ms=start, end_ms=end)


def text_of(lines: list[Line]) -> list[str]:
    return [line.text for line in lines]


class TestAssignSpeakers:
    def test_no_turns_gives_single_speaker(self):
        words = [w("Hello", 0, 400), w("there", 400, 800)]
        assert assign_speakers(words, []) == ["spk_0", "spk_0"]

    def test_word_goes_to_overlapping_speaker(self):
        words = [w("one", 0, 300), w("two", 1000, 1300)]
        turns = [turn("spk_0", 0, 500), turn("spk_1", 900, 1500)]
        assert assign_speakers(words, turns) == ["spk_0", "spk_1"]

    def test_word_without_overlap_goes_to_nearest_turn(self):
        words = [w("gap", 600, 700)]
        turns = [turn("spk_0", 0, 400), turn("spk_1", 2000, 2400)]
        assert assign_speakers(words, turns) == ["spk_0"]


class TestBuildLines:
    def test_empty_input(self):
        assert build_lines([], []) == []

    def test_single_line_for_continuous_speech(self):
        words = [w("Hello", 0, 400), w("world", 420, 800), w("again", 830, 1200)]
        turns = [turn("spk_0", 0, 1200)]
        lines = build_lines(words, turns)
        assert len(lines) == 1
        assert lines[0].start_ms == 0 and lines[0].end_ms == 1200

    def test_split_on_speaker_change(self):
        words = [w("Hi", 0, 300), w("Hey", 500, 800)]
        turns = [turn("spk_0", 0, 400), turn("spk_1", 450, 900)]
        lines = build_lines(words, turns)
        assert len(lines) == 2
        assert [line.speaker_key for line in lines] == ["spk_0", "spk_1"]

    def test_split_on_long_pause(self):
        words = [w("first", 0, 400), w("second", 1500, 1900)]
        lines = build_lines(words, [turn("spk_0", 0, 1900)], merge_gap_ms=350)
        assert len(lines) == 2

    def test_no_split_on_short_pause(self):
        words = [w("first", 0, 400), w("second", 600, 1000)]
        lines = build_lines(words, [turn("spk_0", 0, 1000)], merge_gap_ms=350)
        assert len(lines) == 1

    def test_sentence_boundary_splits_earlier(self):
        words = [w("Done.", 0, 400), w("Next", 700, 1000)]
        lines = build_lines(
            words,
            [turn("spk_0", 0, 1000)],
            merge_gap_ms=1000,
            sentence_gap_ms=150,
            min_line_ms=100,
        )
        assert len(lines) == 2

    def test_short_sentence_fragment_merges_back(self):
        """Короткая реплика после точки всё равно склеивается: иначе получаются обрубки."""
        words = [w("Done.", 0, 400), w("Next", 700, 1000)]
        lines = build_lines(
            words, [turn("spk_0", 0, 1000)], merge_gap_ms=1000, sentence_gap_ms=150
        )
        assert len(lines) == 1
        assert lines[0].text == "Done. Next"

    def test_long_line_is_split(self):
        words = [w(f"w{i}", i * 500, i * 500 + 400) for i in range(30)]  # 15 секунд
        lines = build_lines(words, [turn("spk_0", 0, 15_000)], max_line_ms=6000, merge_gap_ms=1000)
        assert len(lines) >= 3
        assert all(line.duration_ms <= 6000 for line in lines)

    def test_short_line_merged_with_neighbour(self):
        words = [w("Да.", 0, 300), w("Пойдём", 400, 900), w("домой", 900, 1300)]
        lines = build_lines(words, [turn("spk_0", 0, 1300)], min_line_ms=600, merge_gap_ms=1000)
        assert len(lines) == 1

    def test_short_flag_marks_interjections(self):
        words = [w("Да.", 0, 300), w("Совсем", 3000, 3400)]
        lines = build_lines(words, [turn("spk_0", 0, 3400)], min_line_ms=100, merge_gap_ms=200, short_line_ms=400)
        assert lines[0].is_short is True

    def test_overlap_flag(self):
        turns = [turn("spk_0", 0, 2000), turn("spk_1", 1000, 3000)]
        words = [w("раз", 100, 500), w("два", 1200, 1600)]
        lines = build_lines(words, turns, merge_gap_ms=100)
        assert any(line.overlaps for line in lines)

    def test_no_overlap_flag_without_overlapping_turns(self):
        turns = [turn("spk_0", 0, 900), turn("spk_1", 1000, 2000)]
        words = [w("раз", 100, 500), w("два", 1200, 1600)]
        lines = build_lines(words, turns, merge_gap_ms=100)
        assert all(not line.overlaps for line in lines)

    def test_word_join_punctuation_and_capitalisation(self):
        words = [w("hello", 0, 300), w(",", 300, 320), w("world", 330, 600), w("!", 600, 620)]
        lines = build_lines(words, [turn("spk_0", 0, 620)])
        assert lines[0].text == "Hello, world!"

    def test_words_payload_kept(self):
        words = [w("hi", 0, 200)]
        lines = build_lines(words, [turn("spk_0", 0, 200)])
        assert lines[0].words == [{"w": "hi", "t0": 0, "t1": 200, "p": None}]

    def test_line_duration_matches_bounds(self):
        words = [w("a", 100, 400), w("b", 500, 900)]
        lines = build_lines(words, [turn("spk_0", 0, 1000)])
        assert lines[0].duration_ms == 800

    def test_unsorted_words_are_sorted(self):
        words = [w("second", 500, 800), w("first", 0, 400)]
        lines = build_lines(words, [turn("spk_0", 0, 800)])
        assert lines[0].text == "First second"
