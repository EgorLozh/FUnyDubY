"""Тесты проверки окружения: склейка строк, битые секреты."""

from __future__ import annotations

from app.scripts.check_env import validate_text

GOOD_TOKEN = "hf_" + "a" * 34  # 37 символов — как у настоящего токена


def test_valid_env_has_no_problems():
    text = (
        "# комментарий\n"
        f"HF_TOKEN={GOOD_TOKEN}\n"
        "DATABASE_URL=postgresql+asyncpg://u:p@172.17.0.1:15432/dubbing?ssl=require\n"
        "APP_SECRET=abc\n"
    )
    assert validate_text(text) == []


def test_glued_value_is_detected():
    text = f"HF_TOKEN={GOOD_TOKEN}DATABASE_URL=postgresql+asyncpg://u:p@host:5432/db\n"
    problems = validate_text(text)
    assert any("склеен" in problem for problem in problems)


def test_too_long_token_is_detected():
    text = f"HF_TOKEN={'x' * 120}\n"
    problems = validate_text(text)
    assert any("длиной" in problem for problem in problems)


def test_token_without_prefix_is_detected():
    text = "HF_TOKEN=abcdef1234567890\n"
    assert any("hf_" in problem for problem in validate_text(text))


def test_missing_token_is_reported_but_not_fatal():
    problems = validate_text("DATABASE_URL=postgresql://u:p@h/db\n")
    assert any("нет HF_TOKEN" in problem for problem in problems)


def test_line_without_equals_is_detected():
    assert any("нет «=»" in problem for problem in validate_text("JUST_A_LINE\n"))


def test_lowercase_key_is_detected():
    assert any("имя ключа" in problem for problem in validate_text("hf_token=x\n"))


def test_empty_values_are_allowed():
    text = f"HF_TOKEN={GOOD_TOKEN}\nSENTRY_DSN=\n"
    assert validate_text(text) == []
