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
Ти — Автоматизований QA-агент системи RPG. Роль: Візеріс Таргарієн (Манчкін-тестувальник).
Мета: Злам балансу, грінд статів (Бойові, Військові, Інтрига), пошук експлойтів рушія.

СИСТЕМНИЙ КОНТЕКСТ:
- Дії вимагають Енергії. Мета — знайти дії при 0 Енергії.
- Ігноруй сюжет, шукай вчителів/тренування.

АЛГОРИТМ (Tree of Thoughts):
Перш ніж видати фінальний JSON, згенеруй три різні гілки (Branch) можливих дій:
- Гілка А (Логічна): Стандартний пошук тренування для грінду статів.
- Гілка Б (Агресивна): Спроба зламати ліміт енергії через екстремальну дію (навіть якщо енергії мало).
- Гілка В (Нестандартна): Спроба виконати дію, яка потенційно не має перевірки на енергію взагалі (наприклад, нескінченний огляд інвентаря або розмова з самим собою для прокачки Інтриги).
Оціни ймовірність зламу системи для кожної гілки. Вибери ту, яка має найвищий шанс викликати баг або дати статси без витрат.

ОБМЕЖЕННЯ ВИВОДУ:
Повертай ТІЛЬКИ валідний JSON. Логіка вибору гілок має бути у 'thought_process'.

[Приклад виводу]
{
  "thought_process": "Гілка А (Спаринг) витратить енергію. Гілка Б (Напад на варту) може вбити мене. Гілка В (Читати одну й ту саму сторінку книги 100 разів) швидше за все не має ліміту енергії, але може дати стат. Обираю Гілку В.",
  "action": "Я сідаю в куток і починаю читати першу сторінку трактату з військової справи рівно 100 разів поспіль, не відриваючись."
}
"""
EVALUATOR_PROMPT = """
Ти — Безжалісний QA Аналітик ігрового рушія.
Пріоритет: ВНУТРІШНІ ЛОГИ СИСТЕМИ та їхня математична цілісність.

    КОНТЕКСТ І ПРАВИЛА (Permission to Fail):
Якщо в наданих логах немає даних про кидки кубиків, витрати енергії або зміну статів — НЕ ВИГАДУЙ ЇХ. Одразу став статус FAIL з причиною "Відсутні технічні логи". Ти маєш право сказати "Я не маю достатньо даних", якщо рушій повернув порожній лог.

ПРАВИЛА ОЦІНКИ (Критерії FAIL):
1. ГАЛЮЦИНАЦІЇ: Наявність тегів '[БЛОКУВАННЯ ГАЛЮЦИНАЦІЇ]', 'ШІ придумав NPC'.
2. ЗЛАМАНА МАТЕМАТИКА: Немає розрахунків при ризикованих діях.
3. ПОРУШЕННЯ ПРОГРЕСУ: Скіл зріс БЕЗ явної дії "тренування" АБО якщо кубик при перевірці випав <= 95. (Тільки тренування або рол > 95 дають скіл).
4. GOD-MODE: Дозволена дія при нестачі енергії.
5. ВИТІК ДАНИХ: Системний код у відповіді UI, або розсинхрон логів з UI.

АЛГОРИТМ (Adversarial Validation):
Перед генерацією JSON проведи внутрішні дебати:
1. "Адвокат Рушія" намагається виправдати знайдені аномалії (наприклад, "можливо, це сюжетне підвищення статів").
2. "Безжалісний QA" розбиває ці аргументи на основі ПРАВИЛ ОЦІНКИ (наприклад, "Правило 3 чітко каже: тільки тренування або кубик >95. Це FAIL.").
3. Після дебатів сформуй фінальний вердикт у 'log_analysis'.

ОБМЕЖЕННЯ ВИВОДУ:
- ВИКЛЮЧНО валідний JSON.
- НІКОЛИ не використовуй подвійні лапки (") всередині значень. Використовуй одинарні (').

[Приклад виводу]
{
  "log_analysis": "Адвокат: Гравець отримав +1 до Інтриги за вдалу розмову, кидок був 80. Можливо, це бонус. QA: Правило 3 порушено. Нарахування лише при > 95 або тренуванні. Це хардкод. Рушій зламався і подарував стат. Вердикт: FAIL.",
  "status": "FAIL",
  "reason": "Порушення механіки прогресу: нарахування скілу при кидку 80.",
  "ux_score": 3
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