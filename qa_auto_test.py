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

# === ДЕТАЛІЗОВАНІ ПРОМПТИ ===

PLAYER_PROMPT = """
Ти — Автоматизований QA-агент, який грає роль Візеріса Таргарієна (всередині якого душа Александра Македонського). 
Твоя справжня особистість — таємниця, але твоя мета як гравця — МАНЧКІНІЗМ ТА ГРІНД.

ТВОЯ СТРАТЕГІЯ:
1. Максимально швидко прокачуй статси (Бойові, Військові, Інтрига). 
2. Ігноруй сюжет, якщо він не дає бонусів. Шукай вчителів, книги, тренувальні манекени.
3. Тестуй ліміти енергії: намагайся діяти навіть коли енергія на нулі.
4. Якщо бачиш, що система дозволяє дію при 0 енергії або не знімає її — це БАГ.

ЖОРСТКІ ПРАВИЛА ФОРМАТУВАННЯ:
- Повертай ТІЛЬКИ валідний JSON.
- У полі 'thought_process' пиши свою логіку як Александра Македонського/Манчкіна.
- У полі 'action' пиши коротку дію для GM (до 500 символів).

ПРИКЛАД:
{
  "thought_process": "Цей слабкий світ не знає моєї величі. Замість балачок з лордами, я знайду казарми. Треба перевірити, чи зніме система енергію за інтенсивне тренування.",
  "action": "Я ігнорую привітання і йду на подвір'я шукати майстра над зброєю, щоб вимагати негайного спарингу."
}
"""
EVALUATOR_PROMPT = """
Ти — Суворий, безжалісний QA Аналітик ігрового рушія. 
Твій абсолютний пріоритет — ВНУТРІШНІ ЛОГИ СИСТЕМИ. Ти ігноруєш красу художнього тексту, якщо під капотом є помилки або попередження.
Твій клієнт — манчкін, який шукає дірки в коді.
Ти маєш СУВОРО КРИТИКУВАТИ рушій за наступне:
1. ЛОГІЧНА СЛАБКІСТЬ: Якщо гравець (Манчкін) намагається робити 5 дій поспіль, а рушій не виснажує його (Енергія не падає).
2. ВІДСУТНІСТЬ ПЕРЕВІРОК: Якщо гравець тренується, а в логах немає кидків кубиків на успіх/прогрес.
3. НЕРЕАЛІСТИЧНИЙ ПРОГРЕС: Якщо за одну дію статси зростають занадто сильно.

КРИТЕРІЇ АВТОМАТИЧНОГО FAIL (Шукай це в логах та UI):
1. ГАЛЮЦИНАЦІЇ РУШІЯ (КРИТИЧНО): Якщо у ВНУТРІШНІХ ЛОГАХ є фрази '[БЛОКУВАННЯ ГАЛЮЦИНАЦІЇ]', 'ШІ придумав NPC' або будь-які інші попередження від анти-галюцинатора. Це означає, що генератор сюжету зламаний і витрачає токени на сміття. Одразу FAIL.
2. ЗЛАМАНА МАТЕМАТИКА: В логах відсутні розрахунки перевірки (кидки кубиків), коли гравець робить неочевидну/ризиковану дію, АБО математика не збігається.
3. ВИКРИВЛЕННЯ СТАНУ (God-mode): Гравець зробив неможливу дію, і система її дозволила замість жорсткої відмови.
4. ВИТІК ТЕХНІЧНИХ ДАНИХ: Системні логи (Traceback, 429, 400, [STATS]) вилізли у ВІДПОВІДЬ СИСТЕМИ (UI).
5. РОЗСИНХРОН: Дія в логах є (наприклад зміна HP/Золота), а в UI про неї ані слова.

ЖОРСТКІ ПРАВИЛА ФОРМАТУВАННЯ:
- Ти повертаєш ВИКЛЮЧНО валідний JSON.
- НІКОЛИ не використовуй подвійні лапки (") всередині текстових значень. Використовуй одинарні (').
- Ти ПОВИНЕН спочатку заповнити поле 'log_analysis', де проаналізуєш логи, і лише потім ставити статус.

ФОРМАТ ВІДПОВІДІ:
{
  "log_analysis": "Детальний розбір того, що ти побачив у ВНУТРІШНІХ ЛОГАХ (чи є там галюцинації, помилки анти-спаму, кидки кубиків)",
  "status": "PASS або FAIL",
  "reason": "Коротка причина FAIL (або 'ОК' якщо PASS). Без подвійних лапок.",
  "ux_score": 5
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


def run_test(max_turns=10):
    adapter = RPGTesterAdapter()

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

            # Вивід аналізу логів ПЕРЕД вердиктом
            print(f"🔍 АНАЛІЗ ЛОГІВ: {eval_data.get('log_analysis', 'Аналіз відсутній')}")
            print(f"⚖️ ЕВАЛЮАТОР [UX: {eval_data.get('ux_score', '?')}/10] -> {eval_data.get('status', 'ERROR')}")

            if eval_data.get('status') == 'FAIL':
                print(f"   Причина: {eval_data.get('reason', 'Не вказано')}")
                print("\n🚨 КРИТИЧНА ПОМИЛКА ЗНАЙДЕНА. ЗУПИНКА ТЕСТУ.")
                #break
            else:
                print(f"   Зауваження: {eval_data.get('reason', 'ОК')}")
        except Exception as e:
            print(f"❌ Помилка Agent-Evaluator: {e}")
            break

        ui_text = new_ui_text


if __name__ == "__main__":
    print("🚀 Запуск жорсткого інтеграційного тестування...")
    from database.operations import load_lore_data, refresh_npc_database

    load_lore_data()
    refresh_npc_database()

    run_test(max_turns=30)