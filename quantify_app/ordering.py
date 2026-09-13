"""Turning a forecast into a shopping list.

The forecast says how many of each menu item will sell. The menu composition
says what each item is physically made of. Between them you can say how much
beef, how many buns, how much mozzarella a week is going to consume. That is
the arithmetic in this file.

Three things it is careful about, because getting any of them wrong makes the
whole screen untrustworthy:

1. **A portion is not a purchase.** You do not buy lettuce in portions, you buy
   it in cases. Usage is computed in whatever unit the recipe speaks, and it is
   only turned into an order once somebody has said how they actually buy the
   thing. Until then the screen says what will be used and asks. It never
   invents a case count.
2. **A range is a range.** "1 to 2 patties" is the honest reading of a burger
   line that covers singles and doubles. It is carried as a low and a high, not
   flattened to one number and presented as fact.
3. **Nothing here knows what is in the walk-in.** This is consumption, not a
   replenishment order, until an operator says what is on hand. Every figure is
   labelled that way.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from .intelligence import forecast_day

# Units the recipe lines speak, and how to add them up. Anything countable is
# left alone. Weights and volumes are normalised so 500 g and 2 lb can be
# summed, then reported back in whatever unit reads best at that size.
MASS_IN_GRAMS = {"g": 1.0, "kg": 1000.0, "oz": 28.3495, "lb": 453.592}
VOLUME_IN_ML = {"ml": 1.0, "l": 1000.0, "cup": 236.588, "floz": 29.5735}

# Units that describe a share of something, not a thing you can order.
# Frying oil is real, but "one basket share" is not a line on an invoice, so it
# is reported separately and never pretended into a case count.
UNCOUNTABLE = {"basket share", "share", "pinch", "coat", "part"}

# Words a till label adds to an ingredient that do not change what gets bought.
# "Burger bun" and "Bun" are one order line; "Wrap" and "Wrap and box" are not.
_MERGE_DROP = {"burger", "hamburger", "sandwich", "the", "a", "an", "of"}

# Plural forms the recipe lines use, mapped back to one word so "2 slices" and
# "1 slice" land in the same bucket.
_SINGULAR = {
    "patties": "patty", "slices": "slice", "eggs": "egg", "buns": "bun",
    "rolls": "roll", "balls": "ball", "boxes": "box", "bags": "bag",
    "cartons": "carton", "cups": "cup", "portions": "portion", "sets": "set",
    "sleeves": "sleeve", "plates": "plate", "wraps": "wrap", "wrappers": "wrapper",
    "scoops": "scoop", "fillets": "fillet", "containers": "container",
    "pinches": "pinch", "coats": "coat", "baskets": "basket",
}

_NUMBER = r"(\d+(?:\.\d+)?)"
_RANGE = re.compile(_NUMBER + r"\s*(?:to|-)\s*" + _NUMBER + r"\s*([a-z ]+)", re.I)
_SINGLE = re.compile(_NUMBER + r"\s*([a-z ]+)", re.I)


@dataclass
class Amount:
    """A quantity per menu item, carried as a range because recipes are ranges."""

    low: float
    high: float
    unit: str
    exact: bool = True

    @property
    def mid(self) -> float:
        return (self.low + self.high) / 2.0


def _normalise_unit(text: str) -> str:
    unit = " ".join(str(text).strip().lower().split())
    unit = _SINGULAR.get(unit, unit)
    if unit in {"fl oz", "fluid oz"}:
        return "floz"
    if unit in {"litre", "liter"}:
        return "l"
    return unit


def parse_quantity(text: str) -> Amount | None:
    """Read a recipe line like "about 4 oz" or "1 to 2 patties" into an amount.

    Returns None for a line with no number in it, which is a line the ordering
    screen has to ask about instead of guessing.
    """
    if not text:
        return None
    raw = str(text).strip().lower()

    exact = True
    # "2 slices or 1 roll" describes two ways of building the same thing. The
    # first branch is taken and the line is marked inexact, because which one a
    # kitchen actually uses is not something a register label can tell you.
    if " or " in raw:
        raw = raw.split(" or ")[0].strip()
        exact = False
    if raw.startswith("about ") or raw.startswith("around "):
        raw = raw.split(" ", 1)[1]
        exact = False
    if raw.startswith("up to "):
        # Stocking for the maximum is the only safe reading of a drink size.
        raw = raw[6:].strip()
        exact = False
    if raw.startswith("part of "):
        parsed = _SINGLE.match(raw[8:].strip())
        if not parsed:
            return None
        value = float(parsed.group(1))
        return Amount(0.25 * value, 0.75 * value, _normalise_unit(parsed.group(2)), exact=False)

    ranged = _RANGE.match(raw)
    if ranged:
        return Amount(
            float(ranged.group(1)), float(ranged.group(2)),
            _normalise_unit(ranged.group(3)), exact=False,
        )

    single = _SINGLE.match(raw)
    if single:
        value = float(single.group(1))
        return Amount(value, value, _normalise_unit(single.group(2)), exact=exact)
    return None


def _kind(unit: str) -> str:
    if unit in MASS_IN_GRAMS:
        return "mass"
    if unit in VOLUME_IN_ML:
        return "volume"
    if unit in UNCOUNTABLE or any(word in unit for word in UNCOUNTABLE):
        return "share"
    return "count"


def _to_base(amount: float, unit: str) -> float:
    if unit in MASS_IN_GRAMS:
        return amount * MASS_IN_GRAMS[unit]
    if unit in VOLUME_IN_ML:
        return amount * VOLUME_IN_ML[unit]
    return amount


def merge_key(name: str, unit: str = "") -> str:
    """The key two spellings of one ingredient share.

    Lower case, one word form ("patties" and "patty" are the same), and the
    qualifiers a till label adds ("burger bun", "bun") dropped. The unit is part
    of the key, so "Bread" by the slice and "Bread or roll" by the roll stay
    apart: they are different things on an order.
    """
    words = [_SINGULAR.get(word, word) for word in re.split(r"[^a-z0-9]+", str(name).lower()) if word]
    kept = [word for word in words if word not in _MERGE_DROP] or words
    return " ".join(kept) + ("|" + _normalise_unit(unit) if unit else "")


def readable(base: float, kind: str, unit: str) -> tuple[float, str]:
    """Report a total back in whatever unit a person would say out loud.

    Countable things come back whole: nobody orders four and a half patties.
    """
    if kind == "mass":
        if base >= 907.184:
            return round(base / 453.592, 1), "lb"
        if base >= 1000:
            return round(base / 1000.0, 2), "kg"
        return round(base), "g"
    if kind == "volume":
        if base >= 3785.41:
            return round(base / 3785.41, 1), "gal"
        if base >= 1000:
            return round(base / 1000.0, 1), "l"
        return round(base), "ml"
    return int(round(base)), unit


@dataclass
class Line:
    """One ingredient, rolled up across every menu item that uses it."""

    name: str
    role: str
    unit: str
    kind: str
    low: float = 0.0
    high: float = 0.0
    exact: bool = True
    from_items: dict[str, float] = field(default_factory=dict)
    # Mid usage in the base unit for each day of the window, in order.
    daily: list[float] = field(default_factory=list)
    # How many menu items use each spelling, so the line can be called what
    # most of the menu calls it.
    spellings: dict[str, int] = field(default_factory=dict)

    def add(self, amount: Amount, servings: float, item_name: str, offset: int, spelling: str) -> None:
        self.low += _to_base(amount.low, amount.unit) * servings
        self.high += _to_base(amount.high, amount.unit) * servings
        self.exact = self.exact and amount.exact
        mid = _to_base(amount.mid, amount.unit) * servings
        self.from_items[item_name] = self.from_items.get(item_name, 0.0) + mid
        while len(self.daily) <= offset:
            self.daily.append(0.0)
        self.daily[offset] += mid
        if offset == 0:
            self.spellings[spelling] = self.spellings.get(spelling, 0) + 1

    @property
    def shown_name(self) -> str:
        """The spelling most of the menu uses; ties go to the longer one."""
        if not self.spellings:
            return self.name
        return max(self.spellings, key=lambda word: (self.spellings[word], len(word)))


def _compositions(conn: sqlite3.Connection, location_id: str) -> dict[str, list[dict[str, Any]]]:
    """What every item here is made of, whether or not anyone has opened Menu.

    A stored composition wins, so anything the owner has corrected is used. For
    the rest the same local reader that would have written one runs here in
    memory. Nothing is written back: this is a read path, and a buying list must
    not depend on somebody having visited another screen first.
    """
    from .explain import local_composition

    rows = conn.execute(
        """SELECT m.id, m.name, i.normalized_name, i.item_family, i.confidence, c.components_json
           FROM menu_items m
           LEFT JOIN menu_interpretations i ON i.menu_item_id = m.id
           LEFT JOIN item_composition c ON c.menu_item_id = m.id
           WHERE m.location_id=? AND m.active=1""",
        (location_id,),
    ).fetchall()
    out: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        parts: list[dict[str, Any]] = []
        if row["components_json"]:
            try:
                parts = json.loads(row["components_json"]) or []
            except (TypeError, json.JSONDecodeError):
                parts = []
        if not parts:
            parts = local_composition({
                "raw_name": row["name"],
                "normalized_name": row["normalized_name"] or row["name"],
                "family": row["item_family"] or "menu-item",
                "interpretation_confidence": float(row["confidence"] or 0),
            })["components"]
        out[row["id"]] = parts
    return out


def ingredient_demand(
    conn: sqlite3.Connection, location_id: str, start: date, days: int = 3
) -> dict[str, Any]:
    """How much of each ingredient the next few days are forecast to consume.

    This is consumption, not an order. It does not know what is already in the
    walk-in and it says so. What it does know is what the register is going to
    ask for, which is the half of the arithmetic nobody currently has.
    """
    days = max(1, min(14, days))
    lines: dict[str, Line] = {}
    unknown: list[dict[str, Any]] = []
    covered = uncovered = 0
    per_day: list[dict[str, Any]] = []
    composition = _compositions(conn, location_id)

    for offset in range(days):
        target = start + timedelta(days=offset)
        plan = forecast_day(conn, location_id, target)
        per_day.append({
            "date": target.isoformat(),
            "weekday": target.strftime("%A"),
            "expected_units": plan["summary"]["expected_units"],
            "expected_revenue": plan["summary"]["expected_revenue"],
        })
        for item in plan["items"]:
            # Order to what will be made, not to what will be sold. The prep
            # number already carries the cost of running out.
            servings = float(item.get("make", item["expected"]))
            parts = composition.get(item["item_id"]) or []
            if not parts:
                if offset == 0:
                    uncovered += 1
                    unknown.append({
                        "item_id": item["item_id"], "name": item["name"],
                        "servings": round(servings),
                        "reason": "nothing on file for what this is made of",
                    })
                continue
            if offset == 0:
                covered += 1
            for part in parts:
                name = str(part.get("name", "")).strip()
                if not name:
                    continue
                amount = parse_quantity(part.get("quantity", ""))
                if amount is None:
                    if offset == 0:
                        unknown.append({
                            "item_id": item["item_id"], "name": item["name"] + ": " + name,
                            "servings": round(servings),
                            "reason": "the recipe line has no quantity in it",
                        })
                    continue
                key = merge_key(name, amount.unit)
                line = lines.get(key)
                if line is None:
                    line = Line(
                        name=name, role=str(part.get("role", "")),
                        unit=amount.unit, kind=_kind(amount.unit),
                    )
                    lines[key] = line
                line.add(amount, servings, item["name"], offset, name)

    out = []
    for line in lines.values():
        low, unit = readable(line.low, line.kind, line.unit)
        high, _ = readable(line.high, line.kind, line.unit)
        typical, _ = readable((line.low + line.high) / 2.0, line.kind, line.unit)
        drivers = sorted(line.from_items.items(), key=lambda kv: -kv[1])[:3]
        total = sum(line.from_items.values()) or 1.0
        shown = line.shown_name
        out.append({
            "name": shown,
            # Every spelling that rolled into this line, lower case, so a count
            # or a supplier saved under "burger bun" still finds "Bun".
            "aliases": sorted({word.lower() for word in line.spellings} | {line.name.lower(), shown.lower()}),
            "role": line.role,
            "kind": line.kind,
            "unit": unit,
            "low": low,
            "high": high,
            "typical": typical,
            "daily_base": [round(value, 3) for value in line.daily],
            # Two different kinds of doubt, kept apart. `has_range` means the
            # recipe genuinely spans two amounts, like a single or a double.
            # `certain` means the line was read straight, with no interpreting.
            "has_range": abs(line.high - line.low) > 1e-9,
            "certain": line.exact,
            "base_low": round(line.low, 3),
            "base_high": round(line.high, 3),
            "driven_by": [
                {"item": name, "share_percent": round(value / total * 100)}
                for name, value in drivers
            ],
        })
    # Biggest consumers first, countable things above shares, because a case of
    # buns is a decision and a share of frying oil is not.
    order = {"count": 0, "mass": 1, "volume": 2, "share": 3}
    out.sort(key=lambda row: (order.get(row["kind"], 9), -row["typical"]))

    return {
        "location_id": location_id,
        "start": start.isoformat(),
        "end": (start + timedelta(days=days - 1)).isoformat(),
        "days": days,
        "per_day": per_day,
        "lines": out,
        "unknown": unknown[:12],
        "items_covered": covered,
        "items_uncovered": uncovered,
        "basis": (
            "Covers what the forecast says you will make over these days. "
            "Subtract whatever is already in the walk-in."
        ),
    }


def _recipe_backlog(
    conn: sqlite3.Connection, location_id: str, unknown: list[dict[str, Any]], days: int
) -> list[dict[str, Any]]:
    """What adding one recipe would be worth, in money, ranked.

    Recipe entry is the single most cited reason this kind of software gets
    abandoned, so nothing here is required. This is a list the owner can work
    through when it suits them, ordered by what each one is actually worth. It
    never blocks the screen.
    """
    if not unknown:
        return []
    prices = {
        row["id"]: (float(row["price"]), row["name"])
        for row in conn.execute(
            "SELECT id, price, name FROM menu_items WHERE location_id=?", (location_id,)
        )
    }
    out = []
    for row in unknown:
        item_id = row.get("item_id")
        if item_id not in prices:
            continue
        price, name = prices[item_id]
        # Scale the window up to a month so the figure means something.
        monthly = float(row.get("servings") or 0) * (30.0 / max(1, days))
        out.append({
            "item_id": item_id,
            "name": name,
            "monthly_units": int(round(monthly)),
            "monthly_value": round(monthly * price, 2),
            "reason": row.get("reason", ""),
        })
    out.sort(key=lambda r: -r["monthly_value"])
    return out[:6]


def order_plan(
    conn: sqlite3.Connection, location_id: str, start: date, days: int = 3
) -> dict[str, Any]:
    """The buying list for a window, with everything it does not know said out loud.

    Two kinds of line, never blended. A **forecast line** has a recipe behind it,
    so the quantity comes from what the forecast says will be made. Everything
    else on a real order guide, the to go boxes, the fryer oil, the blue roll,
    is not in any recipe and never will be, and those become pattern lines once
    supplier order guides are connected.

    There is no supplier and no pack size in the database yet, so this returns
    usage in the unit the recipe speaks and says so. It will not invent a case
    count before somebody has said how the thing is bought.
    """
    demand = ingredient_demand(conn, location_id, start, days)
    if not demand.get("lines") and not demand.get("unknown"):
        return {**demand, "ready": False, "backlog": [], "counts": {}}

    context = None
    try:
        from .costs import cost_context

        context = cost_context(conn, location_id)
    except Exception:  # costing must never stop the buying list rendering
        context = None

    covered = demand["items_covered"]
    total_items = covered + demand["items_uncovered"]
    for line in demand["lines"]:
        line["orderable"] = line["kind"] != "share"
        line["note"] = (
            "Used as a share of a bigger thing, like a basket of oil, so there is no case count."
            if line["kind"] == "share" else ""
        )

    return {
        **demand,
        "ready": True,
        "counts": {
            "menu_items": total_items,
            "covered": covered,
            "uncovered": demand["items_uncovered"],
            "lines": len([row for row in demand["lines"] if row["kind"] != "share"]),
        },
        "backlog": _recipe_backlog(conn, location_id, demand["unknown"], days),
        "wage_note": (context or {}).get("wage", {}).get("detail", ""),
    }
