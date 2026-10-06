"""Ідентичність героя: стать і родина (поля профілю "Стать" / "Родина").

Чисті функції без імпортів engine/world/handlers/operations (лише stdlib), щоб не було циклів.
"Родина" — list[{"name", "relation"}], relation — з погляду героя ("мати" = цей NPC — мати героя).
"""
import difflib

GENDER_MALE = "чоловіча"
GENDER_FEMALE = "жіноча"

FAMILY_NAME_MATCH_THRESHOLD = 0.8   # як HERO_NAME_MATCH_THRESHOLD у core/world.py
FAMILY_FIRST_WORD_THRESHOLD = 0.85  # особисте ім'я суворіше (Едмур != Едмар)
FAMILY_RELATION_MAX_LEN = 30
FAMILY_DEFAULT_LIMIT = 8

_MALE_TOKENS = {"чоловіча", "чоловік", "чоловічий", "male", "man", "m", "ч", "м", "чол"}
_FEMALE_TOKENS = {"жіноча", "жінка", "жіночий", "female", "woman", "f", "ж", "жін"}


def normalize_gender(raw) -> str:
    """Приводить стать до GENDER_MALE / GENDER_FEMALE; інше/None -> ""."""
    if not isinstance(raw, str):
        return ""
    token = raw.strip().casefold()
    if token in _MALE_TOKENS:
        return GENDER_MALE
    if token in _FEMALE_TOKENS:
        return GENDER_FEMALE
    return ""


def _norm_name(name) -> str:
    """Ключ порівняння імен: casefold, уніфіковані апострофи, схлопнуті пробіли."""
    s = str(name or "")
    for ch in ("'", "’", "ʼ", "`", "‘"):
        s = s.replace(ch, "'")
    return " ".join(s.casefold().split())


def _same_name(a, b) -> bool:
    """Симетричний fuzzy-збіг імен (відтворює логіку _is_same_hero з core/world.py)."""
    na, nb = _norm_name(a), _norm_name(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    return _name_ratio(na, nb) >= FAMILY_NAME_MATCH_THRESHOLD


def _name_ratio(na: str, nb: str) -> float:
    """Ratio двох нормалізованих імен з word-guard: особисте ім'я (перше слово) має збігатися
    >= порога окремо, інакше 0.0 (родичі зі спільним прізвищем не склеюються)."""
    na, nb = _fold_translit(na), _fold_translit(nb)   # Ар'я ~ Арья, Кейтлін ~ Кейтлин
    if not na or not nb:
        return 0.0
    wa, wb = na.split(), nb.split()
    first = difflib.SequenceMatcher(None, wa[0], wb[0]).ratio()
    if first < FAMILY_FIRST_WORD_THRESHOLD:
        return 0.0
    if len(wa) > 1 and len(wb) > 1:
        last = difflib.SequenceMatcher(None, wa[-1], wb[-1]).ratio()
        if last < FAMILY_NAME_MATCH_THRESHOLD:
            return 0.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def _fold_translit(s: str) -> str:
    """Уніфікація орфографічних варіантів перед порівнянням: апострофи/ь прибираються, і/ї/й -> и."""
    for ch in ("'", "ь"):
        s = s.replace(ch, "")
    return s.translate(str.maketrans({"і": "и", "ї": "и", "й": "и"}))


def _clean(s) -> str:
    """Схлопує whitespace і прибирає керівні символи (\\n, \\r, \\t тощо)."""
    s = "".join(ch if ch.isprintable() or ch.isspace() else " " for ch in str(s or ""))
    return " ".join(s.split())


_SPOUSE_WORDS = {"дружина", "чоловік", "жінка", "наречена", "наречений", "суджена", "суджений",
                 "подружжя", "spouse", "wife", "husband", "betrothed"}


def drop_spouses(family) -> list:
    """Новий список без подружжя (relation містить дружина/чоловік/жінка/наречен*/суджен*/spouse/...).
    Вхід не мутується; не-dict елементи відкидаються."""
    if not isinstance(family, list):
        return []
    out = []
    for m in family:
        if not isinstance(m, dict):
            continue
        rel = _clean(m.get("relation")).casefold()
        if any(w in rel for w in _SPOUSE_WORDS):
            continue
        out.append(dict(m))
    return out


def _canonical_name(name: str, canon_names) -> str:
    """Канонічне написання за fuzzy-збігом (>= 0.8, найвищий ratio); інакше name як є."""
    nq = _norm_name(name)
    best, best_ratio = None, 0.0
    for cand in canon_names or []:
        if not isinstance(cand, str) or not cand.strip():
            continue
        nc = _norm_name(cand)
        if not nc or not nq:
            continue
        ratio = 1.0 if nc == nq else _name_ratio(nq, nc)
        if ratio >= FAMILY_NAME_MATCH_THRESHOLD and ratio > best_ratio:
            best, best_ratio = cand.strip(), ratio
    return best or name


def normalize_family(raw, canon_names=None, hero_name: str = "", limit: int = FAMILY_DEFAULT_LIMIT) -> list:
    """Нормалізує родину з LLM: strip, відкидає порожні, relation <= 30 символів,
    name -> канонічне написання (fuzzy >= 0.8), без самого героя, без дублів, не більше limit."""
    if not isinstance(raw, list):
        return []
    result: list = []
    for item in raw:
        if len(result) >= max(0, int(limit)):
            break
        if not isinstance(item, dict):
            continue
        name = _clean(item.get("name"))
        relation = _clean(item.get("relation"))[:FAMILY_RELATION_MAX_LEN].strip()
        if not name or not relation:
            continue
        name = _canonical_name(name, canon_names)
        if hero_name and _same_name(hero_name, name):
            continue
        if any(_same_name(name, r["name"]) for r in result):
            continue
        result.append({"name": name, "relation": relation})
    return result


def get_gender(profile) -> str:
    """Безпечне читання статі з профілю (старі профілі/некоректні типи -> "")."""
    if not isinstance(profile, dict):
        return ""
    return normalize_gender(profile.get("Стать"))


def get_family(profile) -> list:
    """Безпечне читання родини з профілю (старі профілі/некоректні типи -> [])."""
    if not isinstance(profile, dict):
        return []
    raw = profile.get("Родина")
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = _clean(item.get("name"))
        relation = _clean(item.get("relation"))[:FAMILY_RELATION_MAX_LEN].strip()
        if name and relation:
            out.append({"name": name, "relation": relation})
    return out


def family_relation_map(profile) -> dict:
    """{name: relation} з профілю (ім'я як є; нормалізація для зіставлення — на споживачі)."""
    return {m["name"]: m["relation"] for m in get_family(profile)}


def format_family_line(family) -> str:
    """"мати — Кейтлін Старк; батько — Еддард Старк" або "" якщо порожньо."""
    if not isinstance(family, list):
        return ""
    parts = []
    for m in family:
        if not isinstance(m, dict):
            continue
        name = str(m.get("name") or "").strip()
        relation = str(m.get("relation") or "").strip()
        if name and relation:
            parts.append(f"{relation} — {name}")
    return "; ".join(parts)


def gender_address(gender) -> dict:
    """Звертання за статтю: {"lord": "лорд"/"леді", "child": "сину"/"доню"}; для невідомої — порожні."""
    g = normalize_gender(gender)
    if g == GENDER_MALE:
        return {"lord": "лорд", "child": "сину"}
    if g == GENDER_FEMALE:
        return {"lord": "леді", "child": "доню"}
    return {"lord": "", "child": ""}
