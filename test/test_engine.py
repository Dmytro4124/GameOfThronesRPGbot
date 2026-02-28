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


# Ми "мокаємо" (підміняємо) всі зовнішні виклики
@patch('core.engine.get_user_data')
@patch('core.engine.model.generate_content')
@patch('core.engine.Thread')  # Мокаємо потоки, щоб тест не чекав їх завершення
@patch('core.engine.check_skill_mechanics')
def test_process_game_turn_success(mock_check_skill, mock_thread, mock_generate, mock_get_user, mock_profile):
    # 1. Налаштовуємо фіктивну базу даних
    mock_get_user.return_value = (mock_profile, 2)

    # 2. Налаштовуємо фіктивний вердикт механіки
    mock_check_skill.return_value = "MECHANICAL VERDICT: SUCCESS"

    # 3. Налаштовуємо фіктивну відповідь від головного Gemini
    mock_response = MagicMock()
    mock_response.text = """
    {
        "story": "Ви успішно вдарили вартового, і він впав. Що робите далі?",
        "updates": {
            "health_impact": "none",
            "gold_impact": "earn_small",
            "time_passed": "short"
        }
    }
    """
    mock_generate.return_value = mock_response

    # 4. Виконуємо функцію, ніби гравець написав повідомлення в Telegram
    chat_id = 999
    user_input = "Я б'ю вартового"

    result_text = process_game_turn(chat_id, user_input)

    # 5. Перевіряємо результати
    assert "Ви успішно вдарили вартового" in result_text

    # Перевіряємо, чи застосувалися системні логи (gold_impact: earn_small додає золото)
    assert "💰 Золото" in result_text

    # Перевіряємо, чи зберігся стан у сесії
    assert chat_id in user_sessions
    assert user_sessions[chat_id]["state"] == "GAME_ACTIVE"