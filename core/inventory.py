"""
core/inventory.py
Inventory operations: parsing, normalization, item ops with quantity tracking.

Backward compatibility:
  - profile["Інвентар"] may be a str (old CSV format: "Рапіра, Лист x3"),
    a list[str], or (new) a list[dict {name, quantity}].
  - parse_inventory() normalises ALL three formats into list[dict].
  - format_inventory() serialises list[dict] back to a display string
    identical in style to the old CSV ("Рапіра, Лист x3").

Invariants (CLAUDE.md §5.4):
  - All operations are synchronous; no shared mutable state — per-user profile dict only.
  - quantity is always >= 1 after any operation.
"""

import re
from typing import Optional

# Matches "Item Name x3", "Item Name х3" (Cyrillic х), "Item Name × 3"
_ITEM_QTY_RE = re.compile(r"^(.+?)\s*[xхX×]\s*(\d+)\s*$")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_inventory(raw) -> list[dict]:
    """Convert inventory raw format → normalised list[{name, quantity}].

    Accepts:
      - None / empty string / empty list → []
      - str  : "Рапіра, Лист x3, Зілля"  → [{name:"Рапіра",quantity:1}, ...]
      - list[str] : ["Рапіра", "Лист x3"] → same result
      - list[dict]: [{name,quantity}, ...]  → returned as-is (already normalised)
      - Mixed list (dicts + strings) is handled element-by-element.

    quantity is always >= 1.
    """
    if not raw:
        return []

    if isinstance(raw, list):
        if not raw:
            return []
        # Detect already-structured format: first element is dict with "name" key
        result: list[dict] = []
        for item in raw:
            if isinstance(item, dict) and "name" in item:
                result.append({
                    "name": str(item["name"]).strip(),
                    "quantity": max(1, int(item.get("quantity", 1))),
                })
            elif isinstance(item, str):
                parsed = _parse_one(item)
                if parsed is not None:
                    result.append(parsed)
            # Other types silently skipped (defensive)
        return result

    if isinstance(raw, str):
        raw = raw.strip()
        if not raw or raw in ("-", "Пусто", "Нічого", "порожній"):
            return []
        parts = [s.strip() for s in raw.split(",") if s.strip()]
        return [p for s in parts if (p := _parse_one(s)) is not None]

    return []


def format_inventory(items: list[dict]) -> str:
    """Convert list[{name, quantity}] → display string.

    Examples:
      [{name:"Рапіра",quantity:1}, {name:"Лист",quantity:3}]
      → "Рапіра, Лист x3"

    Empty list → "Пусто"
    """
    if not items:
        return "Пусто"
    parts = []
    for item in items:
        name = item.get("name", "")
        qty = int(item.get("quantity", 1))
        if qty > 1:
            parts.append(f"{name} x{qty}")
        else:
            parts.append(name)
    return ", ".join(parts)


def add_item(inventory: list[dict], name: str, quantity: int = 1) -> None:
    """Add item(s) to inventory. Mutates in-place.

    If an item with the same name (case-insensitive) already exists,
    increments its quantity. Otherwise appends a new entry.

    quantity floor: 1 (adding 0 or negative is a no-op guard).
    """
    name = name.strip()
    quantity = max(1, quantity)
    for item in inventory:
        if item["name"].lower() == name.lower():
            item["quantity"] += quantity
            return
    inventory.append({"name": name, "quantity": quantity})


def remove_item(inventory: list[dict], name: str, quantity: int = 1) -> int:
    """Remove item(s) from inventory. Mutates in-place.

    Returns the number of items actually removed:
      - 0  if item not found
      - min(available, quantity) otherwise

    If quantity decremented to 0 — the entire entry is removed from the list.
    """
    name = name.strip()
    quantity = max(1, quantity)
    for i, item in enumerate(inventory):
        if item["name"].lower() == name.lower():
            available = item["quantity"]
            to_remove = min(available, quantity)
            item["quantity"] -= to_remove
            if item["quantity"] <= 0:
                inventory.pop(i)
            return to_remove
    return 0


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _parse_one(s: str) -> Optional[dict]:
    """Parse a single item string like "Лист x3" or "Рапіра" into a dict.

    Returns None for empty/whitespace-only strings.
    """
    s = s.strip()
    if not s:
        return None
    m = _ITEM_QTY_RE.match(s)
    if m:
        return {"name": m.group(1).strip(), "quantity": int(m.group(2))}
    return {"name": s, "quantity": 1}
