"""Тести last-resort fallback Narrator: _is_content_block, _fallback_story_from_notes,
_build_deterministic_narrative (3 гілки + content_blocked) та end-to-end прокидання
прапорця content_blocked (blocking / streaming / A/B / ContextVar). Без мережі і Sheets.

Інваріант (оновлено): при content_blocked=True і придатних director_notes гравцю показується
абзац із notes + примітка "Сцену описано скорочено."; при блоці БЕЗ придатних notes --
нейтральна відмова. Службові/механічні notes не показуються ніколи.
"""
import asyncio
import json
from contextlib import ExitStack
from enum import Enum
from types import SimpleNamespace
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

import core.narrator_ab as na
from core.engine import (
    _is_content_block,
    _is_abort_finish,
    _is_block_exception,
    _fallback_story_from_notes,
    _build_deterministic_narrative,
    _FALLBACK_BLOCKED_NOTE,
    _FALLBACK_SHORTENED_NOTE,
    _FALLBACK_STORY_MAX_CHARS,
    _FALLBACK_STORY_MAX_FACTS,
)

SECRET = "ТАЄМНИЙМАРКЕР"
BLOCKED_TEXT = "_Цю сцену неможливо описати детально. Спробуйте іншу дію._"
UNAVAIL_TEXT = "_Детальний опис сцени тимчасово недоступний._"
SHORT_TEXT = "_Сцену описано скорочено._"


class _FR(Enum):
    STOP = 1
    MAX_TOKENS = 2
    SAFETY = 3
    PROHIBITED_CONTENT = 4
    BLOCKLIST = 5
    SPII = 6


def _obj(finish_reason=None, block_reason=None, with_pf=True, with_cand=True):
    cands = [SimpleNamespace(finish_reason=finish_reason)] if with_cand else []
    ns = SimpleNamespace(candidates=cands)
    if with_pf:
        ns.prompt_feedback = SimpleNamespace(block_reason=block_reason)
    return ns


# ============================== 1. _is_content_block ==============================

@pytest.mark.parametrize("fr", [
    _FR.PROHIBITED_CONTENT, _FR.SAFETY, _FR.BLOCKLIST, _FR.SPII,
    "PROHIBITED_CONTENT", "SAFETY", "BLOCKLIST", "SPII",
    "FinishReason.PROHIBITED_CONTENT", "finishreason.safety",
])
def test_is_content_block_true_for_block_finish_reasons(fr):
    assert _is_content_block(_obj(finish_reason=fr)) is True


@pytest.mark.parametrize("fr", [_FR.STOP, _FR.MAX_TOKENS, "STOP", "MAX_TOKENS", "FinishReason.STOP", None])
def test_is_content_block_false_for_normal_finish_reasons(fr):
    assert _is_content_block(_obj(finish_reason=fr)) is False


def test_is_content_block_true_for_prompt_feedback_block_reason():
    assert _is_content_block(_obj(finish_reason=_FR.STOP, block_reason="PROHIBITED_CONTENT")) is True


def test_is_content_block_true_for_block_reason_with_no_candidates():
    assert _is_content_block(_obj(with_cand=False, block_reason="SAFETY")) is True


@pytest.mark.parametrize("br", [None, "", 0])
def test_is_content_block_false_for_empty_block_reason(br):
    assert _is_content_block(_obj(finish_reason=_FR.STOP, block_reason=br)) is False


def test_is_content_block_false_for_none():
    assert _is_content_block(None) is False


def test_is_content_block_false_for_chunk_without_attributes():
    assert _is_content_block(object()) is False
    assert _is_content_block(SimpleNamespace()) is False


def test_is_content_block_false_for_empty_candidates_no_feedback():
    assert _is_content_block(_obj(with_cand=False, with_pf=False)) is False


def test_is_content_block_does_not_raise_on_broken_object():
    class Boom:
        @property
        def candidates(self):
            raise RuntimeError("boom")
    assert _is_content_block(Boom()) is False


@pytest.mark.parametrize("br", ["BLOCKED_REASON_UNSPECIFIED", "BlockedReason.BLOCKED_REASON_UNSPECIFIED",
                                "blocked_reason_unspecified"])
def test_is_content_block_false_for_blocked_reason_unspecified(br):
    assert _is_content_block(_obj(finish_reason=_FR.STOP, block_reason=br)) is False


def test_is_content_block_false_for_enum_unspecified():
    class BR(Enum):
        BLOCKED_REASON_UNSPECIFIED = 0
    # Enum member is truthy (default Enum) -- must still not be treated as a block
    assert _is_content_block(_obj(finish_reason=_FR.STOP, block_reason=BR.BLOCKED_REASON_UNSPECIFIED)) is False


@pytest.mark.parametrize("br", ["SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "OTHER", "IMAGE_SAFETY"])
def test_is_content_block_true_for_real_block_reasons(br):
    assert _is_content_block(_obj(finish_reason=_FR.STOP, block_reason=br)) is True


def test_unspecified_block_reason_with_block_finish_still_blocked():
    assert _is_content_block(_obj(finish_reason=_FR.SAFETY, block_reason="BLOCKED_REASON_UNSPECIFIED")) is True


# ---- _is_abort_finish ----

@pytest.mark.parametrize("fr", [
    "MALFORMED_FUNCTION_CALL", "OTHER", "FinishReason.OTHER", "PROHIBITED_CONTENT", "SAFETY", "BLOCKLIST",
    "SPII", _FR.SAFETY, _FR.PROHIBITED_CONTENT, "finishreason.malformed_function_call",
])
def test_is_abort_finish_true(fr):
    assert _is_abort_finish(fr) is True


@pytest.mark.parametrize("fr", ["STOP", "MAX_TOKENS", "FinishReason.STOP", _FR.STOP, _FR.MAX_TOKENS, None,
                                "FINISH_REASON_UNSPECIFIED", ""])
def test_is_abort_finish_false(fr):
    assert _is_abort_finish(fr) is False


# ---- _is_block_exception ----

class _Boom(Exception):
    pass


@pytest.mark.parametrize("msg", [
    "Response blocked: finish_reason=PROHIBITED_CONTENT",
    "blocked due to SAFETY",
    "block_reason: BLOCKLIST",
    "reason SPII detected",
    "prohibited_content in lowercase",
])
def test_is_block_exception_true_on_marker_text(msg):
    assert _is_block_exception(_Boom(msg)) is True


def test_is_block_exception_true_on_exception_type_name():
    class SafetyError(Exception):
        pass
    assert _is_block_exception(SafetyError("x")) is True


@pytest.mark.parametrize("msg", ["timeout", "503 UNAVAILABLE", "429 RESOURCE_EXHAUSTED", "", "deadline exceeded"])
def test_is_block_exception_false_on_ordinary_errors(msg):
    assert _is_block_exception(RuntimeError(msg)) is False


@pytest.mark.parametrize("msg", [
    "Invalid argument: safety_settings[0].category is not supported",
    "bad safety setting supplied",
    "unknown SAFETY_SETTINGS field",
])
def test_is_block_exception_false_on_config_error_mentioning_safety_settings(msg):
    assert _is_block_exception(ValueError(msg)) is False


@pytest.mark.parametrize("msg", ["finish_reason SAFETY", "blocked: SAFETY", "finish_reason=FinishReason.SAFETY"])
def test_is_block_exception_true_on_safety_finish_reason(msg):
    assert _is_block_exception(RuntimeError(msg)) is True


def test_is_block_exception_does_not_raise_on_broken_str():
    class Bad(Exception):
        def __str__(self):
            raise RuntimeError("no str")
    assert _is_block_exception(Bad()) is False


# ========================== 2. _fallback_story_from_notes ==========================

@pytest.mark.parametrize("empty", [None, [], (), "string not list", 42, {}, ["", "   "], [None, 5]])
def test_notes_empty_or_invalid_returns_empty_string(empty):
    assert _fallback_story_from_notes(empty) == ""


@pytest.mark.parametrize("raw", [
    "Факт: Лорд Старк мовчки дивиться на вогонь.",
    "Note: Лорд Старк мовчки дивиться на вогонь.",
    "Director: Лорд Старк мовчки дивиться на вогонь.",
    "- Лорд Старк мовчки дивиться на вогонь.",
    "• Лорд Старк мовчки дивиться на вогонь.",
    "1. Лорд Старк мовчки дивиться на вогонь.",
    "2) Лорд Старк мовчки дивиться на вогонь.",
    "- Факт 1: Лорд Старк мовчки дивиться на вогонь.",
])
def test_notes_prefixes_and_bullets_stripped(raw):
    assert _fallback_story_from_notes([raw]) == "Лорд Старк мовчки дивиться на вогонь."


def test_notes_markdown_chars_stripped():
    out = _fallback_story_from_notes(["*Лорд* _Старк_ `мовчки` #дивиться > на вогонь."])
    assert out == "Лорд Старк мовчки дивиться на вогонь."
    assert not any(c in out for c in "*_`#>")


def test_notes_correction_skipped_case_insensitive():
    out = _fallback_story_from_notes(["КОРЕКЦІЯ: виправити локацію негайно.", "Корекція: ще щось тут.",
                                      "Лорд Старк мовчки дивиться на вогонь."])
    assert out == "Лорд Старк мовчки дивиться на вогонь."


def test_notes_something_happened_skipped():
    assert _fallback_story_from_notes(["Щось сталося."]) == ""


@pytest.mark.parametrize("mech", [
    "Перевірка DC 15 пройдена гравцем.",
    "Гравець отримує 50 XP за дію.",
    "Гравець втратив 5 HP у бійці.",
    "Кидок d20 дав високий результат.",
    "Шкода становить 2d6 за удар мечем.",
    "Гравець віддав 15 золотих торговцю.",
    "Гравець отримав +5 HP від лікаря.",
])
def test_notes_with_mechanics_skipped(mech):
    assert _fallback_story_from_notes([mech]) == ""


def test_notes_mixed_keeps_only_clean_facts():
    out = _fallback_story_from_notes(["Перевірка DC 15 пройдена гравцем.", "Двері відчинилися зі скрипом."])
    assert out == "Двері відчинилися зі скрипом."


def test_notes_short_notes_skipped():
    assert _fallback_story_from_notes(["Так.", "Ні"]) == ""


def test_notes_period_appended_and_capitalized():
    assert _fallback_story_from_notes(["двері відчинилися зі скрипом"]) == "Двері відчинилися зі скрипом."


@pytest.mark.parametrize("end", [".", "!", "?", "…"])
def test_notes_existing_terminal_punctuation_kept(end):
    assert _fallback_story_from_notes([f"Двері відчинилися зі скрипом{end}"]).endswith(f"скрипом{end}")


def test_notes_max_four_facts():
    notes = [f"Унікальний факт номер {w} відбувся." for w in ("один", "два", "три", "чотири", "п'ять", "шість")]
    out = _fallback_story_from_notes(notes)
    assert out.count("Унікальний") == _FALLBACK_STORY_MAX_FACTS
    assert "п'ять" not in out and "шість" not in out


def test_notes_length_limit_with_ellipsis():
    long_note = " ".join(["слово"] * 400) + "."
    out = _fallback_story_from_notes([long_note, long_note])
    assert len(out) <= _FALLBACK_STORY_MAX_CHARS + 1
    assert out.endswith("…")


def test_notes_non_string_items_ignored():
    assert _fallback_story_from_notes([None, 3, {"a": 1}, "Двері відчинилися зі скрипом."]) == "Двері відчинилися зі скрипом."


def test_notes_accepts_tuple():
    assert _fallback_story_from_notes(("Двері відчинилися зі скрипом.",)) == "Двері відчинилися зі скрипом."


# ================== 3. _build_deterministic_narrative: 3 гілки + безпека ==================

_PROFILE = {"Поточне місцезнаходження": "Вінтерфелл", "Поточна сцена": "Зала"}
_UPD = {"minutes_passed": 10, "location_impact": "none", "scene_impact": "none", "health_impact": "none"}


def test_shortened_note_constant():
    assert _FALLBACK_SHORTENED_NOTE == SHORT_TEXT


def test_branch_content_blocked_with_notes_shows_notes_plus_shortened_note():
    out = _build_deterministic_narrative(_UPD, _PROFILE, "Вінтерфелл", "Зала",
                                         director_notes=[f"{SECRET} відбувається у залі."], content_blocked=True)
    assert out == f"{SECRET} відбувається у залі.\n\n{SHORT_TEXT}"
    assert UNAVAIL_TEXT not in out and BLOCKED_TEXT not in out


@pytest.mark.parametrize("notes", [
    [f"{SECRET} один відбувся у залі.", f"{SECRET} два відбувся у залі."],
    [f"Факт: {SECRET} три.", "- Двері відчинилися зі скрипом."],
])
def test_content_blocked_with_usable_notes_shows_them_with_shortened_note(notes):
    out = _build_deterministic_narrative(_UPD, _PROFILE, "Вінтерфелл", "Зала",
                                         director_notes=notes, content_blocked=True)
    assert SECRET in out
    assert out.endswith(f"\n\n{SHORT_TEXT}")
    assert BLOCKED_TEXT not in out


@pytest.mark.parametrize("notes", [
    None, [], ["", "   "], ["Щось сталося."], ["Перевірка DC 15 пройдена гравцем.", "КОРЕКЦІЯ: виправити."],
    "not a list",
])
def test_content_blocked_without_usable_notes_gives_neutral_refusal(notes):
    out = _build_deterministic_narrative(_UPD, _PROFILE, "Вінтерфелл", "Зала",
                                         director_notes=notes, content_blocked=True)
    assert out == BLOCKED_TEXT == _FALLBACK_BLOCKED_NOTE
    assert SHORT_TEXT not in out


def test_content_blocked_mechanics_note_never_shown_even_when_blocked():
    out = _build_deterministic_narrative(_UPD, _PROFILE, "Вінтерфелл", "Зала",
                                         director_notes=["Гравець отримує 50 XP за дію."], content_blocked=True)
    assert out == BLOCKED_TEXT


def test_content_blocked_ignores_location_and_time_details():
    out = _build_deterministic_narrative({"minutes_passed": 300}, {"Поточне місцезнаходження": "Дорн"},
                                         "Вінтерфелл", "Зала", director_notes=["Двері відчинилися."],
                                         content_blocked=True)
    assert out == f"Двері відчинилися.\n\n{SHORT_TEXT}"
    assert "Дорн" not in out and "Минуло" not in out


def test_content_blocked_no_notes_ignores_location_and_time_details():
    out = _build_deterministic_narrative({"minutes_passed": 300}, {"Поточне місцезнаходження": "Дорн"},
                                         "Вінтерфелл", "Зала", director_notes=None, content_blocked=True)
    assert out == BLOCKED_TEXT


def test_branch_notes_paragraph_plus_unavailable_note():
    out = _build_deterministic_narrative(_UPD, _PROFILE, "Вінтерфелл", "Зала",
                                         director_notes=["Двері відчинилися зі скрипом.", "Хтось кашлянув у кутку."])
    assert out == f"Двері відчинилися зі скрипом. Хтось кашлянув у кутку.\n\n{UNAVAIL_TEXT}"


def test_branch_old_location_changed_no_vestheros_phrase():
    prof = {"Поточне місцезнаходження": "Штормовий берег", "Поточна сцена": "Зала"}
    out = _build_deterministic_narrative({"minutes_passed": 90}, prof, "Вінтерфелл", "Зала", director_notes=None)
    assert "Ви залишили *Вінтерфелл* і вирушили до *Штормовий берег*." in out
    assert "Вестерос" not in out
    assert out.endswith(UNAVAIL_TEXT)


def test_branch_old_when_notes_unusable():
    out = _build_deterministic_narrative(_UPD, _PROFILE, "Вінтерфелл", "Зала",
                                         director_notes=["Щось сталося.", "Перевірка DC 15."])
    assert "Ви перебуваєте у *Вінтерфелл*." in out
    assert "Минуло кілька хвилин." in out
    assert out.endswith(UNAVAIL_TEXT)


def test_branch_nothing_at_all():
    out = _build_deterministic_narrative({}, {}, "", "", director_notes=None)
    assert "Час спливав непомітно у *невідомому місці*." in out
    assert out.endswith(UNAVAIL_TEXT)
    assert "Вестерос" not in out


def test_default_args_backward_compatible():
    out = _build_deterministic_narrative(_UPD, _PROFILE)
    assert out.endswith(UNAVAIL_TEXT)


# ===================== 4. End-to-end прокидання content_blocked =====================

_PROFILE_FULL = {
    "Ім'я": "Тест Герой", "Дім": "Старк", "Титул": "Лорд", "Здоров'я": 100, "Енергія": 800,
    "Особисте Золото": 200, "Бойові навички": 50, "Військові навички": 20, "Інтрига": 15,
    "Управління": 10, "Поточне місцезнаходження": "Вінтерфелл", "Поточна сцена": "Зала",
    "Регіон": "Північ", "Ігровий час": "День 1, Ранок", "Інвентар": "Меч", "Зброя": "Довгий меч",
    "Броня": "Кольчуга", "Транспорт": "Кінь", "Світогляд": "Нейтральний", "Риси": "Хоробрий",
    "Вади": "Упертий", "Вороги": "", "Друзі": "", "Годинники": {"Scene_Tension": "0/4"},
}
_WORKER_UPDATES = {
    "action_type": "standard", "skill_used": "None", "difficulty": 10, "outcome": "SUCCESS",
    "dice_roll": "50", "skill_val": 20, "total_score": 70, "reputation_delta": 0,
    "reputation_target_npc": None, "minutes_passed": 10, "location_impact": "none",
    "scene_impact": "none", "health_impact": "none", "energy_impact": "none",
    "gold_impact": "none", "inventory_new": [], "inventory_lost": [], "clocks_impact": {},
}
_NOTE_TEXT = f"{SECRET} мовчки стоїть біля каміна у великій залі."
_GOOD_TEXT = "Герой обережно ступає кам'яною підлогою великої зали. Смолоскипи кидають тремтливі тіні."


def _gm_resp(note=_NOTE_TEXT):
    r = MagicMock()
    r.text = json.dumps({
        "reasoning": "t", "npc_reasoning": "t", "director_notes": [note],
        "companion_npcs": [], "npc_updates": [],
        "suggested_actions": [{"button": f"A{i}", "intent": f"I{i}"} for i in range(1, 5)],
    }, ensure_ascii=False)
    return r


def _resp(text="", finish_reason=None, block_reason=None):
    cands = [SimpleNamespace(finish_reason=finish_reason)] if finish_reason is not None else []
    return SimpleNamespace(text=text, candidates=cands,
                           prompt_feedback=SimpleNamespace(block_reason=block_reason))


def _blocked():
    return _resp("", "PROHIBITED_CONTENT")


def _empty():
    return _resp("", "STOP")


def _seq(items):
    """side_effect: віддає елементи по черзі, останній повторює (hedging/retry-безпечно)."""
    state = {"i": 0}

    def fn(*a, **k):
        i = min(state["i"], len(items) - 1)
        state["i"] += 1
        it = items[i]
        if isinstance(it, BaseException):
            raise it
        return it
    return fn


def _base_patches(extra_patches=()):
    """Перший 8 патчів ізолюють залежності; append_log (індекс 8) використовується тестом A/B log."""
    return [
        patch("core.engine.get_user_data", new=AsyncMock(return_value=(dict(_PROFILE_FULL), 2))),
        patch("core.engine.save_user_data", new=AsyncMock(return_value=True)),
        patch("core.engine.validate_action", new=AsyncMock(return_value=(True, ""))),
        patch("core.engine.resolve_normal_action",
              new=AsyncMock(return_value=("MECHANICAL VERDICT: SUCCESS", dict(_WORKER_UPDATES)))),
        patch("core.engine.get_location_npcs", return_value=("", [], {})),
        patch("core.engine.get_relevant_context", new=AsyncMock(return_value="")),
        patch("core.engine.get_dead_npc_names", return_value=set()),
        patch("core.engine.model_gm_logic.generate_content", return_value=_gm_resp()),
        patch("core.engine.append_log", new=AsyncMock()),
        patch("core.engine._run_bg_task", side_effect=lambda c: (c.close(), MagicMock())[1]),
        *extra_patches,
    ]


@pytest.fixture(autouse=True)
def _clean():
    na._pending.clear()
    na._last_turn_id.clear()
    na._log_lock = asyncio.Lock()
    yield
    na._pending.clear()
    na._last_turn_id.clear()


def _turn(chat_id, patches, queue=None):
    from core import engine
    from core.engine import process_game_turn

    async def go():
        return await process_game_turn(chat_id, "Оглянути зал", narrator_queue=queue)

    try:
        with ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            return asyncio.run(go())
    finally:
        engine.user_sessions.pop(chat_id, None)


def _story(text):
    return text.split("📊")[0].strip()


def _assert_blocked_with_notes(story):
    """Блок + придатні notes: абзац із notes + примітка 'скорочено' (не відмова, не 'недоступний')."""
    assert SECRET in story
    assert story.endswith(SHORT_TEXT)
    assert BLOCKED_TEXT not in story and UNAVAIL_TEXT not in story


def _blocking_patches(narr_side_effect, ab=False):
    return _base_patches([
        patch("core.engine.NARRATOR_AB_ENABLED", ab),
        patch("core.engine.model_narrator.generate_content", MagicMock(side_effect=narr_side_effect)),
    ])


# ---- blocking ----

def test_e2e_blocking_all_attempts_blocked_gives_notes_with_shortened_note():
    text, _ = _turn(1001, _blocking_patches(_seq([_blocked()])))
    _assert_blocked_with_notes(_story(text))


def test_e2e_blocking_block_reason_from_prompt_feedback():
    text, _ = _turn(1002, _blocking_patches(_seq([_resp("", "STOP", block_reason="PROHIBITED_CONTENT")])))
    _assert_blocked_with_notes(_story(text))


def test_e2e_blocking_unspecified_block_reason_is_not_a_block():
    text, _ = _turn(1040, _blocking_patches(_seq([_resp("", "STOP", block_reason="BLOCKED_REASON_UNSPECIFIED")])))
    story = _story(text)
    assert SECRET in story and story.endswith(UNAVAIL_TEXT)
    assert SHORT_TEXT not in story


def test_e2e_blocking_blocked_without_usable_notes_gives_neutral_refusal():
    patches = _blocking_patches(_seq([_blocked()]))
    patches[7] = patch("core.engine.model_gm_logic.generate_content", return_value=_gm_resp("Щось сталося."))
    text, _ = _turn(1041, patches)
    assert _story(text) == BLOCKED_TEXT
    assert SHORT_TEXT not in text


def test_e2e_blocking_block_in_exception_text_sets_blocked_flag():
    text, _ = _turn(1042, _blocking_patches(_seq([RuntimeError("Blocked: finish_reason=PROHIBITED_CONTENT"), _empty()])))
    _assert_blocked_with_notes(_story(text))


def test_e2e_blocking_attempt2_block_exception_gives_fallback_not_raven():
    """Attempt 2 кидає виняток з блок-маркером -> fallback (notes + 'скорочено'), не generic-помилка."""
    text, _ = _turn(1044, _blocking_patches(_seq([_empty(), RuntimeError("finish_reason PROHIBITED_CONTENT")])))
    story = _story(text)
    _assert_blocked_with_notes(story)
    assert "Ворон" not in text


def test_e2e_blocking_block_exception_in_attempt3_only_is_sticky():
    text, _ = _turn(1043, _blocking_patches(_seq([_empty(), _empty(), RuntimeError("SAFETY block"), _empty()])))
    _assert_blocked_with_notes(_story(text))


def test_e2e_blocking_empty_without_block_gives_notes_text():
    text, _ = _turn(1003, _blocking_patches(_seq([_empty()])))
    story = _story(text)
    assert SECRET in story and story.endswith(UNAVAIL_TEXT)
    assert "неможливо описати" not in story


def test_e2e_blocking_exception_without_block_gives_notes_text():
    text, _ = _turn(1004, _blocking_patches(_seq([RuntimeError("timeout"), _empty()])))
    story = _story(text)
    assert SECRET in story and story.endswith(UNAVAIL_TEXT)


def test_e2e_blocking_flag_is_sticky_block_in_first_attempt_only():
    text, _ = _turn(1005, _blocking_patches(_seq([_blocked(), _empty()])))
    _assert_blocked_with_notes(_story(text))


def test_e2e_blocking_flag_is_sticky_block_in_last_attempt_only():
    calls = {"n": 0}

    def fn(*a, **k):
        calls["n"] += 1
        return _blocked() if calls["n"] >= 3 else _empty()

    text, _ = _turn(1006, _blocking_patches(fn))
    assert calls["n"] >= 3
    _assert_blocked_with_notes(_story(text))


def test_e2e_blocking_good_text_not_replaced():
    text, _ = _turn(1007, _blocking_patches(_seq([_resp(_GOOD_TEXT, "STOP")])))
    assert "Герой обережно ступає" in text
    assert BLOCKED_TEXT not in text and UNAVAIL_TEXT not in text


# ---- streaming ----

def _chunk(finish_reason=None, text=""):
    return SimpleNamespace(
        text=text,
        candidates=[SimpleNamespace(finish_reason=finish_reason, content=SimpleNamespace(parts=[]))],
        prompt_feedback=SimpleNamespace(block_reason=None),
    )


def _drain(q):
    items = []
    while True:
        try:
            it = q.get_nowait()
        except asyncio.QueueEmpty:
            break
        items.append(it)
        if it is None:
            break
    return items


def _stream_patches(stream_side_effect, blocking):
    return _base_patches([
        patch("core.engine.model_narrator.generate_content_stream", MagicMock(side_effect=stream_side_effect)),
        patch("core.engine.model_narrator.generate_content", MagicMock(side_effect=blocking)),
    ])


def test_e2e_streaming_blocked_chunk_gives_notes_with_shortened_note():
    q = asyncio.Queue()
    text, _ = _turn(1010, _stream_patches(
        [iter([_chunk("PROHIBITED_CONTENT")]), iter([])], _seq([_empty()])), queue=q)
    items = _drain(q)
    pushed = [i for i in items if i is not None]
    assert len(pushed) == 1
    _assert_blocked_with_notes(pushed[0])
    assert items[-1] is None
    _assert_blocked_with_notes(_story(text))


def test_e2e_streaming_block_in_retry_stream_is_sticky():
    q = asyncio.Queue()
    _turn(1011, _stream_patches([iter([]), iter([_chunk("SAFETY")])], _seq([_empty()])), queue=q)
    pushed = [i for i in _drain(q) if i is not None]
    assert len(pushed) == 1
    _assert_blocked_with_notes(pushed[0])


def test_e2e_streaming_block_only_in_attempt3_blocking_response():
    q = asyncio.Queue()
    text, _ = _turn(1012, _stream_patches([iter([]), iter([])], _seq([_blocked()])), queue=q)
    pushed = [i for i in _drain(q) if i is not None]
    assert len(pushed) == 1
    _assert_blocked_with_notes(pushed[0])


def test_e2e_streaming_empty_without_block_gives_notes_text():
    q = asyncio.Queue()
    _turn(1013, _stream_patches([iter([_chunk("STOP")]), iter([])], _seq([_empty()])), queue=q)
    pushed = [i for i in _drain(q) if i is not None]
    assert len(pushed) == 1 and SECRET in pushed[0] and pushed[0].endswith(UNAVAIL_TEXT)


def test_e2e_streaming_exception_in_attempt3_without_block_gives_notes_text():
    q = asyncio.Queue()
    _turn(1014, _stream_patches([iter([]), iter([])], _seq([RuntimeError("deadline")])), queue=q)
    pushed = [i for i in _drain(q) if i is not None]
    assert len(pushed) == 1 and SECRET in pushed[0]


# ---- A/B ----

def _ab_patches(main_effect, alt_effect):
    return _base_patches([
        patch("core.engine.NARRATOR_AB_ENABLED", True),
        patch("core.engine.model_narrator.generate_content", MagicMock(side_effect=main_effect)),
        patch("core.engine.model_narrator_alt.generate_content", MagicMock(side_effect=alt_effect)),
    ])


def test_e2e_ab_both_failed_with_block_gives_notes_with_shortened_note_not_a_vote_variant():
    text, actions = _turn(1020, _ab_patches(_seq([_blocked()]), _seq([_blocked()])))
    _assert_blocked_with_notes(_story(text))
    assert na.get_pending(1020) is None  # fallback не стає варіантом голосування
    assert len(actions) == 4


def test_e2e_ab_block_in_only_one_chain_still_blocked_flow():
    text, _ = _turn(1021, _ab_patches(_seq([_blocked()]), _seq([_empty()])))
    _assert_blocked_with_notes(_story(text))
    assert na.get_pending(1021) is None


def test_e2e_ab_both_failed_without_block_gives_notes_text():
    text, _ = _turn(1022, _ab_patches(_seq([_empty()]), _seq([_empty()])))
    story = _story(text)
    assert SECRET in story and story.endswith(UNAVAIL_TEXT)
    assert na.get_pending(1022) is None


def test_e2e_ab_both_failed_logs_both_failed_reason():
    patches = _ab_patches(_seq([_blocked()]), _seq([_blocked()]))
    log_mock = patches[8].new  # append_log AsyncMock
    _turn(1023, patches)
    log_mock.assert_awaited_once()
    rec = log_mock.await_args.args[0]
    assert rec["reason"] == "both_failed" and rec["vote"] is None


def test_e2e_ab_one_chain_ok_other_blocked_shows_single_variant_not_fallback():
    text, _ = _turn(1024, _ab_patches(_seq([_resp(_GOOD_TEXT, "STOP")]), _seq([_blocked()])))
    assert "Герой обережно ступає" in text
    assert BLOCKED_TEXT not in text
    assert na.get_pending(1024) is None


# ---- ContextVar не протікає між ходами ----

def test_e2e_contextvar_flag_does_not_leak_to_next_turn_same_task():
    """Два послідовні ходи в одному Task: 1-й заблокований, 2-й порожній без блоку -> notes-гілка."""
    from core.engine import process_game_turn
    from core import engine

    state = {"turn": 1}

    def narr(*a, **k):
        return _blocked() if state["turn"] == 1 else _empty()

    async def go():
        r1 = await process_game_turn(1030, "Оглянути зал", narrator_queue=None)
        state["turn"] = 2
        r2 = await process_game_turn(1030, "Оглянути зал", narrator_queue=None)
        return r1, r2

    try:
        with ExitStack() as st:
            for p in _blocking_patches(narr):
                st.enter_context(p)
            (t1, _), (t2, _) = asyncio.run(go())
    finally:
        engine.user_sessions.pop(1030, None)
    _assert_blocked_with_notes(_story(t1))
    s2 = _story(t2)
    assert SECRET in s2 and s2.endswith(UNAVAIL_TEXT)
    assert SHORT_TEXT not in s2 and "неможливо описати" not in s2


def test_e2e_contextvar_fresh_dict_per_turn():
    from core.engine import _narr_diag_var, process_game_turn
    from core import engine
    seen = []

    async def go():
        for _ in range(2):
            await process_game_turn(1031, "Оглянути зал", narrator_queue=None)
            seen.append(_narr_diag_var.get())

    try:
        with ExitStack() as st:
            for p in _blocking_patches(_seq([_blocked()])):
                st.enter_context(p)
            asyncio.run(go())
    finally:
        engine.user_sessions.pop(1031, None)
    assert seen[0] is not seen[1]
    assert seen[0]["content_blocked"] is True and seen[1]["content_blocked"] is True


def test_run_narrator_chain_without_diag_var_does_not_crash_on_block():
    """Прямий виклик _run_narrator_chain (без process_game_turn): _narr_diag_var=None -> no-op."""
    from core.engine import _run_narrator_chain
    wrapper = MagicMock()
    wrapper.generate_content.side_effect = _seq([_blocked()])
    wrapper.model_name = "x"

    async def go():
        with patch("core.engine.hedged_generate_content_async", AsyncMock(return_value=_blocked())):
            return await _run_narrator_chain(wrapper, "PROMPT", "gemma")

    r = asyncio.run(go())
    assert r.used_fallback is True and r.text is None
