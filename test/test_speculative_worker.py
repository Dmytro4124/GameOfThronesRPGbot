"""test_speculative_worker.py

Integration тести для speculative Worker pattern у core/engine.py:process_game_turn.

Покриває два критичних шляхи:
  1. Censor passes  → Worker result використовується (happy-path speculative).
  2. Censor blocks  → Worker cancelled, повертається refusal-рядок, не story.

Стратегія мокування — аналогічна test_narrator_fallback.py:
  - asyncio.create_task патчити НЕ потрібно: speculative pattern покладається
    на реальний asyncio.create_task для запуску Censor+Worker у EventLoop.
    Ми мокуємо саму корутину-функцію, а не task scheduling.
  - _run_bg_task → no-op (щоб background_task не звертався до Sheets).
  - Жодного реального LLM чи Google Sheets.
"""

import asyncio
import pytest
from contextlib import ExitStack
from unittest.mock import patch, MagicMock, AsyncMock


# ─── Мінімальний профіль ────────────────────────────────────────────────────
_PROFILE = {
    "Ім'я": "Тест Герой",
    "Дім": "Старк",
    "Титул": "Лорд",
    "Здоров'я": 100,
    "Енергія": 800,
    "Особисте Золото": 200,
    "Бойові навички": 50,
    "Військові навички": 20,
    "Поточне місцезнаходження": "Вінтерфелл",
    "Поточна сцена": "Зала",
    "Регіон": "Північ",
    "Ігровий час": "298 рік В.Е., 1-й місяць, День 1, 08:00",
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
    "mode": "NORMAL",
}

# ─── Мінімальний механічний результат ──────────────────────────────────────
_WORKER_UPDATES = {
    "action_type": "standard",
    "skill_used": "None",
    "difficulty": 15,
    "outcome": "SUCCESS",
    "dice_roll": "12",
    "skill_val": 3,
    "total_score": 15,
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

_NARRATOR_TEXT = (
    "The hero stepped through the gates of Winterfell into the grey northern morning. "
    "The cold air bit his cheeks as he surveyed the courtyard, guards nodding in recognition."
)


def _make_gm_response():
    r = MagicMock()
    r.text = _GM_LOGIC_JSON
    return r


def _make_narrator_response(text: str = _NARRATOR_TEXT):
    r = MagicMock()
    r.text = text
    return r


def _build_base_patches(
    validate_action_mock,
    resolve_normal_action_mock,
    narrator_mock=None,
):
    """Будує список patch()-об'єктів, спільних для обох тестів.
    narrator_mock: якщо None — використовуємо стандартну відповідь.
    """
    if narrator_mock is None:
        narrator_mock = MagicMock(return_value=_make_narrator_response())

    return [
        patch(
            "core.engine.get_user_data",
            new=AsyncMock(return_value=(_PROFILE.copy(), 2)),
        ),
        patch("core.engine.validate_action", new=validate_action_mock),
        patch("core.engine.resolve_normal_action", new=resolve_normal_action_mock),
        patch("core.engine.get_location_npcs", return_value=("", [], {})),
        patch("core.engine.get_relevant_context", new=AsyncMock(return_value="")),
        patch("core.engine.get_dead_npc_names", return_value=set()),
        patch(
            "core.engine.model_gm_logic.generate_content",
            return_value=_make_gm_response(),
        ),
        patch("core.engine.model_narrator.generate_content", narrator_mock),
        patch("core.engine._run_bg_task", return_value=MagicMock()),
    ]


# ─────────────────────────────────────────────────────────────────────────────
# ТЕСТ 1: Censor passes → Worker результат використовується
# ─────────────────────────────────────────────────────────────────────────────

def test_speculative_censor_passes_worker_result_used():
    """
    Censor повертає (True, "") → Worker result використовується.
    Очікування:
      - Результат містить Narrator-текст (не refusal).
      - resolve_normal_action викликається рівно 1 раз (speculative task).
      - validate_action викликається рівно 1 раз.
    """
    # Лічильники для перевірки кількості викликів
    censor_calls = []
    worker_calls = []

    async def _censor(user_input, profile):
        censor_calls.append(user_input)
        return True, ""

    async def _worker(user_input, profile, npc_rep=None, **kwargs):
        worker_calls.append(user_input)
        return "MECHANICAL VERDICT: SUCCESS", _WORKER_UPDATES.copy()

    patches = _build_base_patches(
        validate_action_mock=_censor,
        resolve_normal_action_mock=_worker,
    )

    async def _run():
        from core.engine import process_game_turn
        return await process_game_turn(
            chat_id=9001,
            user_input="Walk to the courtyard",
            narrator_queue=None,
        )

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        result_text, result_actions = asyncio.run(_run())

    # Повернуто не refusal
    assert "🛑" not in result_text, (
        f"Censor-pass path must NOT contain block marker. Got: {result_text!r}"
    )
    assert "Цензором" not in result_text, (
        f"Censor-pass path must NOT contain refusal text. Got: {result_text!r}"
    )

    # Narrator-текст присутній
    assert _NARRATOR_TEXT[:40] in result_text, (
        f"Worker result path must contain narrator story. Got: {result_text!r}"
    )

    # Обидва виклики відбулись рівно по 1 разу
    assert len(censor_calls) == 1, (
        f"validate_action must be called exactly once. Got {len(censor_calls)} calls."
    )
    assert len(worker_calls) == 1, (
        f"resolve_normal_action must be called exactly once. Got {len(worker_calls)} calls."
    )

    # suggested_actions — непорожній список
    assert isinstance(result_actions, list) and len(result_actions) > 0, (
        "suggested_actions must be non-empty on success."
    )


# ─────────────────────────────────────────────────────────────────────────────
# ТЕСТ 2: Censor blocks → Worker cancelled, повертається refusal
# ─────────────────────────────────────────────────────────────────────────────

def test_speculative_censor_blocks_returns_refusal():
    """
    Censor повертає (False, "Дія заборонена.") → Worker скасовується.
    Очікування:
      - Результат містить refusal-текст і маркер блокування "🛑".
      - Результат НЕ містить Narrator-текст.
      - suggested_actions = [] (порожній список — handler очікує це при refusal).
      - resolve_normal_action викликається 1 раз speculatively, але результат відкидається.
    """
    refusal_text = "Дія заборонена: анахронізм."
    censor_calls = []
    worker_calls = []
    worker_cancel_event = asyncio.Event() if False else None  # placeholder

    async def _blocking_censor(user_input, profile):
        censor_calls.append(user_input)
        return False, refusal_text

    async def _worker(user_input, profile, npc_rep=None, **kwargs):
        worker_calls.append(user_input)
        # Симулюємо тривалу роботу — Worker може бути ще в процесі
        await asyncio.sleep(0)  # yield щоб Censor міг завершитись першим
        return "SHOULD NOT BE USED", _WORKER_UPDATES.copy()

    patches = _build_base_patches(
        validate_action_mock=_blocking_censor,
        resolve_normal_action_mock=_worker,
    )

    async def _run():
        from core.engine import process_game_turn
        return await process_game_turn(
            chat_id=9002,
            user_input="Use a pistol",  # явний анахронізм
            narrator_queue=None,
        )

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        result_text, result_actions = asyncio.run(_run())

    # Refusal текст присутній
    assert refusal_text in result_text, (
        f"Refusal text must be in result. Got: {result_text!r}"
    )

    # Маркер блокування присутній
    assert "🛑" in result_text, (
        f"Block marker '🛑' must be in result. Got: {result_text!r}"
    )

    # Narrator-текст відсутній (Worker скасовано)
    assert _NARRATOR_TEXT[:40] not in result_text, (
        f"On Censor block, narrator story must NOT appear. Got: {result_text!r}"
    )

    # Порожній список дій при блокуванні
    assert result_actions == [], (
        f"suggested_actions must be [] on Censor block. Got: {result_actions!r}"
    )

    # Censor викликався 1 раз
    assert len(censor_calls) == 1, (
        f"validate_action must be called once. Got {len(censor_calls)} calls."
    )


# ─────────────────────────────────────────────────────────────────────────────
# ТЕСТ 3: Censor exception → fail-open, Worker result використовується
# ─────────────────────────────────────────────────────────────────────────────

def test_speculative_censor_exception_fails_open_uses_worker():
    """If validate_action raises, fail-open: is_valid=True, refusal=''.
    Worker task result should still be used (no refusal returned to user).

    Перевіряємо fail-open path:
      - validate_action піднімає Exception замість (bool, str).
      - Engine логує warning і продовжує з is_valid=True, refusal=''.
      - Результат містить Narrator-текст, а не refusal-маркер.
      - resolve_normal_action викликається рівно 1 раз (speculative task).
    """
    worker_calls = []

    async def _raising_censor(user_input, profile):
        raise RuntimeError("Simulated Censor network failure")

    async def _worker(user_input, profile, npc_rep=None, **kwargs):
        worker_calls.append(user_input)
        return "MECHANICAL VERDICT: SUCCESS", _WORKER_UPDATES.copy()

    patches = _build_base_patches(
        validate_action_mock=_raising_censor,
        resolve_normal_action_mock=_worker,
    )

    async def _run():
        from core.engine import process_game_turn
        return await process_game_turn(
            chat_id=9003,
            user_input="Talk to the guard",
            narrator_queue=None,
        )

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        result_text, result_actions = asyncio.run(_run())

    # Fail-open: НЕ повинен повернути refusal-маркер
    assert "🛑" not in result_text, (
        f"Censor-exception fail-open must NOT block action. Got: {result_text!r}"
    )
    assert "Цензором" not in result_text, (
        f"Censor-exception fail-open must NOT contain refusal text. Got: {result_text!r}"
    )

    # Narrator-текст присутній (Worker result використаний)
    assert _NARRATOR_TEXT[:40] in result_text, (
        f"Fail-open path must use Worker result and show narrator story. Got: {result_text!r}"
    )

    # Worker викликався рівно 1 раз
    assert len(worker_calls) == 1, (
        f"resolve_normal_action must be called exactly once. Got {len(worker_calls)} calls."
    )

    # suggested_actions — непорожній список
    assert isinstance(result_actions, list) and len(result_actions) > 0, (
        "suggested_actions must be non-empty on fail-open success."
    )
