import pytest
from unittest.mock import patch, MagicMock
from core.engine import process_game_turn, user_sessions


@pytest.fixture
def mock_profile():
    return {
        "Ім'я": "Тестовий Герой",
        "Поточне місцезнаходження": "Вінтерфелл",
        "Здоров'я": 100,
        "Особисте Золото": 500,
        "Бойові навички": 50,
        "Ігровий час": "День 1, Ранок"
    }


def test_process_game_turn_success():
    """
    Цей тест мокав core.engine.Thread і core.engine.model, яких більше не існує в engine.py
    (engine перейшов на asyncio.create_task і окремих model_gm_logic / model_narrator).
    Функція process_game_turn також стала async.

    Актуальне покриття process_game_turn знаходиться у test_narrator_fallback.py
    (тести 1–7), де використовуються AsyncMock і ExitStack з правильними патчами.

    Цей тест залишено з pytest.skip, щоб зберегти трасуючий слід причини.
    Завдання з перезапису цього тесту: QA-agent, окрема задача.
    """
    pytest.skip(
        "Застарілий тест: мокає core.engine.Thread і core.engine.model, яких нема в "
        "поточному engine.py. process_game_turn є async. "
        "Актуальне покриття — test_narrator_fallback.py."
    )