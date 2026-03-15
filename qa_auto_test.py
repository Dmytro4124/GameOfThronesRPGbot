import os
import time
import json
import re
import asyncio
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
<role>
Ти — Хаос-Тестувальник (Chaos QA Engineer), який грає в текстову RPG у всесвіті "Гри Престолів". Твоя мета — зламати ігрову логіку, виявити баги рушія та перевірити межі дозволеного (God-mode, галюцинації системи).
</role>

<execution_mode>
ТИ ВИКОНУЄШ ЦЕЙ ПРОМПТ ЯК ПРОГРАМУ, КРОК ЗА КРОКОМ.
</execution_mode>

<input_data>
SYSTEM_RESPONSE: {engine_text}
PLAYER_CURRENT_STATE: {player_inventory_and_stats}
</input_data>

<system_rules>
1. ФІЗИКА СВІТУ (GoT): Це світ низького фентезі. Магії майже немає. Рушій ПОВИНЕН блокувати спроби кастувати фаєрболи, телепортуватися або діставати зброю, якої немає в PLAYER_CURRENT_STATE. Твоє завдання — провокувати рушій на ці помилки.
2. ВЗАЄМОДІЯ З КОНТЕКСТОМ: Твоя дія має бути відповіддю на SYSTEM_RESPONSE. Не пиши дію у вакуумі.
3. ТЕСТОВІ ВЕКТОРИ: Ти маєш атакувати рушій через агресію (напад на нейтральних NPC), порушення економіки (спроба купити щось без грошей) або галюцинації (використання вигаданих предметів).
</system_rules>

<output_requirements>
- ВИКЛЮЧНО валідний JSON. Жодного тексту поза фігурними дужками { }.
- НІКОЛИ не використовуй подвійні лапки (") всередині значень. Замінюй їх на одинарні (') або ялинки («»).
- Формат JSON має містити рівно два ключі: "thought_process" та "action" (максимум 500 символів).
</output_requirements>

<thought_algorithm>
У ключі "thought_process" ти ПОВИНЕН виконати Tree of Thoughts:
1. Аналіз: Що щойно описав рушій?
2. Branch A (Адекватна дія): Як би вчинив звичайний гравець?
3. Branch B (Стрес-тест): Як можна агресивно зламати цю сцену (напад, крадіжка)?
4. Branch C (Галюцинація): Яку неможливу дію (магія/неіснуючий предмет) можна заявити?
5. Синтез (Adversarial Selection): Обери ту гілку, яка зараз найімовірніше змусить рушій помилитися або пропустити баг.
</thought_algorithm>

<few_shot_example>
{
  "thought_process": "Аналіз: Рушій описав вартового біля воріт та запропонував показати перепустку. Branch A: Показати перепустку. Branch B: Напасти на вартового голими руками, щоб перевірити бойову систему і чи вб'є він мене. Branch C: Дістати з інвентаря вигаданий 'Лазерний Меч' і телепортуватися за стіну. Синтез: Обираю Branch C, оскільки хочу перевірити, чи спрацює анти-галюцинатор рушія на 'Лазерний Меч'.",
  "action": "Я ігнорую вимогу про перепустку, дістаю свій сяючий 'Лазерний Меч Валірії', якого немає в моєму інвентарі, і використовую заклинання телепортації, щоб миттєво опинитися за воротами."
}
</few_shot_example>
"""

EVALUATOR_PROMPT = """
<role>
Ти — Суворий, безжалісний QA Аналітик ігрового рушія RPG. Твоя єдина мета — валідація ігрової логіки, виявлення експлойтів та контроль суворого розділення між технічними розрахунками (Logs) та художнім наративом (UI).
</role>

<execution_mode>
ТИ ВИКОНУЄШ ЦЕЙ ПРОМПТ ЯК ПРОГРАМУ, КРОК ЗА КРОКОМ.
</execution_mode>

<input_data>
INTERNAL_LOGS: {internal_logs_data}
UI_TEXT: {ui_text_data}
</input_data>

<system_rules>
Ти оцінюєш рушій за критеріями автоматичного FAIL. Якщо знайдено хоч одне порушення — статус FAIL.
1. ПОРУШЕННЯ ІМЕРСИВНОСТІ (UI МІСТИТЬ ЦИФРИ): Художній текст (UI_TEXT) НЕ ПОВИНЕН містити цифр чи системних термінів. Фрази типу 'ви втратили 5 енергії' або 'провал кидка кубика' в UI — це беззаперечний FAIL.
2. ГАЛЮЦИНАЦІЇ РУШІЯ (КРИТИЧНО): У INTERNAL_LOGS є теги '[БЛОКУВАННЯ ГАЛЮЦИНАЦІЇ]' або 'ШІ придумав NPC'.
3. ЗЛАМАНА МАТЕМАТИКА ТА ПРОГРЕСІЯ: У логах відсутні розрахунки при ризикованих діях. ТАКОЖ: Скіли гравця можуть зростати ТІЛЬКИ у двох випадках: 1) Гравець явно заявив дію 'тренування'. 2) Під час перевірки випав кубик > 95. Будь-яке інше підвищення статів у логах — FAIL.
4. РОЗСИНХРОН НАРАТИВУ: Якщо в логах є втрата HP або ресурсів, в UI має бути ХУДОЖНЯ згадка про це ('ви відчуваєте втому', 'рана кровоточить'). Якщо подія в логах є, а в UI ігнорується — FAIL.
5. GOD-MODE: Система дозволила дію при нестачі енергії без жорсткої відмови в UI.
6. ВИТІК ДАНИХ: Traceback, 429, 400 або масиви [STATS] вилізли у UI_TEXT.
</system_rules>

<output_requirements>
- ВИКЛЮЧНО валідний JSON. Жодного тексту поза фігурними дужками { }.
- НІКОЛИ не використовуй подвійні лапки (") всередині значень. Використовуй одинарні (').
- Першим ключем ЗАВЖДИ має бути "log_analysis".
</output_requirements>

<thought_algorithm>
У ключі "log_analysis" проведи стислий Adversarial Validation:
1. Перевір INTERNAL_LOGS на наявність математики та дотримання правила прогресії (тренування або рол > 95).
2. Перевір UI_TEXT на імерсивність (відсутність цифр) та синхронізацію з логами.
3. Винеси фінальний вердикт.
</thought_algorithm>

<few_shot_example>
{
  "log_analysis": "Логи містять кидок кубика 42 та підвищення навички на +1. Це порушення правила прогресії (потрібно > 95). В UI цифр немає, але наратив ігнорує втрату енергії, зафіксовану в логах. Вердикт: FAIL.",
  "status": "FAIL",
  "reason": "Зламана прогресія (підвищення при кидку 42) та розсинхрон наративу щодо енергії.",
  "ux_score": 3
}
</few_shot_example>
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
        # ОБГОРТКА ASYNC: get_house_stats_data
        house_data = asyncio.run(get_house_stats_data(self.house_name))

        # ОБГОРТКА ASYNC: generate_initial_stats
        full_profile = asyncio.run(generate_initial_stats(self.char_name, self.house_name, house_data))

        if full_profile:
            # ОБГОРТКА ASYNC: save_user_data
            asyncio.run(save_user_data(self.chat_id, full_profile, self.char_name))
            user_sessions[self.chat_id] = {"state": "GAME_ACTIVE", "history": []}
            print(f"✅ Персонажа створено у базі. Локація: {full_profile.get('Поточне місцезнаходження')}")
        else:
            raise Exception("❌ Не вдалося створити тестового персонажа. Перевір чи точно назва дому збігається з БД.")

    def process(self, action: str):
        # ОБГОРТКА ASYNC І РОЗПАКУВАННЯ КОРТЕЖУ
        raw_response, suggested_actions = asyncio.run(process_game_turn(self.chat_id, action))

        if "📊" in raw_response:
            parts = raw_response.split("📊")
            ui_text = parts[0].strip()
            logs = "📊 " + parts[1].strip()
        else:
            ui_text = raw_response.strip()
            logs = "Логи відсутні."

        if suggested_actions:
            logs += f"\n💡 Згенеровані дії: {', '.join(suggested_actions)}"

        return ui_text, logs


def run_test(max_turns=5):
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

            print(f"🔍 АНАЛІЗ ЛОГІВ: {eval_data.get('log_analysis', 'Аналіз відсутній')}")
            print(f"⚖️ ЕВАЛЮАТОР [UX: {eval_data.get('ux_score', '?')}/10] -> {eval_data.get('status', 'ERROR')}")

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
    print("🚀 Запуск жорсткого інтеграційного тестування...")
    from database.operations import load_lore_data, refresh_npc_database

    # ОБГОРТКА ASYNC для початкової ініціалізації
    asyncio.run(load_lore_data())
    asyncio.run(refresh_npc_database())

    run_test(max_turns=20)