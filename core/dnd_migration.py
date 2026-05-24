# core/dnd_migration.py
"""D&D migration utilities for Phase 6.

- backup_canon_npc(): saves timestamped Python copy of CANON_NPCS
- async wipe_users_db(): deletes all rows from Users_DB except header
- async regenerate_canon_npc_via_llm(): calls LLM for each NPC to add D&D-statblock fields
- delete_lore_embeddings(): forces RAG re-build on next bot start

All functions are STANDALONE — they don't run automatically on import.
Trigger via `scripts/dnd_migrate.py` CLI.

Async invariant (CLAUDE.md §5.1):
  All gspread calls are wrapped in asyncio.to_thread.
  All Gemini SDK calls (via AIWrapper.generate_content) are wrapped in asyncio.to_thread
  inside regenerate_one_npc.
"""
import asyncio
import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Callable

from core.ai_client import model_worker, clean_and_parse_json, build_strict_config
from core.prompts import build_npc_regen_prompt, build_npc_regen_schema

# ── Constants ───────────────────────────────────────────────────────────────

# Valid CR values per build_npc_regen_prompt output_schema
_VALID_CR = {
    "0", "1/8", "1/4", "1/2",
    "1", "2", "3", "4", "5",
    "6", "7", "8", "9", "10",
}

# Required ability score keys
_ABILITY_KEYS = {"STR", "DEX", "CON", "INT", "WIS", "CHA"}

# Lore-frozen fields that must be preserved verbatim from the source NPC
_FROZEN_FIELDS = {
    "Name", "Description", "Character", "Goal", "Secrets",
    "Location", "Scene", "Region", "Relation_Player",
    "Memory_Anchor", "Relation_NPCs", "Status", "Is_Canon",
    "Inventory", "Reputation_Score",
}


# ── Backup ───────────────────────────────────────────────────────────────────

def backup_canon_npc(
    canon_path: str = "database/canon_npc.py",
    backup_dir: str = "database/backups",
) -> Path:
    """Creates a timestamped Python copy of canon_npc.py.

    Returns Path to the backup file.
    Raises FileNotFoundError if canon_npc.py does not exist.
    """
    src = Path(canon_path)
    if not src.exists():
        raise FileNotFoundError(f"canon_npc.py not found at: {src.resolve()}")

    dest_dir = Path(backup_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dest = dest_dir / f"canon_npc_{ts}.py"
    shutil.copy2(src, dest)
    print(f"[BACKUP] canon_npc.py -> {dest}")
    return dest


# ── Users_DB wipe ────────────────────────────────────────────────────────────

async def wipe_users_db(
    spreadsheet_id: Optional[str] = None,
    confirm: bool = False,
) -> int:
    """Deletes all rows from Users_DB except the header row.

    Args:
        spreadsheet_id: Optional override. If None, uses the singleton db
            (which reads SPREADSHEET_ID from ENV). Passing a value here
            is only meaningful if you initialise an alternative gspread
            client yourself — the current implementation uses the
            singleton for simplicity.
        confirm: Must be True to execute. Raises ValueError otherwise.

    Returns:
        Number of data rows deleted.

    All gspread calls are executed via asyncio.to_thread (CLAUDE.md §5.1).
    """
    if not confirm:
        raise ValueError(
            "wipe_users_db: confirm=True is required to perform the wipe. "
            "Pass confirm=True explicitly after verifying intent."
        )

    from database.sheets import db
    from config import TAB_USERS

    def _sync_wipe() -> int:
        sheet = db.get_sheet(TAB_USERS)
        if sheet is None:
            raise RuntimeError(f"Sheet '{TAB_USERS}' not found.")

        # Read all values to know how many data rows exist
        all_values = sheet.get_all_values()
        # Row 1 = header; rows 2+ are data rows
        data_row_count = max(0, len(all_values) - 1)
        if data_row_count == 0:
            print("[WIPE] Users_DB is already empty (header-only). Nothing to delete.")
            return 0

        # Delete from the bottom up so row indices stay stable
        # gspread delete_rows is 1-indexed; header is row 1
        for row_idx in range(len(all_values), 1, -1):
            sheet.delete_rows(row_idx)

        print(f"[WIPE] Deleted {data_row_count} rows from Users_DB.")
        return data_row_count

    return await asyncio.to_thread(_sync_wipe)


# ── LLM-regen single NPC ─────────────────────────────────────────────────────

def _validate_statblock(statblock: dict) -> list[str]:
    """Returns a list of validation error strings. Empty list = valid."""
    errors: list[str] = []

    # cr
    cr = statblock.get("cr")
    if cr not in _VALID_CR:
        errors.append(f"cr '{cr}' is not in valid set {_VALID_CR}")

    # ability_scores
    ab = statblock.get("ability_scores")
    if not isinstance(ab, dict):
        errors.append("ability_scores is missing or not a dict")
    else:
        missing = _ABILITY_KEYS - ab.keys()
        if missing:
            errors.append(f"ability_scores missing keys: {missing}")
        for key, val in ab.items():
            if key in _ABILITY_KEYS:
                if not isinstance(val, int) or not (3 <= val <= 30):
                    # Allow up to 30 for legendary creatures (The Mountain tier)
                    errors.append(
                        f"ability_scores[{key}]={val!r} must be int in 3-30 range"
                    )

    # hp_max
    hp = statblock.get("hp_max")
    if not isinstance(hp, int) or hp <= 0:
        errors.append(f"hp_max={hp!r} must be a positive integer")

    # ac
    ac = statblock.get("ac")
    if not isinstance(ac, int) or ac < 5:
        errors.append(f"ac={ac!r} must be an integer >= 5")

    # attacks
    attacks = statblock.get("attacks")
    if not isinstance(attacks, list) or len(attacks) < 1:
        errors.append("attacks must be a non-empty list")
    else:
        for i, atk in enumerate(attacks):
            if not isinstance(atk, dict):
                errors.append(f"attacks[{i}] is not a dict")
            elif "name" not in atk or "to_hit" not in atk or "dmg" not in atk:
                errors.append(
                    f"attacks[{i}] missing required keys (name, to_hit, dmg)"
                )

    return errors


async def regenerate_one_npc(
    npc: dict,
    model=None,
) -> Optional[dict]:
    """Calls build_npc_regen_prompt for a single NPC, parses JSON,
    merges D&D statblock fields into the original NPC dict.

    Preserved lore-frozen fields (verbatim from source):
        Name, Description, Character, Goal, Secrets, Location, Scene,
        Region, Relation_Player, Memory_Anchor, Relation_NPCs, Status,
        Is_Canon, Inventory, Reputation_Score

    Added/updated D&D fields:
        cr, ability_scores, hp_max, ac, speed, attacks, saves,
        skills, conditions, tags

    Returns:
        Updated NPC dict on success.
        None if LLM call fails or returns invalid JSON — caller may retry.

    Gemini SDK call is wrapped in asyncio.to_thread (CLAUDE.md §5.1).
    """
    active_model = model or model_worker
    prompt = build_npc_regen_prompt(npc)

    try:
        _regen_cfg = build_strict_config(active_model, build_npc_regen_schema())
        def _sync_call():
            try:
                return active_model.generate_content(prompt, config=_regen_cfg)
            except Exception as _schema_err:
                if "INVALID_ARGUMENT" in str(_schema_err) or "schema" in str(_schema_err).lower():
                    print(f"[REGEN] Schema rejected for '{npc.get('Name', '?')}', falling back: {_schema_err}")
                    return active_model.generate_content(prompt)
                raise

        response = await asyncio.to_thread(_sync_call)
        raw_text = response.text if hasattr(response, "text") else str(response)
    except Exception as exc:
        print(f"[REGEN] LLM call failed for '{npc.get('Name', '?')}': {exc}")
        return None

    statblock = clean_and_parse_json(raw_text)
    if statblock is None:
        print(
            f"[REGEN] JSON parse failed for '{npc.get('Name', '?')}'. "
            f"Raw (first 200 chars): {raw_text[:200]!r}"
        )
        return None

    errors = _validate_statblock(statblock)
    if errors:
        print(
            f"[REGEN] Validation errors for '{npc.get('Name', '?')}': {errors}. "
            "Returning None so caller can retry."
        )
        return None

    # Merge: start with a copy of the original NPC (preserves all frozen fields),
    # then overlay only the D&D statblock keys.
    updated = dict(npc)
    for key in (
        "cr", "ability_scores", "hp_max", "ac", "speed",
        "attacks", "saves", "skills", "conditions", "tags",
    ):
        if key in statblock:
            updated[key] = statblock[key]

    return updated


# ── Batched LLM regen ────────────────────────────────────────────────────────

async def regenerate_canon_npc_via_llm(
    npcs: list[dict],
    batch_size: int = 10,
    pause_seconds: int = 30,
    model=None,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> list[dict]:
    """Batched regeneration of canon NPCs with D&D statblocks.

    Processes NPCs in batches of `batch_size`, pausing `pause_seconds`
    between batches to respect Gemini free-tier quota (~15 RPM).

    Failed NPCs (LLM error or validation failure) are returned with
    their original fields intact plus an `_regen_error` marker, so
    the caller can identify and retry them without corrupting the list.

    Args:
        npcs:             Input list of NPC dicts (read-only; originals not mutated).
        batch_size:       How many NPCs to send per LLM batch (default 10).
        pause_seconds:    Seconds to sleep between batches (default 30).
        model:            AIWrapper instance; defaults to model_worker.
        progress_callback: Optional callable(idx, total, npc_name) for CLI progress.

    Returns:
        list[dict] of same length as `npcs`.
        Each element is either the updated NPC (with D&D fields) or the
        original NPC + {"_regen_error": "<reason>"}.
    """
    total = len(npcs)
    results: list[Optional[dict]] = [None] * total

    for batch_start in range(0, total, batch_size):
        batch_indices = range(batch_start, min(batch_start + batch_size, total))
        batch_npcs = [npcs[i] for i in batch_indices]

        # Run all NPCs in the batch concurrently (within Gemini RPM budget)
        tasks = [regenerate_one_npc(npc, model=model) for npc in batch_npcs]
        batch_results = await asyncio.gather(*tasks, return_exceptions=True)

        for local_idx, (global_idx, result) in enumerate(
            zip(batch_indices, batch_results)
        ):
            npc_name = npcs[global_idx].get("Name", f"NPC[{global_idx}]")

            if progress_callback:
                try:
                    progress_callback(global_idx + 1, total, npc_name)
                except Exception:
                    pass  # never let progress callback crash the migration

            if isinstance(result, Exception):
                print(
                    f"[REGEN] Unexpected exception for '{npc_name}': {result}"
                )
                fallback = dict(npcs[global_idx])
                fallback["_regen_error"] = f"exception: {result}"
                results[global_idx] = fallback

            elif result is None:
                # regenerate_one_npc already logged the reason
                fallback = dict(npcs[global_idx])
                fallback["_regen_error"] = "llm_failed_or_invalid_json"
                results[global_idx] = fallback

            else:
                results[global_idx] = result
                print(
                    f"[REGEN] OK  [{global_idx + 1}/{total}] "
                    f"'{npc_name}' -> CR {result.get('cr', '?')}"
                )

        # Pause between batches (skip after the last batch)
        if batch_start + batch_size < total:
            print(
                f"[REGEN] Batch {batch_start // batch_size + 1} done. "
                f"Sleeping {pause_seconds}s for quota..."
            )
            await asyncio.sleep(pause_seconds)

    failed = sum(1 for r in results if r and "_regen_error" in r)
    print(
        f"[REGEN] Complete. {total - failed}/{total} succeeded, "
        f"{failed} failed (marked with _regen_error)."
    )
    return results  # type: ignore[return-value]  # guaranteed non-None by logic above


# ── RAG cache invalidation ───────────────────────────────────────────────────

def delete_lore_embeddings(
    embeddings_path: str = "lore_embeddings.npy",
    hash_path: str = "lore_hash.txt",
) -> tuple[bool, bool]:
    """Deletes RAG cache files so the bot rebuilds embeddings on next start.

    This operation is irreversible — no backup is created (embeddings are
    derived data; re-generation is the intended recovery).

    Returns:
        (embeddings_deleted, hash_deleted) — True for each file that existed
        and was successfully removed.
    """
    emb = Path(embeddings_path)
    hsh = Path(hash_path)

    emb_deleted = False
    if emb.exists():
        emb.unlink()
        emb_deleted = True
        print(f"[RAG] Deleted {emb}")
    else:
        print(f"[RAG] {emb} not found — skipping.")

    hsh_deleted = False
    if hsh.exists():
        hsh.unlink()
        hsh_deleted = True
        print(f"[RAG] Deleted {hsh}")
    else:
        print(f"[RAG] {hsh} not found — skipping.")

    return emb_deleted, hsh_deleted


# ── Write back to canon_npc.py ───────────────────────────────────────────────

def write_canon_npc(
    npcs: list[dict],
    output_path: str = "database/canon_npc.py",
    preserve_helpers: bool = True,
) -> None:
    """Writes an updated canon_npc.py with the regenerated NPC list.

    Args:
        npcs:             Updated NPC list (output of regenerate_canon_npc_via_llm).
        output_path:      Destination path (default: database/canon_npc.py).
        preserve_helpers: If True, reads the existing file and preserves
                          everything above the `CANON_NPCS = _ensure_reputation_scores([`
                          line (helper functions, constants, imports).
                          If False, writes a minimal header.

    The list is serialised as a Python tuple-of-dicts (pretty-printed via
    pprint) to match the existing format expected by `_ensure_reputation_scores`.

    Raises:
        FileNotFoundError: if preserve_helpers=True and the existing file
                           does not contain the expected sentinel line.
    """
    import pprint

    dest = Path(output_path)

    # ── Determine the header (everything before CANON_NPCS = ...) ──
    SENTINEL = "CANON_NPCS = _ensure_reputation_scores(["

    if preserve_helpers:
        if not dest.exists():
            raise FileNotFoundError(
                f"preserve_helpers=True but {dest} does not exist. "
                "Run with preserve_helpers=False or point to the correct path."
            )
        original_text = dest.read_text(encoding="utf-8")
        sentinel_pos = original_text.find(SENTINEL)
        if sentinel_pos == -1:
            raise ValueError(
                f"Sentinel '{SENTINEL}' not found in {dest}. "
                "Cannot preserve helpers. "
                "Run with preserve_helpers=False to overwrite completely."
            )
        header = original_text[:sentinel_pos]
    else:
        header = (
            "# database/canon_npc.py\n"
            "# Auto-generated by dnd_migrate.py (Phase 6).\n"
            "# Do NOT edit manually — use scripts/dnd_migrate.py --regen-only.\n\n"
        )

    # ── Serialise NPC list ──
    # Emit one dict per NPC, indented by 4 spaces, separated by commas.
    # This mirrors the hand-written style of the original file and avoids
    # the *-spread operator which is not valid inside a function call's
    # positional-arg list in Python 2 (and looks unusual in Python 3).
    npc_lines: list[str] = []
    for npc in npcs:
        formatted = pprint.pformat(npc, indent=4, width=120)
        # Indent every line of the formatted dict by 4 spaces
        indented = "\n".join("    " + line for line in formatted.splitlines())
        npc_lines.append(indented + ",")

    body = SENTINEL + "\n" + "\n".join(npc_lines) + "\n])\n"

    full_content = header + body
    dest.write_text(full_content, encoding="utf-8")
    print(f"[WRITE] canon_npc.py written to {dest} ({len(npcs)} NPCs).")
