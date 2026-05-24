# test/test_dnd_migration.py
"""Tests for core/dnd_migration.py — Phase 6 D&D migration utilities.

Strategy:
  - No real gspread, Gemini, or filesystem access outside tmp_path.
  - Async tests are run via asyncio.run() inside sync test functions.
    (pytest-asyncio is not installed; anyio plugin does not support
     @pytest.mark.asyncio by itself.)
  - Mocking targets: core.dnd_migration.model_worker,
    database.sheets.db, config.TAB_USERS,
    core.dnd_migration.build_npc_regen_prompt,
    core.dnd_migration.clean_and_parse_json.
"""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

import core.dnd_migration as dnd_mod
from core.dnd_migration import (
    _validate_statblock,
    backup_canon_npc,
    delete_lore_embeddings,
    regenerate_canon_npc_via_llm,
    regenerate_one_npc,
    wipe_users_db,
    write_canon_npc,
)


# ===========================================================================
# Helpers / Factories
# ===========================================================================

def _valid_statblock(**overrides) -> dict:
    """Returns a minimal valid statblock that passes _validate_statblock."""
    base = {
        "cr": "1/4",
        "ability_scores": {
            "STR": 10, "DEX": 10, "CON": 10,
            "INT": 10, "WIS": 10, "CHA": 10,
        },
        "hp_max": 10,
        "ac": 12,
        "attacks": [{"name": "Sword", "to_hit": "+2", "dmg": "1d6+0"}],
    }
    base.update(overrides)
    return base


def _mock_model_ok(statblock: dict) -> MagicMock:
    """Returns a fake model whose generate_content returns valid JSON."""
    model = MagicMock()
    resp = MagicMock()
    resp.text = json.dumps(statblock)
    model.generate_content.return_value = resp
    return model


def _mock_model_error() -> MagicMock:
    """Returns a fake model whose generate_content raises an exception."""
    model = MagicMock()
    model.generate_content.side_effect = ConnectionError("API down")
    return model


def _make_npc(name: str = "Test NPC") -> dict:
    return {
        "Name": name,
        "Description": "A test NPC",
        "Character": "Brave",
        "Goal": "Survive",
        "Secrets": "None",
        "Location": "King's Landing",
        "Scene": "Great Hall",
        "Region": "Crownlands",
        "Relation_Player": "Нейтральний",
        "Memory_Anchor": "",
        "Relation_NPCs": "",
        "Status": "Active",
        "Is_Canon": True,
        "Inventory": [],
        "Reputation_Score": 0,
    }


def _make_npcs(n: int) -> list[dict]:
    return [_make_npc(f"NPC_{i}") for i in range(n)]


# ===========================================================================
# 1. backup_canon_npc
# ===========================================================================

class TestBackupCanonNpc:

    def test_creates_backup_file_with_timestamp(self, tmp_path):
        """backup_canon_npc must create a file with timestamp in the name."""
        canon = tmp_path / "canon_npc.py"
        canon.write_text("CANON_NPCS = []\n", encoding="utf-8")
        backup_dir = tmp_path / "backups"

        result = backup_canon_npc(
            canon_path=str(canon),
            backup_dir=str(backup_dir),
        )

        assert result.exists(), "Backup file must exist after backup"
        assert result.name.startswith("canon_npc_")
        assert result.suffix == ".py"

    def test_backup_dir_auto_created(self, tmp_path):
        """backup_canon_npc must create backup_dir if it does not exist."""
        canon = tmp_path / "canon_npc.py"
        canon.write_text("CANON_NPCS = []\n", encoding="utf-8")
        backup_dir = tmp_path / "nested" / "backups"
        assert not backup_dir.exists()

        backup_canon_npc(
            canon_path=str(canon),
            backup_dir=str(backup_dir),
        )

        assert backup_dir.exists(), "backup_dir must be created automatically"

    def test_original_not_modified(self, tmp_path):
        """backup_canon_npc must not alter the source canon_npc.py."""
        content = "CANON_NPCS = []\n# original content\n"
        canon = tmp_path / "canon_npc.py"
        canon.write_text(content, encoding="utf-8")
        backup_dir = tmp_path / "backups"

        backup_canon_npc(
            canon_path=str(canon),
            backup_dir=str(backup_dir),
        )

        assert canon.read_text(encoding="utf-8") == content

    def test_raises_if_source_missing(self, tmp_path):
        """backup_canon_npc must raise FileNotFoundError when canon_npc.py is absent."""
        with pytest.raises(FileNotFoundError):
            backup_canon_npc(
                canon_path=str(tmp_path / "nonexistent.py"),
                backup_dir=str(tmp_path / "backups"),
            )


# ===========================================================================
# 2. _validate_statblock
# ===========================================================================

class TestValidateStatblock:

    def test_valid_statblock_returns_no_errors(self):
        errors = _validate_statblock(_valid_statblock())
        assert errors == [], f"Expected no errors, got: {errors}"

    @pytest.mark.parametrize("cr", ["1/4", "1/2", "1/8", "0", "5", "10"])
    def test_valid_cr_values(self, cr):
        errors = _validate_statblock(_valid_statblock(cr=cr))
        cr_errors = [e for e in errors if "cr" in e]
        assert cr_errors == [], f"CR '{cr}' should be valid but got: {cr_errors}"

    @pytest.mark.parametrize("bad_cr", ["11", "90", "15", "2.5", "", None, 3])
    def test_invalid_cr_produces_error(self, bad_cr):
        errors = _validate_statblock(_valid_statblock(cr=bad_cr))
        assert any("cr" in e for e in errors), (
            f"Expected cr validation error for cr={bad_cr!r}, got: {errors}"
        )

    @pytest.mark.parametrize("missing_key", ["STR", "DEX", "CON", "INT", "WIS", "CHA"])
    def test_missing_ability_score_key_produces_error(self, missing_key):
        ab = {"STR": 10, "DEX": 10, "CON": 10, "INT": 10, "WIS": 10, "CHA": 10}
        del ab[missing_key]
        errors = _validate_statblock(_valid_statblock(ability_scores=ab))
        assert any("ability_scores" in e for e in errors), (
            f"Expected ability_scores error when '{missing_key}' is missing; got: {errors}"
        )

    def test_ac_below_5_produces_error(self):
        errors = _validate_statblock(_valid_statblock(ac=4))
        assert any("ac" in e for e in errors), f"Expected ac error, got: {errors}"

    def test_ac_exactly_5_is_valid(self):
        errors = _validate_statblock(_valid_statblock(ac=5))
        ac_errors = [e for e in errors if "ac" in e]
        assert ac_errors == [], f"AC=5 should be valid, got: {ac_errors}"

    def test_hp_max_zero_produces_error(self):
        errors = _validate_statblock(_valid_statblock(hp_max=0))
        assert any("hp_max" in e for e in errors), f"Expected hp_max error, got: {errors}"

    def test_hp_max_negative_produces_error(self):
        errors = _validate_statblock(_valid_statblock(hp_max=-5))
        assert any("hp_max" in e for e in errors), f"Expected hp_max error, got: {errors}"

    def test_hp_max_positive_is_valid(self):
        errors = _validate_statblock(_valid_statblock(hp_max=1))
        hp_errors = [e for e in errors if "hp_max" in e]
        assert hp_errors == [], f"hp_max=1 should be valid, got: {hp_errors}"

    def test_empty_attacks_produces_error(self):
        errors = _validate_statblock(_valid_statblock(attacks=[]))
        assert any("attacks" in e for e in errors), f"Expected attacks error, got: {errors}"

    def test_ability_score_above_30_produces_error(self):
        ab = {"STR": 31, "DEX": 10, "CON": 10, "INT": 10, "WIS": 10, "CHA": 10}
        errors = _validate_statblock(_valid_statblock(ability_scores=ab))
        assert any("STR" in e for e in errors), (
            f"Expected error for STR=31 (above max 30), got: {errors}"
        )

    def test_ability_score_exactly_30_is_valid(self):
        ab = {"STR": 30, "DEX": 10, "CON": 10, "INT": 10, "WIS": 10, "CHA": 10}
        errors = _validate_statblock(_valid_statblock(ability_scores=ab))
        ability_errors = [e for e in errors if "STR" in e]
        assert ability_errors == [], f"STR=30 should be valid, got: {ability_errors}"

    def test_missing_ability_scores_entirely(self):
        sb = _valid_statblock()
        del sb["ability_scores"]
        errors = _validate_statblock(sb)
        assert any("ability_scores" in e for e in errors)

    def test_attack_missing_required_keys(self):
        attacks = [{"name": "Sword"}]  # missing to_hit and dmg
        errors = _validate_statblock(_valid_statblock(attacks=attacks))
        assert any("attacks" in e for e in errors), (
            f"Expected error for attack missing to_hit/dmg, got: {errors}"
        )


# ===========================================================================
# 3. regenerate_one_npc (mock LLM) — run via asyncio.run()
# ===========================================================================

class TestRegenerateOneNpc:

    def test_valid_llm_response_merges_statblock(self):
        """Valid JSON statblock from LLM must be merged into NPC; frozen fields preserved."""
        npc = _make_npc("Ned Stark")
        statblock = _valid_statblock(cr="5", hp_max=52, ac=16)
        model = _mock_model_ok(statblock)

        async def run():
            with patch("core.dnd_migration.build_npc_regen_prompt", return_value="prompt"):
                return await regenerate_one_npc(npc, model=model)

        result = asyncio.run(run())

        assert result is not None
        assert result["cr"] == "5"
        assert result["hp_max"] == 52
        assert result["ac"] == 16
        # Frozen lore fields preserved verbatim
        assert result["Name"] == "Ned Stark"
        assert result["Description"] == npc["Description"]
        assert result["Goal"] == npc["Goal"]
        assert result["Secrets"] == npc["Secrets"]
        assert result["Location"] == npc["Location"]
        assert result["Status"] == npc["Status"]

    def test_invalid_json_from_llm_returns_none(self):
        """If LLM returns non-parseable JSON, regenerate_one_npc must return None."""
        npc = _make_npc("Cersei")
        model = MagicMock()
        resp = MagicMock()
        resp.text = "This is not JSON at all {{{{ bad }}"
        model.generate_content.return_value = resp

        async def run():
            with patch("core.dnd_migration.build_npc_regen_prompt", return_value="prompt"):
                return await regenerate_one_npc(npc, model=model)

        result = asyncio.run(run())
        assert result is None

    def test_llm_raises_returns_none_gracefully(self):
        """If LLM call raises an exception, regenerate_one_npc must return None (no crash)."""
        npc = _make_npc("Tyrion")
        model = _mock_model_error()

        async def run():
            with patch("core.dnd_migration.build_npc_regen_prompt", return_value="prompt"):
                return await regenerate_one_npc(npc, model=model)

        result = asyncio.run(run())
        assert result is None

    def test_invalid_statblock_validation_returns_none(self):
        """JSON parses but fails validation (bad cr) → must return None."""
        npc = _make_npc("Joffrey")
        bad_statblock = _valid_statblock(cr="99")  # invalid CR
        model = _mock_model_ok(bad_statblock)

        async def run():
            with patch("core.dnd_migration.build_npc_regen_prompt", return_value="prompt"):
                return await regenerate_one_npc(npc, model=model)

        result = asyncio.run(run())
        assert result is None

    def test_gemini_call_wrapped_in_to_thread(self):
        """The sync model.generate_content call must be offloaded to asyncio.to_thread."""
        npc = _make_npc("Sansa")
        statblock = _valid_statblock()
        model = _mock_model_ok(statblock)

        to_thread_calls: list = []
        original_to_thread = asyncio.to_thread

        async def spy_to_thread(func, *args, **kwargs):
            to_thread_calls.append(True)
            return await original_to_thread(func, *args, **kwargs)

        async def run():
            with patch("core.dnd_migration.build_npc_regen_prompt", return_value="prompt"), \
                 patch("core.dnd_migration.asyncio.to_thread", side_effect=spy_to_thread):
                return await regenerate_one_npc(npc, model=model)

        asyncio.run(run())
        assert len(to_thread_calls) >= 1, (
            "asyncio.to_thread must be called at least once to wrap the sync LLM call"
        )


# ===========================================================================
# 4. regenerate_canon_npc_via_llm (batched) — run via asyncio.run()
# ===========================================================================

class TestRegenerateCanonNpcViallm:

    def test_12_npcs_batch_10_creates_2_batches_with_one_pause(self):
        """12 NPCs, batch_size=10 → 2 batches; asyncio.sleep called once (between them)."""
        npcs = _make_npcs(12)
        statblock = _valid_statblock()
        sleep_calls: list = []

        async def fake_sleep(seconds):
            sleep_calls.append(seconds)

        async def run():
            with patch("core.dnd_migration.build_npc_regen_prompt", return_value="p"), \
                 patch("core.dnd_migration.asyncio.sleep", side_effect=fake_sleep):
                model = _mock_model_ok(statblock)
                return await regenerate_canon_npc_via_llm(
                    npcs,
                    batch_size=10,
                    pause_seconds=0,
                    model=model,
                )

        results = asyncio.run(run())

        assert len(results) == 12
        assert len(sleep_calls) == 1, (
            f"Expected 1 inter-batch sleep, got {len(sleep_calls)}"
        )

    def test_failed_npc_gets_regen_error_marker_rest_succeed(self):
        """When one NPC fails LLM regen, it must carry _regen_error; rest succeed."""
        npcs = _make_npcs(3)
        statblock = _valid_statblock()

        call_count = [0]

        def side_effect(prompt):
            call_count[0] += 1
            resp = MagicMock()
            if call_count[0] == 2:
                resp.text = "{ invalid json }"
            else:
                resp.text = json.dumps(statblock)
            return resp

        model = MagicMock()
        model.generate_content.side_effect = side_effect

        async def run():
            with patch("core.dnd_migration.build_npc_regen_prompt", return_value="p"), \
                 patch("core.dnd_migration.asyncio.sleep", new=AsyncMock()):
                return await regenerate_canon_npc_via_llm(
                    npcs,
                    batch_size=10,
                    pause_seconds=0,
                    model=model,
                )

        results = asyncio.run(run())

        assert len(results) == 3
        assert "_regen_error" in results[1], (
            f"Failed NPC must have _regen_error marker; got: {results[1]}"
        )
        assert "_regen_error" not in results[0]
        assert "_regen_error" not in results[2]

    def test_progress_callback_called_once_per_npc(self):
        """progress_callback must be called exactly once per NPC."""
        n = 5
        npcs = _make_npcs(n)
        statblock = _valid_statblock()
        progress_calls: list[tuple] = []

        def progress(idx, total, name):
            progress_calls.append((idx, total, name))

        async def run():
            with patch("core.dnd_migration.build_npc_regen_prompt", return_value="p"), \
                 patch("core.dnd_migration.asyncio.sleep", new=AsyncMock()):
                model = _mock_model_ok(statblock)
                return await regenerate_canon_npc_via_llm(
                    npcs,
                    batch_size=10,
                    pause_seconds=0,
                    model=model,
                    progress_callback=progress,
                )

        asyncio.run(run())
        assert len(progress_calls) == n, (
            f"progress_callback must be called {n} times, was called {len(progress_calls)}"
        )

    def test_no_pause_after_last_batch(self):
        """If all NPCs fit in one batch, asyncio.sleep must NOT be called."""
        npcs = _make_npcs(3)
        statblock = _valid_statblock()
        sleep_calls: list = []

        async def fake_sleep(s):
            sleep_calls.append(s)

        async def run():
            with patch("core.dnd_migration.build_npc_regen_prompt", return_value="p"), \
                 patch("core.dnd_migration.asyncio.sleep", side_effect=fake_sleep):
                model = _mock_model_ok(statblock)
                return await regenerate_canon_npc_via_llm(
                    npcs,
                    batch_size=10,
                    pause_seconds=5,
                    model=model,
                )

        asyncio.run(run())
        assert sleep_calls == [], "No sleep should occur when everything fits in one batch"

    def test_result_length_equals_input_length(self):
        """Output list must have same length as input — no NPCs dropped or duplicated."""
        n = 7
        npcs = _make_npcs(n)
        statblock = _valid_statblock()

        async def run():
            with patch("core.dnd_migration.build_npc_regen_prompt", return_value="p"), \
                 patch("core.dnd_migration.asyncio.sleep", new=AsyncMock()):
                model = _mock_model_ok(statblock)
                return await regenerate_canon_npc_via_llm(
                    npcs,
                    batch_size=3,
                    pause_seconds=0,
                    model=model,
                )

        results = asyncio.run(run())
        assert len(results) == n


# ===========================================================================
# 5. delete_lore_embeddings
# ===========================================================================

class TestDeleteLoreEmbeddings:

    def test_both_files_exist_both_deleted(self, tmp_path):
        """When both files exist, both must be deleted and (True, True) returned."""
        emb = tmp_path / "lore_embeddings.npy"
        hsh = tmp_path / "lore_hash.txt"
        emb.write_bytes(b"\x00")
        hsh.write_text("abc123", encoding="utf-8")

        result = delete_lore_embeddings(
            embeddings_path=str(emb),
            hash_path=str(hsh),
        )

        assert result == (True, True)
        assert not emb.exists()
        assert not hsh.exists()

    def test_embeddings_missing_returns_false_for_emb(self, tmp_path):
        """When embeddings file is absent, first element must be False."""
        hsh = tmp_path / "lore_hash.txt"
        hsh.write_text("abc123", encoding="utf-8")

        emb_del, hsh_del = delete_lore_embeddings(
            embeddings_path=str(tmp_path / "nonexistent.npy"),
            hash_path=str(hsh),
        )

        assert emb_del is False
        assert hsh_del is True

    def test_hash_missing_returns_false_for_hash(self, tmp_path):
        """When hash file is absent, second element must be False."""
        emb = tmp_path / "lore_embeddings.npy"
        emb.write_bytes(b"\x00")

        emb_del, hsh_del = delete_lore_embeddings(
            embeddings_path=str(emb),
            hash_path=str(tmp_path / "nonexistent.txt"),
        )

        assert emb_del is True
        assert hsh_del is False

    def test_both_missing_returns_false_false(self, tmp_path):
        """When both files are absent, both flags must be False (no error raised)."""
        emb_del, hsh_del = delete_lore_embeddings(
            embeddings_path=str(tmp_path / "a.npy"),
            hash_path=str(tmp_path / "b.txt"),
        )
        assert emb_del is False
        assert hsh_del is False


# ===========================================================================
# 6. write_canon_npc
# ===========================================================================

class TestWriteCanonNpc:

    def _minimal_npcs(self) -> list[dict]:
        return [
            {"Name": "Eddard Stark", "Location": "Winterfell"},
            {"Name": "Cersei Lannister", "Location": "King's Landing"},
        ]

    def test_writes_file_with_both_npcs(self, tmp_path):
        """write_canon_npc with preserve_helpers=False must write both NPCs."""
        out = tmp_path / "canon_npc.py"
        write_canon_npc(self._minimal_npcs(), output_path=str(out), preserve_helpers=False)

        content = out.read_text(encoding="utf-8")
        assert "Eddard Stark" in content
        assert "Cersei Lannister" in content

    def test_preserve_helpers_false_starts_with_auto_header(self, tmp_path):
        """preserve_helpers=False must produce a file starting with the auto-header comment."""
        out = tmp_path / "canon_npc.py"
        write_canon_npc(self._minimal_npcs(), output_path=str(out), preserve_helpers=False)

        content = out.read_text(encoding="utf-8")
        assert content.startswith("# database/canon_npc.py"), (
            f"Expected auto-header at start; file begins with: {content[:80]!r}"
        )

    def test_preserve_helpers_true_keeps_header(self, tmp_path):
        """preserve_helpers=True must retain content before the CANON_NPCS sentinel."""
        sentinel = "CANON_NPCS = _ensure_reputation_scores(["
        original = (
            "# Original header\n"
            "def _ensure_reputation_scores(lst): return lst\n\n"
            + sentinel
            + "\n]\n)\n"
        )
        out = tmp_path / "canon_npc.py"
        out.write_text(original, encoding="utf-8")

        write_canon_npc(self._minimal_npcs(), output_path=str(out), preserve_helpers=True)

        updated = out.read_text(encoding="utf-8")
        assert "# Original header" in updated
        assert "_ensure_reputation_scores" in updated
        assert "Eddard Stark" in updated

    def test_generated_file_is_parseable_via_exec(self, tmp_path):
        """The generated canon_npc.py must be syntactically valid Python."""
        out = tmp_path / "canon_npc.py"
        write_canon_npc(self._minimal_npcs(), output_path=str(out), preserve_helpers=False)

        src = out.read_text(encoding="utf-8")
        helper = "def _ensure_reputation_scores(lst): return lst\n\n"
        ns: dict = {}
        exec(helper + src, ns)  # must not raise SyntaxError / NameError

        canon = ns.get("CANON_NPCS")
        assert canon is not None, "CANON_NPCS must be defined after exec"
        assert len(canon) == 2

    def test_preserve_helpers_true_raises_if_file_missing(self, tmp_path):
        """preserve_helpers=True on a non-existent file must raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            write_canon_npc(
                [_make_npc()],
                output_path=str(tmp_path / "missing.py"),
                preserve_helpers=True,
            )

    def test_preserve_helpers_true_raises_if_sentinel_absent(self, tmp_path):
        """preserve_helpers=True must raise ValueError if sentinel line is not found."""
        out = tmp_path / "canon_npc.py"
        out.write_text("# No sentinel here\nCANON_NPCS = []\n", encoding="utf-8")

        with pytest.raises(ValueError, match="Sentinel"):
            write_canon_npc(
                [_make_npc()],
                output_path=str(out),
                preserve_helpers=True,
            )


# ===========================================================================
# 7. wipe_users_db (mock gspread) — run via asyncio.run()
# ===========================================================================

class TestWipeUsersDb:

    def test_confirm_false_raises_value_error(self):
        """wipe_users_db(confirm=False) must raise ValueError immediately."""
        async def run():
            await wipe_users_db(confirm=False)

        with pytest.raises(ValueError, match="confirm=True"):
            asyncio.run(run())

    def test_confirm_true_deletes_data_rows(self):
        """With 5 rows (header + 4 data), delete_rows must be called 4 times."""
        mock_sheet = MagicMock()
        mock_sheet.get_all_values.return_value = [
            ["Name", "Data"],   # row 1 = header
            ["Alice", "..."],   # row 2
            ["Bob",   "..."],   # row 3
            ["Carol", "..."],   # row 4
            ["Dave",  "..."],   # row 5
        ]
        mock_db = MagicMock()
        mock_db.get_sheet.return_value = mock_sheet

        async def run():
            with patch("database.sheets.db", mock_db), \
                 patch("config.TAB_USERS", "Users_DB"):
                return await wipe_users_db(confirm=True)

        deleted = asyncio.run(run())
        assert deleted == 4, f"Expected 4 rows deleted, got {deleted}"
        assert mock_sheet.delete_rows.call_count == 4

    def test_empty_sheet_returns_zero(self):
        """When Users_DB has only a header row, wipe must delete 0 rows."""
        mock_sheet = MagicMock()
        mock_sheet.get_all_values.return_value = [["Name", "Data"]]
        mock_db = MagicMock()
        mock_db.get_sheet.return_value = mock_sheet

        async def run():
            with patch("database.sheets.db", mock_db), \
                 patch("config.TAB_USERS", "Users_DB"):
                return await wipe_users_db(confirm=True)

        deleted = asyncio.run(run())
        assert deleted == 0
        mock_sheet.delete_rows.assert_not_called()

    def test_gspread_calls_go_through_to_thread(self):
        """The sync _sync_wipe closure must run inside asyncio.to_thread (CLAUDE.md §5.1)."""
        mock_sheet = MagicMock()
        mock_sheet.get_all_values.return_value = [["h"]]
        mock_db = MagicMock()
        mock_db.get_sheet.return_value = mock_sheet

        to_thread_calls: list = []

        async def spy_to_thread(fn, *args, **kwargs):
            to_thread_calls.append(True)
            # Execute the sync function so the mock is exercised
            return fn(*args, **kwargs)

        async def run():
            with patch("core.dnd_migration.asyncio.to_thread", side_effect=spy_to_thread), \
                 patch("database.sheets.db", mock_db), \
                 patch("config.TAB_USERS", "Users_DB"):
                return await wipe_users_db(confirm=True)

        asyncio.run(run())
        assert len(to_thread_calls) == 1, (
            "asyncio.to_thread must be called exactly once inside wipe_users_db"
        )

    def test_delete_rows_called_bottom_up(self):
        """Rows must be deleted bottom-up so gspread row indices stay stable."""
        mock_sheet = MagicMock()
        mock_sheet.get_all_values.return_value = [
            ["h"],   # row 1 header
            ["a"],   # row 2
            ["b"],   # row 3
            ["c"],   # row 4
        ]
        mock_db = MagicMock()
        mock_db.get_sheet.return_value = mock_sheet

        async def run():
            with patch("database.sheets.db", mock_db), \
                 patch("config.TAB_USERS", "Users_DB"):
                return await wipe_users_db(confirm=True)

        asyncio.run(run())
        expected_order = [call(4), call(3), call(2)]
        assert mock_sheet.delete_rows.call_args_list == expected_order, (
            f"Expected bottom-up deletion order {expected_order}, "
            f"got {mock_sheet.delete_rows.call_args_list}"
        )
