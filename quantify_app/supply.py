"""Who the kitchen buys from, and whether a thing runs out before the truck comes.

The ordering module says what the register is going to consume. This one adds
the three facts that turn consumption into an order: who sells the thing, how
it is bought, and how much is already on the shelf. None of them can be read
off a till, so each one is asked for once and then kept.

The date arithmetic is deliberately dull. A supplier delivers on some days of
the week, needs the order in by a certain time, and takes a number of days to
arrive. From those and the day an ingredient runs out there is exactly one
latest moment to place the order, and that is what the screen shows.
"""

from __future__ import annotations

import html
import json
import math
import sqlite3
import uuid
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from . import localtime
from .ordering import MASS_IN_GRAMS, VOLUME_IN_ML, _normalise_unit, order_plan, readable

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WEEKDAY_LABELS = {"mon": "Mon", "tue": "Tue", "wed": "Wed", "thu": "Thu", "fri": "Fri", "sat": "Sat", "sun": "Sun"}
PACK_LABELS = ["case", "box", "bag", "tray", "flat", "tub", "bucket", "sleeve", "carton", "sack", "pack", "each"]
CHANNELS = {"email", "mail-app", "site", "copy"}

# The volume table in ordering.py stops at litres. Kitchens buy in gallons.
_VOLUME = dict(VOLUME_IN_ML) | {"gal": 3785.41, "gallon": 3785.41, "gallons": 3785.41, "qt": 946.353, "quart": 946.353, "quarts": 946.353}
_MASS = dict(MASS_IN_GRAMS) | {"lbs": 453.592, "pound": 453.592, "pounds": 453.592, "ounce": 28.3495, "ounces": 28.3495, "kilo": 1000.0, "kilos": 1000.0}

ATTENTION_DAYS = 7
_CACHE: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}
_CACHE_SECONDS = 300.0


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean(value: Any, limit: int = 120) -> str:
    return " ".join(str(value or "").split())[:limit]


def _number(value: Any, low: float, high: float, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    if number != number:  # NaN
        return fallback
    return max(low, min(high, number))


def _location(conn: sqlite3.Connection, location_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT id, name, city, region, timezone, open_hour, close_hour FROM locations WHERE id=?",
        (location_id,),
    ).fetchone()
    if row is None:
        raise ValueError("Unknown location")
    return row


def local_now(conn: sqlite3.Connection, location_id: str) -> datetime:
    return localtime.now(_location(conn, location_id)["timezone"])


def _kind_of_unit(unit: str) -> str:
    unit = _normalise_unit(unit)
    if unit in _MASS:
        return "mass"
    if unit in _VOLUME:
        return "volume"
    return "count"


def to_base(value: float, unit: str, kind: str) -> float:
    """A quantity in any unit the kitchen uses, in the base unit for its kind."""
    unit = _normalise_unit(unit)
    if kind == "mass":
        return float(value) * _MASS.get(unit, 1.0)
    if kind == "volume":
        return float(value) * _VOLUME.get(unit, 1.0)
    return float(value)


def from_base(base: float, unit: str, kind: str) -> float:
    unit = _normalise_unit(unit)
    if kind == "mass":
        return float(base) / _MASS.get(unit, 1.0)
    if kind == "volume":
        return float(base) / _VOLUME.get(unit, 1.0)
    return float(base)


def _tidy(value: float) -> float | int:
    """A number a person would write down: whole when it is whole, else one decimal."""
    if value >= 100:
        return int(round(value))
    rounded = round(value, 1)
    return int(rounded) if abs(rounded - round(rounded)) < 1e-9 else rounded


def _clock_label(value: str) -> str:
    """"15:00" reads as "3 PM"; "07:30" as "7:30 AM"."""
    try:
        hour, minute = (int(part) for part in value.split(":")[:2])
    except (ValueError, AttributeError):
        return ""
    suffix = "AM" if hour < 12 else "PM"
    shown = hour % 12 or 12
    return f"{shown} {suffix}" if minute == 0 else f"{shown}:{minute:02d} {suffix}"


def _day_label(day: date, today: date) -> str:
    """Today, tomorrow, a weekday inside the week, a date beyond it."""
    gap = (day - today).days
    if gap == 0:
        return "today"
    if gap == 1:
        return "tomorrow"
    if 1 < gap < 7:
        return day.strftime("%A")
    return day.strftime("%b %-d") if hasattr(date, "strftime") and _supports_dash() else day.strftime("%b %d").replace(" 0", " ")


def _supports_dash() -> bool:
    try:
        date(2026, 1, 5).strftime("%-d")
        return True
    except ValueError:
        return False


def _parse_time(value: str) -> time | None:
    try:
        hour, minute = (int(part) for part in str(value).split(":")[:2])
        return time(hour, minute)
    except (ValueError, TypeError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# Suppliers
# ---------------------------------------------------------------------------

def _supplier_dict(row: sqlite3.Row) -> dict[str, Any]:
    days = [d for d in str(row["delivery_days"] or "").split(",") if d in WEEKDAY_LABELS]
    return {
        "id": row["id"],
        "name": row["name"],
        "rep_name": row["rep_name"],
        "order_email": row["order_email"],
        "phone": row["phone"],
        "website": row["website"],
        "account_number": row["account_number"],
        "delivery_days": days,
        "delivers": ", ".join(WEEKDAY_LABELS[d] for d in days) if days else "any day",
        "cutoff_time": row["cutoff_time"],
        "cutoff_label": _clock_label(row["cutoff_time"]) if row["cutoff_time"] else "",
        "lead_days": int(row["lead_days"] or 0),
        "notes": row["notes"],
        "updated_at": row["updated_at"],
    }


def list_suppliers(conn: sqlite3.Connection, location_id: str, now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or local_now(conn, location_id)
    rows = conn.execute(
        "SELECT * FROM suppliers WHERE location_id=? ORDER BY lower(name)", (location_id,)
    ).fetchall()
    last = last_orders(conn, location_id)
    out = []
    for row in rows:
        supplier = _supplier_dict(row)
        supplier["schedule"] = schedule(supplier, now)
        supplier["last_order"] = last.get(supplier["id"])
        out.append(supplier)
    return out


def get_supplier(conn: sqlite3.Connection, location_id: str, supplier_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM suppliers WHERE location_id=? AND id=?", (location_id, supplier_id)
    ).fetchone()
    return _supplier_dict(row) if row else None


def _website(value: Any) -> str:
    text = _clean(value, 300)
    if not text:
        return ""
    if not text.lower().startswith(("http://", "https://")):
        text = "https://" + text
    if not text.lower().startswith(("http://", "https://")):
        return ""
    return text


def save_supplier(conn: sqlite3.Connection, location_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    name = _clean(payload.get("name"), 80)
    if len(name) < 2:
        raise ValueError("Give the supplier a name")
    days = [d for d in (payload.get("delivery_days") or []) if d in WEEKDAY_LABELS]
    days = [d for d in WEEKDAYS if d in days]
    cutoff = _clean(payload.get("cutoff_time"), 5)
    if cutoff and _parse_time(cutoff) is None:
        raise ValueError("The order cutoff should be a time like 15:00")
    now = _utc_now()
    supplier_id = _clean(payload.get("id"), 40)
    values = (
        name, _clean(payload.get("rep_name"), 80), _clean(payload.get("order_email"), 120).lower(),
        _clean(payload.get("phone"), 40), _website(payload.get("website")),
        _clean(payload.get("account_number"), 60), ",".join(days), cutoff,
        int(_number(payload.get("lead_days"), 0, 14, 1)), _clean(payload.get("notes"), 400), now,
    )
    existing = conn.execute(
        "SELECT id FROM suppliers WHERE location_id=? AND id=?", (location_id, supplier_id)
    ).fetchone() if supplier_id else None
    if existing:
        conn.execute(
            """UPDATE suppliers SET name=?, rep_name=?, order_email=?, phone=?, website=?, account_number=?,
                   delivery_days=?, cutoff_time=?, lead_days=?, notes=?, updated_at=?
               WHERE id=? AND location_id=?""",
            values + (supplier_id, location_id),
        )
    else:
        supplier_id = uuid.uuid4().hex
        conn.execute(
            """INSERT INTO suppliers(id, location_id, name, rep_name, order_email, phone, website,
                   account_number, delivery_days, cutoff_time, lead_days, notes, created_at, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (supplier_id, location_id) + values[:-1] + (now, now),
        )
    conn.commit()
    _CACHE.clear()
    saved = get_supplier(conn, location_id, supplier_id)
    assert saved is not None
    saved["schedule"] = schedule(saved, local_now(conn, location_id))
    return saved


def delete_supplier(conn: sqlite3.Connection, location_id: str, supplier_id: str) -> None:
    conn.execute("DELETE FROM suppliers WHERE location_id=? AND id=?", (location_id, supplier_id))
    conn.commit()
    _CACHE.clear()


# ---------------------------------------------------------------------------
# Delivery arithmetic
# ---------------------------------------------------------------------------

def _delivers_on(supplier: dict[str, Any] | None, day: date) -> bool:
    days = (supplier or {}).get("delivery_days") or []
    return not days or WEEKDAYS[day.weekday()] in days


def _order_by(supplier: dict[str, Any] | None, delivery: date) -> datetime:
    """The last moment an order can go in and still arrive on `delivery`."""
    lead = int((supplier or {}).get("lead_days") or (1 if supplier is None else 0))
    cutoff = _parse_time((supplier or {}).get("cutoff_time") or "") or time(23, 59)
    return datetime.combine(delivery - timedelta(days=lead), cutoff)


def schedule(supplier: dict[str, Any] | None, now: datetime) -> dict[str, Any]:
    """The next delivery that can still be ordered, and when the order has to go in."""
    today = now.date()
    naive = now.replace(tzinfo=None)
    for offset in range(0, 21):
        day = today + timedelta(days=offset)
        if not _delivers_on(supplier, day):
            continue
        by = _order_by(supplier, day)
        if by >= naive:
            return {
                "next_delivery": day.isoformat(),
                "next_delivery_label": _day_label(day, today),
                "order_by": by.isoformat(timespec="minutes"),
                "order_by_label": _when_label(by, today),
                "closes_soon": (by - naive) <= timedelta(hours=3),
            }
    return {"next_delivery": "", "next_delivery_label": "", "order_by": "", "order_by_label": "", "closes_soon": False}


def _when_label(moment: datetime, today: date) -> str:
    day = _day_label(moment.date(), today)
    clock = _clock_label(moment.strftime("%H:%M"))
    if clock == "11:59 PM":
        return day
    return f"{day} by {clock}"


def order_by_for_runout(supplier: dict[str, Any] | None, runs_out_on: date, now: datetime) -> dict[str, Any]:
    """When to order so the delivery lands before the shelf is empty.

    Works back from the run-out day to the latest delivery that still lands in
    time. When no delivery can make it, says so and gives the first one that can.
    """
    today = now.date()
    naive = now.replace(tzinfo=None)
    day = runs_out_on
    while day >= today:
        if _delivers_on(supplier, day):
            by = _order_by(supplier, day)
            if by >= naive:
                return {
                    "order_by": by.isoformat(timespec="minutes"),
                    "order_by_label": _when_label(by, today),
                    "arrives": day.isoformat(),
                    "arrives_label": _day_label(day, today),
                    "late": False,
                    "days_short": 0,
                    "urgent": (by - naive) <= timedelta(hours=24),
                }
        day -= timedelta(days=1)
    soonest = schedule(supplier, now)
    arrives = date.fromisoformat(soonest["next_delivery"]) if soonest["next_delivery"] else None
    return {
        "order_by": soonest["order_by"],
        "order_by_label": "now",
        "arrives": soonest["next_delivery"],
        "arrives_label": soonest["next_delivery_label"],
        "late": True,
        "days_short": max(0, (arrives - runs_out_on).days) if arrives else 0,
        "urgent": True,
    }


# ---------------------------------------------------------------------------
# How each ingredient is bought, and what is on the shelf
# ---------------------------------------------------------------------------

def item_settings(conn: sqlite3.Connection, location_id: str) -> dict[str, dict[str, Any]]:
    rows = conn.execute("SELECT * FROM supplier_items WHERE location_id=?", (location_id,)).fetchall()
    return {
        row["ingredient"]: {
            "supplier_id": row["supplier_id"] or "",
            "pack_size": float(row["pack_size"] or 0),
            "pack_unit": row["pack_unit"],
            "pack_label": row["pack_label"] or "case",
            "product_code": row["product_code"],
        }
        for row in rows
    }


def save_item(conn: sqlite3.Connection, location_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    ingredient = _clean(payload.get("ingredient"), 120).lower()
    if not ingredient:
        raise ValueError("Which ingredient?")
    supplier_id = _clean(payload.get("supplier_id"), 40)
    if supplier_id and not get_supplier(conn, location_id, supplier_id):
        raise ValueError("That supplier is not on this location")
    pack_size = _number(payload.get("pack_size"), 0, 1_000_000, 0)
    pack_unit = _normalise_unit(_clean(payload.get("pack_unit"), 30))
    pack_label = _clean(payload.get("pack_label"), 30).lower() or "case"
    conn.execute(
        """INSERT INTO supplier_items(location_id, ingredient, supplier_id, pack_size, pack_unit, pack_label, product_code, updated_at)
           VALUES(?,?,?,?,?,?,?,?)
           ON CONFLICT(location_id, ingredient) DO UPDATE SET
               supplier_id=excluded.supplier_id, pack_size=excluded.pack_size, pack_unit=excluded.pack_unit,
               pack_label=excluded.pack_label, product_code=excluded.product_code, updated_at=excluded.updated_at""",
        (location_id, ingredient, supplier_id or None, pack_size, pack_unit, pack_label,
         _clean(payload.get("product_code"), 60), _utc_now()),
    )
    conn.commit()
    _CACHE.clear()
    return {"ingredient": ingredient, **item_settings(conn, location_id).get(ingredient, {})}


def stock_counts(conn: sqlite3.Connection, location_id: str) -> dict[str, dict[str, Any]]:
    rows = conn.execute("SELECT * FROM stock_counts WHERE location_id=?", (location_id,)).fetchall()
    return {
        row["ingredient"]: {
            "on_hand_base": float(row["on_hand_base"]),
            "kind": row["kind"],
            "counted_at": row["counted_at"],
            "counted_by": row["counted_by"],
        }
        for row in rows
    }


def save_count(conn: sqlite3.Connection, location_id: str, payload: dict[str, Any], counted_by: str = "") -> dict[str, Any]:
    """Write down what is on the shelf for one ingredient.

    The count arrives in whatever the operator was looking at: the recipe unit,
    or packs when a pack size is known. It is kept in the base unit so it still
    adds up when the screen later reports the same thing in a different unit.
    An empty value clears the count, which is different from counting zero.
    """
    ingredient = _clean(payload.get("ingredient"), 120).lower()
    if not ingredient:
        raise ValueError("Which ingredient?")
    raw = payload.get("on_hand")
    if raw is None or str(raw).strip() == "":
        conn.execute("DELETE FROM stock_counts WHERE location_id=? AND ingredient=?", (location_id, ingredient))
        conn.commit()
        _CACHE.clear()
        return {"ingredient": ingredient, "cleared": True}
    value = _number(raw, 0, 10_000_000, 0)
    unit = _clean(payload.get("unit"), 30)
    kind = _clean(payload.get("kind"), 10) or _kind_of_unit(unit)
    if kind not in {"mass", "volume", "count", "share"}:
        kind = "count"
    if unit == "pack":
        setting = item_settings(conn, location_id).get(ingredient)
        if not setting or setting["pack_size"] <= 0:
            raise ValueError("Say how this is bought first, then count it in packs")
        base = value * to_base(setting["pack_size"], setting["pack_unit"], kind)
    else:
        base = to_base(value, unit, kind)
    conn.execute(
        """INSERT INTO stock_counts(location_id, ingredient, on_hand_base, kind, counted_at, counted_by)
           VALUES(?,?,?,?,?,?)
           ON CONFLICT(location_id, ingredient) DO UPDATE SET
               on_hand_base=excluded.on_hand_base, kind=excluded.kind,
               counted_at=excluded.counted_at, counted_by=excluded.counted_by""",
        (location_id, ingredient, base, kind, _utc_now(), _clean(counted_by, 80)),
    )
    conn.commit()
    _CACHE.clear()
    return {"ingredient": ingredient, "on_hand_base": base, "kind": kind}


# ---------------------------------------------------------------------------
# Putting it on the order list
# ---------------------------------------------------------------------------

def attach(conn: sqlite3.Connection, location_id: str, plan: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """Add supplier, pack, on-hand and run-out facts to every line of an order plan.

    The plan's own numbers are not touched. What is added sits beside them:
    what is on the shelf, how long that lasts, what is short, and how many packs
    that is once somebody has said how the thing is bought.
    """
    now = now or local_now(conn, location_id)
    today = now.date()
    suppliers = {row["id"]: row for row in list_suppliers(conn, location_id, now)}
    settings = item_settings(conn, location_id)
    counts = stock_counts(conn, location_id)
    days = max(1, int(plan.get("days") or 1))
    counted = 0
    latest_count = ""

    for line in plan.get("lines") or []:
        key = str(line["name"]).lower()
        kind = line.get("kind", "count")
        base_typical = (float(line.get("base_low") or 0) + float(line.get("base_high") or 0)) / 2.0
        per_day_base = base_typical / days
        setting = settings.get(key) or {}
        supplier = suppliers.get(setting.get("supplier_id") or "")
        count = counts.get(key)

        pack_base = 0.0
        pack_note = ""
        if setting.get("pack_size", 0) > 0:
            pack_kind = _kind_of_unit(setting["pack_unit"]) if kind in {"mass", "volume"} else "count"
            if kind in {"mass", "volume"} and pack_kind != kind:
                pack_note = f"The pack is in {setting['pack_unit']} but the recipe counts this in {line['unit']}"
            else:
                pack_base = to_base(setting["pack_size"], setting["pack_unit"], kind)

        line["supplier_id"] = supplier["id"] if supplier else ""
        line["supplier_name"] = supplier["name"] if supplier else ""
        line["pack_size"] = _tidy(setting["pack_size"]) if setting.get("pack_size") else 0
        line["pack_unit"] = setting.get("pack_unit", "") or line["unit"]
        line["pack_label"] = setting.get("pack_label", "case") if setting else "case"
        line["product_code"] = setting.get("product_code", "")
        line["pack_note"] = pack_note
        line["per_day"] = _tidy(from_base(per_day_base, line["unit"], kind)) if kind != "share" else 0

        if count:
            counted += 1
            latest_count = max(latest_count, count["counted_at"])
            on_hand_base = count["on_hand_base"]
            line["on_hand"] = _tidy(from_base(on_hand_base, line["unit"], kind))
            line["on_hand_packs"] = _tidy(on_hand_base / pack_base) if pack_base else None
            line["counted_at"] = count["counted_at"]
            shortfall_base = max(0.0, base_typical - on_hand_base)
            if per_day_base > 0:
                cover = on_hand_base / per_day_base
                runs_out = today + timedelta(days=int(math.floor(cover)))
                line["days_of_cover"] = round(cover, 1)
                line["runs_out_on"] = runs_out.isoformat()
                line["runs_out_label"] = _day_label(runs_out, today)
                line["order"] = order_by_for_runout(supplier, runs_out, now)
            else:
                line["days_of_cover"] = None
                line["runs_out_on"] = ""
                line["runs_out_label"] = ""
                line["order"] = None
        else:
            on_hand_base = None
            line["on_hand"] = None
            line["on_hand_packs"] = None
            line["counted_at"] = ""
            line["days_of_cover"] = None
            line["runs_out_on"] = ""
            line["runs_out_label"] = ""
            line["order"] = None
            shortfall_base = base_typical

        shortfall_value, _unit = readable(shortfall_base, kind, line["unit"]) if kind in {"mass", "volume"} else (_tidy(shortfall_base), line["unit"])
        # readable() may pick a different unit for a smaller number; keep the
        # line's unit so the column reads consistently top to bottom.
        line["short"] = _tidy(from_base(shortfall_base, line["unit"], kind)) if kind != "share" else 0
        if pack_base:
            line["packs_short"] = int(math.ceil(shortfall_base / pack_base - 1e-9)) if shortfall_base > 0 else 0
            line["packs_for_window"] = int(math.ceil(base_typical / pack_base - 1e-9))
            line["suggested"] = {"quantity": line["packs_short"], "unit": line["pack_label"]}
        else:
            line["packs_short"] = None
            line["packs_for_window"] = None
            line["suggested"] = {"quantity": line["short"], "unit": line["unit"]}

    plan["suppliers"] = list(suppliers.values())
    plan["counts_taken"] = counted
    plan["last_counted_at"] = latest_count
    plan["mail_provider"] = _mail_provider()
    return plan


def _mail_provider() -> str:
    try:
        from .email_brief import mail_provider
        return mail_provider()
    except Exception:  # pragma: no cover - never let mail config break the page
        return "outbox"


def attention(conn: sqlite3.Connection, location_id: str, now: datetime | None = None) -> dict[str, Any]:
    """Everything that runs out inside the week, soonest first.

    Empty until somebody has counted something, because a run-out date with no
    count behind it would be a guess dressed as a warning.
    """
    now = now or local_now(conn, location_id)
    today = now.date()
    counts = stock_counts(conn, location_id)
    if not counts:
        return {"lines": [], "counted": 0, "as_of": ""}
    stamp = conn.execute(
        "SELECT MAX(updated_at) AS s FROM suppliers WHERE location_id=?", (location_id,)
    ).fetchone()["s"] or ""
    items_stamp = conn.execute(
        "SELECT MAX(updated_at) AS s FROM supplier_items WHERE location_id=?", (location_id,)
    ).fetchone()["s"] or ""
    latest = max(row["counted_at"] for row in counts.values())
    key = (location_id, today.isoformat(), now.hour, latest, stamp, items_stamp)
    cached = _CACHE.get(key)
    if cached and (datetime.now(timezone.utc).timestamp() - cached[0]) < _CACHE_SECONDS:
        return cached[1]

    plan = attach(conn, location_id, order_plan(conn, location_id, today, ATTENTION_DAYS), now)
    horizon = today + timedelta(days=ATTENTION_DAYS)
    lines = []
    for line in plan.get("lines") or []:
        if line.get("days_of_cover") is None or not line.get("runs_out_on"):
            continue
        runs_out = date.fromisoformat(line["runs_out_on"])
        if runs_out > horizon:
            continue
        order = line.get("order") or {}
        lines.append({
            "name": line["name"],
            "unit": line["unit"],
            "on_hand": line["on_hand"],
            "per_day": line["per_day"],
            "days_of_cover": line["days_of_cover"],
            "runs_out_on": line["runs_out_on"],
            "runs_out_label": line["runs_out_label"],
            "order_by": order.get("order_by", ""),
            "order_by_label": order.get("order_by_label", ""),
            "arrives": order.get("arrives", ""),
            "late": bool(order.get("late")),
            "supplier": line.get("supplier_name", ""),
            "supplier_id": line.get("supplier_id", ""),
            "short": line["short"],
            "suggested": line["suggested"],
        })
    lines.sort(key=lambda row: (row["runs_out_on"], row["days_of_cover"]))
    result = {"lines": lines, "counted": plan.get("counts_taken", 0), "as_of": latest}
    _CACHE[key] = (datetime.now(timezone.utc).timestamp(), result)
    return result


# ---------------------------------------------------------------------------
# Sending an order, and remembering that it was sent
# ---------------------------------------------------------------------------

def _line_text(line: dict[str, Any]) -> str:
    quantity = line.get("quantity")
    unit = _clean(line.get("unit"), 30)
    name = _clean(line.get("name"), 120)
    pack = ""
    if line.get("pack_size") and line.get("pack_unit") and unit not in ("", line.get("pack_unit")):
        pack = f" ({_tidy(float(line['pack_size']))} {line['pack_unit']} each)"
    code = f", code {_clean(line.get('product_code'), 60)}" if line.get("product_code") else ""
    return f"{name}: {quantity} {unit}{pack}{code}".strip()


def order_text(location: sqlite3.Row | dict[str, Any], supplier: dict[str, Any] | None,
               lines: list[dict[str, Any]], window: tuple[str, str], sent_by: str,
               requested: str = "", note: str = "") -> tuple[str, str, str]:
    """Subject, plain text and HTML for one order. The text is what gets pasted."""
    name = location["name"]
    who = (supplier or {}).get("rep_name") or (supplier or {}).get("name") or "there"
    start, end = window
    try:
        span = f"{date.fromisoformat(start).strftime('%a %b')} {date.fromisoformat(start).day} to {date.fromisoformat(end).strftime('%a %b')} {date.fromisoformat(end).day}"
    except ValueError:
        span = ""
    subject = f"Order from {name}" + (f", {span}" if span else "")
    body = [f"Hi {who},", ""]
    intro = f"Order for {name}"
    if (supplier or {}).get("account_number"):
        intro += f", account {supplier['account_number']}"
    body.append(intro + ".")
    if requested:
        try:
            when = date.fromisoformat(requested)
            body.append(f"Delivery {when.strftime('%A')} {when.strftime('%b')} {when.day}.")
        except ValueError:
            pass
    body.append("")
    body.extend(_line_text(line) for line in lines)
    if note:
        body.extend(["", _clean(note, 400)])
    body.extend(["", "Thanks,", sent_by or name, f"{name}, {location['city']}"])
    text = "\n".join(body)
    rows = "".join(
        f"<tr><td style='padding:6px 10px;border-bottom:1px solid #e7e6e2'>{html.escape(_clean(l.get('name'), 120))}</td>"
        f"<td style='padding:6px 10px;border-bottom:1px solid #e7e6e2;text-align:right'>{html.escape(str(l.get('quantity', '')))} {html.escape(_clean(l.get('unit'), 30))}</td>"
        f"<td style='padding:6px 10px;border-bottom:1px solid #e7e6e2;color:#797f88'>{html.escape(_clean(l.get('product_code'), 60))}</td></tr>"
        for l in lines
    )
    html_body = (
        "<div style='font:14px/1.5 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#15171a'>"
        + "".join(f"<p>{html.escape(p)}</p>" for p in body[:4] if p)
        + f"<table style='border-collapse:collapse;min-width:360px'>{rows}</table>"
        + (f"<p>{html.escape(_clean(note, 400))}</p>" if note else "")
        + f"<p>Thanks,<br>{html.escape(sent_by or name)}<br>{html.escape(name)}, {html.escape(location['city'])}</p></div>"
    )
    return subject, text, html_body


def record_order(conn: sqlite3.Connection, root: Path, location_id: str, payload: dict[str, Any],
                 sent_by: str = "") -> dict[str, Any]:
    """Send an order the way the operator chose, and keep a record either way."""
    channel = _clean(payload.get("channel"), 12)
    if channel not in CHANNELS:
        raise ValueError("How is this order going out?")
    raw_lines = payload.get("lines") or []
    lines = []
    for row in raw_lines[:200]:
        if not isinstance(row, dict) or not _clean(row.get("name")):
            continue
        quantity = _number(row.get("quantity"), 0, 1_000_000, 0)
        if quantity <= 0:
            continue
        lines.append({
            "name": _clean(row.get("name"), 120),
            "quantity": _tidy(quantity),
            "unit": _clean(row.get("unit"), 30),
            "pack_size": _number(row.get("pack_size"), 0, 1_000_000, 0),
            "pack_unit": _clean(row.get("pack_unit"), 30),
            "product_code": _clean(row.get("product_code"), 60),
        })
    if not lines:
        raise ValueError("There is nothing on this order")

    location = _location(conn, location_id)
    supplier_id = _clean(payload.get("supplier_id"), 40)
    supplier = get_supplier(conn, location_id, supplier_id) if supplier_id else None
    now = local_now(conn, location_id)
    window = (_clean(payload.get("window_start"), 10), _clean(payload.get("window_end"), 10))
    requested = _clean(payload.get("requested_delivery"), 10)
    expected = requested or (schedule(supplier, now)["next_delivery"] if supplier else "")
    subject, text, html_body = order_text(location, supplier, lines, window, sent_by, expected, _clean(payload.get("note"), 400))

    status, artifact, error, mailto = "sent", "", "", ""
    if channel == "email":
        recipient = (supplier or {}).get("order_email", "")
        if not recipient:
            raise ValueError("This supplier has no order email yet. Add one in Settings, or open it in your mail app.")
        from .email_brief import send_transactional
        result = send_transactional(root, recipient, subject, text, html_body)
        if result.get("provider") == "blocked":
            raise ValueError(result.get("error") or "That address cannot be sent to")
        status = "sent" if result.get("delivered") else "outbox"
        artifact = str(result.get("path") or "")
        error = str(result.get("error") or "")
    elif channel == "mail-app":
        recipient = (supplier or {}).get("order_email", "")
        mailto = f"mailto:{quote(recipient)}?subject={quote(subject)}&body={quote(text)}"
        status = "drafted"
    elif channel == "site":
        status = "opened"
    else:
        status = "copied"

    order_id = uuid.uuid4().hex
    conn.execute(
        """INSERT INTO purchase_orders(id, location_id, supplier_id, supplier_name, channel, status, window_start,
               window_end, expected_on, lines_json, line_count, sent_at, sent_by, artifact_path, error)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (order_id, location_id, supplier["id"] if supplier else None, (supplier or {}).get("name", ""),
         channel, status, window[0], window[1], expected, json.dumps(lines), len(lines),
         now.isoformat(timespec="seconds"), _clean(sent_by, 80), artifact, error),
    )
    conn.commit()
    _CACHE.clear()
    message = {
        "sent": f"Sent to {(supplier or {}).get('name', 'the supplier')}.",
        "outbox": "No mail service is set up, so the order was written to data/outbox instead of sent.",
        "drafted": "Opening it in your mail app.",
        "opened": f"Opening {(supplier or {}).get('name', 'the site')}.",
        "copied": "Copied. Paste it wherever the order goes.",
    }[status]
    if expected and status in {"sent", "drafted"}:
        try:
            message += f" Arriving {_day_label(date.fromisoformat(expected), now.date())}."
        except ValueError:
            pass
    return {
        "order": {"id": order_id, "status": status, "channel": channel, "expected_on": expected,
                  "line_count": len(lines), "subject": subject},
        "text": text,
        "mailto": mailto,
        "message": message,
    }


def _order_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "supplier_id": row["supplier_id"] or "",
        "supplier_name": row["supplier_name"],
        "channel": row["channel"],
        "status": row["status"],
        "window_start": row["window_start"],
        "window_end": row["window_end"],
        "expected_on": row["expected_on"],
        "line_count": int(row["line_count"] or 0),
        "sent_at": row["sent_at"],
        "sent_by": row["sent_by"],
        "lines": json.loads(row["lines_json"] or "[]"),
    }


def recent_orders(conn: sqlite3.Connection, location_id: str, limit: int = 12) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM purchase_orders WHERE location_id=? ORDER BY sent_at DESC LIMIT ?", (location_id, limit)
    ).fetchall()
    return [_order_dict(row) for row in rows]


def last_orders(conn: sqlite3.Connection, location_id: str) -> dict[str, dict[str, Any]]:
    """The most recent order per supplier, for "sent Tue, arriving Thu"."""
    rows = conn.execute(
        """SELECT * FROM purchase_orders WHERE location_id=? AND supplier_id IS NOT NULL
           ORDER BY sent_at DESC""",
        (location_id,),
    ).fetchall()
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row["supplier_id"] not in out:
            out[row["supplier_id"]] = _order_dict(row)
    return out


def supplier_view(conn: sqlite3.Connection, location_id: str) -> dict[str, Any]:
    now = local_now(conn, location_id)
    return {
        "suppliers": list_suppliers(conn, location_id, now),
        "items": item_settings(conn, location_id),
        "recent_orders": recent_orders(conn, location_id),
        "mail_provider": _mail_provider(),
        "pack_labels": PACK_LABELS,
        "weekdays": [[key, WEEKDAY_LABELS[key]] for key in WEEKDAYS],
        "today": now.date().isoformat(),
    }
