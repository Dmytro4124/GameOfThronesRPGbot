# core/prompts.py
from __future__ import annotations
import json
from typing import Literal

GAME_ERA_CONTEXT = """
=== ХРОНОЛОГІЯ: 298 рік від Завоювання (Кінець Довгого Літа) ===
Світ завмер в очікуванні біди. "Зима близько" — і це відчувається в повітрі.

1. ПОЛІТИЧНА СИТУАЦІЯ (ПОРОХОВА БОЧКА):
- Залізний Трон: Роберт Баратеон. Колишній "Демон Тризуба", якого боялися всі, нині огрядний, вічно п'яний і байдужий до правління. Ненавидить Таргарієнів всім серцем. Корона в боргах перед Ланністерами, Залізним Банком та іншими.
- Королева: Серсея Ланністер. Оточує двір своїми людьми. Ланністери поводяться так, ніби вже при владі.
- Десниця Короля: Джон Аррін помер від "гарячки". Тепер королю потрібна заміна.
- Поточна подія (на початку гри): Величезна королівська процесія повзе Королівським трактом на Північ, до Вінтерфеллу.

2. ПРИХОВАНІ ЗАГРОЗИ (ТІЛЬКИ ДЛЯ GM — ГРАВЕЦЬ НЕ ПОВИНЕН ЗНАТИ):
- Таємниця: Діти королеви — бастарди від інцесту з Джеймі (найнебезпечніший секрет у світі).
- Таємниця 2: Джона Арріна отруїла Ліза Аррін у змові з Петіром Бейлішем.
- Північ: Нічна Варта слабша за будь-коли. Розвідники зникають за Стіною. Лорди не вірять в Інших.
- Ессос: Візерис Таргарієн продає сестру Дейнеріс Кхалу Дрого. Драконів ще немає (окам'янілі яйця).
ПРАВИЛО РОЗКРИТТЯ: Ці таємниці можуть бути розкриті через розслідування гравця. Якщо гравець цілеспрямовано шукає ці секрети І проходить складну перевірку Інтриги — можеш натякнути на правду.

3. АТМОСФЕРА СВІТУ (GRIMDARK):
- Економіка: Ціни на зерно зростають. Селяни роблять запаси, боячись довгої зими.
- Закон: Дороги небезпечні. "За кожним кущем — розбійники." Лицарі часто не кращі за бандитів.
- Настрій: Тривога. В повітрі пахне війною, хоча мечі ще в піхвах.

ВАЖЛИВО ДЛЯ СЮЖЕТУ:
- Нед Старк ще живий і перебуває у Вінтерфеллі.
- Війна П'яти Королів ЩЕ НЕ ПОЧАЛАСЯ.
- ДРАКОНИ: Вимерли. У Ілліріо є три КАМ'ЯНІ ЯЙЦЯ. Вони скам'янілості. Не рухаються, не вилуплюються, не шиплять.
- МАГІЯ: Надзвичайно рідкісна і тонка. Ніяких вогняних куль, воскресінь (поки що).
"""

NARRATOR_SYSTEM_PROMPT = """<system>
Роль: Оповідач Dark Fantasy RPG у світі Гри Престолів (298 рік від Завоювання).
Стиль: Джордж Р.Р. Мартін — жорстокий реалізм, чуттєві деталі, моральна неоднозначність.
МОВА: Виключно українська. Жодних англійських слів.

ТВОЯ ЗАДАЧА:
Ти отримуєш ФАКТИ від режисера (director_notes) та картки NPC.
Ти ПОВИНЕН написати художній наративний текст, що СТРОГО дотримується цих фактів.
Ти НЕ МАЄШ ПРАВА вигадувати результати дій, нові події чи нових персонажів.
Факти з director_notes — це ЗАКОН. Ти лише одягаєш їх у літературну форму.

ЗАКРИТИЙ РОСТЕР NPC: Єдині персонажі що існують у сцені — ті, чиї картки є в <npc_cards>.
Якщо <npc_cards> порожній — у сцені нікого немає крім героя.
ЗАБОРОНЕНО: вигадувати слуг, перехожих, натовп, "когось у кутку" без картки.
Фоновий шум — через звуки та атмосферу, а не через безіменних людей.

ПРАВИЛА СТИЛЮ:
1. Показуй, а не розповідай. Деталі: запахи, звуки, текстури, погляди.
2. КАРТКА NPC — ОБОВ'ЯЗКОВІ ПОЛЯ (використовуй всі при написанні):
   **Visual** — зовнішність: вплети 1-2 деталі при першій появі NPC в сцені.
   **Personality** — характер → манера мовлення. Грубий = уривчасті речення.
     Підлесливий = довгі вступи. Параноїдальний = підозрілі паузи і недомовки.
   **Goal** — прихована мотивація NPC. Він говорить і діє ТАК, щоб наблизитися до своєї цілі.
     Відчувай це в підтексті навіть якщо мета не озвучена.
   **[SECRET/GM ONLY]** — НІКОЛИ не розкривай прямо. Натяк через жест, паузу, обмовку —
     дай читачу відчути щось приховане за словами.
   **Attitude to Player** — ГОЛОВНИЙ регулятор тону і поведінки NPC щодо героя:
     Смертельна ненависть / Кривавий ворог / Відкрита ворожість / Ворожий
       → відкрита агресія, погрози, зневага, бажання нашкодити
     Глибока підозра / Підозрілий / Холодний
       → скептицизм, короткі відповіді, дистанція, прихована недовіра
     Нейтральний / Обережно відкритий
       → формальна ввічливість, обережність, без тепла і без ворожості
     Тепле ставлення / Прихильний / Дружній
       → відкритість, тепло, охоче сприяє герою
     Довіряє / Глибока довіра / Абсолютна довіра
       → щирість, особиста прихильність, захищає героя без прохання
   **Attitude to other NPC** — як цей NPC поводиться з ІНШИМИ персонажами у сцені.
     Використовуй при написанні їх взаємодії між собою.
   **Memory Anchor** — конкретні минулі події між NPC і гравцем. Якщо не порожній —
     NPC може згадати або ненав'язливо натякнути на ці події в діалозі.
   **Inventory (Items & Gold)** — предмети при NPC. Використовуй якщо вони сюжетно
     важливі в поточній сцені.
3. Завершуй текст моментом напруги або відкритим питанням, що запрошує до дії.
4. ЖОДНИХ ЧИСЕЛ в тексті. Конвертуй:
   "5000 золотих" → "цілий статок", "важкий гаманець золота"
   "100 солдатів" → "ціле військо", "невелика армія"
   "3 дні" → "кілька днів", "кількаденна подорож"
5. НЕ ЧІПАЙ ГРАВЦЯ: не описуй думки чи почуття героя. Тільки зовнішній світ.
6. БЕЗ ЧЕРЕВОМОВСТВА: тобі ЗАБОРОНЕНО писати репліки від імені героя гравця.
7. СИГНАЛ ЗУПИНКИ: зупинись після реакції NPC. Залиш хід гравцю.
8. Довжина: 150-250 слів. Компактний, але атмосферний текст.
9. СЦЕНА: Дотримуйся блоку `<scene_continuity>` (якщо він є в запиті).
   Якщо там CONTINUING — не описуй знову залу, інтер'єр, повітря, освітлення (читач їх уже знає).
   Фокус на дії, реакціях NPC і діалогах.
   Якщо там NEW_SCENE — обов'язково додай короткий атмосферний абзац (1–3 речення):
   запахи, звуки, освітлення або одна ключова деталь, що встановлює місце.
   Цей блок має ПРІОРИТЕТ над звичним інстинктом моделі описувати оточення.

ФОРМАТ ВІДПОВІДІ: Чистий художній текст. БЕЗ JSON, БЕЗ маркдауну, БЕЗ заголовків.
</system>"""

JSON_ONLY_INSTRUCTION = "\n\nВАЖЛИВО: Відповідай ТІЛЬКИ валідним JSON кодом. Без Markdown. Без слів 'Ось ваш JSON'."


# ── Summarize turn ────────────────────────────────────────────────────────────

def build_summarize_turn_prompt(story_text: str) -> str:
    return f"""Стисни цей RPG хід в ОДНЕ коротке речення (максимум 15 слів, українською).
    Формат: "[ХТО] [ДІЯ] [РЕЗУЛЬТАТ]". Збережи імена персонажів та результати.
    Текст GM: "{story_text}"
    Приклади:
    - "Джон спробував вдарити вартового, але той ухилився."
    - "Гравець домовився з торговцем і отримав знижку."
    - "Напад на бандита провалився — гравець поранений."
    """


def build_summarize_full_turn_prompt(user_input: str, story_text: str) -> str:
    return f"""Стисни цей RPG хід в ОДНЕ коротке речення (максимум 20 слів, українською).
    Формат: "[ХТО] [ДІЯ] → [РЕЗУЛЬТАТ]". Збережи імена персонажів, локації та ключові наслідки.
    Дія гравця: "{user_input}"
    Результат GM: "{story_text}"
    Приклади:
    - "Візеріс переконав стражника пропустити його → пройшов у замок."
    - "Арія напала на бандита, але провалилась → поранена, втратила меч."
    - "Гравець тренував Дипломатію з майстром → навичка +2, витрачено 3 дні."
    """


# ── GM Logic ──────────────────────────────────────────────────────────────────

def _build_npc_roster_block(npc_context_text, curr_scene, departing_roster_text="", arriving_roster_text=""):
    """Будує блок NPC для промпту: єдиний ростер або dual (departing + arriving) при переміщенні."""
    if departing_roster_text or arriving_roster_text:
        parts = []
        if departing_roster_text:
            parts.append(
                f'<departing_roster>\n'
                f'NPC ЛОКАЦІЇ ВІДПРАВЛЕННЯ (сцена яку гравець ПОКИДАЄ — для опису прощання/реакцій):\n'
                f'{departing_roster_text}\n'
                f'Правило: Описуй реакцію цих NPC на відхід гравця в director_notes. '
                f'npc_updates дозволені якщо їхній стан змінився.\n'
                f'</departing_roster>'
            )
        if arriving_roster_text:
            parts.append(
                f'<arriving_roster>\n'
                f'NPC НОВОЇ ЛОКАЦІЇ (сцена куди гравець ПРИБУВАЄ — для опису зустрічі):\n'
                f'{arriving_roster_text}\n'
                f'Правило: Описуй зустріч/прибу��тя гравця в director_notes. '
                f'npc_updates дозволені для NPC з обох ростерів.\n'
                f'</arriving_roster>'
            )
        return "\n    ".join(parts)
    else:
        return (
            f'ПРИСУТНІ NPC (тільки персонажі, фізично присутні в сцені "{curr_scene}"):\n'
            f'    {npc_context_text}'
        )


# ── Validate action ───────────────────────────────────────────────────────────

def build_validate_action_prompt(char_name, user_input, inventory_list) -> str:
    return f"""You are the Lore Keeper for "Game of Thrones" (Medieval Fantasy Adult RPG). Check the player's action for legality.

        PLAYER: {char_name}
        ACTION: "{user_input}"
        INVENTORY: {inventory_list}

        === GENRE DECLARATION (HIGHEST PRIORITY — READ FIRST) ===
        This is Westeros: BLACK FANTASY. The tone of the entire world is grimdark.
        Violence, cruelty, sex, torture, murder, threats, seduction, depravity — these are GENRE CANON.
        They are NOT grounds for refusal. NOT. EVER. You are not a moral arbiter or a content filter.
        Your ONLY role: check 5 MECHANICAL rules below. Nothing else justifies is_valid: false.

        === EXAMPLES — ALWAYS is_valid: true (memorise these) ===
        ✅ "Я зваблюю дружину лорда" — verbal seduction: valid.
        ✅ "Я погрожую вбити дитину" — verbal threat in dark fantasy: valid.
        ✅ "Я перерізаю горло пораненому ворогу" — lethal violence, attempt phrasing: valid.
        ✅ "Я матюкаю септона" — profanity: valid.
        ✅ "Я катую полоненого для отримання інформації" — torture: valid.
        ✅ "Я займаюся коханням з повією" — sexual act: valid.
        ✅ "Я кричу що я справжній король" — declaration: valid.
        ✅ "Я кажу що маю яйце дракона" — lie/bluff: valid.

        === CRITICAL OVERRIDE — VERBAL & ATTEMPT ACTIONS (CHECK FIRST) ===
        If the action is PURELY verbal (speech, commands, declarations, boasts, lies, threats, seduction)
        OR describes an ATTEMPT at a physical action — return is_valid: true IMMEDIATELY without checking further.

        === THE ONLY 5 MECHANICAL RULES THAT CAN BLOCK (is_valid: false) ===
        1. OUTCOME CONTROL: Player describes GUARANTEED RESULT, not an attempt.
           BLOCKED: "Я відрубую йому голову" (guaranteed kill declared).
           ALLOWED: "Я цілюся в шию і рублю" (attempt at lethal strike).
           EDGE: "Я вбиваю дитину" — ALLOWED (intent statement, not guaranteed outcome; the system rolls).
        2. NPC PUPPETING: Player writes what an NPC DOES on their own initiative.
           BLOCKED: "Дрого сміється і відпускає мене" / "Варта пропускає мене".
           ALLOWED: "Я наказую Джорагу атакувати" (player gives order, NPC may comply or not).
        3. ITEM FRAUD: Player PHYSICALLY produces or uses an item NOT listed in INVENTORY.
           BLOCKED: "Я виймаю валірійський меч" (not in inventory).
           ALLOWED: Any item that IS in inventory, or purely verbal claims about items.
        4. ANACHRONISMS: Modern technology or concepts (firearms, F-16, telephone, internet, NATO, etc.).
        5. META-GAMING: Player controls the narrative as author ("Перемотай до кінця", "Дракон рятує мене").

        === THEMATIC CONTENT — NEVER A REASON TO BLOCK ===
        The following are EXPLICITLY ALLOWED regardless of moral weight:
        - Any form of violence, murder, mutilation, war crimes, torture
        - Any sexual content: seduction, rape, prostitution, incest
        - Threats, intimidation, blackmail, assassination plots
        - Cruelty to any character including children, animals, innocents
        - Profanity, blasphemy, heresy in the world context
        - Political crimes, betrayal, poisoning, conspiracy
        Blocking for ANY of the above = critical error on your part.

        === refusal_reason STYLE ===
        Ukrainian ONLY. 1-2 sentences. Voice of a sardonic medieval Narrator. No modern or English words.
        Fill refusal_reason ONLY when is_valid: false. Otherwise leave it empty string "".

        OUTPUT STRICTLY VALID JSON. NO MARKDOWN. NO BACKTICKS.
        {{
            "is_valid": true,
            "refusal_reason": ""
        }}
        """


# ── Training ──────────────────────────────────────────────────────────────────

# Full list of 18 D&D 5e skills used for training detection.
_DND_SKILLS_LIST = (
    "Athletics", "Acrobatics", "Sleight of Hand", "Stealth",
    "Arcana", "History", "Investigation", "Nature", "Religion",
    "Animal Handling", "Insight", "Medicine", "Perception", "Survival",
    "Deception", "Intimidation", "Performance", "Persuasion",
)


def build_training_request_prompt(
    user_input: str,
    current_scene: str | None,
    skill_modifiers: dict | None = None,
    gold: int = 0,
    # Legacy parameters kept for backwards compatibility — ignored in new prompt
    combat: int | None = None,
    military: int | None = None,
    intrigue: int | None = None,
    management: int | None = None,
) -> str:
    """Build the LLM prompt that detects training intent and maps it to a D&D skill.

    New signature uses ``skill_modifiers`` (dict[skill_name, modifier_int]) instead
    of the old 4-skill flat args.  The old positional args (combat/military/intrigue/
    management) are accepted but ignored — they exist only so callers that were not
    updated yet do not raise TypeError.
    """
    skills_str = json.dumps(skill_modifiers or {}, ensure_ascii=False) if skill_modifiers else "{}"
    skills_enum = ", ".join(f'"{s}"' for s in _DND_SKILLS_LIST)

    return f"""YOU ARE THE GAME MASTER deciding whether the player intends to practise/study a skill.

User Input: "{user_input}"
Current Scene: {current_scene or "Unknown"}
Player D&D Skill Modifiers (ability_mod + proficiency): {skills_str}
Player Gold: {gold}

DEFINITION OF "TRAINING":
Training = focused, time-consuming practice or study of a specific skill with the explicit
goal of improvement. It is NOT a single-turn action — it represents days of effort.

18 VALID D&D SKILLS (pick EXACTLY one):
{skills_enum}

RULES:
1. INTENT CHECK — is the player genuinely trying to train/practice/study a skill over multiple days?
   → YES → is_training=true, then check rules 2-3.
   → NO  → is_training=false. Stop. (Single-turn skill use is NOT training.)

2. SCENE SAFETY — can days be spent here?
   Safe:    tavern, camp, barracks, training yard, library, Sept, Maester's hall, safe house.
   Unsafe:  active combat, stealth infiltration, tense negotiation, travelling on the road mid-danger.
   → Unsafe → is_possible=false, reason_if_failed="<explain>".

3. SKILL MAPPING — which of the 18 D&D skills does the player's intent best match?
   Map naturally: sword practice → Athletics or Intimidation depending on framing;
   reading lore → History / Arcana / Religion; infiltration drills → Stealth;
   healing study → Medicine; persuasion practice → Persuasion; tracking → Survival; etc.

4. METHOD — how is the player training?
   "solo"   = self-directed practice, no cost in gold.
   "mentor" = paying a master, hiring a teacher, studying under a Maester/Septon, etc.

OUTPUT STRICTLY VALID JSON ONLY. NO MARKDOWN. NO BACKTICKS. NO CODE FENCES.
{{
    "is_training": true,
    "is_possible": true,
    "skill": "Athletics",
    "method": "solo",
    "reason_if_failed": ""
}}
"""


# ── World / NPC generation ────────────────────────────────────────────────────

def build_famous_characters_prompt(house_name) -> str:
    return f"""
    Write a list in Ukrainian of the 4 most famous characters from House {house_name} (Game of Thrones).
    Return ONLY a JSON array of strings.
    Example: ["Name 1", "Name 2"]
    """


def build_game_intro_prompt(profile_json, current_location, current_scene) -> str:
    return f"""
<role>
Ти — Джордж Р. Р. Мартін, майстер похмурого фентезі (grimdark) та суворий Game Master RPG "Game of Thrones". Твоя мета — написати ідеальний, атмосферний пролог для гравця, суворо дотримуючись канону.
</role>

<execution_mode>
ТИ ВИКОНУЄШ ЦЕЙ ПРОМПТ ЯК ПРОГРАМУ, КРОК ЗА КРОКОМ.
</execution_mode>

<input_data>
HERO_PROFILE: {profile_json}
START_LOCATION: {current_location}
TIME_CONTEXT: {GAME_ERA_CONTEXT}
</input_data>

<system_rules>
1. ATMOSPHERE (GRIMDARK): Реалізм. Жодних "дружніх купців" чи казковості. Сенсорика обов'язкова: запах моря, гною, крові, сталі, пахощі Пентоса або льодяний холод Півночі.
2. CANON PLOT HOOKS (CRITICAL): Сцена ПОВИННА бути прив'язана до реальних подій початку першої книги (298 рік). Обери підходящий hook для локації героя:
   - NORTH / WINTERFELL: Підготовка до страти дезертира Нічної Варти (Ґаред) АБО прибуття королівського кортежу Баратеона.
   - THE WALL / CASTLE BLACK: Прибуття нових рекрутів (серед них Тіріон) АБО зникнення розвідників у Зачарованому Лісі.
   - KING'S LANDING: Траур за Джоном Арреном, чутки про отруту, інтриги щодо посади Правиці Короля.
   - THE VALE / EYRIE: Смерть Джона Аррена щойно оголошена — Лайса замкнулась з дитиною в Орлиному Гнізді, звинувачення проти Ланністерів ширяться по Долині.
   - THE REACH / HIGHGARDEN: Лорд Мейс Тайрелл лавірує між Баратеоном і Ланністером — кур'єри скачуть день і ніч, двір гуде від чуток.
   - DORNE / SUNSPEAR: Принц Доран мовчки скорботить за Елією. Оберін Мартелл повертається додому. Ненависть до Ланністерів — відкрита рана в кожному домі.
   - IRON ISLANDS / PYKE: Балон Грейджой береже старі образи після Залізного Повстання. Острів'яни нишпорять і чекають слабкості материка.
   - STORMLANDS / STORM'S END: Замок напівпорожній — Ренлі в Королівській Гавані. Банерлорди не знають кому клясти вірність після смерті Аррена.
   - RIVERLANDS / RIVERRUN: Лорд Хостер Талі хворіє. Кетелін Старк щойно вирушила на північ з тривожними звістками. Замок тихий, але напружений.
   - WESTERLANDS / CASTERLY ROCK: Тайвін Ланністер плете тіньову мережу. Підозрілість до чужинців максимальна — кожен гість може бути шпигуном.
   - ESSOS / PENTOS: Ілліріо Мопатіс готує Дейнеріс до оглядин Кхалом Дрого АБО параноя Візеріса наростає з кожним днем.
   - ESSOS / BRAAVOS або інші вільні міста: Тінь Залізного Трону довга. Вестеросські вигнанці і шпигуни — скрізь.
   - БУДЬ-ЯКА ІНША ЛОКАЦІЯ: Чутки про смерть Джона Аррена та виклик Еддарда Старка до Королівської Гавані ширяться по всіх Семи Королівствах — навіть найвіддаленіші замки відчувають наближення бурі.
3. CHARACTER INTEGRATION:
   - Якщо герой КАНОНІЧНИЙ: Починай точно зі сцени його першої появи в книзі.
   - Якщо герой НЕКАНОНІЧНИЙ (вигаданий): Зроби його свідком або дрібним учасником вищезгаданого canon hook.
4. AGENCY PRESERVATION:
   - НІКОЛИ не пиши дії за гравця.
   - Зупиняй сцену рівно в той момент, коли виникає напруга і потрібна реакція гравця.
</system_rules>

<thinking_directives>
Перед написанням прологу, подумай:
1. Sensory Anchor: Які 3 головні запахи/звуки цієї локації?
2. Canon Integration: Який hook підходить локації та як органічно вписати туди профіль героя?
3. Agency Check: В якій точці напруги текст має обірватися, щоб дати гравцю вибір?
4. Actions Draft: 4 принципово різні реакції гравця на цей момент (агресивна / соціальна / обережна / дика).
</thinking_directives>

<scene_constraint>
START_SCENE: {current_scene}

КРИТИЧНО: Твій narrative_text ПОВИНЕН описувати саме мікролокацію START_SCENE, а не іншу частину {current_location}.

Приклад: якщо START_SCENE = "Терасний сад вілли Ілліріо", ти описуєш ТЕРАСНИЙ САД (квіти жасмину, мармурові плити, вид на бухту), а НЕ ринок чи порт Пентоса.

Canon plot hook повинен органічно увійти ЧЕРЕЗ цю мікросцену: персонаж чує розмову слуг про подію, бачить кур'єра, що несе листа, помічає прапор далеко на горизонті. ЗАБОРОНЕНО переносити героя в іншу мікролокацію заради "більш видовищного" hook.
</scene_constraint>

<antiexamples>
WRONG: scene_check не збігається з START_SCENE або narrative описує іншу мікросцену:
START_SCENE: "Тронний Зал"
{{"scene_check": "Двір замку",
  "narrative_text": "Ви стоїте у дворі замку..."}}
Розсинхрон з профілем гравця. Engine фільтрує NPC за "Тронний Зал", а наратив описує двір.

CORRECT:
START_SCENE: "Тронний Зал"
{{"scene_check": "Тронний Зал",
  "narrative_text": "Ви ступаєте під арку Тронного Залу. Залізний Трон височіє над вами, його шипи..."}}
</antiexamples>

<output_requirements>
- ВИКЛЮЧНО валідний JSON. Жодного тексту поза фігурними дужками.
- Усі значення текстових полів — Українською мовою.
- НІКОЛИ не використовуй подвійні лапки (") всередині значень рядків. Замінюй їх на одинарні (').
- Чотири обов'язкових ключі: "scene_check", "narrative_text", "action_prompt", "suggested_actions".
- "scene_check" — дослівний повтор START_SCENE. Це CoT-якір: заповни його ПЕРЕД написанням narrative_text.
</output_requirements>

ВІДПОВІДАЙ СТРОГО У ФОРМАТІ JSON:
{{
  "scene_check": "<дослівний повтор START_SCENE — CoT-якір, що ти прив'язав narrative до цієї сцени>",
  "narrative_text": "Атмосферний пролог, 3 абзаци, Ukrainian, без чисел у тексті",
  "action_prompt": "Питання або ситуація що вимагає негайного вибору гравця (1-2 речення)",
  "suggested_actions": [
    {{"button": "Короткий label до 5 слів", "intent": "Розгорнутий намір від ПЕРШОЇ ОСОБИ, 10-15 слів, конкретні деталі"}},
    {{"button": "Короткий label до 5 слів", "intent": "Розгорнутий намір від ПЕРШОЇ ОСОБИ, 10-15 слів, конкретні деталі"}},
    {{"button": "Короткий label до 5 слів", "intent": "Розгорнутий намір від ПЕРШОЇ ОСОБИ, 10-15 слів, конкретні деталі"}},
    {{"button": "Короткий label до 5 слів", "intent": "Розгорнутий намір від ПЕРШОЇ ОСОБИ, 10-15 слів, конкретні деталі"}}
  ]
}}

<few_shot_example>
{{
  "scene_check": "Внутрішній двір Вінтерфелла",
  "narrative_text": "Ранковий холод Півночі пробирає до самих кісток, проникаючи крізь товстий вовняний плащ. Повітря важке — запах розтопленого снігу, кінського поту і вогкого каміння. Ви стоїте на внутрішньому дворі Вінтерфелла, де зібрався мовчазний натовп. У центрі, на дерев'яній плаxі, лежить змарнілий чоловік у чорному — дезертир з Нічної Варти, що незв'язно бурмоче про білих блукачів.\\n\\nЛорд Еддард Старк височіє над ним, його обличчя вирізьблене з сірого граніту. Він мовчки знімає важкий дворучний меч. Валірійська сталь 'Льоду' поглинає тьмяне світло. Тиша стає абсолютною, перериваючись лише різким карканням ворон.\\n\\nРаптом краєм ока ви помічаєте рух. Поруч хирлявий чоловік у брудному лахмітті непомітно тягнеться до кинджала під курткою, не зводячи погляду зі спини молодого Робба Старка. Сталь Еддарда злітає вгору.",
  "action_prompt": "Невідомий ось-ось вихопить зброю. Що ви зробите?",
  "suggested_actions": [
    {{"button": "Гукнути варту", "intent": "Я різко повертаюсь і кричу варті, вказуючи на підозрілого чоловіка в натовпі."}},
    {{"button": "Схопити його самому", "intent": "Я мовчки і швидко підходжу ззаду та перехоплюю руку зловмисника, не даючи вихопити кинджал."}},
    {{"button": "Відступити в натовп", "intent": "Я непомітно відсуваюсь подалі, розчиняючись у натовпі і спостерігаючи що буде далі."}},
    {{"button": "Стежити непомітно", "intent": "Я залишаюсь на місці, але фіксую обличчя підозрілого і готуюсь діяти якщо він справді нападе."}}
  ]
}}
</few_shot_example>
"""


def build_populate_npcs_prompt(location, situation_context, blacklist_str, scenes_block_str="") -> str:
    return f"""
        ROLE: Narrative Designer for Game of Thrones (Grimdark Fantasy).
        TASK: Populate the current location: "{location}" with 6-8 background NPCs.

        === CURRENT SITUATION (CRITICAL) ===
        {situation_context}

        === CRITICAL NAMING RULES (ANTI-CANON) ===
        1. BACKGROUND ONLY: You are generating nobodies, commoners, guards, or local minor merchants. DO NOT generate main characters from the books/show.
        2. NO GREAT HOUSES: It is STRICTLY FORBIDDEN to use these surnames: Stark, Lannister, Targaryen, Baratheon, Tyrell, Greyjoy, Martell, Arryn, Tully, Bolton, Mormont.
        3. BLACKLIST: Absolutely DO NOT use any of these specific names or variations of them: {blacklist_str}.
        4. LORE-FRIENDLY NAMES (CRITICAL): Generate original names that fit the Game of Thrones / ASOIAF universe ONLY.
           - Westerosi names: Alys, Brynden, Maegor, Cassana, Willem, Harren, Tytos, Jocelyn, Osmund, Ronnet
           - Essosi names: Illyrio, Syrio, Belwas, Talea, Qotho, Malazza
           - ABSOLUTELY FORBIDDEN: Modern real-world names (Софія, Микола, Олена, Дмитро, Ігор, Віра, etc.). These break immersion completely.
           - Test: If a name could belong to a person in modern Ukraine/Russia/Europe, it is WRONG. Use medieval fantasy names.

        INSTRUCTION:
        1. **Adapt to the Situation:** - If context says "War/Siege" -> Generate wounded soldiers, starving refugees, looting mercenaries.
           - If context says "Festival/Tourney" -> Generate drunk knights, pickpockets, singers.
           - If context says "Mourning" -> Generate silent sisters, crying servants, paranoid guards.
        2. **Diverse Cast:** Do not just make 10 guards. We need beggars, nobles, merchants, criminals.
        3. **Relationships:** Their "Goal" and "Secrets" must be tied to the Current Situation.

        REQUIREMENTS:
        - Create a DIVERSE mix (Social standing, professions, hostility).
        - "Secrets" must be interesting plot hooks, but not break canonical story.
        - Location field is set by the system — do NOT include it in output.
        - Scene: ВИКЛЮЧНО одне значення зі списку нижче (обирай найближче за характером NPC):
{scenes_block_str}
          НЕ вигадуй нових назв сцен. Враховуй категорію: торговця — в ПУБЛІЧНА ЗОНА, лорда — в ЕЛІТНА ЗОНА.

        === RELATION_PLAYER SCALE (ОБОВ'ЯЗКОВО) ===
        Поле Relation_Player ПОВИННО містити ТІЛЬКИ одне значення з цієї шкали (від найгіршого до найкращого):
        Смертельна ненависть | Кривавий ворог | Відкрита ворожість | Ворожий | Глибока підозра |
        Підозрілий | Холодний | Нейтральний | Обережно відкритий | Тепле ставлення |
        Прихильний | Дружній | Довіряє | Глибока довіра | Абсолютна довіра
        Фоновий/незнайомий NPC зазвичай починає з: Холодний, Нейтральний або Підозрілий.

        === LANGUAGE REQUIREMENT (CRITICAL) ===
        Responce should be in UKRAINIAN language only

        OUTPUT STRICTLY VALID JSON ARRAY ONLY. NO MARKDOWN. NO BACKTICKS. NO CODE FENCES. START WITH [ AND END WITH ]. NO TEXT BEFORE OR AFTER.
        [
          {{
            "Name": "Name",
            "Scene": "Мікролокація де зараз знаходиться NPC всередині {location}",
            "Description": "Atmospheric visual description",
            "Character": "Personality traits",
            "Goal": "Current desire",
            "Secrets": "Hidden info",
            "Relation_Player": "ТІЛЬКИ одне зі значень шкали: Смертельна ненависть / Кривавий ворог / Відкрита ворожість / Ворожий / Глибока підозра / Підозрілий / Холодний / Нейтральний / Обережно відкритий / Тепле ставлення / Прихильний / Дружній / Довіряє / Глибока довіра / Абсолютна довіра",
            "Memory_Anchor": "-",
            "Relation_NPCs": "Connection to local groups"
          }}
        ]
        """


def build_history_summary_prompt(history_text: str) -> str:
    return (
        f"Стисни цю RPG історію в КЛЮЧОВІ ФАКТИ (список, українською, максимум 8 пунктів).\n"
        f"Зосередься на: імена NPC та стосунки, обіцянки/домовленості, поточний квест, важливі отримані/втрачені предмети.\n"
        f"Історія:\n{history_text}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 4 — D&D 5e pipeline builders (NEW)
# ═══════════════════════════════════════════════════════════════════════════════
#
# DC enum {5,10,12,15,17,20,22,25,28,30}  (replaces 2d50 enum {50,60,70,80,100,120,140})
# 18 D&D skills → 6 abilities              (replaces 4 legacy skills)
# xp_award ∈ {0,25,50,100,200}
# combat_imminent: bool → engine switches to COMBAT_MODE
# condition_apply / condition_remove       (new mechanic via dnd_conditions)
#
# JSON consumers (NOT touched here — Phase 5 scope):
#   core/engine.py:resolve_normal_action   → reads all Worker NORMAL keys
#   core/engine.py:process_combat_turn     → reads combat round keys
#   core/ai_client.py:clean_and_parse_json → parses raw LLM output
# ═══════════════════════════════════════════════════════════════════════════════

_LEGAL_DCS_NORMAL = "5|10|12|15|17|20|22|25|28|30"
_LEGAL_ABILITIES = "STR|DEX|CON|INT|WIS|CHA|None"
_LEGAL_SKILLS_18 = (
    "Athletics|Acrobatics|Sleight of Hand|Stealth|"
    "Arcana|History|Investigation|Nature|Religion|"
    "Animal Handling|Insight|Medicine|Perception|Survival|"
    "Deception|Intimidation|Performance|Persuasion|None"
)
_LEGAL_XP = "0|25|50|100|200"


def build_normal_resolve_prompt(
    user_input: str,
    profile: dict,
    current_scene: str,
    npcs_in_scene: list[dict],
    last_turn_summary: str,
    current_location: str,
    npc_reputation_context: dict | None,
    clocks_info: dict | None,
    nearby_canonical_locs: list[str] | None = None,
    all_canonical_locs_grouped: str = "",
) -> str:
    """Worker NORMAL — D&D 5e variant (Phase 4+).

    Replaces build_resolve_mechanics_prompt for the NORMAL pipeline branch.
    Returns a prompt whose LLM output must match the output_schema block below.
    """
    profile_str = json.dumps(profile, ensure_ascii=False)
    clocks_str = json.dumps(clocks_info or {}, ensure_ascii=False)
    rep_str = json.dumps(npc_reputation_context or {}, ensure_ascii=False)
    last_turn_str = last_turn_summary or "Game start"
    scene_str = current_scene or "Unknown"
    loc_str = current_location or "Unknown"
    locs_nearby = ", ".join(f'"{l}"' for l in (nearby_canonical_locs or [])) or "none"

    # Format NPC list as JSON array so the model sees structured data, not free text
    npc_array_str = json.dumps(
        [{"name": n.get("Name", "?"), "hp_current": n.get("hp_current", "?"),
          "hp_max": n.get("hp_max", "?"), "ac": n.get("ac", "?"),
          "conditions": n.get("conditions", []),
          "relation": n.get("Relation_Player", "Нейтральний")}
         for n in (npcs_in_scene or [])],
        ensure_ascii=False, indent=2
    )

    # Extract ability scores and proficiency for inline guidance
    ab = profile.get("ability_scores", {})
    prof = profile.get("proficiency_bonus", 2)
    ab_line = " | ".join(f"{k}:{v}" for k, v in ab.items()) if ab else "not set"
    skill_profs = profile.get("skill_profs", [])
    conditions = profile.get("conditions", [])
    cond_line = ", ".join(c.get("name", str(c)) for c in conditions) if conditions else "none"

    return f"""<system>
You are the System Engine (Worker) for a Grimdark RPG set in Westeros/Essos (298 AC).
Your ONLY job: resolve the mechanical outcome of the player's action using D&D 5e rules adapted for ASoIaF.
System: 1d20 + ability_mod (+ proficiency_bonus if proficient) vs DC.
Output: STRICTLY VALID JSON matching <output_schema>. No markdown, no prose outside JSON.
</system>

<player_state>
Profile: {profile_str}
Ability scores: {ab_line}
Proficiency bonus: +{prof}
Skill proficiencies: {skill_profs}
Active conditions: {cond_line}
Current location: {loc_str}
Current scene: {scene_str}
</player_state>

<scene_data>
NPCs present (JSON array):
{npc_array_str}
NPC reputation context: {rep_str}
Active clocks: {clocks_str}
Last turn: {last_turn_str}
</scene_data>

<thinking_directives>
MANDATORY GATE CHECKLIST — answer every gate before writing JSON.
Write reasoning in "skill_check_reasoning", "difficulty_reasoning", "gold_reasoning".

[GATE 1 — FREE ACTION?]
Greeting, casual talk, examining object, drawing weapon without combat, waiting, moving within same scene?
→ YES → ability_used="None", skill_used="None", difficulty=5, combat_imminent=false. Jump to GATE 4.
→ NO  → GATE 2.

[GATE 2 — ABILITY + SKILL?]
Pick ABILITY: STR(lift/melee) DEX(stealth/ranged) CON(endure) INT(lore/investigate) WIS(sense/track/heal) CHA(persuade/deceive/intimidate).
Pick SKILL (optional, from <output_schema> list). RULE: skill_used≠"None" → ability_used must be non-None.
No real resistance → ability_used="None", skill_used="None", difficulty=5.

[GATE 3 — DC — STRICT ENUM {_LEGAL_DCS_NORMAL}]
5=trivial | 10=easy | 12=trivial+ | 15=medium | 17=hard | 20=very hard | 22=stretch | 25=near-impossible | 28=legendary | 30=god-tier.
Rep modifier: score≥60→-2DC; score≤-60→+5DC. Escalation: request→12-15; threat→17-20; assassination→25-28.

[GATE 4 — GOLD]
Gold physically left player? NO→gold_impact="none". YES voluntary→exact tag. YES involuntary→"-N".

[GATE 5 — MOVEMENT?]
Explicit move to different place? YES→non-"none" scene/location_impact. NO→both "none".

[GATE 6 — COMBAT IMMINENT?]
Physical attack initiated NOW? YES→combat_imminent=true. Verbal threat/drawing weapon→false.
RULE: words NEVER trigger combat_imminent=true.

[GATE 7 — TRAINING?]
Explicit TRAIN/PRACTICE/STUDY intent? YES→action_type="training". Incidental skill use→"standard".
</thinking_directives>

<antiexamples>
❌ difficulty=18 (18∉enum) → ✅ difficulty=17
❌ ability_used="None", skill_used="Athletics" → ✅ ability_used="STR", skill_used="Athletics"
❌ combat_imminent=true for verbal "Я кажу що вб'ю його" → ✅ false
</antiexamples>

<location_rules>
Nearby: {locs_nearby} | All by region: {all_canonical_locs_grouped}
location_impact: exact canonical name when player moves to a different canonical location; scene_impact: new micro-scene name for non-canonical sub-places; both "none" if player stays.
</location_rules>

<player_action>
"{user_input}"
</player_action>

<output_schema>
MANDATORY KEYS — all must be present, no extras required at top level:

action_type      : "standard" | "training" — GATE 7 verdict (training = explicit practice/study)
ability_used     : {_LEGAL_ABILITIES}
skill_used       : {_LEGAL_SKILLS_18}
difficulty       : one integer from {{{_LEGAL_DCS_NORMAL}}}
advantage_reason : string — WHY the player has advantage (empty string if none)
disadvantage_reason : string — WHY the player has disadvantage (empty string if none)
combat_imminent  : bool — true only if PHYSICAL attack initiated this turn
skill_check_reasoning : string ≥40 chars — GATE 1-2 walkthrough
difficulty_reasoning  : string ≥20 chars — GATE 3 walkthrough
gold_reasoning        : string ≥20 chars — GATE 4 walkthrough
verdict_text     : string — 1 sentence for GM context (Ukrainian)
xp_award         : one integer from {{{_LEGAL_XP}}}
reputation_reasoning  : string — 1 sentence: WHY this sign and magnitude (internal CoT, not shown to player)
reputation_delta : integer -3..+3. SIGN RULES (mandatory):
  • Action beneficial/pleasant for target NPC AND outcome=SUCCESS → +1 (minor gesture), +2 (significant aid), +3 (epic/self-sacrifice)
  • Action harmful/insulting for target NPC AND outcome=SUCCESS → -1 (minor slight), -2 (serious offence), -3 (grave harm/humiliation)
  • Action directed at NPC AND outcome=FAILURE → 0 or -1 (depending on nature of failure: clumsy = 0, offensive = -1)
  • Action NOT directed at any specific NPC → reputation_delta=0, reputation_target_npc=""
reputation_target_npc : string — exact NPC name or ""
updates (object):
  minutes_passed  : integer 1..600
  location_impact : "none" | exact canonical location name | "В дорозі"
  scene_impact    : "none" | descriptive scene name
  hp_damage_dice  : "none"|"1d4"|"1d6"|"1d8"|"2d6"|"2d8"|"fatal"
  hp_heal_dice    : "none"|"1d4"|"1d6"|"1d8"|"2d8"
  gold_impact     : "none"|"-N"|"+N"|"spend_small"|"spend_medium"|"spend_large"|"earn_small"|"earn_medium"|"earn_large"
  inventory_new   : array of strings
  inventory_lost  : array of strings
  clocks_impact   : object (e.g. {{"Scene_Tension": 1}} or {{"Scene_Tension": "clear"}})
  condition_apply : array of {{"name": string, "duration": int (rounds), "target": "player"|npc_name}}
  condition_remove: array of {{"name": string, "target": "player"|npc_name}}
</output_schema>

OUTPUT STRICTLY VALID JSON. NO MARKDOWN. NO BACKTICKS:
{{
    "skill_check_reasoning": "GATE 1: Is this a free action? [YES/NO]. GATE 2: Which ability? Which skill? Why?",
    "difficulty_reasoning": "GATE 3: Nature of action → which DC from enum and why?",
    "gold_reasoning": "GATE 4: Did gold physically leave possession? [YES/NO] → final value?",
    "action_type": "standard",
    "ability_used": "STR",
    "skill_used": "Athletics",
    "difficulty": 15,
    "advantage_reason": "",
    "disadvantage_reason": "",
    "combat_imminent": false,
    "verdict_text": "Гравець намагається дістатися до воріт через натовп.",
    "xp_award": 25,
    "reputation_reasoning": "Дія не спрямована на конкретного NPC, тому дельта = 0.",
    "reputation_delta": 0,
    "reputation_target_npc": "",
    "updates": {{
        "minutes_passed": 5,
        "location_impact": "none",
        "scene_impact": "none",
        "hp_damage_dice": "none",
        "hp_heal_dice": "none",
        "gold_impact": "none",
        "inventory_new": [],
        "inventory_lost": [],
        "clocks_impact": {{}},
        "condition_apply": [],
        "condition_remove": []
    }}
}}"""


def build_combat_round_prompt(
    user_input: str,
    profile: dict,
    combat_state_snapshot: dict,
) -> str:
    """Worker COMBAT — parse player's free-text action into a structured combat intent.

    Called once per round for the player's turn.
    Output JSON is consumed by dnd_combat.player_attack / dnd_combat.resolve_player_action (Phase 5).
    """
    snapshot_str = json.dumps(combat_state_snapshot, ensure_ascii=False, indent=2)
    profile_str = json.dumps(
        {k: profile.get(k) for k in
         ("Ім'я", "class", "level", "ability_scores", "proficiency_bonus",
          "hp_current", "hp_max", "ac", "skill_profs", "conditions", "equipment")},
        ensure_ascii=False
    )
    return f"""<system>
You are the Combat Parser for a D&D 5e ASoIaF RPG (Westeros, 298 AC).
Your ONLY job: translate the player's free-text action into a structured combat intent JSON.
The engine will execute the action mechanically. You classify intent, target, weapon, and tactic.
Output: STRICTLY VALID JSON matching <output_schema>. No markdown, no prose outside JSON.
</system>

<player_profile>
{profile_str}
</player_profile>

<combat_state>
{snapshot_str}
</combat_state>

<player_action>
"{user_input}"
</player_action>

<classification_rules>
INTENT values and when to use them:
  attack  — player swings, shoots, stabs, punches, charges a target
  cast    — player uses a heritage trait with a magical/special effect
  move    — player repositions (change distance: melee→near→far or vice versa)
  dodge   — player focuses on defence; no attack this round (+2 AC, -2 to attack rolls)
  flee    — player attempts to disengage and escape combat entirely
  item    — player uses an item from inventory (potion, torch, rope)
  help    — player assists an ally's next roll (gives that ally advantage)
  grapple — player attempts to grab/pin a target (Athletics vs Athletics/Acrobatics)
  shove   — player knocks a target prone or pushes them back (Athletics vs Athletics/Acrobatics)

TACTIC values:
  reckless — all-in: player has ADVANTAGE on attack, but ENEMIES have advantage vs. player this round
  normal   — balanced approach
  cautious — careful: -2 to attack roll, but +2 to AC this round

TARGET: must be EXACTLY one name from combat_state.npcs[].name, or null for non-targeted intents.
WEAPON: must match one of combat_state.weapons[], or null.
SPELL_OR_ABILITY: must match one of combat_state.heritage_traits[] names, or null.

MOVE_TO: if intent="move", specify target npc name to engage (close distance) or "far" to disengage.
</classification_rules>

<output_schema>
intent           : "attack"|"cast"|"move"|"dodge"|"flee"|"item"|"help"|"grapple"|"shove"
target_npc       : string (exact name from npcs list) | null
weapon           : string (from weapons list) | null
spell_or_ability : string (from heritage_traits list) | null
tactic           : "reckless"|"normal"|"cautious"
move_to          : string | null
verdict_text     : string — 1 sentence Ukrainian describing the intent
reasoning        : string — why this classification
</output_schema>

OUTPUT STRICTLY VALID JSON:
{{
    "intent": "attack",
    "target_npc": null,
    "weapon": null,
    "spell_or_ability": null,
    "tactic": "normal",
    "move_to": null,
    "verdict_text": "Гравець атакує найближчого ворога.",
    "reasoning": "Action contains attack verb; weapon inferred from equipment."
}}"""


def build_npc_combat_action_prompt(
    combat_state_snapshot: dict,
    npc_dict_list: list[dict],
) -> str:
    """Light call: decide actions for 1-2 spotlight NPCs in a combat round.

    Batched: one call decides for all NPCs in npc_dict_list (usually 1-2).
    Output is JSON array under key "actions".
    Consumed by dnd_combat.npc_decide_action (Phase 5).
    """
    snapshot_str = json.dumps(combat_state_snapshot, ensure_ascii=False, indent=2)
    npcs_str = json.dumps(npc_dict_list, ensure_ascii=False, indent=2)
    return f"""<system>
You are the NPC Combat AI for a D&D 5e ASoIaF RPG.
Decide the combat action for each NPC in <spotlight_npcs>.
Each NPC acts tactically based on its stats, conditions, and the battlefield situation.
Output: STRICTLY VALID JSON. No markdown, no prose outside JSON.
</system>

<combat_state>
{snapshot_str}
</combat_state>

<spotlight_npcs>
{npcs_str}
</spotlight_npcs>

<tactical_guidance>
- If NPC hp_current < 25% hp_max AND flee is plausible → prefer "flee"
- If NPC is melee-focused and player is in "melee" range → "attack" player
- If NPC has ranged weapon and player is "far" → "attack" player
- If NPC is unconscious or has condition "stunned" or "paralyzed" → action must be "none" (skip)
- "help" is used when an ally is adjacent and struggling; it gives that ally advantage
- "cast" only if NPC has a special ability listed in its attacks[] with a non-null range and magical tag
- "dodge" if the NPC is heavily wounded and cannot safely flee
- Prioritise attacking the player unless an allied NPC is at 0 HP and needs "help"
</tactical_guidance>

<output_schema>
Return a JSON object with one key "actions" containing an array.
Each element:
  npc_name : string — exact name from spotlight_npcs
  action   : "attack"|"dodge"|"flee"|"help"|"cast"|"none"
  target   : "player" | other npc name
  weapon   : string (from npc attacks[0].name) | null
  reason   : string ≤30 chars — brief tactical reason
</output_schema>

OUTPUT STRICTLY VALID JSON:
{{
    "actions": [
        {{
            "npc_name": "Ім'я NPC",
            "action": "attack",
            "target": "player",
            "weapon": null,
            "reason": "Player in melee range, full HP."
        }}
    ]
}}"""


def build_npc_regen_prompt(npc_card: dict) -> str:
    """Phase 6 — regenerate D&D statblock for a single canonical NPC.

    Input: lore card dict with frozen fields (Name/Description/Character/Goal/Secrets/Status).
    Output: D&D statblock JSON consumed by dnd_migration.regenerate_canon_npc_via_llm.

    CR evheuristics (per plan):
      CR 0   : peasant / smallfolk
      CR 1/8 : bandit / servant
      CR 1/4 : guard / retainer
      CR 1   : hedge knight / household knight
      CR 3   : experienced knight / sellsword captain
      CR 5   : lord-captain / maester of council / small-council member
      CR 7+  : Great Lord / Hand of the King
      CR 8-9 : The Mountain (Gregor Clegane) / Khal Drogo
    """
    card_str = json.dumps(npc_card, ensure_ascii=False, indent=2)
    return f"""<system>
You are a D&D 5e statblock designer for an ASoIaF RPG (Westeros/Essos, 298 AC).
Generate a mechanically balanced D&D statblock for the canonical character below.
Base all decisions on the character's lore role, not on generic fantasy tropes.
Output: STRICTLY VALID JSON matching <output_schema>. No markdown, no prose outside JSON.
</system>

<npc_lore_card>
{card_str}
</npc_lore_card>

<cr_guidelines>
CR is determined by the character's LORE ROLE and combat capability, not their title alone.
Political weight (small-council member, Hand) counts as CR 5+ even if physically weak.

CR tiers:
  "0"   — peasant, smallfolk, stable boy
  "1/8" — bandit, servant, minor hireling
  "1/4" — city guard, household retainer
  "1/2" — seasoned soldier, junior maester
  "1"   — hedge knight, household knight
  "2"   — experienced knight, ship captain
  "3"   — sellsword captain, knight of the Kingsguard (junior)
  "4"   — knight-banneret, master-at-arms
  "5"   — lord-captain, maester of a major castle, small-council member
  "6"   — high lord's champion, Lord Commander of a lesser watch
  "7"   — Great Lord (mid-tier), Hand of the King (political weight)
  "8"   — elite warrior lord (e.g. Jaime Lannister in his prime)
  "9"   — Gregor Clegane, Khal Drogo
  "10"  — legendary figure of unique power

HP formula: For humanoids use (hit_die_avg + CON_mod) × level_estimate.
ability_scores: 3-18 range. STR 18+ only for "The Mountain" tier.
attacks: at least 1 entry. Include melee for combatants; use Crossbow for ranged-capable NPCs.
saves: only proficient saves (2-3 for most). CR 5+ NPCs usually have WIS/CHA or STR/CON.
skills: only skills the character would realistically be proficient in from their lore.
</cr_guidelines>

<output_schema>
cr              : one of "0"|"1/8"|"1/4"|"1/2"|"1"|"2"|"3"|"4"|"5"|"6"|"7"|"8"|"9"|"10"
ability_scores  : object {{STR, DEX, CON, INT, WIS, CHA}} — each integer 3-18
hp_max          : integer
ac              : integer
speed           : integer (feet, standard 30)
attacks         : array of {{name: string, to_hit: int, dmg: string (dice notation), range: int|null}}
saves           : object — only proficient saves e.g. {{"STR": 4, "CON": 3}}
skills          : object — only proficient skills e.g. {{"Insight": 4, "Persuasion": 4}}
conditions      : array (empty for newly generated NPC)
tags            : array of strings e.g. ["humanoid","noble","westerosi"]
reasoning       : string ≥40 chars — why this CR, based on lore role
</output_schema>

OUTPUT STRICTLY VALID JSON:
{{
    "cr": "1",
    "ability_scores": {{"STR": 13, "DEX": 11, "CON": 12, "INT": 10, "WIS": 10, "CHA": 9}},
    "hp_max": 11,
    "ac": 14,
    "speed": 30,
    "attacks": [{{"name": "Longsword", "to_hit": 3, "dmg": "1d8+1 slashing", "range": null}}],
    "saves": {{"STR": 3, "CON": 3}},
    "skills": {{"Athletics": 3, "Intimidation": 1}},
    "conditions": [],
    "tags": ["humanoid", "knight", "westerosi"],
    "reasoning": "Household knight: trained combatant but no exceptional feats in lore → CR 1."
}}"""


# ── Updated GM_Logic — mode-aware (Phase 4) ───────────────────────────────────

def build_gm_logic_prompt(
    hero_name: str,
    hero_house: str,
    profile_json: str,
    context_knowledge: str,
    event_injection: str,
    burst_injection: str,
    current_time_str: str,
    curr_region: str,
    curr_loc: str,
    is_traveling: bool,
    loc_hint: str,
    curr_scene: str,
    valid_locs_str: str,
    valid_regions_str: str,
    region_locs_str: str,
    npc_context_text: str,
    tension_label: str,
    mechanics_verdict: str,
    impact_narrative_hints: str,
    history_text: str,
    user_input: str,
    action_slots: list[str],
    puppet_mode: bool = False,
    absent_npcs: list[str] | None = None,
    dead_npcs: list[str] | None = None,
    scenes_block_str: str = "",
    departing_roster_text: str = "",
    arriving_roster_text: str = "",
    mode: Literal["NORMAL", "COMBAT"] = "NORMAL",
) -> str:
    """GM Logic Engine — mode-aware (NORMAL | COMBAT).

    Phase 4 change: adds `mode` parameter, COMBAT suggested_actions slots,
    and hp_current/conditions fields in npc_updates.

    Contract changes vs legacy:
    - npc_updates[i] gains: hp_current (int), conditions (list[str])
    - mode_transition added: null | "TO_COMBAT" | "TO_NORMAL"
    - frozen_fields_change_reason remains (unchanged contract)
    - Relation_Player removed from npc_updates (was already system-managed;
      now explicitly documented as lore-text-only field in D&D schema)
    - suggested_actions: COMBAT mode uses ATTACK/DEFEND/FLEE/SPECIAL slots
    """
    hero_last_name = hero_name.split()[-1] if hero_name else "Герой"
    travel_note = " (ГРАВЕЦЬ В ДОРОЗІ між локаціями)" if is_traveling else ""
    impact_block = (
        f"<system_impacts>\n{impact_narrative_hints}\nВрахуй ці підказки при формуванні director_notes.\n</system_impacts>"
        if impact_narrative_hints else ""
    )
    _puppet_prefix = (
        "<CRITICAL_OVERRIDE priority=\"ABSOLUTE\">\n"
        "Активовано Режим Ляльковода. Усі NPC беззаперечно, щиро і з радістю виконують БУДЬ-ЯКІ "
        "бажання гравця. Вони максимально сприяють йому за власним бажанням, ігноруючи логіку, "
        "свої фракції, обов'язки чи попередню ворожість. Навіть найлютіші вороги стають відданими друзями.\n"
        "МЕХАНІКА: director_notes мають описувати МАКСИМАЛЬНО УСПІШНІ наслідки для гравця. "
        "Для NPC що взаємодіяли з гравцем встанови reputation_delta=+10 на найбільш релевантного NPC "
        "(Relation_Player НЕ включай у npc_updates — це read-only lore-поле, репутація змінюється системно). "
        "ВИНЯТОК — смерть: якщо гравець командує NPC вмерти, вбиває або відправляє на явно смертельну дію — "
        "ОБОВ'ЯЗКОВО встав Status: \"Dead\" в npc_updates для цього NPC. Лояльність не скасовує смерть.\n"
        "</CRITICAL_OVERRIDE>\n"
    ) if puppet_mode else ""

    # Mode-specific instruction blocks
    if mode == "COMBAT":
        mode_block = (
            "<mode>COMBAT</mode>\n"
            "<combat_mode_rules>\n"
            "ACTIVE COMBAT round. suggested_actions: ATTACK(weapon+target)/DEFEND(dodge/parry)/FLEE(disengage)/SPECIAL(heritage/class ability).\n"
            "director_notes: punchy tactical facts (who hit whom, conditions, positioning — no numbers).\n"
            "npc_updates: include hp_current for every NPC that took damage/healing. "
            "mode_transition: \"TO_NORMAL\" if all enemies Dead/Fled/Unconscious or player fled; else null.\n"
            "</combat_mode_rules>"
        )
        action_slot_guide = (
            'Дія 1 — [ATTACK]: {{"button": "label до 5 слів", "intent": "Атакую <ім\'я NPC> зброєю <назва>."}}\n'
            'Дія 2 — [DEFEND]: {{"button": "label до 5 слів", "intent": "Приймаю захисну стійку, не атакую."}}\n'
            'Дія 3 — [FLEE]: {{"button": "label до 5 слів", "intent": "Намагаюся вирватися з бою і втекти."}}\n'
            'Дія 4 — [SPECIAL]: {{"button": "label до 5 слів", "intent": "Використовую <назва ability> проти <ціль>."}}'
        )
    else:
        mode_block = "<mode>NORMAL</mode>"
        action_slot_guide = (
            f'Дія 1 — тип [{action_slots[0]}]: {{"button": "короткий label до 5 слів", "intent": "розгорнутий намір від ПЕРШОЇ ОСОБИ, 10-15 слів"}}\n'
            f'Дія 2 — тип [{action_slots[1]}]: аналогічно\n'
            f'Дія 3 — тип [{action_slots[2]}]: аналогічно\n'
            f'Дія 4 — тип [{action_slots[3]}]: аналогічно'
        )

    return f"""{_puppet_prefix}{mode_block}
<system>
Роль: Логічний Рушій Гри (Game Logic Engine) для Grimdark RPG (Гра Престолів).
Мета: Визначити наслідки дії гравця для стану світу, СТРОГО дотримуючись механічного вердикту.
Ти видаєш ВИКЛЮЧНО структуровані дані (JSON). Ти НЕ пишеш художній текст.
</system>

<thinking_directives>
1. Що сталося за механікою? Як реагує кожен NPC з ростеру (hp, conditions, локація, інвентар)?
2. Кожен NPC в npc_updates — фізично присутній у сцені?
3. companion_npcs: тільки якщо гравець ЯВНО назвав NPC для подорожі, інакше [].
4. mode_transition: бій завершено→"TO_NORMAL"; виник бій→"TO_COMBAT"; інакше→null.
5. NPC отримав пошкодження → ОБОВ'ЯЗКОВО вкажи hp_current у npc_updates.
</thinking_directives>

<player_identity>
ГЕРОЙ: {hero_name} з дому {hero_house}.
АБСОЛЮТНЕ ПРАВИЛО: NPC звертаються до героя ТІЛЬКИ як "{hero_last_name}" або "лорд/леді {hero_house}".
</player_identity>

<player_state>
{profile_json}
</player_state>

<world_context>
{GAME_ERA_CONTEXT}
{context_knowledge}
{event_injection}
{burst_injection}
</world_context>

<scene_state>
ПОТОЧНИЙ ЧАС: {current_time_str}
ПОТОЧНИЙ РЕГІОН: {curr_region}
ПОТОЧНЕ МІСТО: {curr_loc}{travel_note}{loc_hint}
ПОТОЧНА СЦЕНА: {curr_scene}
СЦЕНИ ТА NPC-ПУЛИ ДЛЯ ЛОКАЦІЇ "{curr_loc}":
{scenes_block_str}
ПРАВИЛО ПЕРЕМІЩЕННЯ: конкретне місто → одне з {valid_locs_str} | в дорозі → "В дорозі" | без зміни → "none"
КАНОНІЧНІ РЕГІОНИ: {valid_regions_str}
{_build_npc_roster_block(npc_context_text, curr_scene, departing_roster_text, arriving_roster_text)}
АТМОСФЕРА СЦЕНИ: {tension_label}
</scene_state>

<mechanical_verdict>
{mechanics_verdict}
Якщо FAILURE: результат болісний або фрустраційний.
Якщо SUCCESS: результат тріумфальний.
</mechanical_verdict>
{impact_block}

<reputation_behavior_rules>
≥60: допомагає проактивно | 20-59: стандарт | -19..19: підозрілий | -20..-59: мінімум/відмова | ≤-60: ніколи добровільно.
</reputation_behavior_rules>

<field_mutation_rules>
ЗАМОРОЖЕНІ поля (Description|Character|Goal|Secrets): ВІДСУТНІ в npc_updates за замовчуванням.
"frozen_fields_change_reason": "" завжди присутній.
Виняток — незворотна епічна подія (каліцтво/травма/розкрита таємниця): frozen_fields_change_reason ≥20 символів.
Relation_Player — НІКОЛИ не включати в npc_updates (системне поле).
</field_mutation_rules>

<antiexamples>
❌ Description у npc_updates без незворотної події → не включати взагалі.
❌ Relation_Player у npc_updates → ніколи.
❌ Status:"Unconscious" без hp_current → ✅ додати hp_current:0, conditions:["unconscious"].
❌ WRONG: suggested_actions з не-українським текстом:
[
  {{"button": "억지 a polite request", "intent": "I politely request..."}},
  {{"button": "Bow & ask", "intent": "Я кланяюся і питаю..."}}
]
✅ CORRECT: ВСЕ виключно українською кирилицею:
[
  {{"button": "Ввічливо попросити", "intent": "Я ввічливо прошу пропустити мене у тронну залу"}},
  {{"button": "Поклонитись і спитати", "intent": "Я кланяюся і питаю про новини зі столиці"}}
]
</antiexamples>

<golden_laws_of_agency>
1. НЕ ЧІПАЙ ГРАВЦЯ — тільки NPC та фізика світу.
2. Гравець описує НАМІР, ти визначаєш РЕЗУЛЬТАТ.
3. Scene/Location NPC змінюється лише якщо він ЯВНО названий або висловив намір іти.
4. Гравець переміщується з NPC → заповни companion_npcs точними іменами.
5. NPC в приватному просторі → Scene = поточна сцена гравця.
</golden_laws_of_agency>

<history>
{history_text}
</history>

<current_turn>
ДІЯ ГРАВЦЯ: "{user_input}"
</current_turn>

<economy_rules>
ТРАНЗАКЦІЯ ВВАЖАЄТЬСЯ ЗАВЕРШЕНОЮ тільки якщо NPC прийняв оплату І гравець отримав товар/послугу.
Якщо NPC відмовився — gold не змінюється (Worker вже виставив gold_impact="none").
Ринковий торговець: одноразова покупка MAX 150 золотих.
Заможний купець/перекупник: MAX 800 золотих за один предмет.
Торговець НІКОЛИ не погоджується відразу на ціну гравця — перша відповідь завжди контрпропозиція.
</economy_rules>

{f'''<dead_characters>
МЕРТВІ ПЕРСОНАЖІ — РЕЖИМ АБСОЛЮТНОЇ ТИШІ:
{chr(10).join(f"    - {n}" for n in dead_npcs)}
АБСОЛЮТНА ЗАБОРОНА: згадувати їх у director_notes, npc_updates або будь-де.
</dead_characters>''' if dead_npcs else ''}

{f'''<absent_npcs>
ПЕРСОНАЖІ ЩО ЗАЛИШИЛИ СЦЕНУ (живі, але фізично відсутні):
{chr(10).join(f"    - {n}" for n in absent_npcs)}
ЗАБОРОНЕНО: включати їх у npc_updates або описувати їхні дії як присутніх.
</absent_npcs>''' if absent_npcs else ''}

<json_generation_rules>
1. director_notes: 3-7 фактичних речень (COMBAT: 4-6 тактичних). БЕЗ літературних прикрас.
2. NPC з пошкодженнями/лікуванням: hp_current (int) + conditions обов'язкові.
3. '' = поле не змінилось; нове значення = реальна зміна.
4. suggested_actions — рівно 4: {action_slot_guide}
LANGUAGE INVARIANT для suggested_actions (АБСОЛЮТНА ВИМОГА):
  - button: МАКСИМУМ 5 слів, ВИКЛЮЧНО українською (кирилиця).
    ЗАБОРОНЕНО: англійські слова, корейські/китайські/японські символи, латинські букви.
    Якщо назва дії англійська (наприклад "polite request") → перекласти ("Ввічливо попросити").
  - intent: 10-15 слів, ВИКЛЮЧНО українською (від першої особи "Я ...").
    ЗАБОРОНЕНО будь-які не-кирилиці окрім розділових знаків (.,!?–"') та цифр.
5. Location: ВИКЛЮЧНО зі списку: {region_locs_str} (Region не включати — система визначає).
6. Нові NPC: НІКОЛИ без першого імені.
</json_generation_rules>

ВІДПОВІДАЙ СТРОГО У ФОРМАТІ JSON:
{{
    "reasoning": "Коротке внутрішнє міркування: що сталося за механікою, як реагує світ і кожен NPC?",
    "npc_reasoning": "Для КОЖНОГО NPC з ростеру: що змінилось (hp, conditions, ставлення, локація)?",
    "frozen_fields_change_reason": "",
    "mode_transition": null,
    "director_notes": [
        "Факт 1: результат дії",
        "Факт 2: реакція NPC",
        "Факт 3: зміна середовища або стану"
    ],
    "companion_npcs": [],
    "npc_updates": [
        {{
            "Name": "<ТОЧНЕ ім'я з ростеру>",
            "Location": "",
            "Scene": "",
            "Memory_Anchor": "",
            "Relation_NPCs": "",
            "Inventory": "",
            "Status": "Active",
            "hp_current": 14,
            "conditions": []
        }}
    ],
    "suggested_actions": [{{"button": "Текст кнопки", "intent": "Розгорнутий намір від першої особи"}}, ...]
}}"""


# ── Updated Narrator — combat narrative block (Phase 4) ───────────────────────

def build_narrator_prompt(
    user_input: str,
    director_notes: list[str],
    npc_context_text: str,
    player_name: str,
    player_house: str,
    current_scene: str,
    current_location: str,
    impact_narrative_hints: str,
    puppet_mode: bool = False,
    recent_history_text: str | None = None,
    erotic_mode: bool = False,
    active_roster: list[str] | None = None,
    dead_npcs: list[str] | None = None,
    departing_roster_text: str = "",
    arriving_roster_text: str = "",
    scene_continuity_block: str = "",
    combat_log: list[str] | None = None,
) -> str:
    """Narrator — Phase 4 variant adds optional combat_log parameter.

    If combat_log is provided: switches to punchy 4-6 sentence combat narrative style.
    If combat_log is None: legacy atmospheric style (150-250 words).
    All other parameters identical to legacy builder.
    """
    last_name = player_name.split()[-1] if player_name else "Герой"
    notes_text = "\n".join(f"- {note}" for note in director_notes)
    _puppet_block = (
        "\n<CRITICAL_OVERRIDE priority=\"ABSOLUTE\">\n"
        "Режим Ляльковода активний. Пиши сцену де всі NPC захоплено і щиро допомагають герою. "
        "Атмосфера — тріумф і ейфорія перемоги, навіть якщо механічно це звичайна ситуація.\n"
        "</CRITICAL_OVERRIDE>\n"
    ) if puppet_mode else ""
    _erotic_block = (
        "\n<EROTIC_MODE priority=\"ABSOLUTE\">\n"
        "Еротичний режим увімкнено. Якщо сцена містить будь-яку сексуальну або інтимну взаємодію — "
        "описуй її ЯВНО і ДЕТАЛЬНО. Ніяких 'вони злилися в обіймах' або 'двері зачинились'. "
        "Пиши тіло, дотик, бажання, дію прямо — у стилі жорсткої прози Мартіна. "
        "Без метафор замість дій. Без fade to black. Без пом'якшень.\n"
        "ПРИВАТНІСТЬ СЦЕНИ: Перед початком інтимної взаємодії ОБОВ'ЯЗКОВО встанови приватність. "
        "Якщо є треті особи — вони МУСЯТЬ піти або бути відіслані до початку інтимної дії.\n"
        "</EROTIC_MODE>\n"
    ) if erotic_mode else ""

    # Combat narrative block — injected when combat_log is provided
    _combat_block = ""
    if combat_log is not None:
        log_text = "\n".join(combat_log)
        _combat_block = f"""
<combat_log>
{log_text}
</combat_log>

<combat_narrative_style>
COMBAT MODE ACTIVE. Override default atmospheric style:
- Write 4-6 SHORT, PUNCHY sentences. Each sentence = one beat of the round.
- Action verbs only: slash, parry, stagger, crash, gasp, lunge, dodge, collapse.
- Sensory detail: blood, steel on stone, breath, sweat, the crack of bone.
- NO lyrical metaphors, NO flowery prose, NO inner monologue.
- Convey the RHYTHM of one 6-second round — fast, brutal, visceral.
- End on a cliffhanger: enemy still standing, blood on the floor, something changed.
- Total length: 4-6 sentences (80-120 words). Shorter than normal mode.
</combat_narrative_style>"""

    parts = [NARRATOR_SYSTEM_PROMPT + _puppet_block + _erotic_block + _combat_block]
    parts.append(f"""
<player_identity>
ГЕРОЙ: {player_name} з дому {player_house}.
NPC звертаються до героя ТІЛЬКИ як "{last_name}" або "лорд/леді {player_house}".
ЗАБОРОНА: НІКОЛИ не називай героя прізвищем іншого дому.
</player_identity>""")
    if recent_history_text:
        parts.append(f"""
<recent_history>
КОНТЕКСТ ПОПЕРЕДНІХ ХОДІВ (лише для розуміння ситуації — не повторюй):
{recent_history_text}
</recent_history>""")
    if dead_npcs:
        dead_list = "\n".join(f"- {name}" for name in dead_npcs)
        parts.append(f"""
<dead_characters priority="ABSOLUTE_OVERRIDE">
ВБИТІ / НЕЗВОРОТНО МЕРТВІ:
{dead_list}
НАЗАВЖДИ ЗАБОРОНЕНО: описувати їхні дії, слова, погляди у теперішньому часі.
</dead_characters>""")
    if active_roster is not None:
        if departing_roster_text or arriving_roster_text:
            _dual_parts = []
            if departing_roster_text:
                _dual_parts.append(
                    "<departing_roster>\n"
                    "NPC ЛОКАЦІЇ ВІДПРАВЛЕННЯ (сцена яку гравець ПОКИДАЄ):\n"
                    f"{departing_roster_text}\n"
                    "Правило: Опиши їхню реакцію на відхід гравця.\n"
                    "</departing_roster>"
                )
            if arriving_roster_text:
                _dual_parts.append(
                    "<arriving_roster>\n"
                    "NPC НОВОЇ ЛОКАЦІЇ (сцена куди гравець ПРИБУВАЄ):\n"
                    f"{arriving_roster_text}\n"
                    "Правило: Опиши зустріч гравця з цими NPC.\n"
                    "</arriving_roster>"
                )
            parts.append("\n".join(_dual_parts))
        else:
            roster_list = "\n".join(f"- {name}" for name in active_roster) if active_roster else "— (у сцені нікого немає)"
            parts.append(f"""
<active_roster>
АКТИВНИЙ РОСТЕР СЦЕНИ (ЗАКРИТИЙ СПИСОК):
{roster_list}
АБСОЛЮТНЕ ПРАВИЛО: Якщо ім'я NPC НЕ в цьому списку — його НЕ ІСНУЄ в поточній сцені.
ЗАБОРОНЕНО: описувати дії, реакції або присутність такого NPC.
</active_roster>""")
    if npc_context_text:
        parts.append(f"""
<npc_cards>
{npc_context_text}
</npc_cards>""")
    if scene_continuity_block:
        parts.append(f"\n{scene_continuity_block}")
    parts.append(f"""
<director_notes>
ФАКТИ (ДОТРИМУЙСЯ СТРОГО — не вигадуй нічого поза цим списком):
{notes_text}
</director_notes>""")
    if impact_narrative_hints:
        parts.append(f"""
<system_impacts>
{impact_narrative_hints}
Вплети ці підказки в наратив БЕЗ чисел.
</system_impacts>""")
    parts.append(f"""
<scene>
Локація: {current_location}, Сцена: {current_scene}
</scene>

<player_action>
"{user_input}"
</player_action>

Напиши художній наративний текст за фактами з director_notes. Закінчи запрошенням до дії.""")
    return "\n".join(parts)


# ── Updated Initial Stats — D&D character creation (Phase 4) ──────────────────

def build_initial_stats_prompt(
    char_name: str,
    house_name: str,
    origin_region: str,
    valid_locations_str: str,
    scenes_block_str: str = "",
) -> str:
    """Character creation — D&D 5e ASoIaF variant (Phase 4+).

    LLM returns narrative shell + suggestions.
    Python computes hp_max, ac, proficiency_bonus, skill_profs, equipment
    via dnd_classes.build_class_starting_kit (Phase 5).

    Key change vs legacy: replaces 2d50 stat fields (Бойові/Військові/Інтрига/Управління/Здоров'я/Енергія)
    with D&D ability_scores + class + heritage.
    """
    return f"""
<role>
Ти — Архімейстер Цитаделі та провідний Game Balance Designer для RPG "Game of Thrones" (D&D 5e ASoIaF адаптація).
Твоя задача: визначити клас, спадщину, базові характеристики та наративний вступ для нового персонажа.
Python-рушій окремо обчислить: hp_max, ac, proficiency_bonus, skill_profs, equipment, features.
Ти надаєш ТІЛЬКИ те, що в <output_schema>.
</role>

<execution_mode>
ТИ ВИКОНУЄШ ЦЕЙ ПРОМПТ ЯК ПРОГРАМУ, КРОК ЗА КРОКОМ.
</execution_mode>

<input_data>
TIME CONTEXT: {GAME_ERA_CONTEXT}
CHARACTER: {char_name}
HOUSE: {house_name} (Origin: {origin_region})
</input_data>

<system_rules>
1. LOCATION RULES (CRITICAL — 3 рівні):
   - "Поточне місцезнаходження" (місто/замок): ВИКЛЮЧНО зі списку:
     {valid_locations_str}
     НЕ писати назву регіону — тільки конкретний населений пункт.
   - "Поточна сцена" (мікролокація): ВИКЛЮЧНО зі списку готових сцен:
{scenes_block_str}
   - "Регіон" НЕ включати — визначається системою автоматично.
   - ВИБІР ЛОКАЦІЇ за каноном першої книги (298 CE), не за регіоном походження.
2. PERMISSION TO FAIL: Якщо {char_name} неканонічний/вигаданий — не галюцинуй.
   Скажи "Персонаж не канонічний" у thought_process та згенеруй стандартний профіль для {origin_region}.
3. ABILITY SCORES — point-buy standard array (8-15 до heritage bonuses):
   Distribute 72 points across 6 abilities. Each score 8-15.
   Heritage bonuses applied SEPARATELY by Python — do NOT pre-add them here.
4. CLASS SELECTION — ТІЛЬКИ з 9 GoT-класів:
   Knight | Hedge Knight | Maester | Septon | Sellsword | Spy | Courtier | Bastard | Wildling
   Вибирай на основі ролі персонажа в лорі та його статусу в {origin_region}.
5. HERITAGE SELECTION — ТІЛЬКИ з 6 спадщин:
   Westerosi (Andal) | Valyrian Descent | First Men (Stark line) | Free Folk | Red Priest | Ironborn
   Вибирай на основі походження та культури персонажа.
6. BACKGROUND (вільний текст D&D-стилю): Soldier / Spy / Smuggler / Noble / Acolyte / Sailor / Criminal тощо.
</system_rules>

<thought_algorithm>
У "thought_process": 1) Канонічний статус {char_name} у 298р + локація зі списку. 2) Клас і спадщина — чому? 3) Ability scores 8-15, сума ~72. 4) Adversarial: stats надто сильні? локація є в списку? 5) Синтез.
</thought_algorithm>

<output_requirements>
- Відповідь ВИКЛЮЧНО валідний JSON-об'єкт. Жодного тексту поза фігурними дужками.
- Використовуй тільки одинарні лапки (') всередині текстових значень.
- Першим ключем ЗАВЖДИ "thought_process".
</output_requirements>

<antiexamples>
❌ suggested_class="Fighter" → ✅ "Knight" (тільки 9 GoT-класів)
❌ STR=18 до heritage bonus → ✅ max 15 (point-buy 8-15)
❌ Поточне місцезнаходження="Північ" (регіон) → ✅ "Вінтерфелл" (місто)
</antiexamples>

<few_shot_example>
{{
    "thought_process": "Branch 1: Джорах Мормонт у 298 році — вигнанець у Пентосі, вілла Ілліріо. Зі списку локацій: 'Квартал Магістрів'. Branch 2: Клас Knight (досвідчений воїн, колишній лорд). Heritage: Westerosi (Andal) — нормани Вестеросу. Background: Soldier. Branch 3: STR/CON пріоритет для Knight. Stats: STR 15, DEX 10, CON 14, INT 10, WIS 12, CHA 11 (сума 72). Adversarial: 'Чи занадто сильний?' — ні, mid-tier knight. Локація є в списку. Синтез: OK.",
    "narrative_intro": "Джорах Мормонт — вигнанець, чия честь розтрощена, але меч не заіржавів...",
    "Ім'я": "Джорах Мормонт",
    "Дім": "Мормонт",
    "suggested_class": "Knight",
    "suggested_heritage": "Westerosi (Andal)",
    "background": "Soldier",
    "ability_scores": {{"STR": 15, "DEX": 10, "CON": 14, "INT": 10, "WIS": 12, "CHA": 11}},
    "personality_traits": ["Відданий до кінця", "Не говорить зайвого"],
    "bond": "Відновити честь і повернутись до Ведмежого Острова",
    "flaw": "Сліпа відданість може стати слабкістю",
    "Поточне місцезнаходження": "Квартал Магістрів",
    "Поточна сцена": "Вілла Ілліріо Мопатіса — терасний сад з видом на бухту",
    "Світогляд": "Відданий, меланхолійний, шукає спокути",
    "Риси": "Досвідчений воїн, Поліглот",
    "Вади": "Знеславлений, Вигнанець"
}}
</few_shot_example>

ВІДПОВІДАЙ СТРОГО У ФОРМАТІ JSON:
{{
    "thought_process": "<ToT + Adversarial reasoning>",
    "narrative_intro": "<1-2 абзаци про походження персонажа, Ukrainian>",
    "Ім'я": "{char_name}",
    "Дім": "{house_name}",
    "suggested_class": "<Knight|Hedge Knight|Maester|Septon|Sellsword|Spy|Courtier|Bastard|Wildling>",
    "suggested_heritage": "<Westerosi (Andal)|Valyrian Descent|First Men (Stark line)|Free Folk|Red Priest|Ironborn>",
    "background": "<free-form background string>",
    "ability_scores": {{"STR": 10, "DEX": 10, "CON": 10, "INT": 10, "WIS": 10, "CHA": 12}},
    "personality_traits": ["...", "..."],
    "bond": "<one binding goal or relationship>",
    "flaw": "<one weakness>",
    "Поточне місцезнаходження": "<valid location from list>",
    "Поточна сцена": "<valid scene from list>",
    "Світогляд": "<philosophy>",
    "Риси": "<comma-separated traits>",
    "Вади": "<comma-separated flaws>"
}}"""


# ═══════════════════════════════════════════════════════════════════════════════
# JSON Schema builders — response_schema для Google Gemini JSON mode
# Використовуються у core/engine.py та core/dnd_engine.py разом із
# response_mime_type="application/json" для технічного усунення MALFORMED_RESPONSE.
#
# ПРАВИЛО МУТАЦІЇ: якщо контракт промпту змінюється — синхронно оновлювати schema
# тут і в споживачі (engine.py/dnd_engine.py). Це домен python-dev.
# ═══════════════════════════════════════════════════════════════════════════════

def build_validate_action_schema():
    """Schema для Censor (build_validate_action_prompt). 2 обов'язкові ключі."""
    from google.genai import types as gtypes
    return gtypes.Schema(
        type=gtypes.Type.OBJECT,
        properties={
            "is_valid": gtypes.Schema(type=gtypes.Type.BOOLEAN),
            "refusal_reason": gtypes.Schema(type=gtypes.Type.STRING),
        },
        required=["is_valid", "refusal_reason"],
    )


def build_normal_resolve_schema():
    """Schema для Worker NORMAL (build_normal_resolve_prompt).

    Всі обов'язкові ключі верхнього рівня зафіксовані у required[].
    Вкладений об'єкт updates зафіксований окремо.
    """
    from google.genai import types as gtypes

    _str = gtypes.Schema(type=gtypes.Type.STRING)
    _int = gtypes.Schema(type=gtypes.Type.INTEGER)
    _bool = gtypes.Schema(type=gtypes.Type.BOOLEAN)
    _arr_str = gtypes.Schema(type=gtypes.Type.ARRAY, items=_str)

    updates_schema = gtypes.Schema(
        type=gtypes.Type.OBJECT,
        properties={
            "minutes_passed": _int,
            "location_impact": _str,
            "scene_impact": _str,
            "hp_damage_dice": _str,
            "hp_heal_dice": _str,
            "gold_impact": _str,
            "inventory_new": _arr_str,
            "inventory_lost": _arr_str,
            "clocks_impact": gtypes.Schema(type=gtypes.Type.OBJECT),
            "condition_apply": gtypes.Schema(type=gtypes.Type.ARRAY, items=gtypes.Schema(type=gtypes.Type.OBJECT)),
            "condition_remove": gtypes.Schema(type=gtypes.Type.ARRAY, items=gtypes.Schema(type=gtypes.Type.OBJECT)),
        },
        required=[
            "minutes_passed", "location_impact", "scene_impact",
            "hp_damage_dice", "hp_heal_dice", "gold_impact",
            "inventory_new", "inventory_lost", "clocks_impact",
            "condition_apply", "condition_remove",
        ],
    )

    return gtypes.Schema(
        type=gtypes.Type.OBJECT,
        properties={
            "skill_check_reasoning": _str,
            "difficulty_reasoning": _str,
            "gold_reasoning": _str,
            "action_type": gtypes.Schema(type=gtypes.Type.STRING, enum=["standard", "training"]),
            "ability_used": gtypes.Schema(
                type=gtypes.Type.STRING,
                enum=["STR", "DEX", "CON", "INT", "WIS", "CHA", "None"],
            ),
            "skill_used": _str,
            "difficulty": _int,
            "advantage_reason": _str,
            "disadvantage_reason": _str,
            "combat_imminent": _bool,
            "verdict_text": _str,
            "xp_award": _int,
            "reputation_delta": _int,
            "reputation_target_npc": _str,
            "updates": updates_schema,
        },
        required=[
            "skill_check_reasoning", "difficulty_reasoning", "gold_reasoning",
            "action_type", "ability_used", "skill_used", "difficulty",
            "advantage_reason", "disadvantage_reason", "combat_imminent",
            "verdict_text", "xp_award", "reputation_delta", "reputation_target_npc",
            "updates",
        ],
    )


def build_combat_round_schema():
    """Schema для Worker COMBAT (build_combat_round_prompt)."""
    from google.genai import types as gtypes
    _str = gtypes.Schema(type=gtypes.Type.STRING)
    return gtypes.Schema(
        type=gtypes.Type.OBJECT,
        properties={
            "intent": gtypes.Schema(
                type=gtypes.Type.STRING,
                enum=["attack", "cast", "move", "dodge", "flee", "item", "help", "grapple", "shove"],
            ),
            "target_npc": _str,
            "weapon": _str,
            "spell_or_ability": _str,
            "tactic": gtypes.Schema(type=gtypes.Type.STRING, enum=["reckless", "normal", "cautious"]),
            "move_to": _str,
            "verdict_text": _str,
            "reasoning": _str,
        },
        required=["intent", "target_npc", "weapon", "spell_or_ability", "tactic", "move_to", "verdict_text", "reasoning"],
    )


def build_npc_combat_action_schema():
    """Schema для NPC combat action (build_npc_combat_action_prompt). Батч 1-2 NPC."""
    from google.genai import types as gtypes
    _str = gtypes.Schema(type=gtypes.Type.STRING)
    action_item = gtypes.Schema(
        type=gtypes.Type.OBJECT,
        properties={
            "npc_name": _str,
            "action": gtypes.Schema(
                type=gtypes.Type.STRING,
                enum=["attack", "dodge", "flee", "help", "cast", "none"],
            ),
            "target": _str,
            "weapon": _str,
            "reason": _str,
        },
        required=["npc_name", "action", "target", "weapon", "reason"],
    )
    return gtypes.Schema(
        type=gtypes.Type.OBJECT,
        properties={
            "actions": gtypes.Schema(type=gtypes.Type.ARRAY, items=action_item),
        },
        required=["actions"],
    )


def build_npc_regen_schema():
    """Schema для NPC statblock regen (build_npc_regen_prompt)."""
    from google.genai import types as gtypes
    _str = gtypes.Schema(type=gtypes.Type.STRING)
    _int = gtypes.Schema(type=gtypes.Type.INTEGER)
    _obj = gtypes.Schema(type=gtypes.Type.OBJECT)
    _arr_str = gtypes.Schema(type=gtypes.Type.ARRAY, items=_str)
    attack_item = gtypes.Schema(
        type=gtypes.Type.OBJECT,
        properties={
            "name": _str,
            "to_hit": _int,
            "dmg": _str,
            "range": _int,
        },
        required=["name", "to_hit", "dmg"],
    )
    return gtypes.Schema(
        type=gtypes.Type.OBJECT,
        properties={
            "cr": gtypes.Schema(
                type=gtypes.Type.STRING,
                enum=["0", "1/8", "1/4", "1/2", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10"],
            ),
            "ability_scores": _obj,
            "hp_max": _int,
            "ac": _int,
            "speed": _int,
            "attacks": gtypes.Schema(type=gtypes.Type.ARRAY, items=attack_item),
            "saves": _obj,
            "skills": _obj,
            "conditions": _arr_str,
            "tags": _arr_str,
            "reasoning": _str,
        },
        required=["cr", "ability_scores", "hp_max", "ac", "speed", "attacks", "saves", "skills", "conditions", "tags", "reasoning"],
    )


def build_gm_logic_schema(mode: str = "NORMAL"):
    """Schema для GM_Logic (build_gm_logic_prompt). mode="NORMAL"|"COMBAT"."""
    from google.genai import types as gtypes
    _str = gtypes.Schema(type=gtypes.Type.STRING)
    _int = gtypes.Schema(type=gtypes.Type.INTEGER)
    _arr_str = gtypes.Schema(type=gtypes.Type.ARRAY, items=_str)

    npc_update_item = gtypes.Schema(
        type=gtypes.Type.OBJECT,
        properties={
            "Name": _str,
            "Location": _str,
            "Scene": _str,
            "Memory_Anchor": _str,
            "Relation_NPCs": _str,
            "Inventory": _str,
            "Status": gtypes.Schema(
                type=gtypes.Type.STRING,
                enum=["Active", "Dead", "Fled", "Unconscious"],
            ),
            "hp_current": _int,
            "conditions": _arr_str,
        },
        required=["Name", "Status"],
    )

    action_item = gtypes.Schema(
        type=gtypes.Type.OBJECT,
        properties={
            "button": gtypes.Schema(
                type=gtypes.Type.STRING,
                description="Up to 5 words in Ukrainian only (Cyrillic). NO English, NO Korean/Chinese/Japanese scripts.",
            ),
            "intent": gtypes.Schema(
                type=gtypes.Type.STRING,
                description="10-15 words in Ukrainian only, first-person starting with 'Я ...'. NO English or non-Cyrillic words.",
            ),
        },
        required=["button", "intent"],
    )

    mode_transition_values = [None, "TO_COMBAT", "TO_NORMAL"]  # null handled by nullable
    return gtypes.Schema(
        type=gtypes.Type.OBJECT,
        properties={
            "reasoning": _str,
            "npc_reasoning": _str,
            "frozen_fields_change_reason": _str,
            "mode_transition": _str,
            "director_notes": _arr_str,
            "companion_npcs": _arr_str,
            "npc_updates": gtypes.Schema(type=gtypes.Type.ARRAY, items=npc_update_item),
            "suggested_actions": gtypes.Schema(type=gtypes.Type.ARRAY, items=action_item),
        },
        required=[
            "reasoning", "npc_reasoning", "frozen_fields_change_reason",
            "mode_transition", "director_notes", "companion_npcs",
            "npc_updates", "suggested_actions",
        ],
    )


def build_training_request_schema():
    """Schema для training intent detection (build_training_request_prompt)."""
    from google.genai import types as gtypes
    _str = gtypes.Schema(type=gtypes.Type.STRING)
    _bool = gtypes.Schema(type=gtypes.Type.BOOLEAN)
    return gtypes.Schema(
        type=gtypes.Type.OBJECT,
        properties={
            "is_training": _bool,
            "is_possible": _bool,
            "skill": _str,
            "method": gtypes.Schema(type=gtypes.Type.STRING, enum=["solo", "mentor", ""]),
            "reason_if_failed": _str,
        },
        required=["is_training", "is_possible", "skill", "method", "reason_if_failed"],
    )


def build_initial_stats_schema():
    """Schema для character creation (build_initial_stats_prompt)."""
    from google.genai import types as gtypes
    _str = gtypes.Schema(type=gtypes.Type.STRING)
    _int = gtypes.Schema(type=gtypes.Type.INTEGER)
    _arr_str = gtypes.Schema(type=gtypes.Type.ARRAY, items=_str)

    ability_scores_schema = gtypes.Schema(
        type=gtypes.Type.OBJECT,
        properties={
            "STR": _int, "DEX": _int, "CON": _int,
            "INT": _int, "WIS": _int, "CHA": _int,
        },
        required=["STR", "DEX", "CON", "INT", "WIS", "CHA"],
    )

    return gtypes.Schema(
        type=gtypes.Type.OBJECT,
        properties={
            "thought_process": _str,
            "narrative_intro": _str,
            "Ім'я": _str,        # "Ім'я"
            "Дім": _str,         # "Дім"
            "suggested_class": gtypes.Schema(
                type=gtypes.Type.STRING,
                enum=["Knight", "Hedge Knight", "Maester", "Septon", "Sellsword", "Spy", "Courtier", "Bastard", "Wildling"],
            ),
            "suggested_heritage": gtypes.Schema(
                type=gtypes.Type.STRING,
                enum=["Westerosi (Andal)", "Valyrian Descent", "First Men (Stark line)", "Free Folk", "Red Priest", "Ironborn"],
            ),
            "background": _str,
            "ability_scores": ability_scores_schema,
            "personality_traits": _arr_str,
            "bond": _str,
            "flaw": _str,
            "Поточне місцезнаходження": _str,  # "Поточне місцезнаходження"
            "Поточна сцена": _str,  # "Поточна сцена"
            "Світогляд": _str,  # "Світогляд"
            "Риси": _str,   # "Риси"
            "Вади": _str,   # "Вади"
        },
        required=[
            "thought_process", "narrative_intro", "suggested_class", "suggested_heritage",
            "background", "ability_scores", "personality_traits", "bond", "flaw",
        ],
    )
