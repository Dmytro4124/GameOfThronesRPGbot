# test/test_censor_gold.py
"""
Tests for the Censor gold-context fix:
  - build_validate_action_prompt gold parameter interpolation
  - validate_action gold extraction from profile

Invariant covered: §5.3 Censor JSON-contract — ITEM FRAUD rule carve-out for gold.
"""

import asyncio
import json
import pytest
from unittest.mock import patch, MagicMock

from core.prompts import build_validate_action_prompt


# ---------------------------------------------------------------------------
# A. build_validate_action_prompt — prompt construction
# ---------------------------------------------------------------------------

class TestBuildValidateActionPromptGoldParam:
    """Group A: prompt-level tests for the gold= parameter."""

    def test_signature_accepts_gold_kwarg(self):
        """A1: build_validate_action_prompt(gold=42) must not raise TypeError."""
        # Will raise TypeError if gold param is missing.
        prompt = build_validate_action_prompt(
            char_name="X",
            user_input="Y",
            inventory_list=["Кинджал"],
            gold=42,
        )
        assert isinstance(prompt, str)

    def test_gold_line_interpolated(self):
        """A2: prompt contains 'GOLD: 42 золотих' with exact spacing."""
        prompt = build_validate_action_prompt(
            char_name="X",
            user_input="Y",
            inventory_list=["Кинджал"],
            gold=42,
        )
        assert "GOLD: 42 золотих" in prompt, (
            f"Expected 'GOLD: 42 золотих' in prompt. Got excerpt: {prompt[:400]}"
        )

    def test_default_gold_zero(self):
        """A3: calling without gold arg yields a prompt containing 'GOLD: 0 золотих'."""
        prompt = build_validate_action_prompt(
            char_name="Hero",
            user_input="Щось робити",
            inventory_list=["Меч"],
            # gold omitted — default is 0
        )
        assert "GOLD: 0 золотих" in prompt, (
            f"Expected default 'GOLD: 0 золотих'. Got excerpt: {prompt[:400]}"
        )

    def test_rule_carve_out_present(self):
        """A4: prompt contains the GOLD IS A RESOURCE carve-out sentence."""
        prompt = build_validate_action_prompt(
            char_name="Тиріон",
            user_input="Я плачу 50 золотих",
            inventory_list=[],
            gold=10,
        )
        assert "GOLD IS A RESOURCE, NOT AN ITEM" in prompt, (
            "Censor prompt must include the ITEM FRAUD carve-out for gold. "
            "Without it, LLM may block valid payment actions."
        )

    def test_positive_gold_example_present(self):
        """A5: prompt contains the positive example about paying more gold than available."""
        prompt = build_validate_action_prompt(
            char_name="Арія",
            user_input="Я плачу 50 золотих",
            inventory_list=[],
            gold=10,
        )
        # The positive example line in the prompt (from prompts.py line 278)
        assert "плачу 50 золотих" in prompt, (
            "Positive example 'плачу 50 золотих' must be present in Censor prompt."
        )
        assert "engine clamps to 0" in prompt, (
            "Positive example must include 'engine clamps to 0' explanation."
        )

    def test_gold_big_number(self):
        """A6: gold=99999 → prompt contains 'GOLD: 99999 золотих'."""
        prompt = build_validate_action_prompt(
            char_name="Ланністер",
            user_input="Я купую замок",
            inventory_list=[],
            gold=99999,
        )
        assert "GOLD: 99999 золотих" in prompt

    def test_gold_zero_explicit(self):
        """A7: gold=0 → prompt contains 'GOLD: 0 золотих'."""
        prompt = build_validate_action_prompt(
            char_name="Бідняк",
            user_input="Я прошу милостиню",
            inventory_list=[],
            gold=0,
        )
        assert "GOLD: 0 золотих" in prompt


# ---------------------------------------------------------------------------
# B. validate_action caller integration — gold extraction from profile
# ---------------------------------------------------------------------------

def _make_censor_response(is_valid: bool = True, refusal: str = "") -> MagicMock:
    """Return a mock response object that clean_and_parse_json accepts."""
    m = MagicMock()
    m.text = json.dumps({"is_valid": is_valid, "refusal_reason": refusal})
    return m


class TestValidateActionGoldExtraction:
    """Group B: validate_action extracts gold from profile and passes it to prompt builder."""

    def _run(self, coro):
        return asyncio.run(coro)

    def _gold_from_capture(self, args_list, kwargs_list):
        """Extract the gold value passed to build_validate_action_prompt."""
        # Signature: build_validate_action_prompt(char_name, user_input, inventory_list, gold=0)
        # Positional index 3 = gold, or keyword 'gold'
        if args_list and len(args_list[0]) >= 4:
            return args_list[0][3]
        if kwargs_list and "gold" in kwargs_list[0]:
            return kwargs_list[0]["gold"]
        return None

    def test_gold_extracted_from_osobyste_zoloto(self):
        """B8: validate_action reads gold from 'Особисте Золото' and passes it to prompt builder."""
        profile = {
            "Ім'я": "Test",
            "Здоров'я": 100,
            "Особисте Золото": 250,
            "Інвентар": [],
        }

        captured_args = []
        captured_kwargs = []
        original_build = build_validate_action_prompt

        def _fake_build(*args, **kwargs):
            captured_args.append(args)
            captured_kwargs.append(kwargs)
            return original_build(*args, **kwargs)

        censor_result = {"is_valid": True, "refusal_reason": ""}

        async def _run_coro():
            from core.mechanics import validate_action
            return await validate_action("Я плачу 50", profile)

        with patch("core.mechanics.build_validate_action_prompt", side_effect=_fake_build):
            with patch("core.mechanics.model_worker.generate_content",
                       return_value=_make_censor_response()):
                with patch("core.mechanics.clean_and_parse_json", return_value=censor_result):
                    self._run(_run_coro())

        gold = self._gold_from_capture(captured_args, captured_kwargs)
        assert gold == 250, (
            f"validate_action must pass gold=250 from 'Особисте Золото'. Got: {gold}"
        )

    def test_gold_fallback_to_gold_key(self):
        """B9: when 'Особисте Золото' absent, falls back to 'gold' key."""
        profile = {
            "Ім'я": "Test",
            "Здоров'я": 100,
            "gold": 75,
            "Інвентар": [],
        }

        captured_args = []
        captured_kwargs = []
        original_build = build_validate_action_prompt

        def _fake_build(*args, **kwargs):
            captured_args.append(args)
            captured_kwargs.append(kwargs)
            return original_build(*args, **kwargs)

        censor_result = {"is_valid": True, "refusal_reason": ""}

        async def _run_coro():
            from core.mechanics import validate_action
            return await validate_action("Я куплю меч", profile)

        with patch("core.mechanics.build_validate_action_prompt", side_effect=_fake_build):
            with patch("core.mechanics.model_worker.generate_content",
                       return_value=_make_censor_response()):
                with patch("core.mechanics.clean_and_parse_json", return_value=censor_result):
                    self._run(_run_coro())

        gold = self._gold_from_capture(captured_args, captured_kwargs)
        assert gold == 75, (
            f"validate_action must fall back to 'gold' key when 'Особисте Золото' absent. Got: {gold}"
        )

    def test_gold_default_zero_when_both_keys_missing(self):
        """B10: when both 'Особисте Золото' and 'gold' absent, gold defaults to 0."""
        profile = {
            "Ім'я": "Test",
            "Здоров'я": 100,
            "Інвентар": [],
        }

        captured_args = []
        captured_kwargs = []
        original_build = build_validate_action_prompt

        def _fake_build(*args, **kwargs):
            captured_args.append(args)
            captured_kwargs.append(kwargs)
            return original_build(*args, **kwargs)

        censor_result = {"is_valid": True, "refusal_reason": ""}

        async def _run_coro():
            from core.mechanics import validate_action
            return await validate_action("Щось зробити", profile)

        with patch("core.mechanics.build_validate_action_prompt", side_effect=_fake_build):
            with patch("core.mechanics.model_worker.generate_content",
                       return_value=_make_censor_response()):
                with patch("core.mechanics.clean_and_parse_json", return_value=censor_result):
                    self._run(_run_coro())

        gold = self._gold_from_capture(captured_args, captured_kwargs)
        assert gold == 0, (
            f"validate_action must default gold to 0 when both gold keys absent. Got: {gold}"
        )

    def test_safe_int_coercion_malformed_string(self):
        """B11: safe_int coercion — 'Особисте Золото': 'abc' must result in gold=0."""
        profile = {
            "Ім'я": "Test",
            "Здоров'я": 100,
            "Особисте Золото": "abc",
            "Інвентар": [],
        }

        captured_args = []
        captured_kwargs = []
        original_build = build_validate_action_prompt

        def _fake_build(*args, **kwargs):
            captured_args.append(args)
            captured_kwargs.append(kwargs)
            return original_build(*args, **kwargs)

        censor_result = {"is_valid": True, "refusal_reason": ""}

        async def _run_coro():
            from core.mechanics import validate_action
            return await validate_action("Щось зробити", profile)

        with patch("core.mechanics.build_validate_action_prompt", side_effect=_fake_build):
            with patch("core.mechanics.model_worker.generate_content",
                       return_value=_make_censor_response()):
                with patch("core.mechanics.clean_and_parse_json", return_value=censor_result):
                    self._run(_run_coro())

        gold = self._gold_from_capture(captured_args, captured_kwargs)
        assert gold == 0, (
            f"safe_int must coerce malformed 'abc' to 0 for gold. Got: {gold}"
        )


# ---------------------------------------------------------------------------
# C. safe_int semantics — standalone verification (supplements test_mechanics.py)
# ---------------------------------------------------------------------------

class TestSafeIntGoldSemantics:
    """C: safe_int handles all expected gold edge cases correctly."""

    def test_safe_int_valid_string(self):
        from core.mechanics import safe_int
        assert safe_int("250", 0) == 250

    def test_safe_int_integer(self):
        from core.mechanics import safe_int
        assert safe_int(75, 0) == 75

    def test_safe_int_zero(self):
        from core.mechanics import safe_int
        assert safe_int(0, 0) == 0

    def test_safe_int_abc_returns_default(self):
        from core.mechanics import safe_int
        assert safe_int("abc", 0) == 0

    def test_safe_int_none_returns_default(self):
        from core.mechanics import safe_int
        assert safe_int(None, 0) == 0

    def test_safe_int_float_string(self):
        """safe_int("100.9") truncates to int via int(float(...))."""
        from core.mechanics import safe_int
        assert safe_int("100.9", 0) == 100
