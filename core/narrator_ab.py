"""Narrator A/B: сліпе порівняння Gemma vs Flash-Lite.

Чистий модуль стану + логування. НЕ імпортує core.engine.
In-memory стан (`_pending`, `_last_turn_id`) -- за §5.4 код, що його торкається, має
звільняти/pop-ати його в try/finally.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from config import (
    MODEL_NARRATOR_NAME,
    MODEL_NARRATOR_ALT_NAME,
    NARRATOR_AB_CHOICE_TTL,
    NARRATOR_AB_LOG_PATH,
    NARRATOR_AB_SINK,
)

logger = logging.getLogger(__name__)

MODEL_KEYS = {"gemma": MODEL_NARRATOR_NAME, "flash_lite": MODEL_NARRATOR_ALT_NAME}


@dataclass
class NarrationResult:
    model_key: str
    text: str | None
    total_ms: int
    attempt_ms: list[int] = field(default_factory=list)
    final_attempt: int | str | None = None  # 1/2/3 or "fallback"
    used_fallback: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return bool(self.text) and not self.used_fallback and self.error is None


@dataclass
class PendingChoice:
    turn_id: str
    chat_id: int
    user_id: int
    created: float  # time.time()
    order: list[NarrationResult]  # blind display order; order[0] = "Варіант 1"
    change_log: str  # "\n\n📊 ..." part, may be ""
    suggested_actions: list
    deferred_history: dict  # {"user_input": str, "mech_updates": dict}
    log_record: dict
    variant_message_ids: list[int] = field(default_factory=list)
    display_intent: str = ""  # intent resolved from a "👉" button; shown as 🗣️ prefix (display only)


_pending: dict[int, PendingChoice] = {}
_last_turn_id: dict[int, str] = {}
_log_lock = asyncio.Lock()


def new_turn_id() -> str:
    return secrets.token_hex(4)


def set_pending(p: PendingChoice) -> None:
    _pending[p.chat_id] = p
    _last_turn_id[p.chat_id] = p.turn_id


def get_pending(chat_id) -> PendingChoice | None:
    return _pending.get(chat_id)


def pop_pending(chat_id, turn_id: str | None = None) -> PendingChoice | None:
    p = _pending.get(chat_id)
    if p is None:
        return None
    if turn_id is not None and p.turn_id != turn_id:
        return None
    return _pending.pop(chat_id, None)


def is_expired(p: PendingChoice, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    return (now - p.created) > NARRATOR_AB_CHOICE_TTL


def last_turn_id(chat_id) -> str | None:
    return _last_turn_id.get(chat_id)


def shuffle_variants(a, b) -> list:
    return random.sample([a, b], 2)


def resolve_pick(p: PendingChoice, pick: str) -> tuple[NarrationResult, str | None]:
    """pick in {"1","2","tie"} -> (winner, vote). Tie: випадковий winner, vote="tie"."""
    if pick == "1":
        w = p.order[0]
        return w, w.model_key
    if pick == "2":
        w = p.order[1]
        return w, w.model_key
    if pick == "tie":
        return random.choice(p.order), "tie"
    raise ValueError(f"invalid pick: {pick!r}")


def _result_dict(r: NarrationResult) -> dict:
    return {
        "text": r.text,
        "len": len(r.text) if r.text else 0,
        "total_ms": r.total_ms,
        "attempt_ms": list(r.attempt_ms),
        "final_attempt": r.final_attempt,
        "used_fallback": r.used_fallback,
        "error": r.error,
    }


def build_log_record(*, turn_id, user_id, chat_id, mode, narrator_prompt, mechanics: dict,
                     results: list[NarrationResult], shown_order: list[str] | None,
                     reason: str | None = None) -> dict:
    return {
        "type": "turn",
        "turn_id": turn_id,
        "ts": datetime.now(timezone.utc).isoformat(),
        "user_id": user_id,
        "chat_id": chat_id,
        "mode": mode,
        "narrator_prompt": narrator_prompt,
        "mechanics": mechanics,
        "shown_order": shown_order,
        "results": {r.model_key: _result_dict(r) for r in results},
        "vote": None,
        "vote_raw": None,
        "vote_ms": None,
        "reason": reason,
    }


def finalize_record(record: dict, *, vote: str | None, vote_raw: str | None,
                    vote_ms: int | None, reason: str | None = None) -> dict:
    record["vote"] = vote
    record["vote_raw"] = vote_raw
    record["vote_ms"] = vote_ms
    if reason is not None:
        record["reason"] = reason
    return record


def _write_line(path: str, line: str) -> None:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


async def append_log(record: dict) -> None:
    """Append record to the configured sink(s) (file JSONL and/or AB_Log sheet). Never raises."""
    sink = NARRATOR_AB_SINK
    if sink in ("file", "both"):
        try:
            line = json.dumps(record, ensure_ascii=False, default=str)
            async with _log_lock:
                await asyncio.to_thread(_write_line, NARRATOR_AB_LOG_PATH, line)
        except Exception as e:  # noqa: BLE001
            logger.warning("narrator_ab: failed to append log: %s", e)
    if sink in ("sheets", "both"):
        try:
            task = asyncio.create_task(_sheets_append(dict(record)))
            _pending_sheet_tasks.add(task)
            task.add_done_callback(_pending_sheet_tasks.discard)
        except Exception as e:  # noqa: BLE001
            logger.warning("narrator_ab: failed to schedule AB_Log row: %s", e)


_pending_sheet_tasks: set = set()
SHEETS_APPEND_TIMEOUT = 30


async def _sheets_append(record: dict) -> None:
    """Fire-and-forget Sheets leg: never raises, bounded by SHEETS_APPEND_TIMEOUT."""
    try:
        from database.operations import append_ab_log_row
        await asyncio.wait_for(append_ab_log_row(record), timeout=SHEETS_APPEND_TIMEOUT)
    except BaseException as e:  # noqa: BLE001
        if isinstance(e, asyncio.CancelledError):
            raise
        logger.warning("narrator_ab: failed to append AB_Log row: %s: %s", type(e).__name__, e)


async def drain_pending_sheet_writes(timeout: float = 10) -> None:
    """Await outstanding Sheets writes (tests/shutdown). Swallows errors and timeout."""
    tasks = list(_pending_sheet_tasks)
    if not tasks:
        return
    try:
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=timeout)
    except Exception as e:  # noqa: BLE001
        logger.warning("narrator_ab: drain_pending_sheet_writes: %s", type(e).__name__)


async def append_note(chat_id, user_id, note: str) -> bool:
    tid = last_turn_id(chat_id)
    if tid is None:
        return False
    await append_log({
        "type": "note",
        "ts": datetime.now(timezone.utc).isoformat(),
        "chat_id": chat_id,
        "user_id": user_id,
        "turn_id": tid,
        "note": note,
    })
    return True
