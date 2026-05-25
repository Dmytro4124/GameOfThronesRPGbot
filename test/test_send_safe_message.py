# test/test_send_safe_message.py
"""
Unit-тести для bot/utils.py:
- _sanitize_markdown: нова поведінка для ~, `, [ ]
- send_safe_message: fallback-ланцюжок (primary → fallback → last-resort → raise)
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, call, patch


# ─── Helpers ─────────────────────────────────────────────────────────────────

def sanitize(text: str) -> str:
    from bot.utils import _sanitize_markdown
    return _sanitize_markdown(text)


def run_async(coro):
    """Завжди створює свіжий event loop — ізолює тести один від одного."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ─── _sanitize_markdown: тильда ~ ─────────────────────────────────────────────

@pytest.mark.parametrize("text,expected_contains_tilde", [
    # непарна ~ → всі ~ видаляються
    ("~text",        False),
    # парна ~~ → лишаються
    ("~text~",       True),
    # чотири ~ → парна → лишаються
    ("~~double~~",   True),
    # три ~ → непарна → всі видаляються
    ("~a~b~",        False),
])
def test_sanitize_markdown_balances_tilde(text, expected_contains_tilde):
    result = sanitize(text)
    has_tilde = "~" in result
    assert has_tilde == expected_contains_tilde, (
        f"Input: {text!r} → got {result!r}; "
        f"expected tilde present={expected_contains_tilde}"
    )


# ─── _sanitize_markdown: бек-тік ` ────────────────────────────────────────────

@pytest.mark.parametrize("text,expected_contains_backtick", [
    # непарний ` → видаляється
    ("`code",           False),
    # парні `` → лишаються
    ("`code`",          True),
    # парні потрійні ``` → лишаються
    ("```block```",     True),
    # п'ять ` → непарна → всі видаляються
    ("`a`b`c`d`",       False),
])
def test_sanitize_markdown_balances_backticks(text, expected_contains_backtick):
    result = sanitize(text)
    has_bt = "`" in result
    assert has_bt == expected_contains_backtick, (
        f"Input: {text!r} → got {result!r}; "
        f"expected backtick present={expected_contains_backtick}"
    )


# ─── _sanitize_markdown: квадратні дужки ──────────────────────────────────────

@pytest.mark.parametrize("text", [
    "[text]",           # обидві
    "[only-open",       # тільки відкриваюча
    "only-close]",      # тільки закриваюча
    "[a][b][c]",        # кілька пар
    "no brackets here", # без дужок — залишається як є, але дужок немає
])
def test_sanitize_markdown_strips_square_brackets(text):
    result = sanitize(text)
    assert "[" not in result, f"Input: {text!r} → got {result!r}; expected no '['"
    assert "]" not in result, f"Input: {text!r} → got {result!r}; expected no ']'"


# ─── _sanitize_markdown: * та _ регресія (не зламано) ─────────────────────────

@pytest.mark.parametrize("text,marker", [
    ("*bold*",    "*"),
    ("_italic_",  "_"),
])
def test_sanitize_markdown_keeps_balanced_asterisks_and_underscores(text, marker):
    result = sanitize(text)
    assert marker in result, (
        f"Balanced marker {marker!r} must be preserved. "
        f"Input: {text!r} → got {result!r}"
    )


@pytest.mark.parametrize("text,marker", [
    ("*bold",    "*"),
    ("_italic",  "_"),
])
def test_sanitize_markdown_strips_unbalanced_asterisks(text, marker):
    result = sanitize(text)
    assert marker not in result, (
        f"Unbalanced marker {marker!r} must be removed. "
        f"Input: {text!r} → got {result!r}"
    )


# ─── send_safe_message: fallback-ланцюжок ────────────────────────────────────

def _make_bot(**kwargs):
    """Повертає AsyncMock-бот з налаштованим send_message."""
    bot = MagicMock()
    bot.send_message = AsyncMock(**kwargs)
    return bot


# Тест 6: primary send успішний → fallback не викликається

def test_send_safe_message_primary_send_succeeds():
    from bot.utils import send_safe_message

    bot = _make_bot()  # send_message завжди успішний

    run_async(send_safe_message(
        bot=bot,
        chat_id=111,
        text="Hello",
        reply_markup=None,
    ))

    assert bot.send_message.call_count == 1, (
        f"Primary call succeeded → no fallback expected. "
        f"Actual call_count={bot.send_message.call_count}"
    )


# Тест 7: primary падає (parse_mode='Markdown'), fallback (без parse_mode) успішний

def test_send_safe_message_primary_fails_fallback_succeeds():
    from bot.utils import send_safe_message

    # Перший виклик → Exception, другий → успіх
    bot = _make_bot(side_effect=[Exception("bad markdown"), None])

    # Не повинно підняти виключення
    run_async(send_safe_message(
        bot=bot,
        chat_id=222,
        text="Test text",
        reply_markup=None,
    ))

    assert bot.send_message.call_count == 2, (
        f"Expected 2 calls (primary fail + fallback success). "
        f"Actual call_count={bot.send_message.call_count}"
    )


# Тест 8: primary і fallback падають, last-resort успішний

def test_send_safe_message_primary_and_fallback_fail_last_resort_succeeds():
    from bot.utils import send_safe_message

    # Перший → Exception, другий → Exception, третій → успіх
    bot = _make_bot(side_effect=[Exception("primary fail"), Exception("fallback fail"), None])

    # Не повинно підняти виключення
    run_async(send_safe_message(
        bot=bot,
        chat_id=333,
        text="A" * 600,   # > 500 символів — щоб перевірити обрізання
        reply_markup=MagicMock(),
    ))

    assert bot.send_message.call_count == 3, (
        f"Expected 3 calls (primary fail + fallback fail + last-resort success). "
        f"Actual call_count={bot.send_message.call_count}"
    )

    # third call: текст ≤ 500 chars і reply_markup=None
    third_call_args, third_call_kwargs = bot.send_message.call_args_list[2]

    # Витягуємо text: позиційно [1] або kwarg 'text'
    if len(third_call_args) >= 2:
        sent_text = third_call_args[1]
    else:
        sent_text = third_call_kwargs.get("text", "")

    assert len(sent_text) <= 500, (
        f"last-resort must trim text to ≤500 chars. Got len={len(sent_text)}"
    )

    # reply_markup у third call повинен бути None (або відсутній)
    sent_markup = third_call_kwargs.get("reply_markup", None)
    assert sent_markup is None, (
        f"last-resort must send without reply_markup. Got: {sent_markup!r}"
    )


# Тест 9: всі три падають → raise

def test_send_safe_message_all_three_fail_raises():
    from bot.utils import send_safe_message

    bot = _make_bot(side_effect=[
        Exception("primary fail"),
        Exception("fallback fail"),
        Exception("last-resort fail"),
    ])

    with pytest.raises(Exception):
        run_async(send_safe_message(
            bot=bot,
            chat_id=444,
            text="Some text",
            reply_markup=None,
        ))

    assert bot.send_message.call_count == 3, (
        f"All three attempts must be made before raise. "
        f"Actual call_count={bot.send_message.call_count}"
    )
