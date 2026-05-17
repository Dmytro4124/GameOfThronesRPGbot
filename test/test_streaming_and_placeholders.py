"""
test_streaming_and_placeholders.py

Нові тести для двох груп:

Група A — Streaming-path (narrator_queue=asyncio.Queue()):
  1. test_streaming_third_attempt_pushes_to_queue
     Шари 1+2 (generate_content_stream) повертають порожній стрім,
     Шар 3 (generate_content) повертає текст ≥ 50 символів.
     Перевірка: перший не-None елемент у черзі — це текст Шару 3.

  2. test_streaming_last_resort_pushes_to_queue
     Всі 3 шари провалились → last-resort детерміністичний текст
     пушиться в чергу; він ≥ 100 символів, не містить "⚠️"/"помилка"/"майстер".

  3. test_streaming_first_attempt_success_no_extra_push
     Happy-path: перший generate_content_stream повертає валідні чанки.
     У черзі немає дублюючого full-text пушу — тільки None-сентинел.

Група B — Placeholder-детектор (blocking mode, narrator_queue=None):
  4. test_placeholder_with_brackets_triggers_retry
     Відповідь з "[Опис сцени]" (довжина > 100) → визнається placeholder → retry.
     generate_content викликано ≥ 2 разів.

  5. test_placeholder_english_marker_triggers_retry
     Відповідь з "INSERT STORY HERE" → також placeholder → retry.
     generate_content викликано ≥ 2 разів.

Стратегія мокування:
- generate_content_stream → повертає ітератор chunk-заглушок.
- generate_content → MagicMock з .text.
- Усі зовнішні залежності (gspread, DB, validate_action) ізольовані
  через _build_patches (той самий хелпер, що в test_narrator_fallback.py).
"""

import asyncio
import pytest
from contextlib import ExitStack
from unittest.mock import patch, MagicMock, AsyncMock


# ─── Спільні константи ────────────────────────────────────────────────────────

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


def _make_empty_stream():
    """Повертає generate_content_stream-мок що видає порожній ітератор."""
    mock = MagicMock()
    mock.return_value = iter([])  # порожній стрім → story=""
    return mock


def _make_chunk_stream(texts: list):
    """
    Повертає generate_content_stream-мок, що видає chunks із .text.
    Chunk-заглушки використовують просту гілку AttributeError:
      chunk.candidates[0] підніматиме AttributeError/IndexError →
      fallback гілка: text = chunk.text if chunk.text else "".
    """
    chunks = []
    for t in texts:
        c = MagicMock()
        c.candidates = []   # IndexError при chunk.candidates[0]
        c.text = t
        chunks.append(c)
    mock = MagicMock()
    mock.return_value = iter(chunks)
    return mock


def _build_patches(
    narrator_stream_mock=None,
    narrator_blocking_mock=None,
) -> list:
    """
    Базовий набір патчів для ізоляції process_game_turn.

    narrator_stream_mock   — замінює model_narrator.generate_content_stream
    narrator_blocking_mock — замінює model_narrator.generate_content

    Якщо будь-який None — не патчиться (поведінка за замовчуванням).
    """
    patches = [
        patch(
            "core.engine.get_user_data",
            new=AsyncMock(return_value=(_PROFILE.copy(), 2)),
        ),
        patch(
            "core.engine.validate_action",
            new=AsyncMock(return_value=(True, "")),
        ),
        patch(
            "core.engine.resolve_action_mechanics",
            new=AsyncMock(return_value=("MECHANICAL VERDICT: SUCCESS", _WORKER_UPDATES.copy())),
        ),
        patch("core.engine.get_location_npcs", return_value=("", [], {})),
        patch("core.engine.get_relevant_context", new=AsyncMock(return_value="")),
        patch("core.engine.get_dead_npc_names", return_value=set()),
        patch(
            "core.engine.model_gm_logic.generate_content",
            return_value=_make_gm_response(),
        ),
        # no-op asyncio.create_task → не запускати background_task з реальним Sheets
        patch("core.engine.asyncio.create_task", return_value=MagicMock()),
    ]
    if narrator_stream_mock is not None:
        patches.append(
            patch("core.engine.model_narrator.generate_content_stream", narrator_stream_mock)
        )
    if narrator_blocking_mock is not None:
        patches.append(
            patch("core.engine.model_narrator.generate_content", narrator_blocking_mock)
        )
    return patches


# ─── Група A: Streaming-path ──────────────────────────────────────────────────

def test_streaming_third_attempt_pushes_to_queue():
    """
    Streaming-режим. Шари 1+2 (generate_content_stream) повертають порожній стрім.
    Шар 3 (generate_content) повертає текст ≥ 50 символів.
    Перевіряємо: перший не-None елемент у черзі — текст Шару 3,
    після нього одразу None-сентинел.
    """
    third_attempt_text = (
        "Вартовий насторожено дивиться на вас крізь залізні грати воріт. "
        "Повітря пахне вогкістю каменю та смолоскипів."
    )
    assert len(third_attempt_text) >= 50, "Test setup: third text must be ≥ 50 chars"

    # Шари 1 і 2 → порожній стрім (два виклики generate_content_stream)
    stream_mock = MagicMock(side_effect=[iter([]), iter([])])

    # Шар 3 → blocking generate_content
    # generate_content викликається також у Шар 3: перша спроба raise Exception
    # (щоб перейти до безконфігового fallback), друга — успіх.
    blocking_mock = MagicMock(return_value=_make_narrator_response(third_attempt_text))

    queue = asyncio.Queue()

    async def _run():
        from core.engine import process_game_turn
        return await process_game_turn(
            chat_id=201,
            user_input="Підійти до воріт",
            narrator_queue=queue,
        )

    with ExitStack() as stack:
        for p in _build_patches(
            narrator_stream_mock=stream_mock,
            narrator_blocking_mock=blocking_mock,
        ):
            stack.enter_context(p)
        asyncio.run(_run())

    # Зчитуємо всі елементи черги до None
    items = []
    while True:
        try:
            item = queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        items.append(item)
        if item is None:
            break

    # Має бути хоча б один не-None елемент перед None-сентинелом
    non_none = [i for i in items if i is not None]
    assert len(non_none) >= 1, (
        f"Queue must contain at least one non-None item before sentinel. "
        f"Got items: {items!r}"
    )

    # Останній елемент — None
    assert items[-1] is None, (
        f"Last item in queue must be None sentinel. Got: {items[-1]!r}"
    )

    # Перший не-None елемент містить текст Шару 3
    assert third_attempt_text[:30] in non_none[0], (
        f"First pushed text must contain third-attempt text. Got: {non_none[0]!r}"
    )


def test_streaming_last_resort_pushes_to_queue():
    """
    Streaming-режим. Усі три шари провалились:
    - Шари 1+2: generate_content_stream → порожній стрім
    - Шар 3: generate_content → порожній текст
    Перевіряємо: в черзі є детерміністичний текст ≥ 100 символів,
    без "⚠️", "помилка", "майстер", після нього None-сентинел.
    """
    stream_mock = MagicMock(side_effect=[iter([]), iter([])])
    blocking_mock = MagicMock(return_value=_make_empty_narrator_response())

    queue = asyncio.Queue()

    async def _run():
        from core.engine import process_game_turn
        return await process_game_turn(
            chat_id=202,
            user_input="Оглянути зал",
            narrator_queue=queue,
        )

    with ExitStack() as stack:
        for p in _build_patches(
            narrator_stream_mock=stream_mock,
            narrator_blocking_mock=blocking_mock,
        ):
            stack.enter_context(p)
        asyncio.run(_run())

    # Зчитуємо всі елементи
    items = []
    while True:
        try:
            item = queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        items.append(item)
        if item is None:
            break

    non_none = [i for i in items if i is not None]
    assert len(non_none) >= 1, (
        f"Queue must contain at least one non-None item (last-resort text). "
        f"Got items: {items!r}"
    )

    last_resort_text = non_none[0]
    assert len(last_resort_text) >= 100, (
        f"Last-resort text must be ≥ 100 chars. Got {len(last_resort_text)} chars: "
        f"{last_resort_text!r}"
    )
    assert "⚠️" not in last_resort_text, (
        f"Last-resort text must NOT contain '⚠️'. Got: {last_resort_text!r}"
    )
    assert "помилка" not in last_resort_text.lower(), (
        f"Last-resort text must NOT contain 'помилка'. Got: {last_resort_text!r}"
    )
    assert "майстер" not in last_resort_text.lower(), (
        f"Last-resort text must NOT contain 'майстер'. Got: {last_resort_text!r}"
    )

    # None-сентинел в кінці
    assert items[-1] is None, (
        f"Last item must be None sentinel. Got: {items[-1]!r}"
    )


def test_streaming_first_attempt_success_no_extra_push():
    """
    Streaming happy-path: перший generate_content_stream повертає валідні chunks.
    Перевіряємо: у черзі НЕ має бути окремого full-text пушу після чанків —
    тільки None-сентинел.

    У streaming happy-path engine не пушить зібраний текст повторно:
    кожен chunk вже потрапив до черги під час стрімінгу.
    Єдине, що має бути після останнього чанка — None (сигнал завершення).
    """
    chunk_text_1 = "Зала Вінтерфелла огорнута тінями. "
    chunk_text_2 = "Вартові стоять нерухомо біля вогнища."
    full_text_combined = chunk_text_1 + chunk_text_2

    stream_mock = _make_chunk_stream([chunk_text_1, chunk_text_2])

    queue = asyncio.Queue()

    async def _run():
        from core.engine import process_game_turn
        return await process_game_turn(
            chat_id=203,
            user_input="Дивлюсь навкруги",
            narrator_queue=queue,
        )

    with ExitStack() as stack:
        for p in _build_patches(narrator_stream_mock=stream_mock):
            stack.enter_context(p)
        asyncio.run(_run())

    # Зчитуємо всі елементи
    items = []
    while True:
        try:
            item = queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        items.append(item)
        if item is None:
            break

    # None-сентинел присутній
    assert items[-1] is None, (
        f"Last item must be None sentinel. Got: {items[-1]!r}"
    )

    non_none = [i for i in items if i is not None]

    # Чанки дійшли до черги
    assert len(non_none) >= 1, (
        f"At least chunk items should be in queue. Got: {items!r}"
    )

    # Перевіряємо, що після чанків немає дублюючого full-text (повного тексту обʼєднаного)
    # Дублюючий пуш виглядатиме як один елемент що рівний full_text_combined.
    duplicate_pushes = [item for item in non_none if item == full_text_combined]
    assert len(duplicate_pushes) == 0, (
        f"Found duplicate full-text push in queue: {duplicate_pushes!r}. "
        f"All items: {items!r}"
    )


# ─── Група B: Placeholder-детектор (blocking mode) ───────────────────────────

def test_placeholder_with_brackets_triggers_retry():
    """
    Blocking mode (narrator_queue=None).
    Відповідь Attempt 1 містить '[Опис сцени]' (довжина > 100) → placeholder → retry.
    Перевіряємо: generate_content викликано ≥ 2 разів.
    """
    # Текст з placeholder-маркером '[опис' (engine перевіряє lower())
    placeholder_text = (
        "[Опис сцени] Герой входить до зали. Тут відбувається щось важливе. "
        "Розпишіть детально всі події цієї сцени відповідно до контексту гри."
    )
    assert len(placeholder_text) > 100, "Test setup: placeholder text must be > 100 chars"
    assert "[опис" in placeholder_text.lower(), "Test setup: must contain '[опис' marker"

    # Attempt 1 → placeholder; Attempt 2 → реальна відповідь (щоб pipeline завершився)
    retry_text = (
        "Зала Вінтерфелла порожня. Вогонь тріщить у каміні. "
        "Холодне повітря просочується крізь щілини старовинних стін."
    )
    narrator_mock = MagicMock(
        side_effect=[
            _make_narrator_response(placeholder_text),   # attempt 1: placeholder
            _make_narrator_response(retry_text),          # attempt 2: retry success
        ]
    )

    async def _run():
        from core.engine import process_game_turn
        return await process_game_turn(
            chat_id=204,
            user_input="Увійти до зали",
            narrator_queue=None,
        )

    patches = _build_patches(narrator_blocking_mock=narrator_mock)
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        asyncio.run(_run())

    assert narrator_mock.call_count >= 2, (
        f"generate_content must be called ≥ 2 times (attempt 1 + retry). "
        f"Called {narrator_mock.call_count} times."
    )


def test_placeholder_english_marker_triggers_retry():
    """
    Blocking mode (narrator_queue=None).
    Відповідь Attempt 1 містить 'INSERT STORY HERE' → placeholder → retry.
    Перевіряємо: generate_content викликано ≥ 2 разів.
    """
    placeholder_text = (
        "INSERT STORY HERE. The character does something important in the scene. "
        "Please fill in the narrative for this moment in the game world."
    )
    assert len(placeholder_text) > 100, "Test setup: placeholder text must be > 100 chars"
    assert "insert story" in placeholder_text.lower(), "Test setup: must contain 'insert story' marker"

    retry_text = (
        "The hall of Winterfell is silent. Fire crackles in the hearth. "
        "Cold air seeps through the ancient stone walls, carrying whispers of the past."
    )
    narrator_mock = MagicMock(
        side_effect=[
            _make_narrator_response(placeholder_text),   # attempt 1: placeholder
            _make_narrator_response(retry_text),          # attempt 2: retry success
        ]
    )

    async def _run():
        from core.engine import process_game_turn
        return await process_game_turn(
            chat_id=205,
            user_input="Look around the hall",
            narrator_queue=None,
        )

    patches = _build_patches(narrator_blocking_mock=narrator_mock)
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        asyncio.run(_run())

    assert narrator_mock.call_count >= 2, (
        f"generate_content must be called ≥ 2 times (attempt 1 + retry). "
        f"Called {narrator_mock.call_count} times."
    )
