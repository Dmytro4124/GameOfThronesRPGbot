"""A2/A3 у core/world.py (стартова позиція героя) + A1 у bot/handlers.py (_ensure_npc_cache, resume).
Без мережі/Sheets: LLM, refresh_npc_database, sleep замоковані."""
import asyncio
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

import core.world as world
from core.world_constants import (
    LOCATION_SCENES, LOCATION_TO_REGION, VALID_LOCATIONS_ORDERED,
    get_locations_for_region, get_scenes_for_location, is_valid_location, is_valid_scene,
)


def _load_real_canon():
    """Інші тести підміняють sys.modules['database.canon_npc'] стабом ([]) -> вантажимо реальний файл напряму."""
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "database" / "canon_npc.py"
    spec = importlib.util.spec_from_file_location("_real_canon_npc_for_tests", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.get_canon_npcs_copy


get_canon_npcs_copy = _load_real_canon()


def _load_real_find_best_match():
    """Інші тести підміняють sys.modules['database.operations'] стабом (find_best_match -> None), тож
    беремо РЕАЛЬНУ функцію з вихідника через ast (без імпорту модуля й gspread)."""
    import ast
    import difflib
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "database" / "operations.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "find_best_match")
    ns = {"difflib": difflib}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "operations.find_best_match", "exec"), ns)
    return ns["find_best_match"]


find_best_match = _load_real_find_best_match()


@pytest.fixture(autouse=True)
def _real_matcher():
    with patch.object(world, "find_best_match", find_best_match),             patch.object(world, "get_canon_npcs_copy", get_canon_npcs_copy):
        yield

_REGIONS = sorted(set(LOCATION_TO_REGION.values()))


# ============================ A2: format_start_locations_for_prompt ============================

@pytest.mark.parametrize("region", ["Північ", "Ессос", "Королівські Землі", "Дорн"])
def test_format_block_header_and_lines(region):
    if not get_locations_for_region(region):
        pytest.skip(f"регіон {region} відсутній у LOCATION_TO_REGION")
    out = world.format_start_locations_for_prompt(region)
    lines = out.split("\n")
    assert lines[0].startswith(f"ЛОКАЦІЇ РЕГІОНУ «{region}» ТА ЇХНІ КАНОНІЧНІ СЦЕНИ")
    assert len(lines) == 1 + len(get_locations_for_region(region))
    assert "NPC-пул" not in out and "ЗАБОРОНЕНО" not in out


@pytest.mark.parametrize("region", _REGIONS)
def test_format_block_scenes_belong_to_location_and_hub_first(region):
    out = world.format_start_locations_for_prompt(region)
    for line in out.split("\n")[1:]:
        assert line.startswith('- "')
        loc, rest = line[3:].split('": ', 1)
        scenes = [s.strip().strip('"') for s in rest.split('", "')]
        scenes[0] = scenes[0].lstrip('"')
        scenes[-1] = scenes[-1].rstrip('"')
        assert loc in get_locations_for_region(region)
        assert scenes and all(s in get_scenes_for_location(loc) for s in scenes), (loc, scenes)
        hub = LOCATION_SCENES.get(loc, {}).get("hub", [])
        if hub:
            assert scenes[0] == hub[0]


def test_format_block_unknown_region_falls_back_to_first_location():
    out = world.format_start_locations_for_prompt("Нема Такого")
    assert f'- "{VALID_LOCATIONS_ORDERED[0]}":' in out


# ============================ _region_hub_location / _hub_scene ============================

def test_region_hub_location_first_of_region():
    reg = _REGIONS[0]
    assert world._region_hub_location(reg) == get_locations_for_region(reg)[0]


def test_region_hub_location_unknown_region():
    assert world._region_hub_location("Нема Такого") == VALID_LOCATIONS_ORDERED[0]


def test_hub_scene_uses_hub_first():
    loc = next(l for l, d in LOCATION_SCENES.items() if d.get("hub"))
    assert world._hub_scene(loc) == LOCATION_SCENES[loc]["hub"][0]


def test_hub_scene_legacy_location_without_scenes_returns_name():
    loc = next(l for l in VALID_LOCATIONS_ORDERED if l not in LOCATION_SCENES)
    assert world._hub_scene(loc) == loc


def test_hub_scene_location_without_hub_but_with_scenes():
    fake = {"Фейк": {"private": ["Кімната"]}}
    with patch.object(world, "LOCATION_SCENES", fake), \
            patch.object(world, "get_scenes_for_location", lambda l: ["Кімната"]):
        assert world._hub_scene("Фейк") == "Кімната"


# ============================ _validate_start_position ============================

def _loc_with_scenes():
    return next(l for l, d in LOCATION_SCENES.items() if d.get("hub") and len(get_scenes_for_location(l)) > 1)


def test_validate_valid_pair_unchanged():
    loc = _loc_with_scenes()
    sc = get_scenes_for_location(loc)[-1]
    assert world._validate_start_position(loc, sc, "Північ") == (loc, sc)


def test_validate_invalid_location_goes_to_region_hub_and_scene_hub():
    reg = "Північ"
    hub_loc = get_locations_for_region(reg)[0]
    loc, sc = world._validate_start_position("Атлантида", "Щось", reg)
    assert loc == hub_loc
    assert is_valid_scene(loc, sc)


@pytest.mark.parametrize("empty", ["", None, "Невідомо", "невідомо", "GLOBAL", "unknown", "  "])
def test_validate_empty_scene_goes_to_hub0(empty):
    loc = _loc_with_scenes()
    assert world._validate_start_position(loc, empty, "Північ") == (loc, LOCATION_SCENES[loc]["hub"][0])


def test_validate_invalid_scene_difflib_correction():
    loc = _loc_with_scenes()
    real = get_scenes_for_location(loc)[-1]
    typo = real[:-1] + "ъ"
    assert world._validate_start_position(loc, typo, "Північ") == (loc, real)


def test_validate_totally_foreign_scene_goes_to_hub0():
    loc = _loc_with_scenes()
    assert world._validate_start_position(loc, "zzzzqqqq", "Північ") == (loc, LOCATION_SCENES[loc]["hub"][0])


def test_validate_scene_from_other_location_not_kept():
    a = _loc_with_scenes()
    other = next(l for l in LOCATION_SCENES if l != a and LOCATION_SCENES[l].get("hub"))
    foreign = LOCATION_SCENES[other]["hub"][0]
    if foreign in get_scenes_for_location(a):
        pytest.skip("збіг назв сцен")
    _, sc = world._validate_start_position(a, foreign, "Північ")
    assert is_valid_scene(a, sc)


# ============================ A3: _canon_start_position ============================

def test_canon_viseris():
    assert world._canon_start_position("Візеріс Таргарієн") == ("Квартал Магістрів", "Маєток Ілліріо")


def test_canon_eddard():
    assert world._canon_start_position("Еддард Старк") == ("Вінтерфел", "Великий Зал")


def test_canon_benjen_not_eddard():
    pos = world._canon_start_position("Бенджен Старк")
    assert pos != ("Вінтерфел", "Великий Зал")


def test_canon_custom_name_none():
    assert world._canon_start_position("Лорд Тестовий Невідомий") is None


def test_canon_invalid_canon_scene_falls_to_hub():
    fake = [{"Name": "Герой Х", "Location": "Вінтерфел", "Scene": "Нема Сцени"}]
    with patch.object(world, "get_canon_npcs_copy", lambda: fake):
        assert world._canon_start_position("Герой Х") == ("Вінтерфел", LOCATION_SCENES["Вінтерфел"]["hub"][0])


def test_canon_invalid_canon_location_none():
    fake = [{"Name": "Герой Х", "Location": "За Стіною", "Scene": "Табір"}]
    with patch.object(world, "get_canon_npcs_copy", lambda: fake):
        assert world._canon_start_position("Герой Х") is None


@pytest.mark.parametrize("hero", ["Візеріс Таргарієн", "Еддард Старк", "Бенджен Старк", "Дейнеріс Таргарієн",
                                  "Лорд Тестовий Невідомий", "еддард старк"])
def test_twin_exclusion_consistent_with_anchoring(hero):
    """background_canon_generation виключає canon NPC через find_best_match(name,[hero],0.8);
    anchoring матчить hero по canon-іменах тим самим порогом -> збіг/відсутність має бути однаковою."""
    canon_names = [n["Name"] for n in get_canon_npcs_copy()]
    excluded = {n for n in canon_names if find_best_match(n, [hero], threshold=0.8)}
    anchored = find_best_match(hero, canon_names, threshold=0.8)
    if anchored is None:
        assert excluded == set()
    else:
        assert excluded == {anchored}
        assert world._canon_start_position(hero) is not None or not is_valid_location(
            next(n for n in get_canon_npcs_copy() if n["Name"] == anchored)["Location"])


# ============================ generate_initial_stats integration ============================

_LLM = {
    "suggested_class": "Knight", "suggested_heritage": "Westerosi (Andal)",
    "ability_scores": {"STR": 15, "DEX": 10, "CON": 14, "INT": 10, "WIS": 12, "CHA": 11},
    "Поточне місцезнаходження": "Королівська Гавань", "Поточна сцена": "Нема Такої",
}


def _gen(char, llm, house_data=None):
    captured = {}
    real_prompt = world.build_initial_stats_prompt

    def _spy(*a, **k):
        captured["args"], captured["kwargs"] = a, k
        return real_prompt(*a, **k)

    resp = SimpleNamespace(text="{}")
    model = MagicMock()
    model.generate_content = MagicMock(return_value=resp)
    with patch.object(world, "build_initial_stats_prompt", _spy), \
            patch.object(world, "model_gm_logic", model), \
            patch.object(world, "build_strict_config", MagicMock(return_value=None)), \
            patch.object(world, "clean_and_parse_json", MagicMock(return_value=llm)):
        prof = asyncio.run(world.generate_initial_stats(char, "Дім", house_data or {"Регіон": "Північ"}))
    return prof, captured


def test_generate_passes_new_block_to_prompt():
    _, cap = _gen("Лорд Тестовий", dict(_LLM))
    block = cap["kwargs"]["scenes_block_str"]
    assert block == world.format_start_locations_for_prompt("Північ")
    assert block.startswith("ЛОКАЦІЇ РЕГІОНУ «Північ»")


def test_generate_custom_hero_position_validated_not_anchored():
    prof, _ = _gen("Лорд Тестовий", dict(_LLM))
    loc, sc = prof["Поточне місцезнаходження"], prof["Поточна сцена"]
    assert is_valid_location(loc) and is_valid_scene(loc, sc)
    assert loc == "Королівська Гавань"  # валідна LLM-локація збережена
    assert is_valid_scene(loc, sc)  # невалідна сцена -> difflib/hub, але результат завжди валідний


def test_generate_anchoring_overrides_llm_choice():
    prof, _ = _gen("Візеріс Таргарієн", dict(_LLM), {"Регіон": "Ессос"})
    assert prof["Поточне місцезнаходження"] == "Квартал Магістрів"
    assert prof["Поточна сцена"] == "Маєток Ілліріо"


def test_generate_anchoring_in_deterministic_fallback():
    prof, _ = _gen("Еддард Старк", None)  # LLM -> None => fallback-профіль
    assert prof["Поточне місцезнаходження"] == "Вінтерфел"
    assert prof["Поточна сцена"] == "Великий Зал"


def test_generate_fallback_custom_hero_not_anchored():
    prof, _ = _gen("Лорд Тестовий Невідомий", None)
    assert is_valid_location(prof["Поточне місцезнаходження"])


# ============================ A1 handlers: _ensure_npc_cache / resume ============================

import bot.handlers as H

CID = 987002


@pytest.fixture
def sess():
    H.user_sessions[CID] = {"npc_cache": {}}
    yield H.user_sessions[CID]
    H.user_sessions.pop(CID, None)


def _run(coro):
    return asyncio.run(coro)


def test_ensure_success_first_try(sess):
    async def _ref(cid):
        H.user_sessions[cid]["npc_cache"] = {"A": {}}
        return True
    ref = AsyncMock(side_effect=_ref)
    sleep = AsyncMock()
    with patch.object(H, "refresh_npc_database", ref), patch.object(H.asyncio, "sleep", sleep):
        assert _run(H._ensure_npc_cache(CID)) is True
    assert ref.await_count == 1
    sleep.assert_not_awaited()
    assert sess["_npc_cache_attempted"] is True


def test_ensure_false_then_retry_success(sess):
    calls = {"n": 0}

    async def _ref(cid):
        calls["n"] += 1
        if calls["n"] == 2:
            H.user_sessions[cid]["npc_cache"] = {"A": {}}
            return True
        return False
    sleep = AsyncMock()
    with patch.object(H, "refresh_npc_database", AsyncMock(side_effect=_ref)), patch.object(H.asyncio, "sleep", sleep):
        assert _run(H._ensure_npc_cache(CID, retries=1, delay=3.0)) is True
    assert calls["n"] == 2
    sleep.assert_awaited_once_with(3.0)


def test_ensure_true_but_empty_cache_retries(sess):
    ref = AsyncMock(return_value=True)  # кеш лишається {}
    with patch.object(H, "refresh_npc_database", ref), patch.object(H.asyncio, "sleep", AsyncMock()):
        _run(H._ensure_npc_cache(CID, retries=1))
    assert ref.await_count == 2


def test_ensure_exception_returns_false_no_raise(sess):
    ref = AsyncMock(side_effect=RuntimeError("429"))
    with patch.object(H, "refresh_npc_database", ref), patch.object(H.asyncio, "sleep", AsyncMock()):
        assert _run(H._ensure_npc_cache(CID, retries=1)) is False
    assert ref.await_count == 2
    assert sess["_npc_cache_attempted"] is True


def test_ensure_retries_zero_single_attempt_no_sleep(sess):
    ref = AsyncMock(return_value=False)
    sleep = AsyncMock()
    with patch.object(H, "refresh_npc_database", ref), patch.object(H.asyncio, "sleep", sleep):
        assert _run(H._ensure_npc_cache(CID, retries=0)) is False
    assert ref.await_count == 1
    sleep.assert_not_awaited()


def test_ensure_without_session_does_not_create_one():
    H.user_sessions.pop(CID, None)
    with patch.object(H, "refresh_npc_database", AsyncMock(return_value=False)), patch.object(H.asyncio, "sleep", AsyncMock()):
        _run(H._ensure_npc_cache(CID, retries=0))
    assert CID not in H.user_sessions


def _call():
    call = MagicMock()
    call.message.chat.id = CID
    call.message.delete = AsyncMock()
    call.message.answer = AsyncMock()
    return call


@pytest.mark.parametrize("refresh_exc", [None, RuntimeError("sheets down")])
def test_resume_calls_refresh_and_survives_errors(refresh_exc):
    ref = AsyncMock(side_effect=refresh_exc, return_value=True) if refresh_exc else AsyncMock(return_value=True)
    send = AsyncMock()
    try:
        with patch.object(H, "get_user_data", AsyncMock(return_value=({"Ім'я": "Т"}, 1))), \
                patch.object(H, "refresh_npc_database", ref), \
                patch.object(H, "send_safe_message", send), \
                patch.object(H, "get_main_menu", MagicMock()), \
                patch.object(H.asyncio, "sleep", AsyncMock()):
            _run(H.resume_game_handler(_call(), MagicMock()))
        assert ref.await_count == 1
        assert H.user_sessions[CID]["state"] == "GAME_ACTIVE"
        send.assert_awaited_once()
    finally:
        H.user_sessions.pop(CID, None)


# ============================ _is_same_hero / threshold / SCENE_CORRECTION_CUTOFF ============================

def test_constants():
    assert world.HERO_NAME_MATCH_THRESHOLD == 0.8
    assert world.SCENE_CORRECTION_CUTOFF == 0.6


@pytest.mark.parametrize("a,b", [
    ("Еддард Старк", "еддард старк"), ("ЕДДАРД СТАРК", "Еддард Старк"),
    ("Візеріс Таргарієн", "Дейнеріс Таргарієн"), ("Бенджен Старк", "Еддард Старк"),
    ("  Робб Старк ", "Робб Старк"), ("Джон Сноу", "Джон Аррен"),
])
def test_is_same_hero_symmetric(a, b):
    assert world._is_same_hero(a, b) == world._is_same_hero(b, a)


@pytest.mark.parametrize("a,b", [("", "Еддард Старк"), ("Еддард Старк", ""), (None, "Х"), ("Х", None), ("  ", " ")])
def test_is_same_hero_empty_false(a, b):
    assert world._is_same_hero(a, b) is False


def test_is_same_hero_case_insensitive_true():
    assert world._is_same_hero("ЕДДАРД СТАРК", "еддард старк") is True


def _canon_names():
    return [n["Name"] for n in get_canon_npcs_copy()]


@pytest.mark.parametrize("a,b", [
    ("Робб Старк", "Роберт Баратеон"), ("Джон Сноу", "Джон Аррен"),
    ("Бенджен Старк", "Еддард Старк"), ("Санса Старк", "Арья Старк"),
    ("Візеріс Таргарієн", "Дейнеріс Таргарієн"), ("Едмур Таллі", "Еддард Старк"),
])
def test_distinct_canon_pairs_do_not_match(a, b):
    names = set(_canon_names())
    for n in (a, b):
        if n not in names:
            pytest.skip(f"{n} відсутній у canon_npc")
    assert world._is_same_hero(a, b) is False


# Відомі колізії порогу 0.8 у реальному canon (hero X "збігається" і з сусіднім NPC Y -> виключаються обидва).
_COLLISIONS = {
    "Джорах Мормонт": {"Джіор Мормонт"}, "Джіор Мормонт": {"Джорах Мормонт"},
    "Теон Грейджой": {"Еурон Грейджой"}, "Еурон Грейджой": {"Теон Грейджой"},
    "Роберт Баратеон": {"Роберт Аррен"}, "Роберт Аррен": {"Роберт Баратеон"},
}


def test_each_canon_name_matches_only_itself_except_known_collisions():
    names = _canon_names()
    for hero in names:
        others = {n for n in names if n != hero and world._is_same_hero(hero, n)}
        assert others == _COLLISIONS.get(hero, set()), (hero, others)


@pytest.mark.xfail(strict=True, reason="BUG: поріг 0.8 колізує Джорах/Джіор Мормонт, Теон/Еурон Грейджой, "
                                       "Роберт Баратеон/Роберт Аррен -> герой з таким іменем виключає обох NPC")
def test_no_canon_collisions_at_threshold():
    names = _canon_names()
    for hero in names:
        assert [n for n in names if world._is_same_hero(hero, n)] == [hero]


def test_anchoring_picks_own_entry_even_on_collision():
    for npc in get_canon_npcs_copy():
        hero = npc["Name"]
        pos = world._canon_start_position(hero)
        if is_valid_location(npc["Location"]):
            exp_scene = npc["Scene"] if is_valid_scene(npc["Location"], npc["Scene"]) else world._hub_scene(npc["Location"])
            assert pos == (npc["Location"], exp_scene), hero
        else:
            assert pos is None, hero


def test_exclusion_set_is_superset_of_anchoring_match_for_every_canon_npc():
    """Узгодженість: той, до кого прив'язали героя, завжди входить у множину виключених двійників."""
    canon = get_canon_npcs_copy()
    for npc in canon:
        hero = npc["Name"]
        excluded = {n["Name"] for n in canon if world._is_same_hero(hero, n["Name"])}
        assert hero in excluded


def test_canon_start_position_picks_highest_ratio_candidate():
    fake = [{"Name": "Аарон Вест", "Location": "Вінтерфел", "Scene": "Великий Зал"},
            {"Name": "Аарон Вестн", "Location": "Королівська Гавань", "Scene": get_scenes_for_location("Королівська Гавань")[0]}]
    with patch.object(world, "get_canon_npcs_copy", lambda: fake):
        assert world._canon_start_position("Аарон Вест")[0] == "Вінтерфел"


def test_scene_correction_close_typo_corrected():
    loc = _loc_with_scenes()
    real = max(get_scenes_for_location(loc), key=len)
    assert world._validate_start_position(loc, real[:-1] + "ъ", "Північ") == (loc, real)


def test_scene_correction_far_goes_to_hub0():
    loc = _loc_with_scenes()
    assert world._validate_start_position(loc, "Абвгд Еєжз", "Північ") == (loc, LOCATION_SCENES[loc]["hub"][0])


def test_scene_correction_uses_cutoff_constant():
    loc = _loc_with_scenes()
    real = get_scenes_for_location(loc)[-1]
    with patch.object(world, "SCENE_CORRECTION_CUTOFF", 1.0):
        assert world._validate_start_position(loc, real[:-1] + "ъ", "Північ")[1] == LOCATION_SCENES[loc]["hub"][0]


# ============================ _find_hero_twin ============================

@pytest.mark.parametrize("hero,twin,survivor", [
    ("Джорах Мормонт", "Джорах Мормонт", "Джіор Мормонт"),
    ("Джіор Мормонт", "Джіор Мормонт", "Джорах Мормонт"),
    ("Теон Грейджой", "Теон Грейджой", "Еурон Грейджой"),
    ("Еурон Грейджой", "Еурон Грейджой", "Теон Грейджой"),
    ("Роберт Баратеон", "Роберт Баратеон", "Роберт Аррен"),
    ("Роберт Аррен", "Роберт Аррен", "Роберт Баратеон"),
])
def test_find_hero_twin_picks_only_own_entry(hero, twin, survivor):
    names = _canon_names()
    if hero not in names or survivor not in names:
        pytest.skip("імена відсутні у canon")
    assert world._find_hero_twin(hero, names) == twin
    assert world._find_hero_twin(hero, names) != survivor


def test_find_hero_twin_case_insensitive():
    assert world._find_hero_twin("ЕДДАРД СТАРК", _canon_names()) == "Еддард Старк"


def test_find_hero_twin_none_when_no_match():
    assert world._find_hero_twin("Лорд Тестовий Невідомий", _canon_names()) is None


@pytest.mark.parametrize("hero,cands", [("", ["Еддард Старк"]), (None, ["Еддард Старк"]),
                                        ("Еддард Старк", []), ("Еддард Старк", [""])])
def test_find_hero_twin_empty_inputs(hero, cands):
    assert world._find_hero_twin(hero, cands) is None


def test_find_hero_twin_equal_ratio_first_in_list():
    # однакові (case-різні) імена -> ratio однаковий -> перший у списку
    assert world._find_hero_twin("Аарон Вест", ["аарон вест", "АААРОН ВЕСТ".replace("АААРОН", "Аарон")]) == "аарон вест"


def test_ai_npc_with_hero_name_is_best_match_vs_canon_pool():
    """Правило populate_contextual_npcs: ai відкидається, якщо найкращий матч героя серед [ai]+canon == ai
    (ai першим -> при рівному ratio виграє ai)."""
    canon = _canon_names()
    assert world._find_hero_twin("Еддард Старк", ["Еддард Старк"] + canon) == "Еддард Старк"


def test_ai_npc_similar_but_worse_than_canon_twin_is_kept():
    canon = _canon_names()
    ai = "Еддард Старкс"  # схожий, але гірший за canon-двійника
    assert world._is_same_hero("Еддард Старк", ai)
    assert world._find_hero_twin("Еддард Старк", [ai] + canon) == "Еддард Старк"  # != ai -> ai не відкидається


# ============================ background_canon_generation: записується без одного двійника ============================

class _FakeWS:
    def __init__(self):
        self.rows = []
        self.header = None

    def clear(self):
        pass

    def append_row(self, h):
        self.header = h

    def append_rows(self, batch):
        self.rows.extend(batch)

    def get_all_values(self):
        return [self.header] + self.rows


def _run_bg(hero):
    ws = _FakeWS()
    fake_db = MagicMock()
    fake_db.get_sheet.return_value = ws
    with patch.object(world, "db", fake_db), \
            patch.object(world, "ensure_user_npc_sheet", AsyncMock()), \
            patch.object(world, "refresh_npc_database", AsyncMock(return_value=True)), \
            patch("time.sleep", lambda *_: None):
        asyncio.run(world.background_canon_generation(987003, excluded_name=hero))
    return [r[2] for r in ws.rows]


@pytest.mark.parametrize("hero,survivor", [
    ("Джорах Мормонт", "Джіор Мормонт"), ("Теон Грейджой", "Еурон Грейджой"),
    ("Роберт Баратеон", "Роберт Аррен"), ("Роберт Аррен", "Роберт Баратеон"),
])
def test_background_canon_excludes_only_one_twin(hero, survivor):
    all_names = _canon_names()
    written = _run_bg(hero)
    assert hero not in written
    assert survivor in written
    assert len(written) == len(all_names) - 1


def test_background_canon_no_excluded_writes_all():
    assert len(_run_bg(None)) == len(_canon_names())


def test_background_canon_custom_name_writes_all():
    assert len(_run_bg("Лорд Тестовий Невідомий")) == len(_canon_names())
