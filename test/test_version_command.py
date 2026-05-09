"""
Tests for /version command and BOT_VERSION constant.
No network calls, no real Telegram token required.
"""
import re
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Test 1: BOT_VERSION exists and is a valid semver string
# ---------------------------------------------------------------------------

def test_bot_version_exists_and_is_string():
    """BOT_VERSION must be a non-empty string."""
    from config import BOT_VERSION
    assert isinstance(BOT_VERSION, str), "BOT_VERSION must be str"
    assert len(BOT_VERSION) > 0, "BOT_VERSION must not be empty"


def test_bot_version_semver_format():
    """BOT_VERSION must match X.Y.Z semver pattern (digits only in each part)."""
    from config import BOT_VERSION
    semver_re = re.compile(r"^\d+\.\d+\.\d+$")
    assert semver_re.match(BOT_VERSION), (
        f"BOT_VERSION={BOT_VERSION!r} does not match semver X.Y.Z"
    )


def test_bot_version_exact_value():
    """BOT_VERSION must equal '0.1.0' as stated in the change report."""
    from config import BOT_VERSION
    assert BOT_VERSION == "0.1.0"


# ---------------------------------------------------------------------------
# Test 2 & 3: cmd_version calls send_safe_message with the right arguments
# ---------------------------------------------------------------------------

def _make_fake_message(chat_id: int = 42) -> MagicMock:
    """Return a MagicMock that looks like an aiogram Message with a chat.id."""
    msg = MagicMock()
    msg.chat = MagicMock()
    msg.chat.id = chat_id
    return msg


def test_cmd_version_calls_send_safe_message():
    """
    cmd_version must call send_safe_message exactly once,
    passing (bot, chat_id, text) with bot and chat_id matching the input objects.
    """
    from config import BOT_VERSION, MODEL_MAIN_NAME

    fake_bot = MagicMock()
    fake_message = _make_fake_message(chat_id=99)

    with patch("bot.handlers.send_safe_message", new_callable=AsyncMock) as mock_send:
        from bot.handlers import cmd_version
        asyncio.run(cmd_version(fake_message, fake_bot))

    mock_send.assert_called_once()
    call_args = mock_send.call_args

    # Positional args: bot, chat_id, text
    assert call_args.args[0] is fake_bot, "First arg must be the bot object"
    assert call_args.args[1] == 99, "Second arg must be message.chat.id"


def test_cmd_version_text_contains_bot_version():
    """The text passed to send_safe_message must include the actual BOT_VERSION value."""
    from config import BOT_VERSION

    fake_bot = MagicMock()
    fake_message = _make_fake_message(chat_id=1)

    with patch("bot.handlers.send_safe_message", new_callable=AsyncMock) as mock_send:
        from bot.handlers import cmd_version
        asyncio.run(cmd_version(fake_message, fake_bot))

    sent_text = mock_send.call_args.args[2]
    assert BOT_VERSION in sent_text, (
        f"BOT_VERSION={BOT_VERSION!r} not found in response text: {sent_text!r}"
    )


def test_cmd_version_text_contains_model_name():
    """The text passed to send_safe_message must include the actual MODEL_MAIN_NAME value."""
    from config import MODEL_MAIN_NAME

    fake_bot = MagicMock()
    fake_message = _make_fake_message(chat_id=2)

    with patch("bot.handlers.send_safe_message", new_callable=AsyncMock) as mock_send:
        from bot.handlers import cmd_version
        asyncio.run(cmd_version(fake_message, fake_bot))

    sent_text = mock_send.call_args.args[2]
    assert MODEL_MAIN_NAME in sent_text, (
        f"MODEL_MAIN_NAME={MODEL_MAIN_NAME!r} not found in response text: {sent_text!r}"
    )


def test_cmd_version_text_contains_both_values():
    """Both BOT_VERSION and MODEL_MAIN_NAME must appear in a single call's text."""
    from config import BOT_VERSION, MODEL_MAIN_NAME

    fake_bot = MagicMock()
    fake_message = _make_fake_message(chat_id=3)

    with patch("bot.handlers.send_safe_message", new_callable=AsyncMock) as mock_send:
        from bot.handlers import cmd_version
        asyncio.run(cmd_version(fake_message, fake_bot))

    sent_text = mock_send.call_args.args[2]
    assert BOT_VERSION in sent_text and MODEL_MAIN_NAME in sent_text, (
        f"Response text must contain both BOT_VERSION and MODEL_MAIN_NAME. "
        f"Got: {sent_text!r}"
    )


# ---------------------------------------------------------------------------
# Test: send_safe_message is called exactly once (no double-send)
# ---------------------------------------------------------------------------

def test_cmd_version_send_called_once():
    """cmd_version must call send_safe_message exactly once, not zero or more times."""
    fake_bot = MagicMock()
    fake_message = _make_fake_message(chat_id=4)

    with patch("bot.handlers.send_safe_message", new_callable=AsyncMock) as mock_send:
        from bot.handlers import cmd_version
        asyncio.run(cmd_version(fake_message, fake_bot))

    mock_send.assert_called_once()
