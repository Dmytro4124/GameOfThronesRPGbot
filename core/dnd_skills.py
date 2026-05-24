"""D&D 5e skills mapped to 6 abilities, adapted for ASoIaF setting."""

# D&D 5e PHB p.61: 18 skills, each governed by one of 6 abilities.
# ASoIaF notes:
#   Arcana ≈ knowledge of dragons/Valyria/Other magic — rare but real.
#   Religion ≈ knowledge of the Seven, Old Gods, R'hllor, Drowned God.
SKILLS: dict[str, str] = {
    # STR
    "Athletics": "STR",
    # DEX
    "Acrobatics": "DEX",
    "Sleight of Hand": "DEX",
    "Stealth": "DEX",
    # INT
    "Arcana": "INT",
    "History": "INT",
    "Investigation": "INT",
    "Nature": "INT",
    "Religion": "INT",
    # WIS
    "Animal Handling": "WIS",
    "Insight": "WIS",
    "Medicine": "WIS",
    "Perception": "WIS",
    "Survival": "WIS",
    # CHA
    "Deception": "CHA",
    "Intimidation": "CHA",
    "Performance": "CHA",
    "Persuasion": "CHA",
}


def skill_modifier(profile: dict, skill_name: str) -> int:
    """Return total modifier: ability_mod + prof_bonus (if proficient) + prof_bonus again (if expertise)."""
    from core.dnd_core import ability_modifier, proficiency_bonus  # noqa: PLC0415

    ability = get_ability_for_skill(skill_name)
    score = profile.get("ability_scores", {}).get(ability, 10)
    ab_mod = ability_modifier(score)
    level = profile.get("level", 1)
    pb = proficiency_bonus(level)

    skill_expertise: list[str] = profile.get("skill_expertise", [])
    skill_profs: list[str] = profile.get("skill_profs", [])

    if skill_name in skill_expertise:
        return ab_mod + pb * 2
    if skill_name in skill_profs:
        return ab_mod + pb
    return ab_mod


def is_valid_skill(name: str) -> bool:
    """Return True if name is one of the 18 recognised D&D 5e skills."""
    return name in SKILLS


def get_ability_for_skill(skill_name: str) -> str:
    """Return the governing ability ('STR'|'DEX'|'CON'|'INT'|'WIS'|'CHA'). Raises KeyError for unknown skill."""
    return SKILLS[skill_name]
