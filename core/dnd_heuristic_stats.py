"""Deterministic D&D 5e statblock derivation from canon NPC text fields.

Matches keywords in NPC Description/Character/Status to assign:
- Challenge Rating (CR)
- Ability scores (STR/DEX/CON/INT/WIS/CHA)
- HP, AC, Speed
- Attacks (weapon dice + damage type)
- Saving throw proficiencies
- Skill modifiers (subset of 18 D&D skills)
- Tags (humanoid/noble/westerosi/etc.)

Used by scripts/dnd_migrate.py --deterministic to populate canon_npc.py
without calling Gemini API.

NO I/O, NO LLM, NO async. Pure Python.
"""

from __future__ import annotations

from typing import Union

# ── CR-to-HP mapping ─────────────────────────────────────────────────────────
# Scaled down for text-RPG combat — 1-5 round fights.
CR_HP_TABLE: dict[str, int] = {
    "0":   6,    "1/8": 12,   "1/4": 16,   "1/2": 22,
    "1":   32,   "2":   45,   "3":   60,   "4":   75,
    "5":   90,   "6":   110,  "7":   130,  "8":   150,
    "9":   170,  "10":  200,
}

# ── Role templates ────────────────────────────────────────────────────────────
# Each entry: ability scores, AC, speed, saves, skills, tags.
ROLE_TEMPLATES: dict[str, dict] = {
    "commoner": {
        "abilities": {"STR": 10, "DEX": 10, "CON": 10, "INT": 10, "WIS": 10, "CHA": 10},
        "ac": 10, "speed": 30, "saves": {},
        "skills": {}, "tags": ["humanoid", "commoner"],
    },
    "smallfolk": {  # peasant, miller, blacksmith
        "abilities": {"STR": 12, "DEX": 10, "CON": 11, "INT": 9, "WIS": 11, "CHA": 9},
        "ac": 10, "speed": 30, "saves": {},
        "skills": {"Athletics": 1}, "tags": ["humanoid", "smallfolk"],
    },
    "guard": {  # вартовий, гайдук, household guard
        "abilities": {"STR": 13, "DEX": 12, "CON": 12, "INT": 10, "WIS": 11, "CHA": 10},
        "ac": 14, "speed": 30, "saves": {"STR": 3},
        "skills": {"Perception": 2, "Athletics": 3},
        "tags": ["humanoid", "soldier"],
    },
    "bandit": {  # бандит, розбійник, smuggler
        "abilities": {"STR": 11, "DEX": 13, "CON": 12, "INT": 10, "WIS": 10, "CHA": 10},
        "ac": 12, "speed": 30, "saves": {},
        "skills": {"Stealth": 3, "Sleight of Hand": 3},
        "tags": ["humanoid", "bandit"],
    },
    "spy": {  # шпигун, варіс, маленькі пташки
        "abilities": {"STR": 10, "DEX": 15, "CON": 11, "INT": 14, "WIS": 13, "CHA": 13},
        "ac": 12, "speed": 30, "saves": {"DEX": 5, "INT": 4},
        "skills": {
            "Stealth": 5, "Deception": 3, "Insight": 3,
            "Persuasion": 3, "Investigation": 4,
        },
        "tags": ["humanoid", "spy", "intriguer"],
    },
    "knight": {  # лицар, сер X
        "abilities": {"STR": 14, "DEX": 12, "CON": 14, "INT": 10, "WIS": 11, "CHA": 13},
        "ac": 18, "speed": 30, "saves": {"STR": 4, "CON": 4},
        "skills": {"Athletics": 4, "Intimidation": 3},
        "tags": ["humanoid", "knight", "noble"],
    },
    "hedge_knight": {  # мандрівний лицар
        "abilities": {"STR": 13, "DEX": 13, "CON": 13, "INT": 10, "WIS": 11, "CHA": 11},
        "ac": 14, "speed": 30, "saves": {"STR": 3},
        "skills": {"Athletics": 3, "Survival": 2},
        "tags": ["humanoid", "hedge-knight"],
    },
    "elite_knight": {  # Барристан, Лорас, відомі лицарі
        "abilities": {"STR": 16, "DEX": 14, "CON": 15, "INT": 11, "WIS": 12, "CHA": 14},
        "ac": 19, "speed": 30, "saves": {"STR": 6, "CON": 5},
        "skills": {"Athletics": 6, "Intimidation": 4, "Insight": 3},
        "tags": ["humanoid", "elite-knight", "noble"],
    },
    "champion": {  # Григор Клеган, Сандор Клеган, Дрого, легендарні бійці
        "abilities": {"STR": 20, "DEX": 13, "CON": 18, "INT": 10, "WIS": 11, "CHA": 11},
        "ac": 18, "speed": 30, "saves": {"STR": 9, "CON": 8},
        "skills": {"Athletics": 9, "Intimidation": 4},
        "tags": ["humanoid", "champion", "monster-like"],
    },
    "maester": {
        "abilities": {"STR": 9, "DEX": 10, "CON": 11, "INT": 16, "WIS": 14, "CHA": 11},
        "ac": 10, "speed": 30, "saves": {"INT": 5, "WIS": 4},
        "skills": {
            "Medicine": 4, "History": 5, "Investigation": 5,
            "Insight": 4, "Arcana": 5,
        },
        "tags": ["humanoid", "scholar", "maester"],
    },
    "septon": {
        "abilities": {"STR": 10, "DEX": 10, "CON": 12, "INT": 12, "WIS": 14, "CHA": 13},
        "ac": 11, "speed": 30, "saves": {"WIS": 4, "CHA": 3},
        "skills": {"Religion": 3, "Persuasion": 3, "Insight": 4, "Medicine": 4},
        "tags": ["humanoid", "cleric", "septon"],
    },
    "courtier": {  # придворний, інтриган, посол
        "abilities": {"STR": 9, "DEX": 11, "CON": 11, "INT": 14, "WIS": 13, "CHA": 16},
        "ac": 10, "speed": 30, "saves": {"CHA": 5, "INT": 4},
        "skills": {
            "Persuasion": 7, "Deception": 5, "Insight": 5,
            "History": 4, "Performance": 3,
        },
        "tags": ["humanoid", "noble", "courtier"],
    },
    "noble_lord": {  # лорд, лорд-капітан, голова дому
        "abilities": {"STR": 12, "DEX": 11, "CON": 13, "INT": 13, "WIS": 12, "CHA": 15},
        "ac": 16, "speed": 30, "saves": {"CHA": 4, "WIS": 3},
        "skills": {
            "Persuasion": 4, "History": 3, "Intimidation": 4,
            "Insight": 3, "Athletics": 3,
        },
        "tags": ["humanoid", "noble", "lord"],
    },
    "great_lord": {  # Тайвін, Ед Старк, голови Великих Домів
        "abilities": {"STR": 13, "DEX": 11, "CON": 14, "INT": 15, "WIS": 14, "CHA": 16},
        "ac": 17, "speed": 30, "saves": {"CHA": 6, "WIS": 5, "INT": 5},
        "skills": {
            "Persuasion": 6, "History": 5, "Intimidation": 6,
            "Insight": 5, "Athletics": 3,
        },
        "tags": ["humanoid", "noble", "great-lord"],
    },
    "king": {  # Роберт, Серсея на троні, Дені
        "abilities": {"STR": 14, "DEX": 11, "CON": 14, "INT": 13, "WIS": 13, "CHA": 17},
        "ac": 17, "speed": 30, "saves": {"CHA": 7, "WIS": 5, "CON": 5},
        "skills": {
            "Persuasion": 7, "History": 4, "Intimidation": 7,
            "Insight": 4, "Athletics": 4,
        },
        "tags": ["humanoid", "noble", "royalty"],
    },
    "child": {  # дитина, Бран ≤ 10 років
        "abilities": {"STR": 8, "DEX": 12, "CON": 10, "INT": 11, "WIS": 10, "CHA": 11},
        "ac": 10, "speed": 25, "saves": {},
        "skills": {}, "tags": ["humanoid", "child"],
    },
    "wildling": {  # вільний народ
        "abilities": {"STR": 14, "DEX": 13, "CON": 15, "INT": 9, "WIS": 11, "CHA": 10},
        "ac": 12, "speed": 35, "saves": {"STR": 4, "CON": 4},
        "skills": {"Survival": 3, "Athletics": 4, "Intimidation": 2},
        "tags": ["humanoid", "wildling", "free-folk"],
    },
}

# ── CR per role (default) ─────────────────────────────────────────────────────
ROLE_CR: dict[str, str] = {
    "commoner":    "0",
    "smallfolk":   "0",
    "child":       "0",
    "guard":       "1/4",
    "bandit":      "1/8",
    "hedge_knight": "1",
    "spy":         "2",
    "knight":      "3",
    "septon":      "1",
    "maester":     "1",
    "courtier":    "2",
    "noble_lord":  "4",
    "elite_knight": "5",
    "great_lord":  "5",
    "king":        "7",
    "champion":    "8",
    "wildling":    "1",
}

# ── Weapons per role ──────────────────────────────────────────────────────────
ROLE_WEAPONS: dict[str, list[dict]] = {
    "commoner":     [{"name": "Кулаки",       "to_hit": 0,  "dmg": "1d4 bludgeoning"}],
    "smallfolk":    [{"name": "Серп",         "to_hit": 1,  "dmg": "1d4+1 slashing"}],
    "guard":        [{"name": "Спис",         "to_hit": 3,  "dmg": "1d6+1 piercing", "range": 20}],
    "bandit":       [{"name": "Кинджал",      "to_hit": 3,  "dmg": "1d4+1 piercing"}],
    "spy":          [{"name": "Кинджал",      "to_hit": 4,  "dmg": "1d4+2 piercing"}],
    "knight":       [{"name": "Довгий меч",   "to_hit": 4,  "dmg": "1d8+2 slashing"}],
    "hedge_knight": [{"name": "Меч",          "to_hit": 3,  "dmg": "1d8+1 slashing"}],
    "elite_knight": [{"name": "Довгий меч",   "to_hit": 6,  "dmg": "1d8+3 slashing"}],
    "champion":     [{"name": "Великий меч",  "to_hit": 7,  "dmg": "2d6+5 slashing"}],
    "maester":      [{"name": "Палиця",       "to_hit": -1, "dmg": "1d6-1 bludgeoning"}],
    "septon":       [{"name": "Кий",          "to_hit": 0,  "dmg": "1d6 bludgeoning"}],
    "courtier":     [{"name": "Рапіра",       "to_hit": 3,  "dmg": "1d8+1 piercing"}],
    "noble_lord":   [{"name": "Довгий меч",   "to_hit": 3,  "dmg": "1d8+1 slashing"}],
    "great_lord":   [{"name": "Довгий меч",   "to_hit": 4,  "dmg": "1d8+1 slashing"}],
    "king":         [{"name": "Бойова сокира","to_hit": 5,  "dmg": "1d8+2 slashing"}],
    "child":        [{"name": "Кулачки",      "to_hit": -1, "dmg": "1d2 bludgeoning"}],
    "wildling":     [{"name": "Великий топір","to_hit": 4,  "dmg": "1d12+2 slashing"}],
}

# ── Valid CR set (mirrors core/dnd_migration._VALID_CR) ───────────────────────
VALID_CR: frozenset[str] = frozenset(CR_HP_TABLE.keys())


# ── Classification ────────────────────────────────────────────────────────────

# ── Named NPC overrides (exact-name lookup, runs BEFORE keyword heuristic) ──
# Lowercase name → role. Covers canon characters where description-keyword
# matching is unreliable (e.g. "Тиріон" has no "лорд" in his Description but
# is clearly a courtier; "Григор Кліган" has both Кліган/Клеган spellings).
NAMED_OVERRIDES: dict[str, str] = {
    # Stark
    "едард старк": "great_lord", "нед старк": "great_lord",
    "кейтлін старк": "noble_lord", "кейтілін старк": "noble_lord",
    "робб старк": "noble_lord", "санса старк": "noble_lord",
    "арія старк": "child", "брандон старк": "child", "бран старк": "child",
    "ріккон старк": "child", "джон сноу": "hedge_knight",
    "теон грейджой": "knight",
    # Lannister
    "тайвін ланністер": "great_lord", "тайвин ланністер": "great_lord",
    "серсея ланністер": "courtier", "серсі ланністер": "courtier",
    "джеймі ланністер": "elite_knight", "джейме ланністер": "elite_knight",
    "тиріон ланністер": "courtier", "тіріон ланністер": "courtier",
    "кеван ланністер": "noble_lord",
    # Baratheon
    "роберт баратеон": "king",
    "станніс баратеон": "noble_lord",
    "ренлі баратеон": "noble_lord",
    "джофрі баратеон": "king", "джоффрі баратеон": "king",
    # Targaryen
    "дейнеріс таргарієн": "king", "дайнеріс таргарієн": "king",
    "візерис таргарієн": "courtier", "визерис таргарієн": "courtier",
    # Clegane
    "григор кліган": "champion", "григор клеган": "champion",
    "сандор кліган": "elite_knight", "сандор клеган": "elite_knight",
    # Dothraki
    "кхал дрого": "champion", "каль дрого": "champion",
    # King's Landing court
    "варіс": "spy", "лорд варіс": "spy",
    "пітір бейліш": "spy", "мізинець": "spy", "петір бейліш": "spy",
    "пайцел": "maester", "великий мейстер пайцел": "maester",
    "барристан селмі": "elite_knight", "сер барристан селмі": "elite_knight",
    # Tyrell
    "мейс тірелл": "noble_lord",
    "лорас тірелл": "elite_knight",
    "марджері тірелл": "courtier",
    "оленна тірелл": "courtier", "королева шипів": "courtier",
    # Others
    "мейстер лювін": "maester", "лювін": "maester",
    "ходор": "guard",  # big and strong but simple
    "санса": "noble_lord",  # сам ім'я як fallback
    "арія": "child",
    "бран": "child",
}


def classify_role(npc: dict) -> str:
    """Determine NPC role from text fields.

    Priority order (first match wins):
      NAMED_OVERRIDES > champion > king > great_lord > elite_knight > maester >
      septon > spy > courtier > knight (hedge/regular) > bandit > guard >
      wildling > noble_lord > child > smallfolk > commoner (fallback)

    Args:
        npc: NPC dict with at least one of: Name, Description, Character, Goal.

    Returns:
        Role key (always present in ROLE_TEMPLATES).
    """
    name = npc.get("Name", "").lower().strip()

    # Named NPC overrides take absolute priority (canon character classification).
    if name in NAMED_OVERRIDES:
        return NAMED_OVERRIDES[name]
    # Partial-name match (e.g. "Сер Сандор Кліган" matches "сандор кліган")
    for canon_name, role in NAMED_OVERRIDES.items():
        if canon_name in name:
            return role

    desc = (
        npc.get("Description", "") + " "
        + npc.get("Character", "") + " "
        + npc.get("Goal", "")
    ).lower()

    # ── Champions / legendary fighters ──
    if any(kw in name for kw in ["григор клеган", "григор кліган", "гора", "дрого", "халіс мо"]):
        return "champion"
    # Also catch by description for unnamed champions
    if any(kw in desc for kw in ["найстрашніший боєць", "непереможний воїн", "кхал дрого"]):
        return "champion"

    # ── Royalty ──
    # Use word-boundary-aware patterns: "король" / "королева" / "король " to avoid
    # matching "Королівська гвардія" or "Королівська гавань" which contain "корол"
    # as a substring but denote a place/institution, not a royal title.
    _royalty_kw = ["є королем", "є королевою", "залізний трон", "сидить на троні",
                   "керує вестеросом", "правитель семи королівств", "владика семи",
                   " трон", " престол"]
    # Also match standalone "король" / "королева" but NOT "королівська" (adjective)
    _royalty_words = ["король", "королева", "королю", "королівства"]
    if (any(kw in desc for kw in _royalty_kw)
            or any(" " + w in desc or desc.startswith(w) for w in _royalty_words)):
        return "king"

    # ── Great Lords (heads of Great Houses) ──
    _great_lord_kw = [
        "великий лорд", "глава дому", "правитель півночі", "правитель заходу",
        "правитель сходу", "правитель облоги",
    ]
    _great_lord_names = ["тайвін", "едард старк", "ед старк", "хайтгарден"]
    if any(kw in desc for kw in _great_lord_kw) or any(kw in name for kw in _great_lord_names):
        return "great_lord"

    # ── Elite knights — Kingsguard, named champions ──
    _elite_kw = [
        "барристан", "лорас", "королівська гвардія", "kingsguard",
        "найкращий мечник", "знаменитий лицар", "лицар сімох",
    ]
    if any(kw in desc for kw in _elite_kw) or any(kw in name for kw in ["барристан", "лорас тірелл"]):
        return "elite_knight"

    # ── Maesters ──
    if any(kw in desc for kw in ["мейстер", "цитадел", "ланцюг знань"]) or "мейстер" in name:
        return "maester"

    # ── Septons ──
    if any(kw in desc for kw in ["септон", "сім богів", "віровчитель"]) or "септон" in name:
        return "septon"

    # ── Spies / Whisperers ──
    if any(kw in desc for kw in ["шпигун", "павук", "маленькі пташки", "розвідник", "інформатор"]):
        return "spy"

    # ── Courtiers (intriguers without primary combat focus) ──
    _courtier_kw = [
        "придворн", "інтриган", "посол", "дипломат", "радник",
        "майстер монети", "майстер шепоту", "правиця короля",
    ]
    if any(kw in desc for kw in _courtier_kw):
        return "courtier"

    # ── Knights ──
    if any(kw in desc for kw in ["лицар", "сер ", "вершник", "найманець-капітан"]):
        if "мандрівн" in desc or "хедж" in desc:
            return "hedge_knight"
        return "knight"

    # ── Bandits / smugglers / mercenaries ──
    if any(kw in desc for kw in ["бандит", "розбійник", "контрабандист", "найманець", "піра"]):
        return "bandit"

    # ── Guards (city/household) ──
    if any(kw in desc for kw in ["вартов", "гайдук", "охорон", "стража"]):
        return "guard"

    # ── Wildlings ──
    if any(kw in desc for kw in ["вільний народ", "вільного народу", "дикун", "за стіною"]):
        return "wildling"

    # ── General nobility (lord without "great") ──
    if any(kw in desc for kw in ["лорд ", "леді ", "благородн", "дім ", "дочка дому", "син дому"]):
        return "noble_lord"

    # ── Children ──
    if any(kw in desc for kw in ["дитин", "хлопчик", "дівчинка", "малюк"]):
        return "child"

    # ── Smallfolk ──
    if any(kw in desc for kw in ["селян", "коваль", "тесля", "мірошник", "пекар", "шинкар", "торговец"]):
        return "smallfolk"

    # ── Default ──
    return "commoner"


def derive_stats_from_npc(npc: dict) -> dict:
    """Return a D&D statblock dict derived deterministically from NPC text.

    Output keys:
        cr, ability_scores, hp_max, hp_current, ac, speed,
        attacks, saves, skills, conditions, tags, _heuristic_role

    The ``_heuristic_role`` key is for debugging — makes it easy to spot
    misclassifications without re-running classify_role separately.

    Args:
        npc: NPC dict (read-only, not mutated).

    Returns:
        New dict with D&D statblock fields only (NOT merged with npc).
        Call ``apply_stats_to_canon_npcs`` or merge manually.
    """
    role = classify_role(npc)
    template = ROLE_TEMPLATES[role]
    cr = ROLE_CR[role]
    hp = CR_HP_TABLE[cr]

    return {
        "cr": cr,
        "ability_scores": dict(template["abilities"]),
        "hp_max": hp,
        "hp_current": hp,
        "ac": template["ac"],
        "speed": template["speed"],
        "attacks": [dict(w) for w in ROLE_WEAPONS[role]],
        "saves": dict(template["saves"]),
        "skills": dict(template["skills"]),
        "conditions": [],
        "tags": list(template["tags"]),
        "_heuristic_role": role,
    }


def apply_stats_to_canon_npcs(canon_npcs: Union[tuple, list]) -> list[dict]:
    """Iterate canon NPC list, add D&D stats to each, return updated list.

    Idempotent: if an NPC already has ``ability_scores``, it is copied as-is
    without overwriting any existing D&D fields. This makes it safe to run
    multiple times on a partially-migrated list.

    Args:
        canon_npcs: Iterable of NPC dicts (tuple or list). Not mutated.

    Returns:
        New list[dict] of same length. Each element is a shallow copy of the
        original NPC, potentially with D&D fields merged in.
    """
    result: list[dict] = []
    for npc in canon_npcs:
        npc_copy = dict(npc)
        if "ability_scores" not in npc_copy:
            stats = derive_stats_from_npc(npc_copy)
            npc_copy.update(stats)
        result.append(npc_copy)
    return result
