"""Order history, and how well the forecast did on days that have closed.

Two things live here because they answer the same question from opposite ends.
The order list is what actually happened at the register. The day score is what
Quantify said would happen before service started. Putting them side by side is
the only honest way to show whether the product is working.

Order source
------------
When a register is connected, orders are read from `pos_orders` and
`pos_order_lines` exactly as the provider reported them. When only daily and
hourly totals exist, which is the case for the sample dataset and for any
provider that will not release line-level history, orders are rebuilt from those
totals with a seed derived from the location and date. The rebuild is stable, it
adds up to the same totals, and the interface always says which source it came
from. Nothing is presented as a receipt that is not one.
"""

from __future__ import annotations

import json
import math
import random
import sqlite3
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterator

from . import localtime
from .intelligence import (
    HistoryRow,
    hour_label,
    location_hour_curve,
    opening_calls,
    service_slots,
    _baseline_prediction,
    _analog_prediction,
    _cold_start_estimate,
    _fit_model_bundle,
    _history_for_item,
    _learned_hour_curve,
    _load_location,
    _settings,
    build_context,
    dot,
    feature_vector,
)
from . import costs as costs_module
from .costs import cost_context, day_costs, day_costs_many

CHANNELS = (
    ("Counter", 0.58),
    ("Pickup", 0.19),
    ("Delivery", 0.13),
    ("Dine in", 0.10),
)
PAYMENTS = (("Card", 0.71), ("Mobile wallet", 0.17), ("Cash", 0.12))
TAX_RATE = 0.08375


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _weighted(rng: random.Random, options: tuple[tuple[str, float], ...]) -> str:
    roll = rng.random()
    running = 0.0
    for name, weight in options:
        running += weight
        if roll <= running:
            return name
    return options[-1][0]


def order_source(conn: sqlite3.Connection, location_id: str) -> str:
    row = conn.execute("SELECT COUNT(*) AS n FROM pos_orders WHERE location_id=?", (location_id,)).fetchone()
    return "register" if row and int(row["n"]) else "rebuilt"


# ---------------------------------------------------------------------------
# Rebuilding orders from hourly totals
# ---------------------------------------------------------------------------

def _basket_shape(conn: sqlite3.Connection, location_id: str) -> tuple[float, float]:
    """Average items per order and its spread, inferred from the menu."""
    row = conn.execute(
        "SELECT COUNT(*) AS items, AVG(price) AS price FROM menu_items WHERE location_id=? AND active=1",
        (location_id,),
    ).fetchone()
    average_price = float(row["price"] or 6.0) if row else 6.0
    # Cheaper menus ring up more lines per order. This is a starting shape only,
    # and it is replaced entirely once real orders arrive.
    if average_price <= 6:
        return 2.4, 1.3
    if average_price <= 11:
        return 2.1, 1.1
    return 1.9, 1.0


def rebuild_day_orders(conn: sqlite3.Connection, location_id: str, target: date) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT h.hour, h.item_id, h.quantity, h.revenue, m.name, m.category, m.price
           FROM sales_hourly h JOIN menu_items m ON m.id=h.item_id
           WHERE h.location_id=? AND h.date=? ORDER BY h.hour, m.name""",
        (location_id, target.isoformat()),
    ).fetchall()
    if not rows:
        return []

    rng = random.Random(f"{location_id}|{target.isoformat()}")
    mean_basket, spread = _basket_shape(conn, location_id)
    orders: list[dict[str, Any]] = []
    sequence = 0

    by_hour: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_hour[int(row["hour"])].append(dict(row))

    for hour in sorted(by_hour):
        pool: list[dict[str, Any]] = []
        for row in by_hour[hour]:
            units = int(round(float(row["quantity"] or 0)))
            unit_price = float(row["revenue"] or 0) / units if units else float(row["price"] or 0)
            for _ in range(units):
                pool.append({"item_id": row["item_id"], "name": row["name"], "category": row["category"], "price": round(unit_price, 2)})
        if not pool:
            continue
        rng.shuffle(pool)

        cursor = 0
        while cursor < len(pool):
            size = max(1, min(6, int(round(rng.gauss(mean_basket, spread)))))
            size = min(size, len(pool) - cursor)
            lines_raw = pool[cursor:cursor + size]
            cursor += size
            sequence += 1

            grouped: dict[str, dict[str, Any]] = {}
            for line in lines_raw:
                entry = grouped.setdefault(line["item_id"], {
                    "item_id": line["item_id"], "name": line["name"],
                    "category": line["category"], "unit_price": line["price"], "quantity": 0,
                })
                entry["quantity"] += 1
            lines = []
            for entry in grouped.values():
                entry["line_total"] = round(entry["unit_price"] * entry["quantity"], 2)
                lines.append(entry)
            lines.sort(key=lambda row: -row["line_total"])

            subtotal = round(sum(line["line_total"] for line in lines), 2)
            channel = _weighted(rng, CHANNELS)
            payment = _weighted(rng, PAYMENTS)
            tax = round(subtotal * TAX_RATE, 2)
            tip = 0.0
            if channel in {"Delivery", "Dine in"} and payment != "Cash":
                tip = round(subtotal * rng.choice((0.10, 0.15, 0.18, 0.20)), 2)
            minute = rng.randint(0, 59)
            second = rng.randint(0, 59)
            orders.append({
                "id": f"{target.isoformat()}-{location_id}-{sequence:05d}",
                "number": f"#{sequence:04d}",
                "date": target.isoformat(),
                "time": time(hour, minute, second).strftime("%H:%M:%S"),
                "hour": hour,
                "channel": channel,
                "payment": payment,
                "item_count": sum(line["quantity"] for line in lines),
                "lines": lines,
                "subtotal": subtotal,
                "tax": tax,
                "tip": tip,
                "total": round(subtotal + tax + tip, 2),
                "source": "rebuilt",
            })

    orders.sort(key=lambda row: row["time"])
    for index, order in enumerate(orders, start=1):
        order["number"] = f"#{index:04d}"
    return orders


def _stored_day_orders(conn: sqlite3.Connection, location_id: str, target: date) -> list[dict[str, Any]]:
    headers = conn.execute(
        """SELECT * FROM pos_orders WHERE location_id=? AND sale_date=? ORDER BY sale_time""",
        (location_id, target.isoformat()),
    ).fetchall()
    if not headers:
        return []
    lines_by_order: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in conn.execute(
        """SELECT l.provider_order_id, l.quantity, l.revenue, m.name, m.category, m.id AS item_id
           FROM pos_order_lines l JOIN menu_items m ON m.id=l.item_id
           WHERE l.location_id=? AND l.sale_date=?""",
        (location_id, target.isoformat()),
    ).fetchall():
        quantity = float(row["quantity"] or 0)
        lines_by_order[row["provider_order_id"]].append({
            "item_id": row["item_id"], "name": row["name"], "category": row["category"],
            "quantity": quantity, "unit_price": round(float(row["revenue"] or 0) / quantity, 2) if quantity else 0.0,
            "line_total": round(float(row["revenue"] or 0), 2),
        })
    output = []
    for index, header in enumerate(headers, start=1):
        lines = sorted(lines_by_order.get(header["provider_order_id"], []), key=lambda row: -row["line_total"])
        output.append({
            "id": header["provider_order_id"],
            "number": header["order_number"] or f"#{index:04d}",
            "date": header["sale_date"],
            "time": header["sale_time"],
            "hour": int(header["sale_hour"]),
            "channel": header["channel"] or "Counter",
            "payment": header["payment_type"] or "Card",
            "item_count": int(sum(line["quantity"] for line in lines)),
            "lines": lines,
            "subtotal": round(float(header["subtotal"] or 0), 2),
            "tax": round(float(header["tax"] or 0), 2),
            "tip": round(float(header["tip"] or 0), 2),
            "total": round(float(header["total"] or 0), 2),
            "source": "register",
        })
    return output


def day_orders(conn: sqlite3.Connection, location_id: str, target: date) -> list[dict[str, Any]]:
    stored = _stored_day_orders(conn, location_id, target)
    return stored if stored else rebuild_day_orders(conn, location_id, target)


# ---------------------------------------------------------------------------
# Day summaries
# ---------------------------------------------------------------------------

def _day_totals(conn: sqlite3.Connection, location_id: str, start: date, end: date) -> dict[str, dict[str, float]]:
    rows = conn.execute(
        """SELECT date, SUM(quantity) AS units, SUM(revenue) AS revenue
           FROM sales WHERE location_id=? AND date>=? AND date<=? GROUP BY date""",
        (location_id, start.isoformat(), end.isoformat()),
    ).fetchall()
    return {row["date"]: {"units": float(row["units"] or 0), "revenue": float(row["revenue"] or 0)} for row in rows}


def day_list(
    conn: sqlite3.Connection,
    location_id: str,
    before: date | None = None,
    limit: int = 20,
    start: date | None = None,
    with_costs: bool = False,
) -> dict[str, Any]:
    """A page of closed days, newest first, for the scrolling history list."""
    limit = max(1, min(60, limit))
    params: list[Any] = [location_id]
    clause = ""
    if before is not None:
        clause += " AND date<?"
        params.append(before.isoformat())
    if start is not None:
        clause += " AND date>=?"
        params.append(start.isoformat())
    rows = conn.execute(
        f"""SELECT date, SUM(quantity) AS units, SUM(revenue) AS revenue, COUNT(DISTINCT item_id) AS items
            FROM sales WHERE location_id=?{clause}
            GROUP BY date ORDER BY date DESC LIMIT ?""",
        (*params, limit + 1),
    ).fetchall()

    has_more = len(rows) > limit
    rows = rows[:limit]
    if not rows:
        return {"days": [], "has_more": False, "next_before": None,
                "source": order_source(conn, location_id), "costs": None}

    dates = [row["date"] for row in rows]
    scored = {
        row["date"]: dict(row)
        for row in conn.execute(
            f"SELECT * FROM day_accuracy WHERE location_id=? AND date IN ({','.join('?' * len(dates))})",
            (location_id, *dates),
        ).fetchall()
    }
    # Dates inside the covered span with no sales at all. The register recorded
    # nothing, so the honest reading is that the place was shut. Saying so beats
    # a gap in the list that makes somebody think data went missing.
    covered = {row["date"] for row in rows}
    oldest = date.fromisoformat(rows[-1]["date"])
    newest = date.fromisoformat(rows[0]["date"])
    closed: list[dict[str, Any]] = []
    cursor = oldest
    while cursor <= newest:
        if cursor.isoformat() not in covered:
            closed.append({
                "date": cursor.isoformat(),
                "weekday": cursor.strftime("%A"),
                "closed": True,
                "sales": 0.0, "units": 0, "orders": 0, "average_order": 0.0,
                "distinct_items": 0, "accuracy": None, "predicted_units": None,
                "predicted_sales": None, "scored": False, "costs": None,
                "note": "Nothing was recorded on this date, so we take it you were closed.",
            })
        cursor += timedelta(days=1)

    days = []
    priced: dict[str, dict[str, Any]] = {}
    for row in rows:
        day = date.fromisoformat(row["date"])
        units = float(row["units"] or 0)
        revenue = float(row["revenue"] or 0)
        # Read the same orders the detail view reads, so the two never disagree.
        orders = day_orders(conn, location_id, day)
        order_count = max(1, len(orders))
        gross = sum(order["total"] for order in orders) or revenue
        score = scored.get(row["date"])
        priced[row["date"]] = {"orders": orders, "revenue": revenue}
        days.append({
            "date": row["date"],
            "weekday": day.strftime("%A"),
            "sales": round(revenue, 2),
            "units": int(round(units)),
            "orders": order_count,
            "average_order": round(gross / order_count, 2),
            "distinct_items": int(row["items"] or 0),
            "accuracy": round(float(score["accuracy"]), 1) if score else None,
            "predicted_units": int(round(float(score["predicted_units"]))) if score else None,
            "predicted_sales": round(float(score["predicted_sales"]), 2) if score else None,
            "scored": bool(score),
            "closed": False,
            "note": "",
            "costs": None,
        })

    costs_summary = None
    if with_costs:
        context = cost_context(conn, location_id)
        computed = day_costs_many(conn, location_id, priced, context)
        for row in days:
            row["costs"] = computed.get(row["date"])
        # A day with no trade still owes the rent. Charging it here is what
        # keeps a month of this column adding up to a month of real cost.
        shut = costs_module.closed_day(context)
        for row in closed:
            row["costs"] = shut
        costs_summary = {
            "configured": context["configured"],
            "wage": context["wage"],
            "recurring_daily": context["recurring"]["daily"],
        }

    days = sorted(days + closed, key=lambda row: row["date"], reverse=True)
    return {
        "days": days,
        "has_more": has_more,
        "next_before": days[-1]["date"] if days and has_more else None,
        "source": order_source(conn, location_id),
        "costs": costs_summary,
    }


def order_page(
    conn: sqlite3.Connection,
    location_id: str,
    before_date: date | None = None,
    skip: int = 0,
    limit: int = 40,
    start: date | None = None,
    with_costs: bool = False,
) -> dict[str, Any]:
    """A page of orders, newest first, walking backwards one day at a time.

    Only the days needed to fill the page are read, so opening the history on a
    tablet costs the same whether the location has one month or three years.
    """
    limit = max(1, min(200, limit))
    params: list[Any] = [location_id]
    clause = ""
    if before_date is not None:
        clause += " AND date<=?"
        params.append(before_date.isoformat())
    if start is not None:
        clause += " AND date>=?"
        params.append(start.isoformat())
    day_rows = conn.execute(
        f"SELECT DISTINCT date FROM sales WHERE location_id=?{clause} ORDER BY date DESC LIMIT 40",
        params,
    ).fetchall()

    collected: list[dict[str, Any]] = []
    cursor_date: str | None = None
    cursor_skip = 0
    remaining_skip = max(0, skip)

    for row in day_rows:
        day = date.fromisoformat(row["date"])
        orders = day_orders(conn, location_id, day)
        orders.reverse()  # newest first within the day
        if remaining_skip >= len(orders):
            remaining_skip -= len(orders)
            continue
        slice_start = remaining_skip
        remaining_skip = 0
        available = orders[slice_start:]
        take = available[:limit - len(collected)]
        collected.extend(take)
        if len(collected) >= limit:
            consumed = slice_start + len(take)
            if consumed < len(orders):
                cursor_date, cursor_skip = row["date"], consumed
            else:
                cursor_date, cursor_skip = None, 0
                index = day_rows.index(row)
                if index + 1 < len(day_rows):
                    cursor_date, cursor_skip = day_rows[index + 1]["date"], 0
            break

    return {
        "orders": collected,
        "has_more": cursor_date is not None,
        "next_before_date": cursor_date,
        "next_skip": cursor_skip,
        "source": order_source(conn, location_id),
    }


# ---------------------------------------------------------------------------
# Scoring closed days against what the model said
# ---------------------------------------------------------------------------

def _blend(baseline: float, ridge: float, analog: float, weights: dict[str, float]) -> float:
    return max(0.0, baseline * weights["baseline"] + ridge * weights["ridge"] + analog * weights["analogs"])


def score_range(conn: sqlite3.Connection, location_id: str, start: date, end: date) -> int:
    """Walk the model forward across a span of closed days and store the result.

    The model is fitted once on everything before `start`, then each day's actual
    sales are appended after it has been predicted. Nothing that happened on or
    after the day being scored is ever visible to the prediction for that day.
    """
    if start > end:
        return 0
    location = _load_location(conn, location_id)
    items = conn.execute("SELECT * FROM menu_items WHERE location_id=? AND active=1", (location_id,)).fetchall()
    if not items:
        return 0

    settings = _settings(conn, location_id)
    per_day: dict[str, dict[str, Any]] = {}

    # What Quantify actually said before each of these days opened. Where a call
    # exists it is the thing being scored, manager override included, because
    # that is the number the kitchen prepped to. Where none exists the
    # prediction is rebuilt from sales up to the day before, and the day is
    # labelled so the two are never confused.
    #
    # forecast_revisions is deliberately not read anywhere in this function. A
    # revision made during service has already watched part of the day, so
    # scoring it as a forecast of that day would flatter the record.
    calls = opening_calls(conn, location_id, start, end)

    for item in items:
        training, weather_map, events_map = _history_for_item(conn, location, item["id"], start)
        coefficients, calibration = _fit_model_bundle(training)
        available = training[:]
        first_date = available[0].target_date if available else start - timedelta(days=365)
        actual_rows = conn.execute(
            """SELECT date,quantity,revenue,stockout_minutes FROM sales
               WHERE location_id=? AND item_id=? AND date>=? AND date<=? ORDER BY date""",
            (location_id, item["id"], start.isoformat(), end.isoformat()),
        ).fetchall()
        for actual in actual_rows:
            target = date.fromisoformat(actual["date"])
            context = build_context(location, target, weather_map.get(actual["date"]), events_map.get(actual["date"], []), weather_map)
            x = feature_vector(context, target, first_date)
            baseline = _baseline_prediction(available, target) if available else _cold_start_estimate(conn, item, target)
            analog, _peers = _analog_prediction(available, target, context) if available else (baseline, [])
            ridge = max(0.0, math.expm1(dot(x, coefficients))) if len(available) >= 28 else baseline
            predicted = _blend(baseline, ridge, analog, calibration["weights"])
            called = calls.get((actual["date"], item["id"]))
            unit_price = float(item["price"])
            if called is not None:
                predicted = max(0.0, float(called["expected"]))
                unit_price = float(called["price"])
            quantity = float(actual["quantity"])

            bucket = per_day.setdefault(actual["date"], {
                "predicted_units": 0.0, "actual_units": 0.0,
                "predicted_sales": 0.0, "actual_sales": 0.0,
                "error": 0.0, "items": [], "condition": context,
                "from_call": 0, "from_rebuild": 0, "overridden": 0,
            })
            bucket["predicted_units"] += predicted
            bucket["actual_units"] += quantity
            bucket["predicted_sales"] += predicted * unit_price
            bucket["actual_sales"] += float(actual["revenue"])
            bucket["error"] += abs(quantity - predicted)
            bucket["from_call" if called is not None else "from_rebuild"] += 1
            bucket["overridden"] += 1 if (called is not None and int(called["overridden"] or 0)) else 0
            bucket["items"].append({
                "item_id": item["id"],
                "name": item["name"],
                "predicted": round(predicted, 1),
                "actual": int(round(quantity)),
                "gap": int(round(quantity - predicted)),
                "sold_out": bool(int(actual["stockout_minutes"] or 0) > 0),
                "overridden": bool(called is not None and int(called["overridden"] or 0)),
            })
            available.append(HistoryRow(
                target_date=target, quantity=quantity, revenue=float(actual["revenue"]),
                stockout_minutes=int(actual["stockout_minutes"] or 0), context=context, x=x,
            ))

    stored = 0
    for day_key, bucket in per_day.items():
        target = date.fromisoformat(day_key)
        actual_units = bucket["actual_units"]
        accuracy = max(0.0, 100.0 - (bucket["error"] / max(1.0, actual_units) * 100.0))
        items_detail = sorted(bucket["items"], key=lambda row: abs(row["gap"]), reverse=True)
        actual_by_item = {row["item_id"]: float(row["actual"]) for row in bucket["items"]}
        review = _revision_review(conn, location_id, day_key, actual_by_item)
        call_source = "stored" if bucket["from_call"] >= bucket["from_rebuild"] else "reconstructed"

        # One row per hour this location is open, in service order, whether or
        # not that hour rang. A missing hour reads as missing data; a quiet hour
        # reads as a quiet hour, and they are not the same thing. Two calendar
        # dates are read because an hour past midnight is stored under the
        # following date and belongs to this service, not the next one.
        curve = location_hour_curve(conn, location, target, settings)["shares"]
        slots = service_slots(location)
        allowed = set(slots)
        actual_slots: dict[int, float] = defaultdict(float)
        for row in conn.execute(
            """SELECT date, hour, SUM(quantity) AS units FROM sales_hourly
               WHERE location_id=? AND date IN (?,?) GROUP BY date, hour""",
            (location_id, day_key, (target + timedelta(days=1)).isoformat()),
        ).fetchall():
            slot = (date.fromisoformat(row["date"]) - target).days * 24 + int(row["hour"])
            if slot in allowed:
                actual_slots[slot] += float(row["units"] or 0)
        hourly = [
            {
                "hour": slot % 24,
                "slot": slot,
                "label": hour_label(slot),
                "predicted": round(bucket["predicted_units"] * curve.get(slot, 0.0), 1),
                "actual": round(actual_slots.get(slot, 0.0), 1),
            }
            for slot in slots
        ]
        conn.execute(
            """INSERT INTO day_accuracy(
                   location_id,date,predicted_units,actual_units,predicted_sales,actual_sales,
                   accuracy,items_json,hourly_json,conditions_json,scored_at,
                   call_source,caught_slot,revisions_used)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(location_id,date) DO UPDATE SET
                   predicted_units=excluded.predicted_units, actual_units=excluded.actual_units,
                   predicted_sales=excluded.predicted_sales, actual_sales=excluded.actual_sales,
                   accuracy=excluded.accuracy, items_json=excluded.items_json,
                   hourly_json=excluded.hourly_json, conditions_json=excluded.conditions_json,
                   scored_at=excluded.scored_at, call_source=excluded.call_source,
                   caught_slot=excluded.caught_slot, revisions_used=excluded.revisions_used""",
            (
                location_id, day_key, bucket["predicted_units"], actual_units,
                bucket["predicted_sales"], bucket["actual_sales"], accuracy,
                json.dumps(items_detail, separators=(",", ":")),
                json.dumps(hourly, separators=(",", ":")),
                json.dumps({
                    "weather": bucket["condition"]["weather_condition"],
                    "high": round(bucket["condition"]["temp_high"]),
                    "low": round(bucket["condition"]["temp_low"]),
                    "rain_mm": round(bucket["condition"]["precipitation_mm"], 1),
                    "occasion": bucket["condition"]["occasion_name"],
                    "events": len(bucket["condition"]["events"]),
                }, separators=(",", ":")),
                _utc_now(),
                call_source, review["caught_slot"], review["revisions_used"],
            ),
        )
        stored += 1
    conn.commit()
    return stored


def _revision_review(
    conn: sqlite3.Connection, location_id: str, day_key: str, actuals: dict[str, float]
) -> dict[str, Any]:
    """What the revisions did, kept well clear of the score.

    One figure comes out of here and it is not an accuracy. It is the hour at
    which the running call first came inside ten percent of where the day
    actually finished, which is how much warning an operator had. That is the
    only thing a revision can honestly claim credit for.

    There is deliberately no second accuracy number. Any revision has already
    watched part of the day, so its error can only come from the part it has not
    seen, and it will read higher than the morning call every time. Printing
    that beside the score, under any name, would let the product be read as more
    accurate than it is.
    """
    empty = {"caught_slot": -1, "revisions_used": 0}
    total_actual = sum(actuals.values())
    if total_actual <= 0:
        return empty
    rows = conn.execute(
        """SELECT slot,item_id,revised FROM forecast_revisions
           WHERE location_id=? AND date=? ORDER BY slot""",
        (location_id, day_key),
    ).fetchall()
    if not rows:
        return empty
    by_slot: dict[int, list[Any]] = defaultdict(list)
    for row in rows:
        by_slot[int(row["slot"])].append(row)
    caught = -1
    for slot in sorted(by_slot):
        # Scored item by item, the same way the day score is, so if the two are
        # ever shown together they are at least the same kind of number.
        error = sum(
            abs(actuals.get(row["item_id"], 0.0) - float(row["revised"])) for row in by_slot[slot]
        )
        if error / total_actual <= 0.10:
            caught = slot
            break
    return {"caught_slot": caught, "revisions_used": len(by_slot)}


def _local_today(conn: sqlite3.Connection, location_id: str) -> date:
    """The trading date this location is currently in, on its own clock."""
    row = conn.execute(
        "SELECT timezone,open_hour,close_hour FROM locations WHERE id=?", (location_id,)
    ).fetchone()
    if row is None:
        return date.today()
    from .intraday import trading_date

    try:
        return trading_date(row, localtime.now(row["timezone"]))
    except Exception:  # a bad zone must not stop scoring, only shift it a day
        return date.today()


def last_closed_day(conn: sqlite3.Connection, location_id: str) -> date:
    """The most recent day this location has actually finished trading."""
    return _local_today(conn, location_id) - timedelta(days=1)


def unscored_days(conn: sqlite3.Connection, location_id: str, limit: int = 45) -> list[date]:
    """Closed days with no score yet, newest first.

    A day that is still trading is left alone. When a register is connected,
    today's partial totals land in `sales` as the orders arrive, so scoring the
    current date would compare a whole day's call against three hours of sales
    and record a miss that never happened. Nothing rescores a day once it has a
    row, so that wrong number would sit in the record permanently.
    """
    cutoff = _local_today(conn, location_id)
    rows = conn.execute(
        """SELECT s.date FROM (SELECT DISTINCT date FROM sales WHERE location_id=? AND date<?) s
           LEFT JOIN day_accuracy a ON a.location_id=? AND a.date=s.date
           WHERE a.date IS NULL ORDER BY s.date DESC LIMIT ?""",
        (location_id, cutoff.isoformat(), location_id, limit),
    ).fetchall()
    return [date.fromisoformat(row["date"]) for row in rows]


def stale_scored_days(conn: sqlite3.Connection, location_id: str, limit: int = 3) -> list[date]:
    """Scored days whose register data has moved since they were scored.

    A void, a re-fired ticket, or a provider backfill changes what a closed day
    actually sold, and the score has to follow it. The limit is deliberately
    small and each day is rescored on its own, because a provider that re-sends
    a year of orders touches every row's timestamp whether the numbers changed
    or not, and a whole year must not be refitted on the back of that.
    """
    cutoff = _local_today(conn, location_id)
    rows = conn.execute(
        """SELECT a.date FROM day_accuracy a
           WHERE a.location_id=? AND a.date<? AND EXISTS (
               SELECT 1 FROM pos_order_lines l
               WHERE l.location_id=a.location_id AND l.sale_date=a.date
                 AND l.updated_at>a.scored_at)
           ORDER BY a.date DESC LIMIT ?""",
        (location_id, cutoff.isoformat(), limit),
    ).fetchall()
    return [date.fromisoformat(row["date"]) for row in rows]


def ensure_day_scored(conn: sqlite3.Connection, location_id: str, target: date) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM day_accuracy WHERE location_id=? AND date=?", (location_id, target.isoformat())
    ).fetchone()
    if row is None and target <= last_closed_day(conn, location_id):
        score_range(conn, location_id, target, target)
        row = conn.execute(
            "SELECT * FROM day_accuracy WHERE location_id=? AND date=?", (location_id, target.isoformat())
        ).fetchone()
    return dict(row) if row else None


def day_detail(conn: sqlite3.Connection, location_id: str, target: date) -> dict[str, Any]:
    """Everything about one closed day: what sold, and how close the call was."""
    score = ensure_day_scored(conn, location_id, target)
    orders = day_orders(conn, location_id, target)
    totals = _day_totals(conn, location_id, target, target).get(target.isoformat(), {"units": 0.0, "revenue": 0.0})

    channels: dict[str, dict[str, float]] = defaultdict(lambda: {"orders": 0, "sales": 0.0})
    for order in orders:
        entry = channels[order["channel"]]
        entry["orders"] += 1
        entry["sales"] += order["total"]

    top_items: dict[str, dict[str, Any]] = {}
    for order in orders:
        for line in order["lines"]:
            entry = top_items.setdefault(line["item_id"], {"name": line["name"], "units": 0, "sales": 0.0})
            entry["units"] += line["quantity"]
            entry["sales"] += line["line_total"]

    items_detail: list[dict[str, Any]] = []
    hourly: list[dict[str, Any]] = []
    conditions: dict[str, Any] = {}
    if score:
        items_detail = json.loads(score["items_json"] or "[]")
        hourly = json.loads(score["hourly_json"] or "[]")
        conditions = json.loads(score["conditions_json"] or "{}")

    order_total = sum(order["total"] for order in orders)
    return {
        "date": target.isoformat(),
        "weekday": target.strftime("%A"),
        "sales": round(totals["revenue"], 2),
        "units": int(round(totals["units"])),
        "orders": len(orders),
        "average_order": round(order_total / len(orders), 2) if orders else 0.0,
        "busiest_hour": max(hourly, key=lambda row: row["actual"], default=None),
        "channels": [
            {"channel": name, "orders": int(value["orders"]), "sales": round(value["sales"], 2)}
            for name, value in sorted(channels.items(), key=lambda row: -row[1]["sales"])
        ],
        "top_items": sorted(top_items.values(), key=lambda row: -row["units"])[:8],
        "accuracy": round(float(score["accuracy"]), 1) if score else None,
        "predicted_units": int(round(float(score["predicted_units"]))) if score else None,
        "predicted_sales": round(float(score["predicted_sales"]), 2) if score else None,
        "costs": day_costs(conn, location_id, target, orders, totals["revenue"]),
        "item_scores": items_detail[:12],
        "hourly": hourly,
        "conditions": conditions,
        "source": orders[0]["source"] if orders else order_source(conn, location_id),
    }


def review_payload(detail: dict[str, Any]) -> dict[str, Any]:
    """The record handed to the writing layer for a closed day."""
    conditions = detail.get("conditions") or {}
    misses = [row for row in detail.get("item_scores", []) if abs(int(row.get("gap") or 0)) >= 3][:4]
    note = None
    if conditions.get("occasion"):
        note = f"{conditions['occasion']} fell on this date."
    elif float(conditions.get("rain_mm") or 0) >= 4:
        note = f"{conditions['rain_mm']} mm of rain fell, which usually shifts the mix toward delivery."
    elif conditions.get("events"):
        note = f"{conditions['events']} nearby events overlapped service hours."
    return {
        "date": detail.get("date"),
        "weekday": detail.get("weekday"),
        "actual": {"items": detail.get("units"), "sales": detail.get("sales"), "orders": detail.get("orders")},
        "predicted": {"items": detail.get("predicted_units"), "sales": detail.get("predicted_sales")},
        "accuracy_percent": detail.get("accuracy"),
        "item_misses": misses,
        "condition_note": note,
        "conditions": conditions,
    }


def accuracy_trend(conn: sqlite3.Connection, location_id: str, days: int = 45) -> dict[str, Any]:
    rows = conn.execute(
        """SELECT date, accuracy, predicted_units, actual_units, predicted_sales, actual_sales
           FROM day_accuracy WHERE location_id=? ORDER BY date DESC LIMIT ?""",
        (location_id, max(7, min(180, days))),
    ).fetchall()
    series = [dict(row) for row in reversed(rows)]
    if not series:
        return {"series": [], "average": None, "days": 0, "best": None, "worst": None, "within_ten": None}
    values = [float(row["accuracy"]) for row in series]
    within = sum(1 for value in values if value >= 90)
    return {
        "series": [
            {
                "date": row["date"],
                "accuracy": round(float(row["accuracy"]), 1),
                "predicted": int(round(float(row["predicted_units"]))),
                "actual": int(round(float(row["actual_units"]))),
            }
            for row in series
        ],
        "average": round(sum(values) / len(values), 1),
        "days": len(values),
        "best": round(max(values), 1),
        "worst": round(min(values), 1),
        "within_ten": round(within / len(values) * 100),
    }


def iter_scoring_targets(conn: sqlite3.Connection, chunk: int = 21) -> Iterator[tuple[str, date, date]]:
    """Chunks of unscored days, newest first, for the background scorer.

    Locations with the least history scored go first, so every location gets a
    recent record before any one of them is taken all the way back.
    """
    rows = conn.execute(
        """SELECT l.id, COALESCE(a.n, 0) AS scored FROM locations l
           LEFT JOIN (SELECT location_id, COUNT(*) AS n FROM day_accuracy GROUP BY location_id) a
             ON a.location_id = l.id
           WHERE l.active=1 ORDER BY scored ASC, l.id"""
    ).fetchall()
    for row in rows:
        pending = unscored_days(conn, row["id"], limit=chunk)
        if pending:
            yield row["id"], min(pending), max(pending)
