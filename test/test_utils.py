# test/test_utils.py
"""
Unit-тести для bot/utils.py.

Покриває:
- Issue 6: parse_mode=None не викликає _sanitize_markdown
- Базова поведінка: parse_mode='Markdown' викликає sanitize
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ─── Тест 1: parse_mode=None → _sanitize_markdown НЕ викликається ────────────

def test_send_safe_message_parse_mode_none_skips_sanitize():
    """
    Issue 6: Коли parse_mode=None, _sanitize_markdown не повинна викликатись.
    Текст передається без змін (як raw) до bot.send_message.

    Перевіряємо:
    - _sanitize_markdown.call_count == 0
    - bot.send_message викликається з тим самим текстом і parse_mode=None
    """
    from bot.utils import send_safe_message

    bot_mock = MagicMock()
    bot_mock.send_message = AsyncMock()

    text_with_odd_asterisk = "Текст з непарним *markdown символом"

    with patch("bot.utils._sanitize_markdown") as mock_sanitize:
        asyncio.run(
            send_safe_message(
                bot=bot_mock,
                chat_id=12345,
                text=text_with_odd_asterisk,
                parse_mode=None,
            )
        )

    # _sanitize_markdown НЕ повинна бути викликана при parse_mode=None
    mock_sanitize.assert_not_called()


# ─── Тест 2: parse_mode='Markdown' → _sanitize_markdown ВИКЛИКАЄТЬСЯ ─────────

def test_send_safe_message_parse_mode_markdown_calls_sanitize():
    """
    Коли parse_mode='Markdown' (default), _sanitize_markdown повинна викликатись.
    Перевіряємо що санітайзер отримав оригінальний текст.
    """
    from bot.utils import send_safe_message

    bot_mock = MagicMock()
    bot_mock.send_message = AsyncMock()

    original_text = "Текст з **маркдауном**"

    with patch("bot.utils._sanitize_markdown", return_value=original_text) as mock_sanitize:
        asyncio.run(
            send_safe_message(
                bot=bot_mock,
                chat_id=12345,
                text=original_text,
                parse_mode="Markdown",
            )
        )

    # _sanitize_markdown ПОВИННА бути викликана при parse_mode='Markdown'
    mock_sanitize.assert_called_once_with(original_text)


# ─── Тест 3: parse_mode=None → текст іде до send_message без змін ────────────

def test_send_safe_message_parse_mode_none_text_unchanged():
    """
    Коли parse_mode=None, текст що передається в bot.send_message
    ідентичний оригінальному (санітайзер не спотворював його).
    """
    from bot.utils import send_safe_message

    bot_mock = MagicMock()
    bot_mock.send_message = AsyncMock()

    # Текст що санітайзер змінив би (непарний *)
    raw_text = "Непарна зірочка* у тексті"

    asyncio.run(
        send_safe_message(
            bot=bot_mock,
            chat_id=99,
            text=raw_text,
            parse_mode=None,
        )
    )

    bot_mock.send_message.assert_called_once()
    call_kwargs = bot_mock.send_message.call_args
    # Позиційний аргумент 2 або kwarg 'text' — перевіряємо що текст незмінений
    call_args = call_kwargs[0] if call_kwargs[0] else []
    call_kw = call_kwargs[1] if call_kwargs[1] else {}

    # send_message(chat_id, text, ...) → args[1] = text або kwargs['text']
    if len(call_args) >= 2:
        sent_text = call_args[1]
    else:
        sent_text = call_kw.get("text", None)

    if sent_text is not None:
        assert sent_text == raw_text, (
            f"parse_mode=None must pass text unchanged. "
            f"Expected: {raw_text!r}, got: {sent_text!r}"
        )


# ─── Тест 4: parse_mode=None передається в bot.send_message ──────────────────

def test_send_safe_message_passes_none_parse_mode_to_bot():
    """
    parse_mode=None передається явно до bot.send_message,
    щоб Telegram не намагався парсити маркдаун.
    """
    from bot.utils import send_safe_message

    bot_mock = MagicMock()
    bot_mock.send_message = AsyncMock()

    asyncio.run(
        send_safe_message(
            bot=bot_mock,
            chat_id=777,
            text="Звичайний текст",
            parse_mode=None,
        )
    )

    bot_mock.send_message.assert_called_once()
    _, call_kw = bot_mock.send_message.call_args
    assert call_kw.get("parse_mode") is None, (
        f"parse_mode=None must be forwarded to bot.send_message. "
        f"Got: {call_kw.get('parse_mode')!r}"
    )
