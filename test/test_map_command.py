# test/test_map_command.py
"""
Тести для /map команди:
- format_player_map() з core/world_constants.py (sync, pure function)
- cmd_map() з bot/handlers.py (async, через asyncio.run)

Async-тести запускаються через asyncio.run() (без pytest-asyncio —
відповідно до патерну, прийнятого у test/test_npc_isolation.py і test/test_help_command.py).

Моки:
- bot.handlers.get_user_data  — AsyncMock (щоб не чіпати реальні Sheets)
- bot.handlers.send_safe_message — AsyncMock (щоб не надсилати реальних повідомлень)
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


def _make_message(chat_id: int = 12345, user_id: int = 12345) -> MagicMock:
    """Будує мінімальний мок aiogram Message."""
    msg = MagicMock()
    msg.chat.id = chat_id
    msg.from_user.id = user_id
    msg.answer = AsyncMock()
    return msg


def _make_bot() -> MagicMock:
    """Будує мінімальний мок aiogram Bot."""
    bot = MagicMock()
    bot.send_message = AsyncMock()
    return bot


# ── Тест 1: format_player_map — відома локація з поточною сценою ──────────────

def test_format_player_map_known_location_with_current_scene():
    """
    Ланніспорт має 4 категорії: hub, semi_public, labor, military.
    format_player_map повинна:
    - містити заголовок "🗺️ *Ланніспорт*"
    - містити "📍 Ви тут: _Головна Пристань_" (поточна сцена)
    - містити "Головна Пристань ←" (маркер поточної сцени)
    - містити назви всіх наявних категорій: hub, semi_public, labor, military
    - НЕ містити категорію "sacred" ("Священні"), якої нема у Ланніспорті
    """
    from core.world_constants import format_player_map

    result = format_player_map("Ланніспорт", "Головна Пристань")

    assert "🗺️ *Ланніспорт*" in result, (
        f"Заголовок 'Ланніспорт' відсутній. result={repr(result)}"
    )
    assert "📍 Ви тут: _Головна Пристань_" in result, (
        f"Рядок 'Ви тут: Головна Пристань' відсутній. result={repr(result)}"
    )
    assert "Головна Пристань ←" in result, (
        f"Маркер '←' поряд з поточною сценою відсутній. result={repr(result)}"
    )
    # Перевіряємо label-частини наявних категорій
    assert "Публічні місця" in result, f"'Публічні місця' (hub) відсутні. result={repr(result)}"
    assert "Напівпублічні" in result, f"'Напівпублічні' (semi_public) відсутні. result={repr(result)}"
    assert "Робочі зони" in result, f"'Робочі зони' (labor) відсутні. result={repr(result)}"
    assert "Військові" in result, f"'Військові' (military) відсутні. result={repr(result)}"
    # Категорії, яких НЕМА у Ланніспорті — мають бути відсутні
    assert "Священні" not in result, (
        f"'Священні' (sacred) не повинна бути в Ланніспорті, але є. result={repr(result)}"
    )


# ── Тест 2: format_player_map — без поточної сцени ────────────────────────────

def test_format_player_map_no_current_scene():
    """
    Якщо current_scene == "" — маркер '←' і 'Ви тут:' не повинні з'являтися.
    Заголовок 'Вінтерфел' має бути присутнім.
    """
    from core.world_constants import format_player_map

    result = format_player_map("Вінтерфел", "")

    assert "🗺️ *Вінтерфел*" in result, (
        f"Заголовок 'Вінтерфел' відсутній. result={repr(result)}"
    )
    assert "←" not in result, (
        f"Маркер '←' не повинен бути в тексті без поточної сцени. result={repr(result)}"
    )
    assert "Ви тут:" not in result, (
        f"'Ви тут:' не повинен бути в тексті без поточної сцени. result={repr(result)}"
    )


# ── Тест 3: format_player_map — невідома локація ─────────────────────────────

def test_format_player_map_unknown_location():
    """
    Якщо локація не в LOCATION_SCENES — повертається fallback-рядок.
    Заголовок з назвою локації і фраза 'Карта цього місця ще не складена' — присутні.
    """
    from core.world_constants import format_player_map

    result = format_player_map("Атлантида", "")

    assert "Карта цього місця ще не складена" in result, (
        f"Fallback-текст відсутній. result={repr(result)}"
    )
    assert "🗺️ *Атлантида*" in result, (
        f"Заголовок 'Атлантида' відсутній у fallback. result={repr(result)}"
    )


# ── Тест 4: format_player_map — стабільний порядок категорій ─────────────────

def test_format_player_map_categories_stable_order():
    """
    Королівська Гавань містить усі 10 категорій з PLAYER_MAP_CATEGORIES.
    Перевіряємо стабільний порядок відповідно до PLAYER_MAP_CATEGORIES:
    hub → semi_public → elite → sacred → private → restricted → labor → military
    → containment → stealth
    (labels: Публічні місця → Напівпублічні → Престижні → Священні → Приватні
    → Обмежені → Робочі зони → Військові → Підземелля → Приховані)
    """
    from core.world_constants import format_player_map

    result = format_player_map("Королівська Гавань", "")

    hub_idx        = result.index("Публічні місця")
    semi_idx       = result.index("Напівпублічні")
    elite_idx      = result.index("Престижні")
    sacred_idx     = result.index("Священні")
    private_idx    = result.index("Приватні")
    restricted_idx = result.index("Обмежені")
    labor_idx      = result.index("Робочі зони")
    military_idx   = result.index("Військові")
    contain_idx    = result.index("Підземелля")
    stealth_idx    = result.index("Приховані")

    assert hub_idx < semi_idx, "hub повинен бути перед semi_public"
    assert semi_idx < elite_idx, "semi_public повинен бути перед elite"
    assert elite_idx < sacred_idx, "elite повинен бути перед sacred"
    assert sacred_idx < private_idx, "sacred повинен бути перед private"
    assert private_idx < restricted_idx, "private повинен бути перед restricted"
    assert restricted_idx < labor_idx, "restricted повинен бути перед labor"
    assert labor_idx < military_idx, "labor повинен бути перед military"
    assert military_idx < contain_idx, "military повинен бути перед containment"
    assert contain_idx < stealth_idx, "containment повинен бути перед stealth"


# ── Тест 5: cmd_map — немає профілю ───────────────────────────────────────────

def test_cmd_map_no_profile():
    """
    Якщо get_user_data повертає (None, None) — cmd_map надсилає
    'Спершу почніть гру через /start' і повертає без виклику format_player_map.
    """
    from bot.handlers import cmd_map

    message = _make_message(chat_id=12345, user_id=12345)
    bot = _make_bot()

    mock_send = AsyncMock()
    mock_get_user_data = AsyncMock(return_value=(None, None))

    with patch("bot.handlers.get_user_data", mock_get_user_data), \
         patch("bot.handlers.send_safe_message", mock_send):
        asyncio.run(cmd_map(message, bot))

    mock_send.assert_called_once()
    sent_text = mock_send.call_args.args[2]
    assert sent_text == "Спершу почніть гру через /start", (
        f"Очікувався рядок 'Спершу почніть гру через /start', отримано: {repr(sent_text)}"
    )


# ── Тест 6 (бонус): cmd_map — з профілем повертає карту ──────────────────────

def test_cmd_map_with_profile():
    """
    Якщо get_user_data повертає валідний профіль (Ланніспорт / Головна Пристань),
    cmd_map викликає send_safe_message з текстом, що починається з '🗺️ *Ланніспорт*'.
    """
    from bot.handlers import cmd_map

    profile = {
        "Поточне місцезнаходження": "Ланніспорт",
        "Поточна сцена": "Головна Пристань",
    }

    message = _make_message(chat_id=12345, user_id=12345)
    bot = _make_bot()

    mock_send = AsyncMock()
    mock_get_user_data = AsyncMock(return_value=(profile, 1))

    with patch("bot.handlers.get_user_data", mock_get_user_data), \
         patch("bot.handlers.send_safe_message", mock_send):
        asyncio.run(cmd_map(message, bot))

    mock_send.assert_called_once()
    sent_text = mock_send.call_args.args[2]
    assert sent_text.startswith("🗺️ *Ланніспорт*"), (
        f"Текст повинен починатися з '🗺️ *Ланніспорт*'. Отримано: {repr(sent_text[:80])}"
    )
