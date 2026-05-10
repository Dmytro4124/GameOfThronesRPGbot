# test/test_help_command.py
"""
Тести для cmd_help, cmd_adminhelp, cmd_speed у bot/handlers.py.

Async-тести запускаються через asyncio.run() (без pytest-asyncio — відповідно до
патерну, прийнятого у test/test_npc_isolation.py).

Моки send_safe_message — через patch() на рівні модуля bot.handlers,
щоб перехопити вже імпортований локальний символ.

Сигнатура: send_safe_message(bot, chat_id, text, ...) — text завжди args[2].
"""

import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clean_user_sessions():
    """Чистить user_sessions до і після кожного тесту."""
    from core.engine import user_sessions
    user_sessions.clear()
    yield
    user_sessions.clear()


def _make_message(chat_id: int = 12345, user_id: int = 12345, text: str = "/help") -> MagicMock:
    """Будує мінімальний мок aiogram Message."""
    msg = MagicMock()
    msg.chat.id = chat_id
    msg.from_user.id = user_id
    msg.text = text
    msg.answer = AsyncMock()
    return msg


def _make_bot() -> MagicMock:
    """Будує мінімальний мок aiogram Bot."""
    bot = MagicMock()
    bot.send_message = AsyncMock()
    return bot


def _get_text_arg(call_args) -> str:
    """
    Витягує текстовий аргумент з call_args моку send_safe_message.
    Сигнатура: send_safe_message(bot, chat_id, text, ...) — text завжди позиція [2].
    """
    return call_args.args[2]


# ── Тест 1: cmd_help — публічна команда ──────────────────────────────────────

def test_cmd_help_public():
    """
    cmd_help повинна викликати send_safe_message рівно один раз
    і передати HELP_TEXT як третій позиційний аргумент (text).
    """
    from bot.handlers import cmd_help
    from bot.help_text import HELP_TEXT

    message = _make_message(chat_id=12345, user_id=12345, text="/help")
    bot = _make_bot()

    mock_send = AsyncMock()
    with patch("bot.handlers.send_safe_message", mock_send):
        asyncio.run(cmd_help(message, bot))

    mock_send.assert_called_once()
    assert _get_text_arg(mock_send.call_args) == HELP_TEXT, (
        f"send_safe_message не отримала HELP_TEXT. args={mock_send.call_args.args}"
    )


# ── Тест 2: кнопка «📖 Інструкція» веде до того самого тексту ────────────────

def test_help_button_same_handler():
    """
    Натискання кнопки «📖 Інструкція» концептуально веде до cmd_help —
    одна й та сама функція, той самий HELP_TEXT.
    Перевіряємо: send_safe_message(bot, chat_id, HELP_TEXT).
    """
    from bot.handlers import cmd_help
    from bot.help_text import HELP_TEXT

    message = _make_message(chat_id=55555, user_id=99999, text="📖 Інструкція")
    bot = _make_bot()

    mock_send = AsyncMock()
    with patch("bot.handlers.send_safe_message", mock_send):
        asyncio.run(cmd_help(message, bot))

    mock_send.assert_called_once()
    assert _get_text_arg(mock_send.call_args) == HELP_TEXT, (
        f"Очікувався HELP_TEXT. args={mock_send.call_args.args}"
    )


# ── Тест 3: cmd_adminhelp — адмін отримує ADMIN_HELP_TEXT ────────────────────

def test_cmd_adminhelp_for_admin():
    """
    Якщо from_user.id in ADMIN_TELEGRAM_IDS, cmd_adminhelp надсилає ADMIN_HELP_TEXT.
    """
    from bot.handlers import cmd_adminhelp
    from bot.help_text import ADMIN_HELP_TEXT
    from config import ADMIN_TELEGRAM_IDS

    admin_id = ADMIN_TELEGRAM_IDS[0]
    message = _make_message(chat_id=admin_id, user_id=admin_id)
    bot = _make_bot()

    mock_send = AsyncMock()
    with patch("bot.handlers.send_safe_message", mock_send):
        asyncio.run(cmd_adminhelp(message, bot))

    mock_send.assert_called_once()
    assert _get_text_arg(mock_send.call_args) == ADMIN_HELP_TEXT, (
        f"Адмін повинен отримати ADMIN_HELP_TEXT. args={mock_send.call_args.args}"
    )


# ── Тест 4: cmd_adminhelp — не-адмін отримує відмову ────────────────────────

def test_cmd_adminhelp_denied_for_non_admin():
    """
    Якщо from_user.id not in ADMIN_TELEGRAM_IDS, cmd_adminhelp відповідає
    '🔒 Команда недоступна.' і не надсилає ADMIN_HELP_TEXT.
    """
    from bot.handlers import cmd_adminhelp
    from bot.help_text import ADMIN_HELP_TEXT

    FAKE_NON_ADMIN_ID = 1  # Гарантовано не в ADMIN_TELEGRAM_IDS
    message = _make_message(chat_id=9999, user_id=FAKE_NON_ADMIN_ID)
    bot = _make_bot()

    mock_send = AsyncMock()
    with patch("bot.handlers.send_safe_message", mock_send):
        asyncio.run(cmd_adminhelp(message, bot))

    # Рівно один виклик
    mock_send.assert_called_once()

    passed_text = _get_text_arg(mock_send.call_args)

    # Перевіряємо: відповідь — відмова
    assert passed_text == "🔒 Команда недоступна.", (
        f"Очікувалася відмова '🔒 Команда недоступна.', отримано: {repr(passed_text)}"
    )

    # Перевіряємо: ADMIN_HELP_TEXT не передавався жодним аргументом
    assert ADMIN_HELP_TEXT not in mock_send.call_args.args, (
        "ADMIN_HELP_TEXT не повинен надсилатися не-адміну"
    )


# ── Тест 5: cmd_speed — debug log та fallback ─────────────────────────────────

def test_cmd_speed_returns_debug_text():
    """
    Sub-сценарій a: якщо в user_sessions є last_debug_time — відповідь його значення,
                    parse_mode=None.
    Sub-сценарій b: якщо сесії немає — fallback містить '❌'.
    """
    from bot.handlers import cmd_speed
    from core.engine import user_sessions

    # ── Sub-сценарій a: є логи ───────────────────────────────────────────────
    CHAT_ID_WITH_LOG = 77777
    user_sessions[CHAT_ID_WITH_LOG] = {"last_debug_time": "X-DEBUG-LOG"}

    msg_a = _make_message(chat_id=CHAT_ID_WITH_LOG, user_id=CHAT_ID_WITH_LOG)
    asyncio.run(cmd_speed(msg_a))

    msg_a.answer.assert_called_once()
    call_args_a = msg_a.answer.call_args
    assert call_args_a.args[0] == "X-DEBUG-LOG", (
        f"Очікувався 'X-DEBUG-LOG', отримано: {repr(call_args_a.args[0])}"
    )
    assert call_args_a.kwargs.get("parse_mode") is None, (
        "parse_mode має бути None"
    )

    # ── Sub-сценарій b: немає сесії — fallback ───────────────────────────────
    CHAT_ID_NO_LOG = 88888
    user_sessions.pop(CHAT_ID_NO_LOG, None)

    msg_b = _make_message(chat_id=CHAT_ID_NO_LOG, user_id=CHAT_ID_NO_LOG)
    asyncio.run(cmd_speed(msg_b))

    msg_b.answer.assert_called_once()
    call_args_b = msg_b.answer.call_args
    fallback_text = call_args_b.args[0]
    assert "❌" in fallback_text, (
        f"Fallback повинен містити '❌'. Отримано: {repr(fallback_text)}"
    )
