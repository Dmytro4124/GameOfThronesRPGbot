# core/engine.py
import time
import json
import re
import asyncio
import contextvars
import functools
import logging

logger = logging.getLogger(__name__)

# Backoff lazy NPC-cache guard (A1): збій refresh / відсутній лист vs успішний refresh з порожнім ростером.
_NPC_CACHE_RETRY_FAIL_S = 60
_NPC_CACHE_RETRY_EMPTY_S = 600

from core.ai_client import model_worker, model_gm_logic, model_narrator, model_narrator_alt, clean_and_parse_json, clear_thoughts, get_thoughts_log, record_thought, hedged_generate_content_async, build_strict_config
from config import MODEL_NARRATOR_NAME, NARRATOR_AB_ENABLED
from core.narrator_ab import (
    NarrationResult, PendingChoice, new_turn_id, set_pending, shuffle_variants,
    build_log_record, append_log,
)
from core.mechanics import apply_system_impacts, process_training_request, safe_int, validate_action
from core.dnd_engine import resolve_normal_action, apply_dnd_impacts
from core.dnd_classes import GOT_CLASSES
from core.dnd_core import score_to_relation_text
from core.dnd_progression import apply_pending_levelups
from core.dnd_combat_engine import (
    execute_combat_round,
    cleanup_and_exit_combat,
    format_combat_log_for_narrator,
    initiate_combat_from_normal,
)
from core.combat_state import (
    is_in_combat as _is_in_combat_for_engine,
    clear_combat_state as _clear_combat_state_for_engine,
    cleanup_lock as _cleanup_lock_for_engine,
    get_combat_state as _get_combat_state_for_engine,
)
from core.prompts import (
    GAME_ERA_CONTEXT, build_summarize_turn_prompt, build_summarize_full_turn_prompt,
    build_narrator_parts, build_gm_logic_parts, build_history_summary_prompt,
    GM_LOGIC_SCHEMA,
)
from core.world_constants import (
    VALID_LOCATIONS_ORDERED, VALID_REGIONS_ORDERED, TRAVEL_LOCATION,
    get_region_for_location, get_locations_for_region, LOCATION_DESCRIPTIONS,
    format_scenes_for_prompt,
    REGION_CLIMATE_MAP, EVENT_PLAUSIBILITY,
)
from core.inventory import parse_inventory as _parse_inventory, format_inventory as _fmt_inventory
from database.operations import get_canon_npc_statblock


def _format_inventory_for_prompt(raw) -> str:
    """Normalise profile['Інвентар'] → display string for GM_Logic prompt context."""
    return _fmt_inventory(_parse_inventory(raw))


# ── Safe wrapper for sync functions passed to asyncio.to_thread ─────────────
# Python asyncio bug: StopIteration from a thread cannot be propagated into a
# Future (raises "StopIteration interacts badly with generators and cannot be
# raised into a Future") — the Future hangs forever. Happens when MagicMock
# side_effect exhausts in tests or any sync code raises StopIteration.
# Wrapping converts StopIteration → RuntimeError so the awaiter sees a failure.
def _safe_thread_call(fn):
    """Decorator: convert StopIteration → RuntimeError for asyncio.to_thread targets."""
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except StopIteration as exc:
            raise RuntimeError(f"StopIteration in thread ({fn.__name__}): {exc}") from exc
    wrapper.__name__ = getattr(fn, "__name__", "wrapper")
    return wrapper


async def _safe_to_thread(fn, *args, **kwargs):
    """asyncio.to_thread wrapper that converts StopIteration → RuntimeError.

    See _safe_thread_call for rationale. Use this instead of asyncio.to_thread
    for any function that might raise StopIteration (e.g. functions calling
    iterables, or functions called against mocks with exhausted side_effect).
    """
    return await asyncio.to_thread(_safe_thread_call(fn), *args, **kwargs)


# ── Background task wrapper ──────────────────────────────────────────────────
# Тести масово патчать це замість asyncio.create_task — щоб не блокувати
# hedging у core.ai_client, який потребує живого asyncio.create_task.
def _run_bg_task(coro):
    """Wrapper навколо asyncio.create_task для background tasks engine.py.

    Тести підміняють цю функцію (patch core.engine._run_bg_task) щоб
    подавити справжнє виконання background_task / travel_population_task
    БЕЗ впливу на asyncio.create_task у hedging/ai_client.
    """
    return asyncio.create_task(coro)
from core.world import populate_contextual_npcs
from database.operations import (
    get_user_data, save_user_data, get_relevant_context,
    get_location_npcs, update_npcs_in_db,
    update_npc_reputation, append_memory_anchor, get_dead_npc_names,
    refresh_npc_database,
)

user_sessions = {}
_atmosphere_cache = {}  # P11: кеш атмосфери локацій {location: atmosphere_str}

# WARN #5: хвилин на добу — замість magic number 1440
_MINUTES_PER_DAY = 24 * 60  # 1440

# WARN #1: маркери placeholder-відповідей Narrator (case-insensitive)
_PLACEHOLDER_MARKERS = (
    "тут напиши",
    "тут напишіть",
    "вставте текст",
    "insert story",
    "insert text",
    "placeholder",
    "todo:",
    "[опис",
    "<опис",
    "[текст",
    "<текст",
    "write here",
    "напишіть тут",
)


# §5.2 scene_continuity — структурні блоки для Narrator.
# NEW_SCENE: гравець щойно змінив сцену або локацію → описати атмосферу.
# CONTINUING: сцена та локація незмінні → фокус на дії, без повтору обстановки.
SCENE_CONTINUITY_NEW = (
    "<scene_continuity>NEW_SCENE: гравець уперше в цій сцені цього ходу. "
    "Опиши атмосферу одним коротким абзацом (1–3 речення): "
    "запахи/звуки/освітлення/ключова деталь.</scene_continuity>"
)
SCENE_CONTINUITY_CONTINUING = (
    "<scene_continuity>CONTINUING: гравець залишається в тій самій сцені. "
    "НЕ повторюй опис залу/інтер'єру/повітря. "
    "Фокус на дії, реакціях NPC і діалогах.</scene_continuity>"
)


def _sanitize_story(text: str) -> str:
    """Видаляє технічні рядки кидків, що випадково потрапили в наратив."""
    # "Кидок 47 + Навичка 30 = 77 (Ціль: 100) -> ПРОВАЛ"
    text = re.sub(r'Кидок\s+\d+\s*\+\s*Навичка\s+\d+\s*=\s*\d+\s*\(Ціль:\s*\d+\)\s*->\s*\S+', '', text)
    # Осиротілі числові патерни типу "HP: 80", "DC 60" — строго технічні формати
    text = re.sub(r"\b(HP|DC)\s*[:=]\s*\d+", '', text)
    text = re.sub(r"\bE\s*[:=]\s*\d+", '', text)
    # Технічні терміни годинників/механік
    text = re.sub(r'Scene_Tension', '', text)
    # A15 FIX: Безіменні "Лорд/Леді [Великий Дім]" → нейтральне звертання
    # Regex: ловить "Лорд Ланністер" але НЕ чіпає "Лорд Тайвін Ланністер" (є ім'я перед прізвищем)
    _great_houses_pattern = r'Ланністер|Старк|Таргарієн|Баратеон|Тірелл|Грейджой|Мартелл|Аррен|Таллі|Болтон'
    text = re.sub(
        rf'(?<![А-ЯІЇЄҐа-яіїєґ]\s)[Лл]орд(?:е|у|а|ом|ові)?\s+({_great_houses_pattern})\w*',
        'місцевий лорд',
        text
    )
    text = re.sub(
        rf'(?<![А-ЯІЇЄҐа-яіїєґ]\s)[Лл]еді\s+({_great_houses_pattern})\w*',
        'місцева леді',
        text
    )
    # Подвійні пробіли та порожні рядки
    text = re.sub(r'  +', ' ', text)
    return text.strip()


def _build_impact_hints(impact_logs: list) -> str:
    """Перетворює технічні логи імпактів на наративні підказки для GM (без чисел)."""
    hints = []
    for entry in impact_logs:
        if "⚡ Енергія" in entry:
            m_new = re.search(r'-> (\d+)', entry)
            m_delta = re.search(r'\(([+-]?\d+)\)', entry)
            new_val = int(m_new.group(1)) if m_new else 1000
            delta = int(m_delta.group(1)) if m_delta else 0

            if delta >= 1:
                hints.append("ВІДНОВЛЕННЯ ЕНЕРГІЇ: Персонаж відновився. GM може тонко згадати відчуття свіжості або прилив сил.")
            elif new_val <= 290 and delta < 0:
                hints.append("ВИСНАЖЕННЯ (КРИТИЧНЕ): Персонаж тяжко виснажений. GM ПОВИНЕН описати видиму боротьбу: тремтячі руки, переривчасте дихання, нетверда хода.")
            elif new_val <= 590 and delta <= -10:
                hints.append("ВИСНАЖЕННЯ: Персонаж помітно втомлений. GM ПОВИНЕН вплести втому в наратив (наприклад: 'ноги стають важкими', 'хвиля знесилення накриває').")
            # 590-1000: без підказки — персонаж у нормі

        elif "❤️ Здоров'я" in entry:
            m_vals = re.search(r'(\d+) -> (\d+)', entry)
            if not m_vals:
                continue
            old_val = int(m_vals.group(1))
            new_val = int(m_vals.group(2))
            delta = new_val - old_val

            if delta >= 1:
                hints.append("ЗЦІЛЕННЯ: Персонаж підлікувався. GM може згадати полегшення або фізичне відновлення.")
            elif delta < 0:
                if new_val <= 39:
                    hints.append("ЗДОРОВ'Я КРИТИЧНЕ: Персонаж тяжко поранений. GM ПОВИНЕН передати терміновість і смертельну небезпеку (наприклад: 'кров ллється потоком', 'кожен подих — як останній', 'смерть витає поруч').")
                elif new_val <= 69:
                    hints.append("СЕРЙОЗНА РАНА: Персонаж значно поранений. GM ПОВИНЕН чітко описати рану (наприклад: 'кров тече', 'гострий біль пронизує', 'хитається на ногах').")
                else:
                    hints.append("ЛЕГКА РАНА: Персонаж отримав поверхневий удар. GM МОЖЕ коротко згадати (наприклад: 'болісне зморщення', 'подряпина', 'жалючий біль від удару').")

    return "\n".join(hints)


_narr_diag_var: contextvars.ContextVar = contextvars.ContextVar("narr_diag", default=None)
_FALLBACK_UNAVAILABLE_NOTE = "_Детальний опис сцени тимчасово недоступний._"
_FALLBACK_BLOCKED_NOTE = "_Цю сцену неможливо описати детально. Спробуйте іншу дію._"
_FALLBACK_SHORTENED_NOTE = "_Сцену описано скорочено._"
_BLOCK_FINISH_MARKERS = ("PROHIBITED_CONTENT", "SAFETY", "BLOCKLIST", "SPII")
# Для ТЕКСТУ винятків (не finish_reason): голе "SAFETY" дає хибнопозитив на конфіг-помилки
# 400/INVALID_ARGUMENT, що згадують safety_settings. SAFETY -- лише окреме слово і НЕ
# SAFETY_SETTINGS / "SAFETY SETTING(S)". Вхід уже uppercase.
_BLOCK_EXC_RE = re.compile(r"PROHIBITED_CONTENT|BLOCKLIST|SPII|\bSAFETY\b(?![ _]?SETTING)")
# Fallback-абзац із director_notes: макс. довжина (символів) і макс. кількість фактів.
_FALLBACK_STORY_MAX_CHARS = 700
_FALLBACK_STORY_MAX_FACTS = 4
# Debug-діагностика roster: макс. кількість показаних попереджень.
_ROSTER_WARN_LIMIT = 8
_NOTE_PREFIX_RE = re.compile(
    r"^\s*(?:[-*•–—]+|\d+[.)])?\s*(?:(?:факт|fact|note|нотатка|director|gm)\s*\d*\s*[:\-–—]\s*)?",
    re.IGNORECASE,
)
_NOTE_MECHANICS_RE = re.compile(
    r"\b(?:DC|XP|HP|d\d+|\d+d\d+)\b|[+\-−]\s?\d+\s*(?:HP|XP|gold|золот)|\d+\s*(?:HP|XP|gold|золот|хп|дк)",
    re.IGNORECASE,
)


def _is_content_block(obj) -> bool:
    """True if a Gemini response/chunk was blocked by Google's content filter
    (finish_reason PROHIBITED_CONTENT/SAFETY/BLOCKLIST/SPII or prompt_feedback.block_reason)."""
    if obj is None:
        return False
    try:
        fr = obj.candidates[0].finish_reason if obj.candidates else None
        if fr is not None and any(m in str(fr).upper() for m in _BLOCK_FINISH_MARKERS):
            return True
    except Exception:
        pass
    try:
        pf = getattr(obj, "prompt_feedback", None)
        if pf is not None:
            br = getattr(pf, "block_reason", None)
            # BLOCKED_REASON_UNSPECIFIED = "не заблоковано" (дефолт enum), НЕ блок.
            if br and "UNSPECIFIED" not in str(br).upper():
                return True
    except Exception:
        pass
    return False


def _is_block_exception(exc) -> bool:
    """True якщо текст/тип винятку містить ознаки блоку контент-фільтра Google
    (SAFETY/PROHIBITED/BLOCKLIST/SPII)."""
    try:
        tname = type(exc).__name__.upper()
        s = f"{tname} {exc}".upper()
    except Exception:
        return False
    # Ім'я типу (напр. SafetyError/BlockedPromptException) -- довіряємо підрядку SAFETY;
    # звуження стосується лише вільного тексту повідомлення.
    if any(m in tname for m in _BLOCK_FINISH_MARKERS):
        return True
    return bool(_BLOCK_EXC_RE.search(s))


def _is_abort_finish(fr) -> bool:
    """Streaming fail-fast: finish_reason, після якого далі чекати чанки марно."""
    s = str(fr).upper()
    return "MALFORMED" in s or "OTHER" in s or any(m in s for m in _BLOCK_FINISH_MARKERS)


def _narr_diag_note_exc(diag, exc, where: str) -> None:
    """Виняток Narrator-виклику: ознаки блоку -> diag["content_blocked"]=True; у debug-режимі
    ще й замаскований текст помилки -> diag["errors"] (для STAGE 5b)."""
    if diag is None:
        return
    if _is_block_exception(exc):
        diag["content_blocked"] = True
    if _debug_meta_var.get():
        diag.setdefault("errors", []).append(f"{where}: {type(exc).__name__}: {_mask(exc)}")


def _fallback_story_from_notes(director_notes) -> str:
    """Склеює director_notes у короткий абзац для last-resort fallback.
    Прибирає маркери списку, службові префікси, markdown, нотатки з механікою
    та службові корекції. Повертає "" якщо придатних фактів немає."""
    if not isinstance(director_notes, (list, tuple)):
        return ""
    sentences = []
    for note in director_notes:
        if not isinstance(note, str):
            continue
        t = _NOTE_PREFIX_RE.sub("", note.strip(), count=1)
        t = re.sub(r"[*_`#>]+", "", t)
        t = re.sub(r"\s+", " ", t).strip()
        low = t.lower()
        if (len(t) < 8 or low.startswith("корекція") or low == "щось сталося."
                or _NOTE_MECHANICS_RE.search(t)):
            continue
        if t[-1] not in ".!?…":
            t += "."
        sentences.append(t[0].upper() + t[1:])
        if len(sentences) >= _FALLBACK_STORY_MAX_FACTS:
            break
    text = " ".join(sentences)
    if len(text) > _FALLBACK_STORY_MAX_CHARS:
        text = text[:_FALLBACK_STORY_MAX_CHARS].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return text


def _build_deterministic_narrative(updates: dict, profile: dict,
                                    old_location: str = "", old_scene: str = "",
                                    director_notes=None, content_blocked: bool = False) -> str:
    """
    Шар 3 (last-resort): детерміністично будує fallback-текст. Викликається лише
    якщо всі LLM-спроби Narrator'а провалились.
    - content_blocked=True: теж будуємо абзац із director_notes (вони згенеровані GM_Logic і
      вже пройшли фільтр Google) + примітка "Сцену описано скорочено."; якщо придатних notes
      немає — нейтральне повідомлення.
    - інакше, якщо є придатні director_notes: абзац із фактів + примітка курсивом.
    - інакше: локація/час/ефекти + примітка курсивом.
    """
    _notes_text = _fallback_story_from_notes(director_notes)
    if content_blocked:
        if _notes_text:
            return f"{_notes_text}\n\n{_FALLBACK_SHORTENED_NOTE}"
        return _FALLBACK_BLOCKED_NOTE
    if _notes_text:
        return f"{_notes_text}\n\n{_FALLBACK_UNAVAILABLE_NOTE}"

    parts = []

    # Локація (зміна або поточна)
    new_location = profile.get("Поточне місцезнаходження", "")
    loc_impact = updates.get("location_impact", "")

    if old_location and new_location and old_location != new_location:
        parts.append(
            f"Ви залишили *{old_location}* і вирушили до *{new_location}*."
        )
    elif new_location and new_location not in ("Невідомо", ""):
        parts.append(f"Ви перебуваєте у *{new_location}*.")
    elif loc_impact and loc_impact not in ("none", "None", ""):
        parts.append(f"Місце подій змінилось.")

    # Сцена
    new_scene = profile.get("Поточна сцена", "")
    scene_impact = updates.get("scene_impact", "")
    if old_scene and new_scene and old_scene != new_scene and new_scene not in ("none", "None", ""):
        parts.append(f"Обстановка навколо вас змінилась.")
    elif scene_impact and scene_impact not in ("none", "None", ""):
        parts.append(f"Обставини набули нового обертання.")

    # Час
    minutes_passed = int(updates.get("minutes_passed", 0))
    if minutes_passed >= _MINUTES_PER_DAY:
        days = minutes_passed // _MINUTES_PER_DAY
        parts.append(f"Минуло {'кілька днів' if days > 1 else 'цілий день'}.")
    elif minutes_passed >= 60:
        hours = minutes_passed // 60
        h_word = "годину" if hours == 1 else ("дві години" if hours == 2 else f"близько {hours} годин")
        parts.append(f"Минуло {h_word}.")
    elif minutes_passed > 0:
        parts.append(f"Минуло кілька хвилин.")

    # Здоров'я
    health_impact = updates.get("health_impact", "none")
    if health_impact not in ("none", "None", "", None):
        if health_impact == "heal_small" or health_impact == "heal_full":
            parts.append("Рани потроху загоюються.")
        elif health_impact in ("dmg_light",):
            parts.append("Невелика подряпина нагадує про недавню небезпеку.")
        elif health_impact in ("dmg_medium", "dmg_heavy", "dmg_fatal"):
            parts.append("Біль від поранення дається взнаки.")

    # Енергія
    energy_impact = updates.get("energy_impact", "none")
    if energy_impact in ("spend_large", "spend_medium"):
        parts.append("Втома легко далася взнаки — сили зменшились.")
    elif energy_impact in ("restore_full", "sleep"):
        parts.append("Відпочинок повернув сили.")
    elif energy_impact in ("restore_small", "restore_medium"):
        parts.append("Коротка перепочинка освіжила думки.")

    # Золото
    gold_impact = str(updates.get("gold_impact", "none")).strip()
    if gold_impact not in ("none", "None", "0", ""):
        if gold_impact.startswith("-") or gold_impact in ("spend_small", "spend_medium", "spend_large"):
            parts.append("Золото змінило власника.")
        elif gold_impact.startswith("+") or gold_impact in ("earn_small", "earn_medium", "earn_large"):
            parts.append("Кілька монет осіло у кишені.")

    # Інвентар
    inv_new = updates.get("inventory_new") or []
    inv_lost = updates.get("inventory_lost") or []
    if isinstance(inv_new, str):
        inv_new = [inv_new] if inv_new else []
    if isinstance(inv_lost, str):
        inv_lost = [inv_lost] if inv_lost else []
    if inv_lost:
        item_str = ", ".join(f"*{i}*" for i in inv_lost[:2])
        parts.append(f"Ви розстались з {item_str}.")
    if inv_new:
        item_str = ", ".join(f"*{i}*" for i in inv_new[:2])
        parts.append(f"До ваших речей додалось: {item_str}.")

    # Якщо нічого немає — загальна фраза
    if not parts:
        current_loc = new_location or "невідомому місці"
        parts.append(f"Час спливав непомітно у *{current_loc}*.")

    return " ".join(parts) + f"\n\n{_FALLBACK_UNAVAILABLE_NOTE}"


async def summarize_turn(gm_response):
    clean_story = gm_response.split("📊")[0][:800]
    prompt = build_summarize_turn_prompt(clean_story)
    try:
        def _sync_gen():
            return model_worker.generate_content(prompt)

        resp = await asyncio.to_thread(_sync_gen)
        return resp.text.strip()
    except Exception:
        return "Гравець діє."


async def summarize_full_turn(
    user_input: str,
    gm_response: str,
    mechanical_updates: dict | None = None,
) -> str:
    """Об'єднана сумаризація: дія гравця + результат GM → одне речення."""
    prompt = build_summarize_full_turn_prompt(user_input, gm_response, mechanical_updates=mechanical_updates)
    try:
        def _sync_gen():
            return model_worker.generate_content(prompt)

        resp = await asyncio.to_thread(_sync_gen)
        return resp.text.strip()
    except Exception:
        return f"{user_input[:100]} → ..."


def _build_narrator_prompt(user_input, director_notes, npc_context_text,
                           player_name, player_house, current_scene,
                           current_location, impact_narrative_hints,
                           puppet_mode=False, recent_history_text=None,
                           erotic_mode=False, active_roster=None, dead_npcs=None,
                           departing_roster_text="", arriving_roster_text="",
                           scene_continuity_block: str = "",
                           combat_log=None):
    """Returns (static, dynamic): static -> system_instruction (cached), dynamic -> contents."""
    return build_narrator_parts(
        user_input=user_input,
        director_notes=director_notes,
        npc_context_text=npc_context_text,
        player_name=player_name,
        player_house=player_house,
        current_scene=current_scene,
        current_location=current_location,
        impact_narrative_hints=impact_narrative_hints,
        puppet_mode=puppet_mode,
        recent_history_text=recent_history_text,
        erotic_mode=erotic_mode,
        active_roster=active_roster,
        dead_npcs=dead_npcs,
        departing_roster_text=departing_roster_text,
        arriving_roster_text=arriving_roster_text,
        scene_continuity_block=scene_continuity_block,
        combat_log=combat_log,
    )


def _static_tag(static: str) -> str:
    """Compact identifier of a static system_instruction for debug/thoughts (no big text dup)."""
    import hashlib
    _h = hashlib.md5((static or "").encode("utf-8")).hexdigest()[:8]
    return f"[SYSTEM_INSTRUCTION static len={len(static or '')} md5={_h}]"


# ══════════════ DEBUG TRACE: збір метаданих LLM-викликів (лише debug-режим) ══════════════
# Контракт з core.ai_client: start_call_meta_capture / get_call_meta / call_meta_mark /
# stop_call_meta_capture / mask_secrets + kwarg meta_label. Усе через getattr-guard: якщо API
# відсутнє — трейс просто без метрик. Поза debug-режимом жоден з цих хелперів нічого не робить
# (_debug_meta_var=False -> _ml() повертає {}, mark() повертає 0).
import inspect as _inspect
from core import ai_client as _aic

_debug_meta_var: contextvars.ContextVar = contextvars.ContextVar("debug_meta_active", default=False)
_SECRET_RE = re.compile(
    r"AIza[0-9A-Za-z_\-]{20,}|\b\d{6,}:[A-Za-z0-9_\-]{25,}|https?://\S+|"
    r"(?i:(?:api[_-]?key|token|secret|authorization)\s*[=:]\s*\S+)"
)


def _mask(text, limit: int = 200) -> str:
    """Маскує секрети та обрізає текст (через ai_client.mask_secrets, з локальним fallback)."""
    s = "" if text is None else str(text)
    fn = getattr(_aic, "mask_secrets", None)
    try:
        if fn is not None:
            s = fn(s, limit)
    except Exception:
        pass
    s = _SECRET_RE.sub("[REDACTED]", s)  # belt-and-braces (idempotent)
    s = s.replace("\n", " ")
    return s if len(s) <= limit else s[:limit] + "…"


def _meta_start() -> None:
    fn = getattr(_aic, "start_call_meta_capture", None)
    if fn is None:
        return
    try:
        fn()
        _debug_meta_var.set(True)
    except Exception as exc:
        logger.warning(f"[DEBUG_MODE] start_call_meta_capture failed: {type(exc).__name__}")


def _meta_stop() -> None:
    """Idempotent: no-op якщо збір не вмикався у цьому task-контексті."""
    if not _debug_meta_var.get():
        return
    _debug_meta_var.set(False)
    fn = getattr(_aic, "stop_call_meta_capture", None)
    try:
        if fn is not None:
            fn()
    except Exception as exc:
        logger.warning(f"[DEBUG_MODE] stop_call_meta_capture failed: {type(exc).__name__}")


def _meta_mark() -> int:
    if not _debug_meta_var.get():
        return 0
    try:
        fn = getattr(_aic, "call_meta_mark", None)
        if fn is not None:
            return int(fn())
        return len(_aic.get_call_meta())
    except Exception:
        return 0


def _meta_records(a=None, b=None) -> list:
    if not _debug_meta_var.get():
        return []
    try:
        return list(_aic.get_call_meta())[a:b]
    except Exception:
        return []


def _ml(label: str, fn=None) -> dict:
    """kwargs {"meta_label": label} лише в debug-режимі і лише якщо callee його приймає."""
    if not _debug_meta_var.get():
        return {}
    if fn is not None:
        try:
            params = _inspect.signature(fn).parameters
            if "meta_label" not in params and not any(
                p.kind is _inspect.Parameter.VAR_KEYWORD for p in params.values()
            ):
                return {}
        except (TypeError, ValueError):
            return {}
    return {"meta_label": label}


def _fmt_call(rec: dict, detail: bool = False) -> list:
    """Компактне форматування одного запису call-meta (1-3 рядки)."""
    if not isinstance(rec, dict):
        return [f"  ? {_mask(rec)}"]
    u = rec.get("usage") or {}
    atts = [a for a in (rec.get("attempts") or []) if isinstance(a, dict)]
    errs = [a for a in atts if a.get("error_code") or a.get("error")]
    head = f"  ⏱ {rec.get('label') or rec.get('path') or '?'} [{rec.get('path')}] {rec.get('model')} " \
           f"{rec.get('elapsed_s')}s"
    if rec.get("hedge_idx") is not None:
        head += f" hedge#{rec.get('hedge_idx')}"
    out = [head]
    out.append(
        f"    finish={rec.get('finish_reason')} block={rec.get('block_reason')} text_len={rec.get('text_len')} "
        f"tokens in/out/think/cached/total="
        f"{u.get('prompt')}/{u.get('candidates')}/{u.get('thoughts')}/{u.get('cached')}/{u.get('total')}"
    )
    out.append(
        f"    schema sent={rec.get('schema_sent')} retry={rec.get('schema_retry')} | "
        f"cache={rec.get('cache_mode')} fallback={rec.get('cache_fallback')} | "
        f"breaker_open={rec.get('breaker_open')} | attempts={len(atts)} retries={len(errs)} "
        f"codes={[a.get('error_code') for a in errs]}"
    )
    if detail:
        sf = rec.get("safety")
        if sf:
            out.append(f"    safety={_mask(sf, 240)}")
        for a in errs:
            out.append(
                f"    retry #{a.get('n')}: code={a.get('error_code')} sleep={a.get('sleep_s')}s "
                f"err={_mask(a.get('error'), 160)}"
            )
    exc = rec.get("exception")
    if exc:
        out.append(f"    exception={_mask(exc.get('type') if isinstance(exc, dict) else exc, 80)}: "
                   f"{_mask(exc.get('msg') if isinstance(exc, dict) else '', 200)}")
    return out


def _fmt_calls(records: list, detail: bool = False) -> list:
    if not records:
        return ["  ⏱ (no LLM calls captured in this stage)"]
    lines = []
    for r in records:
        lines.extend(_fmt_call(r, detail=detail))
    return lines


def _npc_index_from_session(chat_id) -> dict:
    """{name: (location, scene)} з уже завантаженого user_sessions[chat_id]['npc_cache'] (без Sheets)."""
    idx = {}
    try:
        cache = user_sessions.get(chat_id, {}).get("npc_cache", {}) or {}
        dead = user_sessions.get(chat_id, {}).get("dead_npc_names", set()) or set()
        for loc, npcs in cache.items():
            for n in npcs or []:
                name = n.get("name") if isinstance(n, dict) else None
                if name and name not in dead:
                    idx[name] = (loc, n.get("scene") or "—")
    except Exception:
        return {}
    return idx


def _roster_diag_lines(chat_id, legal_npc_names, texts: dict) -> list:
    """Діагностика ростеру: legal_npc_names + попередження про NPC, згаданих у texts
    ({source_label: text}), яких немає в ростері. Лише in-memory дані (жодних запитів до Sheets)."""
    lines = [f"  roster (legal_npc_names, {len(legal_npc_names or [])}): {list(legal_npc_names or [])}"]
    idx = _npc_index_from_session(chat_id)
    if not idx:
        lines.append("  (NPC_DB cache not loaded in session — mention check skipped)")
        return lines
    legal = set(legal_npc_names or [])
    first_tokens: dict = {}
    for nm in idx:
        tok = nm.split()[0].lower() if nm.split() else ""
        first_tokens.setdefault(tok, []).append(nm)
    warned = 0
    for src, text in texts.items():
        low = (text or "").lower()
        if not low:
            continue
        seen = set()
        for nm, (loc, scene) in idx.items():
            if nm in legal or nm in seen:
                continue
            tok = nm.split()[0].lower() if nm.split() else ""
            hit = nm.lower() in low or (len(tok) >= 5 and len(first_tokens.get(tok, [])) == 1 and tok in low)
            if hit:
                seen.add(nm)
                if warned < _ROSTER_WARN_LIMIT:
                    lines.append(
                        f"  ⚠ '{_mask(nm, 60)}' згадана в {src}, але не в ростері; NPC_DB: "
                        f"{_mask(loc, 60)} / {_mask(scene, 60)}"
                    )
                warned += 1
    if warned > _ROSTER_WARN_LIMIT:
        lines.append(f"  ... ще {warned - _ROSTER_WARN_LIMIT} попереджень")
    if warned == 0:
        lines.append("  (no out-of-roster NPC mentions detected)")
    return lines


def _wrapper_desc(w) -> str:
    try:
        return f"{getattr(w, 'model_name', '?')} (thinking={getattr(w, 'thinking_level', None)})"
    except Exception:
        return "?"


def _trace_finalize(trace, chat_id, global_start, marks: dict) -> None:
    """Дописує у debug-trace шапку, метрики етапів і total time. Ніколи не кидає виняток.
    Нові ключі trace (рендерить bot/handlers.py::_send_debug_trace_file):
      trace["header"]: list[str]; trace[<stage>]["metrics"]: list[str]; trace["narrator_diag"]: list[str]."""
    if trace is None:
        return
    try:
        import config as _cfg
        total = time.time() - global_start
        trace["total_s"] = round(total, 2)
        ab_on = bool(NARRATOR_AB_ENABLED)
        cache_on = getattr(_aic, "GEMINI_EXPLICIT_CACHE_ENABLED", getattr(_cfg, "GEMINI_EXPLICIT_CACHE_ENABLED", None))
        trace["header"] = [
            f"  BOT_VERSION: {getattr(_cfg, 'BOT_VERSION', '?')}",
            f"  Censor/Worker: {_wrapper_desc(model_worker)}",
            f"  GM_Logic:      {_wrapper_desc(model_gm_logic)}",
            f"  Narrator:      {_wrapper_desc(model_narrator)}"
            + (f" | A/B alt: {_wrapper_desc(model_narrator_alt)}" if ab_on else ""),
            f"  Narrator experiment: model={getattr(model_narrator, 'model_name', '?')} "
            f"thinking_level={getattr(model_narrator, 'thinking_level', '?')} "
            f"preamble_variant={getattr(model_narrator, 'preamble_variant', '?')}"
            + (f" | alt: model={getattr(model_narrator_alt, 'model_name', '?')} "
               f"thinking_level={getattr(model_narrator_alt, 'thinking_level', '?')} "
               f"preamble_variant={getattr(model_narrator_alt, 'preamble_variant', '?')}" if ab_on else ""),
            f"  Flags: GEMINI_EXPLICIT_CACHE_ENABLED={cache_on} NARRATOR_AB_ENABLED={ab_on} "
            f"erotic={chat_id in getattr(_cfg, 'EROTIC_USERS', set())} "
            f"puppet={chat_id in getattr(_cfg, 'PUPPET_USERS', set())} "
            f"godmode={chat_id in getattr(_cfg, 'GODMODE_USERS', set())}",
            f"  Turn total time: {total:.2f}s",
        ]
        if not _debug_meta_var.get():
            for st in ("censor", "worker", "gm_logic", "narrator"):
                trace[st].setdefault("metrics", ["  (call metrics unavailable: capture API not active)"])
            return

        def _sl(a_key, b_key):
            if a_key not in marks:
                return []
            return _meta_records(marks[a_key], marks.get(b_key))

        trace["censor"]["metrics"] = _fmt_calls(_sl("c0", "c1"))
        if "c1" in marks:
            trace["worker"]["metrics"] = _fmt_calls(_sl("c1", "w1"))
        if "w1" in marks:
            trace["gm_logic"]["metrics"] = _fmt_calls(_sl("w1", "g1"))
        if "g1" in marks:
            nrecs = _sl("g1", "n1")
            trace["narrator"]["metrics"] = _fmt_calls(nrecs)
            trace["narrator_diag"] = _narrator_diag_lines(nrecs, trace.pop("_narr_summary", {}))
    except Exception as exc:  # trace must never break the turn
        logger.warning(f"[DEBUG_MODE] trace finalize failed: {type(exc).__name__}: {exc}")


def _narrator_diag_lines(records: list, summary: dict) -> list:
    lines = []
    for r in records:
        lines.extend(_fmt_call(r, detail=True))
    if not records:
        lines.append("  (no narrator LLM calls captured)")
    for e in summary.get("errors", []):
        lines.append(f"  caught error: {_mask(e, 240)}")
    lines.append(f"  content_blocked={summary.get('content_blocked')}")
    lines.append(f"  fallback branch shown to player: {summary.get('branch')}")
    lines.append(f"  Narrator total time: {summary.get('elapsed_s')}s")
    return lines


def _is_debug_active(chat_id: int, profile: dict, debug_users: set) -> bool:
    """Return True if debug mode is active for this user.

    Two sources are OR-combined so that debug survives Render cold-starts
    that wipe the in-memory DEBUG_USERS set:
    - ``chat_id in debug_users``  — immediate in-memory toggle (/debugmode)
    - ``profile["_debug_mode"]``  — persistent flag stored in Google Sheets
    """
    return (chat_id in debug_users) or bool(profile.get("_debug_mode", False))


async def _run_narrator_chain(model_wrapper, narrator_prompt, model_key, narrator_static=None) -> NarrationResult:
    """BLOCKING narrator chain: attempt 1 (hedged) -> quality checks -> attempt 2 -> attempt 3 (temp 0.5, 90s).

    Returns NarrationResult. If all attempts fail -> used_fallback=True, text=None
    (the caller builds the deterministic narrative). Exceptions (except CancelledError/
    BaseException) from attempts 1, 2 and 3 are caught and logged; the chain falls through
    to the next attempt, so a content-block still reaches the content_blocked fallback.
    """
    t_chain = time.time()
    attempt_ms: list = []
    # Static part -> system_instruction (IDENTICAL for all attempts/models => same cache key).
    # config_with is pure (no network); cache upgrade happens in generate_content's worker thread.
    _cfg_base = model_wrapper.config_with(system_instruction=narrator_static) if narrator_static else None
    _mname = getattr(model_wrapper, "model_name", None) or model_key

    # A/B: два паралельні ланцюги -> префікс моделі в meta_label, щоб розрізнити виклики у trace.
    _lp = f"{model_key}." if NARRATOR_AB_ENABLED else ""

    def _note_block(resp_obj):
        # _narr_diag_var holds a mutable dict set by process_game_turn (gather() children copy
        # the context but share the dict); content_blocked is only used to pick last-resort text.
        diag = _narr_diag_var.get()
        if diag is not None and _is_content_block(resp_obj):
            diag["content_blocked"] = True
            logger.info(f"[NARRATOR_FAIL] model={model_key} content-filter block detected")

    def _note_exc(exc, where):
        # Виняток з ознаками блоку (SAFETY/PROHIBITED/BLOCKLIST/SPII) -> content_blocked;
        # текст помилки (замаскований) йде в debug-діагностику Narrator.
        _narr_diag_note_exc(_narr_diag_var.get(), exc, f"{_lp}{where}")

    def _res(text, final, fallback=False):
        return NarrationResult(
            model_key=model_key, text=text,
            total_ms=int((time.time() - t_chain) * 1000),
            attempt_ms=list(attempt_ms), final_attempt=final,
            used_fallback=fallback, error=None,
        )

    # --- Attempt 1: single request via hedged helper (hedge_count=1: no duplicate
    #     request -> no doubled token cost/quota; 60s timeout still applies) ---
    _t = time.time()
    try:
        narrator_response = await hedged_generate_content_async(
            model_wrapper, narrator_prompt, config=_cfg_base, hedge_count=1, max_retries=2,
            **_ml(f"{_lp}narrator.attempt1.hedged", hedged_generate_content_async)
        )
        story = narrator_response.text.strip() if narrator_response and narrator_response.text else ""
        _note_block(narrator_response)
    except Exception as _hedge_exc:
        _note_exc(_hedge_exc, "attempt1")
        logger.warning(
            f"[NARRATOR_FAIL] model={model_key}({_mname}) Attempt 1 (hedged) raised "
            f"{type(_hedge_exc).__name__}: {str(_hedge_exc)[:120]} — fallthrough до Шар 2 retry"
        )
        story = ""
    attempt_ms.append(int((time.time() - _t) * 1000))

    # --- Quality checks (Шар 1) ---
    _story_truncated = story and len(story) > 20 and not story.rstrip().endswith(('.', '!', '?', '…', '"', '*'))
    _is_placeholder = story and any(marker in story.lower() for marker in _PLACEHOLDER_MARKERS)
    _is_hard_fail = not story or _is_placeholder or len(story) < 20

    if _story_truncated and not _is_hard_fail:
        if len(story) >= 100:
            logger.info(
                f"[NARRATOR_FAIL] model={model_key} Attempt 1 truncated but substantial "
                f"({len(story)} chars) — accepted with ellipsis. tail={story[-50:]!r}"
            )
            story = story.rstrip() + "…"
        else:
            _is_hard_fail = True

    if not _is_hard_fail:
        return _res(story, 1)

    _fail_reason = (
        "empty" if not story
        else "placeholder" if _is_placeholder
        else f"too_short({len(story)})" if len(story) < 20
        else f"truncated_short({len(story)})"
    )
    logger.info(
        f"[NARRATOR_FAIL] model={model_key} Attempt 1 failed. reason={_fail_reason} "
        f"prompt_len={len(narrator_prompt)} "
        f"story_preview={story[:200]!r}"
    )

    # --- Attempt 2: blocking retry ---
    def _sync_gen_narrator_retry():
        _t0 = time.time()
        try:
            resp = model_wrapper.generate_content(
                narrator_prompt, config=_cfg_base,
                **_ml(f"{_lp}narrator.attempt2", model_wrapper.generate_content))
        except Exception as _a2_exc:
            _note_exc(_a2_exc, "attempt2")
            raise
        _elapsed = time.time() - _t0
        try:
            fr = resp.candidates[0].finish_reason if resp and resp.candidates else None
        except Exception:
            fr = None
        try:
            safety = resp.candidates[0].safety_ratings if resp and resp.candidates else None
        except Exception:
            safety = None
        try:
            block_reason = resp.prompt_feedback.block_reason if resp and resp.prompt_feedback else None
        except Exception:
            block_reason = None
        _text = (resp.text or "") if resp else ""
        _note_block(resp)
        logger.info(
            f"[NARRATOR_FAIL] model={model_key} Attempt 2 (blocking) done. elapsed={_elapsed:.2f}s "
            f"finish_reason={fr} block_reason={block_reason} "
            f"safety={safety} "
            f"text_len={len(_text)} preview={_text[:200]!r}"
        )
        return resp

    _t = time.time()
    try:
        retry_resp = await _safe_to_thread(_sync_gen_narrator_retry)
    except Exception as _a2_outer:  # not BaseException: CancelledError must propagate
        # _note_exc already ran inside _sync_gen_narrator_retry for generate_content errors.
        logger.warning(
            f"[NARRATOR_FAIL] model={model_key}({_mname}) Attempt 2 raised "
            f"{type(_a2_outer).__name__}: {_mask(_a2_outer, 120)} — proceeding to Attempt 3"
        )
        retry_resp = None
    finally:
        attempt_ms.append(int((time.time() - _t) * 1000))
    story = retry_resp.text.strip() if retry_resp and retry_resp.text else ""

    if story and len(story) >= 20:
        return _res(story, 2)

    logger.info(
        f"[NARRATOR_FAIL] model={model_key} Attempts 1+2 failed (blocking). "
        f"prompt_len={len(narrator_prompt)} story={story!r} "
        f"— proceeding to Attempt 3 (blocking low-temp)"
    )

    # --- Attempt 3: low-temp blocking ---
    def _sync_gen_narrator_third_blocking():
        _t0 = time.time()
        try:
            _cfg = model_wrapper.config_with(system_instruction=narrator_static, temperature=0.5)
            resp = model_wrapper.generate_content(
                narrator_prompt, config=_cfg,
                **_ml(f"{_lp}narrator.attempt3", model_wrapper.generate_content))
        except Exception as _a3_exc:
            _note_exc(_a3_exc, "attempt3")
            resp = model_wrapper.generate_content(
                narrator_prompt, config=_cfg_base,
                **_ml(f"{_lp}narrator.attempt3.retry", model_wrapper.generate_content))
        _elapsed = time.time() - _t0
        _text = (resp.text or "") if resp else ""
        _note_block(resp)
        logger.info(
            f"[NARRATOR_FAIL] model={model_key} Attempt 3 (blocking low-temp) done. "
            f"elapsed={_elapsed:.2f}s text_len={len(_text)} "
            f"preview={_text[:200]!r}"
        )
        return resp

    _t = time.time()
    try:
        third_resp = await asyncio.wait_for(
            _safe_to_thread(_sync_gen_narrator_third_blocking),
            timeout=90.0,
        )
    except asyncio.TimeoutError:
        logger.warning(f"[NARRATOR_FAIL] model={model_key} Attempt 3 timed out after 90s — falling through to deterministic fallback")
        third_resp = None
    except Exception as _exc:
        _note_exc(_exc, "attempt3")
        logger.warning(f"[NARRATOR_FAIL] model={model_key} Attempt 3 exception: {type(_exc).__name__}: {_exc} — falling through")
        third_resp = None
    attempt_ms.append(int((time.time() - _t) * 1000))
    third_text = third_resp.text.strip() if third_resp and third_resp.text else ""
    if third_text and len(third_text) >= 50:
        return _res(third_text, 3)

    logger.warning(
        f"[NARRATOR_FAIL] model={model_key} All 3 attempts failed (blocking path)."
    )
    return _res(None, "fallback", fallback=True)


async def commit_narration_to_history(chat_id, user_input, story, mech_updates):
    """Summarize the turn, append it to session history and run sliding-window compression.

    Public: called by the production path (at the end of background_task) and,
    in Narrator A/B mode, by the vote handler after the player picks a variant.
    `story` is the final chosen text (the "📊" change-log tail is stripped here).
    """
    try:
        clean_story = story.split("📊")[0][:800]
        turn_summary = await summarize_full_turn(user_input, clean_story, mechanical_updates=mech_updates)

        if chat_id in user_sessions:
            hist = user_sessions[chat_id].get('history', [])
            hist.append({"role": "Turn", "content": turn_summary})

            if len(hist) > 20:
                # Sliding window: стискаємо старі записи, зберігаємо останні 15 verbatim
                old_part = hist[:-15]
                recent_part = hist[-15:]
                history_to_compress = "\n".join([f"{m['role']}: {m['content']}" for m in old_part])
                summary_prompt = build_history_summary_prompt(history_to_compress)
                try:
                    def _sync_gen_sum():
                        return model_worker.generate_content(summary_prompt)

                    summary_resp = await asyncio.to_thread(_sync_gen_sum)
                    user_sessions[chat_id]['history'] = [
                        {"role": "SYSTEM", "content": f"PREVIOUS EVENTS SUMMARY:\n{summary_resp.text.strip()}"}
                    ] + recent_part
                except Exception as ex:
                    print(f"⚠️ Помилка стиснення: {ex}")
                    user_sessions[chat_id]['history'] = hist[-20:]
            else:
                user_sessions[chat_id]['history'] = hist
    except Exception as e:
        logger.error(f"[BG] commit_narration_to_history failed: {e}", exc_info=True)


def _debug_meta_cleanup(fn):
    """Decorator: гарантує (try/finally, §5.4) зупинку збору call-meta debug-trace на будь-якому
    виході з ходу (return / виняток / cancel) — стан не витікає між ходами. functools.wraps
    зберігає __wrapped__ (inspect.getsource бачить оригінальне тіло)."""
    @functools.wraps(fn)
    async def _wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        finally:
            _meta_stop()
    return _wrapper


@_debug_meta_cleanup
async def process_game_turn(chat_id, user_input, progress_callback=None, narrator_queue=None):
    """
    Головний ігровий цикл.
    progress_callback: async callable(str) — оновлює статус для гравця.
    narrator_queue: asyncio.Queue — якщо передано, стрімить narrator-текст чанками замість блокуючого виклику.
    """
    clear_thoughts()
    debug_log = ""
    global_start = time.time()
    debug_log += f"🚀 [START] Хід гравця {chat_id}..."

    # Early init so the crash-handler (except path) never hits NameError if an
    # exception fires before these are assigned post get_user_data.
    _debug_active = False
    _debug_trace = None

    user_id = chat_id
    timing_details = []

    t_start = time.time()

    profile, row_id = await get_user_data(user_id)
    if not profile: return "❌ Профіль не знайдено.", []

    # === DEBUG TRACE (admin-only, toggle via /debugmode) ===
    # _debug_active placed AFTER get_user_data so profile is available for the
    # persistent flag check.  Survives Render cold-start that wipes DEBUG_USERS.
    from core.cheats import DEBUG_USERS
    _debug_active = _is_debug_active(chat_id, profile, DEBUG_USERS)
    if _debug_active:
        logger.info(
            f"[DEBUG_MODE] Active for chat_id={chat_id} "
            f"(in_set={chat_id in DEBUG_USERS}, profile_flag={bool(profile.get('_debug_mode', False))})"
        )
    _debug_trace: dict | None = None
    _tmarks: dict = {}  # межі етапів у call-meta (лише debug); див. _trace_finalize
    if _debug_active:
        _meta_start()  # збір метаданих LLM-викликів лише для debug-гравця; stop — у finally обгортки
        _tmarks["c0"] = _meta_mark()
        _debug_trace = {
            "chat_id": chat_id,
            "user_input": user_input,
            "timestamp": time.time(),
            "censor": {"prompt": None, "raw": None, "parsed": None, "thoughts": []},
            "worker": {"prompt": None, "raw": None, "parsed": None, "thoughts": []},
            "gm_logic": {"prompt": None, "raw": None, "parsed": None, "thoughts": []},
            "narrator": {"prompt": None, "thoughts": [], "final_text": None},
            "logs": [],
        }

    session = user_sessions.get(chat_id, {})
    if session.get("state") == "INITIALIZING":
        return "⏳ *Світ відроджується... Зачекайте кілька секунд.*", []
    if chat_id not in user_sessions:
        user_sessions[chat_id] = {"state": "GAME_ACTIVE", "history": [], "npc_cache": {}, "dead_npc_names": set()}
    else:
        # Idempotent guard: підтягуємо ключі, якщо session вже існує але без нових полів
        if "npc_cache" not in user_sessions[chat_id]:
            user_sessions[chat_id]["npc_cache"] = {}
        if "dead_npc_names" not in user_sessions[chat_id]:
            user_sessions[chat_id]["dead_npc_names"] = set()

    # A1: lazy NPC-cache guard. Після рестарту/resume npc_cache порожній, а ростер будується лише з нього
    # (GM не бачить NPC -> npc_updates=[] -> refresh не викликається = deadlock). Один refresh, прапор ставимо
    # ДО await (захист від паралельних/повторних спроб). Throttle — за часом (_npc_cache_attempt_ts), НЕ за
    # bool-прапором handlers (_npc_cache_attempted ставиться навіть після невдалої спроби). Backoff залежить
    # від наслідку попередньої спроби (_npc_cache_retry_after_s): збій/відсутній лист -> 60 с; refresh ок, але
    # активних NPC немає (легітимно порожній лист) -> 600 с. Заповнений кеш -> guard no-op.
    _sess_guard = user_sessions[chat_id]
    if not _sess_guard.get("npc_cache"):
        _now_ts = time.time()
        _retry_after = _sess_guard.get("_npc_cache_retry_after_s", _NPC_CACHE_RETRY_FAIL_S)
        if _now_ts - _sess_guard.get("_npc_cache_attempt_ts", 0) >= _retry_after:
            _sess_guard["_npc_cache_attempt_ts"] = _now_ts
            _sess_guard["_npc_cache_retry_after_s"] = _NPC_CACHE_RETRY_FAIL_S
            try:
                _refreshed = await refresh_npc_database(chat_id)  # to_thread всередині (operations.py)
                if _refreshed and not _sess_guard.get("npc_cache"):
                    _sess_guard["_npc_cache_retry_after_s"] = _NPC_CACHE_RETRY_EMPTY_S
            except Exception as _e:
                logger.warning(f"[NPC CACHE GUARD] refresh failed for {chat_id}: {_e}")
    history = session.get('history', [])

    history_text = "\n".join([f"{msg['role']}: {msg['content']}" for msg in history[-40:]])

    # === ОНОВЛЕНО: Читаємо локацію та сцену ===
    old_location = profile.get("Поточне місцезнаходження", "Невідомо")
    old_scene = profile.get("Поточна сцена", "Невідомо")

    # === P1 FIX: Фільтрований стан для GM (без числових статів) ===
    # D&D-профіль: hp_current / hp_max (абсолютні числа, напр. 11/11).
    # Legacy-профіль: "Здоров'я" вже у шкалі 0–100.
    # _qualitative очікує шкалу 0–100, тому для D&D конвертуємо ratio.
    if "hp_current" in profile and "hp_max" in profile:
        hp_max_val = safe_int(profile.get("hp_max", 0), 0)
        hp_current_val = safe_int(profile.get("hp_current", 0), 0)
        # hp_max=0 не може бути у живого персонажа — дефолт 100 (повне здоров'я)
        hp = round(100 * hp_current_val / hp_max_val) if hp_max_val > 0 else 100
    else:
        hp = safe_int(profile.get("Здоров'я", 100), 100)
    energy = safe_int(profile.get("Енергія", 1000), 1000)
    gold = safe_int(profile.get("Особисте Золото", 0), 0)

    def _qualitative(val, thresholds):
        """Перетворює число на якісну мітку."""
        for threshold, label in thresholds:
            if val <= threshold:
                return label
        return thresholds[-1][1]

    hp_label = _qualitative(hp, [(0, "dead"), (29, "critically wounded — on the brink of death"), (49, "seriously wounded"), (69, "wounded"), (89, "lightly bruised"), (100, "healthy")])
    energy_label = _qualitative(energy, [(0, "collapsed from exhaustion"), (200, "barely standing, trembling with fatigue"), (400, "visibly exhausted"), (600, "somewhat tired"), (800, "energetic"), (1000, "full of vigour")])
    gold_label = _qualitative(gold, [(0, "penniless"), (20, "a few coins"), (100, "modest purse"), (500, "comfortable wealth"), (2000, "wealthy"), (100000, "rich as a lord")])

    player_state_for_gm = {
        "Name": profile.get("Ім'я", ""),
        "House": profile.get("Дім", ""),
        "Title": profile.get("Титул", ""),
        "Physical condition": hp_label,
        "Fatigue level": energy_label,
        "Wealth": gold_label,
        "Weapon": profile.get("Зброя", "None"),
        "Armor": profile.get("Броня", "None"),
        "Transport": profile.get("Транспорт", "None"),
        "Inventory": _format_inventory_for_prompt(profile.get("Інвентар", [])),
        "Worldview": profile.get("Світогляд", ""),
        "Traits": profile.get("Риси", ""),
        "Flaws": profile.get("Вади", ""),
        "Enemies": profile.get("Вороги", ""),
        "Friends": profile.get("Друзі", ""),
    }
    profile_json = json.dumps(player_state_for_gm, ensure_ascii=False, indent=2)

    curr_loc = profile.get("Поточне місцезнаходження", "Невідомо")
    curr_scene = profile.get("Поточна сцена", "Невідомо")
    curr_region = profile.get("Регіон", get_region_for_location(curr_loc) or "Невідомо")

    # === ОНОВЛЕНО: 3-рівнева фільтрація NPC (Регіон → Локація → Сцена) ===
    context_knowledge = await get_relevant_context(user_input, curr_loc)
    npc_context_text, legal_npc_names, npc_reputation_context = get_location_npcs(
        chat_id, curr_loc, curr_scene, current_region=curr_region
    )

    # === B+C: відстежуємо зникнення NPC між ходами (розділяємо мертвих та просто відсутніх) ===
    _dead_names_cache = get_dead_npc_names(chat_id)
    prev_legal_npc_names = user_sessions.get(chat_id, {}).get("prev_legal_npc_names", [])
    absent_npcs_raw = [n for n in prev_legal_npc_names if n not in set(legal_npc_names)]
    dead_npcs = [n for n in absent_npcs_raw if n in _dead_names_cache]
    absent_npcs = [n for n in absent_npcs_raw if n not in _dead_names_cache]
    # Мертві ніколи не повернуться — не зберігаємо їх у prev_legal_npc_names
    user_sessions.setdefault(chat_id, {})["prev_legal_npc_names"] = [n for n in legal_npc_names if n not in _dead_names_cache]
    if dead_npcs or absent_npcs:
        print(f"☠️ [B+C] Мертві: {dead_npcs} | Зниклі живі: {absent_npcs} | Активний: {legal_npc_names}")
    else:
        print(f"👁️ [B+C] Ростер стабільний: {legal_npc_names}")

    duration = time.time() - t_start
    timing_details.append(f"📚 Data: {duration:.2f}s")

    t_start = time.time()
    logs = []

    # === ЦЕНЗОР + SPECULATIVE WORKER ===
    # NORMAL mode: Censor і Worker запускаються паралельно (speculative execution).
    # Worker стартує одночасно з Censor — у 95% випадків Censor пропускає дію,
    # тому Worker вже закінчив роботу до того, як ми його очікуємо.
    # При блокуванні Censor'ом — Worker скасовується (asyncio.CancelledError;
    # thread продовжить виконання, але результат відкидається — прийнятна вартість 5% викликів).
    # COMBAT mode: залишається sequential (combat_state lock + складний cancel).
    if progress_callback:
        await progress_callback("🎲 Оцінюємо дію...")
    last_turn = history[-1]["content"] if history else ""

    # === FSM-ДИСПЕТЧЕР (Phase 7: COMBAT активний; NORMAL — основний pipeline) ===
    _game_mode = profile.get("mode", "NORMAL")
    mechanics_verdict: str | None = None
    mechanical_updates: dict = {}

    if _game_mode == "NORMAL":
        if _debug_active:
            # --- Debug sequential path: Censor then Worker in sequence ---
            # Runs sequentially so clear_thoughts() between stages accurately attributes
            # each stage's thoughts. ~2-3s slower than speculative; acceptable for admin-only.
            logger.info("[DEBUG_SEQ] Sequential Censor+Worker for debug trace")
            clear_thoughts()
            try:
                is_valid, refusal_reason = await validate_action(user_input, profile, debug_trace=_debug_trace)
            except Exception as _censor_exc:
                logger.warning(f"[CENSOR] failed, fail-open: {_censor_exc}")
                is_valid, refusal_reason = True, ""

            _tmarks["c1"] = _meta_mark()
            if _debug_trace is not None:
                _debug_trace["censor"]["parsed"] = {
                    "is_valid": is_valid,
                    "refusal_reason": refusal_reason,
                }
                _debug_trace["censor"]["thoughts"] = [
                    e.get("thought", "") for e in get_thoughts_log() if e.get("thought")
                ]

            if not is_valid:
                if _debug_trace is not None:
                    _debug_trace["narrator"]["final_text"] = "[BLOCKED BY CENSOR — pipeline stopped]"
                    _debug_trace["logs"] = [f"Censor refusal: {refusal_reason}"]
                    _trace_finalize(_debug_trace, chat_id, global_start, _tmarks)
                    user_sessions.setdefault(chat_id, {})["last_debug_trace"] = _debug_trace
                    logger.info(f"[DEBUG_MODE] Trace saved to session (path=censor) for chat_id={chat_id}")
                return refusal_reason + "\n\n📊 🛑 Дію заблоковано Цензором.", []

            if progress_callback:
                await progress_callback("⚔️ Кидаємо кубики...")

            clear_thoughts()  # reset before Worker — isolates Worker thoughts
            _combat_log_for_narrator = []
            try:
                mechanics_verdict, mechanical_updates = await resolve_normal_action(
                    user_input, profile, npc_reputation_context,
                    current_scene=curr_scene, npc_names=legal_npc_names,
                    last_turn_summary=last_turn, user_id=chat_id,
                    current_location=curr_loc,
                    debug_trace=_debug_trace,
                )
            except Exception as _worker_exc:
                logger.error(f"[WORKER] sequential debug failed: {_worker_exc}", exc_info=True)
                raise

            if _debug_trace is not None:
                _debug_trace["worker"]["thoughts"] = [
                    e.get("thought", "") for e in get_thoughts_log() if e.get("thought")
                ]
                _debug_trace["worker"]["parsed"] = mechanical_updates

        else:
            # --- Speculative: запускаємо Censor і Worker паралельно ---
            logger.info("[SPEC] Worker started speculatively with Censor")
            censor_task = asyncio.create_task(validate_action(user_input, profile))
            worker_task = asyncio.create_task(resolve_normal_action(
                user_input, profile, npc_reputation_context,
                current_scene=curr_scene, npc_names=legal_npc_names,
                last_turn_summary=last_turn, user_id=chat_id,
                current_location=curr_loc,
            ))

            # Крок 1: чекаємо результат Censor
            try:
                is_valid, refusal_reason = await censor_task
            except Exception as _censor_exc:
                logger.warning(f"[CENSOR] failed, fail-open: {_censor_exc}")
                is_valid, refusal_reason = True, ""

            if not is_valid:
                # Censor заблокував — скасовуємо Worker
                # NOTE: cancel() does NOT stop the underlying thread inside asyncio.to_thread.
                # The Worker LLM call will continue in background and may append to the
                # context-local thoughts log after the turn returns. clear_thoughts() at next
                # turn start overwrites stale data. Acceptable cost for ~5% of blocked actions.
                worker_task.cancel()
                try:
                    await worker_task
                except (asyncio.CancelledError, Exception):
                    pass
                return refusal_reason + "\n\n📊 🛑 Дію заблоковано Цензором.", []

            if progress_callback:
                await progress_callback("⚔️ Кидаємо кубики...")

            # Крок 2: Censor пройшов — забираємо результат Worker (він вже міг завершитись)
            _combat_log_for_narrator = []
            try:
                mechanics_verdict, mechanical_updates = await worker_task
            except Exception as _worker_exc:
                logger.error(f"[WORKER] speculative task failed: {_worker_exc}", exc_info=True)
                raise

    elif _game_mode == "COMBAT":
        # Sequential Censor для COMBAT (combat_state lock несумісний з cancel)
        if _debug_active:
            clear_thoughts()
        is_valid, refusal_reason = await validate_action(
            user_input, profile,
            debug_trace=_debug_trace if _debug_active else None,
        )
        if _debug_active:
            _tmarks["c1"] = _meta_mark()
        if _debug_active and _debug_trace is not None:
            _debug_trace["censor"]["parsed"] = {
                "is_valid": is_valid,
                "refusal_reason": refusal_reason,
            }
            _debug_trace["censor"]["thoughts"] = [
                e.get("thought", "") for e in get_thoughts_log() if e.get("thought")
            ]
        if not is_valid:
            if _debug_active and _debug_trace is not None:
                _trace_finalize(_debug_trace, chat_id, global_start, _tmarks)
            return refusal_reason + "\n\n📊 🛑 Дію заблоковано Цензором.", []

        if progress_callback:
            await progress_callback("⚔️ Кидаємо кубики...")

        from core.combat_state import get_combat_state, get_or_create_lock

        combat_lock = get_or_create_lock(chat_id)
        _combat_crashed = False
        async with combat_lock:
            combat_state = get_combat_state(chat_id)
            if not combat_state:
                # State lost (server restart?) — fallback to NORMAL without crash
                logger.warning(
                    f"[FSM] COMBAT mode but no CombatState for chat_id={chat_id} "
                    f"(server restart?) — falling back to NORMAL."
                )
                profile["mode"] = "NORMAL"
                _game_mode = "NORMAL"
            else:
                try:
                    combat_log, combat_updates, npc_actions = await execute_combat_round(
                        chat_id, user_input, profile
                    )

                    # Refresh combat_state reference (execute_combat_round mutates it)
                    combat_state = get_combat_state(chat_id)
                    combat_phase = combat_updates.get("combat_phase", "ONGOING")

                    # If combat ended — cleanup and merge updates
                    if combat_phase != "ONGOING" and combat_state is not None:
                        cleanup_updates = await cleanup_and_exit_combat(chat_id, profile)
                        # Merge: cleanup_updates overrides combat_updates for hp/xp/status
                        combat_updates.update(cleanup_updates)

                    elif combat_state is None:
                        # Combat ended inside execute_combat_round (e.g. player fled)
                        cleanup_updates = await cleanup_and_exit_combat(chat_id, profile)
                        combat_updates.update(cleanup_updates)

                    mechanics_verdict = (
                        f"COMBAT ROUND {combat_updates.get('combat_round', '?')}: "
                        f"{combat_log[:500]}"
                    )
                    mechanical_updates = combat_updates

                    # === COMBAT DICE SUMMARY (відображається внизу повідомлення як 📊 блок) ===
                    _combat_round_num = combat_updates.get("combat_round", "?")
                    _combat_dice_lines: list[str] = []

                    # Рядок дії гравця — перший не-заголовний рядок у combat_log
                    for _cl in combat_log.splitlines():
                        _cl_s = _cl.strip()
                        if _cl_s and not _cl_s.startswith("---"):
                            _combat_dice_lines.append(f"  {_cl_s}")
                            break

                    # Рядки атак NPC (attack_result.log_line присутній у кожному NPC результаті)
                    for _npc_res in npc_actions:
                        _ar = _npc_res.get("attack_result")
                        if _ar is not None and hasattr(_ar, "log_line") and _ar.log_line:
                            _combat_dice_lines.append(f"  {_ar.log_line}")

                    # HP summary рядок
                    _player_hp_now = combat_updates.get("player_hp_current")
                    _player_hp_max = profile.get("hp_max")
                    _hp_parts: list[str] = []
                    if _player_hp_now is not None and _player_hp_max:
                        _hp_parts.append(f"HP гравця: {_player_hp_now}/{_player_hp_max}")
                    if combat_state is not None:
                        for _npc_name, _npc_snap in combat_state.npcs.items():
                            _nhp = _npc_snap.get("hp_current", "?")
                            _nhp_max = _npc_snap.get("hp_max", "?")
                            _nstatus = _npc_snap.get("Status", "Active")
                            _dead_mark = " ☠️" if _nstatus == "Dead" else ""
                            _hp_parts.append(f"{_npc_name}: {_nhp}/{_nhp_max}{_dead_mark}")
                    if _hp_parts:
                        _combat_dice_lines.append("  " + " | ".join(_hp_parts))

                    if _combat_dice_lines:
                        logs.append(f"БІЙ Раунд {_combat_round_num}:\n" + "\n".join(_combat_dice_lines))

                    # Build combat_log list for Narrator
                    _combat_log_for_narrator = (
                        format_combat_log_for_narrator(combat_state, npc_actions)
                        if combat_state is not None
                        else [combat_log]
                    )

                except Exception as combat_exc:
                    # On crash — log but DO NOT re-raise to prevent deadlock
                    # (async with auto-releases the lock)
                    logger.error(
                        f"[FSM] COMBAT pipeline crashed for chat_id={chat_id}: {combat_exc}",
                        exc_info=True,
                    )
                    # Safe fallback: treat as NORMAL for this turn.
                    # Also clear stale CombatState so that combat_imminent on the
                    # next NORMAL turn does not find a lingering registry entry and
                    # skip re-init entirely (see reinit-guard in initiate_combat_from_normal).
                    _clear_combat_state_for_engine(chat_id)
                    # Do NOT call _cleanup_lock_for_engine here — the Lock is still
                    # held by the surrounding async with.  Removing it from the registry
                    # while held lets a concurrent task acquire a NEW Lock for the same
                    # chat_id, defeating mutual exclusion (CLAUDE.md §5.4).
                    # The flag is consumed immediately after async with exits (below).
                    _combat_crashed = True
                    profile["mode"] = "NORMAL"
                    _game_mode = "NORMAL"
                    _combat_log_for_narrator = []

        # Lock is now fully released by async with __aexit__.
        # Only NOW it is safe to remove it from the registry.
        if _combat_crashed:
            _cleanup_lock_for_engine(chat_id)

    # COMBAT→NORMAL fallback (state lost) або unknown mode — додатковий NORMAL resolve.
    # mechanics_verdict залишається None якщо COMBAT block скинув mode до NORMAL
    # через відсутній combat_state (Speculative Worker не запускався в COMBAT block).
    if _game_mode == "NORMAL" and mechanics_verdict is None:
        # Цей шлях досягається лише якщо COMBAT fallback скинув mode до NORMAL
        # (combat_state was None). Speculative Worker не запускався в COMBAT block,
        # тому потрібен звичайний sequential resolve.
        _combat_log_for_narrator = []
        mechanics_verdict, mechanical_updates = await resolve_normal_action(
            user_input, profile, npc_reputation_context,
            current_scene=curr_scene, npc_names=legal_npc_names, last_turn_summary=last_turn,
            user_id=chat_id, current_location=curr_loc
        )
    elif _game_mode not in ("NORMAL", "COMBAT"):
        # Unknown mode — force NORMAL
        logger.warning(f"[FSM] Unknown mode '{_game_mode}' — forcing NORMAL for chat_id={chat_id}")
        profile["mode"] = "NORMAL"
        _game_mode = "NORMAL"
        _combat_log_for_narrator = []
        mechanics_verdict, mechanical_updates = await resolve_normal_action(
            user_input, profile, npc_reputation_context,
            current_scene=curr_scene, npc_names=legal_npc_names, last_turn_summary=last_turn,
            user_id=chat_id, current_location=curr_loc
        )

    # === БЛОКУВАННЯ РЕПУТАЦІЄЮ: NPC відмовляє через кровну ворожнечу ===
    if mechanical_updates.get("reputation_block"):
        npc_name = mechanical_updates.get("reputation_target_npc", "NPC")
        logs.append(f"🚫 Репутація: {npc_name} — соціальна дія заблокована (кровна ворожнеча)")
        change_log = "\n\n📊 " + " | ".join(logs) if logs else ""
        # Передаємо GM для генерації художньої відмови
        mechanics_verdict_for_block = mechanics_verdict

    if mechanical_updates.get("action_type") == "training":
        training_result = await process_training_request(user_input, profile, current_scene=curr_scene)

        if training_result:
            if "error" in training_result:
                mechanics_verdict = (
                    f"SYSTEM: Player tried to train but it was impossible. "
                    f"Reason: {training_result['error']}. Describe their disappointment briefly."
                )
                logs.append(f"❌ {training_result['error']}")
            else:
                mechanics_verdict = training_result["story_prompt"]
                skill = training_result["skill"]
                xp_gained = training_result["xp_gained"]
                cost_time_days = training_result["cost_time_days"]
                cost_gold = int(training_result["cost_gold"])
                energy_cost = training_result.get("energy_cost", 40)
                hp_damage_dice = training_result.get("hp_damage_dice", "none")

                logs.append(
                    f"🏋️ Тренування: {skill} +{xp_gained} XP "
                    f"(Витрачено: {cost_time_days} дн., {cost_gold} 🪙, {energy_cost} ⚡)"
                )

                # Time advance
                mechanical_updates["minutes_passed"] = cost_time_days * _MINUTES_PER_DAY

                # Energy drain (clamped to 0, cap already enforced by apply_dnd_impacts)
                current_energy = safe_int(profile.get("Енергія", 1000))
                profile["Енергія"] = max(0, current_energy - energy_cost)

                # Gold payment (mentor only, already checked inside process_training_request)
                current_gold = safe_int(profile.get("Особисте Золото", profile.get("gold", 0)))
                if cost_gold > 0:
                    if "hp_current" in profile:
                        profile["gold"] = max(0, safe_int(profile.get("gold", 0)) - cost_gold)
                    else:
                        profile["Особисте Золото"] = max(0, current_gold - cost_gold)
                    logs.append(f"💰 Золото: -{cost_gold} (Оплата наставнику)")

                # HP damage (failure path: 1d4 overexertion trauma)
                if hp_damage_dice and hp_damage_dice != "none":
                    mechanical_updates["hp_damage_dice"] = hp_damage_dice
                    logs.append(f"🩸 Травма від виснаження ({hp_damage_dice} HP).")

                # Level-up: apply pending level mechanics (HP+, proficiency, features, auto-ASI)
                if training_result.get("leveled_up"):
                    logs.append(
                        f"РІВЕНЬ {training_result['level_before']} → {training_result['level_after']}!"
                    )
                    tr_class = profile.get("class", "")
                    if not tr_class or tr_class not in GOT_CLASSES:
                        tr_class = "Knight"
                    levelup_logs = apply_pending_levelups(
                        profile,
                        level_before=training_result["level_before"],
                        levels_gained=(
                            training_result["level_after"] - training_result["level_before"]
                        ),
                        class_name=tr_class,
                    )
                    logs.extend(levelup_logs)

        mechanical_updates["skill_used"] = "None"

    # === [SECURITY AUDIT] САНІТИЗАЦІЯ LLM INJECTION ===
    # Гарантуємо, що ШІ не згалюцинував зміну навичок в обхід правил
    is_valid_training = mechanical_updates.get("action_type") == "training"
    is_critical_success = mechanical_updates.get("outcome") == "CRITICAL SUCCESS"
    is_critical_failure = mechanical_updates.get("outcome") == "CRITICAL FAILURE"

    if not (is_valid_training or is_critical_success or is_critical_failure):
        if "skill_impact" in mechanical_updates:
            print(f"⚠️ [SECURITY] Видалено нелегальну галюцинацію навичок від ШІ: {mechanical_updates['skill_impact']}")
            mechanical_updates.pop("skill_impact", None)

    # apply_dnd_impacts handles D&D-specific tags (hp_damage_dice, conditions, xp, level_up)
    # and delegates remainder to legacy apply_system_impacts (time, gold, location, clocks).
    profile, impact_logs = apply_dnd_impacts(profile, mechanical_updates)
    logs.extend(impact_logs)
    impact_narrative_hints = _build_impact_hints(impact_logs)

    # Cache asi_pending in session so _check_and_trigger_asi can skip Sheets
    # reads on the 99% of turns where no level-up with ASI occurred.  The flag
    # is written even when False so the handler always has a fresh value.
    user_sessions.setdefault(chat_id, {})['asi_pending'] = bool(profile.get("asi_pending", False))

    # FSM transition: combat_imminent=True → initiate COMBAT mode for next turn
    # Trigger-guard: skip if a CombatState already exists in the registry — a lingering
    # in-memory state must not cause a second init (which would re-roll initiative and
    # replace any bystander filtering done on turn 1).
    if (
        mechanical_updates.get("combat_imminent")
        and profile.get("mode") != "COMBAT"
        and not _is_in_combat_for_engine(chat_id)
    ):
        # Friend/foe filter: ONLY the explicitly attacked NPC enters combat.
        # Every other NPC in the scene (allies, civilians, bystanders) stays out
        # until the player explicitly attacks them.  This prevents the "ally beats
        # up the player" regression where ALL scene NPCs were enrolled.
        #
        # Resolution order (2 tiers — no tier-3 to avoid enrolling allies):
        #   1. Worker's reputation_target_npc — most reliable (prompt-engineer ensures
        #      this is set to the attacked NPC when combat_imminent=True).
        #   2. Fallback: most-hostile NPC in npc_reputation_context (lowest score < 0).
        #   If neither resolves to a legal scene NPC — do not initiate combat.
        #   (A Worker name-mismatch or absence of hostile NPCs must NOT fall through
        #    to the first legal NPC — that could enroll an ally into combat.)
        _rep_ctx = npc_reputation_context or {}
        _target_npc_name: str | None = None

        # 1. Primary: Worker explicitly named the target
        _raw_target = mechanical_updates.get("reputation_target_npc", "")
        if _raw_target and _raw_target in legal_npc_names:
            _target_npc_name = _raw_target
        else:
            # 2. Fallback: most-hostile (lowest reputation score) among legal scene NPCs
            _hostile_candidates = [
                (n, _rep_ctx.get(n, 0))
                for n in legal_npc_names
                if _rep_ctx.get(n, 0) < 0  # only actually hostile NPCs
            ]
            if _hostile_candidates:
                _target_npc_name = min(_hostile_candidates, key=lambda x: x[1])[0]
            # No tier-3: if Worker named an invalid NPC and no hostile fallback exists,
            # _target_npc_name stays None → combat is not initiated.

        if _target_npc_name:
            _npc_combat_entry = {
                "Name": _target_npc_name,
                "Relation_Player": score_to_relation_text(
                    _rep_ctx.get(_target_npc_name, 0)
                ),
            }
            _statblock = get_canon_npc_statblock(_target_npc_name)
            if _statblock:
                _npc_combat_entry.update(_statblock)
                # Engine-resolved name takes precedence over any Name field in the statblock
                _npc_combat_entry["Name"] = _target_npc_name
                logger.info(
                    f"[FSM] enrolled canon NPC '{_target_npc_name}' with statblock "
                    f"(AC={_statblock.get('ac')}, HP={_statblock.get('hp_max')})."
                )
            else:
                logger.info(
                    f"[FSM] non-canon NPC '{_target_npc_name}' — will use tier fallback in initiate_combat."
                )
            _scene_npcs_for_combat = [_npc_combat_entry]
            try:
                _new_cs = await initiate_combat_from_normal(chat_id, profile, _scene_npcs_for_combat)
                # Initiative display: "Ім'я (roll), Ім'я (roll)"
                _init_parts = [
                    f"{_ref.name} ({_ref.init_roll})"
                    for _ref in _new_cs.initiative_order
                ]
                _init_str = ", ".join(_init_parts) if _init_parts else "?"
                logs.append(f"⚔️ БІЙ РОЗПОЧАТО! Ініціатива: {_init_str}")
                logger.info(
                    f"[FSM] combat_imminent=true → CombatState initiated for chat_id={chat_id}, "
                    f"enemy={_target_npc_name}"
                )
            except Exception as _ci_exc:
                logger.error(f"[FSM] initiate_combat_from_normal failed: {_ci_exc}", exc_info=True)
        else:
            logger.info(
                f"[FSM] combat_imminent=true but no valid enemy NPC resolved "
                f"(raw_target='{_raw_target}', legal={legal_npc_names}) — staying NORMAL for chat_id={chat_id}"
            )

    # === REFRESH: Перечитуємо loc/scene/region якщо apply_system_impacts їх змінила ===
    # Зберігаємо "ростер відправлення" ПЕРЕД rebuild (для dual roster промптів)
    departing_npc_context = npc_context_text
    departing_npc_names = list(legal_npc_names)
    arriving_npc_context = ""
    arriving_npc_names = []
    location_or_scene_changed = False

    _post_loc   = profile.get("Поточне місцезнаходження", curr_loc)
    _post_scene = profile.get("Поточна сцена", curr_scene)
    _post_region = profile.get("Регіон", get_region_for_location(_post_loc) or curr_region)
    if _post_scene != curr_scene or _post_loc != curr_loc:
        location_or_scene_changed = True
        curr_loc    = _post_loc
        curr_scene  = _post_scene
        curr_region = _post_region
        npc_context_text, legal_npc_names, npc_reputation_context = get_location_npcs(
            chat_id, curr_loc, curr_scene, current_region=curr_region
        )
        # "Ростер прибуття" = NPC нової локації/сцени
        arriving_npc_context = npc_context_text
        arriving_npc_names = list(legal_npc_names)
        # Перераховуємо dead/absent з оновленим Ростером
        _dnames_post = get_dead_npc_names(chat_id)
        _absent_raw_post = [n for n in prev_legal_npc_names if n not in set(legal_npc_names)]
        dead_npcs   = [n for n in _absent_raw_post if n in _dnames_post]
        absent_npcs = [n for n in _absent_raw_post if n not in _dnames_post]
        user_sessions.setdefault(chat_id, {})["prev_legal_npc_names"] = [
            n for n in legal_npc_names if n not in _dnames_post
        ]
        print(f"🔄 [ROSTER REBUILD] {old_scene}/{old_location} → {curr_scene}/{curr_loc} | Новий Ростер: {legal_npc_names}")
        print(f"🔄 [DUAL ROSTER] Departing: {len(departing_npc_names)} NPC {departing_npc_names} | Arriving: {len(arriving_npc_names)} NPC {arriving_npc_names}")
    else:
        # Нема переміщення — departing не потрібний
        departing_npc_context = ""
        departing_npc_names = []

    # === BURST CHECK: годинник щойно досяг максимуму ===
    burst_clock = profile.pop("_burst_this_turn", None)
    is_exhaustion_collapse = (
        mechanical_updates.get("energy_impact") == "sleep"
        and mechanical_updates.get("outcome") == "CRITICAL FAILURE"
        and mechanical_updates.get("minutes_passed") == 480
    )
    burst_injection = ""
    if burst_clock == "Scene_Tension" and not is_exhaustion_collapse:
        burst_injection = (
            "\n[SYSTEM INTERRUPT — VIOLENCE ERUPTS: "
            "Scene_Tension досягла максимуму. АБСОЛЮТНИЙ OVERRIDE. "
            "NPC негайно починає атаку, спалахує бійка або прибуває варта — "
            "щось насильницьке та неминуче відбувається ПРЯМО ЗАРАЗ. "
            "Дія гравця перервана цією подією. Гравець НЕ може уникнути її через розмову цього ходу.]\n"
        )
        logs.append("💥 Напруга вибухнула! Сцена переходить до відкритого насильства.")

    duration = time.time() - t_start
    timing_details.append(f"🤖 Mechanics: {duration:.2f}s")

    t_start = time.time()

    current_time_str = profile.get("Ігровий час", "")
    day_match = re.search(r'День (\d+)', current_time_str)
    current_day = int(day_match.group(1)) if day_match else 1

    world_events = {
        5: "Починається сильна хуртовина. Пересування стає майже неможливим.",
        14: "До міста прибуває величезний королівський кортеж.",
        30: "Починається війна. Оголошено загальну мобілізацію."
    }

    event_injection = ""
    if current_day in world_events:
        region_climate = REGION_CLIMATE_MAP.get(curr_region, "temperate")
        plausible_climates = EVENT_PLAUSIBILITY.get(current_day, ["*"])
        if "*" in plausible_climates or region_climate in plausible_climates:
            event_injection = f"\n[SYSTEM INTERRUPT: СЬОГОДНІ ВАЖЛИВА ПОДІЯ: {world_events[current_day]}. Ти ПОВИНЕН органічно вплести цю подію в поточну сцену.]\n"
        else:
            logger.info(
                "[FSM] event day=%d skipped: region=%r climate=%s not in plausible=%s",
                current_day, curr_region, region_climate, plausible_climates,
            )

    # Якісна мітка напруги сцени (без технічного терміну)
    _tension_raw = profile.get("Годинники", {}).get("Scene_Tension", "0/4")
    _tension_val = str(_tension_raw).split("/")[0] if isinstance(_tension_raw, str) else str(_tension_raw)
    _tension_labels = {"0": "спокійно", "1": "легка напруга", "2": "напружено", "3": "небезпечно — на межі конфлікту", "4": "на межі вибуху — насильство неминуче"}
    _tension_label = _tension_labels.get(_tension_val, "невідомо")
    _action_slots_map = {
        "0": ["ДОСЛІДИТИ", "ПОГОВОРИТИ", "ПІДГОТУВАТИСЬ", "РУШИТИ ДАЛІ"],
        "1": ["РОЗПИТАТИ", "ПОГРОЖУВАТИ", "СЛІДКУВАТИ", "ЗАЧЕКАТИ"],
        "2": ["ТИСНУТИ", "МАНІПУЛЮВАТИ", "ОТОЧИТИ", "ВІДСТУПИТИ"],
        "3": ["АТАКУВАТИ", "ТОРГУВАТИСЬ", "ВТЕКТИ", "ПЕРЕХИТРИТИ"],
        "4": ["АТАКУВАТИ", "ЗАХИСТИТИСЬ", "ВТЕКТИ", "ДИКИЙ GAMBLE"],
    }
    _action_slots = _action_slots_map.get(_tension_val, _action_slots_map["2"])
    _valid_locs_str = ", ".join(f'"{loc}"' for loc in VALID_LOCATIONS_ORDERED)
    _valid_regions_str = ", ".join(f'"{r}"' for r in VALID_REGIONS_ORDERED)
    _region_locs = get_locations_for_region(curr_region)
    _region_locs_str = ", ".join(f'"{loc}"' for loc in _region_locs) if _region_locs else _valid_locs_str
    _curr_loc_desc = LOCATION_DESCRIPTIONS.get(curr_loc, "")
    _loc_hint = f"\n    ОПИС ПОТОЧНОЇ ЛОКАЦІЇ: {_curr_loc_desc}" if _curr_loc_desc else ""

    # ============ GM_LOGIC PROMPT (JSON стану світу, без story) ============
    from config import PUPPET_USERS, EROTIC_USERS
    _puppet_mode = chat_id in PUPPET_USERS
    _erotic_mode = chat_id in EROTIC_USERS
    _scenes_block_str = format_scenes_for_prompt(curr_loc)
    _gm_mode = "COMBAT" if profile.get("mode") == "COMBAT" else "NORMAL"
    # For COMBAT mode, override action slots with combat buttons
    if _gm_mode == "COMBAT":
        _action_slots = ["АТАКУВАТИ", "ЗАХИСТИТИСЬ", "ВТЕКТИ", "СПЕЦДІЯ"]

    # B1 — Build npc_hp_snapshot for GM_Logic in COMBAT so the prompt can
    # show authoritative HP values and prevent GM hallucinating HP drift.
    # combat_state is the sole HP authority during combat (Package 3 design).
    _npc_hp_snapshot: dict | None = None
    if _gm_mode == "COMBAT":
        _cs_for_snapshot = _get_combat_state_for_engine(chat_id)
        if _cs_for_snapshot is not None:
            _npc_hp_snapshot = {}
            for _snap_name, _snap_data in _cs_for_snapshot.npcs.items():
                _npc_hp_snapshot[_snap_name] = {
                    "hp_current": _snap_data.get("hp_current", 0),
                    "hp_max":     _snap_data.get("hp_max",     0),
                    "ac":         _snap_data.get("ac",         10),
                    "conditions": list(_snap_data.get("conditions", [])),
                }
            logger.debug(
                f"[FSM] npc_hp_snapshot built for GM_Logic: "
                f"{list(_npc_hp_snapshot.keys())} (chat_id={chat_id})"
            )

    gm_logic_static, gm_logic_prompt = build_gm_logic_parts(
        hero_name=profile.get("Ім'я", "Невідомий"),
        hero_house=profile.get("Дім", "Невідомий"),
        profile_json=profile_json,
        context_knowledge=context_knowledge,
        event_injection=event_injection,
        burst_injection=burst_injection,
        current_time_str=current_time_str,
        curr_region=curr_region,
        curr_loc=curr_loc,
        is_traveling=(curr_loc == TRAVEL_LOCATION),
        loc_hint=_loc_hint,
        curr_scene=curr_scene,
        valid_locs_str=_valid_locs_str,
        valid_regions_str=_valid_regions_str,
        region_locs_str=_region_locs_str,
        npc_context_text=npc_context_text,
        tension_label=_tension_label,
        mechanics_verdict=mechanics_verdict,
        impact_narrative_hints=impact_narrative_hints,
        history_text=history_text,
        user_input=user_input,
        action_slots=_action_slots,
        puppet_mode=_puppet_mode,
        absent_npcs=absent_npcs,
        dead_npcs=dead_npcs,
        scenes_block_str=_scenes_block_str,
        departing_roster_text=departing_npc_context if location_or_scene_changed else "",
        arriving_roster_text=arriving_npc_context if location_or_scene_changed else "",
        mode=_gm_mode,
        npc_hp_snapshot=_npc_hp_snapshot,
    )


    try:
        # ============ КРОК 1: GM_Logic (легка модель → JSON) ============
        if progress_callback:
            await progress_callback("🌍 Оновлюємо стан світу...")
        t_gm_logic = time.time()
        if _debug_trace is not None:
            _tmarks["w1"] = _meta_mark()
            try:
                _debug_trace["worker"]["roster"] = _roster_diag_lines(
                    chat_id, legal_npc_names, {"user_input": user_input})
            except Exception as _rd_exc:
                logger.warning(f"[DEBUG_MODE] worker roster diag failed: {type(_rd_exc).__name__}")

        _gm_logic_cfg = build_strict_config(model_gm_logic, schema=GM_LOGIC_SCHEMA, system_instruction=gm_logic_static)

        if _debug_active:
            clear_thoughts()  # isolate GM_Logic thoughts from previous stages
            if _debug_trace is not None:
                _debug_trace["gm_logic"]["prompt"] = _static_tag(gm_logic_static) + "\n\n" + gm_logic_prompt

        def _sync_gen_gm_logic():
            return model_gm_logic.generate_content(
                gm_logic_prompt, config=_gm_logic_cfg,
                **_ml("gm_logic", model_gm_logic.generate_content))

        # GM_Logic call with None-text guard + one retry (the model may return
        # MALFORMED/empty .text=None, which is not an exception — guard against crash).
        gm_logic_raw = ""
        for _gm_attempt in range(2):  # 1 initial + 1 retry
            try:
                gm_logic_response = await asyncio.to_thread(_sync_gen_gm_logic)
            except Exception as _gm_exc:
                logger.warning(f"[GM_Logic] call failed (attempt {_gm_attempt+1}/2): {_gm_exc}")
                continue
            _raw = gm_logic_response.text if gm_logic_response else None
            if _raw:
                gm_logic_raw = _raw.strip()
                if gm_logic_raw:
                    break  # got usable text
            logger.warning(f"[GM_Logic] empty/None text (attempt {_gm_attempt+1}/2) — retrying")

        ai_data = clean_and_parse_json(gm_logic_raw) if gm_logic_raw else None

        # GM_Logic fallback якщо JSON не парситься або текст порожній (MALFORMED after retries)
        if not ai_data:
            logger.warning("[GM_Logic] using minimal fallback (empty/unparseable after retries)")
            ai_data = {"director_notes": ["Дія мала неоднозначний результат."],
                       "npc_updates": [], "suggested_actions": []}

        if _debug_active and _debug_trace is not None:
            _debug_trace["gm_logic"]["thoughts"] = [
                e.get("thought", "") for e in get_thoughts_log() if e.get("thought")
            ]
            _debug_trace["gm_logic"]["raw"] = gm_logic_raw
            _debug_trace["gm_logic"]["parsed"] = ai_data
            _tmarks["g1"] = _meta_mark()
            try:
                _dn_raw = ai_data.get("director_notes")
                _dn_txt = " ".join(str(x) for x in _dn_raw) if isinstance(_dn_raw, (list, tuple)) else str(_dn_raw or "")
                _debug_trace["gm_logic"]["roster"] = _roster_diag_lines(
                    chat_id, legal_npc_names, {"director_notes": _dn_txt, "user_input": user_input})
            except Exception as _rd_exc:
                logger.warning(f"[DEBUG_MODE] gm roster diag failed: {type(_rd_exc).__name__}")

        duration_gm_logic = time.time() - t_gm_logic
        timing_details.append(f"🧠 GM_Logic: {duration_gm_logic:.2f}s")

        # Зберігаємо ai_data в сесію для QA-валідації
        user_sessions[chat_id]['last_ai_data'] = ai_data

        # B3 — Unconditional guard: strip hp_current if None/non-int (both modes).
        # GM_Logic sometimes returns "hp_current": null despite prompt rules.
        # null is never a valid HP value and must never reach the DB writer.
        _gm_npc_updates = ai_data.get("npc_updates") if isinstance(ai_data.get("npc_updates"), list) else []
        for _u in _gm_npc_updates:
            if not isinstance(_u, dict):
                continue
            _hp_val = _u.get("hp_current")
            if _hp_val is None or not isinstance(_hp_val, int):
                _u.pop("hp_current", None)

        # B2 — COMBAT: strip hp_current AND conditions from GM_Logic npc_updates.
        # combat_state is the sole HP/conditions authority during combat.
        # Status (Dead/Fled/Unconscious) is NOT stripped — legitimate from GM_Logic.
        # On combat exit, cleanup_and_exit_combat syncs NPC HP/conditions from
        # combat_state to the DB, so stripping here causes no data loss.
        if _gm_mode == "COMBAT":
            for _u in _gm_npc_updates:
                if not isinstance(_u, dict):
                    continue
                _u.pop("hp_current", None)
                _u.pop("conditions", None)
            logger.debug(
                f"[FSM] stripped hp_current/conditions from {len(_gm_npc_updates)} "
                f"GM npc_updates (COMBAT — combat_state is authoritative) chat_id={chat_id}"
            )

        # Витягуємо suggested_actions з GM_Logic
        raw_actions = ai_data.get("suggested_actions", [])
        button_texts = []
        intents_map = {}
        for item in raw_actions:
            if isinstance(item, dict):
                btn = str(item.get("button", "...")).strip()[:40]
                intent = str(item.get("intent", btn)).strip()
            else:
                btn = str(item).strip()[:40]
                intent = btn
            button_texts.append(btn)
            intents_map[btn] = intent
        if len(button_texts) < 4:
            button_texts = (button_texts + ["..."] * 4)[:4]
        user_sessions.setdefault(chat_id, {})["action_intents"] = intents_map
        suggested_actions = button_texts

        # ============ КРОК 2: Narrator (важка модель → художній текст) ============
        if progress_callback:
            # Показуємо результати кубиків одразу, поки Narrator думає
            preview = "✍️ Пишемо історію...\n\n"
            if logs:
                preview += "📊 " + " | ".join(logs)
            await progress_callback(preview)
        t_narrator = time.time()
        director_notes = ai_data.get("director_notes", [])
        if not director_notes:
            director_notes = ["Щось сталося."]

        # Fix 1: Санітайзер director_notes — видаляє згадки мертвих NPC до Narrator
        if dead_npcs:
            _dead_lower = {n.lower(): n for n in dead_npcs}
            _correction_added = set()
            _clean_notes = []
            for note in director_notes:
                note_lower = note.lower()
                _found_dead = next((orig for low, orig in _dead_lower.items() if low in note_lower), None)
                if _found_dead:
                    if _found_dead not in _correction_added:
                        _clean_notes.append(f"КОРЕКЦІЯ: {_found_dead} мертвий — відсутній у сцені. Не згадуй його дій.")
                        _correction_added.add(_found_dead)
                        print(f"🧹 [NOTES SANITIZED] Видалено згадку мертвого '{_found_dead}' з director_notes.")
                else:
                    _clean_notes.append(note)
            director_notes = _clean_notes

        _recent_hist_text = "\n".join(
            f"{m['role']}: {m['content']}" for m in history[-10:]
        ) if history else ""

        # Fix 3: Анотація history — попереджає Narrator про мертвих NPC у свіжій пам'яті
        if dead_npcs:
            _dead_list_str = ", ".join(dead_npcs)
            _recent_hist_text = (_recent_hist_text or "") + (
                f"\n\n[СИСТЕМНА НОТАТКА ДЛЯ КОНТЕКСТУ: Такі NPC МЕРТВІ і НЕ існують у поточній реальності: "
                f"{_dead_list_str}. Все що ти читаєш про них в попередніх ходах — минуле.]"
            )
        # Формуємо scene_continuity_block на основі вже обчисленого location_or_scene_changed
        scene_continuity_block = (
            SCENE_CONTINUITY_NEW if location_or_scene_changed else SCENE_CONTINUITY_CONTINUING
        )

        narrator_static, narrator_prompt = _build_narrator_prompt(
            user_input=user_input,
            director_notes=director_notes,
            npc_context_text=npc_context_text,
            player_name=profile.get("Ім'я", "Невідомий"),
            player_house=profile.get("Дім", "Невідомий"),
            current_scene=curr_scene,
            current_location=curr_loc,
            impact_narrative_hints=impact_narrative_hints,
            puppet_mode=_puppet_mode,
            recent_history_text=_recent_hist_text,
            erotic_mode=_erotic_mode,
            active_roster=legal_npc_names,
            dead_npcs=dead_npcs,
            departing_roster_text=departing_npc_context if location_or_scene_changed else "",
            arriving_roster_text=arriving_npc_context if location_or_scene_changed else "",
            scene_continuity_block=scene_continuity_block,
            combat_log=_combat_log_for_narrator if _combat_log_for_narrator else None,
        )

        if _debug_active:
            clear_thoughts()  # isolate Narrator thoughts from GM_Logic
            if _debug_trace is not None:
                _debug_trace["narrator"]["prompt"] = _static_tag(narrator_static) + "\n\n" + narrator_prompt

        # Один і той самий cfg (system_instruction=static) для ВСІХ narrator-шляхів (stream/retry/attempt 3).
        _narrator_cfg = model_narrator.config_with(system_instruction=narrator_static)
        _narrator_cfg_t05 = model_narrator.config_with(system_instruction=narrator_static, temperature=0.5)

        _narrator_failed = False  # прапор: всі три спроби провалились
        _narr_diag = {"content_blocked": False}  # блок контент-фільтра Google (лише для вибору last-resort тексту)
        _narr_diag_var.set(_narr_diag)
        _ab_results = []   # Narrator A/B: results of both chains (blocking mode only)
        _ab_mode = None    # None | "pair" | "single" | "none"
        if narrator_queue is not None:
            # === STREAMING MODE: стрімимо narrator-текст чанками через queue ===
            _loop = asyncio.get_event_loop()

            def _sync_stream_narrator():
                chunks = []
                thought_parts = []
                print("🔵 [STREAM] Narrator streaming started...")
                try:
                    for chunk in model_narrator.generate_content_stream(
                            narrator_prompt, config=_narrator_cfg,
                            **_ml("narrator.stream.main", model_narrator.generate_content_stream)):
                        try:
                            for part in chunk.candidates[0].content.parts:
                                if getattr(part, "thought", False):
                                    thought_parts.append(part.text or "")
                                elif part.text:
                                    chunks.append(part.text)
                                    _loop.call_soon_threadsafe(narrator_queue.put_nowait, part.text)
                                    print(f"🔵 [STREAM] Chunk #{len(chunks)}: {len(part.text)} chars")
                        except (AttributeError, IndexError):
                            text = chunk.text if chunk.text else ""
                            if text:
                                chunks.append(text)
                                _loop.call_soon_threadsafe(narrator_queue.put_nowait, text)
                                print(f"🔵 [STREAM] Chunk #{len(chunks)}: {len(text)} chars")
                        if _is_content_block(chunk):
                            _narr_diag["content_blocked"] = True
                        try:
                            fr = chunk.candidates[0].finish_reason if chunk.candidates else None
                            if fr and str(fr) not in ("FinishReason.STOP", "STOP", "1", "None"):
                                print(f"🔵 [STREAM] finish_reason={fr}")
                                # Fail fast: MALFORMED/OTHER/будь-який блок-маркер (SAFETY, PROHIBITED_CONTENT,
                                # BLOCKLIST, SPII) → no point waiting for more chunks
                                if _is_abort_finish(fr):
                                    print(f"🔴 [STREAM] aborted early: finish_reason={fr}, chunks={len(chunks)}")
                                    break
                        except Exception:
                            pass
                except Exception as e:
                    print(f"🔴 [STREAM] Error during streaming: {e}")
                    _narr_diag_note_exc(_narr_diag, e, "stream.main")
                    return ""  # порожній → retry спрацює
                record_thought(MODEL_NARRATOR_NAME, "\n".join(thought_parts))
                print(f"🔵 [STREAM] Done. Total chunks: {len(chunks)}")
                return "".join(chunks)

            story = await _safe_to_thread(_sync_stream_narrator)
            story = story.strip() if story else ""
            # None надсилається ПІСЛЯ перевірки на обрізання — див. нижче
        else:
            # === BLOCKING MODE: chain (hedged attempt 1 -> attempt 2 -> attempt 3) ===
            if NARRATOR_AB_ENABLED:
                _t_ab = time.time()
                _ab_raw = await asyncio.gather(
                    _run_narrator_chain(model_narrator, narrator_prompt, "gemma", narrator_static),
                    _run_narrator_chain(model_narrator_alt, narrator_prompt, "flash_lite", narrator_static),
                    return_exceptions=True,
                )
                _ab_results = []
                for _k, _r in zip(("gemma", "flash_lite"), _ab_raw):
                    if isinstance(_r, asyncio.CancelledError):
                        raise _r
                    if isinstance(_r, BaseException):
                        logger.warning(f"[NARRATOR_AB] chain {_k} raised {type(_r).__name__}: {str(_r)[:160]}")
                        _r = NarrationResult(
                            model_key=_k, text=None,
                            total_ms=int((time.time() - _t_ab) * 1000),
                            attempt_ms=[], final_attempt="fallback", used_fallback=True,
                            error=f"{type(_r).__name__}: {str(_r)[:200]}",
                        )
                    _ab_results.append(_r)
                _ab_ok = [_r for _r in _ab_results if _r.ok]
                _ab_mode = "pair" if len(_ab_ok) == 2 else ("single" if len(_ab_ok) == 1 else "none")
                if _ab_mode == "single":
                    story = _ab_ok[0].text
                elif _ab_mode == "none":
                    logger.warning(
                        f"[NARRATOR_LAST_RESORT] A/B: both narrators failed. "
                        f"updates_keys={list(mechanical_updates.keys())} — building deterministic narrative."
                    )
                    story = _build_deterministic_narrative(
                        mechanical_updates, profile, old_location, old_scene,
                        director_notes=director_notes, content_blocked=_narr_diag["content_blocked"],
                    )
                    _narrator_failed = True
                else:
                    story = ""  # texts are held in _ab_results until the vote
            else:
                _chain_res = await _run_narrator_chain(model_narrator, narrator_prompt, "gemma", narrator_static)
                if _chain_res.used_fallback:
                    logger.warning(
                        f"[NARRATOR_LAST_RESORT] All 3 attempts failed (blocking path). "
                        f"updates_keys={list(mechanical_updates.keys())} "
                        f"— building deterministic narrative."
                    )
                    story = _build_deterministic_narrative(
                        mechanical_updates, profile, old_location, old_scene,
                        director_notes=director_notes, content_blocked=_narr_diag["content_blocked"],
                    )
                    _narrator_failed = True
                else:
                    story = _chain_res.text

        # === Шар 1: обрізаний, але змістовний текст — приймаємо без retry ===
        # Визначаємо "справжній" failure: порожньо, placeholder або надто короткий.
        # Трункований текст (≥100 символів, не placeholder) — НЕ є failure: додаємо "…" і рухаємось далі.
        # Blocking mode: the chain (_run_narrator_chain) already did these checks.
        _story_truncated = narrator_queue is not None and story and len(story) > 20 and not story.rstrip().endswith(('.', '!', '?', '…', '"', '*'))
        _is_placeholder = narrator_queue is not None and story and any(marker in story.lower() for marker in _PLACEHOLDER_MARKERS)
        _is_hard_fail = narrator_queue is not None and (not story or _is_placeholder or len(story) < 20)

        if _story_truncated and not _is_hard_fail:
            if len(story) >= 100:
                # Змістовний текст, просто обрізаний — використовуємо як є
                logger.info(
                    f"[NARRATOR_FAIL] Attempt 1 truncated but substantial "
                    f"({len(story)} chars) — accepted with ellipsis. tail={story[-50:]!r}"
                )
                story = story.rstrip() + "…"
                _story_truncated = False  # більше не вважаємо проблемою
            else:
                # Коротший обрізаний текст — все одно retry
                _is_hard_fail = True

        if _is_hard_fail:
            # --- Логування Attempt 1 failure ---
            _fail_reason = (
                "empty" if not story
                else "placeholder" if _is_placeholder
                else f"too_short({len(story)})" if len(story) < 20
                else f"truncated_short({len(story)})"
            )
            logger.info(
                f"[NARRATOR_FAIL] Attempt 1 failed. reason={_fail_reason} "
                f"prompt_len={len(narrator_prompt)} "
                f"story_preview={story[:200]!r}"
            )

            # === Шар 2 (Attempt 2): retry через streaming або blocking ===
            if narrator_queue is not None:
                # Streaming retry: consumer ще живий (None не надсилали), тому пушимо нові чанки
                def _sync_stream_retry():
                    chunks = []
                    thought_parts = []
                    _t0 = time.time()
                    try:
                        for chunk in model_narrator.generate_content_stream(
                                narrator_prompt, config=_narrator_cfg,
                                **_ml("narrator.stream.retry", model_narrator.generate_content_stream)):
                            try:
                                for part in chunk.candidates[0].content.parts:
                                    if getattr(part, "thought", False):
                                        thought_parts.append(part.text or "")
                                    elif part.text:
                                        chunks.append(part.text)
                                        _loop.call_soon_threadsafe(narrator_queue.put_nowait, part.text)
                            except (AttributeError, IndexError):
                                text = chunk.text if chunk.text else ""
                                if text:
                                    chunks.append(text)
                                    _loop.call_soon_threadsafe(narrator_queue.put_nowait, text)
                            # finish_reason при retry — abort early on MALFORMED_RESPONSE
                            if _is_content_block(chunk):
                                _narr_diag["content_blocked"] = True
                            try:
                                fr = chunk.candidates[0].finish_reason if chunk.candidates else None
                                if fr and str(fr) not in ("FinishReason.STOP", "STOP", "1", "None"):
                                    logger.info(f"[NARRATOR_FAIL] Retry finish_reason={fr}")
                                    # Fail fast: MALFORMED/OTHER/будь-який блок-маркер → no point waiting for more chunks
                                    if _is_abort_finish(fr):
                                        logger.info(f"[NARRATOR_FAIL] Retry aborted early: finish_reason={fr}, chunks={len(chunks)}")
                                        break
                            except Exception:
                                pass
                    except Exception as e:
                        logger.info(f"[NARRATOR_FAIL] Retry stream error: {e}")
                        _narr_diag_note_exc(_narr_diag, e, "stream.retry")
                    record_thought(MODEL_NARRATOR_NAME, "\n".join(thought_parts))
                    _elapsed = time.time() - _t0
                    _result = "".join(chunks)
                    logger.info(
                        f"[NARRATOR_FAIL] Retry done. chunks={len(chunks)} "
                        f"total_chars={len(_result)} elapsed={_elapsed:.2f}s "
                        f"preview={_result[:200]!r}"
                    )
                    return _result

                story = await _safe_to_thread(_sync_stream_retry)
                story = story.strip() if story else ""

                if not story or len(story) < 20:
                    logger.info(
                        f"[NARRATOR_FAIL] Attempts 1+2 failed (streaming). "
                        f"prompt_len={len(narrator_prompt)} story={story!r} "
                        f"— proceeding to Attempt 3 (blocking fallback)"
                    )
                    # === Шар 2→3: third attempt blocking з нижчою температурою ===
                    def _sync_gen_narrator_third():
                        _t0 = time.time()
                        # Fail fast in degraded path: max_retries=2 instead of default 6
                        # (avoid 140s+ user-wait when whole pipeline is degraded).
                        try:
                            resp = model_narrator.generate_content(
                                narrator_prompt, max_retries=2, config=_narrator_cfg_t05,
                                **_ml("narrator.attempt3.degraded", model_narrator.generate_content))
                        except Exception as _d_exc:
                            _narr_diag_note_exc(_narr_diag, _d_exc, "attempt3.degraded")
                            resp = model_narrator.generate_content(
                                narrator_prompt, max_retries=2, config=_narrator_cfg,
                                **_ml("narrator.attempt3.degraded.retry", model_narrator.generate_content))
                        _elapsed = time.time() - _t0
                        _text = (resp.text or "") if resp else ""
                        if _is_content_block(resp):
                            _narr_diag["content_blocked"] = True
                        logger.info(
                            f"[NARRATOR_FAIL] Attempt 3 (blocking low-temp) done. "
                            f"elapsed={_elapsed:.2f}s text_len={len(_text)} "
                            f"preview={_text[:200]!r}"
                        )
                        return resp

                    # Hard timeout 90s — якщо blocking fallback не встиг → deterministic fallback
                    try:
                        third_resp = await asyncio.wait_for(
                            _safe_to_thread(_sync_gen_narrator_third),
                            timeout=90.0,
                        )
                    except asyncio.TimeoutError:
                        logger.warning("[NARRATOR_FAIL] Attempt 3 timed out after 90s — falling through to deterministic fallback")
                        third_resp = None
                    except Exception as _e3:
                        _narr_diag_note_exc(_narr_diag, _e3, "attempt3.degraded")
                        logger.warning(f"[NARRATOR_FAIL] Attempt 3 exception: {_e3} — falling through to deterministic fallback")
                        third_resp = None
                    third_text = third_resp.text.strip() if third_resp and third_resp.text else ""
                    if third_text and len(third_text) >= 50:
                        story = third_text
                        # WARN #2: Шар 3 blocking — чанки не стрімились, пушимо повний текст
                        try:
                            narrator_queue.put_nowait(story)
                        except asyncio.QueueFull:
                            pass
                    else:
                        logger.warning(
                            f"[NARRATOR_LAST_RESORT] All 3 attempts failed (streaming path). "
                            f"updates_keys={list(mechanical_updates.keys())} "
                            f"— building deterministic narrative."
                        )
                        story = _build_deterministic_narrative(
                            mechanical_updates, profile, old_location, old_scene,
                            director_notes=director_notes, content_blocked=_narr_diag["content_blocked"],
                        )
                        _narrator_failed = True
                        # WARN #2: last-resort — пушимо детерміністичний текст
                        try:
                            narrator_queue.put_nowait(story)
                        except asyncio.QueueFull:
                            pass

                narrator_queue.put_nowait(None)  # сигнал завершення після retry
        elif narrator_queue is not None:
            # Перший стрім вдався — надсилаємо сигнал завершення
            narrator_queue.put_nowait(None)

        duration_narrator = time.time() - t_narrator
        timing_details.append(f"✍️ Narrator: {duration_narrator:.2f}s")
        if _ab_results:
            timing_details.append(
                "🆚 Narrator A/B: " + " | ".join(
                    f"{_r.model_key} {_r.total_ms}ms att={_r.attempt_ms} final={_r.final_attempt}"
                    f"{' ERR' if _r.error else ''}"
                    for _r in _ab_results
                ) + f" [{_ab_mode}]"
            )

        # === DEBUG TRACE: stage-aware capture (thoughts already collected per-stage above) ===
        # Narrator thoughts + final text captured here; censor/worker/gm_logic already set.
        if _debug_active and _debug_trace is not None:
            _debug_trace["narrator"]["thoughts"] = [
                e.get("thought", "") for e in get_thoughts_log() if e.get("thought")
            ]
            if _ab_mode == "pair":
                _debug_trace["narrator"]["final_text"] = "\n\n=====\n\n".join(
                    f"[A/B {_r.model_key}]\n{_r.text}" for _r in _ab_results
                )
            else:
                _debug_trace["narrator"]["final_text"] = story
            _debug_trace["logs"] = list(logs)
            _tmarks["n1"] = _meta_mark()
            _has_notes = bool(_fallback_story_from_notes(director_notes))
            if _ab_mode == "pair":
                _fb_branch = "model_text (A/B pair: обидва варіанти від моделей)"
            elif not _narrator_failed:
                _fb_branch = "model_text"
            elif _narr_diag["content_blocked"]:
                _fb_branch = "blocked_notes" if _has_notes else "empty (blocked, немає придатних notes)"
            else:
                _fb_branch = "notes" if _has_notes else "empty (механічний/загальний текст без notes)"
            _debug_trace["_narr_summary"] = {
                "content_blocked": _narr_diag["content_blocked"],
                "branch": _fb_branch,
                "elapsed_s": round(duration_narrator, 2),
                "errors": list(_narr_diag.get("errors", [])),
            }
            _trace_finalize(_debug_trace, chat_id, global_start, _tmarks)
            # Store in session for handlers.py pickup
            user_sessions.setdefault(chat_id, {})["last_debug_trace"] = _debug_trace
            logger.info(f"[DEBUG_MODE] Trace saved to session (path=success) for chat_id={chat_id}")

        t_start = time.time()
        npc_changes = ai_data.get("npc_updates", []) + mechanical_updates.get("npc_updates", [])
        companion_npcs = ai_data.get("companion_npcs", [])
        if companion_npcs:
            print(f"🤝 [COMPANIONS] NPC що рухаються з гравцем: {companion_npcs}")
        if not npc_changes and npc_context_text:
            print(f"[NPC_WARN] npc_updates=[] при наявних NPC у сцені.")

        # Death check: support both legacy "Здоров'я" (0-100) and D&D "hp_current" fields
        _is_dead = (
            safe_int(profile.get("Здоров'я", 100)) <= 0
            or ("hp_current" in profile and safe_int(profile.get("hp_current", 1)) <= 0)
        )
        _DEATH_SUFFIX = "\n\n💀 *ВАШ ДОЗОР ЗАКІНЧИВСЯ. Ви загинули.*"
        if _is_dead:
            story += _DEATH_SUFFIX
            user_sessions.setdefault(chat_id, {})["action_intents"] = {}
            suggested_actions = ["🔄 Почати заново"]

        if "skill_used" in mechanical_updates and mechanical_updates["skill_used"] not in ["None", "Немає", "none", ""]:
            skill = mechanical_updates["skill_used"]
            ability_used = mechanical_updates.get("ability_used", "")
            roll_str = mechanical_updates.get("dice_roll", "0")
            skill_val = mechanical_updates.get("skill_val", 0)
            total_score = mechanical_updates.get("total_score", 0)
            outcome = mechanical_updates.get("outcome", "UNKNOWN")

            # D&D: advantage/disadvantage замінює старий circumstance
            adv_reason = mechanical_updates.get("advantage_reason", "")
            dis_reason = mechanical_updates.get("disadvantage_reason", "")
            circumstance = mechanical_updates.get("circumstance", "")  # legacy fallback
            if adv_reason and not dis_reason:
                circ_label = " (Перевага)"
            elif dis_reason and not adv_reason:
                circ_label = " (Недолік)"
            elif circumstance == "ADVANTAGE":
                circ_label = " (Перевага)"
            elif circumstance == "DISADVANTAGE":
                circ_label = " (Недолік)"
            else:
                circ_label = ""

            # Ability label for d20 format display
            ab_label = f" [{ability_used}]" if ability_used and ability_used not in ("None", "none", "") else ""

            if outcome == "CRITICAL SUCCESS":
                icon, outcome_ua = "🔥", "КРИТИЧНИЙ УСПІХ"
                logs.insert(1, f"✨ Крит! Навичка '{skill}' — видатне виконання.")
            elif outcome == "CRITICAL FAILURE":
                icon, outcome_ua = "💀", "КРИТИЧНИЙ ПРОВАЛ"
                temp_debuff = (mechanical_updates.get("temp_debuff") or {}).get(skill, 0)
                if temp_debuff:
                    logs.insert(1, f"🩸 Тяжкий наслідок! Тимчасовий штраф -{temp_debuff} до '{skill}'.")
                if mechanical_updates.get("permanent_scar"):
                    logs.insert(2, f"☠️ Фатальна помилка! Навичка '{skill}' назавжди знижена.")
            elif outcome == "SUCCESS":
                icon, outcome_ua = "✅", "УСПІХ"
            else:
                icon, outcome_ua = "❌", "ПРОВАЛ"

            target_dc = mechanical_updates.get("difficulty", 15)
            # D&D format: "Skill [ABILITY](circ): roll_str = total vs DC X -> RESULT"
            dice_log = (
                f"{icon} {skill}{ab_label}{circ_label}: "
                f"{roll_str} = {total_score} vs DC {target_dc} -> {outcome_ua}"
            )
            logs.insert(0, dice_log)

            # XP лог
            xp_val = mechanical_updates.get("xp_award", 0)
            if xp_val and xp_val > 0:
                logs.append(f"XP +{xp_val}")

            # Reputation delta лог
            rep_delta = mechanical_updates.get("reputation_delta", 0)
            rep_target = mechanical_updates.get("reputation_target_npc")
            if rep_delta and rep_target:
                rep_icon = "📈" if rep_delta > 0 else "📉"
                logs.append(f"{rep_icon} Репутація з {rep_target}: {rep_delta:+d}")

        new_location = profile.get("Поточне місцезнаходження", "")
        # Генерація NPC тільки при зміні реальної локації (не при travel-стані)
        if old_location != new_location and len(new_location) > 3 and new_location != TRAVEL_LOCATION:
            current_char_name = profile.get("Ім'я", "")

            async def travel_population_task(new_loc, char_name):
                try:
                    if new_loc in _atmosphere_cache:
                        situation = _atmosphere_cache[new_loc]
                    else:
                        try:
                            def _sync_gen_travel():
                                return model_worker.generate_content(
                                    f'Describe atmosphere in "{new_loc}" (Year 298) in 1 sentence.')

                            situation_resp = await asyncio.to_thread(_sync_gen_travel)
                            situation = situation_resp.text.strip()
                            _atmosphere_cache[new_loc] = situation
                        except Exception:
                            situation = "A tense day in Westeros."
                    await populate_contextual_npcs(chat_id, new_loc, situation, excluded_name=char_name,
                                                   excluded_dead_names=get_dead_npc_names(chat_id))
                except Exception as e:
                    logger.error(f"[BG] travel_population_task failed: {e}", exc_info=True)

            _run_bg_task(travel_population_task(new_location, current_char_name))

        # === PROFILE SAVE (synchronous) ===
        # Player profile is flushed BEFORE returning so the next turn sees
        # up-to-date xp / mode / time / clocks / hp.  All other DB work
        # (NPC updates, reputation, history summary) stays in the background task.
        await save_user_data(user_id, profile, profile.get("Ім'я"))

        async def background_task(chat_id_arg, user_id_arg, profile_arg, char_name_arg, input_arg, story_arg,
                                  npc_changes_arg, legal_names_arg, mech_updates_arg=None,
                                  new_location_arg=None, player_location_changed_arg=False,
                                  player_new_scene_arg=None, player_scene_changed_arg=False,
                                  companion_npcs_arg=None, frozen_fields_reason_arg="",
                                  commit_history_arg=True):
            try:
                # Profile already persisted synchronously above; only secondary
                # writes remain here (NPC DB, reputation, history summarization).
                if npc_changes_arg:
                    await update_npcs_in_db(
                        chat_id_arg, npc_changes_arg,
                        player_new_location=new_location_arg,
                        player_location_changed=player_location_changed_arg,
                        player_new_scene=player_new_scene_arg,
                        player_scene_changed=player_scene_changed_arg,
                        companion_npcs=companion_npcs_arg,
                        frozen_fields_reason=frozen_fields_reason_arg,
                    )

                # Оновлення репутації NPC після соціальних перевірок
                if mech_updates_arg:
                    rep_delta = mech_updates_arg.get("reputation_delta", 0)
                    rep_target = mech_updates_arg.get("reputation_target_npc")
                    if rep_delta and rep_target:
                        await update_npc_reputation(chat_id_arg, rep_target, rep_delta)
                        outcome = mech_updates_arg.get("outcome", "")
                        skill = mech_updates_arg.get("skill_used", "")
                        game_minutes = safe_int(profile_arg.get("Час_хвилини", 0), 0)
                        game_day = game_minutes // _MINUTES_PER_DAY
                        await append_memory_anchor(
                            chat_id_arg,
                            rep_target,
                            f"{skill} {outcome} (дія гравця)",
                            game_day=game_day,
                            rep_change=rep_delta
                        )

                # Summary + append Turn to history + compression. In Narrator A/B mode this is
                # deferred: the vote handler calls commit_narration_to_history after the choice.
                if commit_history_arg:
                    await commit_narration_to_history(chat_id_arg, input_arg, story_arg, mech_updates_arg)
            except Exception as e:
                logger.error(f"[BG] background_task failed: {e}", exc_info=True)

        _player_loc_changed  = (old_location != new_location)
        _player_scene_changed = (old_scene != curr_scene)
        _run_bg_task(
            background_task(
                chat_id, user_id, profile, profile.get("Ім'я"), user_input, story, npc_changes,
                legal_npc_names, mechanical_updates,
                new_location_arg=new_location,
                player_location_changed_arg=_player_loc_changed,
                player_new_scene_arg=curr_scene,
                player_scene_changed_arg=_player_scene_changed,
                companion_npcs_arg=companion_npcs,
                frozen_fields_reason_arg=ai_data.get("frozen_fields_change_reason", ""),
                commit_history_arg=(_ab_mode != "pair"),
            ))

        duration = time.time() - t_start
        timing_details.append(f"💾 Save: {duration:.2f}s")
        total_time = time.time() - global_start

        debug_msg = "⏱️ ТЕХНІЧНИЙ ЗВІТ ХОДУ:\n━━━━━━━━━━━━━━━━\n"
        debug_msg += "\n".join(timing_details)
        debug_msg += "\n━━━━━━━━━━━━━━━━"
        debug_msg += f"\n🏁 ВСЬОГО: {total_time:.2f}s"
        user_sessions[chat_id]['last_debug_time'] = debug_msg
        # Snapshot thoughts into session so /thoughts cheat can read them.
        # ContextVar isolates _thoughts_log per-task, so the cheat handler (a
        # different asyncio Task) cannot see them via get_thoughts_log() directly.
        user_sessions[chat_id]['last_thoughts'] = get_thoughts_log()

        # _narrator_failed=True означає, що story містить детерміністичний last-resort абзац
        # від _build_deterministic_narrative. Це художній текст — повертаємо з impact-summary як завжди.

        change_log = "\n\n📊 " + " | ".join(logs) if logs else ""

        if _ab_mode is not None:
            # --- Narrator A/B bookkeeping (blocking mode, NARRATOR_AB_ENABLED) ---
            _mech_log = {
                k: mechanical_updates.get(k)
                for k in ("outcome", "natural_roll", "skill_val", "total_score", "difficulty",
                          "ability_used", "skill_used", "dice_roll", "mechanics_verdict")
            }
            if profile.get("mode") == "COMBAT":
                _mech_log["combat_round"] = mechanical_updates.get("combat_round")
                _mech_log["combat_phase"] = mechanical_updates.get("combat_phase")
            _ab_turn_id = new_turn_id()

            def _ab_post(_text):
                return _sanitize_story((_text or "") + (_DEATH_SUFFIX if _is_dead else ""))

            if _ab_mode == "pair":
                for _r in _ab_results:
                    _r.text = _ab_post(_r.text)
                _order = shuffle_variants(_ab_results[0], _ab_results[1])
                _rec = build_log_record(
                    turn_id=_ab_turn_id, user_id=user_id, chat_id=chat_id,
                    mode=profile.get("mode"), narrator_prompt=narrator_prompt,
                    narrator_static_tag=_static_tag(narrator_static),
                    mechanics=_mech_log, results=_ab_results,
                    shown_order=[_r.model_key for _r in _order], reason=None,
                )
                set_pending(PendingChoice(
                    turn_id=_ab_turn_id, chat_id=chat_id, user_id=user_id,
                    created=time.time(), order=_order, change_log=change_log,
                    suggested_actions=suggested_actions,
                    deferred_history={"user_input": user_input, "mech_updates": mechanical_updates},
                    log_record=_rec,
                ))
                return change_log, suggested_actions

            # single_variant / both_failed: normal flow, vote=null
            try:
                _rec = build_log_record(
                    turn_id=_ab_turn_id, user_id=user_id, chat_id=chat_id,
                    mode=profile.get("mode"), narrator_prompt=narrator_prompt,
                    narrator_static_tag=_static_tag(narrator_static),
                    mechanics=_mech_log, results=_ab_results, shown_order=None,
                    reason="single_variant" if _ab_mode == "single" else "both_failed",
                )
                await append_log(_rec)
            except Exception as _ab_log_exc:
                logger.warning(f"[NARRATOR_AB] log write failed: {_ab_log_exc}")

        story = _sanitize_story(story)

        return story + change_log, suggested_actions

    except Exception as e:
        import traceback
        traceback.print_exc()
        # Якщо streaming вже запущений — надсилаємо сигнал завершення, щоб consumer не висів 120с
        if narrator_queue is not None:
            try:
                narrator_queue.put_nowait(None)
            except Exception:
                pass
        # Persist partial debug trace so admin can inspect engine crash.
        # Most valuable debug context — preserve whatever was collected before crash.
        try:
            if _debug_active and _debug_trace is not None:
                _debug_trace["narrator"]["final_text"] = (
                    f"[ENGINE CRASH — {type(e).__name__}: {_mask(e, 200)}]"
                )
                _debug_trace["logs"] = (_debug_trace.get("logs") or []) + [
                    f"EXCEPTION: {type(e).__name__}",
                    f"MESSAGE: {_mask(e, 300)}",
                    f"TRACEBACK (last 5 lines): {_SECRET_RE.sub('[REDACTED]', traceback.format_exc()[-500:])}",
                ]
                _trace_finalize(_debug_trace, chat_id, global_start, _tmarks)
                user_sessions.setdefault(chat_id, {})["last_debug_trace"] = _debug_trace
                logger.info(f"[DEBUG_MODE] Trace saved to session (path=crash) for chat_id={chat_id}")
        except Exception:
            pass  # never block crash recovery for debug persistence
        return "📜 *Ворон згубив вашого листа... Спробуйте ще раз.*", []