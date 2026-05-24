# Skill: RPG Mechanics & Prompt Engineering (D&D 5e ASoIaF)

> Оновлено Phase 8 (cleanup). Попередня 2d50-система повністю замінена.

## 1. Математика (Система D&D 5e — ASoIaF адаптація)

- **Базовий кидок:** 1d20 + ability_modifier + proficiency_bonus (якщо персонаж proficient у скілі).
- **Ability modifiers:** `floor((score - 10) / 2)`. Score range: 1–20. Mod range: -5..+5.
- **Proficiency bonus:** рівень 1–4 → +2, 5–8 → +3, 9–12 → +4, 13–16 → +5, 17–20 → +6.
- **DC (складність) — STRICT enum:** `{5, 10, 12, 15, 17, 20, 22, 25, 28, 30}`. Будь-яке інше значення — **баг**. Функція `clamp_dc()` у `core/dnd_core.py` обрізає.
  - DC 5: тривіально · DC 10: знайти таверну · DC 15: переконати вартового · DC 20: зламати шифр · DC 25: переплисти Тризуб · DC 30: звабити Серсею.
- **Перевага (Advantage):** кидаємо 2d20, беремо вищий.
- **Недолік (Disadvantage):** кидаємо 2d20, беремо нижчий.
- **Критичний успіх:** натуральний 20. **Критичний провал:** натуральний 1.

## 2. Шкали ресурсів

- **HP (Здоров'я):** `hp_current` / `hp_max`. Розраховується за класом + рівнем + CON mod. НЕ є фіксованим 0–100.
  - Для зворотної сумісності з UI: `profile["Здоров'я"] = round(100 * hp_current / hp_max)` синхронізується в `apply_dnd_impacts` після кожної зміни HP.
- **Енергія:** 0–1000 (legacy, паралельно з HP). Пороги: 200/400/600/800/1000. Старт = 1000.
- **XP:** таблиця у `core/dnd_progression.py`. Формула per-turn: 0 / 25 / 50 / 100 / 200.
- **Навички (18 D&D skills):** modifier = ability_mod + (proficiency_bonus якщо proficient) + (proficiency_bonus знову якщо expertise).
  - Athletics → STR; Acrobatics, Sleight of Hand, Stealth → DEX; Arcana, History, Investigation, Nature, Religion → INT; Animal Handling, Insight, Medicine, Perception, Survival → WIS; Deception, Intimidation, Performance, Persuasion → CHA.
- **Ability scores:** 6 характеристик (STR/DEX/CON/INT/WIS/CHA), діапазон 1–20 під час гри.
- **9 GoT-класів:** Knight, Hedge Knight, Maester, Septon, Sellsword, Spy/Whisperer, Courtier, Bastard, Wildling Raider.
- **6 Heritage traits:** Westerosi (Andal), Valyrian Descent, First Men (Stark line), Free Folk, Red Priest, Ironborn.

## 3. FSM режими (engine.py)

- **NORMAL_MODE:** Censor → Worker(NORMAL) → GM_Logic → Narrator.
- **COMBAT_MODE:** Worker(COMBAT: player action) → Worker(spotlight 1-2 NPC) → GM_Logic(round) → Narrator. 4 LLM-виклики/раунд.
- Перехід NORMAL → COMBAT: `combat_imminent=true` у Worker або `mode_transition="TO_COMBAT"` у GM_Logic.
- Перехід COMBAT → NORMAL: всі вороги HP≤0, або гравець втік (DEX check), або `round_counter ≥ 10`.

## 4. JSON-контракти (ЖОРСТКІ ПРАВИЛА)

Правило мутації: зміна ключа вимагає синхронного оновлення промпту + парсера + qa_auto_test.

**Censor** (`build_validate_action_prompt`):
- `is_valid` (bool), `refusal_reason` (string)

**Worker NORMAL** (`build_normal_resolve_prompt`):
- `skill_check_reasoning`, `ability_used` (STR|DEX|CON|INT|WIS|CHA|None), `skill_used` (18 skills|None)
- `difficulty` — STRICT enum `{5, 10, 12, 15, 17, 20, 22, 25, 28, 30}`
- `advantage_reason`, `disadvantage_reason`
- `combat_imminent` (bool), `verdict_text`, `xp_award` (0|25|50|100|200)
- `reputation_delta`, `reputation_target_npc`
- `updates`: `minutes_passed`, `location_impact`, `scene_impact`, `hp_damage_dice`, `hp_heal_dice`, `gold_impact`, `inventory_new`, `inventory_lost`, `clocks_impact`, `condition_apply`, `condition_remove`

**Worker COMBAT** (`build_combat_round_prompt`):
- `intent` (attack|cast|move|dodge|flee|item|help|grapple|shove)
- `target_npc`, `weapon`, `spell_or_ability`, `tactic` (reckless|normal|cautious), `move_to`, `verdict_text`, `reasoning`

**GM_Logic** (`build_gm_logic_prompt`):
- `reasoning`, `npc_reasoning` — внутрішнє міркування
- `director_notes` — масив 3–7 фактичних речень (без літератури)
- `companion_npcs` — масив імен (білий список проти телепортацій)
- `npc_updates` — масив NPC-об'єктів: `Name`, `Location`, `Scene`, `Memory_Anchor`, `Relation_NPCs`, `Inventory`, `Status`, `hp_current`, `conditions[]`
- `mode_transition` (null | "TO_COMBAT" | "TO_NORMAL")
- `suggested_actions` — **рівно 4** об'єкти `{button, intent}`

**Narrator** (`build_narrator_prompt`): чистий художній текст 150–250 слів, без JSON.

**Заморожені поля NPC:** `Description`, `Character`, `Goal`, `Secrets`. Поле `Attitude to Player` — read-only. `Relation_Player` — lore-текст без системного впливу (15-тирна шкала зберігається для lore, але не впливає на механіку).

**Допоміжні промпти:**
- Training intent (`build_training_request_prompt`): `is_training`, `is_possible`, `skill`, `method`, `reason_if_failed`
- Initial stats (`build_initial_stats_prompt`): D&D 5e character creation (class, heritage, ability_scores тощо)
- Game intro (`build_game_intro_prompt`): `narrative_text`, `action_prompt`, `suggested_actions`
- NPC populate (`build_populate_npcs_prompt`): масив NPC з D&D statblock
- Combat round (`build_combat_round_prompt`): COMBAT Worker output
- NPC combat action (`build_npc_combat_action_prompt`): spotlight NPC actions
- NPC regen (`build_npc_regen_prompt`): D&D statblock для канонічних NPC

## 5. Золоті правила GM (The Golden Laws of Agency)

- **Intent Only:** Гравець описує лише наміри. Результат вирішує GM.
- **No Ventriloquism:** GM ніколи не пише репліки за гравця.
- **No Mind Reading:** GM описує лише те, що можна побачити або почути.
- Якщо гравець залишає локацію — сцена негайно обривається. Реакції залишених NPC не описуються.
- **NPC roster isolation:** Worker/GM_Logic/Narrator не дозволяють NPC поза `active_roster`.
