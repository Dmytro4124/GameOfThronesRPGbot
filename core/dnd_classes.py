"""9 GoT character classes with D&D 5e mechanics, L1-20 features (Фаза 1 + 1b).

Клас (class) і спадщина (heritage) — розв'язані поняття. Наприклад, клас Wildling
описує бойовий архетип вільного народу, але будь-яка спадщина може обрати цей клас.
Аналогічно персонаж із спадщиною Free Folk може обрати Knight або Bastard.
"""

from dataclasses import dataclass


@dataclass
class Feature:
    """A single class/heritage feature."""
    name: str
    desc: str
    source: str  # e.g. "Knight L1", "Heritage", "Fighting Style"


@dataclass
class ClassDef:
    """Full definition of a GoT character class."""
    name: str
    hit_die: int                      # 6, 8, 10, or 12
    primary_abilities: list[str]      # e.g. ["STR", "CHA"]
    saves_proficient: list[str]       # 2 ability saves per D&D canon
    armor_profs: list[str]            # e.g. ["light", "medium", "heavy", "shields"]
    weapon_profs: list[str]           # e.g. ["simple", "martial"]
    skill_choices: int                # how many skills the player picks from skill_pool
    skill_pool: list[str]             # which of the 18 skills are available
    level_features: dict[int, list[Feature]]   # {1: [...], 2: [...], ..., 20: [...]}
    starting_equipment: dict          # {"weapon_main", "weapon_off", "armor", "shield", "items", "gold"}
    description: str                  # short GoT lore blurb


# ---------------------------------------------------------------------------
# Helper: ASI feature factory
# ---------------------------------------------------------------------------

def _asi(class_name: str, level: int) -> Feature:
    return Feature(
        name="Ability Score Improvement",
        desc=(
            "Збільшити одну здібність на +2 АБО дві різні здібності на +1 кожну. "
            "Жодна здібність не може перевищити 20."
        ),
        source=f"{class_name} L{level}",
    )


# ---------------------------------------------------------------------------
# Helper: merge implemented features into a full L1-20 dict
# ---------------------------------------------------------------------------

def _build_level_features(
    class_name: str,
    implemented: dict[int, list[Feature]],
) -> dict[int, list[Feature]]:
    """Return full L1-20 feature dict. Every level must be present in implemented."""
    result: dict[int, list[Feature]] = {}
    for lvl in range(1, 21):
        result[lvl] = list(implemented[lvl])   # loose copy — protects GOT_CLASSES singleton
    return result


# ---------------------------------------------------------------------------
# GOT_CLASSES definition
# ---------------------------------------------------------------------------

GOT_CLASSES: dict[str, "ClassDef"] = {}


def _define_knight() -> ClassDef:
    name = "Knight"
    implemented = {
        1: [
            Feature(
                name="Лицарський кодекс",
                desc=(
                    "Перевага на рятівні кидки проти Переляку. Якщо мусиш збрехати про "
                    "дотримання клятви — перешкода на Обман."
                ),
                source="Knight L1",
            ),
            Feature(
                name="Обітниця служіння",
                desc=(
                    "Починаєш гру з оруженосцем-cohort (CR 0). Оруженосець виконує накази, "
                    "носить спорядження, не бʼється. Заміна: 1 тиждень і 10 зм."
                ),
                source="Knight L1",
            ),
        ],
        2: [
            Feature(
                name="Fighting Style",
                desc=(
                    "Обери один: Defense (+1 AC в броні), Dueling (+2 пошкоджень "
                    "одноручною зброєю при вільній другій руці), або Great Weapon "
                    "(перекидай 1 і 2 на кубиках пошкоджень дворучної зброї)."
                ),
                source="Knight L2",
            ),
        ],
        3: [
            Feature(
                name="Heroic Surge",
                desc=(
                    "1/short rest: бонусна дія — додаткова дія на ході. Можна: Attack, "
                    "Dash, Disengage, Hide або Use Object."
                ),
                source="Knight L3",
            ),
        ],
        4: [_asi(name, 4)],
        5: [
            Feature(
                name="Extra Attack",
                desc="Атакуєш двічі у рамках однієї дії Attack.",
                source="Knight L5",
            ),
        ],
        6: [
            Feature(
                name="Banner of House",
                desc=(
                    "Союзники в 30 фут. отримують +1 до рятівних кидків проти Переляку, "
                    "поки бачать твій прапор або чують твій голос."
                ),
                source="Knight L6",
            ),
        ],
        7: [
            Feature(
                name="Knight's Vigil",
                desc=(
                    "Перевага на кидки ініціативи. Не можеш бути Здивованим, якщо при "
                    "свідомості."
                ),
                source="Knight L7",
            ),
        ],
        8: [_asi(name, 8)],
        9: [
            Feature(
                name="Charge",
                desc=(
                    "Рухаєшся до ворога по прямій і атакуєш — при попаданні +2d6 пошкоджень. "
                    "1/хід."
                ),
                source="Knight L9",
            ),
        ],
        10: [
            Feature(
                name="Indomitable",
                desc=(
                    "1/long rest: перекидаєш провалений рятівний кидок і береш новий результат."
                ),
                source="Knight L10",
            ),
        ],
        11: [
            Feature(
                name="Second Wind",
                desc=(
                    "Бонусна дія: відновлюєш 1d10 + рівень HP. 1/short rest."
                ),
                source="Knight L11",
            ),
        ],
        12: [_asi(name, 12)],
        13: [
            Feature(
                name="Indomitable (2)",
                desc="Тепер 2/long rest: перекидаєш провалений рятівний кидок.",
                source="Knight L13",
            ),
        ],
        14: [
            Feature(
                name="Sworn Cohort",
                desc=(
                    "Оруженосець підвищується до повноцінного cohort рівня L/2 (ваш рівень "
                    "поділений на 2). Може брати участь у бою поруч."
                ),
                source="Knight L14",
            ),
        ],
        15: [
            Feature(
                name="Battle Master",
                desc=(
                    "4 maneuver dice d8 (Riposte, Disarm, Trip — обираєш 2 на rest). "
                    "Витрачаєш 1 die, щоб додати до атаки або захисту."
                ),
                source="Knight L15",
            ),
        ],
        16: [_asi(name, 16)],
        17: [
            Feature(
                name="Action Surge",
                desc=(
                    "1/short rest: отримуєш додаткову повноцінну дію на своєму ході."
                ),
                source="Knight L17",
            ),
        ],
        18: [
            Feature(
                name="Indomitable (3)",
                desc="Тепер 3/long rest: перекидаєш провалений рятівний кидок.",
                source="Knight L18",
            ),
        ],
        19: [_asi(name, 19)],
        20: [
            Feature(
                name="Lord of the Vanguard",
                desc=(
                    "Союзники в 60 фут. мають перевагу на атаки проти ворогів, яких ти "
                    "вразив цього ходу. Постійно активно."
                ),
                source="Knight L20",
            ),
        ],
    }
    return ClassDef(
        name=name,
        hit_die=10,
        primary_abilities=["STR", "CHA"],
        saves_proficient=["STR", "CON"],
        armor_profs=["light", "medium", "heavy", "shields"],
        weapon_profs=["simple", "martial"],
        skill_choices=2,
        skill_pool=["Athletics", "Intimidation", "Persuasion", "Insight", "Animal Handling", "History"],
        level_features=_build_level_features(name, implemented),
        starting_equipment={
            "weapon_main": "Довгий меч (1d8 рублюча, дворуч 1d10)",
            "weapon_off": None,
            "armor": "Кольчуга (КЗ 16)",
            "shield": True,
            "items": ["Геральдичний сюрко", "Скакун (rouncey)", "Спис"],
            "gold": 75,
        },
        description=(
            "A sworn knight of the Seven Kingdoms, bound by vows of chivalry to a liege lord. "
            "Knights are the backbone of Westerosi armies and the highest rank most common-born "
            "warriors can aspire to. Their word is their honour — or ought to be."
        ),
    )


def _define_hedge_knight() -> ClassDef:
    name = "Hedge Knight"
    implemented = {
        1: [
            Feature(
                name="Око мандрівника",
                desc=(
                    "+5 до Виживання в незнайомій місцевості (регіон, де ти не провів "
                    "мінімум тиждень поточної сесії)."
                ),
                source="Hedge Knight L1",
            ),
            Feature(
                name="Меч за плату",
                desc=(
                    "+2 до Переконання при торзі за роботу, оплату або турнірний внесок."
                ),
                source="Hedge Knight L1",
            ),
        ],
        2: [
            Feature(
                name="Fighting Style",
                desc=(
                    "Обери один: Defense (+1 AC в броні), Dueling (+2 пошкоджень "
                    "одноручною зброєю), або Great Weapon (перекидай 1 і 2 на кубиках "
                    "пошкоджень дворучної зброї)."
                ),
                source="Hedge Knight L2",
            ),
        ],
        3: [
            Feature(
                name="Tournament Veteran",
                desc=(
                    "1/long rest: оголошуєш перевагу на кидок ініціативи до кидання кубиків."
                ),
                source="Hedge Knight L3",
            ),
        ],
        4: [_asi(name, 4)],
        5: [
            Feature(
                name="Extra Attack",
                desc="Атакуєш двічі у рамках однієї дії Attack.",
                source="Hedge Knight L5",
            ),
        ],
        6: [
            Feature(
                name="Road Worn",
                desc=(
                    "Імунітет до Виснаження від маршу. Можеш спати в броні без штрафу до "
                    "відновлення."
                ),
                source="Hedge Knight L6",
            ),
        ],
        7: [
            Feature(
                name="Lucky Strike",
                desc=(
                    "1/short rest: перекидаєш провалений кидок атаки і береш новий результат."
                ),
                source="Hedge Knight L7",
            ),
        ],
        8: [_asi(name, 8)],
        9: [
            Feature(
                name="Tournament Glory",
                desc=(
                    "1/long rest у місті з турніром: виграєш 1d6×10 зм. і отримуєш +1 до "
                    "наступного перевірки CHA в тому ж місті."
                ),
                source="Hedge Knight L9",
            ),
        ],
        10: [
            Feature(
                name="Battle Hardened",
                desc=(
                    "1/long rest, бонусна дія: стійкість до немагічних пошкоджень "
                    "дробінням/проколюванням/рубанням на 1 хвилину."
                ),
                source="Hedge Knight L10",
            ),
        ],
        11: [
            Feature(
                name="Indomitable",
                desc="1/long rest: перекидаєш провалений рятівний кидок.",
                source="Hedge Knight L11",
            ),
        ],
        12: [_asi(name, 12)],
        13: [
            Feature(
                name="Improved Lucky Strike",
                desc="Тепер 2/short rest: перекидаєш провалений кидок атаки.",
                source="Hedge Knight L13",
            ),
        ],
        14: [
            Feature(
                name="Reputation of the Road",
                desc=(
                    "Перевага на Переконання з простолюдинами і найманцями — тебе знають "
                    "як людину слова на дорогах."
                ),
                source="Hedge Knight L14",
            ),
        ],
        15: [
            Feature(
                name="Action Surge",
                desc="1/short rest: отримуєш додаткову повноцінну дію на своєму ході.",
                source="Hedge Knight L15",
            ),
        ],
        16: [_asi(name, 16)],
        17: [
            Feature(
                name="Veteran's Resolve",
                desc=(
                    "1/long rest: ігноруєш смертельний удар — залишаєшся з 1 HP замість "
                    "падіння до 0."
                ),
                source="Hedge Knight L17",
            ),
        ],
        18: [
            Feature(
                name="Master of the Sword",
                desc=(
                    "Критичне влучання на 19-20 (замість 20) для всієї бойової зброї."
                ),
                source="Hedge Knight L18",
            ),
        ],
        19: [_asi(name, 19)],
        20: [
            Feature(
                name="Knight Errant Legend",
                desc=(
                    "Glory pool: 5 кубиків d6. Витрач 1+ перед будь-яким кидком, щоб додати "
                    "Nd6 до результату. Відновлюється на long rest."
                ),
                source="Hedge Knight L20",
            ),
        ],
    }
    return ClassDef(
        name=name,
        hit_die=10,
        primary_abilities=["STR", "CON"],
        saves_proficient=["STR", "CON"],
        armor_profs=["light", "medium", "shields"],
        weapon_profs=["simple", "martial"],
        skill_choices=2,
        skill_pool=["Athletics", "Survival", "Animal Handling", "Insight", "Perception", "Persuasion"],
        level_features=_build_level_features(name, implemented),
        starting_equipment={
            "weapon_main": "Довгий меч (1d8 рублюча, дворуч 1d10)",
            "weapon_off": None,
            "armor": "Шкіра (КЗ 11+DEX)",
            "shield": False,
            "items": ["Спальник", "Залатаний плащ", "Старий кінь"],
            "gold": 15,
        },
        description=(
            "A wandering knight without a lord or lands, sleeping beneath hedgerows between "
            "tourneys. Hedge Knights are proud but poor, skilled but unsponsored — survivors "
            "who have learned that a sharp sword and sharper wits keep them fed."
        ),
    )


def _define_maester() -> ClassDef:
    name = "Maester"
    implemented = {
        1: [
            Feature(
                name="Ланцюг знань",
                desc=(
                    "Обери 2 навички, якими володієш (INT або WIS): отримуєш Expertise "
                    "(подвійний бонус майстерності)."
                ),
                source="Maester L1",
            ),
            Feature(
                name="Тренування Цитаделі",
                desc=(
                    "Ритуали: Identify (10 хв, 10 зм.) і Detect Poison (1 хв, безкоштовно). "
                    "Потребують інструменти мейстера."
                ),
                source="Maester L1",
            ),
        ],
        2: [
            Feature(
                name="Healer's Hands",
                desc=(
                    "1/day: під час short rest лікуєш союзника так само, як long rest "
                    "(повне відновлення HP). Потребує 10 хвилин безперервного догляду."
                ),
                source="Maester L2",
            ),
            Feature(
                name="Ravens",
                desc=(
                    "Надсилаєш Ravens-послання до замків і поселень, де бував. "
                    "Час доставки: 1d4 дні / 100 миль."
                ),
                source="Maester L2",
            ),
        ],
        3: [
            Feature(
                name="Scholar's Mind",
                desc=(
                    "Перевага на рятівні кидки проти Зачарування та Переляку від "
                    "інтелектуальних маніпуляцій (пропаганда, коерція, аргументація)."
                ),
                source="Maester L3",
            ),
        ],
        4: [_asi(name, 4)],
        5: [
            Feature(
                name="Expertise (2 more skills)",
                desc=(
                    "Обираєш ще 2 навички (INT або WIS) і отримуєш Expertise — тепер "
                    "загалом 4 навички з подвійним бонусом."
                ),
                source="Maester L5",
            ),
        ],
        6: [
            Feature(
                name="Maester's Tome",
                desc=(
                    "Читаєш і запам'ятовуєш сторінку тексту за 1 хвилину. Можеш відтворити "
                    "дослівно будь-коли без перевірки."
                ),
                source="Maester L6",
            ),
        ],
        7: [
            Feature(
                name="Surgeon",
                desc=(
                    "1/short rest, дія: торкаєшся союзника і відновлюєш 4d8 + WIS_mod HP; "
                    "автоматично стабілізуєш вмираючого без перевірки."
                ),
                source="Maester L7",
            ),
        ],
        8: [_asi(name, 8)],
        9: [
            Feature(
                name="Divination by Stars",
                desc=(
                    "1/long rest, 1 година: GM правдиво відповідає на 1 запитання про "
                    "місцеві або поточні події."
                ),
                source="Maester L9",
            ),
        ],
        10: [
            Feature(
                name="Polyglot Mind",
                desc=(
                    "+3 додаткові мови. Після 1 хвилини Insight DC 12 розумієш будь-яку "
                    "розмовну мову."
                ),
                source="Maester L10",
            ),
        ],
        11: [
            Feature(
                name="Master of Poisons",
                desc=(
                    "1/long rest: створюєш отруту з {сон, параліч, гарячка}. "
                    "Автоматично ідентифікуєш будь-яку отруту без перевірки."
                ),
                source="Maester L11",
            ),
        ],
        12: [_asi(name, 12)],
        13: [
            Feature(
                name="Alchemical Fire",
                desc=(
                    "1/long rest: крафтиш гранату дикогорячого вогню. Вибух: конус 10 фут., "
                    "3d6 fire пошкоджень (DEX DC 13 — половина)."
                ),
                source="Maester L13",
            ),
        ],
        14: [
            Feature(
                name="Counsel of the Conclave",
                desc=(
                    "Реакція: коли союзник в 30 фут. кидає INT перевірку — додаєш +1d6 "
                    "до результату."
                ),
                source="Maester L14",
            ),
        ],
        15: [
            Feature(
                name="Glass Candle",
                desc=(
                    "1/тиждень, ритуал 1 година: GM правдиво відповідає на будь-яке "
                    "запитання про минуле або теперішнє (не майбутнє)."
                ),
                source="Maester L15",
            ),
        ],
        16: [_asi(name, 16)],
        17: [
            Feature(
                name="Mind Like Steel",
                desc=(
                    "Перевага на рятівні проти Зачарування/Переляку/ілюзій. "
                    "1/long rest реакція: знімаєш ефект зачарування з себе або союзника."
                ),
                source="Maester L17",
            ),
        ],
        18: [
            Feature(
                name="Healer Beyond Measure",
                desc=(
                    "1/long rest: протягом short rest повністю зцілюєш усіх союзників "
                    "в 30 фут. (витрачає всі HD союзників)."
                ),
                source="Maester L18",
            ),
        ],
        19: [_asi(name, 19)],
        20: [
            Feature(
                name="Grand Maester",
                desc=(
                    "Отримуєш 5 ланок ланцюга: кожна надає одну з (додатковий ритуал, "
                    "+1 INT max 22, всі мови, пророцтво 1 дня 1/тиждень, порятунок від "
                    "смерті 1/lifetime)."
                ),
                source="Maester L20",
            ),
        ],
    }
    return ClassDef(
        name=name,
        hit_die=6,
        primary_abilities=["INT", "WIS"],
        saves_proficient=["INT", "WIS"],
        armor_profs=[],
        weapon_profs=["simple"],
        skill_choices=4,
        skill_pool=[
            "Arcana", "History", "Insight", "Investigation",
            "Medicine", "Nature", "Persuasion", "Religion",
        ],
        level_features=_build_level_features(name, implemented),
        starting_equipment={
            "weapon_main": "Палиця (1d6 дробильна)",
            "weapon_off": None,
            "armor": None,
            "shield": False,
            "items": [
                "Ланцюг мейстра (foci ланки)",
                "Мішок трав",
                "Круки x2",
                "Книги x3 (історія, медицина, зорезнавство)",
            ],
            "gold": 40,
        },
        description=(
            "A Maester of the Citadel, trained in history, medicine, astronomy, and the "
            "natural world. Maesters serve the lords they are assigned to — not as servants "
            "but as counsellors. They wear no sword, but their knowledge can topple kingdoms."
        ),
    )


def _define_septon() -> ClassDef:
    name = "Septon"
    implemented = {
        1: [
            Feature(
                name="Віра в Семеро",
                desc=(
                    "1/day, 1 хвилина проповіді: союзник в 30 фут. отримує inspiration — "
                    "додає 1d4 до одного кидка до кінця наступного long rest."
                ),
                source="Septon L1",
            ),
            Feature(
                name="Благословіння Матері",
                desc=(
                    "1/short rest, дія: торкаєшся істоти і відновлюєш 1d8 + WIS_mod HP."
                ),
                source="Septon L1",
            ),
        ],
        2: [
            Feature(
                name="Sermon",
                desc=(
                    "10 хвилин проповіді, CHA Переконання DC 15: на успіх — натовп "
                    "переходить до мирних дій, або отримує перевагу на рятівні проти Переляку "
                    "1 годину, або стає ворожим до названої цілі 1 годину."
                ),
                source="Septon L2",
            ),
        ],
        3: [
            Feature(
                name="Sacred Oath",
                desc=(
                    "Перевага на рятівні проти Переляку. Союзники в 10 фут. +2 до рятівних "
                    "проти Переляку."
                ),
                source="Septon L3",
            ),
        ],
        4: [_asi(name, 4)],
        5: [
            Feature(
                name="Sermon (improved)",
                desc=(
                    "Проповідь діє в конусі 30 фут., CHA save DC 13: ціль зачарована на "
                    "1 хв АБО отримує inspiration (+1d4 до одного кидка, 1 хв)."
                ),
                source="Septon L5",
            ),
        ],
        6: [
            Feature(
                name="Channel Divinity",
                desc=(
                    "1/short rest: звертаєшся до Семи. Варіанти: Turn Undead (60 фут., "
                    "WIS DC), або Sacred Light (1d8 radiant, ranged spell attack 30 фут.)."
                ),
                source="Septon L6",
            ),
        ],
        7: [
            Feature(
                name="Aura of Devotion",
                desc=(
                    "Союзники в 10 фут. імунні до Зачарування, поки ти при свідомості."
                ),
                source="Septon L7",
            ),
        ],
        8: [_asi(name, 8)],
        9: [
            Feature(
                name="Greater Healing",
                desc=(
                    "Blessing of the Mother тепер відновлює 2d8 + WIS_mod HP і "
                    "може використовуватись 2/short rest."
                ),
                source="Septon L9",
            ),
        ],
        10: [
            Feature(
                name="Faithful Resolve",
                desc=(
                    "Перевага на рятівні проти всіх магічних форм страху і безумства."
                ),
                source="Septon L10",
            ),
        ],
        11: [
            Feature(
                name="Divine Smite",
                desc=(
                    "1/хід при влучанні в ближньому бою: +2d8 radiant пошкоджень."
                ),
                source="Septon L11",
            ),
        ],
        12: [_asi(name, 12)],
        13: [
            Feature(
                name="Channel Divinity (2)",
                desc="Channel Divinity тепер 2/short rest.",
                source="Septon L13",
            ),
        ],
        14: [
            Feature(
                name="Aura of Faith",
                desc=(
                    "Союзники в 10 фут. додають твій CHA_mod до рятівних кидків."
                ),
                source="Septon L14",
            ),
        ],
        15: [
            Feature(
                name="Lay on Hands",
                desc=(
                    "Pool HP = рівень × 5. Бонусна дія: розподіляєш HP між союзниками "
                    "і собою в будь-яких частках. Відновлюється на long rest."
                ),
                source="Septon L15",
            ),
        ],
        16: [_asi(name, 16)],
        17: [
            Feature(
                name="Cleansing Touch",
                desc=(
                    "1/long rest: знімаєш 1 закляття або стан (Зачарований, Паралізований "
                    "тощо) з себе або союзника, якого торкаєшся."
                ),
                source="Septon L17",
            ),
        ],
        18: [
            Feature(
                name="Improved Aura",
                desc=(
                    "Aura of Devotion і Aura of Faith розширюються з 10 до 30 фут."
                ),
                source="Septon L18",
            ),
        ],
        19: [_asi(name, 19)],
        20: [
            Feature(
                name="Avatar of the Seven",
                desc=(
                    "1/long rest, 1 хвилина: світишся, імунітет до хвороби/отрути; "
                    "союзники в 30 фут. +1 AC та перевага на рятівні; твої атаки +2d8 radiant."
                ),
                source="Septon L20",
            ),
        ],
    }
    return ClassDef(
        name=name,
        hit_die=8,
        primary_abilities=["WIS", "CHA"],
        saves_proficient=["WIS", "CHA"],
        armor_profs=["light", "medium"],
        weapon_profs=["simple"],
        skill_choices=2,
        skill_pool=["History", "Insight", "Medicine", "Persuasion", "Religion", "Intimidation"],
        level_features=_build_level_features(name, implemented),
        starting_equipment={
            "weapon_main": "Булава (1d6 дробильна)",
            "weapon_off": None,
            "armor": "Стьобана (КЗ 11+DEX)",
            "shield": False,
            "items": [
                "Семиконечна зірка (святий символ)",
                "Святі тексти (Семиконечна зірка)",
                "Кадило",
            ],
            "gold": 30,
        },
        description=(
            "A holy man or woman of the Faith of the Seven, ministering to the smallfolk and "
            "nobility alike. Septons wield no sword but command formidable moral authority. "
            "In a realm of endless war, they are among the few figures all sides respect."
        ),
    )


def _define_sellsword() -> ClassDef:
    name = "Sellsword"
    implemented = {
        1: [
            Feature(
                name="Око ветерана",
                desc=(
                    "+2 до кидків ініціативи. Роки читання поля бою загострили "
                    "інстинкти."
                ),
                source="Sellsword L1",
            ),
            Feature(
                name="Прагматизм найманця",
                desc=(
                    "1/short rest: перекидаєш провалений кидок атаки і береш новий результат."
                ),
                source="Sellsword L1",
            ),
        ],
        2: [
            Feature(
                name="Fighting Style",
                desc=(
                    "Обери один: Two-Weapon Fighting (бонусна атака другою легкою зброєю "
                    "з додаванням modifier), Archery (+2 до ranged attacks), або "
                    "Defense (+1 AC в броні)."
                ),
                source="Sellsword L2",
            ),
        ],
        3: [
            Feature(
                name="Battle Hardened",
                desc=(
                    "1/long rest, бонусна дія: стійкість до немагічних b/p/s пошкоджень "
                    "на 1 хвилину."
                ),
                source="Sellsword L3",
            ),
        ],
        4: [_asi(name, 4)],
        5: [
            Feature(
                name="Extra Attack",
                desc="Атакуєш двічі у рамках однієї дії Attack.",
                source="Sellsword L5",
            ),
        ],
        6: [
            Feature(
                name="Battle-Hardened (improved)",
                desc=(
                    "Battle Hardened тепер 2/long rest замість 1/long rest. "
                    "Тривалість зростає до 2 хвилин."
                ),
                source="Sellsword L6",
            ),
        ],
        7: [
            Feature(
                name="Mercenary's Eye",
                desc=(
                    "1/short rest: перекидаєш провалений кидок атаки і береш новий результат "
                    "(незалежно від Mercenary's Pragmatism)."
                ),
                source="Sellsword L7",
            ),
        ],
        8: [_asi(name, 8)],
        9: [
            Feature(
                name="Maneuvers",
                desc=(
                    "3 maneuver dice d6. Обираєш 2 прийоми: Trip Attack, Disarming Attack, "
                    "Riposte. Витрачаєш 1 die на прийом."
                ),
                source="Sellsword L9",
            ),
        ],
        10: [
            Feature(
                name="Action Surge",
                desc="1/short rest: отримуєш додаткову повноцінну дію на своєму ході.",
                source="Sellsword L10",
            ),
        ],
        11: [
            Feature(
                name="Extra Attack (2)",
                desc="Тепер атакуєш тричі у рамках однієї дії Attack.",
                source="Sellsword L11",
            ),
        ],
        12: [_asi(name, 12)],
        13: [
            Feature(
                name="Improved Maneuvers",
                desc="Maneuver dice збільшуються з d6 до d8.",
                source="Sellsword L13",
            ),
        ],
        14: [
            Feature(
                name="Lucky Bastard",
                desc=(
                    "3/long rest: перекидаєш будь-який кидок атаки, рятівний або "
                    "перевірку характеристики — береш новий результат."
                ),
                source="Sellsword L14",
            ),
        ],
        15: [
            Feature(
                name="Action Surge (2)",
                desc="Action Surge тепер 2/short rest.",
                source="Sellsword L15",
            ),
        ],
        16: [_asi(name, 16)],
        17: [
            Feature(
                name="Survivor",
                desc=(
                    "На початку свого ходу, якщо HP менше половини максимуму: відновлюєш "
                    "5 + CON_mod HP автоматично."
                ),
                source="Sellsword L17",
            ),
        ],
        18: [
            Feature(
                name="Mercenary Legend",
                desc=(
                    "+2 до будь-якої характеристики на вибір. Максимум підвищується до 22."
                ),
                source="Sellsword L18",
            ),
        ],
        19: [_asi(name, 19)],
        20: [
            Feature(
                name="Captain of the Free Company",
                desc=(
                    "Отримуєш 1d4+2 найманців-cohort (CR 1 кожен). Зʼявляються після "
                    "long rest. Лояльні, поки ти платиш."
                ),
                source="Sellsword L20",
            ),
        ],
    }
    return ClassDef(
        name=name,
        hit_die=10,
        primary_abilities=["STR", "DEX"],
        saves_proficient=["STR", "DEX"],
        armor_profs=["light", "medium"],
        weapon_profs=["simple", "martial"],
        skill_choices=2,
        skill_pool=["Athletics", "Acrobatics", "Intimidation", "Perception", "Survival", "Persuasion"],
        level_features=_build_level_features(name, implemented),
        starting_equipment={
            "weapon_main": "Короткий меч (1d6 колота, фінесс)",
            "weapon_off": "Легкий арбалет (1d8 колота, 80/320)",
            "armor": "Шкіра з клепками (КЗ 12+DEX, max +2)",
            "shield": False,
            "items": [
                "Перев'язь",
                "20 болтів",
                "Кубок",
            ],
            "gold": 50,
        },
        description=(
            "A professional soldier who fights for coin, not loyalty. Sellswords serve the "
            "Free Companies of Essos and the Lords of Westeros without sentiment — competent, "
            "pragmatic, and very hard to kill."
        ),
    )


def _define_spy() -> ClassDef:
    name = "Spy"
    implemented = {
        1: [
            Feature(
                name="Хитра дія",
                desc=(
                    "Щоходу: бонусна дія — Dash, Disengage або Hide."
                ),
                source="Spy L1",
            ),
            Feature(
                name="Маленькі пташки",
                desc=(
                    "В місті: 1d6 годин збору інформації, Investigation DC 15. "
                    "На успіх — 1 секрет, чутка або розвіддані від GM."
                ),
                source="Spy L1",
            ),
        ],
        2: [
            Feature(
                name="Expertise",
                desc=(
                    "Обираєш 2 навички, якими володієш: Expertise (подвійний бонус майстерності)."
                ),
                source="Spy L2",
            ),
        ],
        3: [
            Feature(
                name="Faceless",
                desc=(
                    "1/long rest після 1 год. спостереження за ціллю: переймаєш їх особистість. "
                    "Викриття: Persuasion DC 20. Магія виявляє автоматично."
                ),
                source="Spy L3",
            ),
        ],
        4: [_asi(name, 4)],
        5: [
            Feature(
                name="Uncanny Dodge",
                desc=(
                    "Реакція: бачиш атаку — отримуєш половину пошкоджень замість повних."
                ),
                source="Spy L5",
            ),
        ],
        6: [
            Feature(
                name="Expertise (2 more skills)",
                desc=(
                    "Обираєш ще 2 навички для Expertise — тепер загалом 4 навички "
                    "з подвійним бонусом майстерності."
                ),
                source="Spy L6",
            ),
        ],
        7: [
            Feature(
                name="Evasion",
                desc=(
                    "DEX save: на успіх — 0 пошкоджень замість половини. "
                    "На провал — половина замість повних."
                ),
                source="Spy L7",
            ),
        ],
        8: [_asi(name, 8)],
        9: [
            Feature(
                name="Faceless (improved)",
                desc=(
                    "Перейняту особистість утримуєш 24 години. При спостереженні "
                    "автоматично дізнаєшся 1 секрет цілі."
                ),
                source="Spy L9",
            ),
        ],
        10: [
            Feature(
                name="Network of Birds",
                desc=(
                    "Збираєш інформацію по всьому місту за 1d4 годин: 2 секрети і "
                    "1 чутка про конкретного NPC."
                ),
                source="Spy L10",
            ),
        ],
        11: [
            Feature(
                name="Reliable Talent",
                desc=(
                    "Кидок <10 на будь-якій навичці, якою ти профіцієнтний — "
                    "вважається як 10."
                ),
                source="Spy L11",
            ),
        ],
        12: [_asi(name, 12)],
        13: [
            Feature(
                name="Slippery Mind",
                desc="Отримуєш proficiency у рятівних кидках WIS.",
                source="Spy L13",
            ),
        ],
        14: [
            Feature(
                name="Blindsense",
                desc=(
                    "Усвідомлюєш присутність невидимих або прихованих істот у 10 фут., "
                    "навіть якщо не бачиш їх."
                ),
                source="Spy L14",
            ),
        ],
        15: [
            Feature(
                name="Elusive",
                desc=(
                    "Жоден кидок атаки проти тебе не має переваги, поки ти не "
                    "недієздатний."
                ),
                source="Spy L15",
            ),
        ],
        16: [_asi(name, 16)],
        17: [
            Feature(
                name="Master Spy",
                desc=(
                    "Переймаєш особистість безстроково. Кожен long rest вивчаєш "
                    "1 нову мову."
                ),
                source="Spy L17",
            ),
        ],
        18: [
            Feature(
                name="Spymaster's Web",
                desc=(
                    "Контролюєш 1d6 іменних інформаторів у будь-якому місті. "
                    "Звітують 1/тиждень."
                ),
                source="Spy L18",
            ),
        ],
        19: [_asi(name, 19)],
        20: [
            Feature(
                name="Whisperer",
                desc=(
                    "1/long rest: дізнаєшся 1 правдивий секрет про будь-якого названого NPC "
                    "на континенті — миттєво, без перевірки."
                ),
                source="Spy L20",
            ),
        ],
    }
    return ClassDef(
        name=name,
        hit_die=8,
        primary_abilities=["DEX", "INT"],
        saves_proficient=["DEX", "INT"],
        armor_profs=["light"],
        weapon_profs=["simple", "hand crossbows", "longswords", "rapiers", "shortswords"],
        skill_choices=4,
        skill_pool=[
            "Deception", "Insight", "Investigation", "Perception",
            "Sleight of Hand", "Stealth", "Persuasion", "Performance",
        ],
        level_features=_build_level_features(name, implemented),
        starting_equipment={
            "weapon_main": "Кинджал (1d4 колота, фінесс, метний 20/60)",
            "weapon_off": "Кинджал (1d4 колота, фінесс, метний 20/60)",
            "armor": "Шкіра (КЗ 11+DEX)",
            "shield": False,
            "items": [
                "Набір для перевдягання",
                "Злодійські інструменти",
                "Відмички",
            ],
            "gold": 35,
        },
        description=(
            "A whisperer, intelligencer, and professional shadow. In a game of thrones, "
            "information is worth more than swords. The Spy moves unseen, speaks in "
            "half-truths, and knows secrets that could end dynasties."
        ),
    )


def _define_courtier() -> ClassDef:
    name = "Courtier"
    implemented = {
        1: [
            Feature(
                name="Срібний язик",
                desc=(
                    "1/day: перекидаєш Переконання або Обман після бачення результату; "
                    "береш новий кидок навіть якщо він нижчий."
                ),
                source="Courtier L1",
            ),
            Feature(
                name="Шляхетне поводження",
                desc=(
                    "Перевага на CHA-перевірки (Переконання, Обман, Гра, Залякування) "
                    "проти осіб рівного або нижчого соціального статусу."
                ),
                source="Courtier L1",
            ),
        ],
        2: [
            Feature(
                name="Мережа впливу",
                desc=(
                    "У будь-якому великому місті: кидаєш 1d4 — стільки шляхетних контактів "
                    "в боргу або знайомстві. Надають інформацію, притулок або вступ."
                ),
                source="Courtier L2",
            ),
        ],
        3: [
            Feature(
                name="Courtly Intrigue",
                desc=(
                    "Перевага на Проникливість для виявлення брехні. "
                    "Перешкода на Залякування (роки придворного дипломатизму)."
                ),
                source="Courtier L3",
            ),
        ],
        4: [_asi(name, 4)],
        5: [
            Feature(
                name="Мережа впливу (покращена)",
                desc=(
                    "У будь-якому великому місті знаєш 2d4 шляхетних контактів замість 1d4."
                ),
                source="Courtier L5",
            ),
        ],
        6: [
            Feature(
                name="Двір брехні",
                desc=(
                    "Перевага на Проникливість для виявлення брехні. 1/сцена: примушуєш "
                    "ціль відповісти на 1 так/ні питання правдиво (CHA save DC 13)."
                ),
                source="Courtier L6",
            ),
        ],
        7: [
            Feature(
                name="Срібний язик (покращений)",
                desc=(
                    "Тепер 2/day: перекидаєш Переконання або Обман після бачення результату."
                ),
                source="Courtier L7",
            ),
        ],
        8: [_asi(name, 8)],
        9: [
            Feature(
                name="Mark of Favor",
                desc=(
                    "1/тиждень на регіон: використовуєш репутацію лорда — отримуєш "
                    "притулок, аудієнцію або розвіддані."
                ),
                source="Courtier L9",
            ),
        ],
        10: [
            Feature(
                name="Diplomatic Immunity",
                desc=(
                    "Переконання DC 20: твоє слово важить як свідчення лорда в будь-якому "
                    "шляхетному суді."
                ),
                source="Courtier L10",
            ),
        ],
        11: [
            Feature(
                name="Reliable Talent",
                desc=(
                    "Кидок <10 на будь-якій CHA-навичці — вважається як 10."
                ),
                source="Courtier L11",
            ),
        ],
        12: [_asi(name, 12)],
        13: [
            Feature(
                name="Master of Court",
                desc=(
                    "Перевага на всіх CHA-перевірках, перебуваючи в шляхетному приміщенні "
                    "(тронна зала, банкет, зала прийомів)."
                ),
                source="Courtier L13",
            ),
        ],
        14: [
            Feature(
                name="Reputation (improved)",
                desc=(
                    "Перевага на соціальних перевірках з особами рівного АБО вищого "
                    "соціального статусу."
                ),
                source="Courtier L14",
            ),
        ],
        15: [
            Feature(
                name="Patron's Hand",
                desc=(
                    "1/long rest: називаєш імʼя патрона — союзник отримує перевагу на "
                    "1 соціальну перевірку."
                ),
                source="Courtier L15",
            ),
        ],
        16: [_asi(name, 16)],
        17: [
            Feature(
                name="Cunning Web",
                desc=(
                    "На початку сесії: GM розкриває 1 нитку поточного змовницького "
                    "сценарію (хто, що, де)."
                ),
                source="Courtier L17",
            ),
        ],
        18: [
            Feature(
                name="Royal Favor",
                desc=(
                    "Названий правитель, чию прихильність ти здобув: 1/місяць — його наказ "
                    "виконується від його імені на твій запит."
                ),
                source="Courtier L18",
            ),
        ],
        19: [_asi(name, 19)],
        20: [
            Feature(
                name="Hand of the King",
                desc=(
                    "Здобуваєш титул і ранг члена малої ради. "
                    "5 cohort-ретинерів (CR 1 благородні). Постійно."
                ),
                source="Courtier L20",
            ),
        ],
    }
    return ClassDef(
        name=name,
        hit_die=6,
        primary_abilities=["CHA", "INT"],
        saves_proficient=["CHA", "INT"],
        armor_profs=["light"],
        weapon_profs=["simple", "rapiers"],
        skill_choices=4,
        skill_pool=["Deception", "History", "Insight", "Performance", "Persuasion", "Investigation"],
        level_features=_build_level_features(name, implemented),
        starting_equipment={
            "weapon_main": "Рапіра (1d8 колота, фінесс)",
            "weapon_off": None,
            "armor": None,
            "shield": False,
            "items": [
                "Вишукані шати",
                "Перстень з гербом дому",
                "Запечатані рекомендаційні листи x3",
            ],
            "gold": 100,
        },
        description=(
            "A master of the court, fluent in the language of power: flattery, alliance, "
            "and careful betrayal. Courtiers win without fighting. They are the most "
            "dangerous people in the Seven Kingdoms — and the most underestimated."
        ),
    )


def _define_bastard() -> ClassDef:
    name = "Bastard"
    implemented = {
        1: [
            Feature(
                name="Рішучість байстрюка",
                desc=(
                    "Перевага на рятівні проти Переляку. Народжений з нічим — страх "
                    "став розкішшю, якої не можеш собі дозволити."
                ),
                source="Bastard L1",
            ),
            Feature(
                name="Стійкість недопереможеного",
                desc=(
                    "Коли HP менше або дорівнює половині максимуму: +1 до кидків атаки "
                    "і пошкоджень."
                ),
                source="Bastard L1",
            ),
        ],
        2: [
            Feature(
                name="Fighting Style",
                desc=(
                    "Обери один: Defense (+1 AC в броні) або Dueling (+2 пошкоджень "
                    "одноручною зброєю при вільній другій руці чи зі щитом)."
                ),
                source="Bastard L2",
            ),
        ],
        3: [
            Feature(
                name="Brother of the Watch",
                desc=(
                    "Опційний шлях: складаєш клятву Нічної Варти (незворотно). "
                    "Отримуєш +2 CON на північ від Перешийка, імунітет до немагічного "
                    "холодового пошкодження."
                ),
                source="Bastard L3",
            ),
        ],
        4: [_asi(name, 4)],
        5: [
            Feature(
                name="Extra Attack",
                desc="Атакуєш двічі у рамках однієї дії Attack.",
                source="Bastard L5",
            ),
        ],
        6: [
            Feature(
                name="Reckless Attack",
                desc=(
                    "На ході: перевага на всі ближні атаки, але вороги мають перевагу "
                    "проти тебе до початку твого наступного ходу."
                ),
                source="Bastard L6",
            ),
        ],
        7: [
            Feature(
                name="Сплеск стійкості",
                desc=(
                    "При HP ≤ половині максимуму: +1 атака/пошкодження, +2 AC, "
                    "перевага на рятівні. Підсилена версія Стійкість недопереможеного."
                ),
                source="Bastard L7",
            ),
        ],
        8: [_asi(name, 8)],
        9: [
            Feature(
                name="Battle Cry",
                desc=(
                    "1/long rest, бонусна дія: всі союзники в 30 фут. додають +1d6 "
                    "до наступного кидка атаки."
                ),
                source="Bastard L9",
            ),
        ],
        10: [
            Feature(
                name="Indomitable Will",
                desc="1/long rest: перекидаєш провалений рятівний кидок.",
                source="Bastard L10",
            ),
        ],
        11: [
            Feature(
                name="Maneuvers",
                desc=(
                    "3 maneuver dice d6: Trip Attack, Disarming Attack, Riposte. "
                    "Витрачаєш 1 die на прийом."
                ),
                source="Bastard L11",
            ),
        ],
        12: [_asi(name, 12)],
        13: [
            Feature(
                name="Brother Bastard (improved)",
                desc=(
                    "Якщо склав клятву Нічної Варти: отримуєш 5 cohort-Brothers (Вартові "
                    "CR ½ кожен)."
                ),
                source="Bastard L13",
            ),
        ],
        14: [
            Feature(
                name="Action Surge",
                desc="1/short rest: отримуєш додаткову повноцінну дію на своєму ході.",
                source="Bastard L14",
            ),
        ],
        15: [
            Feature(
                name="Survivor's Grit",
                desc=(
                    "На початку ходу, якщо bloodied: автоматично відновлюєш 5 + CON_mod HP."
                ),
                source="Bastard L15",
            ),
        ],
        16: [_asi(name, 16)],
        17: [
            Feature(
                name="Indomitable (2)",
                desc="Тепер 2/long rest: перекидаєш провалений рятівний кидок.",
                source="Bastard L17",
            ),
        ],
        18: [
            Feature(
                name="Battle Master Capstone",
                desc="Maneuver dice збільшуються з d6 до d10.",
                source="Bastard L18",
            ),
        ],
        19: [_asi(name, 19)],
        20: [
            Feature(
                name="Legitimacy or Legend",
                desc=(
                    "Обираєш: Законнонароджений (шляхетний титул, +5 соціальних) АБО "
                    "Бастард-лорд (власний прапор, +2 STR/CON max 22)."
                ),
                source="Bastard L20",
            ),
        ],
    }
    return ClassDef(
        name=name,
        hit_die=8,
        primary_abilities=["STR", "CHA"],
        saves_proficient=["STR", "CHA"],
        armor_profs=["light", "medium"],
        weapon_profs=["simple", "martial"],
        skill_choices=2,
        skill_pool=["Athletics", "Intimidation", "Survival", "Insight", "Stealth", "Persuasion"],
        level_features=_build_level_features(name, implemented),
        starting_equipment={
            "weapon_main": "Довгий меч (1d8 рублюча, дворуч 1d10)",
            "weapon_off": None,
            "armor": "Шкіра (КЗ 11+DEX)",
            "shield": False,
            "items": [
                "Зношений плащ",
                "Дрібничка від відсутнього родителя",
            ],
            "gold": 20,
        },
        description=(
            "The baseborn get nothing by right — no name, no inheritance, no seat at the table. "
            "What they get, they take through grit, skill, and the grudging respect of those "
            "who cannot deny talent. Every bastard in Westeros is either broken or forged."
        ),
    )


def _define_wildling() -> ClassDef:
    """Клас описує бойовий архетип Вільного Народу. Клас і спадщина (heritage) — розвʼязані:
    обрати Wildling може будь-яка спадщина; спадщина Free Folk може обрати будь-який клас."""
    name = "Wildling"
    implemented = {
        1: [
            Feature(
                name="Витривалість вільного народу",
                desc=(
                    "Перевага на рятівні проти Переляку і Виснаження від природних факторів "
                    "(холод, голод, форсований марш). Не стаєш на коліна."
                ),
                source="Wildling L1",
            ),
            Feature(
                name="За Стіною",
                desc=(
                    "Імунітет до пошкоджень від екстремального холоду. Рух по снігу/льоду "
                    "коштує половину замість подвійного."
                ),
                source="Wildling L1",
            ),
        ],
        2: [
            Feature(
                name="Reckless Attack",
                desc=(
                    "1/хід: атакуєш безрозсудно — перевага на кидок атаки, але вороги "
                    "мають перевагу проти тебе до наступного ходу."
                ),
                source="Wildling L2",
            ),
        ],
        3: [
            Feature(
                name="Wild Strength",
                desc=(
                    "1/short rest, бонусна дія: 1 хвилина первісної люті — +2 до пошкоджень "
                    "у ближньому бою та стійкість до немагічних b/p/s пошкоджень."
                ),
                source="Wildling L3",
            ),
        ],
        4: [_asi(name, 4)],
        5: [
            Feature(
                name="Extra Attack",
                desc="Атакуєш двічі у рамках однієї дії Attack.",
                source="Wildling L5",
            ),
        ],
        6: [
            Feature(
                name="Wild Strength (improved)",
                desc=(
                    "Wild Strength тепер 2/long rest; +2 пошкоджень і стійкість b/p/s "
                    "тривають 1 хвилину."
                ),
                source="Wildling L6",
            ),
        ],
        7: [
            Feature(
                name="Brute",
                desc=(
                    "При влучанні дворучною зброєю додаєш +1d6 пошкоджень понад кубик зброї."
                ),
                source="Wildling L7",
            ),
        ],
        8: [_asi(name, 8)],
        9: [
            Feature(
                name="Survivor",
                desc=(
                    "На початку свого ходу, якщо HP менше половини максимуму: "
                    "автоматично відновлюєш 5 + CON_mod HP."
                ),
                source="Wildling L9",
            ),
        ],
        10: [
            Feature(
                name="Fast Movement",
                desc="Швидкість руху +10 фут. (загалом 45 фут. базово).",
                source="Wildling L10",
            ),
        ],
        11: [
            Feature(
                name="Relentless Endurance",
                desc=(
                    "1/long rest: замість падіння до 0 HP — залишаєшся з 1 HP."
                ),
                source="Wildling L11",
            ),
        ],
        12: [_asi(name, 12)],
        13: [
            Feature(
                name="Wild Charge",
                desc=(
                    "Ривок 30 фут. до ворога і атака: +2d8 пошкоджень при влучанні. "
                    "Ворог: STR save DC 13 або Prone."
                ),
                source="Wildling L13",
            ),
        ],
        14: [
            Feature(
                name="Feral Instinct",
                desc=(
                    "Перевага на кидки ініціативи. Не можеш бути Здивованим, "
                    "якщо при свідомості."
                ),
                source="Wildling L14",
            ),
        ],
        15: [
            Feature(
                name="Battlerager",
                desc=(
                    "Половина пошкоджень, яких ти зазнав, зберігається як бонус до "
                    "наступного влучання (максимум +20)."
                ),
                source="Wildling L15",
            ),
        ],
        16: [_asi(name, 16)],
        17: [
            Feature(
                name="Indomitable Might",
                desc=(
                    "При провалі перевірки STR: якщо твій STR score ≥ DC — вважається "
                    "автоматичним успіхом."
                ),
                source="Wildling L17",
            ),
        ],
        18: [
            Feature(
                name="Persistent Rage",
                desc=(
                    "Wild Strength тепер тривалістю 10 хвилин, 3/long rest."
                ),
                source="Wildling L18",
            ),
        ],
        19: [_asi(name, 19)],
        20: [
            Feature(
                name="King-Beyond-the-Wall",
                desc=(
                    "1/long rest: збираєш 2d6 + CON воїнів-дикунів (CR ½) на 1 годину. "
                    "+2 STR/CON, максимум підвищується до 24."
                ),
                source="Wildling L20",
            ),
        ],
    }
    return ClassDef(
        name=name,
        hit_die=12,
        primary_abilities=["STR", "CON"],
        saves_proficient=["STR", "CON"],
        armor_profs=["light", "medium"],
        weapon_profs=["simple", "martial"],
        skill_choices=2,
        skill_pool=["Athletics", "Survival", "Intimidation", "Perception", "Animal Handling", "Nature"],
        level_features=_build_level_features(name, implemented),
        starting_equipment={
            "weapon_main": "Велика сокира (1d12 рублюча, дворучна)",
            "weapon_off": None,
            "armor": "Шкура (КЗ 12+DEX max +2)",
            "shield": False,
            "items": [
                "Кістяна дрібничка",
                "Хутра",
                "Ніж для оббілування (1d4 колота)",
            ],
            "gold": 5,
        },
        description=(
            "A Free Folk raider from beyond the Wall — fierce, proud, and utterly contemptuous "
            "of the kneelers and their castles. Wildlings are not savages; they are survivors "
            "of a world that would kill softer men in a fortnight."
        ),
    )


# Build the global registry
GOT_CLASSES = {
    "Knight": _define_knight(),
    "Hedge Knight": _define_hedge_knight(),
    "Maester": _define_maester(),
    "Septon": _define_septon(),
    "Sellsword": _define_sellsword(),
    "Spy": _define_spy(),
    "Courtier": _define_courtier(),
    "Bastard": _define_bastard(),
    "Wildling": _define_wildling(),
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_class(name: str) -> ClassDef:
    """Return the ClassDef for the given class name. Raises KeyError for unknown class."""
    return GOT_CLASSES[name]


def get_class_features_at_level(class_name: str, level: int) -> list[Feature]:
    """Return features granted at exactly this level (not cumulative). Raises KeyError for unknown class."""
    cls = get_class(class_name)
    if level < 1 or level > 20:
        raise ValueError(f"Level must be 1-20, got {level}.")
    return list(cls.level_features[level])   # loose copy — захист від мутації singleton


def get_starting_hp(class_name: str, con_mod: int) -> int:
    """Return starting HP at L1: hit_die_max + CON modifier (D&D L1 canon). Minimum 1."""
    cls = get_class(class_name)
    return max(1, cls.hit_die + con_mod)


def build_class_starting_kit(class_name: str) -> dict:
    """Return a shallow copy of the class's starting_equipment dict."""
    cls = get_class(class_name)
    kit = dict(cls.starting_equipment)
    # Deep-copy the mutable items list so callers can't mutate the original.
    if "items" in kit and isinstance(kit["items"], list):
        kit["items"] = list(kit["items"])
    return kit
