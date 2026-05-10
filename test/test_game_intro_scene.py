# test/test_game_intro_scene.py
"""
Тести для scene-sync моделі (Варіант B):
- build_game_intro_prompt: нова сигнатура з current_scene, <scene_constraint>, scene_check
- get_narrative_intro: прокидання current_scene, soft-warn при mismatch
- generate_initial_stats: fallback Поточна сцена через LOCATION_SCENES hub[0]

Async-тести запускаються через asyncio.run() (без pytest-asyncio — відповідно до
патерну, прийнятого у test/test_npc_isolation.py).

Примітка щодо ізоляції від gspread/Sheets:
  core.world імпортує database.sheets на рівні модуля, що тригерить GoogleSheetsDB()
  і вимагає SPREADSHEET_ID. Для ізоляції тестів 3-5 ми патчимо sys.modules
  перед першим import core.world, щоб підставити заглушку замість реального db.
"""

import asyncio
import json
import sys
import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Утиліта: встановити заглушку database.sheets у sys.modules ДО того як
# core.world буде вперше завантажено (або вже завантажено попередніми тестами).
# ---------------------------------------------------------------------------

def _ensure_sheets_mock():
    """
    Якщо database.sheets ще не у sys.modules — вставляємо мок-модуль,
    щоб GoogleSheetsDB не намагалась підключитись до реальних Sheets.
    Якщо вже є (попередній тест вже зробив import) — просто повертаємо.
    """
    if "database.sheets" not in sys.modules:
        fake_sheets = MagicMock()
        fake_sheets.db = MagicMock()
        sys.modules["database.sheets"] = fake_sheets
    if "database.operations" not in sys.modules:
        fake_ops = MagicMock()
        fake_ops.refresh_npc_database = MagicMock(return_value=None)
        fake_ops.find_best_match = MagicMock(return_value=None)
        fake_ops.ensure_user_npc_sheet = MagicMock(return_value=None)
        fake_ops._npc_tab_name = MagicMock(return_value="NPC_0")
        sys.modules["database.operations"] = fake_ops


# Викликаємо одразу під час збору, щоб core.world не встиг імпортуватись
# реальним чином до першого тесту, що його використовує.
_ensure_sheets_mock()


# ===========================================================================
# Тест 1: build_game_intro_prompt містить <scene_constraint> і scene_check
# ===========================================================================

def test_intro_prompt_contains_scene_constraint():
    """
    build_game_intro_prompt("{}","Ланніспорт","Головна Пристань") повертає
    рядок, що містить:
    - "START_SCENE: Головна Пристань" (точно)
    - "scene_check" (ключ у JSON-шаблоні)
    - "<scene_constraint>" (XML-тег)
    """
    from core.prompts import build_game_intro_prompt

    result = build_game_intro_prompt(
        profile_json="{}",
        current_location="Ланніспорт",
        current_scene="Головна Пристань",
    )

    assert isinstance(result, str), "build_game_intro_prompt повинна повертати рядок"
    assert "START_SCENE: Головна Пристань" in result, (
        f"Промпт не містить 'START_SCENE: Головна Пристань'.\n"
        f"Перші 500 символів: {result[:500]}"
    )
    assert "scene_check" in result, (
        "Промпт не містить ключа 'scene_check' у JSON-шаблоні"
    )
    assert "<scene_constraint>" in result, (
        "Промпт не містить XML-тегу '<scene_constraint>'"
    )


# ===========================================================================
# Тест 2: build_game_intro_prompt — виклик з 2 аргументами кидає TypeError
# ===========================================================================

def test_intro_prompt_signature_three_args_required():
    """
    Старий 2-аргументний виклик build_game_intro_prompt(profile_json, current_location)
    є навмисним breaking change і повинен кидати TypeError.
    Це захищає від call-sites, які не оновились.
    """
    from core.prompts import build_game_intro_prompt

    with pytest.raises(TypeError):
        build_game_intro_prompt("{}", "Ланніспорт")  # бракує current_scene


# ===========================================================================
# Тест 3: get_narrative_intro передає current_scene як третій аргумент
# ===========================================================================

def test_get_narrative_intro_passes_scene():
    """
    get_narrative_intro(profile) з profile["Поточна сцена"] = "Тронний Зал"
    повинна викликати build_game_intro_prompt з third arg = "Тронний Зал".

    Стратегія:
    - patch core.world.build_game_intro_prompt на capture-функцію
    - patch core.world.model.generate_content повертає валідний JSON з scene_check
    - patch core.world.clean_and_parse_json щоб уникнути реального парсингу
    """
    captured_calls = []

    def fake_build(profile_json, current_location, current_scene):
        captured_calls.append({
            "profile_json": profile_json,
            "current_location": current_location,
            "current_scene": current_scene,
        })
        return "FAKE_PROMPT"

    valid_intro = {
        "scene_check": "Тронний Зал",
        "narrative_text": "Зала велична.",
        "action_prompt": "Що робите?",
        "suggested_actions": [],
    }

    fake_response = MagicMock()
    fake_response.text = json.dumps(valid_intro, ensure_ascii=False)

    profile = {
        "Ім'я": "Тест",
        "Поточна сцена": "Тронний Зал",
        "Поточне місцезнаходження": "Королівська Гавань",
    }

    import core.world  # вже з мок-sheets у sys.modules
    with patch.object(core.world, "build_game_intro_prompt", fake_build), \
         patch.object(core.world, "model") as mock_model, \
         patch.object(core.world, "clean_and_parse_json", return_value=valid_intro):
        mock_model.generate_content.return_value = fake_response
        asyncio.run(core.world.get_narrative_intro(profile))

    assert len(captured_calls) == 1, (
        f"build_game_intro_prompt має бути викликана рівно 1 раз, "
        f"викликана {len(captured_calls)} разів"
    )
    assert captured_calls[0]["current_scene"] == "Тронний Зал", (
        f"Третій аргумент (current_scene) повинен бути 'Тронний Зал', "
        f"отримано: {repr(captured_calls[0]['current_scene'])}"
    )


# ===========================================================================
# Тест 4: get_narrative_intro логує ⚠️ [SCENE MISMATCH] при розсинхроні
# ===========================================================================

def test_scene_mismatch_logs_warning(capsys):
    """
    Якщо LLM повертає scene_check="Двір", а profile["Поточна сцена"]="Тронний Зал"
    → у stdout з'являється "⚠️ [SCENE MISMATCH]".

    Також перевірка: якщо scene_check="" — warning НЕ друкується
    (порожній scene_check ≠ mismatch, це просто невідповідь).
    """
    import core.world

    # --- Сценарій A: scene_check відрізняється → warning ---
    mismatch_parsed = {
        "scene_check": "Двір",
        "narrative_text": "Ви у дворі.",
        "action_prompt": "Що робите?",
        "suggested_actions": [],
    }

    fake_response_mismatch = MagicMock()
    fake_response_mismatch.text = json.dumps(mismatch_parsed, ensure_ascii=False)

    profile_mismatch = {
        "Ім'я": "Тест",
        "Поточна сцена": "Тронний Зал",
        "Поточне місцезнаходження": "Королівство Корони",
    }

    with patch.object(core.world, "build_game_intro_prompt", lambda pj, loc, sc: "FAKE"), \
         patch.object(core.world, "model") as mock_model_a, \
         patch.object(core.world, "clean_and_parse_json", return_value=mismatch_parsed):
        mock_model_a.generate_content.return_value = fake_response_mismatch
        asyncio.run(core.world.get_narrative_intro(profile_mismatch))

    captured_a = capsys.readouterr()
    assert "⚠️ [SCENE MISMATCH]" in captured_a.out, (
        f"При scene_check='Двір' ≠ expected='Тронний Зал' "
        f"очікувався ⚠️ [SCENE MISMATCH] у stdout.\n"
        f"stdout: {repr(captured_a.out)}"
    )

    # --- Сценарій B: scene_check="" → warning НЕ друкується ---
    empty_check_parsed = {
        "scene_check": "",
        "narrative_text": "Щось там.",
        "action_prompt": "Що робите?",
        "suggested_actions": [],
    }

    fake_response_empty = MagicMock()
    fake_response_empty.text = json.dumps(empty_check_parsed, ensure_ascii=False)

    profile_empty_check = {
        "Ім'я": "Тест2",
        "Поточна сцена": "Тронний Зал",
        "Поточне місцезнаходження": "Королівство Корони",
    }

    with patch.object(core.world, "build_game_intro_prompt", lambda pj, loc, sc: "FAKE"), \
         patch.object(core.world, "model") as mock_model_b, \
         patch.object(core.world, "clean_and_parse_json", return_value=empty_check_parsed):
        mock_model_b.generate_content.return_value = fake_response_empty
        asyncio.run(core.world.get_narrative_intro(profile_empty_check))

    captured_b = capsys.readouterr()
    assert "⚠️ [SCENE MISMATCH]" not in captured_b.out, (
        "При scene_check='' warning НЕ повинен з'являтися у stdout.\n"
        f"stdout: {repr(captured_b.out)}"
    )


# ===========================================================================
# Тест 5: generate_initial_stats fallback — hub[0] замість назви локації
# ===========================================================================

def test_fallback_uses_hub_scene():
    """
    Якщо LLM повертає profile БЕЗ "Поточна сцена" (або порожнє значення),
    generate_initial_stats повинна заповнити profile["Поточна сцена"]
    першою hub-сценою з LOCATION_SCENES для відповідної локації.

    Підтест A: location="Ланніспорт" → profile["Поточна сцена"] == "Головна Пристань"
      (LOCATION_SCENES["Ланніспорт"]["hub"][0])

    Підтест B (legacy): LOCATION_SCENES пустий (патчимо) → Поточна сцена = назва локації
    """
    import core.world
    from core.world_constants import LOCATION_SCENES

    # --- Підтест A: Ланніспорт з реальним LOCATION_SCENES ---
    expected_hub_scene = LOCATION_SCENES["Ланніспорт"]["hub"][0]
    assert expected_hub_scene == "Головна Пристань", (
        f"Очікувана hub[0] для Ланніспорту = 'Головна Пристань', "
        f"отримано '{expected_hub_scene}'"
    )

    # LLM повертає profile без Поточна сцена
    profile_no_scene = {
        "Ім'я": "Тест",
        "Поточне місцезнаходження": "Ланніспорт",
        "Регіон": "Вестерлянди",
        # Поточна сцена відсутня
    }

    fake_resp_a = MagicMock()
    fake_resp_a.text = json.dumps(profile_no_scene, ensure_ascii=False)

    house_data_a = {"Регіон": "Вестерлянди"}

    with patch.object(core.world, "model_gm_logic") as mock_gm_a, \
         patch.object(core.world, "clean_and_parse_json", return_value=dict(profile_no_scene)):
        mock_gm_a.generate_content.return_value = fake_resp_a
        result_a = asyncio.run(
            core.world.generate_initial_stats("ТестГерой", "Ланністер", house_data_a)
        )

    assert result_a is not None, "generate_initial_stats повернула None замість profile"
    assert result_a.get("Поточна сцена") == "Головна Пристань", (
        f"Fallback для Ланніспорту: очікувалась 'Головна Пристань', "
        f"отримано '{result_a.get('Поточна сцена')}'"
    )

    # --- Підтест B: legacy fallback (LOCATION_SCENES без локації → scene = loc) ---
    profile_empty_scene = {
        "Ім'я": "ТестЛегасі",
        "Поточне місцезнаходження": "Ланніспорт",
        "Регіон": "Вестерлянди",
        "Поточна сцена": "",  # порожня — тригер fallback
    }

    fake_resp_b = MagicMock()
    fake_resp_b.text = json.dumps(profile_empty_scene, ensure_ascii=False)

    # Патчимо LOCATION_SCENES на порожній словник → жодного hub → legacy гілка
    with patch.object(core.world, "model_gm_logic") as mock_gm_b, \
         patch.object(core.world, "clean_and_parse_json", return_value=dict(profile_empty_scene)), \
         patch("core.world.LOCATION_SCENES", {}):
        mock_gm_b.generate_content.return_value = fake_resp_b
        result_b = asyncio.run(
            core.world.generate_initial_stats("ТестЛегасі", "Ланністер", {"Регіон": "Вестерлянди"})
        )

    assert result_b is not None, "generate_initial_stats (legacy path) повернула None"
    loc_b = result_b.get("Поточне місцезнаходження")
    scene_b = result_b.get("Поточна сцена")
    assert scene_b == loc_b, (
        f"Legacy fallback: Поточна сцена повинна == локація. "
        f"Поточна сцена='{scene_b}', Поточне місцезнаходження='{loc_b}'"
    )
