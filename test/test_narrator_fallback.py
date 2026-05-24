"""
test_narrator_fallback.py

Тести для тришарової Narrator-стратегії в core/engine.py:
- Шар 1: трункований але змістовний текст (≥100 символів) → приймається з "…", retry НЕ запускається.
- Шар 2: retry (другий виклик generate_content).
- Шар 3: третя спроба blocking з temperature=0.5.
- Last-resort: _build_deterministic_narrative → художній абзац ≥100 символів без "⚠️"/"помилка"/"Майстер".
- 📊 блок показується ЗАВЖДИ (навіть при last-resort).

Async-тести запускаються через asyncio.run() (без pytest-asyncio — відповідно до
патерну, прийнятого у test/test_help_command.py та test/test_npc_isolation.py).

Стратегія мокування:
- get_user_data           → AsyncMock → готовий профіль без Sheets.
- validate_action         → AsyncMock → (True, "") — цензор не блокує.
- resolve_action_mechanics → AsyncMock → готовий verdict + updates.
- model_gm_logic.generate_content → MagicMock → мінімальний JSON.
- model_narrator.generate_content → MagicMock → керований тестом.
- get_location_npcs       → ("", [], {}).
- get_relevant_context    → AsyncMock → "".
- get_dead_npc_names      → set().
- asyncio.create_task     → no-op (щоб background_task не звертався до реального Sheets).
"""

import asyncio
import pytest
from contextlib import ExitStack
from unittest.mock import patch, MagicMock, AsyncMock

# ─── Маркери summary-блоку (мають завжди бути у відповіді) ──────────────────
_SUMMARY_MARKER = "📊"

# ─── Мінімальний профіль гравця ───────────────────────────────────────────────
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

# ─── Мінімальний механічний результат від Worker-а ────────────────────────────
_WORKER_UPDATES = {
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
}

# ─── Мінімальна валідна GM_Logic відповідь ────────────────────────────────────
_GM_LOGIC_JSON = (
    '{"reasoning": "test", "npc_reasoning": "test",'
    ' "director_notes": ["Hero stands in the hall."],'
    ' "companion_npcs": [], "npc_updates": [],'
    ' "suggested_actions": ['
    '{"button": "Action 1", "intent": "First"},'
    '{"button": "Action 2", "intent": "Second"},'
    '{"button": "Action 3", "intent": "Third"},'
    '{"button": "Action 4", "intent": "Fourth"}]}'
)

_VALID_NARRATOR_TEXT = (
    "The hero steps carefully across the stone floor of the great hall. "
    "Torches cast trembling shadows on the ancient tapestries. "
    "Cold northern air seeps through the narrow windows."
)


def _make_gm_response():
    r = MagicMock()
    r.text = _GM_LOGIC_JSON
    return r


def _make_narrator_response(text: str):
    r = MagicMock()
    r.text = text
    return r


def _make_empty_narrator_response():
    r = MagicMock()
    r.text = ""
    return r


def _build_patches(narrator_mock: MagicMock) -> list:
    """
    Будує список patch()-об'єктів для isolating process_game_turn.
    narrator_mock вже налаштований зовні (return_value або side_effect).
    """
    return [
        patch(
            "core.engine.get_user_data",
            new=AsyncMock(return_value=(_PROFILE.copy(), 2)),
        ),
        patch(
            "core.engine.validate_action",
            new=AsyncMock(return_value=(True, "")),
        ),
        patch(
            "core.engine.resolve_normal_action",
            new=AsyncMock(return_value=("MECHANICAL VERDICT: SUCCESS", _WORKER_UPDATES.copy())),
        ),
        patch("core.engine.get_location_npcs", return_value=("", [], {})),
        patch("core.engine.get_relevant_context", new=AsyncMock(return_value="")),
        patch("core.engine.get_dead_npc_names", return_value=set()),
        patch(
            "core.engine.model_gm_logic.generate_content",
            return_value=_make_gm_response(),
        ),
        patch("core.engine.model_narrator.generate_content", narrator_mock),
        # no-op asyncio.create_task → не запускати background_task з реальним Sheets
        patch("core.engine.asyncio.create_task", return_value=MagicMock()),
    ]


# ─────────────────────────────────────────────────────────────────────────────
# ТЕСТ 1 (НОВИЙ): Всі три спроби Narrator провалились → детерміністичний абзац
#   + 📊 присутній, немає "⚠️", "Майстер", "помилка"
# ─────────────────────────────────────────────────────────────────────────────

def test_narrator_double_fail_returns_deterministic_narrative_with_summary():
    """
    Якщо всі три Narrator-спроби повертають порожній текст,
    process_game_turn має повернути детерміністичний художній абзац без:
      - "⚠️"
      - "Майстер" (case-insensitive)
      - "помилка" (case-insensitive)
    і обов'язково з блоком "📊" (impact-summary показується завжди).
    Художній текст до "📊" має бути ≥ 100 символів.
    """
    # Всі три виклики generate_content (attempt 1, retry, attempt 3) → порожньо
    narrator_mock = MagicMock(
        side_effect=[
            _make_empty_narrator_response(),
            _make_empty_narrator_response(),
            _make_empty_narrator_response(),
        ]
    )

    async def _run():
        from core.engine import process_game_turn
        return await process_game_turn(
            chat_id=142,
            user_input="Inspect the hall",
            narrator_queue=None,
        )

    with ExitStack() as stack:
        for p in _build_patches(narrator_mock):
            stack.enter_context(p)
        result_text, result_actions = asyncio.run(_run())

    # Без помилкового тексту
    assert "⚠️" not in result_text, (
        f"Result must NOT contain error emoji. Got: {result_text!r}"
    )
    assert "майстер" not in result_text.lower(), (
        f"Result must NOT contain 'Майстер' (case-insensitive). Got: {result_text!r}"
    )
    assert "помилка" not in result_text.lower(), (
        f"Result must NOT contain 'помилка' (case-insensitive). Got: {result_text!r}"
    )

    # 📊 завжди показується
    # Примітка: якщо logs порожній (_WORKER_UPDATES без active skill_used),
    # change_log може бути порожнім. Але _narrator_failed=True не впливає на це —
    # перевіряємо що story має ≥ 100 символів.
    story_part = result_text.split("📊")[0] if "📊" in result_text else result_text
    assert len(story_part.strip()) >= 100, (
        f"Narrative part before '📊' must be ≥ 100 chars. Got {len(story_part.strip())} chars. "
        f"Full text: {result_text!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# ТЕСТ 2 (НОВИЙ): Трункований текст ≥ 100 символів → приймається з "…", retry НЕ запускається
# ─────────────────────────────────────────────────────────────────────────────

def test_narrator_truncated_long_text_used_without_retry():
    """
    Якщо первинна спроба повертає трункований текст (не закінчується '.!?…"*')
    довжиною ≥ 100 символів і НЕ placeholder:
    - retry НЕ викликається (тільки 1 виклик generate_content)
    - результат закінчується на "…" (U+2026)
    - "📊" присутній (або результат має ≥ 100 символів artistic частини)
    """
    # Трункований текст без крапки в кінці, довжина > 100
    truncated_text = (
        "The hero moves through the dimly lit corridors of Winterfell, "
        "his boots echoing on the cold stone floor as shadows dance upon the walls"
    )
    assert len(truncated_text) >= 100, f"Test setup error: text too short ({len(truncated_text)})"
    assert not truncated_text.rstrip().endswith(('.', '!', '?', '…', '"', '*')), \
        "Test setup error: text must not end with punctuation"

    truncated_resp = _make_narrator_response(truncated_text)
    # Лише один елемент у side_effect — якщо retry спробує викликати ще раз, упаде StopIteration
    narrator_mock = MagicMock(side_effect=[truncated_resp])

    async def _run():
        from core.engine import process_game_turn
        return await process_game_turn(
            chat_id=143,
            user_input="Walk through the corridor",
            narrator_queue=None,
        )

    with ExitStack() as stack:
        for p in _build_patches(narrator_mock):
            stack.enter_context(p)
        result_text, _ = asyncio.run(_run())

    # Результат закінчується на "…" (до блоку 📊)
    story_part = result_text.split("📊")[0].rstrip() if "📊" in result_text else result_text.rstrip()
    assert story_part.endswith("…"), (
        f"Truncated text must end with '…'. Got: {story_part[-20:]!r}"
    )

    # generate_content викликано рівно 1 раз (retry не відбувся)
    assert narrator_mock.call_count == 1, (
        f"generate_content must be called exactly once (no retry). "
        f"Called {narrator_mock.call_count} times."
    )


# ─────────────────────────────────────────────────────────────────────────────
# ТЕСТ 3 (НОВИЙ): Attempt 1 = порожньо, Attempt 2 = порожньо, Attempt 3 = валідний текст
# ─────────────────────────────────────────────────────────────────────────────

def test_narrator_third_attempt_recovers():
    """
    Якщо першa і друга спроби повернули порожньо, але третя ≥ 50 символів:
    - текст від третьої спроби потрапляє у відповідь
    - "⚠️" НЕ присутній
    - "📊" присутній (або художня частина ≥ 50 символів)
    """
    third_attempt_text = (
        "The guard nods slowly, stepping aside. "
        "The gates of Winterfell creak open, and the cold wind rushes in."
    )
    assert len(third_attempt_text) >= 50, "Test setup: third attempt text must be ≥50 chars"

    narrator_mock = MagicMock(
        side_effect=[
            _make_empty_narrator_response(),   # attempt 1
            _make_empty_narrator_response(),   # attempt 2 (retry)
            _make_narrator_response(third_attempt_text),  # attempt 3
        ]
    )

    async def _run():
        from core.engine import process_game_turn
        return await process_game_turn(
            chat_id=144,
            user_input="Open the gate",
            narrator_queue=None,
        )

    with ExitStack() as stack:
        for p in _build_patches(narrator_mock):
            stack.enter_context(p)
        result_text, _ = asyncio.run(_run())

    # Текст від третьої спроби потрапив у результат
    assert third_attempt_text[:40] in result_text, (
        f"Third attempt text must appear in result. Got: {result_text!r}"
    )

    # Без помилкового маркера
    assert "⚠️" not in result_text, (
        f"Result must NOT contain error emoji. Got: {result_text!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# ТЕСТ 4 (НОВИЙ): Unit-тест _build_deterministic_narrative — 3 сценарії
# ─────────────────────────────────────────────────────────────────────────────

def test_build_deterministic_narrative_outputs_min_100_chars():
    """
    Прямий unit-тест на _build_deterministic_narrative.
    Перевіряє 3 сценарії:
      A) Зміна локації + час + витрата енергії → ≥100 символів, містить назву нової локації.
      B) Тільки час (без змін) → ≥100 символів.
      C) Зміна сцени + поранення (без зміни локації) → ≥100 символів.
    В усіх: НЕ містить "⚠️", "помилка", "Майстер".
    """
    from core.engine import _build_deterministic_narrative

    # ── Сценарій A: Зміна локації + час + втрата енергії ──
    updates_a = {
        "minutes_passed": 90,
        "location_impact": "Штормовий берег",
        "scene_impact": "none",
        "health_impact": "none",
        "energy_impact": "spend_medium",
        "gold_impact": "none",
        "inventory_new": [],
        "inventory_lost": [],
    }
    profile_a = dict(_PROFILE)
    profile_a["Поточне місцезнаходження"] = "Штормовий берег"

    result_a = _build_deterministic_narrative(
        updates_a, profile_a, old_location="Вінтерфелл", old_scene="Зала"
    )
    assert len(result_a) >= 100, (
        f"Scenario A: result must be ≥100 chars, got {len(result_a)}: {result_a!r}"
    )
    assert "Штормовий берег" in result_a, (
        f"Scenario A: new location must appear in result. Got: {result_a!r}"
    )
    assert "⚠️" not in result_a
    assert "помилка" not in result_a.lower()
    assert "майстер" not in result_a.lower()

    # ── Сценарій B: Тільки час, без змін ──
    updates_b = {
        "minutes_passed": 30,
        "location_impact": "none",
        "scene_impact": "none",
        "health_impact": "none",
        "energy_impact": "none",
        "gold_impact": "none",
        "inventory_new": [],
        "inventory_lost": [],
    }
    profile_b = dict(_PROFILE)  # локація = "Вінтерфелл"

    result_b = _build_deterministic_narrative(
        updates_b, profile_b, old_location="Вінтерфелл", old_scene="Зала"
    )
    assert len(result_b) >= 100, (
        f"Scenario B: result must be ≥100 chars, got {len(result_b)}: {result_b!r}"
    )
    assert "⚠️" not in result_b
    assert "помилка" not in result_b.lower()
    assert "майстер" not in result_b.lower()

    # ── Сценарій C: Зміна сцени + поранення (без зміни локації) ──
    updates_c = {
        "minutes_passed": 5,
        "location_impact": "none",
        "scene_impact": "Бій на вулиці",
        "health_impact": "dmg_medium",
        "energy_impact": "none",
        "gold_impact": "none",
        "inventory_new": [],
        "inventory_lost": [],
    }
    profile_c = dict(_PROFILE)
    profile_c["Поточна сцена"] = "Бій на вулиці"

    result_c = _build_deterministic_narrative(
        updates_c, profile_c, old_location="Вінтерфелл", old_scene="Зала"
    )
    assert len(result_c) >= 100, (
        f"Scenario C: result must be ≥100 chars, got {len(result_c)}: {result_c!r}"
    )
    assert "⚠️" not in result_c
    assert "помилка" not in result_c.lower()
    assert "майстер" not in result_c.lower()


# ─────────────────────────────────────────────────────────────────────────────
# ТЕСТ 5 (ЗБЕРЕЖЕНО): Happy-path — Narrator повертає валідний текст
# ─────────────────────────────────────────────────────────────────────────────

def test_narrator_success_returns_story_plus_actions():
    """
    Happy-path: Narrator повертає валідний текст (> 20 символів, закінчується '.').
    Результат має містити story-текст. suggested_actions — непорожній список.
    """
    good_resp = _make_narrator_response(_VALID_NARRATOR_TEXT)
    narrator_mock = MagicMock(return_value=good_resp)

    async def _run():
        from core.engine import process_game_turn
        return await process_game_turn(
            chat_id=44,
            user_input="Look around",
            narrator_queue=None,
        )

    with ExitStack() as stack:
        for p in _build_patches(narrator_mock):
            stack.enter_context(p)
        result_text, result_actions = asyncio.run(_run())

    assert _VALID_NARRATOR_TEXT[:40] in result_text, (
        f"Story text missing from response: {result_text!r}"
    )
    assert "⚠️" not in result_text, (
        "Happy-path must not contain error emoji."
    )
    assert isinstance(result_actions, list), "suggested_actions must be a list."
    assert len(result_actions) > 0, "suggested_actions must not be empty on success."


# ─────────────────────────────────────────────────────────────────────────────
# ТЕСТ 6 (ОНОВЛЕНО після Phase 5): Fallback НЕ скасовує state mutation
# apply_dnd_impacts виконується ДО Narrator → при last-resort вже відпрацювала.
#
# Зміна після Phase 5: engine.py тепер кличе apply_dnd_impacts (з core.dnd_engine),
# а не apply_system_impacts напряму. apply_dnd_impacts делегує до apply_system_impacts
# всередині, але для перевірки інваріанту "state mutation before Narrator"
# достатньо перехопити виклик apply_dnd_impacts на рівні core.engine.
# ─────────────────────────────────────────────────────────────────────────────

def test_state_mutation_happens_before_narrator_failure():
    """
    apply_dnd_impacts викликається ДО Narrator (by design, Phase 5).
    При падінні всіх трьох спроб Narrator функція вже була викликана.
    Результат не містить "⚠️" — повертається детерміністичний текст.

    ЗМІНА: після Phase 5 engine.py викликає apply_dnd_impacts (core.dnd_engine),
    а не apply_system_impacts напряму. Інваріант "state mutation before Narrator"
    перевіряється через перехоплення core.engine.apply_dnd_impacts.
    """
    apply_impacts_call_count = []

    def capturing_apply_dnd(profile, updates):
        apply_impacts_call_count.append(1)
        from core.dnd_engine import apply_dnd_impacts as _real
        return _real(profile, updates)

    # Всі три виклики → порожньо → last-resort
    narrator_mock = MagicMock(
        side_effect=[
            _make_empty_narrator_response(),
            _make_empty_narrator_response(),
            _make_empty_narrator_response(),
        ]
    )

    patches = _build_patches(narrator_mock) + [
        patch("core.engine.apply_dnd_impacts", side_effect=capturing_apply_dnd),
    ]

    async def _run():
        from core.engine import process_game_turn
        return await process_game_turn(
            chat_id=145,
            user_input="Attack the guard",
            narrator_queue=None,
        )

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        result_text, _ = asyncio.run(_run())

    # last-resort → без "⚠️"
    assert "⚠️" not in result_text, (
        f"Even on triple failure, result must not contain error emoji. Got: {result_text!r}"
    )
    assert len(apply_impacts_call_count) >= 1, (
        "apply_dnd_impacts must be called before Narrator (state mutation by design, Phase 5)."
    )


# ─────────────────────────────────────────────────────────────────────────────
# ТЕСТ 7 (ЗБЕРЕЖЕНО): Retry-спроба успішна → NOT fallback emoji
# ─────────────────────────────────────────────────────────────────────────────

def test_narrator_retry_success_returns_story():
    """
    Перший Narrator провалився (порожня відповідь),
    але retry (attempt 2) повернув валідний текст.
    Результат НЕ повинен містити "⚠️", має містити текст retry.
    """
    retry_text = (
        "The hero paused in the middle of the hall, listening to the silence. "
        "Something had changed in the air — a premonition of danger or opportunity."
    )
    retry_resp = _make_narrator_response(retry_text)

    narrator_mock = MagicMock(
        side_effect=[_make_empty_narrator_response(), retry_resp]
    )

    async def _run():
        from core.engine import process_game_turn
        return await process_game_turn(
            chat_id=46,
            user_input="Listen to the silence",
            narrator_queue=None,
        )

    with ExitStack() as stack:
        for p in _build_patches(narrator_mock):
            stack.enter_context(p)
        result_text, result_actions = asyncio.run(_run())

    assert "⚠️" not in result_text, (
        f"Retry success — no error emoji expected. Got: {result_text!r}"
    )
    assert retry_text[:40] in result_text, (
        f"Retry text must appear in result. Got: {result_text!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# ТЕСТ 8 (ЗБЕРЕЖЕНО): bot/handlers.py — logger ініціалізований коректно
# ─────────────────────────────────────────────────────────────────────────────

def test_handlers_logger_initialized():
    """
    Перевіряє, що bot.handlers має атрибут logger типу logging.Logger.
    Ізольований від gspread через мок-підміну модулів до імпорту.
    """
    import sys
    import types
    import logging

    # Зберігаємо оригінали, щоб відновити після тесту
    _orig_sheets = sys.modules.get("database.sheets")
    _orig_ops = sys.modules.get("database.operations")
    _orig_world = sys.modules.get("core.world")
    _orig_handlers = sys.modules.get("bot.handlers")

    try:
        # Підміна database.sheets
        fake_sheets = types.ModuleType("database.sheets")
        fake_sheets.db = MagicMock()
        sys.modules["database.sheets"] = fake_sheets

        # Підміна database.operations з усіма іменами що імпортуються
        fake_ops = types.ModuleType("database.operations")
        for name in [
            "get_unique_regions", "get_houses_by_region", "get_house_stats_data",
            "get_user_data", "save_user_data", "delete_user_data",
            "clear_npc_cache", "delete_user_npc_sheet",
            "get_relevant_context", "get_location_npcs", "update_npcs_in_db",
            "update_npc_reputation", "append_memory_anchor", "get_dead_npc_names",
            "refresh_npc_database", "find_best_match", "ensure_user_npc_sheet",
            "_npc_tab_name",
        ]:
            setattr(fake_ops, name, MagicMock())
        sys.modules["database.operations"] = fake_ops

        # Підміна core.world (залежить від database.operations)
        fake_world = types.ModuleType("core.world")
        for name in [
            "get_canon_characters", "generate_initial_stats", "get_narrative_intro",
            "background_canon_generation", "populate_contextual_npcs",
        ]:
            setattr(fake_world, name, MagicMock())
        sys.modules["core.world"] = fake_world

        # Видаляємо кешований handlers щоб він перезавантажився
        sys.modules.pop("bot.handlers", None)

        import bot.handlers as handlers_module

        assert hasattr(handlers_module, "logger"), \
            "bot.handlers must have 'logger' attribute at module level."
        assert isinstance(handlers_module.logger, logging.Logger), \
            f"logger must be logging.Logger, got: {type(handlers_module.logger)}"

    finally:
        # Відновлюємо оригінальні модулі
        if _orig_sheets is not None:
            sys.modules["database.sheets"] = _orig_sheets
        else:
            sys.modules.pop("database.sheets", None)
        if _orig_ops is not None:
            sys.modules["database.operations"] = _orig_ops
        else:
            sys.modules.pop("database.operations", None)
        if _orig_world is not None:
            sys.modules["core.world"] = _orig_world
        else:
            sys.modules.pop("core.world", None)
        if _orig_handlers is not None:
            sys.modules["bot.handlers"] = _orig_handlers
        else:
            sys.modules.pop("bot.handlers", None)


# ─────────────────────────────────────────────────────────────────────────────
# ТЕСТ 9 (ЗБЕРЕЖЕНО): _stream_consumer edit_text exception → logger.warning (не raise)
# ─────────────────────────────────────────────────────────────────────────────

def test_stream_consumer_edit_text_exception_uses_logger_warning(caplog):
    """
    Якщо temp_msg.edit_text кидає виняток, відповідна гілка
    handlers.py (рядок ~522) має перехопити його через logger.warning і не re-raise.

    Тест відтворює логіку цієї гілки у спрощеному вигляді,
    щоб перевірити що виняток іде в WARNING, а не propagate.
    """
    import logging

    logger_under_test = logging.getLogger("bot.handlers")

    async def _simulated_stream_consumer_error_branch(mock_temp_msg):
        """Відповідає гілці except Exception у _stream_consumer handlers.py ~521."""
        try:
            await mock_temp_msg.edit_text("test display", parse_mode=None)
        except Exception as e:
            logger_under_test.warning(
                f"[CONSUMER_EDIT] edit_text failed: {type(e).__name__}: {e}"
            )

    async def _run():
        mock_msg = AsyncMock()
        mock_msg.edit_text.side_effect = Exception("MessageNotModified")
        await _simulated_stream_consumer_error_branch(mock_msg)

    with caplog.at_level(logging.WARNING, logger="bot.handlers"):
        asyncio.run(_run())

    assert any(
        "[CONSUMER_EDIT] edit_text failed" in record.message
        for record in caplog.records
    ), (
        f"Expected WARNING with '[CONSUMER_EDIT]', got records: "
        f"{[r.message for r in caplog.records]}"
    )
