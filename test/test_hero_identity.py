"""core/hero_identity.py: стать і родина героя (чисті функції)."""
import pytest

from core.hero_identity import (
    normalize_gender, normalize_family, get_gender, get_family,
    family_relation_map, format_family_line, gender_address,
)

CANON = ["Кейтлін Старк", "Ар'я Старк", "Дейнеріс Таргарієн", "Візеріс Таргарієн",
         "Робб Старк", "Бран Старк", "Джейме Ланністер", "Серсея Ланністер"]


@pytest.mark.parametrize("raw,expected", [
    ("ч", "чоловіча"), ("Ч", "чоловіча"), ("male", "чоловіча"), ("MALE", "чоловіча"),
    ("m", "чоловіча"), ("чоловіча", "чоловіча"), ("  чоловіча ", "чоловіча"),
    ("ж", "жіноча"), ("female", "жіноча"), ("f", "жіноча"), ("жіноча", "жіноча"),
    ("Жіноча", "жіноча"),
    ("сміття", ""), ("", ""), (None, ""), (5, ""), ([], ""), ("other", ""),
])
def test_normalize_gender(raw, expected):
    assert normalize_gender(raw) == expected


@pytest.mark.parametrize("raw,canon", [
    ("Ар'я Старк", "Ар'я Старк"),
    ("Арья Старк", "Ар'я Старк"),
    ("Ар’я Старк", "Ар'я Старк"),
    ("Кейтлин Старк", "Кейтлін Старк"),
    ("кейтлін старк", "Кейтлін Старк"),
])
def test_normalize_family_canonicalises_names(raw, canon):
    out = normalize_family([{"name": raw, "relation": "мати"}], CANON)
    assert out == [{"name": canon, "relation": "мати"}]


@pytest.mark.parametrize("a,b", [
    ("Дейнеріс Таргарієн", "Візеріс Таргарієн"),
    ("Робб Старк", "Бран Старк"),
    ("Джейме Ланністер", "Серсея Ланністер"),
])
def test_normalize_family_keeps_distinct_relatives_with_same_surname(a, b):
    out = normalize_family([{"name": a, "relation": "брат"}, {"name": b, "relation": "сестра"}], CANON)
    assert [m["name"] for m in out] == [a, b]


@pytest.mark.parametrize("a,b", [
    ("Дейнеріс Таргарієн", "Візеріс Таргарієн"),
    ("Робб Старк", "Бран Старк"),
    ("Джейме Ланністер", "Серсея Ланністер"),
])
def test_normalize_family_distinct_even_without_canon_list(a, b):
    out = normalize_family([{"name": a, "relation": "брат"}, {"name": b, "relation": "брат"}])
    assert len(out) == 2


def test_normalize_family_drops_hero_himself():
    raw = [{"name": "Арья Старк", "relation": "сестра"}, {"name": "Робб Старк", "relation": "брат"}]
    out = normalize_family(raw, CANON, hero_name="Ар'я Старк")
    assert [m["name"] for m in out] == ["Робб Старк"]


def test_normalize_family_dedups_variants():
    raw = [{"name": "Кейтлін Старк", "relation": "мати"}, {"name": "Кейтлин Старк", "relation": "мати"}]
    assert len(normalize_family(raw, CANON)) == 1


def test_normalize_family_relation_truncated_to_30():
    out = normalize_family([{"name": "Х Старк", "relation": "а" * 80}])
    assert len(out[0]["relation"]) <= 30


def test_normalize_family_default_limit_8():
    raw = [{"name": f"Унікальне{chr(0x430 + i)}{chr(0x430 + i)}{chr(0x430 + i)} Ім", "relation": "кузен"}
           for i in range(15)]
    assert len(normalize_family(raw)) == 8


def test_normalize_family_custom_limit():
    raw = [{"name": "Альфа Старк", "relation": "брат"}, {"name": "Омега Тайрел", "relation": "брат"}]
    assert len(normalize_family(raw, limit=1)) == 1


@pytest.mark.parametrize("junk", [
    None, "рядок", 42, {}, [], [None, 1, "x"], [{"name": "", "relation": "мати"}],
    [{"name": "Х", "relation": ""}], [{"relation": "мати"}], [{"name": "Х"}],
])
def test_normalize_family_garbage_gives_empty(junk):
    assert normalize_family(junk, CANON) == []


def test_normalize_family_unknown_name_kept_as_is():
    out = normalize_family([{"name": "Вигаданий Невідомий", "relation": "кузен"}], CANON)
    assert out == [{"name": "Вигаданий Невідомий", "relation": "кузен"}]


# ---- get_* для старих профілів ----

@pytest.mark.parametrize("profile", [{}, {"Ім'я": "X"}, None, "str", 5, {"Стать": None}, {"Стать": "?"}])
def test_get_gender_old_or_bad_profile(profile):
    assert get_gender(profile) == ""


def test_get_gender_normalizes():
    assert get_gender({"Стать": "ж"}) == "жіноча"


@pytest.mark.parametrize("profile", [{}, None, "x", {"Родина": None}, {"Родина": "мати"},
                                      {"Родина": [None, 1, {"name": "", "relation": "x"}]}])
def test_get_family_old_or_bad_profile(profile):
    assert get_family(profile) == []


def test_get_family_returns_valid_items_only():
    p = {"Родина": [{"name": " Кейтлін Старк ", "relation": " мати "}, {"name": "Х"}, "junk"]}
    assert get_family(p) == [{"name": "Кейтлін Старк", "relation": "мати"}]


def test_family_relation_map():
    p = {"Родина": [{"name": "Кейтлін Старк", "relation": "мати"},
                    {"name": "Робб Старк", "relation": "брат"}]}
    assert family_relation_map(p) == {"Кейтлін Старк": "мати", "Робб Старк": "брат"}
    assert family_relation_map({}) == {}


def test_format_family_line():
    fam = [{"name": "Кейтлін Старк", "relation": "мати"}, {"name": "Еддард Старк", "relation": "батько"}]
    assert format_family_line(fam) == "мати — Кейтлін Старк; батько — Еддард Старк"


@pytest.mark.parametrize("fam", [[], None, "x", [None], [{"name": "", "relation": ""}]])
def test_format_family_line_empty(fam):
    assert format_family_line(fam) == ""


def test_gender_address():
    assert gender_address("ж")["child"] == "доню"
    assert gender_address("ч")["child"] == "сину"
    assert gender_address("")["lord"] == ""


# ---------------- word-guard / translit / _clean / drop_spouses ----------------

from core.hero_identity import _same_name, _fold_translit, drop_spouses  # noqa: E402


@pytest.mark.parametrize("a,b", [
    ("Едмур Таллі", "Едмар Таллі"),
    ("Дейнеріс Таргарієн", "Візеріс Таргарієн"),
    ("Робб Старк", "Бран Старк"),
    ("Джейме Ланністер", "Серсея Ланністер"),
])
def test_word_guard_different_people(a, b):
    assert not _same_name(a, b)
    assert not _same_name(b, a)


@pytest.mark.parametrize("a,b", [
    ("Ар'я Старк", "Арья Старк"),
    ("Кейтлін Старк", "Кейтлин Старк"),
    ("Ар’я Старк", "Арья Старк"),
])
def test_translit_variants_same_person(a, b):
    assert _same_name(a, b) and _same_name(b, a)


def test_fold_translit():
    assert _fold_translit("ар'я") == "аря"
    assert _fold_translit("кейтлін") == "кейтлин"
    assert _fold_translit("ьїй") == "ии"


def test_normalize_family_edmure_edmar_not_merged_into_canon():
    out = normalize_family([{"name": "Едмар Таллі", "relation": "дядько"}],
                           canon_names=["Едмур Таллі"])
    assert out[0]["name"] == "Едмар Таллі"


def test_normalize_family_translit_canonicalized():
    out = normalize_family([{"name": "Кейтлин Старк", "relation": "мати"}], canon_names=CANON)
    assert out[0]["name"] == "Кейтлін Старк"


def test_normalize_family_keeps_siblings_with_shared_surname():
    out = normalize_family([{"name": "Робб Старк", "relation": "брат"},
                            {"name": "Бран Старк", "relation": "брат"}])
    assert [m["name"] for m in out] == ["Робб Старк", "Бран Старк"]


def test_clean_collapses_control_chars_in_normalize_family():
    out = normalize_family([{"name": "Кейтлін\nСтарк\t", "relation": "ма\nти\r\x07 герою"}])
    assert out == [{"name": "Кейтлін Старк", "relation": "ма ти герою"}]


def test_clean_collapses_control_chars_in_get_family():
    prof = {"Родина": [{"name": "Робб\n\tСтарк\x00", "relation": "брат\n"}]}
    assert get_family(prof) == [{"name": "Робб Старк", "relation": "брат"}]


@pytest.mark.parametrize("rel", ["дружина", "чоловік", "наречена", "подружжя", "wife", "husband",
                                 "Дружина героя", "spouse", "наречений"])
def test_drop_spouses_removes(rel):
    assert drop_spouses([{"name": "X", "relation": rel}]) == []


@pytest.mark.parametrize("rel", ["мати", "брат", "син", "батько", "сестра"])
def test_drop_spouses_keeps(rel):
    assert drop_spouses([{"name": "X", "relation": rel}]) == [{"name": "X", "relation": rel}]


def test_drop_spouses_does_not_mutate_input():
    fam = [{"name": "A", "relation": "мати"}, {"name": "B", "relation": "дружина"}]
    snapshot = [dict(m) for m in fam]
    out = drop_spouses(fam)
    assert fam == snapshot and len(fam) == 2
    assert out == [{"name": "A", "relation": "мати"}]
    out[0]["name"] = "changed"
    assert fam[0]["name"] == "A"


@pytest.mark.parametrize("bad", [None, "x", 5, {}])
def test_drop_spouses_non_list(bad):
    assert drop_spouses(bad) == []


def test_drop_spouses_skips_non_dict_items():
    assert drop_spouses(["junk", None, {"name": "A", "relation": "брат"}]) == [{"name": "A", "relation": "брат"}]
