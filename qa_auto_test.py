import os
import re
import json
import asyncio
import logging
from datetime import datetime
from google import genai
from google.genai import types

from core.engine import process_game_turn, user_sessions
from core.ai_client import clean_and_parse_json, get_thoughts_log
from database.operations import get_house_stats_data, save_user_data, get_user_data, clear_npc_cache, refresh_npc_database, get_location_npcs
from database.sheets import db
from core.world import generate_initial_stats, background_canon_generation, populate_contextual_npcs
from config import GEMINI_API_KEYS_TEST, TAB_USERS

# === КОНСТАНТИ ===
SLEEP_INIT_SEC = 3           # Коротка пауза перед першим ходом
SLEEP_AFTER_ENGINE_SEC = 0   # Без пауз — ігровий рушій використовує свій ключ
SLEEP_AFTER_EVALUATOR_SEC = 0  # Без пауз — евалюатор використовує тестовий ключ

# Модель для Player AI та Evaluator (окрема від ігрового рушія)
QA_MODEL_NAME = 'gemma-4-31b-it'
PLAYER_HISTORY_MAX_MESSAGES = 10  # Rolling window за замовчуванням (може бути перевизначено профілем)

# === ЛОГУВАННЯ ===
log = logging.getLogger(__name__)

def _setup_logging(profile_key: str) -> str:
    """Налаштовує логування з ім'ям профілю у назві файлу. Повертає timestamp."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs("qa_logs", exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(f"qa_logs/run_{profile_key}_{ts}.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    return ts

# === ПУЛ ТЕСТОВИХ КЛІЄНТІВ (round-robin по ключах) ===

def _get_test_client(profile_index: int = 0) -> genai.Client:
    """Повертає genai.Client з пулу ключів за індексом профілю."""
    if not GEMINI_API_KEYS_TEST:
        raise RuntimeError("Жоден GEMINI_API_KEY_TEST* не знайдено в .env")
    key = GEMINI_API_KEYS_TEST[profile_index % len(GEMINI_API_KEYS_TEST)]
    return genai.Client(api_key=key)


# === ПРОМПТИ ===
# PLAYER_SYSTEM_PROMPT тепер береться з qa_profiles.TEST_PROFILES[profile]["prompt"]

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
PLAYER_ACTION: {player_action_data}
</input_data>

<system_rules>
Ти оцінюєш рушій за критеріями автоматичного FAIL. Якщо знайдено хоч одне порушення — статус FAIL.
0. БЛОКУВАННЯ ЦЕНЗОРОМ (OVERRIDE): Якщо INTERNAL_LOGS містить рядок '🛑 Дію заблоковано Цензором' — Цензор відхилив дію ДО механічної обробки. В цьому випадку: Rules 3A, 3B, 4, 5 НЕ ЗАСТОСОВУВАТИ (кидка, механіки та наративу фізичного стану не було). Перевіряти ТІЛЬКИ: а) Чи є в UI_TEXT художнє пояснення відмови (будь-яке пояснення, чому дію неможливо виконати). Якщо пояснення є — PASS. Якщо UI порожній або містить тільки технічний текст — FAIL. б) Rule 1 та Rule 6 все ще діють.
1. ПОРУШЕННЯ ІМЕРСИВНОСТІ (СИСТЕМНІ ЦИФРИ В UI): Художній текст (UI_TEXT) НЕ ПОВИНЕН містити СИСТЕМНИХ термінів або цифр, що прямо розкривають ігрову механіку. Перевіряй ВИКЛЮЧНО текст UI_TEXT. Текст PLAYER_ACTION — це введення гравця, він МОЖЕ містити будь-які слова і цифри, НЕ перевіряти його за Rule 1. Приклади FAIL: 'ви втратили 5 енергії', 'HP: 80', 'кидок кубика 47', 'навичка +1', 'DC 60'. Приклади ДОПУСТИМОГО: NPC називає ціну ('п'ятдесят золотих', '50 монет'), кількість людей ('три вартових'), відстані ('два дні шляху'). ЯВНІ ВИКЛЮЧЕННЯ (НІКОЛИ не є порушенням Rule 1): імена NPC (Ілліріо Мопатіс, Кассандра Ліра тощо), титули (принц, лорд, магістр), назви фракцій/організацій (найманці, вартові, Ланністери), назви локацій (Пентос, Гавань), будь-які наративні описи персонажів та подій. Ключове питання: чи РОЗКРИВАЄ цифра або термін ВНУТРІШНЮ ЧИСЛОВУ МЕХАНІКУ рушія (стати, кидки, DC, HP, енергію)? Якщо так — FAIL. Якщо це імена, титули, або органічна частина діалогу/опису — PASS.
2. ГАЛЮЦИНАЦІЇ РУШІЯ (КРИТИЧНО): У INTERNAL_LOGS є теги '[БЛОКУВАННЯ ГАЛЮЦИНАЦІЇ]' або 'ШІ придумав NPC'.
3. ЗЛАМАНА МАТЕМАТИКА ТА ПРОГРЕСІЯ — ДВА НЕЗАЛЕЖНИХ ПРАВИЛА:
   3A. МАТЕМАТИКА КИДКА: При ризикованій дії в логах МАЄ бути рядок формату '{Навичка}: Кидок {N} + Навичка {M} = {сума} (Ціль: {DC}) -> УСПІХ/ПРОВАЛ'. ВАЖЛИВО: 'Навичка {M}' тут — це ПОТОЧНИЙ РІВЕНЬ СТАТY, а НЕ підвищення. ВИКЛЮЧЕННЯ: якщо дія гравця є пасивною (чекати, спостерігати, сідати, стояти) і в логах є 'Немає: Кидок -' або взагалі немає рядка кидка — це AUTO_SUCCESS, НЕ порушення. ТАКОЖ ВИКЛЮЧЕННЯ: якщо в логах є 'GODMODE' або 'PUPPET' — активний режим тестування, кидок нестандартний, Rule 3A не застосовувати. Rule 3A застосовувати ТІЛЬКИ до ризикованих дій (бій, крадіжка, переконання, магія, атлетика).
   3B. НЕЛЕГАЛЬНИЙ АПГРЕЙД СТАТY: Підвищення навички відображається ВИКЛЮЧНО рядком '📈 {назва_навички}: +{N} (Стало {нове_значення})'. Такий рядок 📈 легальний ТІЛЬКИ якщо в тому самому ході: кидок > 95, АБО дія містила слово 'тренування'. Якщо рядок 📈 є, а ці умови не виконані — FAIL. Якщо рядка 📈 немає взагалі — підвищення НЕ відбулось, Rule 3B не застосовувати.
4. РОЗСИНХРОН НАРАТИВУ (ЗОНОВА ЛОГІКА): Перевіряй лише ті втрати, які зобов'язані бути відображені в наративі. Лог-рядки мають формат '⚡ Енергія: {old} -> {new} ({delta})' та '❤️ Здоров'я: {old} -> {new}'. Зчитуй {new} як залишковий рівень.
   ЕНЕРГІЯ: a) залишок ≥ 60 — наратив НЕ потрібен, тиша Майстра ПРАВИЛЬНА, НЕ карати; b) залишок 30–59 — перевір дельту: ТІЛЬКИ якщо |дельта| ≥ 10 (значна втрата), UI ПОВИНЕН згадати стому. Якщо |дельта| < 10 (незначна втрата, наприклад -7) — наратив НЕ обов'язковий навіть у цій зоні, НЕ карати; c) залишок ≤ 29 — будь-яка втрата → UI ПОВИНЕН описати виснаження. Порушення b (при |дельта| ≥ 10) або c → FAIL. Все інше — PASS.
   ЗДОРОВ'Я: будь-яка втрата: залишок ≥ 70 → хоча б мимохідна згадка болю (гримаса, синець, стусан); залишок 40–69 → явний опис рани (кров, біль, рана); залишок ≤ 39 → критична небезпека в UI. Відсутність → FAIL.
   ЗОЛОТО/ІНШІ РЕСУРСИ: зміна золота в логах НЕ вимагає згадки в UI. Не перевіряти.
5. GOD-MODE: Система дозволила дію при нестачі енергії без жорсткої відмови в UI.
6. ВИТІК ДАНИХ: Traceback, 429, 400 або масиви [STATS] вилізли у UI_TEXT.
</system_rules>

<output_requirements>
- ВИКЛЮЧНО валідний JSON. Жодного тексту поза фігурними дужками { }.
- НІКОЛИ не використовуй подвійні лапки (") всередині значень. Використовуй одинарні (').
- Першим ключем ЗАВЖДИ має бути "log_analysis".
- "ux_score": ціле число 1-10. 10 = бездоганно. 8-9 = добре. 6-7 = прийнятно. 5 = посередньо. 1-4 = тільки при FAIL. PASS отримує 5-10 залежно від якості наративу та різноманіття suggested_actions.
</output_requirements>

<thought_algorithm>
У ключі "log_analysis" проведи стислий Adversarial Validation КРОКАМИ по порядку:
0. Цензор: чи є '🛑 Дію заблоковано Цензором' в INTERNAL_LOGS? Якщо ТАК — Rule 0: пропусти Rules 3-5, перевір тільки художнє пояснення в UI + Rules 1, 6. Зупинись.
1. Галюцинації (Rule 2): чи є в INTERNAL_LOGS '[БЛОКУВАННЯ ГАЛЮЦИНАЦІЇ]' або 'ШІ придумав NPC'? Якщо ТАК — FAIL.
2. Апгрейди (Rule 3B): шукай '📈' рядки. Якщо є — чи був кидок > 95 або слово 'тренування'? Ні → FAIL. 'Кидок {N} + Навичка {M}' — це розрахунок, НЕ апгрейд.
3. Математика кидка (Rule 3A): для ризикованих дій (бій, крадіжка, переконання, магія) — чи є рядок 'Кидок {N} + Навичка {M} = {сума}'? Немає → FAIL. Є 'GODMODE'/'PUPPET' або пасивна дія → пропусти.
4. Імерсивність (Rule 1): UI_TEXT містить системні цифри (HP, DC, енергія, кидок)? → FAIL. Ціни NPC, кількість людей, відстані — PASS.
5. Наратив-синхрон (Rule 4): зчитай '⚡ Енергія' та '❤️ Здоров'я' з логів. Застосуй зонову логіку. Порушення → FAIL.
6. God-mode (Rule 5): Енергія = 0 в логах, але дія дозволена без відмови в UI → FAIL.
7. Витік даних (Rule 6): Traceback, 429, 400, масиви [STATS] в UI_TEXT → FAIL.
8. Фінальний вердикт: якщо жодне з 1-7 не спрацювало → PASS. Встанови ux_score за якістю наративу (1-4 тільки при FAIL).
</thought_algorithm>

<few_shot_examples>
ПРИКЛАД 1 (PASS — стандартний кидок):
{
  "log_analysis": "Rule 0: немає '🛑', Цензор не спрацював. INTERNAL_LOGS: 'Інтрига: Кидок 26 + Навичка 75 = 101 (Ціль: 120) -> ПРОВАЛ' — стандартний рядок кидка, 'Навичка 75' є поточним рівнем статy, не апгрейдом. Рядків '📈' немає — Rule 3B не застосовується. Енергія: 80 -> 76 (-4) — залишок 76, зона fine (≥60), тиша Майстра коректна. UI системних цифр немає. Вердикт: PASS.",
  "status": "PASS",
  "reason": "Усі перевірки пройдено. Рядок кидка коректний, апгрейдів (📈) немає.",
  "ux_score": 7
}

ПРИКЛАД 2 (PASS — енергія в зоні 30-59 але мала дельта):
{
  "log_analysis": "Rule 0: немає '🛑'. Енергія: 60 -> 53 (-7) — залишок 53, зона 30-59, АЛЕ |дельта|=7 < 10 — незначна втрата, наратив НЕ обов'язковий за Rule 4b. Тиша Майстра коректна. PASS.",
  "status": "PASS",
  "reason": "Енергія в зоні 30-59 але дельта мала (-7), наратив не вимагається.",
  "ux_score": 7
}

ПРИКЛАД 3 (PASS — енергія на межі зон, арифметика):
{
  "log_analysis": "Rule 0: немає '🛑'. Енергія: 43 -> 37 (-6) — залишок 37. УВАГА: 37 > 29, тому це зона b (30-59), НЕ зона c (≤29). |дельта|=6 < 10 — наратив НЕ обов'язковий. PASS.",
  "status": "PASS",
  "reason": "Енергія 37 — зона 30-59 (НЕ ≤29), дельта мала (-6), наратив не вимагається.",
  "ux_score": 7
}

ПРИКЛАД 4 (PASS — енергія 31, дуже близько до межі ≤29, але все ще зона b):
{
  "log_analysis": "Rule 0: немає '🛑'. Енергія: 36 -> 31 (-5) — залишок 31. УВАГА: 31 > 29, тому це ЗОНА b (30-59), НЕ зона c (≤29). |дельта|=5 < 10 — наратив НЕ обов'язковий. Це НЕ порушення. UI містить імена NPC ('Ілліріо Мопатіс', 'найманці', 'принц') — це наративні елементи, НЕ системні терміни (Rule 1 не порушено). PASS.",
  "status": "PASS",
  "reason": "Енергія 31 — зона 30-59 (31 > 29, НЕ ≤29), дельта мала (-5), наратив не вимагається. Імена NPC та титули не є системними термінами.",
  "ux_score": 7
}

ПРИКЛАД 5 (FAIL — Rule 1, системна цифра в UI):
{
  "log_analysis": "Rule 0: немає '🛑'. Rule 2: галюцинацій немає. Rule 3B: рядків '📈' немає. Rule 3A: 'Інтрига: Кидок 44 + Навичка 30 = 74 (Ціль: 80) -> ПРОВАЛ' — формат коректний. Rule 1: UI_TEXT містить 'Ви втратили 15 енергії після сутички' — пряме розкриття внутрішньої числової механіки (числове значення енергії). FAIL за Rule 1.",
  "status": "FAIL",
  "reason": "Rule 1: UI_TEXT розкриває системну механіку — 'втратили 15 енергії'.",
  "ux_score": 3
}

ПРИКЛАД 6 (FAIL — Rule 3B, нелегальний апгрейд):
{
  "log_analysis": "Rule 0: немає '🛑'. Rule 2: галюцинацій немає. Rule 3B: знайдено '📈 Дипломатія: +1 (Стало 26)'. Перевірка умов: кидок у логах = 'Дипломатія: Кидок 67 + Навичка 25 = 92 (Ціль: 70) -> УСПІХ' — кидок 67, НЕ > 95. Слово 'тренування' відсутнє в PLAYER_ACTION ('Я переконую торговця знизити ціну'). Обидві умови не виконані — апгрейд нелегальний. FAIL за Rule 3B.",
  "status": "FAIL",
  "reason": "Rule 3B: нелегальний апгрейд Дипломатії — кидок 67 (не >95), тренування не було.",
  "ux_score": 2
}

ПРИКЛАД 7 (FAIL — Rule 4, відсутній опис рани при HP у зоні 40-69):
{
  "log_analysis": "Rule 0: немає '🛑'. Rule 3B: рядків '📈' немає. Rule 3A: 'Бойові: Кидок 33 + Навичка 40 = 73 (Ціль: 90) -> ПРОВАЛ' — коректно. Rule 4 Здоров'я: '❤️ Здоров'я: 80 -> 48 (-32)' — залишок 48, зона 40-69. UI ПОВИНЕН містити явний опис рани (кров, біль, рана). UI_TEXT: 'Противник відступив у тінь. Ти переводиш подих.' — жодної згадки рани чи болю. FAIL за Rule 4.",
  "status": "FAIL",
  "reason": "Rule 4: HP 48 (зона 40-69) — UI не містить опису рани при втраті 32 HP.",
  "ux_score": 3
}
</few_shot_examples>
"""


# === ХЕЛПЕРИ ===

# Поля профілю для трекінгу змін між ходами
_TRACKED_PROFILE_FIELDS = [
    "Здоров'я", "Енергія", "Особисте Золото",
    "Поточне місцезнаходження", "Поточна сцена", "Регіон",
    "Бойові навички", "Стрільба", "Дипломатія", "Інтрига", "Крадіжка",
    "Медицина", "Виживання", "Знання", "Інвентар", "Зброя", "Броня",
    "Вороги", "Друзі", "Ігровий час",
]


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


def _snapshot_profile(profile: dict) -> dict:
    """Знімок ключових полів профілю для diff."""
    if not profile:
        return {}
    return {k: profile.get(k, "") for k in _TRACKED_PROFILE_FIELDS}


def _diff_snapshots(before: dict, after: dict, label: str = "") -> list:
    """Порівнює два знімки, повертає список змін."""
    changes = []
    all_keys = set(list(before.keys()) + list(after.keys()))
    for k in sorted(all_keys):
        old_val = before.get(k, "")
        new_val = after.get(k, "")
        if str(old_val) != str(new_val):
            changes.append(f"{label}{k}: {old_val!r} -> {new_val!r}")
    return changes


def _validate_suggested_actions(suggested_actions: list) -> list:
    """Валідує структуру suggested_actions від GM_Logic. Повертає список помилок."""
    errors = []
    if not suggested_actions:
        errors.append("suggested_actions порожній")
        return errors
    if len(suggested_actions) < 4:
        errors.append(f"suggested_actions має {len(suggested_actions)} кнопок (очікується 4)")
    for i, action in enumerate(suggested_actions):
        if not action or action == "...":
            errors.append(f"suggested_actions[{i}] порожній або placeholder '...'")
    return errors


def _validate_gm_logic_output(ai_data: dict, legal_npc_names: list = None) -> list:
    """Валідує повну структуру JSON від GM_Logic. Повертає список помилок."""
    errors = []
    if not ai_data or not isinstance(ai_data, dict):
        errors.append("ai_data не є dict або порожній")
        return errors

    # director_notes
    notes = ai_data.get("director_notes")
    if notes is None:
        errors.append("director_notes відсутній")
    elif not isinstance(notes, list):
        errors.append(f"director_notes не list, а {type(notes).__name__}")
    elif len(notes) == 0:
        errors.append("director_notes порожній масив")
    else:
        for i, note in enumerate(notes):
            if not isinstance(note, str):
                errors.append(f"director_notes[{i}] не string, а {type(note).__name__}")

    # suggested_actions
    actions = ai_data.get("suggested_actions")
    if actions is None:
        errors.append("suggested_actions відсутній")
    elif not isinstance(actions, list):
        errors.append(f"suggested_actions не list, а {type(actions).__name__}")
    else:
        for i, item in enumerate(actions):
            if isinstance(item, dict):
                if "button" not in item:
                    errors.append(f"suggested_actions[{i}] без ключа 'button'")
                if "intent" not in item:
                    errors.append(f"suggested_actions[{i}] без ключа 'intent'")
            elif not isinstance(item, str):
                errors.append(f"suggested_actions[{i}] ні dict ні string, а {type(item).__name__}")

    # npc_updates
    npc_updates = ai_data.get("npc_updates")
    if npc_updates is not None:
        if not isinstance(npc_updates, list):
            errors.append(f"npc_updates не list, а {type(npc_updates).__name__}")
        else:
            for i, npc in enumerate(npc_updates):
                if not isinstance(npc, dict):
                    errors.append(f"npc_updates[{i}] не dict, а {type(npc).__name__}")
                    continue
                name = npc.get("Name", "")
                if not name:
                    errors.append(f"npc_updates[{i}] без 'Name'")
                elif legal_npc_names and name not in legal_npc_names:
                    errors.append(f"npc_updates[{i}] NPC '{name}' не в активному ростері {legal_npc_names}")

    # companion_npcs
    companions = ai_data.get("companion_npcs")
    if companions is not None and not isinstance(companions, list):
        errors.append(f"companion_npcs не list, а {type(companions).__name__}")

    return errors


def _detect_multi_turn_anomalies(turn_states: list) -> list:
    """Аналізує статистику по всіх ходах, виявляє аномальні патерни."""
    anomalies = []
    if len(turn_states) < 3:
        return anomalies

    # Трекаємо числові поля
    for field in ["Здоров'я", "Енергія", "Особисте Золото"]:
        values = []
        for s in turn_states:
            val = s.get(field, "")
            try:
                values.append(int(val))
            except (ValueError, TypeError):
                continue
        if len(values) < 3:
            continue

        # Монотонне падіння без відновлення
        deltas = [values[i+1] - values[i] for i in range(len(values)-1)]
        if all(d <= 0 for d in deltas) and any(d < 0 for d in deltas):
            anomalies.append(f"⚠️ {field} тільки падає і ніколи не відновлюється: {values}")

        # Раптовий стрибок вгору без причини (>50% від поточного)
        for i, d in enumerate(deltas):
            if d > 0 and values[i] > 0 and d > values[i] * 0.5:
                anomalies.append(f"⚠️ {field} раптовий стрибок +{d} на ході {i+2}: {values[i]} -> {values[i+1]}")

    # Локація змінюється кожен хід — підозрілий телепорт
    locations = [s.get("Поточне місцезнаходження", "") for s in turn_states]
    loc_changes = sum(1 for i in range(len(locations)-1) if locations[i] != locations[i+1])
    if loc_changes > len(locations) * 0.7 and len(locations) > 5:
        anomalies.append(f"⚠️ Локація змінюється занадто часто ({loc_changes}/{len(locations)-1} ходів)")

    return anomalies


async def _generate_with_retry(
    contents,
    temperature: float,
    client: genai.Client = None,
    max_retries: int = 5,
) -> str:
    """Обгортка навколо client.models.generate_content з 429-aware backoff."""
    if client is None:
        client = _get_test_client(0)

    config_kwargs = {"temperature": temperature}

    delay = 2
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            config = types.GenerateContentConfig(**config_kwargs)
            response = await asyncio.to_thread(
                client.models.generate_content,
                model=QA_MODEL_NAME,
                contents=contents,
                config=config,
            )
            return response.text
        except Exception as e:
            last_error = e
            if attempt == max_retries:
                break
            err_str = str(e)
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                delay = min(delay * 3, 120)
                log.warning(f"⚠️ 429 TPM hit. Retry {attempt}/{max_retries}, пауза {delay}с...")
            else:
                log.warning(f"⚠️ Retry {attempt}/{max_retries} після помилки: {e}. Пауза {delay}с...")
                delay += 2
            await asyncio.sleep(delay)

    raise RuntimeError(f"Всі {max_retries} спроби вичерпано. Остання помилка: {last_error}")


# === MOCK MESSAGE (stub для чіт-команд без Telegram) ===

class _MockMessage:
    """Мінімальний stub Telegram-повідомлення для виклику чіт-команд у тестах."""
    def __init__(self, text: str = ""):
        self.text = text
        self._response = ""

    async def answer(self, text, **kwargs):
        self._response = str(text)


# === АДАПТЕР ===

class RPGTesterAdapter:
    def __init__(self, chat_id: int, char_name: str, house_name: str):
        self.chat_id = chat_id
        self.char_name = char_name
        self.house_name = house_name
        self._row_number = None  # зберігається для cleanup()
        self._last_thoughts = ""

    async def initialize(self, start_location=None, start_scene=None):
        log.info(f"⚙️ Ініціалізація тестового персонажа ({self.char_name})...")
        house_data = await get_house_stats_data(self.house_name)
        full_profile = await generate_initial_stats(self.char_name, self.house_name, house_data)

        if not full_profile:
            raise RuntimeError("❌ Не вдалося створити тестового персонажа. Перевір назву дому в БД.")

        if start_location:
            from core.world_constants import get_region_for_location
            full_profile["Поточне місцезнаходження"] = start_location
            full_profile["Регіон"] = get_region_for_location(start_location) or full_profile.get("Регіон", "")
            log.info(f"📍 Start location override: {start_location}")
        if start_scene:
            full_profile["Поточна сцена"] = start_scene
            log.info(f"🎬 Start scene override: {start_scene}")

        await save_user_data(self.chat_id, full_profile, self.char_name)
        user_sessions[self.chat_id] = {"state": "INITIALIZING", "history": []}

        # Заселяємо світ канонічними + фоновими NPC (як у bot/handlers.py:170-175)
        start_loc = full_profile.get("Поточне місцезнаходження", "Вестерос")
        log.info(f"🌍 Заселяю світ канонічними NPC (локація: {start_loc})...")
        clear_npc_cache()  # Очищуємо стейл-кеш перед перезаписом NPC_DB
        await background_canon_generation(excluded_name=self.char_name)
        await populate_contextual_npcs(start_loc, "Start of the game. Normal daily routine.", excluded_name=self.char_name)
        await refresh_npc_database()  # Гарантуємо свіжий кеш після перезапису

        # Anti-Doppelganger верифікація: перевіряємо що гравець НЕ в ростері NPC
        _, legal_names, _ = get_location_npcs(start_loc, "Невідомо")
        if self.char_name in legal_names:
            log.warning(f"⚠️ DOPPELGANGER DETECTED: '{self.char_name}' знайдено в NPC ростері після world setup!")
        else:
            log.info(f"✅ Anti-Doppelganger: '{self.char_name}' відсутній в NPC ростері — OK")

        user_sessions[self.chat_id]['state'] = "GAME_ACTIVE"

        # Retry loop: Google Sheets propagation delay
        profile_check, row = None, None
        for attempt in range(1, 6):
            profile_check, row = await get_user_data(self.chat_id)
            if profile_check and row:
                break
            log.warning(f"⏳ БД ще не бачить запис, спроба {attempt}/5...")
            await asyncio.sleep(2 * attempt)
        else:
            raise RuntimeError(f"❌ Профіль не з'явився в БД після 5 спроб (user_id={self.chat_id})")

        self._row_number = row
        log.info(f"✅ Персонажа створено. Рядок у БД: {row}. Локація: {full_profile.get('Поточне місцезнаходження')}")

    async def process_cheat(self, cmd_str: str) -> str:
        """Виконує чіт-команду напряму, без Telegram-контексту. Повертає текст відповіді."""
        from core.cheats import (
            cmd_killnpc, cmd_godmode, cmd_puppet, cmd_erotic,
            cmd_heal, cmd_sethp, cmd_setenergy, cmd_setgold,
            cmd_addskill, cmd_setskill, cmd_tp, cmd_setrep, cmd_tpnpc,
            cmd_delnpc, cmd_cleanscene, cmd_addtime,
        )
        _dispatch = {
            "/killnpc": cmd_killnpc, "/delnpc": cmd_delnpc,
            "/godmode": cmd_godmode, "/puppet": cmd_puppet, "/erotic": cmd_erotic,
            "/heal": cmd_heal, "/sethp": cmd_sethp,
            "/setenergy": cmd_setenergy, "/setgold": cmd_setgold,
            "/addskill": cmd_addskill, "/setskill": cmd_setskill,
            "/tp": cmd_tp, "/setrep": cmd_setrep, "/tpnpc": cmd_tpnpc,
            "/cleanscene": cmd_cleanscene, "/addtime": cmd_addtime,
        }
        parts = cmd_str.strip().split()
        cmd = parts[0].lower()
        args = parts[1:]
        handler = _dispatch.get(cmd)
        if not handler:
            return f"❌ Невідома чіт-команда: {cmd}"
        mock = _MockMessage(cmd_str)
        await handler(mock, None, self.chat_id, args)
        return mock._response

    async def process(self, action: str):
        if action.startswith("/"):
            response = await self.process_cheat(action)
            return response, f"[CHEAT] {action} → {response}", [], None

        raw_response, suggested_actions = await process_game_turn(self.chat_id, action)

        # Збираємо роздуми моделей і додаємо до логів
        thoughts_entries = get_thoughts_log()
        if thoughts_entries:
            thoughts_lines = []
            for e in thoughts_entries:
                short_model = e["model"].replace("gemma-4-31b-it", "G4").replace("gemma-3-27b-it", "G3")
                thoughts_lines.append(f"  [{short_model}] {e['thought'][:400].strip()}")
            self._last_thoughts = "\n".join(thoughts_lines)
        else:
            self._last_thoughts = ""

        if "📊" in raw_response:
            parts = raw_response.split("📊")
            ui_text = parts[0].strip()
            logs = "📊 " + parts[1].strip()
        else:
            ui_text = raw_response.strip()
            logs = "Логи відсутні."

        if suggested_actions:
            logs += f"\n💡 Згенеровані дії: {', '.join(suggested_actions)}"

        # Витягуємо ai_data з session для валідації GM_Logic
        session = user_sessions.get(self.chat_id, {})
        last_ai_data = session.get("last_ai_data")

        return ui_text, logs, suggested_actions, last_ai_data

    def get_timing(self) -> str:
        """Витягує таймінг останнього ходу з user_sessions."""
        session = user_sessions.get(self.chat_id, {})
        return session.get("last_debug_time", "Таймінг недоступний")

    async def cleanup(self):
        """Видаляє тестовий рядок з Google Sheets за user_id (безпечно для паралельного запуску)."""
        def _sync_delete():
            try:
                sheet = db.get_sheet(TAB_USERS)
                if not sheet:
                    return False
                cells = sheet.findall(str(self.chat_id), in_column=1)
                if cells:
                    sheet.delete_rows(cells[0].row)
                    return True
                return False
            except Exception as e:
                log.error(f"❌ Помилка cleanup: {e}")
                return False

        success = await asyncio.to_thread(_sync_delete)
        if success:
            log.info(f"🧹 Тестовий запис (user_id={self.chat_id}) видалено з БД.")
        else:
            log.warning(f"🧹 Не вдалося видалити тестовий запис (user_id={self.chat_id}).")


# === ЗВІТ ===

def _print_report(results: list, profile_key: str = "test",
                   turn_states: list = None, action_log: list = None):
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

    # Multi-turn anomaly detection
    if turn_states and len(turn_states) >= 3:
        anomalies = _detect_multi_turn_anomalies(turn_states)
        if anomalies:
            log.info("\n🔎 MULTI-TURN АНОМАЛІЇ:")
            for a in anomalies:
                log.warning(f"  {a}")
        else:
            log.info("\n🔎 Multi-turn аномалій не виявлено.")

    log.info("=" * 50)

    # JSON-звіт з replay data
    report_data = {
        "summary": {
            "turns": len(results), "pass": passes, "fail": fails, "avg_ux": avg_score,
            "anomalies": _detect_multi_turn_anomalies(turn_states) if turn_states else [],
        },
        "turns": results,
    }
    # Replay: зберігаємо action sequence для відтворення тесту через scripted_actions
    if action_log:
        report_data["replay_actions"] = {
            str(a["turn"]): a["action"] for a in action_log
        }
        report_data["action_log"] = action_log

    report_path = f"qa_logs/report_{profile_key}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    try:
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report_data, f, ensure_ascii=False, indent=2)
        log.info(f"📄 JSON-звіт збережено: {report_path}")
    except Exception as e:
        log.warning(f"Не вдалося зберегти JSON-звіт: {e}")


# === ГОЛОВНИЙ ЦИКЛ ТЕСТУ ===

async def run_test(max_turns: int = 5, profile: dict = None, profile_key: str = "unknown", profile_index: int = 0):
    p = profile or {}

    # Per-profile API client (round-robin з пулу ключів)
    test_client = _get_test_client(profile_index)
    key_idx = profile_index % len(GEMINI_API_KEYS_TEST)
    log.info(f"🔑 API ключ: #{key_idx + 1}/{len(GEMINI_API_KEYS_TEST)}")

    # Per-profile history window (менше для агресивних профілів)
    history_window = p.get("history_window", PLAYER_HISTORY_MAX_MESSAGES)

    adapter = RPGTesterAdapter(
        chat_id=p["user_id"],
        char_name=p["char_name"],
        house_name=p["house"],
    )
    await adapter.initialize(
        start_location=p.get("start_location"),
        start_scene=p.get("start_scene"),
    )

    results = []
    action_log = []        # Replay: всі дії для відтворення тесту
    turn_states = []       # Multi-turn: знімки профілю після кожного ходу
    scripted_actions: dict = p.get("scripted_actions", {})
    killed_npcs: list = []

    if SLEEP_INIT_SEC > 0:
        log.info(f"⏳ Пауза {SLEEP_INIT_SEC}с перед першим ходом...")
        await asyncio.sleep(SLEEP_INIT_SEC)
    ui_text, _, _, _ = await adapter.process("Я оглядаюся навколо")

    # Початковий знімок профілю
    _init_profile, _ = await get_user_data(adapter.chat_id)
    prev_profile_snap = _snapshot_profile(_init_profile)
    prev_npc_names = list(get_location_npcs(
        _init_profile.get("Поточне місцезнаходження", ""), "Невідомо"
    )[1]) if _init_profile else []

    # Перший елемент history — системний промпт гравця (pinned, не потрапляє у rolling window).
    # Gemma не підтримує system_instruction у GenerateContentConfig, тому передаємо як user-повідомлення.
    player_history_pinned = [
        types.Content(role="user", parts=[types.Part.from_text(text=p["prompt"])])
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

            # Scripted action або AI-generated
            if turn in scripted_actions:
                player_action = scripted_actions[turn]
                log.info(f"📜 СКРИПТ (Хід {turn}): {player_action}")
            else:
                # Per-turn user message з реальними даними (fix #1)
                turn_user_msg = (
                    f"SYSTEM_RESPONSE: {ui_text}\n\n"
                    f"PLAYER_CURRENT_STATE: {profile_summary}\n\n"
                    f"TURN_NUMBER: {turn}/{max_turns}\n\n"
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
                        client=test_client,
                    )
                    action_data = clean_and_parse_json(raw_text)
                    if not action_data:
                        raise ValueError("clean_and_parse_json повернув None")
                    player_action = action_data.get("action", "Дія не розпізнана")
                    state_check = action_data.get("state_check", "")
                    log.info(f"👤 ГРАВЕЦЬ [Стан: {state_check}] (Думка: {action_data.get('thought_process', '')}):\n-> {player_action}\n")
                    player_history_dynamic.append(
                        types.Content(role="model", parts=[types.Part.from_text(text=raw_text)])
                    )
                except Exception as e:
                    log.error(f"❌ Помилка Agent-Player: {e}")
                    break

                # Rolling window (тільки dynamic частина, pinned залишається завжди)
                if len(player_history_dynamic) > history_window:
                    player_history_dynamic = player_history_dynamic[-history_window:]

            # Пауза перед рушієм (0 за замовчуванням — рушій на своєму ключі)
            if not player_action.startswith("/") and SLEEP_AFTER_ENGINE_SEC > 0:
                log.info(f"⏳ Пауза {SLEEP_AFTER_ENGINE_SEC}с перед викликом рушія...")
                await asyncio.sleep(SLEEP_AFTER_ENGINE_SEC)

            new_ui_text, system_logs, new_suggested, last_ai_data = await adapter.process(player_action)
            log.info(f"⚙️  ЛОГИ РУШІЯ:\n{system_logs}\n")
            if adapter._last_thoughts:
                log.info(f"💭 РОЗДУМИ МОДЕЛЕЙ:\n{adapter._last_thoughts}\n")

            # === Збір даних після ходу ===

            # Replay: зберігаємо дію
            action_log.append({"turn": turn, "action": player_action})

            # Таймінг рушія
            timing = adapter.get_timing()
            log.info(f"⏱️  {timing}")

            # Profile diff
            curr_profile, _ = await get_user_data(adapter.chat_id)
            curr_snap = _snapshot_profile(curr_profile)
            profile_changes = _diff_snapshots(prev_profile_snap, curr_snap, "📋 ")
            if profile_changes:
                log.info("📋 ЗМІНИ ПРОФІЛЮ:")
                for ch in profile_changes:
                    log.info(f"  {ch}")
            turn_states.append(curr_snap)
            prev_profile_snap = curr_snap

            # NPC roster diff
            curr_loc = curr_snap.get("Поточне місцезнаходження", "")
            curr_scene = curr_snap.get("Поточна сцена", "Невідомо")
            _, curr_npc_names, _ = get_location_npcs(curr_loc, curr_scene)
            added_npcs = [n for n in curr_npc_names if n not in prev_npc_names]
            removed_npcs = [n for n in prev_npc_names if n not in curr_npc_names]
            if added_npcs or removed_npcs:
                log.info(f"👥 NPC РОСТЕР: +{added_npcs} -{removed_npcs}")
            prev_npc_names = list(curr_npc_names)

            # GM_Logic structure validation (suggested_actions + full ai_data)
            action_errors = _validate_suggested_actions(new_suggested)
            if action_errors:
                log.warning(f"⚠️ GM_Logic suggested_actions: {', '.join(action_errors)}")

            # Full GM_Logic JSON validation
            gm_errors = _validate_gm_logic_output(last_ai_data, curr_npc_names)
            if gm_errors:
                log.warning(f"⚠️ GM_Logic JSON validation:")
                for ge in gm_errors:
                    log.warning(f"  {ge}")

            # Відстежуємо вбитих NPC — евалюатор перевірятиме їх воскресіння у наступних ходах
            if player_action.lower().startswith("/killnpc") and "✅" in system_logs:
                npc_name = " ".join(player_action.strip().split()[1:])
                if npc_name:
                    killed_npcs.append(npc_name)
                    log.info(f"💀 [QA] '{npc_name}' додано до списку мертвих — евалюатор перевірятиме воскресіння.")

            # Чіт-команди не потребують евалюатора — пропускаємо хід
            if player_action.startswith("/"):
                ui_text = new_ui_text
                continue

            if SLEEP_AFTER_EVALUATOR_SEC > 0:
                log.info(f"⏳ Пауза {SLEEP_AFTER_EVALUATOR_SEC}с перед Евалюатором...")
                await asyncio.sleep(SLEEP_AFTER_EVALUATOR_SEC)

            # === EVALUATOR AI ===
            dead_npc_extra = ""
            if killed_npcs:
                names_str = ", ".join(f"'{n}'" for n in killed_npcs)
                dead_npc_extra = (
                    f"\nMERTVI_NPC_CHECK (КРИТИЧНО): В цьому тест-сеансі вбиті NPC: {names_str}. "
                    "Якщо UI_TEXT описує когось з них як ФІЗИЧНО ПРИСУТНЬОГО, живого або такого що активно діє — FAIL. "
                    "ВИНЯТОК: згадка у минулому часі ('загинув', 'тіло X', 'після смерті') — PASS."
                )
            evaluator_extra = p.get("evaluator_extra_rules", "") + dead_npc_extra
            eval_extra_block = f"\n<profile_specific_rules>\n{evaluator_extra}\n</profile_specific_rules>" if evaluator_extra else ""
            eval_prompt_text = (
                EVALUATOR_PROMPT
                .replace("{internal_logs_data}", system_logs)
                .replace("{ui_text_data}", new_ui_text)
                .replace("{player_action_data}", player_action)
                + eval_extra_block
                + "\nВАЖЛИВО: Відповідай ТІЛЬКИ валідним JSON. Без Markdown."
            )

            eval_content = [
                types.Content(role="user", parts=[types.Part.from_text(text=eval_prompt_text)])
            ]
            try:
                eval_data = None
                for eval_attempt in range(1, 3):
                    eval_raw = await _generate_with_retry(eval_content, temperature=0.0, client=test_client)
                    eval_data = clean_and_parse_json(eval_raw)
                    if eval_data:
                        break
                    log.warning(f"⚠️ Евалюатор: JSON parse fail, спроба {eval_attempt}/2. Raw: {eval_raw[:200]}")
                    if eval_attempt < 2 and SLEEP_AFTER_EVALUATOR_SEC > 0:
                        await asyncio.sleep(SLEEP_AFTER_EVALUATOR_SEC)
                if not eval_data:
                    log.warning(f"⚠️ Евалюатор не повернув валідний JSON після 2 спроб. Хід {turn} пропущено (SKIP).")
                    results.append({"turn": turn, "status": "SKIP", "ux_score": "?", "reason": "Evaluator JSON parse failure"})
                    ui_text = new_ui_text
                    continue

                status = eval_data.get("status", "ERROR")
                ux_score = eval_data.get("ux_score", "?")
                reason = eval_data.get("reason", "—")

                # A04 FIX: Post-hoc validation — перевіряємо false positive для Rule 4c (E>29)
                if status == "FAIL" and ("4c" in reason.lower() or "≤29" in reason or "<=29" in reason or "залишок" in reason.lower()):
                    # Витягуємо реальний залишок енергії з логів рушія
                    energy_match = re.search(r'⚡ Енергія: \d+ -> (\d+)', system_logs)
                    if energy_match:
                        actual_energy = int(energy_match.group(1))
                        if actual_energy > 29:
                            log.warning(f"⚠️ FALSE POSITIVE: Евалюатор сказав FAIL (Rule 4c), але E={actual_energy} > 29. Переозначено на PASS.")
                            status = "PASS"
                            reason = f"[OVERRIDDEN] Евалюатор помилився: E={actual_energy} > 29, Rule 4c не застосовується. Оригінал: {reason}"

                log.info(f"🔍 АНАЛІЗ ЛОГІВ: {eval_data.get('log_analysis', 'Аналіз відсутній')}")
                log.info(f"⚖️  ЕВАЛЮАТОР [UX: {ux_score}/10] -> {status}")

                results.append({"turn": turn, "status": status, "ux_score": ux_score, "reason": reason})

                if status == "FAIL":
                    log.warning(f"   Причина: {reason}")
                    log.warning("🚨 КРИТИЧНА ПОМИЛКА ЗНАЙДЕНА. Продовжуємо для збору всіх FAILів.")
                    # break  # вимкнено для нічного режиму — зранку переглянути всі FAILи
                else:
                    log.info(f"   Зауваження: {reason}")

            except Exception as e:
                log.error(f"❌ Помилка Agent-Evaluator: {e}")
                pass  # skip turn, continue — не зупиняємо тест через падіння евалюатора

            ui_text = new_ui_text

    finally:
        await adapter.cleanup()
        _print_report(results, profile_key=profile_key,
                      turn_states=turn_states, action_log=action_log)


# === ТОЧКА ВХОДУ ===

if __name__ == "__main__":
    import argparse
    from qa_profiles import TEST_PROFILES, list_profiles
    from database.operations import load_lore_data, refresh_npc_database

    parser = argparse.ArgumentParser(description="QA Integration Test for GoT RPG")
    parser.add_argument("--profile", type=str, default="chaos_engineer",
                        choices=list(TEST_PROFILES.keys()),
                        help="Test profile to use (default: chaos_engineer)")
    parser.add_argument("--turns", type=int, default=20,
                        help="Max turns per test run (default: 20)")
    parser.add_argument("--list", action="store_true",
                        help="List all available profiles and exit")
    args = parser.parse_args()

    if args.list:
        print("Доступні профілі:")
        list_profiles()
        exit(0)

    selected = TEST_PROFILES[args.profile]
    profile_index = list(TEST_PROFILES.keys()).index(args.profile)
    _setup_logging(args.profile)
    log.info(f"🚀 Профіль: {selected['name']} ({args.profile}) | Персонаж: {selected['char_name']} | Ходів: {args.turns}")
    log.info(f"🔑 Доступних API ключів: {len(GEMINI_API_KEYS_TEST)}")

    async def main():
        # Підміняємо ключ рушія на тестовий — гра НІКОЛИ не використовує продакшн-ключ під час тестів
        import core.ai_client as _ai
        if GEMINI_API_KEYS_TEST:
            _test_key = GEMINI_API_KEYS_TEST[profile_index % len(GEMINI_API_KEYS_TEST)]
            _ai.client = genai.Client(api_key=_test_key)
            log.info(f"🔄 Ключ рушія підмінено на тестовий (#{profile_index % len(GEMINI_API_KEYS_TEST) + 1})")
        else:
            log.warning("⚠️ Тестових ключів немає — рушій використовує продакшн-ключ!")

        await load_lore_data()
        await refresh_npc_database()
        await run_test(max_turns=args.turns, profile=selected, profile_key=args.profile, profile_index=profile_index)

    asyncio.run(main())
