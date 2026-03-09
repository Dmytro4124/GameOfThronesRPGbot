import os
import time
import json
import re
from google import genai
from google.genai import types

from core.engine import process_game_turn, user_sessions
from database.operations import get_house_stats_data, save_user_data
from core.world import generate_initial_stats
from config import GEMINI_API_KEY_TEST

client = genai.Client(api_key=GEMINI_API_KEY_TEST)
MODEL_ID = "gemma-3-27b-it"

# === ПРОМПТИ (вбудовані в текст) ===
PLAYER_PROMPT = """
Ти — QA-тестувальник у ролі звичайного гравця у всесвіті "Гри Престолів".
Твоя мета: генерувати РІЗНОМАНІТНІ дії. Пробуй битися, вести діалоги, застосовувати хитрощі та переміщатися.
Ти реагуєш ВИКЛЮЧНО на те, що бачить гравець у тексті. 

ВАЖЛИВЕ ПРАВИЛО: НІКОЛИ не використовуй подвійні лапки (") всередині текстових значень JSON. Для прямої мови чи цитат використовуй виключно одинарні лапки (') або ялинки («»).

Ти ПОВИНЕН повернути ВИНЯТКОВО валідний JSON у такому форматі:
{
  "thought_process": "Логіка вибору дії (без подвійних лапок всередині)",
  "action": "Дія від першої особи (без подвійних лапок всередині)"
}
Ніякого іншого тексту, крім JSON.
"""

EVALUATOR_PROMPT = """
Ти — Суворий QA Аналітик.
Перевір відповідь ігрового рушія (UI) та Логи системи.
Критерії FAIL:
1. Ігнорування механіки: Гравець отримав шкоду або витратив ресурс, але в UI про це не сказано.
2. Галюцинації: Модель вигадала персонажа, якого немає в локації, або дію, яку не замовляв гравець.
3. Технічний витік: В UI потрапили системні помилки (наприклад, про ліміти запитів, API чи код).
4. God-mode: Гравець заявив неможливу дію, а система дозволила це зробити.

ВАЖЛИВЕ ПРАВИЛО: НІКОЛИ не використовуй подвійні лапки (") всередині текстових значень JSON. Використовуй виключно одинарні лапки (').

Ти ПОВИНЕН повернути ВИНЯТКОВО валідний JSON у такому форматі:
{
  "status": "PASS або FAIL",
  "reason": "Детальний опис помилок (без подвійних лапок всередині)",
  "ux_score": 8
}
Ніякого іншого тексту, крім JSON.
"""


def extract_json_from_text(text: str) -> dict:
    try:
        match = re.search(r'`{3}(?:json)?(.*?)`{3}', text, re.DOTALL)
        if match:
            return json.loads(match.group(1).strip())
        return json.loads(text.strip())
    except Exception as e:
        print(f"\n[КРИТИЧНО] Не вдалося розпарсити JSON від моделі. Сирий текст:\n{text}")
        raise e


class RPGTesterAdapter:
    def __init__(self, chat_id=999999999, char_name="Візеріс Таргарієн", house_name="Таргарієн"):
        self.chat_id = chat_id
        self.char_name = char_name
        self.house_name = house_name
        self._init_character()

    def _init_character(self):
        print(f"⚙️ Ініціалізація тестового персонажа ({self.char_name})...")
        house_data = get_house_stats_data(self.house_name)
        full_profile = generate_initial_stats(self.char_name, self.house_name, house_data)

        if full_profile:
            save_user_data(self.chat_id, full_profile, self.char_name)
            user_sessions[self.chat_id] = {"state": "GAME_ACTIVE", "history": []}
            print(f"✅ Персонажа створено у базі. Локація: {full_profile.get('Поточне місцезнаходження')}")
        else:
            raise Exception("❌ Не вдалося створити тестового персонажа. Перевір чи точно назва дому збігається з БД.")

    def process(self, action: str):
        raw_response = process_game_turn(self.chat_id, action)
        if "📊" in raw_response:
            parts = raw_response.split("📊")
            ui_text = parts[0].strip()
            logs = "📊 " + parts[1].strip()
        else:
            ui_text = raw_response.strip()
            logs = "Логи відсутні."
        return ui_text, logs


def run_test(max_turns=5):
    adapter = RPGTesterAdapter()

    player_history = [
        types.Content(role="user", parts=[types.Part.from_text(text=f"{PLAYER_PROMPT}\n\nГру розпочато.")])
    ]

    # Перший ініт-запит
    print("⏳ Очікування 10 сек перед першим ходом...")
    time.sleep(10)
    ui_text, _ = adapter.process("Я оглядаюся навколо")

    for turn in range(1, max_turns + 1):
        print(f"\n{'=' * 40}")
        print(f"⚔️ ТУРН {turn}")
        print(f"{'=' * 40}")
        print(f"🖥️ СИСТЕМА (UI): {ui_text}\n")

        # 1. Генерація дії гравця
        player_history.append(
            types.Content(role="user", parts=[types.Part.from_text(text=f"Система каже:\n{ui_text}\nЩо ти робиш?")]))

        try:
            player_resp = client.models.generate_content(
                model=MODEL_ID,
                contents=player_history,
                config=types.GenerateContentConfig(temperature=0.7)
            )
            action_data = extract_json_from_text(player_resp.text)
            player_action = action_data["action"]
            print(f"👤 ГРАВЕЦЬ (Думка: {action_data.get('thought_process', '')}):\n-> {player_action}\n")
            player_history.append(types.Content(role="model", parts=[types.Part.from_text(text=player_resp.text)]))
        except Exception as e:
            print(f"❌ Помилка Agent-Player: {e}")
            break

        print("⏳ Пауза 30 сек перед викликом рушія (щоб уникнути помилки 429)...")
        time.sleep(30)

        # 2. Обробка дії твоїм рушієм
        new_ui_text, system_logs = adapter.process(player_action)
        print(f"⚙️ ЛОГИ РУШІЯ:\n{system_logs}\n")

        print("⏳ Пауза 30 сек перед Евалюатором...")
        time.sleep(30)

        # 3. Аналіз Евалюатором
        eval_prompt = f"""
        {EVALUATOR_PROMPT}

        ДІЯ ГРАВЦЯ: {player_action}
        ВІДПОВІДЬ СИСТЕМИ (UI): {new_ui_text}
        ВНУТРІШНІ ЛОГИ СИСТЕМИ: {system_logs}
        """
        try:
            eval_resp = client.models.generate_content(
                model=MODEL_ID,
                contents=eval_prompt,
                config=types.GenerateContentConfig(temperature=0.0)
            )
            eval_data = extract_json_from_text(eval_resp.text)
            print(f"🔍 ЕВАЛЮАТОР [UX: {eval_data.get('ux_score', '?')}/10] -> {eval_data.get('status', 'ERROR')}")
            if eval_data.get('status') == 'FAIL':
                print(f"   Причина: {eval_data.get('reason', 'Не вказано')}")
                print("\n🚨 КРИТИЧНА ПОМИЛКА ЗНАЙДЕНА. ЗУПИНКА ТЕСТУ.")
                break
            else:
                print(f"   Зауваження: {eval_data.get('reason', 'ОК')}")
        except Exception as e:
            print(f"❌ Помилка Agent-Evaluator: {e}")
            break

        ui_text = new_ui_text


if __name__ == "__main__":
    print("🚀 Запуск інтеграційного тестування ігрового рушія...")
    from database.operations import load_lore_data, refresh_npc_database

    load_lore_data()
    refresh_npc_database()

    run_test(max_turns=10)