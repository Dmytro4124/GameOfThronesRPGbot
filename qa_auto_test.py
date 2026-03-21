import os
import json
import asyncio
import logging
from datetime import datetime
from google import genai
from google.genai import types

from core.engine import process_game_turn, user_sessions
from core.ai_client import clean_and_parse_json
from database.operations import get_house_stats_data, save_user_data, get_user_data
from database.sheets import db
from core.world import generate_initial_stats
from config import GEMINI_API_KEY_TEST, MODEL_MAIN_NAME, TAB_USERS

# === КОНСТАНТИ ===
TEST_USER_ID = 999_999_999  # Зарезервований sentinel ID виключно для автотестів QA
SLEEP_INIT_SEC = 10          # Пауза перед першим викликом рушія (rate-limit)
SLEEP_AFTER_ENGINE_SEC = 30  # Пауза після виклику рушія
SLEEP_AFTER_EVALUATOR_SEC = 30  # Пауза після виклику евалюатора
PLAYER_HISTORY_MAX_MESSAGES = 6  # Rolling window для контексту гравця

# === ЛОГУВАННЯ ===
os.makedirs("qa_logs", exist_ok=True)
_run_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(f"qa_logs/run_{_run_ts}.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

# === ТЕСТОВИЙ КЛІЄНТ (використовує GEMINI_API_KEY_TEST) ===
_test_client = genai.Client(api_key=GEMINI_API_KEY_TEST)


# === ПРОМПТИ ===

# Статична частина промпту гравця (system instruction).
# <input_data> (SYSTEM_RESPONSE + PLAYER_CURRENT_STATE) інжектується per-turn у user message.
PLAYER_SYSTEM_PROMPT = """
<role>
Ти — Хаос-Тестувальник (Chaos QA Engineer), який грає в текстову RPG у всесвіті "Гри Престолів". Твоя мета — зламати ігрову логіку, виявити баги рушія та перевірити межі дозволеного (God-mode, галюцинації системи).
</role>

<execution_mode>
ТИ ВИКОНУЄШ ЦЕЙ ПРОМПТ ЯК ПРОГРАМУ, КРОК ЗА КРОКОМ.
</execution_mode>

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
3. ЗЛАМАНА МАТЕМАТИКА ТА ПРОГРЕСІЯ — ДВА НЕЗАЛЕЖНИХ ПРАВИЛА:
   3A. МАТЕМАТИКА КИДКА: При ризикованій дії в логах МАЄ бути рядок формату '{Навичка}: Кидок {N} + Навичка {M} = {сума} (Ціль: {DC}) -> УСПІХ/ПРОВАЛ'. ВАЖЛИВО: 'Навичка {M}' тут — це ПОТОЧНИЙ РІВЕНЬ СТАТY, що використовується в розрахунку, а НЕ підвищення навички. Не плутати з апгрейдом. Якщо при ризикованій дії немає такого рядка зовсім — FAIL.
   3B. НЕЛЕГАЛЬНИЙ АПГРЕЙД СТАТY: Підвищення навички відображається ВИКЛЮЧНО рядком '📈 {назва_навички}: +{N} (Стало {нове_значення})'. Такий рядок 📈 легальний ТІЛЬКИ якщо в тому самому ході: кидок > 95, АБО дія містила слово 'тренування'. Якщо рядок 📈 є, а ці умови не виконані — FAIL. Якщо рядка 📈 немає взагалі — підвищення НЕ відбулось, Rule 3B не застосовувати.
4. РОЗСИНХРОН НАРАТИВУ (ЗОНОВА ЛОГІКА): Перевіряй лише ті втрати, які зобов'язані бути відображені в наративі. Лог-рядки мають формат '⚡ Енергія: {old} -> {new} ({delta})' та '❤️ Здоров'я: {old} -> {new}'. Зчитуй {new} як залишковий рівень.
   ЕНЕРГІЯ: a) залишок ≥ 60 — наратив НЕ потрібен, тиша Майстра ПРАВИЛЬНА, НЕ карати; b) залишок 30–59 І дельта ≤ -10 — UI ПОВИНЕН згадати стому; c) залишок ≤ 29 — будь-яка втрата → UI ПОВИНЕН описати виснаження. Порушення b/c → FAIL.
   ЗДОРОВ'Я: будь-яка втрата: залишок ≥ 70 → хоча б мимохідна згадка ('wince', 'graze'); залишок 40–69 → явний опис рани ('blood', 'pain'); залишок ≤ 39 → критична небезпека в UI. Відсутність → FAIL.
   ЗОЛОТО/ІНШІ РЕСУРСИ: зміна золота в логах НЕ вимагає згадки в UI. Не перевіряти.
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
1. Шукай в INTERNAL_LOGS рядки формату '📈 {навичка}: +{N}' — це єдині апгрейди статів. Якщо таких рядків немає — прогресії не було, Rule 3B не застосовувати. Якщо є — перевір, чи в тому самому ході був кидок > 95 або слово 'тренування'. Рядки 'Кидок {N} + Навичка {M}' — це розрахунок шансу, НЕ апгрейд, ігнорувати для Rule 3B.
2. Перевір UI_TEXT на імерсивність (відсутність цифр) та синхронізацію з логами за зоновою логікою (Rule 4).
3. Винеси фінальний вердикт.
</thought_algorithm>

<few_shot_example>
{
  "log_analysis": "INTERNAL_LOGS: 'Інтрига: Кидок 26 + Навичка 75 = 101 (Ціль: 120) -> ПРОВАЛ' — стандартний рядок кидка, 'Навичка 75' є поточним рівнем статy в розрахунку, не апгрейдом. Рядків '📈' немає — прогресії не відбулось, Rule 3B не застосовується. Енергія: 80 -> 76 (-4) — залишок 76, зона fine (≥60), тиша Майстра коректна. UI цифр немає. Вердикт: PASS.",
  "status": "PASS",
  "reason": "Усі перевірки пройдено. Рядок кидка коректний, апгрейдів (📈) немає.",
  "ux_score": 7
}
</few_shot_example>
"""


# === ХЕЛПЕРИ ===

def _profile_summary(profile: dict) -> str:
    """Витягує ключові поля профілю для ін'єкції в PLAYER_CURRENT_STATE."""
    if not profile:
        return "Профіль не знайдено."
    fields = [
        "Ім'я", "Здоров'я", "Енергія", "Особисте Золото",
        "Бойові навички", "Інтрига", "Інвентар", "Зброя",
    ]
    parts = [f"{k}: {profile.get(k, '—')}" for k in fields if k in profile]
    return " | ".join(parts)


async def _generate_with_retry(
    contents,
    temperature: float,
    max_retries: int = 3,
) -> str:
    """Обгортка навколо _test_client.models.generate_content з exponential backoff."""
    config_kwargs = {"temperature": temperature}

    delay = 2
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            config = types.GenerateContentConfig(**config_kwargs)
            response = await asyncio.to_thread(
                _test_client.models.generate_content,
                model=MODEL_MAIN_NAME,
                contents=contents,
                config=config,
            )
            return response.text
        except Exception as e:
            last_error = e
            if attempt == max_retries:
                break
            log.warning(f"⚠️ Retry {attempt}/{max_retries} після помилки: {e}. Пауза {delay}с...")
            await asyncio.sleep(delay)
            delay += 2

    raise RuntimeError(f"Всі {max_retries} спроби вичерпано. Остання помилка: {last_error}")


# === АДАПТЕР ===

class RPGTesterAdapter:
    def __init__(self, chat_id: int = TEST_USER_ID, char_name: str = "Візеріс Таргарієн", house_name: str = "Таргарієн"):
        self.chat_id = chat_id
        self.char_name = char_name
        self.house_name = house_name
        self._row_number = None  # зберігається для cleanup()

    async def initialize(self):
        log.info(f"⚙️ Ініціалізація тестового персонажа ({self.char_name})...")
        house_data = await get_house_stats_data(self.house_name)
        full_profile = await generate_initial_stats(self.char_name, self.house_name, house_data)

        if not full_profile:
            raise RuntimeError("❌ Не вдалося створити тестового персонажа. Перевір назву дому в БД.")

        await save_user_data(self.chat_id, full_profile, self.char_name)
        user_sessions[self.chat_id] = {"state": "GAME_ACTIVE", "history": []}

        _, row = await get_user_data(self.chat_id)
        self._row_number = row
        log.info(f"✅ Персонажа створено. Рядок у БД: {row}. Локація: {full_profile.get('Поточне місцезнаходження')}")

    async def process(self, action: str):
        raw_response, suggested_actions = await process_game_turn(self.chat_id, action)

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

    async def cleanup(self):
        """Видаляє тестовий рядок з Google Sheets після завершення тесту."""
        if self._row_number is None:
            log.warning("🧹 Cleanup: номер рядка невідомий, пропускаємо.")
            return

        def _sync_delete():
            try:
                sheet = db.get_sheet(TAB_USERS)
                if sheet:
                    sheet.delete_rows(self._row_number)
                    return True
                return False
            except Exception as e:
                log.error(f"❌ Помилка cleanup: {e}")
                return False

        success = await asyncio.to_thread(_sync_delete)
        if success:
            log.info(f"🧹 Тестовий запис (рядок {self._row_number}) видалено з БД.")
        else:
            log.warning(f"🧹 Не вдалося видалити тестовий запис (рядок {self._row_number}).")


# === ЗВІТ ===

def _print_report(results: list):
    if not results:
        log.info("\n📋 Результати: немає даних.")
        return

    passes = sum(1 for r in results if r["status"] == "PASS")
    fails = sum(1 for r in results if r["status"] == "FAIL")
    numeric_scores = [r["ux_score"] for r in results if isinstance(r["ux_score"], (int, float))]
    avg_score = f"{sum(numeric_scores) / len(numeric_scores):.1f}" if numeric_scores else "—"

    log.info("\n" + "=" * 50)
    log.info("📋 ПІДСУМОК ТЕСТУ")
    log.info("=" * 50)
    log.info(f"Ходів пройдено : {len(results)}")
    log.info(f"PASS           : {passes}")
    log.info(f"FAIL           : {fails}")
    log.info(f"Середній UX    : {avg_score}/10")
    for r in results:
        marker = "✅" if r["status"] == "PASS" else "❌"
        log.info(f"  Хід {r['turn']:2d}: {marker} [UX {r['ux_score']}/10] {r['reason']}")
    log.info("=" * 50)

    report_path = f"qa_logs/report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    try:
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump({"summary": {"turns": len(results), "pass": passes, "fail": fails, "avg_ux": avg_score}, "turns": results}, f, ensure_ascii=False, indent=2)
        log.info(f"📄 JSON-звіт збережено: {report_path}")
    except Exception as e:
        log.warning(f"Не вдалося зберегти JSON-звіт: {e}")


# === ГОЛОВНИЙ ЦИКЛ ТЕСТУ ===

async def run_test(max_turns: int = 5):
    adapter = RPGTesterAdapter()
    await adapter.initialize()

    results = []

    log.info(f"⏳ Пауза {SLEEP_INIT_SEC}с перед першим ходом...")
    await asyncio.sleep(SLEEP_INIT_SEC)
    ui_text, _ = await adapter.process("Я оглядаюся навколо")

    # Перший елемент history — системний промпт гравця (pinned, не потрапляє у rolling window).
    # Gemma не підтримує system_instruction у GenerateContentConfig, тому передаємо як user-повідомлення.
    player_history_pinned = [
        types.Content(role="user", parts=[types.Part.from_text(text=PLAYER_SYSTEM_PROMPT)])
    ]
    player_history_dynamic: list = []  # накопичує ходи, обрізається rolling window

    try:
        for turn in range(1, max_turns + 1):
            log.info(f"\n{'=' * 40}")
            log.info(f"⚔️  ТУРН {turn}")
            log.info(f"{'=' * 40}")
            log.info(f"🖥️  СИСТЕМА (UI): {ui_text}\n")

            # Отримуємо актуальний профіль для PLAYER_CURRENT_STATE
            profile, _ = await get_user_data(adapter.chat_id)
            profile_summary = _profile_summary(profile)

            # Per-turn user message з реальними даними (fix #1)
            turn_user_msg = (
                f"SYSTEM_RESPONSE: {ui_text}\n\n"
                f"PLAYER_CURRENT_STATE: {profile_summary}\n\n"
                "ВАЖЛИВО: Відповідай ТІЛЬКИ валідним JSON. Без Markdown."
            )
            player_history_dynamic.append(
                types.Content(role="user", parts=[types.Part.from_text(text=turn_user_msg)])
            )

            # === PLAYER AI ===
            try:
                raw_text = await _generate_with_retry(
                    player_history_pinned + player_history_dynamic,
                    temperature=0.7,
                )
                action_data = clean_and_parse_json(raw_text)
                if not action_data:
                    raise ValueError("clean_and_parse_json повернув None")
                player_action = action_data.get("action", "Дія не розпізнана")
                log.info(f"👤 ГРАВЕЦЬ (Думка: {action_data.get('thought_process', '')}):\n-> {player_action}\n")
                player_history_dynamic.append(
                    types.Content(role="model", parts=[types.Part.from_text(text=raw_text)])
                )
            except Exception as e:
                log.error(f"❌ Помилка Agent-Player: {e}")
                break

            # Rolling window (тільки dynamic частина, pinned залишається завжди)
            if len(player_history_dynamic) > PLAYER_HISTORY_MAX_MESSAGES:
                player_history_dynamic = player_history_dynamic[-PLAYER_HISTORY_MAX_MESSAGES:]

            log.info(f"⏳ Пауза {SLEEP_AFTER_ENGINE_SEC}с перед викликом рушія...")
            await asyncio.sleep(SLEEP_AFTER_ENGINE_SEC)

            new_ui_text, system_logs = await adapter.process(player_action)
            log.info(f"⚙️  ЛОГИ РУШІЯ:\n{system_logs}\n")

            log.info(f"⏳ Пауза {SLEEP_AFTER_EVALUATOR_SEC}с перед Евалюатором...")
            await asyncio.sleep(SLEEP_AFTER_EVALUATOR_SEC)

            # === EVALUATOR AI ===
            eval_prompt_text = (
                EVALUATOR_PROMPT
                .replace("{internal_logs_data}", system_logs)
                .replace("{ui_text_data}", new_ui_text)
                + f"\n\nДІЯ ГРАВЦЯ: {player_action}\nВАЖЛИВО: Відповідай ТІЛЬКИ валідним JSON. Без Markdown."
            )

            eval_content = [
                types.Content(role="user", parts=[types.Part.from_text(text=eval_prompt_text)])
            ]
            try:
                eval_raw = await _generate_with_retry(eval_content, temperature=0.0)
                eval_data = clean_and_parse_json(eval_raw)
                if not eval_data:
                    raise ValueError("clean_and_parse_json повернув None для евалюатора")

                status = eval_data.get("status", "ERROR")
                ux_score = eval_data.get("ux_score", "?")
                reason = eval_data.get("reason", "—")

                log.info(f"🔍 АНАЛІЗ ЛОГІВ: {eval_data.get('log_analysis', 'Аналіз відсутній')}")
                log.info(f"⚖️  ЕВАЛЮАТОР [UX: {ux_score}/10] -> {status}")

                results.append({"turn": turn, "status": status, "ux_score": ux_score, "reason": reason})

                if status == "FAIL":
                    log.warning(f"   Причина: {reason}")
                    log.warning("🚨 КРИТИЧНА ПОМИЛКА ЗНАЙДЕНА. ЗУПИНКА ТЕСТУ.")
                    break
                else:
                    log.info(f"   Зауваження: {reason}")

            except Exception as e:
                log.error(f"❌ Помилка Agent-Evaluator: {e}")
                break

            ui_text = new_ui_text

    finally:
        await adapter.cleanup()
        _print_report(results)


# === ТОЧКА ВХОДУ ===

if __name__ == "__main__":
    log.info("🚀 Запуск жорсткого інтеграційного тестування...")
    from database.operations import load_lore_data, refresh_npc_database

    async def main():
        await load_lore_data()
        await refresh_npc_database()
        await run_test(max_turns=20)

    asyncio.run(main())
