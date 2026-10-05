"""Stage 5: Narrator prompt invariants (static + dynamic, NORMAL/COMBAT). No network."""
import pytest

from core import prompts as P
from core.prompts import build_narrator_parts, build_narrator_prompt

_PL = [
    dict(name="Орвелл Тестовий", house="Тестерлі", gold=7431, npc="Тестовий Слуга ZXQ"),
    dict(name="Ґвінет Вигадана", house="Вигаданих", gold=98765, npc="Вигаданий Радник QWV"),
]


def _parts(pl, **kw):
    a = dict(user_input="дія", director_notes=["Факт про " + pl["npc"]], npc_context_text=pl["npc"],
             player_name=pl["name"], player_house=pl["house"], current_scene="Сцена ZXQ",
             current_location="Локація ZXQ", impact_narrative_hints="", active_roster=[pl["npc"]])
    a.update(kw)
    return build_narrator_parts(**a)


STATICS = [("normal", P.NARRATOR_SYSTEM), ("combat", P.NARRATOR_SYSTEM_COMBAT)]


@pytest.mark.parametrize("name,s", STATICS, ids=["normal", "combat"])
def test_static_is_pure_text_contract(name, s):
    assert "БЕЗ JSON" in s and "БЕЗ маркдауну" in s
    assert "```" not in s
    assert not s.lstrip().startswith("{")


@pytest.mark.parametrize("name,s", STATICS, ids=["normal", "combat"])
def test_static_priority_and_scene_tokens(name, s):
    assert "ПРІОРИТЕТ РЕЖИМІВ" in s
    assert "<CRITICAL_OVERRIDE>" in s and "<EROTIC_MODE>" in s
    assert "NEW_SCENE" in s and "CONTINUING" in s
    assert "ЗАКРИТИЙ РОСТЕР" in s


@pytest.mark.parametrize("name,s", STATICS, ids=["normal", "combat"])
@pytest.mark.parametrize("needle", [
    "Показуй, а не розповідай", "НЕ став наприкінці", "Питання допустиме лише як репліка NPC",
    "150-250", "Не менше 150", "ТЕХНІКА ПИСЬМА", "пряма репліка", "director_notes",
    "Visual", "один раз", "Не вживай заїжджені звороти", "максимум один холодний епітет",
    "лише як звук чи гул", "ЖОДНИХ ЧИСЕЛ",
])
def test_static_new_style_rules_present(name, s, needle):
    assert needle in s


def test_combat_style_block_only_in_combat_static():
    assert "<combat_narrative_style>" not in P.NARRATOR_SYSTEM
    c = P.NARRATOR_SYSTEM_COMBAT
    assert c.startswith(P.NARRATOR_SYSTEM)
    assert "<combat_narrative_style>" in c and "</combat_narrative_style>" in c
    assert "4-6" in c
    block = c[c.index("<combat_narrative_style>"):]
    assert "<combat_log>" in block
    assert "Do NOT end with a question" in block
    assert "Do not repeat the same verb" in block
    assert "Avoid stock phrases" in block
    assert "concrete actor" in block


def test_static_length_budget():
    assert len(P.NARRATOR_SYSTEM) < 7500
    assert len(P.NARRATOR_SYSTEM_COMBAT) < 9000


def test_microexample_has_no_canon_npc_names():
    from database.canon_npc import get_canon_npcs_copy
    s = P.NARRATOR_SYSTEM
    example = s[s.index("Зразок"):s.index("ПРАВИЛО ФІЗИЧНОГО КОНФЛІКТУ")]
    for npc in get_canon_npcs_copy():
        for part in str(npc.get("Name", "")).split():
            if len(part) >= 4:
                assert part not in example, part


@pytest.mark.parametrize("pl", _PL, ids=["a", "b"])
@pytest.mark.parametrize("combat", [False, True])
def test_static_has_no_player_data_incl_example(pl, combat):
    static, dyn = _parts(pl, combat_log=["лог"] if combat else None)
    for needle in (pl["name"], pl["house"], str(pl["gold"]), pl["npc"], "Сцена ZXQ", "Локація ZXQ"):
        assert needle not in static
    assert pl["name"] in dyn and pl["npc"] in dyn


def test_static_identical_across_players():
    assert _parts(_PL[0])[0] == _parts(_PL[1], puppet_mode=True, erotic_mode=True)[0] == P.NARRATOR_SYSTEM
    assert _parts(_PL[0], combat_log=["a"])[0] == _parts(_PL[1], combat_log=["b"])[0] == P.NARRATOR_SYSTEM_COMBAT


def test_dynamic_roster_rules():
    _, d = _parts(_PL[0], dead_npcs=["Мертвий ZXQ"])
    assert "<active_roster>" in d and "ЗАКРИТИЙ СПИСОК" in d
    assert "<dead_characters" in d and "Мертвий ZXQ" in d
    assert "<npc_cards>" in d


def test_dynamic_empty_roster_message():
    _, d = _parts(_PL[0], active_roster=[])
    assert "у сцені нікого немає" in d


def test_dynamic_final_line_normal():
    _, d = _parts(_PL[0])
    assert "без прямого питання до героя" in d
    assert "Закінчи відкритим моментом" in d
    assert "150-250 слів" in d
    assert "<combat_log>" not in d


def test_dynamic_final_line_combat():
    _, d = _parts(_PL[0], combat_log=["Ворог падає."])
    assert "без прямого питання до героя" in d
    assert "<combat_narrative_style>" in d
    assert "4-6 коротких речень" in d
    assert "150-250 слів" not in d
    assert "Ворог падає." in d


def test_dynamic_modes_blocks():
    _, d = _parts(_PL[0], puppet_mode=True, erotic_mode=True)
    assert "<CRITICAL_OVERRIDE" in d and "<EROTIC_MODE" in d
    _, d2 = _parts(_PL[0])
    assert "<CRITICAL_OVERRIDE" not in d2 and "<EROTIC_MODE" not in d2


def test_scene_continuity_passthrough():
    _, d = _parts(_PL[0], scene_continuity_block="<scene_continuity>CONTINUING</scene_continuity>")
    assert "CONTINUING" in d


@pytest.mark.parametrize("combat", [None, ["x"]])
def test_legacy_builder_concatenates(combat):
    kw = dict(user_input="x", director_notes=["n"], npc_context_text="", player_name="Джон Сноу",
              player_house="Старк", current_scene="S", current_location="L", impact_narrative_hints="",
              combat_log=combat)
    s, d = build_narrator_parts(**kw)
    assert build_narrator_prompt(**kw) == s + "\n\n" + d
