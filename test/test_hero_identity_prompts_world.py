"""Стать/родина героя: схеми й промпти (core/prompts.py), world.py, intro_cache."""
import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import core.prompts as P
import core.world as world
import core.intro_cache as ic

FAM = [{"name": "Кейтлін Старк", "relation": "мати"}, {"name": "Робб Старк", "relation": "брат"}]


# ---------------- INITIAL_STATS_SCHEMA ----------------

def test_schema_gender_enum_and_required():
    s = P.INITIAL_STATS_SCHEMA
    assert s["properties"]["gender"]["enum"] == ["чоловіча", "жіноча"]
    assert "gender" in s["required"]


def test_schema_family_array_max8_not_required():
    s = P.INITIAL_STATS_SCHEMA
    fam = s["properties"]["family"]
    assert fam["type"].lower() == "array" and fam["maxItems"] == 8
    assert "family" not in s["required"]
    item = fam["items"]
    assert set(item["properties"]) == {"name", "relation"}
    assert set(item["required"]) == {"name", "relation"}


def test_initial_stats_prompt_has_gender_family_rule():
    out = P.build_initial_stats_prompt("Тест Герой", "Старк", "Північ", "Вінтерфелл")
    assert "GENDER & FAMILY" in out
    assert '"gender"' in out and '"family"' in out


# ---------------- Narrator / GM prompts ----------------

def _narr(**kw):
    a = dict(user_input="u", director_notes=["n"], npc_context_text="", player_name="Джон Сноу",
             player_house="Старк", current_scene="S", current_location="L", impact_narrative_hints="")
    a.update(kw)
    return P.build_narrator_parts(**a)


def _gm(**kw):
    a = dict(
        hero_name="Джон Сноу", hero_house="Старк", profile_json="{}", context_knowledge="",
        event_injection="", burst_injection="", current_time_str="t", curr_region="r", curr_loc="l",
        is_traveling=False, loc_hint="", curr_scene="s", valid_locs_str="l", valid_regions_str="r",
        region_locs_str="l", npc_context_text="", tension_label="x", mechanics_verdict="SUCCESS",
        impact_narrative_hints="", history_text="", user_input="u", action_slots=["A", "B", "C", "D"],
    )
    a.update(kw)
    return P.build_gm_logic_parts(**a)


@pytest.mark.parametrize("builder,gk,fk", [(_narr, "player_gender", "player_family"),
                                            (_gm, "hero_gender", "hero_family")])
class TestIdentityInDynamic:
    def test_unknown_gender_avoid_gendered_address(self, builder, gk, fk):
        static, dyn = builder()
        assert "<player_identity>" in dyn
        assert "уникай гендерних звертань" in dyn

    def test_known_gender_no_avoid_phrase(self, builder, gk, fk):
        _, dyn = builder(**{gk: "жіноча"})
        assert "СТАТЬ ГЕРОЯ: жіноча" in dyn
        assert "уникай гендерних звертань" not in dyn

    def test_male(self, builder, gk, fk):
        _, dyn = builder(**{gk: "чоловіча"})
        assert "СТАТЬ ГЕРОЯ: чоловіча" in dyn

    def test_family_listed(self, builder, gk, fk):
        _, dyn = builder(**{fk: FAM})
        assert "РОДИНА ГЕРОЯ (вичерпний список)" in dyn
        assert "мати — Кейтлін Старк" in dyn and "брат — Робб Старк" in dyn

    def test_no_family_no_exhaustive_list(self, builder, gk, fk):
        _, dyn = builder()
        assert "РОДИНА ГЕРОЯ (вичерпний список)" not in dyn

    def test_static_identical_across_players(self, builder, gk, fk):
        s0, _ = builder()
        s1, _ = builder(**{gk: "жіноча", fk: FAM})
        s2, _ = builder(**{gk: "чоловіча", fk: [{"name": "Інший Тайрел", "relation": "дядько"}]})
        assert s0 == s1 == s2

    def test_static_has_no_player_data(self, builder, gk, fk):
        s, _ = builder(**{gk: "жіноча", fk: FAM})
        assert "Кейтлін Старк" not in s and "СТАТЬ ГЕРОЯ" not in s


@pytest.mark.parametrize("mode", ["NORMAL", "COMBAT"])
def test_gm_static_identical_in_each_mode(mode):
    a, _ = _gm(mode=mode)
    b, _ = _gm(mode=mode, hero_gender="жіноча", hero_family=FAM)
    assert a == b


def test_narrator_static_identical_combat_mode():
    a, _ = _narr(combat_log=["x"])
    b, d = _narr(combat_log=["x"], player_gender="жіноча", player_family=FAM)
    assert a == b and "СТАТЬ ГЕРОЯ: жіноча" in d


def test_system_prompts_constants_not_mutated_by_identity():
    before = dict(P.SYSTEM_PROMPTS)
    _narr(player_gender="жіноча", player_family=FAM)
    _gm(hero_gender="жіноча", hero_family=FAM)
    assert dict(P.SYSTEM_PROMPTS) == before


# ---------------- world.py ----------------

_LLM = {
    "suggested_class": "Knight", "suggested_heritage": "Westerosi (Andal)",
    "ability_scores": {"STR": 15, "DEX": 10, "CON": 14, "INT": 10, "WIS": 12, "CHA": 11},
    "Поточне місцезнаходження": "Королівська Гавань", "Поточна сцена": "Нема Такої",
}


def _gen(llm, char="Лорд Тестовий"):
    model = MagicMock()
    model.generate_content = MagicMock(return_value=SimpleNamespace(text="{}"))
    with patch.object(world, "model_gm_logic", model), \
            patch.object(world, "build_strict_config", MagicMock(return_value=None)), \
            patch.object(world, "clean_and_parse_json", MagicMock(return_value=llm)), \
            patch.object(world, "_canon_names_for_family", return_value=["Кейтлін Старк"]):
        return asyncio.run(world.generate_initial_stats(char, "Дім", {"Регіон": "Північ"}))


def test_generate_puts_normalized_gender_and_family():
    llm = dict(_LLM, gender="ж", family=[{"name": "Кейтлин Старк", "relation": "мати"},
                                          {"name": "Лорд Тестовий", "relation": "я"}])
    prof = _gen(llm)
    assert prof["Стать"] == "жіноча"
    assert prof["Родина"] == [{"name": "Кейтлін Старк", "relation": "мати"}]


def test_generate_garbage_gender_family():
    prof = _gen(dict(_LLM, gender="???", family="мати"))
    assert prof["Стать"] == "" and prof["Родина"] == []


def test_generate_missing_gender_family_keys():
    prof = _gen(dict(_LLM))
    assert prof["Стать"] == "" and prof["Родина"] == []


def test_deterministic_profile_has_empty_identity():
    prof = world._build_deterministic_dnd_profile("Тест Герой", "Старк", "Північ")
    assert prof["Стать"] == "" and prof["Родина"] == []


@pytest.mark.parametrize("gender,needle,absent", [
    ("жіноча", "міледі", "мілорде"),
    ("чоловіча", "мілорде", "міледі"),
])
def test_fallback_intro_address_by_gender(gender, needle, absent):
    out = world.build_fallback_intro({"Ім'я": "Тест", "Стать": gender})
    assert needle in out and absent not in out


def test_fallback_intro_unknown_gender_uses_name():
    out = world.build_fallback_intro({"Ім'я": "Тест Героїв"})
    assert "міледі" not in out and "мілорде" not in out
    assert "Тест Героїв" in out.split("Що зробите першим")[1]


# ---------------- intro_cache ----------------

@pytest.fixture
def cache(tmp_path):
    with patch.object(ic, "_CACHE_FILE", tmp_path / "c.json"):
        ic._CACHE, ic._LOADED = {}, False
        yield ic
        ic._CACHE, ic._LOADED = {}, False


def _hero(**kw):
    p = {"Ім'я": "Тест", "class": "Knight", "heritage": "H", "Регіон": "R", "Стать": "чоловіча"}
    p.update(kw)
    return p


def test_fingerprint_differs_by_gender(cache):
    assert cache._fingerprint(_hero(Стать="жіноча")) != cache._fingerprint(_hero(Стать="чоловіча"))


@pytest.mark.parametrize("g", ["", None, "   ", "щось"])
def test_fingerprint_unknown_gender_equals_missing(cache, g):
    p = _hero(Стать=g)
    p_missing = _hero()
    del p_missing["Стать"]
    assert cache._fingerprint(p) == cache._fingerprint(p_missing)


def test_fingerprint_gender_synonyms_normalized(cache):
    assert cache._fingerprint(_hero(Стать="female")) == cache._fingerprint(_hero(Стать="жіноча"))


def test_cache_gender_change_invalidates(cache):
    asyncio.run(cache.set_cached_intro(1, _hero(Стать="чоловіча"), "male-intro"))
    assert cache.get_cached_intro(1, _hero(Стать="чоловіча")) == "male-intro"
    assert cache.get_cached_intro(1, _hero(Стать="жіноча")) is None


def test_unknown_gender_does_not_hit_gendered_entry(cache):
    asyncio.run(cache.set_cached_intro(1, _hero(Стать="жіноча"), "female-intro"))
    assert cache.get_cached_intro(1, _hero(Стать="")) is None


def test_family_line_format_exact_in_player_identity():
    """Блок родини в <player_identity> форматується через hero_identity.format_family_line."""
    from core.hero_identity import format_family_line
    block = P._hero_gender_family_block("жіноча", FAM, "Старк")
    assert f"РОДИНА ГЕРОЯ (вичерпний список): {format_family_line(FAM)}." in block
    assert "мати — Кейтлін Старк; брат — Робб Старк" in block
    assert not hasattr(P, "_format_family_line")
