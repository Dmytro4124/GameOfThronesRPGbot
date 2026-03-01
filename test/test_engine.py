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
@patch('core.engine.resolve_action_mechanics')  # ЗМІНЕНО: мокаємо нову функцію Worker'а
def test_process_game_turn_success(mock_resolve_mechanics, mock_thread, mock_generate, mock_get_user, mock_profile):
    # 1. Налаштовуємо фіктивну базу даних
    mock_get_user.return_value = (mock_profile, 2)

    # 2. Налаштовуємо фіктивний вердикт ВІД WORKER'А (Тепер він повертає tuple: текст + словник оновлень)
    worker_updates = {
        "health_impact": "none",
        "gold_impact": "earn_small",  # Золото тепер видає Worker!
        "minutes_passed": 15,
        "energy_impact": "spend_small"
    }
    mock_resolve_mechanics.return_value = ("MECHANICAL VERDICT: SUCCESS", worker_updates)

    # 3. Налаштовуємо фіктивну відповідь від головного Gemini (Тільки сюжет!)
    mock_response = MagicMock()
    mock_response.text = """
        {
            "story": "Ви успішно вдарили вартового, і він впав. Що робите далі?",
            "npc_updates": []
        }
        """
    mock_generate.return_value = mock_response

    # 4. Виконуємо функцію, ніби гравець написав повідомлення в Telegram
    chat_id = 999
    user_input = "Я б'ю вартового"

    result_text = process_game_turn(chat_id, user_input)

    # 5. Перевіряємо результати
    assert "Ви успішно вдарили вартового" in result_text

    # Перевіряємо, чи застосувалися системні логи від Worker'а (gold_impact: earn_small додає золото)
    assert "💰 Золото" in result_text