"""6 ASoIaF heritages with D&D 5e-style ability bonuses and traits (Фаза 1)."""

import copy
from dataclasses import dataclass


@dataclass
class Trait:
    """A single heritage trait with a name and mechanical description."""
    name: str
    desc: str


@dataclass
class HeritageDef:
    """Full definition of a player heritage (replaces D&D race)."""
    name: str
    ability_bonuses: dict[str, int]
    # Keys with "any+1" or "any+1_b" indicate player-choice +1 to any ability.
    # Concrete keys ("STR", "CHA", etc.) are applied directly.
    speed: int                  # base walking speed in feet
    languages: list[str]
    traits: list[Trait]
    no_magic: bool              # True for heritages without supernatural heritage traits


# ---------------------------------------------------------------------------
# Heritage registry
# ---------------------------------------------------------------------------

HERITAGES: dict[str, HeritageDef] = {
    "Westerosi (Andal)": HeritageDef(
        name="Westerosi (Andal)",
        ability_bonuses={"any+1": 1, "any+1_b": 1},  # +1 to any two different abilities (player chooses)
        speed=30,
        languages=["Common Tongue"],
        traits=[
            Trait(
                name="Adaptable",
                desc=(
                    "You gain proficiency in one additional skill of your choice from any "
                    "of the 18 D&D 5e skills. The Andal people are broadly capable survivors."
                ),
            ),
        ],
        no_magic=True,
    ),

    "Valyrian Descent": HeritageDef(
        name="Valyrian Descent",
        ability_bonuses={"CHA": 2, "INT": 1},
        speed=30,
        languages=["Common Tongue", "High Valyrian"],
        traits=[
            Trait(
                name="Fire Resistance",
                desc=(
                    "You take half damage from fire, whether from mundane sources (burning "
                    "buildings, alchemist's fire) or magical ones (dragon breath, Red Priest "
                    "pyromancy). This is not full immunity."
                ),
            ),
            Trait(
                name="Dragon Bond",
                desc=(
                    "If a living dragon of bonding age exists in the world and you have "
                    "spent at least 1 month with it, you establish a telepathic link. The "
                    "dragon obeys simple commands while within 1 mile. "
                    "In 298 AC (default campaign start): no living dragons of bonding age "
                    "exist — this trait is dormant and confers no benefit until the story "
                    "changes. The GM decides when and if the trait awakens."
                ),
            ),
        ],
        no_magic=False,
    ),

    "First Men (Stark line)": HeritageDef(
        name="First Men (Stark line)",
        ability_bonuses={"WIS": 2, "CON": 1},
        speed=30,
        languages=["Common Tongue", "Old Tongue"],
        traits=[
            Trait(
                name="Wolf Bond",
                desc=(
                    "You begin play with one direwolf pup companion (CR 1/4, treat as wolf "
                    "stats per Monster Manual with HP 11, AC 13, bite attack +4 to hit "
                    "2d4+2 piercing). The pup grows and gains CR over levels (Phase 1b "
                    "defines the progression table). If the pup dies, it cannot be "
                    "replaced — the bond is permanently lost."
                ),
            ),
            Trait(
                name="Warging",
                desc=(
                    "Once per long rest, as an action, you attempt to project your mind into "
                    "a beast (not a humanoid) within 100 feet. Make a WIS saving throw "
                    "(DC 15). On success: you control the beast's body for up to 1 hour. "
                    "Your own body is unconscious and helpless during this time. You perceive "
                    "through the beast's senses. On a natural 1: you are trapped in the beast "
                    "for 24 hours. The beast can attempt to end the bond each hour (WIS save "
                    "DC 10 + your WIS modifier)."
                ),
            ),
        ],
        no_magic=False,
    ),

    "Free Folk": HeritageDef(
        name="Free Folk",
        ability_bonuses={"STR": 1},
        speed=35,  # hardened by survival, faster base movement
        languages=["Common Tongue", "Old Tongue"],
        traits=[
            Trait(
                name="Cold Bred",
                desc=(
                    "You are immune to extreme cold environmental damage as defined in the "
                    "DMG. You never suffer Exhaustion levels from cold weather alone. "
                    "This is innate hardening, not magic."
                ),
            ),
            Trait(
                name="No Kneeler",
                desc=(
                    "You have disadvantage on Persuasion checks when dealing with Westerosi "
                    "nobles who know you are Free Folk (you refuse to adopt the expected "
                    "deference). You have advantage on Intimidation checks against those "
                    "same nobles — your rejection of their customs unnerves them."
                ),
            ),
        ],
        no_magic=True,
    ),

    "Red Priest": HeritageDef(
        name="Red Priest",
        ability_bonuses={"CHA": 1, "WIS": 1},
        speed=30,
        languages=["Common Tongue", "High Valyrian"],
        traits=[
            Trait(
                name="Pyromancy",
                desc=(
                    "As an action, you call fire from R'hllor. Creatures in a 15-foot cone "
                    "must make a DEX saving throw (DC = 10 + your CHA modifier) or take "
                    "2d6 fire damage (half on success). Cost: you take 1d6 damage yourself "
                    "(no save, no resistance). This self-damage cannot reduce you below 1 HP. "
                    "There is no usage limit beyond the self-damage cost."
                ),
            ),
            Trait(
                name="Fire-Reading",
                desc=(
                    "Once per long rest, you spend 10 minutes gazing into an open flame and "
                    "commune with the Lord of Light. You may ask the GM one yes/no question "
                    "about a future event or the current status of a distant person or place. "
                    "The GM may answer truthfully, vaguely ('the flames are unclear'), or "
                    "misleadingly (R'hllor is not omniscient). The answer is never guaranteed."
                ),
            ),
        ],
        no_magic=False,
    ),

    "Ironborn": HeritageDef(
        name="Ironborn",
        ability_bonuses={"CON": 1, "STR": 1},
        speed=30,
        languages=["Common Tongue"],
        traits=[
            Trait(
                name="Water Affinity",
                desc=(
                    "While you are in or adjacent to seawater (not freshwater), you gain a "
                    "+1 bonus to CON checks and CON saving throws. You can hold your breath "
                    "for twice as long as normal (2 × CON score in seconds, or 2 × CON "
                    "modifier in minutes if using the simpler variant)."
                ),
            ),
            Trait(
                name="Reaver's Greed",
                desc=(
                    "You have advantage on Athletics checks made to climb rigging, ropes, or "
                    "ship-board structures. You have disadvantage on Persuasion checks with "
                    "any creature whose property you have stolen or raided within the last "
                    "30 days, if they are aware of it."
                ),
            ),
        ],
        no_magic=True,
    ),
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_heritage(name: str) -> HeritageDef:
    """Return the HeritageDef for the given name. Raises KeyError for unknown heritage."""
    return HERITAGES[name]


def apply_heritage_bonuses(
    profile: dict,
    heritage_name: str,
    ability_choices: list[str] | None = None,
) -> dict:
    """Apply heritage ability bonuses to profile['ability_scores'] and return updated profile.

    For Westerosi (Andal): ability_choices must be a list of exactly 2 different ability keys
    (e.g. ['STR', 'DEX']). Each gets +1. Other heritages ignore ability_choices.
    Scores are clamped at 20 (standard D&D maximum).
    """
    result = copy.deepcopy(profile)
    heritage = get_heritage(heritage_name)

    if "ability_scores" not in result:
        result["ability_scores"] = {}

    scores: dict[str, int] = result["ability_scores"]

    player_choice_keys = [k for k in heritage.ability_bonuses if k.startswith("any+")]

    if player_choice_keys:
        # Player-choice bonuses (Westerosi Andal): need ability_choices list
        if ability_choices is None:
            ability_choices = []
        # Validate: must be distinct valid ability names
        valid_abilities = {"STR", "DEX", "CON", "INT", "WIS", "CHA"}
        chosen: list[str] = []
        for ab in ability_choices:
            if ab in valid_abilities and ab not in chosen:
                chosen.append(ab)
        # Fill missing choices with first unchosen valid ability (graceful fallback)
        if len(chosen) < len(player_choice_keys):
            for ab in ("STR", "DEX", "CON", "INT", "WIS", "CHA"):
                if ab not in chosen:
                    chosen.append(ab)
                if len(chosen) >= len(player_choice_keys):
                    break
        for i, key in enumerate(player_choice_keys):
            target_ab = chosen[i] if i < len(chosen) else "STR"
            bonus = heritage.ability_bonuses[key]
            scores[target_ab] = min(20, scores.get(target_ab, 10) + bonus)
    else:
        # Direct ability bonuses
        for ability, bonus in heritage.ability_bonuses.items():
            scores[ability] = min(20, scores.get(ability, 10) + bonus)

    result["ability_scores"] = scores
    result["languages"] = list(
        set(result.get("languages", []) + heritage.languages)
    )
    return result


def list_heritages() -> list[str]:
    """Return sorted list of all available heritage names."""
    return sorted(HERITAGES.keys())
