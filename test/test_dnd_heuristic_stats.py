# test/test_dnd_heuristic_stats.py
"""Tests for core/dnd_heuristic_stats.py — deterministic D&D statblock derivation.

Strategy:
  - No I/O, no mocking required: the module is pure Python.
  - Tests cover classify_role (role detection from NPC text),
    derive_stats_from_npc (output structure and values),
    and apply_stats_to_canon_npcs (idempotency + batch behaviour).
"""
import pytest

from core.dnd_heuristic_stats import (
    VALID_CR,
    apply_stats_to_canon_npcs,
    classify_role,
    derive_stats_from_npc,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

_REQUIRED_STATBLOCK_KEYS = {
    "cr", "ability_scores", "hp_max", "hp_current",
    "ac", "speed", "attacks", "saves", "skills",
    "conditions", "tags", "_heuristic_role",
}

_ABILITY_KEYS = {"STR", "DEX", "CON", "INT", "WIS", "CHA"}


def _npc(name: str = "", description: str = "", character: str = "", goal: str = "") -> dict:
    """Minimal NPC dict for testing."""
    return {
        "Name": name,
        "Description": description,
        "Character": character,
        "Goal": goal,
    }


# ===========================================================================
# 1. classify_role — known roles
# ===========================================================================

class TestClassifyRole:

    def test_peasant_is_smallfolk(self):
        npc = _npc(description="Звичайний селянин з Вінтерфеллу.")
        assert classify_role(npc) == "smallfolk"

    def test_guard_keyword(self):
        npc = _npc(description="Вартовий міських воріт Королівської Гавані.")
        assert classify_role(npc) == "guard"

    def test_barristan_selmy_is_elite_knight(self):
        npc = _npc(
            name="Сер Барристан Селмі",
            description="Барристан — найкращий мечник Вестеросу, лицар Королівської гвардії.",
        )
        assert classify_role(npc) == "elite_knight"

    def test_tywin_by_name_is_great_lord(self):
        npc = _npc(
            name="Тайвін Ланністер",
            description="Великий лорд Кастерлі Року, глава Дому Ланністер.",
        )
        assert classify_role(npc) == "great_lord"

    def test_gregor_clegane_by_name_is_champion(self):
        npc = _npc(name="Григор Клеган")
        assert classify_role(npc) == "champion"

    def test_maester_luwin_by_name(self):
        npc = _npc(
            name="Мейстер Лювін",
            description="Мейстер замку Вінтерфелл, носить ланцюг знань.",
        )
        assert classify_role(npc) == "maester"

    def test_spy_keywords(self):
        npc = _npc(description="Варіс — павук, майстер маленьких пташок.")
        assert classify_role(npc) == "spy"

    def test_septon_keywords(self):
        npc = _npc(description="Септон Церкви Семи Богів у Королівській Гавані.")
        assert classify_role(npc) == "septon"

    def test_hedge_knight(self):
        npc = _npc(description="Мандрівний лицар без землі та зобов'язань.")
        assert classify_role(npc) == "hedge_knight"

    def test_regular_knight(self):
        npc = _npc(description="Сер Родрік — довірений лицар Дому Старк.")
        assert classify_role(npc) == "knight"

    def test_child_keywords(self):
        npc = _npc(description="Маленький хлопчик семи років від роду.")
        assert classify_role(npc) == "child"

    def test_king_by_description(self):
        npc = _npc(description="Сидить на залізному троні і керує Вестеросом.")
        assert classify_role(npc) == "king"

    def test_bandit_keywords(self):
        npc = _npc(description="Ватажок бандитів у лісах Річкових Земель.")
        assert classify_role(npc) == "bandit"

    def test_wildling_keywords(self):
        npc = _npc(description="Представник вільного народу із-за Стіни.")
        assert classify_role(npc) == "wildling"

    def test_empty_npc_falls_back_to_commoner(self):
        npc = _npc()
        assert classify_role(npc) == "commoner"

    def test_courtier_keywords(self):
        npc = _npc(description="Придворний інтриган при дворі короля.")
        assert classify_role(npc) == "courtier"


# ===========================================================================
# 2. derive_stats_from_npc — output structure and values
# ===========================================================================

class TestDeriveStatsFromNpc:

    def test_all_required_keys_present(self):
        npc = _npc(description="Звичайний селянин.")
        result = derive_stats_from_npc(npc)
        missing = _REQUIRED_STATBLOCK_KEYS - result.keys()
        assert not missing, f"Missing statblock keys: {missing}"

    def test_cr_is_valid(self):
        npc = _npc(description="Звичайний селянин.")
        result = derive_stats_from_npc(npc)
        assert result["cr"] in VALID_CR, f"cr={result['cr']!r} not in VALID_CR"

    def test_hp_max_is_positive(self):
        npc = _npc(description="Звичайний селянин.")
        result = derive_stats_from_npc(npc)
        assert result["hp_max"] > 0

    def test_hp_current_equals_hp_max_initially(self):
        npc = _npc(description="Звичайний селянин.")
        result = derive_stats_from_npc(npc)
        assert result["hp_current"] == result["hp_max"]

    def test_ac_is_at_least_10(self):
        npc = _npc(description="Звичайний селянин.")
        result = derive_stats_from_npc(npc)
        assert result["ac"] >= 10

    def test_ability_scores_has_all_six_stats(self):
        npc = _npc(description="Звичайний селянин.")
        result = derive_stats_from_npc(npc)
        assert _ABILITY_KEYS == set(result["ability_scores"].keys())

    def test_attacks_is_non_empty_list(self):
        npc = _npc(description="Звичайний селянин.")
        result = derive_stats_from_npc(npc)
        assert isinstance(result["attacks"], list)
        assert len(result["attacks"]) >= 1

    def test_attacks_have_required_keys(self):
        npc = _npc(description="Звичайний селянин.")
        result = derive_stats_from_npc(npc)
        for atk in result["attacks"]:
            assert "name" in atk, "attack missing 'name'"
            assert "to_hit" in atk, "attack missing 'to_hit'"
            assert "dmg" in atk, "attack missing 'dmg'"

    def test_conditions_is_empty_list(self):
        npc = _npc(description="Звичайний селянин.")
        result = derive_stats_from_npc(npc)
        assert result["conditions"] == []

    def test_tags_is_non_empty_list(self):
        npc = _npc(description="Звичайний селянин.")
        result = derive_stats_from_npc(npc)
        assert isinstance(result["tags"], list)
        assert len(result["tags"]) >= 1

    def test_heuristic_role_key_present(self):
        npc = _npc(description="Звичайний селянин.")
        result = derive_stats_from_npc(npc)
        assert "_heuristic_role" in result

    def test_does_not_mutate_original_npc(self):
        npc = _npc(description="Звичайний селянин.")
        original_keys = set(npc.keys())
        derive_stats_from_npc(npc)
        assert set(npc.keys()) == original_keys, "derive_stats_from_npc must not mutate the input"

    def test_champion_cr_8_str_20(self):
        npc = _npc(name="Григор Клеган")
        result = derive_stats_from_npc(npc)
        assert result["cr"] == "8", f"Expected CR 8 for champion, got {result['cr']!r}"
        assert result["ability_scores"]["STR"] == 20, (
            f"Expected STR 20 for champion, got {result['ability_scores']['STR']}"
        )

    def test_commoner_cr_0_hp_6(self):
        npc = _npc()  # empty → commoner fallback
        result = derive_stats_from_npc(npc)
        assert result["cr"] == "0", f"Expected CR 0 for commoner, got {result['cr']!r}"
        assert result["hp_max"] == 6, f"Expected HP 6 for commoner, got {result['hp_max']}"

    def test_great_lord_cr_5(self):
        npc = _npc(name="Тайвін Ланністер", description="Великий лорд Кастерлі Року.")
        result = derive_stats_from_npc(npc)
        assert result["cr"] == "5"

    def test_elite_knight_ac_19(self):
        npc = _npc(
            name="Сер Барристан Селмі",
            description="Барристан — лицар Королівської гвардії.",
        )
        result = derive_stats_from_npc(npc)
        assert result["ac"] == 19

    def test_maester_has_medicine_skill(self):
        npc = _npc(name="Мейстер Лювін", description="Мейстер, носить ланцюг знань.")
        result = derive_stats_from_npc(npc)
        assert "Medicine" in result["skills"], "Maester must have Medicine skill"


# ===========================================================================
# 3. apply_stats_to_canon_npcs — idempotency and batch behaviour
# ===========================================================================

class TestApplyStatsToCanonNpcs:

    def _make_raw_npcs(self, n: int) -> list[dict]:
        return [
            _npc(name=f"NPC_{i}", description="Звичайний селянин.")
            for i in range(n)
        ]

    def test_returns_same_length_as_input(self):
        npcs = self._make_raw_npcs(5)
        result = apply_stats_to_canon_npcs(npcs)
        assert len(result) == 5

    def test_all_npcs_get_ability_scores(self):
        npcs = self._make_raw_npcs(3)
        result = apply_stats_to_canon_npcs(npcs)
        for npc in result:
            assert "ability_scores" in npc, f"{npc.get('Name')} missing ability_scores"

    def test_idempotent_skips_npc_with_existing_ability_scores(self):
        """NPC that already has ability_scores must not be overwritten."""
        pre_existing_scores = {"STR": 99, "DEX": 99, "CON": 99, "INT": 99, "WIS": 99, "CHA": 99}
        npc_with_stats = dict(
            _npc(name="Already Done", description="Звичайний селянин."),
            ability_scores=pre_existing_scores,
            cr="1/4",
        )
        result = apply_stats_to_canon_npcs([npc_with_stats])
        assert result[0]["ability_scores"] == pre_existing_scores, (
            "apply_stats_to_canon_npcs must not overwrite pre-existing ability_scores"
        )
        # _heuristic_role must NOT be added when skipping
        assert "_heuristic_role" not in result[0], (
            "_heuristic_role must not appear on already-processed NPCs"
        )

    def test_second_run_is_truly_idempotent(self):
        """Running twice on the same list must produce identical results."""
        npcs = self._make_raw_npcs(4)
        first_pass = apply_stats_to_canon_npcs(npcs)
        second_pass = apply_stats_to_canon_npcs(first_pass)

        for i, (a, b) in enumerate(zip(first_pass, second_pass)):
            assert a["ability_scores"] == b["ability_scores"], (
                f"NPC[{i}] ability_scores changed on second run"
            )
            assert a["cr"] == b["cr"], f"NPC[{i}] cr changed on second run"
            assert a["hp_max"] == b["hp_max"], f"NPC[{i}] hp_max changed on second run"

    def test_does_not_mutate_originals(self):
        """Original NPC dicts must not be mutated."""
        npcs = self._make_raw_npcs(2)
        originals_before = [dict(npc) for npc in npcs]
        apply_stats_to_canon_npcs(npcs)
        for i, (orig, before) in enumerate(zip(npcs, originals_before)):
            assert orig == before, f"Original NPC[{i}] was mutated by apply_stats_to_canon_npcs"

    def test_works_with_tuple_input(self):
        """apply_stats_to_canon_npcs must accept a tuple (as CANON_NPCS returns one)."""
        npcs_tuple = tuple(self._make_raw_npcs(3))
        result = apply_stats_to_canon_npcs(npcs_tuple)
        assert len(result) == 3
        assert isinstance(result, list)

    def test_empty_input_returns_empty_list(self):
        assert apply_stats_to_canon_npcs([]) == []

    def test_mixed_list_partial_already_migrated(self):
        """List where some NPCs already have ability_scores — only the rest get stats."""
        already_done = dict(
            _npc(name="AlreadyDone", description="Вартовий."),
            ability_scores={"STR": 13, "DEX": 12, "CON": 12, "INT": 10, "WIS": 11, "CHA": 10},
            cr="1/4",
        )
        needs_stats = _npc(name="Fresh", description="Звичайний селянин.")
        result = apply_stats_to_canon_npcs([already_done, needs_stats])

        # AlreadyDone preserved
        assert result[0]["ability_scores"]["STR"] == 13
        assert "_heuristic_role" not in result[0]

        # Fresh got stats
        assert "ability_scores" in result[1]
        assert "_heuristic_role" in result[1]
