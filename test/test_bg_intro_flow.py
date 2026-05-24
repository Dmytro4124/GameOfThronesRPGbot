"""
test_bg_intro_flow.py

Тести для фонового intro-паттерну у bot/handlers.py:start_game_with_character.

Перевіряємо три сценарії:
  1. Cache HIT → LLM не викликається, гравець одразу отримує кешований текст.
  2. Cache MISS + LLM success → cache наповнюється, placeholder оновлюється.
  3. LLM failure → fallback залишається, виняток не спливає, кеш порожній.

Стратегія:
- get_user_data, save_user_data, get_house_stats_data → AsyncMock (без Sheets).
- generate_initial_stats → AsyncMock → мінімальний профіль.
- background_canon_generation, populate_contextual_npcs → AsyncMock (без Sheets).
- bot.send_message → AsyncMock, повертає MagicMock-повідомлення з edit_text.
- send_safe_message → AsyncMock.
- get_cached_intro, set_cached_intro → мокаємо безпосередньо.
- get_narrative_intro → AsyncMock → керований результат.
- asyncio.create_task → реальний (дозволяємо задачам виконатись через await asyncio.sleep(0)).

Async-тести запускаються через asyncio.run() (без pytest-asyncio — відповідно до
патерну, прийнятого у test/test_help_command.py та test/test_npc_isolation.py).
"""

import asyncio
import pytest
from contextlib import ExitStack
from unittest.mock import patch, MagicMock, AsyncMock


# ── Мінімальний профіль, що повертає мок generate_initial_stats ───────────────

_PROFILE = {
    "Ім'я": "Тест Герой",
    "Дім": "Старк",
    "class": "Knight",
    "heritage": "Westerosi (Andal)",
    "Поточне місцезнаходження": "Вінтерфелл",
    "Регіон": "Північ",
    "Поточна сцена": "Зала",
    "Енергія": 1000,
    "Здоров'я": 100,
    "Особисте Золото": 50,
    "Ігровий час": "298 рік В.Е., День 1",
    "Інвентар": "Меч",
    "Зброя": "Довгий меч",
    "Броня": "Кольчуга",
    "history": [],
}

_INTRO_DATA_SUCCESS = {
    "narrative_text": "Ви стоїте у Вінтерфеллі. Вітер вие крізь тріщини у кам'яних мурах.",
    "action_prompt": "Що ви робите?",
    "suggested_actions": [
        {"button": "Оглянутись", "intent": "Оглядаю замок"},
        {"button": "Поговорити", "intent": "Знаходжу когось для розмови"},
    ],
}


def _build_bot_mock():
    """Будує мок Bot з send_message, що повертає Message-мок (для edit_text)."""
    bot = MagicMock()
    placeholder_message = MagicMock()
    placeholder_message.edit_text = AsyncMock()
    bot.send_message = AsyncMock(return_value=placeholder_message)
    bot.send_chat_action = AsyncMock()
    return bot, placeholder_message


def _build_patches(
    cached_intro=None,
    llm_intro_data=None,
    llm_raises=False,
):
    """
    Повертає список (name, mock) для використання у ExitStack + patch.

    cached_intro: None → cache miss; str → cache hit.
    llm_intro_data: dict → AsyncMock returns this.
    llm_raises: bool → AsyncMock raises Exception.
    """
    from core.engine import user_sessions

    # generate_initial_stats
    gen_stats_mock = AsyncMock(return_value=_PROFILE)

    # get_narrative_intro
    if llm_raises:
        intro_mock = AsyncMock(side_effect=Exception("LLM 500 INTERNAL"))
    else:
        intro_mock = AsyncMock(return_value=llm_intro_data or _INTRO_DATA_SUCCESS)

    # get_cached_intro (синхронна)
    cached_intro_mock = MagicMock(return_value=cached_intro)

    # set_cached_intro (async)
    set_cached_mock = AsyncMock()

    patches = [
        ("bot.handlers.generate_initial_stats", gen_stats_mock),
        ("bot.handlers.get_house_stats_data", AsyncMock(return_value={"Регіон": "Північ"})),
        ("bot.handlers.save_user_data", AsyncMock(return_value=True)),
        ("bot.handlers.background_canon_generation", AsyncMock()),
        ("bot.handlers.populate_contextual_npcs", AsyncMock()),
        ("bot.handlers.get_narrative_intro", intro_mock),
        ("bot.handlers.get_cached_intro", cached_intro_mock),
        ("bot.handlers.set_cached_intro", set_cached_mock),
        ("bot.handlers.send_safe_message", AsyncMock()),
        ("bot.handlers.get_main_menu", MagicMock(return_value=MagicMock())),
        ("bot.handlers.get_dynamic_menu", MagicMock(return_value=MagicMock())),
    ]
    return patches, gen_stats_mock, intro_mock, cached_intro_mock, set_cached_mock


# ── Тест 1: Cache HIT → LLM не викликається ──────────────────────────────────

def test_cache_hit_no_llm_call():
    """
    Якщо get_cached_intro повертає текст (cache hit),
    get_narrative_intro НЕ повинна викликатись.
    """
    from core.engine import user_sessions

    chat_id = 11111

    async def _run():
        bot, placeholder_msg = _build_bot_mock()

        # Готуємо сесію з house_name
        user_sessions[chat_id] = {"house_name": "Старк", "history": []}

        patches, _, intro_mock, cached_intro_mock, set_cached_mock = _build_patches(
            cached_intro="Кешований вступ для Лицаря."
        )

        with ExitStack() as stack:
            for target, mock_obj in patches:
                stack.enter_context(patch(target, mock_obj))

            from bot.handlers import start_game_with_character
            await start_game_with_character(bot, chat_id, "Тест Герой")

            # Даємо фоновим задачам можливість виконатись
            await asyncio.sleep(0.05)

        # get_narrative_intro НЕ повинна викликатись при cache hit
        intro_mock.assert_not_called()
        # set_cached_intro теж не повинна
        set_cached_mock.assert_not_called()
        # get_cached_intro повинна була викликатись
        cached_intro_mock.assert_called_once()

    asyncio.run(_run())
    # cleanup
    from core.engine import user_sessions
    user_sessions.pop(chat_id, None)


# ── Тест 2: Cache MISS + LLM success → cache наповнюється ────────────────────

def test_cache_miss_llm_success_fills_cache():
    """
    Якщо get_cached_intro повертає None (cache miss) і LLM відповідає успішно,
    set_cached_intro повинна викликатись з правильним текстом.
    """
    from core.engine import user_sessions

    chat_id = 22222

    async def _run():
        bot, placeholder_msg = _build_bot_mock()
        user_sessions[chat_id] = {"house_name": "Старк", "history": []}

        patches, _, intro_mock, cached_intro_mock, set_cached_mock = _build_patches(
            cached_intro=None,
            llm_intro_data=_INTRO_DATA_SUCCESS,
        )

        with ExitStack() as stack:
            for target, mock_obj in patches:
                stack.enter_context(patch(target, mock_obj))

            from bot.handlers import start_game_with_character
            await start_game_with_character(bot, chat_id, "Тест Герой")

            # Даємо фоновій задачі _bg_intro_task час виконатись
            await asyncio.sleep(0.1)

        # LLM повинна викликатись
        intro_mock.assert_called_once()
        # set_cached_intro повинна викликатись з narrative_text (+ action_prompt)
        set_cached_mock.assert_called_once()
        call_args = set_cached_mock.call_args
        saved_text = call_args.args[3] if len(call_args.args) >= 4 else call_args.kwargs.get("intro_text", "")
        assert _INTRO_DATA_SUCCESS["narrative_text"] in saved_text, (
            f"Кешований текст повинен містити narrative_text. Збережено: {repr(saved_text)}"
        )

    asyncio.run(_run())
    from core.engine import user_sessions
    user_sessions.pop(chat_id, None)


# ── Тест 3: LLM failure → fallback залишається, виняток не спливає ───────────

def test_llm_failure_fallback_stays_no_exception():
    """
    Якщо get_narrative_intro кидає виняток,
    set_cached_intro НЕ викликається,
    start_game_with_character завершується без raise.
    """
    from core.engine import user_sessions

    chat_id = 33333

    async def _run():
        bot, placeholder_msg = _build_bot_mock()
        user_sessions[chat_id] = {"house_name": "Старк", "history": []}

        patches, _, intro_mock, cached_intro_mock, set_cached_mock = _build_patches(
            cached_intro=None,
            llm_raises=True,
        )

        with ExitStack() as stack:
            for target, mock_obj in patches:
                stack.enter_context(patch(target, mock_obj))

            from bot.handlers import start_game_with_character
            # Не повинна кидати виняток
            await start_game_with_character(bot, chat_id, "Тест Герой")

            # Даємо _bg_intro_task час завершитись
            await asyncio.sleep(0.1)

        # set_cached_intro НЕ повинна викликатись при помилці LLM
        set_cached_mock.assert_not_called()
        # LLM викликалась (і впала)
        intro_mock.assert_called_once()

    asyncio.run(_run())
    from core.engine import user_sessions
    user_sessions.pop(chat_id, None)
