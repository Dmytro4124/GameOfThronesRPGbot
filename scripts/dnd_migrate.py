#!/usr/bin/env python3
"""Phase 6 migration CLI — D&D statblock injection for canon NPCs.

Usage examples:
    python scripts/dnd_migrate.py --dry-run
        Regenerates D&D statblocks for the first 5 canon NPCs,
        prints results to stdout. No DB writes, no file writes.

    python scripts/dnd_migrate.py --backup-only
        Creates a timestamped backup of database/canon_npc.py only.

    python scripts/dnd_migrate.py --regen-only
        Regenerates D&D statblocks for ALL canon NPCs and writes
        the result back to database/canon_npc.py. Does NOT wipe Users_DB.

    python scripts/dnd_migrate.py --wipe-only --yes
        Wipes all data rows from Users_DB (header preserved).
        --yes is required; without it the command aborts.

    python scripts/dnd_migrate.py --full --yes
        Full migration (requires --yes):
          1. Backup canon_npc.py
          2. Regenerate all canon NPCs via LLM
          3. Write updated canon_npc.py
          4. Wipe Users_DB
          5. Delete RAG cache (lore_embeddings.npy + lore_hash.txt)

    python scripts/dnd_migrate.py --deterministic
        Deterministic migration (no LLM, no quota consumed):
          1. Backup canon_npc.py
          2. Derive D&D statblocks via keyword/tag heuristics (core/dnd_heuristic_stats.py)
          3. Write updated canon_npc.py
        Idempotent: NPCs that already have ability_scores are skipped.

    python scripts/dnd_migrate.py --deterministic --full --yes
        Deterministic + full cleanup (no LLM):
          1-3. Same as --deterministic above
          4. Wipe Users_DB
          5. Delete RAG cache (lore_embeddings.npy + lore_hash.txt)

Notes:
    - This script ONLY creates/modifies files and calls the Gemini API
      (when using LLM paths). It does NOT start the bot or interact with
      live users.
    - NPCs that fail LLM regen are marked with _regen_error and kept in
      the output with their original fields. Re-run --regen-only to retry.
    - Batch size: 10 NPCs/batch, 30s pause between batches (Gemini free tier).
    - --deterministic never calls Gemini; it is safe to run without API keys.
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

# ── Path setup ──────────────────────────────────────────────────────────────
# Insert project root so all `core.*` and `database.*` imports work correctly
# regardless of the current working directory.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ── Imports (after path setup) ───────────────────────────────────────────────
from core.dnd_migration import (
    backup_canon_npc,
    delete_lore_embeddings,
    regenerate_canon_npc_via_llm,
    wipe_users_db,
    write_canon_npc,
)
from core.dnd_heuristic_stats import apply_stats_to_canon_npcs
from database.canon_npc import CANON_NPCS


# ── Helpers ──────────────────────────────────────────────────────────────────

def _interactive_confirm(prompt: str) -> bool:
    """Prompts the user interactively. Returns True if they type 'yes'."""
    try:
        answer = input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer == "yes"


def _progress(idx: int, total: int, name: str) -> None:
    """Simple progress indicator printed to stdout."""
    pct = int(idx / total * 100)
    print(f"  [{idx:>3}/{total}] {pct:>3}%  {name}", flush=True)


def _print_npc_summary(npc: dict) -> None:
    """Prints a compact statblock summary for --dry-run output."""
    name = npc.get("Name", "?")
    error = npc.get("_regen_error")
    if error:
        print(f"  FAILED  {name}: {error}")
        return

    cr = npc.get("cr", "?")
    hp = npc.get("hp_max", "?")
    ac = npc.get("ac", "?")
    ab = npc.get("ability_scores", {})
    ab_str = " ".join(
        f"{k}:{ab.get(k, '?')}" for k in ("STR", "DEX", "CON", "INT", "WIS", "CHA")
    )
    attacks = npc.get("attacks", [])
    atk_names = ", ".join(a.get("name", "?") for a in attacks[:3])
    tags = ", ".join(npc.get("tags", []))
    print(
        f"  OK  {name}\n"
        f"        CR {cr}  HP {hp}  AC {ac}\n"
        f"        {ab_str}\n"
        f"        Attacks: {atk_names}\n"
        f"        Tags: {tags}\n"
    )


# ── Sub-command handlers ─────────────────────────────────────────────────────

async def cmd_dry_run(args: argparse.Namespace) -> int:
    """Regenerates the first 5 canon NPCs; prints results; no file/DB writes."""
    sample = list(CANON_NPCS)[:5]
    print(
        f"\n[DRY RUN] Regenerating D&D statblocks for first {len(sample)} NPCs "
        f"(no files or DB will be modified)...\n"
    )
    results = await regenerate_canon_npc_via_llm(
        sample,
        batch_size=5,
        pause_seconds=0,  # single batch — no inter-batch pause needed
        progress_callback=_progress,
    )
    print("\n── Results ──────────────────────────────────────────────────────")
    for npc in results:
        _print_npc_summary(npc)

    failed = sum(1 for r in results if "_regen_error" in r)
    print(f"\nDry-run complete: {len(results) - failed}/{len(results)} succeeded.")
    return 0 if failed == 0 else 1


async def cmd_backup_only(args: argparse.Namespace) -> int:
    """Creates a timestamped backup of canon_npc.py."""
    print("\n[BACKUP] Creating backup of database/canon_npc.py...")
    backup_path = backup_canon_npc(
        canon_path=str(PROJECT_ROOT / "database" / "canon_npc.py"),
        backup_dir=str(PROJECT_ROOT / "database" / "backups"),
    )
    print(f"[BACKUP] Done. File: {backup_path}")
    return 0


async def cmd_regen_only(args: argparse.Namespace) -> int:
    """Regenerates all canon NPCs; writes updated canon_npc.py."""
    npcs = list(CANON_NPCS)
    total = len(npcs)
    print(f"\n[REGEN] Starting D&D statblock generation for {total} NPCs...")

    # Backup first — safety net
    print("[REGEN] Creating backup before write...")
    backup_canon_npc(
        canon_path=str(PROJECT_ROOT / "database" / "canon_npc.py"),
        backup_dir=str(PROJECT_ROOT / "database" / "backups"),
    )

    results = await regenerate_canon_npc_via_llm(
        npcs,
        batch_size=args.batch_size,
        pause_seconds=args.pause,
        progress_callback=_progress,
    )

    failed = [r for r in results if "_regen_error" in r]
    if failed:
        names = [r.get("Name", "?") for r in failed]
        print(
            f"\n[REGEN] WARNING: {len(failed)} NPC(s) failed and kept original fields: "
            + ", ".join(names)
        )

    output_path = str(PROJECT_ROOT / "database" / "canon_npc.py")
    write_canon_npc(results, output_path=output_path)
    print(f"\n[REGEN] Done. {total - len(failed)}/{total} NPCs have new D&D statblocks.")
    return 0 if not failed else 1


async def cmd_wipe_only(args: argparse.Namespace) -> int:
    """Wipes all data rows from Users_DB (requires --yes)."""
    if not args.yes:
        if not _interactive_confirm(
            "\nWARNING: This will permanently delete ALL user profiles from Users_DB.\n"
            "Type 'yes' to confirm, anything else to abort: "
        ):
            print("Aborted.")
            return 1

    print("\n[WIPE] Wiping Users_DB...")
    deleted = await wipe_users_db(confirm=True)
    print(f"[WIPE] Done. {deleted} row(s) deleted.")
    return 0


async def cmd_full(args: argparse.Namespace) -> int:
    """Full migration: backup + regen + write + wipe + delete RAG cache."""
    if not args.yes:
        print(
            "\nWARNING: Full migration will:\n"
            "  1. Backup database/canon_npc.py\n"
            "  2. Regenerate ALL canon NPCs via LLM (API quota consumed)\n"
            "  3. Overwrite database/canon_npc.py\n"
            "  4. PERMANENTLY DELETE all user profiles from Users_DB\n"
            "  5. Delete RAG cache (lore_embeddings.npy + lore_hash.txt)\n"
        )
        if not _interactive_confirm("Type 'yes' to continue, anything else to abort: "):
            print("Aborted.")
            return 1

    npcs = list(CANON_NPCS)
    total = len(npcs)

    # 1. Backup
    print("\n[1/5] Backing up canon_npc.py...")
    backup_path = backup_canon_npc(
        canon_path=str(PROJECT_ROOT / "database" / "canon_npc.py"),
        backup_dir=str(PROJECT_ROOT / "database" / "backups"),
    )
    print(f"      Backup: {backup_path}")

    # 2. Regenerate
    print(f"\n[2/5] Regenerating D&D statblocks for {total} NPCs...")
    results = await regenerate_canon_npc_via_llm(
        npcs,
        batch_size=args.batch_size,
        pause_seconds=args.pause,
        progress_callback=_progress,
    )
    failed = [r for r in results if "_regen_error" in r]

    # 3. Write canon_npc.py
    print(f"\n[3/5] Writing updated canon_npc.py...")
    output_path = str(PROJECT_ROOT / "database" / "canon_npc.py")
    write_canon_npc(results, output_path=output_path)
    if failed:
        names = [r.get("Name", "?") for r in failed]
        print(
            f"      WARNING: {len(failed)} NPC(s) kept original fields (no D&D statblock): "
            + ", ".join(names)
        )

    # 4. Wipe Users_DB
    print("\n[4/5] Wiping Users_DB...")
    deleted = await wipe_users_db(confirm=True)
    print(f"      {deleted} row(s) deleted.")

    # 5. Delete RAG cache
    print("\n[5/5] Deleting RAG cache...")
    emb_del, hsh_del = delete_lore_embeddings(
        embeddings_path=str(PROJECT_ROOT / "lore_embeddings.npy"),
        hash_path=str(PROJECT_ROOT / "lore_hash.txt"),
    )
    if emb_del:
        print("      lore_embeddings.npy deleted.")
    if hsh_del:
        print("      lore_hash.txt deleted.")

    print(
        f"\n[FULL] Migration complete.\n"
        f"  NPCs regenerated: {total - len(failed)}/{total}\n"
        f"  Users deleted: {deleted}\n"
        f"  RAG cache cleared: embeddings={emb_del}, hash={hsh_del}\n"
        f"\nNext step: restart the bot — it will rebuild the RAG cache automatically.\n"
    )
    return 0 if not failed else 1


# ── Deterministic sub-command ─────────────────────────────────────────────────

async def cmd_deterministic(args: argparse.Namespace) -> int:
    """Deterministic migration: backup + heuristic statblocks + write (no LLM).

    When combined with --full and --yes, also wipes Users_DB and deletes
    the RAG cache — identical cleanup as cmd_full but without LLM regen.

    Idempotent: NPCs that already have ability_scores are left untouched.
    """
    npcs = list(CANON_NPCS)
    total = len(npcs)

    is_full = getattr(args, "full", False)

    if is_full and not args.yes:
        print(
            "\nWARNING: --deterministic --full will:\n"
            "  1. Backup database/canon_npc.py\n"
            "  2. Derive D&D statblocks via heuristics (NO LLM)\n"
            "  3. Overwrite database/canon_npc.py\n"
            "  4. PERMANENTLY DELETE all user profiles from Users_DB\n"
            "  5. Delete RAG cache (lore_embeddings.npy + lore_hash.txt)\n"
        )
        if not _interactive_confirm("Type 'yes' to continue, anything else to abort: "):
            print("Aborted.")
            return 1

    # 1. Backup
    print("\n[1] Backing up canon_npc.py...")
    backup_path = backup_canon_npc(
        canon_path=str(PROJECT_ROOT / "database" / "canon_npc.py"),
        backup_dir=str(PROJECT_ROOT / "database" / "backups"),
    )
    print(f"    Backup: {backup_path}")

    # 2. Apply heuristic statblocks (pure Python, no I/O, no LLM)
    print(f"\n[2] Applying deterministic D&D statblocks to {total} NPCs...")
    results = apply_stats_to_canon_npcs(npcs)

    skipped = sum(1 for orig, res in zip(npcs, results) if "_heuristic_role" not in res)
    new_stats = total - skipped
    print(f"    {new_stats} NPCs got new statblocks; {skipped} already had ability_scores (skipped).")

    # Print a brief summary so user can spot obvious misclassifications
    for npc in results:
        role = npc.get("_heuristic_role", "pre-existing")
        cr = npc.get("cr", "?")
        hp = npc.get("hp_max", "?")
        print(f"  {npc.get('Name', '?'):40s}  role={role:14s}  CR={cr}  HP={hp}")

    # 3. Write canon_npc.py
    print(f"\n[3] Writing updated canon_npc.py ({total} NPCs)...")
    output_path = str(PROJECT_ROOT / "database" / "canon_npc.py")
    write_canon_npc(results, output_path=output_path)

    if not is_full:
        print(
            "\n[DETERMINISTIC] Done.\n"
            f"  NPCs processed: {total}\n"
            f"  New statblocks: {new_stats}\n"
            f"  Skipped (pre-existing): {skipped}\n"
            "\nNext step: restart the bot — it will pick up the new statblocks.\n"
        )
        return 0

    # 4. Wipe Users_DB (only when --full)
    print("\n[4] Wiping Users_DB...")
    deleted = await wipe_users_db(confirm=True)
    print(f"    {deleted} row(s) deleted.")

    # 5. Delete RAG cache (only when --full)
    print("\n[5] Deleting RAG cache...")
    emb_del, hsh_del = delete_lore_embeddings(
        embeddings_path=str(PROJECT_ROOT / "lore_embeddings.npy"),
        hash_path=str(PROJECT_ROOT / "lore_hash.txt"),
    )
    if emb_del:
        print("    lore_embeddings.npy deleted.")
    if hsh_del:
        print("    lore_hash.txt deleted.")

    print(
        "\n[DETERMINISTIC FULL] Migration complete.\n"
        f"  NPCs processed: {total} ({new_stats} new, {skipped} skipped)\n"
        f"  Users deleted: {deleted}\n"
        f"  RAG cache cleared: embeddings={emb_del}, hash={hsh_del}\n"
        "\nNext step: restart the bot — it will rebuild the RAG cache automatically.\n"
    )
    return 0


# ── Argument parser ──────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dnd_migrate.py",
        description=(
            "Phase 6 migration: inject D&D statblocks into canon NPCs "
            "and optionally wipe Users_DB for a clean D&D restart."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # required=False so --deterministic can be used standalone (without any mode flag).
    # main() performs manual validation to ensure at least one of the two "entry points"
    # (a mode flag OR --deterministic) is provided.
    mode_group = parser.add_mutually_exclusive_group(required=False)
    mode_group.add_argument(
        "--dry-run",
        action="store_true",
        help="Regenerate statblocks for first 5 NPCs only; print results; no writes.",
    )
    mode_group.add_argument(
        "--backup-only",
        action="store_true",
        help="Only create a timestamped backup of database/canon_npc.py.",
    )
    mode_group.add_argument(
        "--regen-only",
        action="store_true",
        help="Regenerate all canon NPCs and write canon_npc.py (no DB wipe).",
    )
    mode_group.add_argument(
        "--wipe-only",
        action="store_true",
        help="Wipe all user profiles from Users_DB (requires --yes).",
    )
    mode_group.add_argument(
        "--full",
        action="store_true",
        help="Full migration: backup + regen + write + wipe + delete RAG cache.",
    )

    # --deterministic is NOT in the mutually-exclusive group so it can be combined
    # with --full --yes for a "deterministic full" run.
    parser.add_argument(
        "--deterministic",
        action="store_true",
        default=False,
        help=(
            "Use Python heuristics instead of LLM. Fast, reproducible, no quota. "
            "Derives D&D statblocks from NPC Description/Character keywords. "
            "Standalone: backup + derive + write. "
            "With --full --yes: also wipes Users_DB and deletes RAG cache."
        ),
    )

    parser.add_argument(
        "--yes",
        action="store_true",
        default=False,
        help="Skip interactive confirmation for destructive operations.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=10,
        metavar="N",
        help="NPCs per LLM batch (default: 10). Reduce if hitting 429 errors.",
    )
    parser.add_argument(
        "--pause",
        type=int,
        default=30,
        metavar="SECONDS",
        help="Pause between batches in seconds (default: 30). Increase for stricter quota.",
    )

    return parser


# ── Entry point ──────────────────────────────────────────────────────────────

async def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    # --deterministic intercepts first; it is compatible with --full --yes.
    if args.deterministic:
        return await cmd_deterministic(args)

    # Legacy LLM paths — require exactly one mode flag from the exclusive group.
    if args.dry_run:
        return await cmd_dry_run(args)
    elif args.backup_only:
        return await cmd_backup_only(args)
    elif args.regen_only:
        return await cmd_regen_only(args)
    elif args.wipe_only:
        return await cmd_wipe_only(args)
    elif args.full:
        return await cmd_full(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
