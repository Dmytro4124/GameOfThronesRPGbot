"""Unit tests for bot/menus.py :: build_asi_picker_keyboard (4-fix package).

Tests focus on the math/layout of the keyboard builder, not full aiogram E2E.
No mocking of Telegram or bot is needed — build_asi_picker_keyboard is a pure
function that returns an InlineKeyboardMarkup.

Covers:
- Returns InlineKeyboardMarkup (type check).
- 6-ability layout: 6 rows of 3 buttons each, plus 1 budget row, plus 1 action row.
- '+' button for STR enabled when sum_used=0 and STR=13 (< 20).
- '+' button disabled (shows ' ' with 'noop') when ability is at cap (score=20).
- '+' button disabled when sum_used == max_total (budget exhausted).
- '-' button enabled when used[STR] > 0, disabled otherwise.
- Footer row shows 'Витрачено: N/2' info.
- Action row has 'Підтвердити' when sum_used == max_total.
- Action row has 'Скинути' always.
- No 'Скасувати' button — ASI is mandatory.
"""

import pytest
from aiogram.types import InlineKeyboardMarkup

from bot.menus import build_asi_picker_keyboard


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_BASE_SCORES = {
    "STR": 13, "DEX": 10, "CON": 12,
    "INT": 10, "WIS": 11, "CHA": 14,
}
_ABILITY_KEYS_ORDERED = ["STR", "DEX", "CON", "INT", "WIS", "CHA"]
_ZERO_USED = {ab: 0 for ab in _ABILITY_KEYS_ORDERED}


def _all_buttons(kb: InlineKeyboardMarkup) -> list:
    """Flatten all InlineKeyboardButton from InlineKeyboardMarkup."""
    buttons = []
    for row in kb.inline_keyboard:
        buttons.extend(row)
    return buttons


def _button_texts(kb: InlineKeyboardMarkup) -> list[str]:
    return [btn.text for btn in _all_buttons(kb)]


def _button_callbacks(kb: InlineKeyboardMarkup) -> list[str]:
    return [btn.callback_data for btn in _all_buttons(kb)]


# ---------------------------------------------------------------------------
# 1. Return type
# ---------------------------------------------------------------------------

def test_returns_inline_keyboard_markup():
    kb = build_asi_picker_keyboard(_BASE_SCORES, _ZERO_USED)
    assert isinstance(kb, InlineKeyboardMarkup)


# ---------------------------------------------------------------------------
# 2. Layout: 6 ability rows (3 buttons each) + 1 budget row + 1 action row
# ---------------------------------------------------------------------------

def test_total_rows_count():
    """Should have 6 ability rows + 1 budget row + 1 action row = 8 rows."""
    kb = build_asi_picker_keyboard(_BASE_SCORES, _ZERO_USED)
    assert len(kb.inline_keyboard) == 8, (
        f"Expected 8 rows (6 ability + 1 budget + 1 action), "
        f"got {len(kb.inline_keyboard)}: {[[b.text for b in r] for r in kb.inline_keyboard]}"
    )


def test_ability_rows_have_3_buttons_each():
    """Each of the first 6 rows must have exactly 3 buttons."""
    kb = build_asi_picker_keyboard(_BASE_SCORES, _ZERO_USED)
    for i, row in enumerate(kb.inline_keyboard[:6]):
        assert len(row) == 3, (
            f"Ability row {i} must have 3 buttons, got {len(row)}: {[b.text for b in row]}"
        )


def test_budget_row_has_1_button():
    """Row 7 (index 6) is the budget label — exactly 1 button."""
    kb = build_asi_picker_keyboard(_BASE_SCORES, _ZERO_USED)
    budget_row = kb.inline_keyboard[6]
    assert len(budget_row) == 1, (
        f"Budget row must have 1 button, got {len(budget_row)}"
    )


# ---------------------------------------------------------------------------
# 3. '+' button enabled/disabled logic
# ---------------------------------------------------------------------------

def test_plus_str_enabled_when_below_cap_and_budget_available():
    """STR=13, sum_used=0 < 2: '+' for STR must be active callback, not 'noop'."""
    kb = build_asi_picker_keyboard(_BASE_SCORES, _ZERO_USED)
    # STR is the first ability row (row 0). Buttons: [dec, label, inc]
    str_row = kb.inline_keyboard[0]
    inc_btn = str_row[2]
    assert inc_btn.callback_data == "asi_inc_STR", (
        f"STR '+' must be 'asi_inc_STR' when budget available and score < 20. "
        f"Got: {inc_btn.callback_data!r}"
    )
    assert inc_btn.text == "+", f"Expected '+' text, got {inc_btn.text!r}"


def test_plus_disabled_when_ability_at_cap():
    """STR=20: '+' button must be disabled (callback='noop', text=' ')."""
    scores = dict(_BASE_SCORES)
    scores["STR"] = 20
    kb = build_asi_picker_keyboard(scores, _ZERO_USED)
    str_row = kb.inline_keyboard[0]
    inc_btn = str_row[2]
    assert inc_btn.callback_data == "noop", (
        f"STR at cap 20: '+' must be 'noop'. Got: {inc_btn.callback_data!r}"
    )


def test_plus_disabled_when_budget_exhausted():
    """sum_used=2 (budget full): ALL '+' buttons must be disabled."""
    used = {"STR": 1, "DEX": 1, "CON": 0, "INT": 0, "WIS": 0, "CHA": 0}
    kb = build_asi_picker_keyboard(_BASE_SCORES, used, max_total=2)
    for i, ab in enumerate(_ABILITY_KEYS_ORDERED):
        inc_btn = kb.inline_keyboard[i][2]
        assert inc_btn.callback_data == "noop", (
            f"{ab} '+' must be disabled when budget is exhausted (sum_used=2), "
            f"got callback: {inc_btn.callback_data!r}"
        )


# ---------------------------------------------------------------------------
# 4. '-' button enabled/disabled logic
# ---------------------------------------------------------------------------

def test_minus_disabled_when_used_is_zero():
    """STR used=0: '-' must be disabled."""
    kb = build_asi_picker_keyboard(_BASE_SCORES, _ZERO_USED)
    str_row = kb.inline_keyboard[0]
    dec_btn = str_row[0]
    assert dec_btn.callback_data == "noop", (
        f"STR '-' must be 'noop' when used=0. Got: {dec_btn.callback_data!r}"
    )


def test_minus_enabled_when_used_gt_zero():
    """STR used=1: '-' must be enabled."""
    used = dict(_ZERO_USED)
    used["STR"] = 1
    kb = build_asi_picker_keyboard(_BASE_SCORES, used)
    str_row = kb.inline_keyboard[0]
    dec_btn = str_row[0]
    assert dec_btn.callback_data == "asi_dec_STR", (
        f"STR '-' must be 'asi_dec_STR' when used=1. Got: {dec_btn.callback_data!r}"
    )


# ---------------------------------------------------------------------------
# 5. Centre label shows pending delta
# ---------------------------------------------------------------------------

def test_centre_label_shows_delta_when_used():
    """STR used=1: label must show '+1' indicator."""
    used = dict(_ZERO_USED)
    used["STR"] = 1
    kb = build_asi_picker_keyboard(_BASE_SCORES, used)
    str_row = kb.inline_keyboard[0]
    label_btn = str_row[1]
    assert "+1" in label_btn.text, (
        f"Centre label must show pending delta '+1' when used=1. Got: {label_btn.text!r}"
    )


def test_centre_label_no_delta_when_zero():
    """STR used=0: label must NOT show '+'."""
    kb = build_asi_picker_keyboard(_BASE_SCORES, _ZERO_USED)
    str_row = kb.inline_keyboard[0]
    label_btn = str_row[1]
    assert "+" not in label_btn.text, (
        f"Centre label must not show '+' delta when used=0. Got: {label_btn.text!r}"
    )


# ---------------------------------------------------------------------------
# 6. Footer: budget label contains 'Витрачено'
# ---------------------------------------------------------------------------

def test_budget_label_shows_vitracheno():
    """Budget row button text must contain 'Витрачено:'."""
    kb = build_asi_picker_keyboard(_BASE_SCORES, _ZERO_USED)
    budget_btn = kb.inline_keyboard[6][0]
    assert "Витрачено" in budget_btn.text, (
        f"Budget row must say 'Витрачено:...' Got: {budget_btn.text!r}"
    )


def test_budget_label_shows_sum_out_of_max():
    """Budget label must show 'N/2' where N is sum_used."""
    used = {"STR": 1, "DEX": 0, "CON": 0, "INT": 0, "WIS": 0, "CHA": 0}
    kb = build_asi_picker_keyboard(_BASE_SCORES, used, max_total=2)
    budget_btn = kb.inline_keyboard[6][0]
    assert "1/2" in budget_btn.text, (
        f"Budget label must show '1/2' when 1 point spent. Got: {budget_btn.text!r}"
    )


# ---------------------------------------------------------------------------
# 7. Action row: Підтвердити vs waiting, Скинути always present, no Скасувати
# ---------------------------------------------------------------------------

def test_confirm_button_present_when_sum_equals_max():
    """Action row must have 'Підтвердити' button when sum_used == max_total."""
    used = {"STR": 1, "DEX": 1, "CON": 0, "INT": 0, "WIS": 0, "CHA": 0}
    kb = build_asi_picker_keyboard(_BASE_SCORES, used, max_total=2)
    action_row = kb.inline_keyboard[7]
    button_texts = [b.text for b in action_row]
    assert "Підтвердити" in button_texts, (
        f"'Підтвердити' must appear in action row when sum_used==2. Got: {button_texts}"
    )
    callbacks = [b.callback_data for b in action_row]
    assert "asi_confirm" in callbacks


def test_confirm_not_present_when_budget_not_full():
    """Action row must NOT have 'Підтвердити' when sum_used < max_total."""
    kb = build_asi_picker_keyboard(_BASE_SCORES, _ZERO_USED, max_total=2)
    action_row = kb.inline_keyboard[7]
    button_texts = [b.text for b in action_row]
    assert "Підтвердити" not in button_texts, (
        f"'Підтвердити' must NOT appear when budget is not fully used. Got: {button_texts}"
    )


def test_reset_button_always_present():
    """'Скинути' must always be in action row regardless of sum_used."""
    for used_sum in [0, 1, 2]:
        if used_sum == 0:
            used = _ZERO_USED
        elif used_sum == 1:
            used = dict(_ZERO_USED); used["STR"] = 1
        else:
            used = dict(_ZERO_USED); used["STR"] = 1; used["DEX"] = 1

        kb = build_asi_picker_keyboard(_BASE_SCORES, used, max_total=2)
        action_row = kb.inline_keyboard[7]
        callbacks = [b.callback_data for b in action_row]
        assert "asi_reset" in callbacks, (
            f"'asi_reset' must be in action row for sum_used={used_sum}. Got: {callbacks}"
        )


def test_no_cancel_button():
    """ASI is mandatory — no 'Скасувати' / 'pb_cancel' / 'Cancel' button."""
    kb = build_asi_picker_keyboard(_BASE_SCORES, _ZERO_USED)
    all_callbacks = _button_callbacks(kb)
    all_texts = _button_texts(kb)
    assert "pb_cancel" not in all_callbacks, "No cancel callback allowed in ASI picker"
    # Check text too (Ukrainian)
    cancel_texts = [t for t in all_texts if "Скасувати" in t or "Cancel" in t]
    assert cancel_texts == [], (
        f"No cancel button allowed in ASI picker. Found: {cancel_texts}"
    )
