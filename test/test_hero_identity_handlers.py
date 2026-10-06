"""bot/handlers.py + bot/menus.py: перемикач статі, превʼю/профіль зі статтю й родиною,
seed_family_reputation у initial_world_setup. Реальний bot.handlers (як test_bg_intro_flow)."""
import asyncio
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.menus import build_ability_preview_keyboard
from core.engine import user_sessions

CID = 424242
FAM = [{"name": "Кейтлін Старк", "relation": "мати"}]


def _profile(**kw):
    p = {
        "Ім'я": "Тест Герой", "Дім": "Старк", "class": "Knight", "heritage": "Westerosi (Andal)",
        "ability_scores": {"STR": 15, "DEX": 10, "CON": 14, "INT": 10, "WIS": 12, "CHA": 11},
        "level": 1, "xp": 0, "hp_current": 10, "hp_max": 10, "ac": 12, "proficiency_bonus": 2,
        "Поточне місцезнаходження": "Вінтерфелл", "Регіон": "Північ", "Поточна сцена": "Зала",
        "Особисте Золото": 5, "Зброя": "Меч", "Броня": "Кольчуга", "Інвентар": "Факел",
        "features": [], "conditions": [], "mode": "NORMAL", "Світогляд": "x", "Титул": "x",
        "Ігровий час": "298", "saves_proficient": [], "skill_profs": [], "skill_expertise": [],
    }
    p.update(kw)
    return p


def _call():
    call = MagicMock()
    call.message.chat.id = CID
    call.message.edit_text = AsyncMock()
    call.answer = AsyncMock()
    return call


@pytest.fixture(autouse=True)
def _clean():
    user_sessions.pop(CID, None)
    yield
    user_sessions.pop(CID, None)


# ---------------- keyboard ----------------

def _cbs(kb):
    return [b.callback_data for row in kb.inline_keyboard for b in row]


def test_keyboard_has_gender_button_when_requested():
    cbs = _cbs(build_ability_preview_keyboard(show_gender_toggle=True))
    assert "gender_toggle" in cbs and "ability_accept" in cbs and "ability_redistribute" in cbs


def test_keyboard_default_has_no_gender_button():
    assert "gender_toggle" not in _cbs(build_ability_preview_keyboard())


# ---------------- callback_gender_toggle ----------------

@pytest.mark.parametrize("start,expected", [("", "чоловіча"), ("чоловіча", "жіноча"), ("жіноча", "чоловіча")])
def test_toggle_cycle(start, expected):
    from bot.handlers import callback_gender_toggle
    user_sessions[CID] = {"state": "WAITING_ABILITY_PREVIEW", "temp_profile": _profile(Стать=start),
                          "auto_roll_profile": _profile(Стать=start)}
    call = _call()
    asyncio.run(callback_gender_toggle(call))
    assert user_sessions[CID]["temp_profile"]["Стать"] == expected
    call.message.edit_text.assert_awaited_once()
    call.answer.assert_awaited()


def test_toggle_syncs_auto_roll_profile():
    from bot.handlers import callback_gender_toggle
    user_sessions[CID] = {"state": "WAITING_ABILITY_PREVIEW", "temp_profile": _profile(Стать="чоловіча"),
                          "auto_roll_profile": _profile(Стать="чоловіча")}
    asyncio.run(callback_gender_toggle(_call()))
    assert user_sessions[CID]["auto_roll_profile"]["Стать"] == "жіноча"


_MIXED_FAM = [
    {"name": "Кейтлін Старк", "relation": "мати"},
    {"name": "Робб Старк", "relation": "брат"},
    {"name": "Тайрек Ланністер", "relation": "дружина"},
    {"name": "Едмур Таллі", "relation": "чоловік"},
]


@pytest.mark.parametrize("which", ["temp_profile", "auto_roll_profile"])
def test_toggle_drops_spouses_keeps_others(which):
    from bot.handlers import callback_gender_toggle
    user_sessions[CID] = {
        "state": "WAITING_ABILITY_PREVIEW",
        "temp_profile": _profile(Стать="чоловіча", Родина=[dict(m) for m in _MIXED_FAM]),
        "auto_roll_profile": _profile(Стать="чоловіча", Родина=[dict(m) for m in _MIXED_FAM]),
    }
    asyncio.run(callback_gender_toggle(_call()))
    fam = user_sessions[CID][which]["Родина"]
    assert [m["relation"] for m in fam] == ["мати", "брат"]


def test_toggle_edit_text_shows_gender_and_keyboard():
    from bot.handlers import callback_gender_toggle
    user_sessions[CID] = {"state": "WAITING_ABILITY_PREVIEW", "temp_profile": _profile(Стать="чоловіча")}
    call = _call()
    asyncio.run(callback_gender_toggle(call))
    args, kwargs = call.message.edit_text.call_args
    assert "жіноча" in args[0]
    assert "gender_toggle" in _cbs(kwargs["reply_markup"])


@pytest.mark.parametrize("state", ["GAME_ACTIVE", "WAITING_ABILITY_REDISTRIBUTION", None])
def test_toggle_outside_preview_noop(state):
    from bot.handlers import callback_gender_toggle
    sess = {"temp_profile": _profile(Стать="чоловіча")}
    if state:
        sess["state"] = state
    user_sessions[CID] = sess
    call = _call()
    asyncio.run(callback_gender_toggle(call))
    assert user_sessions[CID]["temp_profile"]["Стать"] == "чоловіча"
    call.message.edit_text.assert_not_called()
    call.answer.assert_awaited_once()


def test_toggle_without_session_noop():
    from bot.handlers import callback_gender_toggle
    call = _call()
    asyncio.run(callback_gender_toggle(call))
    call.message.edit_text.assert_not_called()


def test_toggle_edit_failure_does_not_raise_and_still_toggles():
    from bot.handlers import callback_gender_toggle
    user_sessions[CID] = {"state": "WAITING_ABILITY_PREVIEW", "temp_profile": _profile()}
    call = _call()
    call.message.edit_text.side_effect = RuntimeError("message not modified")
    asyncio.run(callback_gender_toggle(call))
    assert user_sessions[CID]["temp_profile"]["Стать"] == "чоловіча"


# ---------------- preview / profile text ----------------

def test_preview_text_unknown_gender():
    from bot.handlers import _build_ability_preview_text
    t = _build_ability_preview_text(_profile())
    assert "Стать" in t and "не визначено" in t and "Родина" not in t


def test_preview_text_gender_and_family():
    from bot.handlers import _build_ability_preview_text
    t = _build_ability_preview_text(_profile(Стать="жіноча", Родина=FAM))
    assert "жіноча" in t and "Родина" in t and "мати" in t and "Кейтлін Старк" in t


def test_profile_text_shows_gender_and_family():
    from bot.handlers import _build_dnd_profile_text
    t = _build_dnd_profile_text(_profile(Стать="чоловіча", Родина=FAM), chat_id=1)
    assert "Стать" in t and "чоловіча" in t and "Родина" in t and "Кейтлін Старк" in t


def test_profile_text_old_profile_without_fields():
    from bot.handlers import _build_dnd_profile_text
    t = _build_dnd_profile_text(_profile(), chat_id=1)
    assert "Стать" not in t and "Родина" not in t


# ---------------- initial_world_setup -> seed_family_reputation ----------------

def _finalise(profile, seed_mock):
    async def _run():
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MagicMock(delete=AsyncMock()))
        bot.send_chat_action = AsyncMock()
        user_sessions[CID] = {"house_name": "Старк", "history": []}
        canon = AsyncMock()
        with ExitStack() as st:
            for tgt, m in [
                ("bot.handlers.get_house_stats_data", AsyncMock(return_value={"Регіон": "Північ"})),
                ("bot.handlers.save_user_data", AsyncMock(return_value=True)),
                ("bot.handlers.background_canon_generation", canon),
                ("bot.handlers.populate_contextual_npcs", AsyncMock()),
                ("bot.handlers.get_narrative_intro", AsyncMock(return_value={"narrative_text": "t"})),
                ("bot.handlers.get_cached_intro", MagicMock(return_value="cached")),
                ("bot.handlers.set_cached_intro", AsyncMock()),
                ("bot.handlers.send_safe_message", AsyncMock()),
                ("bot.handlers.get_main_menu", MagicMock(return_value=MagicMock())),
                ("bot.handlers.get_dynamic_menu", MagicMock(return_value=MagicMock())),
                ("database.operations.seed_family_reputation", seed_mock),
            ]:
                st.enter_context(patch(tgt, m))
            from bot.handlers import _finalise_character_and_start
            await _finalise_character_and_start(bot, CID, profile)
            await asyncio.sleep(0.1)
        return canon

    return asyncio.run(_run())


def test_seed_called_with_family():
    seed = AsyncMock(return_value=["Кейтлін Старк"])
    _finalise(_profile(Стать="жіноча", Родина=FAM), seed)
    seed.assert_awaited_once()
    assert seed.call_args[0][1] == FAM


@pytest.mark.parametrize("extra", [{}, {"Родина": []}, {"Родина": None}])
def test_seed_not_called_without_family(extra):
    seed = AsyncMock()
    _finalise(_profile(**extra), seed)
    seed.assert_not_called()


def test_seed_exception_does_not_break_world_setup():
    seed = AsyncMock(side_effect=RuntimeError("sheets down"))
    _finalise(_profile(Родина=FAM), seed)
    seed.assert_awaited_once()
    # initial_world_setup дійшов до кінця (finally) -> стан розблоковано
    assert user_sessions[CID]["state"] == "GAME_ACTIVE"


def test_intro_cache_lookup_receives_gender():
    captured = MagicMock(return_value="cached")
    seed = AsyncMock()

    async def _go():
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MagicMock(delete=AsyncMock()))
        user_sessions[CID] = {"house_name": "Старк", "history": []}
        with ExitStack() as st:
            for tgt, m in [
                ("bot.handlers.save_user_data", AsyncMock(return_value=True)),
                ("bot.handlers.background_canon_generation", AsyncMock()),
                ("bot.handlers.populate_contextual_npcs", AsyncMock()),
                ("bot.handlers.get_cached_intro", captured),
                ("bot.handlers.send_safe_message", AsyncMock()),
                ("bot.handlers.get_main_menu", MagicMock(return_value=MagicMock())),
                ("database.operations.seed_family_reputation", seed),
            ]:
                st.enter_context(patch(tgt, m))
            from bot.handlers import _finalise_character_and_start
            await _finalise_character_and_start(bot, CID, _profile(Стать="жіноча"))
            await asyncio.sleep(0.05)

    asyncio.run(_go())
    # get_cached_intro(user_id, profile): стать читається з профілю
    assert captured.call_args[0][1]["Стать"] == "жіноча"
