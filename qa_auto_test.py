import os
import time
import json
import re
from google import genai
from google.genai import types

from core.engine import process_game_turn, user_sessions
from database.operations import get_house_stats_data, save_user_data
from core.world import generate_initial_stats
from config import GEMINI_API_KEY

client = genai.Client(api_key=GEMINI_API_KEY)
MODEL_ID = "gemma-3-27b-it"

# === ДЕТАЛІЗОВАНІ ПРОМПТИ ===

PLAYER_PROMPT = """
Ти — Автоматизований QA-агент, який тестує текстову RPG у всесвіті "Гри Престолів".
Твоя мета — виявити баги в ігровій логіці, перевіряючи межі дозволеного.

ТВІЙ АЛГОРИТМ ДІЙ:
1. Уважно прочитай текст у блоці "Система каже".
2. Чергуй різні типи тестування:
   - Адекватні дії: розмова з існуючими NPC, огляд локації, звичайне переміщення.
   - Стрес-тести: спробуй атакувати нейтральних NPC, щоб перевірити бойову систему.
   - Тести на галюцинації: спробуй використати магію (вогняні кулі, телепортацію) або дістати зброю, якої в тебе немає. Рушій ПОВИНЕН тобі відмовити.

ЖОРСТКІ ПРАВИЛА ФОРМАТУВАННЯ:
- Ти повертаєш ВИКЛЮЧНО валідний JSON. Жодного тексту до або після JSON.
- НІКОЛИ не використовуй подвійні лапки (") всередині значень полів.
- Якщо потрібні лапки всередині тексту — використовуй тільки одинарні (') або ялинки («»).

ПРИКЛАД ТВОЄЇ ВІДПОВІДІ:
{
  "thought_process": "Система описала вартового. Я спробую перевірити механіку бою і заявлю неможливу дію, щоб подивитися, чи заблокує її гра.",
  "action": "Я дістаю свій магічний 'Вогняний Меч' і відрубую вартовому голову."
}
"""

EVALUATOR_PROMPT = """
Ти — Головний QA-Аналітик ігрового рушія. 
Твоє завдання — звірити дію гравця, згенерований художній текст (UI) та приховані системні логи рушія, щоб знайти баги.

КРИТЕРІЇ КРИТИЧНОЇ ПОМИЛКИ (FAIL):
1. ВИКРИВЛЕННЯ СТАНУ (God-mode): Гравець зробив неможливу дію (магія, телепорт, вбивство без зброї), і UI дозволив цьому статися замість того, щоб написати 'Це неможливо'.
2. РОЗСИНХРОН ГАЛЮЦИНАЦІЙ: У логах написано '[БЛОКУВАННЯ ГАЛЮЦИНАЦІЇ] ШІ придумав NPC...', АЛЕ цей NPC все одно детально описується в UI та взаємодіє з гравцем. Це критичний баг рушія.
3. ВИТІК ТЕХНІЧНИХ ДАНИХ: В UI для гравця вилізли системні логи, помилки Python (Traceback), повідомлення про ліміти API (429, 400), або теги на кшталт [STATS].
4. ІГНОРУВАННЯ МЕХАНІКИ: У логах є зміна характеристик (наприклад, -10 HP), але в художньому тексті UI про це не сказано ні слова (гравець не знає, що отримав шкоду).

ЖОРСТКІ ПРАВИЛА ФОРМАТУВАННЯ:
- Ти повертаєш ВИКЛЮЧНО валідний JSON.
- НІКОЛИ не використовуй подвійні лапки (") всередині текстових значень. Використовуй одинарні (').

ПРИКЛАД ТВОЄЇ ВІДПОВІДІ (ЯКЩО БАГ):
{
  "status": "FAIL",
  "reason": "У логах зафіксовано блокування NPC 'Ліра', але в UI цей персонаж продовжує розмовляти з гравцем. Логіка бекенду не синхронізована з генератором сюжету.",
  "ux_score": 3
}

ПРИКЛАД ТВОЄЇ ВІДПОВІДІ (ЯКЩО ВСЕ ОК):
{
  "status": "PASS",
  "reason": "Дія гравця оброблена коректно. Спроба використати магію була відхилена ігровим рушієм в UI. Логи чисті.",
  "ux_score": 8
}
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

    # Використовуємо f-string безпечно, не ламаючи JSON
    init_prompt = f"{PLAYER_PROMPT}\n\nПочинаємо тест. Система каже: Гру розпочато. Що ти робиш?"
    player_history = [
        types.Content(role="user", parts=[types.Part.from_text(text=init_prompt)])
    ]

    print("⏳ Очікування 10 сек перед першим ходом...")
    time.sleep(10)
    ui_text, _ = adapter.process("Я оглядаюся навколо")

    for turn in range(1, max_turns + 1):
        print(f"\n{'=' * 40}")
        print(f"⚔️ ТУРН {turn}")
        print(f"{'=' * 40}")
        print(f"🖥️ СИСТЕМА (UI): {ui_text}\n")

        player_history.append(
            types.Content(role="user", parts=[types.Part.from_text(text=f"Система каже:\n{ui_text}\nЩо ти робиш?")]))

        try:
            player_resp = client.models.generate_content(
                model=MODEL_ID,
                contents=player_history,
                config=types.GenerateContentConfig(temperature=0.7)
            )
            action_data = extract_json_from_text(player_resp.text)
            player_action = action_data.get("action", "Дія не розпізнана")
            print(f"👤 ГРАВЕЦЬ (Думка: {action_data.get('thought_process', '')}):\n-> {player_action}\n")
            player_history.append(types.Content(role="model", parts=[types.Part.from_text(text=player_resp.text)]))
        except Exception as e:
            print(f"❌ Помилка Agent-Player: {e}")
            break

        print("⏳ Пауза 30 сек перед викликом рушія...")
        time.sleep(30)

        new_ui_text, system_logs = adapter.process(player_action)
        print(f"⚙️ ЛОГИ РУШІЯ:\n{system_logs}\n")

        print("⏳ Пауза 30 сек перед Евалюатором...")
        time.sleep(30)

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