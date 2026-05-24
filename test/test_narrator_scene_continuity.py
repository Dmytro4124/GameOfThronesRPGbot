"""
test_narrator_scene_continuity.py

Тести для фічі "Описи навколишнього середовища тільки при зміні сцени".

Перевіряє:
1. build_narrator_prompt приймає scene_continuity_block і вставляє його в промпт.
2. SCENE_CONTINUITY_NEW і SCENE_CONTINUITY_CONTINUING мають правильні токени.
3. Зворотна сумісність: без параметра промпт не містить <scene_continuity>.
4. Engine передає правильний блок залежно від location_or_scene_changed.
5. scene_continuity_block присутній у всіх викликах _build_narrator_prompt
   всередині process_game_turn (включно з retry-шляхами).

Стратегія мокування для engine-тестів:
- get_user_data → AsyncMock → готовий профіль.
- validate_action → AsyncMock → (True, "").
- resolve_action_mechanics → AsyncMock → ready verdict + updates.
- model_gm_logic.generate_content → MagicMock → мінімальний JSON.
- model_narrator.generate_content → MagicMock → фіксований текст.
- get_location_npcs → ("", [], {}).
- get_relevant_context → AsyncMock → "".
- get_dead_npc_names → set().
- asyncio.create_task → no-op.
"""

import asyncio
import inspect
import re
from contextlib import ExitStack
from unittest.mock import patch, MagicMock, AsyncMock

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Тест 1: build_narrator_prompt з NEW_SCENE блоком
# ─────────────────────────────────────────────────────────────────────────────

def test_narrator_prompt_contains_new_scene_block():
    """
    build_narrator_prompt з scene_continuity_block=SCENE_CONTINUITY_NEW →
    повернений рядок містить 'NEW_SCENE' і слово 'атмосферу'.
    """
    from core.prompts import build_narrator_prompt
    from core.engine import SCENE_CONTINUITY_NEW

    prompt = build_narrator_prompt(
        user_input="Оглянутися навколо",
        director_notes=["Герой стоїть у залі."],
        npc_context_text="",
        player_name="Тест Герой",
        player_house="Старк",
        current_scene="Зала",
        current_location="Вінтерфелл",
        impact_narrative_hints="",
        scene_continuity_block=SCENE_CONTINUITY_NEW,
    )

    assert isinstance(prompt, str), "build_narrator_prompt must return a string"
    assert "NEW_SCENE" in prompt, (
        f"Prompt must contain 'NEW_SCENE' when SCENE_CONTINUITY_NEW passed. "
        f"Got: {prompt[-300:]!r}"
    )
    assert "атмосферу" in prompt, (
        f"Prompt must contain 'атмосферу' from SCENE_CONTINUITY_NEW block. "
        f"Got: {prompt[-300:]!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Тест 2: build_narrator_prompt з CONTINUING блоком
# ─────────────────────────────────────────────────────────────────────────────

def test_narrator_prompt_contains_continuing_block():
    """
    build_narrator_prompt з scene_continuity_block=SCENE_CONTINUITY_CONTINUING →
    повернений рядок містить 'CONTINUING' і фрагмент 'НЕ повторюй'.
    """
    from core.prompts import build_narrator_prompt
    from core.engine import SCENE_CONTINUITY_CONTINUING

    prompt = build_narrator_prompt(
        user_input="Поговорити з охоронцем",
        director_notes=["Герой звертається до охоронця."],
        npc_context_text="",
        player_name="Тест Герой",
        player_house="Старк",
        current_scene="Зала",
        current_location="Вінтерфелл",
        impact_narrative_hints="",
        scene_continuity_block=SCENE_CONTINUITY_CONTINUING,
    )

    assert isinstance(prompt, str), "build_narrator_prompt must return a string"
    assert "CONTINUING" in prompt, (
        f"Prompt must contain 'CONTINUING' when SCENE_CONTINUITY_CONTINUING passed. "
        f"Got: {prompt[-300:]!r}"
    )
    assert "НЕ повторюй" in prompt, (
        f"Prompt must contain 'НЕ повторюй' from SCENE_CONTINUITY_CONTINUING block. "
        f"Got: {prompt[-300:]!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Тест 3: build_narrator_prompt без scene_continuity_block (default "")
# ─────────────────────────────────────────────────────────────────────────────

def test_narrator_prompt_no_block_default():
    """
    build_narrator_prompt без scene_continuity_block (default ""):
    - повертає валідний непорожній рядок
    - НЕ містить структурного XML-блоку з даними сцени:
        '<scene_continuity>NEW_SCENE:' або '<scene_continuity>CONTINUING:'
    Зворотна сумісність гарантована.

    Примітка: NARRATOR_SYSTEM_PROMPT (пункт 9) навмисно містить токени 'NEW_SCENE',
    'CONTINUING' і '<scene_continuity>' як частину правил стилю для моделі — це очікувано.
    Тест перевіряє лише відсутність структурного блоку даних, що вставляється
    тільки через параметр scene_continuity_block.
    """
    from core.prompts import build_narrator_prompt

    prompt = build_narrator_prompt(
        user_input="Оглянутися",
        director_notes=["Герой у залі."],
        npc_context_text="",
        player_name="Тест Герой",
        player_house="Старк",
        current_scene="Зала",
        current_location="Вінтерфелл",
        impact_narrative_hints="",
        # scene_continuity_block не передається — default ""
    )

    assert isinstance(prompt, str), "build_narrator_prompt must return a string"
    assert len(prompt) > 50, "Prompt must be non-trivially long even without scene_continuity_block"

    # Структурний блок даних: <scene_continuity>NEW_SCENE: або <scene_continuity>CONTINUING:
    # (без параметра цих блоків не повинно бути в промпті)
    assert "<scene_continuity>NEW_SCENE:" not in prompt, (
        f"Prompt must NOT contain '<scene_continuity>NEW_SCENE:' data block when no "
        f"scene_continuity_block passed. Got (last 300 chars): {prompt[-300:]!r}"
    )
    assert "<scene_continuity>CONTINUING:" not in prompt, (
        f"Prompt must NOT contain '<scene_continuity>CONTINUING:' data block when no "
        f"scene_continuity_block passed. Got (last 300 chars): {prompt[-300:]!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Тест 4: Константи є різними і обидві мають теги <scene_continuity>
# ─────────────────────────────────────────────────────────────────────────────

def test_scene_continuity_constants_are_distinct():
    """
    SCENE_CONTINUITY_NEW != SCENE_CONTINUITY_CONTINUING.
    Обидві константи містять відкриваючий тег <scene_continuity>
    і закриваючий тег </scene_continuity>.
    """
    from core.engine import SCENE_CONTINUITY_NEW, SCENE_CONTINUITY_CONTINUING

    assert SCENE_CONTINUITY_NEW != SCENE_CONTINUITY_CONTINUING, (
        "SCENE_CONTINUITY_NEW and SCENE_CONTINUITY_CONTINUING must be different strings"
    )
    assert "<scene_continuity>" in SCENE_CONTINUITY_NEW, (
        f"SCENE_CONTINUITY_NEW must contain '<scene_continuity>'. Got: {SCENE_CONTINUITY_NEW!r}"
    )
    assert "</scene_continuity>" in SCENE_CONTINUITY_NEW, (
        f"SCENE_CONTINUITY_NEW must contain '</scene_continuity>'. Got: {SCENE_CONTINUITY_NEW!r}"
    )
    assert "<scene_continuity>" in SCENE_CONTINUITY_CONTINUING, (
        f"SCENE_CONTINUITY_CONTINUING must contain '<scene_continuity>'. "
        f"Got: {SCENE_CONTINUITY_CONTINUING!r}"
    )
    assert "</scene_continuity>" in SCENE_CONTINUITY_CONTINUING, (
        f"SCENE_CONTINUITY_CONTINUING must contain '</scene_continuity>'. "
        f"Got: {SCENE_CONTINUITY_CONTINUING!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Тест 5: Токени NEW_SCENE / CONTINUING у правильних константах
# ─────────────────────────────────────────────────────────────────────────────

def test_scene_continuity_constants_have_expected_tokens():
    """
    'NEW_SCENE' присутній у SCENE_CONTINUITY_NEW і відсутній у SCENE_CONTINUITY_CONTINUING.
    'CONTINUING' присутній у SCENE_CONTINUITY_CONTINUING і відсутній у SCENE_CONTINUITY_NEW.
    """
    from core.engine import SCENE_CONTINUITY_NEW, SCENE_CONTINUITY_CONTINUING

    assert "NEW_SCENE" in SCENE_CONTINUITY_NEW, (
        f"'NEW_SCENE' must be in SCENE_CONTINUITY_NEW. Got: {SCENE_CONTINUITY_NEW!r}"
    )
    assert "NEW_SCENE" not in SCENE_CONTINUITY_CONTINUING, (
        f"'NEW_SCENE' must NOT be in SCENE_CONTINUITY_CONTINUING. "
        f"Got: {SCENE_CONTINUITY_CONTINUING!r}"
    )
    assert "CONTINUING" in SCENE_CONTINUITY_CONTINUING, (
        f"'CONTINUING' must be in SCENE_CONTINUITY_CONTINUING. "
        f"Got: {SCENE_CONTINUITY_CONTINUING!r}"
    )
    # SCENE_CONTINUITY_NEW не повинна містити окремий токен CONTINUING
    # (але може містити слово як частину "SCENE_CONTINUITY_NEW" — перевіряємо точніше)
    assert "CONTINUING:" not in SCENE_CONTINUITY_NEW, (
        f"'CONTINUING:' must NOT be in SCENE_CONTINUITY_NEW. Got: {SCENE_CONTINUITY_NEW!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Тест 6а: Статичний аналіз process_game_turn — обидві гілки тернарника присутні
# ─────────────────────────────────────────────────────────────────────────────

def test_process_game_turn_source_contains_both_scene_continuity_branches():
    """
    Статична перевірка source-коду process_game_turn:
    - Присутнє присвоєння scene_continuity_block через тернарник
      із SCENE_CONTINUITY_NEW і SCENE_CONTINUITY_CONTINUING.
    - scene_continuity_block= присутній у виклику _build_narrator_prompt.

    Це гарантує, що обидві гілки логіки сцени закодовані у функції.
    """
    from core.engine import process_game_turn

    source = inspect.getsource(process_game_turn)

    assert "SCENE_CONTINUITY_NEW" in source, (
        "process_game_turn source must reference SCENE_CONTINUITY_NEW"
    )
    assert "SCENE_CONTINUITY_CONTINUING" in source, (
        "process_game_turn source must reference SCENE_CONTINUITY_CONTINUING"
    )
    assert "location_or_scene_changed" in source, (
        "process_game_turn source must use location_or_scene_changed as condition"
    )
    assert "scene_continuity_block=" in source, (
        "process_game_turn source must pass scene_continuity_block= to _build_narrator_prompt"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Тест 6б: Статична перевірка retry-шляху — scene_continuity_block в усіх викликах
# ─────────────────────────────────────────────────────────────────────────────

def test_process_game_turn_scene_continuity_in_all_narrator_calls():
    """
    Перевірка retry-шляху через інспекцію source-коду.

    narrator_prompt будується ОДИН раз із scene_continuity_block і далі
    передається у всі generate_content виклики всередині process_game_turn
    (attempt 1, retry attempt 2, attempt 3 — streaming і blocking гілки).

    Перевіряємо:
    1. scene_continuity_block= передається у виклик _build_narrator_prompt.
    2. narrator_prompt будується рівно один раз (до першого generate_content).
    3. Всі generate_content виклики у функції використовують той самий narrator_prompt
       (без локального перебудування промпту у retry-гілках).
    """
    from core.engine import process_game_turn

    source = inspect.getsource(process_game_turn)

    # 1. scene_continuity_block= передається в _build_narrator_prompt
    assert "scene_continuity_block=scene_continuity_block" in source, (
        "process_game_turn must pass scene_continuity_block=scene_continuity_block "
        "to _build_narrator_prompt"
    )

    # 2. narrator_prompt будується через _build_narrator_prompt (один рядок призначення)
    assignments = re.findall(r'narrator_prompt\s*=\s*_build_narrator_prompt\s*\(', source)
    assert len(assignments) == 1, (
        f"narrator_prompt must be assigned from _build_narrator_prompt exactly once. "
        f"Found {len(assignments)} assignment(s). "
        f"Multiple assignments would mean retry rebuilds prompt without scene_continuity_block."
    )

    # 3. Жодного окремого виклику _build_narrator_prompt у retry-гілках
    #    (всі retry використовують вже побудований narrator_prompt)
    total_build_calls = source.count("_build_narrator_prompt(")
    assert total_build_calls == 1, (
        f"_build_narrator_prompt must be called exactly once in process_game_turn. "
        f"Found {total_build_calls} calls. "
        f"Extra calls in retry path mean scene_continuity_block might be missing there."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Тест 7: Engine integration — scene_changed=True → SCENE_CONTINUITY_NEW у промпті
# ─────────────────────────────────────────────────────────────────────────────

# Мінімальний профіль для інтеграційних тестів engine
_PROFILE = {
    "Ім'я": "Тест Герой",
    "Дім": "Старк",
    "Титул": "Лорд",
    "Здоров'я": 100,
    "Енергія": 800,
    "Особисте Золото": 200,
    "Бойові навички": 50,
    "Військові навички": 20,
    "Інтрига": 15,
    "Управління": 10,
    "Поточне місцезнаходження": "Вінтерфелл",
    "Поточна сцена": "Зала",
    "Регіон": "Північ",
    "Ігровий час": "День 1, Ранок",
    "Інвентар": "Меч",
    "Зброя": "Довгий меч",
    "Броня": "Кольчуга",
    "Транспорт": "Кінь",
    "Світогляд": "Нейтральний",
    "Риси": "Хоробрий",
    "Вади": "Упертий",
    "Вороги": "",
    "Друзі": "",
    "Годинники": {"Scene_Tension": "0/4"},
}

_GM_LOGIC_JSON = (
    '{"reasoning": "test", "npc_reasoning": "test",'
    ' "director_notes": ["Hero steps into the new hall."],'
    ' "companion_npcs": [], "npc_updates": [],'
    ' "suggested_actions": ['
    '{"button": "Action 1", "intent": "First"},'
    '{"button": "Action 2", "intent": "Second"},'
    '{"button": "Action 3", "intent": "Third"},'
    '{"button": "Action 4", "intent": "Fourth"}]}'
)

_VALID_NARRATOR_TEXT = (
    "The hero steps carefully into the new hall. "
    "Torches cast long shadows on ancient stone walls. "
    "Cold northern air fills the chamber."
)


def _make_gm_response():
    r = MagicMock()
    r.text = _GM_LOGIC_JSON
    return r


def _make_narrator_response(text: str):
    r = MagicMock()
    r.text = text
    return r


def _build_base_patches(narrator_mock: MagicMock, profile_override: dict = None) -> list:
    """Базовий набір патчів для isolating process_game_turn."""
    prof = (profile_override or _PROFILE).copy()
    return [
        patch("core.engine.get_user_data", new=AsyncMock(return_value=(prof, 2))),
        patch("core.engine.validate_action", new=AsyncMock(return_value=(True, ""))),
        patch(
            "core.engine.resolve_normal_action",
            new=AsyncMock(return_value=("MECHANICAL VERDICT: SUCCESS", {
                "action_type": "standard",
                "skill_used": "None",
                "difficulty": 70,
                "circumstance": "NORMAL",
                "outcome": "SUCCESS",
                "dice_roll": "50",
                "skill_val": 20,
                "total_score": 70,
                "reputation_delta": 0,
                "reputation_target_npc": None,
                "minutes_passed": 10,
                "location_impact": "none",
                "scene_impact": "none",
                "health_impact": "none",
                "energy_impact": "none",
                "gold_impact": "none",
                "inventory_new": [],
                "inventory_lost": [],
                "clocks_impact": {},
            })),
        ),
        patch("core.engine.get_location_npcs", return_value=("", [], {})),
        patch("core.engine.get_relevant_context", new=AsyncMock(return_value="")),
        patch("core.engine.get_dead_npc_names", return_value=set()),
        patch(
            "core.engine.model_gm_logic.generate_content",
            return_value=_make_gm_response(),
        ),
        patch("core.engine.model_narrator.generate_content", narrator_mock),
        patch("core.engine.asyncio.create_task", return_value=MagicMock()),
    ]


def test_engine_new_scene_passes_scene_continuity_new_to_narrator():
    """
    Engine integration test: коли Worker повертає scene_impact (зміна сцени),
    engine формує scene_continuity_block = SCENE_CONTINUITY_NEW і передає
    його в narrator_prompt, який містить токен 'NEW_SCENE'.

    Перехоплюємо виклик _build_narrator_prompt та перевіряємо аргумент.
    """
    from core.engine import SCENE_CONTINUITY_NEW, SCENE_CONTINUITY_CONTINUING

    captured_block = []
    original_build = None

    def capturing_build_narrator(*args, **kwargs):
        captured_block.append(kwargs.get("scene_continuity_block", ""))
        return original_build(*args, **kwargs)

    narrator_mock = MagicMock(return_value=_make_narrator_response(_VALID_NARRATOR_TEXT))

    # Профіль де сцена "Зала" (буде змінена у Worker-відповіді)
    profile_before = _PROFILE.copy()
    profile_before["Поточна сцена"] = "Зала"

    # Worker повертає scene_impact — означає зміна сцени
    worker_updates_scene_change = {
        "action_type": "standard",
        "skill_used": "None",
        "difficulty": 70,
        "circumstance": "NORMAL",
        "outcome": "SUCCESS",
        "dice_roll": "50",
        "skill_val": 20,
        "total_score": 70,
        "reputation_delta": 0,
        "reputation_target_npc": None,
        "minutes_passed": 10,
        "location_impact": "none",
        "scene_impact": "Тронна зала",  # зміна сцени
        "health_impact": "none",
        "energy_impact": "none",
        "gold_impact": "none",
        "inventory_new": [],
        "inventory_lost": [],
        "clocks_impact": {},
    }

    async def _run():
        import core.engine as engine_mod
        nonlocal original_build
        original_build = engine_mod._build_narrator_prompt

        with patch.object(engine_mod, "_build_narrator_prompt", side_effect=capturing_build_narrator):
            from core.engine import process_game_turn
            return await process_game_turn(
                chat_id=201,
                user_input="Перейти до Тронної зали",
                narrator_queue=None,
            )

    patches = [
        patch("core.engine.get_user_data", new=AsyncMock(return_value=(profile_before, 2))),
        patch("core.engine.validate_action", new=AsyncMock(return_value=(True, ""))),
        # Engine routes NORMAL mode through resolve_normal_action (core.dnd_engine).
        patch(
            "core.engine.resolve_normal_action",
            new=AsyncMock(return_value=("MECHANICAL VERDICT: SUCCESS", worker_updates_scene_change)),
        ),
        patch("core.engine.get_location_npcs", return_value=("", [], {})),
        patch("core.engine.get_relevant_context", new=AsyncMock(return_value="")),
        patch("core.engine.get_dead_npc_names", return_value=set()),
        patch("core.engine.model_gm_logic.generate_content", return_value=_make_gm_response()),
        patch("core.engine.model_narrator.generate_content", narrator_mock),
        patch("core.engine.asyncio.create_task", return_value=MagicMock()),
    ]

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        asyncio.run(_run())

    assert len(captured_block) >= 1, (
        "capturing_build_narrator must have been called at least once"
    )
    actual_block = captured_block[0]
    assert actual_block == SCENE_CONTINUITY_NEW, (
        f"When scene changes, engine must pass SCENE_CONTINUITY_NEW. "
        f"Got: {actual_block!r}"
    )
    assert "NEW_SCENE" in actual_block, (
        f"SCENE_CONTINUITY_NEW must contain 'NEW_SCENE'. Got: {actual_block!r}"
    )


def test_engine_same_scene_passes_scene_continuity_continuing_to_narrator():
    """
    Engine integration test: коли Worker НЕ змінює сцену (scene_impact="none",
    location_impact="none"), engine формує scene_continuity_block = SCENE_CONTINUITY_CONTINUING
    і передає його в narrator_prompt, який містить токен 'CONTINUING'.
    """
    from core.engine import SCENE_CONTINUITY_NEW, SCENE_CONTINUITY_CONTINUING

    captured_block = []
    original_build = None

    def capturing_build_narrator(*args, **kwargs):
        captured_block.append(kwargs.get("scene_continuity_block", ""))
        return original_build(*args, **kwargs)

    narrator_mock = MagicMock(return_value=_make_narrator_response(_VALID_NARRATOR_TEXT))

    profile_same_scene = _PROFILE.copy()
    profile_same_scene["Поточна сцена"] = "Зала"

    # Worker НЕ змінює сцену
    worker_updates_same_scene = {
        "action_type": "standard",
        "skill_used": "None",
        "difficulty": 50,
        "circumstance": "NORMAL",
        "outcome": "SUCCESS",
        "dice_roll": "40",
        "skill_val": 20,
        "total_score": 60,
        "reputation_delta": 0,
        "reputation_target_npc": None,
        "minutes_passed": 5,
        "location_impact": "none",  # локація не змінилась
        "scene_impact": "none",      # сцена не змінилась
        "health_impact": "none",
        "energy_impact": "none",
        "gold_impact": "none",
        "inventory_new": [],
        "inventory_lost": [],
        "clocks_impact": {},
    }

    async def _run():
        import core.engine as engine_mod
        nonlocal original_build
        original_build = engine_mod._build_narrator_prompt

        with patch.object(engine_mod, "_build_narrator_prompt", side_effect=capturing_build_narrator):
            from core.engine import process_game_turn
            return await process_game_turn(
                chat_id=202,
                user_input="Поговорити з охоронцем",
                narrator_queue=None,
            )

    patches = [
        patch("core.engine.get_user_data", new=AsyncMock(return_value=(profile_same_scene, 2))),
        patch("core.engine.validate_action", new=AsyncMock(return_value=(True, ""))),
        patch(
            "core.engine.resolve_normal_action",
            new=AsyncMock(return_value=("MECHANICAL VERDICT: SUCCESS", worker_updates_same_scene)),
        ),
        patch("core.engine.get_location_npcs", return_value=("", [], {})),
        patch("core.engine.get_relevant_context", new=AsyncMock(return_value="")),
        patch("core.engine.get_dead_npc_names", return_value=set()),
        patch("core.engine.model_gm_logic.generate_content", return_value=_make_gm_response()),
        patch("core.engine.model_narrator.generate_content", narrator_mock),
        patch("core.engine.asyncio.create_task", return_value=MagicMock()),
    ]

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        asyncio.run(_run())

    assert len(captured_block) >= 1, (
        "capturing_build_narrator must have been called at least once"
    )
    actual_block = captured_block[0]
    assert actual_block == SCENE_CONTINUITY_CONTINUING, (
        f"When scene does not change, engine must pass SCENE_CONTINUITY_CONTINUING. "
        f"Got: {actual_block!r}"
    )
    assert "CONTINUING" in actual_block, (
        f"SCENE_CONTINUITY_CONTINUING must contain 'CONTINUING'. Got: {actual_block!r}"
    )
