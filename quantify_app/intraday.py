"""Re-forecasting during service, and keeping the morning call on the record.

Two things happen here and they must never be confused.

The first is the re-forecast. At the top of every service hour Quantify reads
what the register has actually rung today and revises what it expects the day to
finish at. The model is not refitted. Nothing has been learned about Tuesdays in
the last sixty minutes. What has been learned is whether this Tuesday is running
to the shape a Tuesday has here, so the morning call is held as the prior and
only the pace is updated. Weather and events are deliberately not re-read
mid-service either: an unforecast storm is already arriving through the observed
pace, and feeding it in a second time through the weather feature would count it
twice.

The second is what a revision may do to the score. Nothing. The call made before
the doors opened is written once and is the only number the accuracy record is
measured against. A revision made at nine in the evening has watched almost the
whole day and would score close to perfect; reporting that as accuracy would
make the record worthless. Revisions are stored so a day can be read back
afterwards, and they are never scored as forecasts of that day.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from . import localtime
from .intelligence import (
    CURVE_REFERENCE_DAYS,
    MODEL_VERSION,
    _load_location,
    _settings,
    clamp,
    hour_label,
    item_hour_curves,
    location_hour_curve,
    opening_calls,
    service_slots,
)

# How much of a morning's deviation carries into the rest of the day. Below one
# because a day that starts badly does sometimes recover, and well above a half
# because most of what makes a day deviate, the weather, a road closure, a slow
# week, a school holiday, is still there in the afternoon. This is a judgement,
# stated as one. To replace it with a measured number, correlate
# sum(actual before noon) / sum(called before noon) against the same ratio after
# noon across scored days.
PACE_PERSISTENCE = 0.85

# The raw pace ratio is clamped before use. Its denominator can be small enough
# at seven in the morning to read twelve times normal off four covers.
PACE_FLOOR = 0.35
PACE_CEILING = 2.50

# Relative variance in the cumulative share of the day, at the reference of
# sixteen same-weekday services, scaled by how much of the day is left. It goes
# to zero as the day closes out, because by then the cumulative share is known
# to be one. A location with fewer observations gets this scaled up in
# proportion, because its curve is worth proportionally less.
CURVE_NOISE = 0.0035

# Fewest same-weekday services behind the curve before any revision is written.
MIN_CURVE_DAYS = 4

MIN_EXPECTED_UNITS = 1.5   # per item, before its own counter is read at all
MIN_LOCATION_UNITS = 5.0   # location wide, before anything is published


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def trading_date(location: sqlite3.Row | dict[str, Any], moment: datetime) -> date:
    """The date a moment belongs to for a location that may trade past midnight.

    Two in the morning at a place open until three belongs to yesterday's
    service. Ten in the morning at that same place belongs to today, which has
    not started yet but is the day the next call is for. The test is against the
    closing hour, not the opening one: everything before close belongs to the
    service that is still running.
    """
    closes = int(location["close_hour"])
    if closes > 24 and moment.hour < (closes - 24):
        return moment.date() - timedelta(days=1)
    return moment.date()


def _measurement_weight(
    sigma: float, expected_units: float, share: float, curve_days: int
) -> float:
    """How much of the observed pace to believe: signal over signal plus noise.

      signal    sigma squared, how far this thing's days genuinely land from
                their own call. Measured, not assumed: for the location from its
                own scored day totals, for an item from the validation error
                stored with the call.
      counting  one over the units expected by now. The count is Poisson, so a
                thin item's pace is mostly arithmetic noise.
      shape     error in the cumulative share. Largest at the start of service,
                zero by close, and scaled by how many same-weekday services the
                curve is actually built from. A curve from three Tuesdays does
                not deserve the confidence of a curve from sixteen.

    An item that normally lands within twelve percent of its call needs a lot of
    evidence before a slow morning is believed. An item that swings forty
    percent either way needs very little, because swinging is what it does.
    """
    signal = max(1e-6, sigma * sigma)
    counting = 1.0 / max(1e-6, expected_units)
    share = clamp(share, 1e-4, 1.0)
    curve_scale = CURVE_REFERENCE_DAYS / max(1, curve_days)
    shape = CURVE_NOISE * curve_scale * (1.0 - share) / share
    return clamp(PACE_PERSISTENCE * signal / (signal + counting + shape), 0.0, 1.0)


@dataclass
class ItemRevision:
    item_id: str
    opening: float
    sold_so_far: float
    expected_share: float
    pace: float
    weight: float
    revised: float


def revise_menu(
    calls: dict[str, dict[str, Any]],
    curves: dict[str, dict[int, float]],
    sold: dict[str, dict[int, float]],
    elapsed: list[int],
    location_sigma: float,
    fallback_curve: dict[int, float],
    curve_days: int = CURVE_REFERENCE_DAYS,
) -> tuple[list[ItemRevision], dict[str, float]]:
    """Revised end-of-day expectations for the whole menu, in two stages.

    `elapsed` is the list of slots that have fully completed. `sold` and
    `curves` are keyed by slot.

    The two stages are the one decision here that is not obvious. A quiet item
    with four units expected by eleven in the morning carries almost no
    information of its own: one customer buying two is a fifty percent swing. On
    a single stage estimator it would barely move even on a day where the whole
    restaurant is visibly a third down. That is wrong. The evidence for that day
    exists, it is just not in that item's counter.

    So the day is estimated first on the summed menu, where the counts are large
    and the shrinkage is light. Every item is then moved by the day's revision,
    and departs from it only to the extent its own counter is strong enough to
    say it should. The quiet item rides the day. The best seller may argue.
    """
    day_opening = day_expected_now = day_sold = 0.0
    for item_id, call in calls.items():
        opening = max(0.0, float(call["expected"]))
        curve = curves.get(item_id) or fallback_curve
        share = clamp(sum(curve.get(slot, 0.0) for slot in elapsed), 0.0, 1.0)
        day_opening += opening
        day_expected_now += opening * share
        day_sold += sum(sold.get(item_id, {}).get(slot, 0.0) for slot in elapsed)

    day_share = day_expected_now / day_opening if day_opening > 0 else 0.0
    day_raw = day_sold / day_expected_now if day_expected_now > 0 else 1.0
    if day_expected_now >= MIN_LOCATION_UNITS and day_share > 0:
        day_pace = clamp(day_raw, PACE_FLOOR, PACE_CEILING)
        day_weight = _measurement_weight(location_sigma, day_expected_now, day_share, curve_days)
    else:
        day_raw = day_pace = 1.0
        day_weight = 0.0
    day_multiplier = 1.0 + day_weight * (day_pace - 1.0)

    revisions: list[ItemRevision] = []
    for item_id, call in calls.items():
        opening = max(0.0, float(call["expected"]))
        curve = curves.get(item_id) or fallback_curve
        share = clamp(sum(curve.get(slot, 0.0) for slot in elapsed), 0.0, 1.0)
        remaining = max(0.0, 1.0 - share)
        item_sold = sum(sold.get(item_id, {}).get(slot, 0.0) for slot in elapsed)
        expected_now = opening * share

        if opening <= 0.5 or expected_now < MIN_EXPECTED_UNITS:
            # Too little was expected by now for this counter to mean anything.
            # It still moves with the day, because the day is real evidence.
            item_pace, item_weight, multiplier = day_pace, 0.0, day_multiplier
        else:
            item_raw = item_sold / expected_now
            item_pace = clamp(item_raw, PACE_FLOOR, PACE_CEILING)
            sigma = clamp(1.25 * float(call.get("validation_wape") or 0.30), 0.08, 0.60)
            item_weight = _measurement_weight(sigma, expected_now, share, curve_days)
            # Compared on the raw ratios, not the clamped ones. Two paces that
            # both hit the same clamp would otherwise divide to exactly one, and
            # this whole stage would do nothing in precisely the thin early
            # hours it exists for.
            excess = clamp(item_raw / max(0.25, day_raw), 0.40, 2.20)
            multiplier = day_multiplier * (1.0 + item_weight * (excess - 1.0))

        revised = item_sold + opening * remaining * multiplier
        revisions.append(ItemRevision(
            item_id=item_id,
            opening=opening,
            sold_so_far=item_sold,
            expected_share=share,
            pace=item_pace,
            weight=clamp(item_weight if expected_now >= MIN_EXPECTED_UNITS else day_weight, 0.0, 1.0),
            # A revision can never sit below what the register has already rung.
            revised=max(item_sold, revised),
        ))

    day = {
        "opening": day_opening, "sold": day_sold, "expected_now": day_expected_now,
        "share": day_share, "pace": day_pace, "weight": day_weight,
        "multiplier": day_multiplier,
    }
    return revisions, day


def has_opening_call(conn: sqlite3.Connection, location_id: str, target_date: date) -> bool:
    return conn.execute(
        "SELECT 1 FROM forecast_calls WHERE location_id=? AND date=? LIMIT 1",
        (location_id, target_date.isoformat()),
    ).fetchone() is not None


def lock_opening_call(
    conn: sqlite3.Connection,
    location_id: str,
    target_date: date,
    items: list[dict[str, Any]],
) -> int:
    """Write the call for a day, once, and only before that day opens.

    The rule is narrow and has no exceptions: a call counts as the opening call
    only if it was made on this location's own clock, for its current trading
    date, while the doors were still shut. If the server was not running that
    morning there is no opening call, the day is scored against one rebuilt from
    sales up to the day before, and the interface says so. Filing a two in the
    afternoon call as a morning call would put a number on the record that
    already knew half the answer.

    Returns items written, and zero when a call exists or service has started.
    """
    location = _load_location(conn, location_id)
    now = localtime.now(location["timezone"])
    if trading_date(location, now) != target_date:
        return 0
    slots = service_slots(location)
    if not slots:
        return 0
    opens = slots[0]
    # A location that opens at midnight is always open by its own clock, so
    # there is no shut window to lock in. It is scored by reconstruction and
    # labelled that way rather than given a call that is not one.
    if opens <= 0 or now.hour >= opens:
        return 0
    if has_opening_call(conn, location_id, target_date):
        return 0

    stamp, local = _utc_now(), now.strftime("%H:%M")
    written = 0
    for item in items:
        if item.get("new_item"):
            # Under a week of sales: there is no call to lock, and scoring a
            # zero against whatever it sells would put a miss on the record.
            continue
        conn.execute(
            """INSERT OR IGNORE INTO forecast_calls(
                   location_id,date,item_id,expected,lower,upper,price,overridden,
                   validation_wape,model_version,locked_at,locked_local)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                location_id, target_date.isoformat(), item["item_id"],
                float(item["expected"]), float(item["lower"]), float(item["upper"]),
                float(item["price"]), 1 if item.get("override") else 0,
                float((item.get("model") or {}).get("validation_wape") or 0.30),
                MODEL_VERSION, stamp, local,
            ),
        )
        written += 1
    conn.commit()
    return written


def _location_sigma(conn: sqlite3.Connection, location_id: str) -> float:
    """How far this location's day totals actually land from their call.

    Taken from the scored record rather than assumed. This is the day total
    error, not the per item error the accuracy column reports, because what the
    pace is trying to detect is a whole day moving.
    """
    rows = conn.execute(
        """SELECT * FROM day_accuracy
           WHERE location_id=?
           ORDER BY date DESC LIMIT 45""",
        (location_id,),
    ).fetchall()
    from .transactions import reconciled_score
    rows = [reconciled_score(conn, dict(row)) for row in rows]
    rows = [row for row in rows if row["score_complete"] and row["actual_units"] > 0 and row["predicted_units"] > 0]
    if len(rows) < 7:
        return 0.16
    total = sum(
        (float(row["actual_units"]) / float(row["predicted_units"]) - 1.0) ** 2 for row in rows
    )
    return clamp((total / len(rows)) ** 0.5, 0.05, 0.40)


def sold_by_slot(
    conn: sqlite3.Connection, location: sqlite3.Row, target_date: date
) -> dict[str, dict[int, float]]:
    """What has rung this service, keyed by item and slot.

    Two calendar dates are read, because an hour past midnight is stored under
    the following date. Without this a bar open 11 to 2 reads its last two hours
    as zero sales every night of its life.
    """
    allowed = set(service_slots(location))
    rows = conn.execute(
        """SELECT item_id, date, hour, SUM(quantity) AS qty FROM sales_hourly
           WHERE location_id=? AND date IN (?,?) GROUP BY item_id, date, hour""",
        (
            location["id"],
            target_date.isoformat(),
            (target_date + timedelta(days=1)).isoformat(),
        ),
    ).fetchall()
    out: dict[str, dict[int, float]] = defaultdict(dict)
    for row in rows:
        offset = (date.fromisoformat(row["date"]) - target_date).days
        slot = offset * 24 + int(row["hour"])
        if slot in allowed:
            out[row["item_id"]][slot] = float(row["qty"] or 0)
    return out


def completed_slots(location: sqlite3.Row, moment: datetime, target_date: date) -> list[int]:
    """Slots that have fully finished on this location's clock.

    A part finished hour is never counted. At twenty past one, one o'clock has
    taken twenty minutes of trade and would read as two thirds down for no
    reason at all.
    """
    elapsed_hours = (moment.date() - target_date).days * 24 + moment.hour
    return [slot for slot in service_slots(location) if slot < elapsed_hours]


def slots_needing_revision(
    conn: sqlite3.Connection, location_id: str, target_date: date, completed: list[int]
) -> list[int]:
    have = {
        int(row["slot"])
        for row in conn.execute(
            "SELECT DISTINCT slot FROM forecast_revisions WHERE location_id=? AND date=?",
            (location_id, target_date.isoformat()),
        ).fetchall()
    }
    return [slot for slot in sorted(completed) if slot not in have]


def revise_day(
    conn: sqlite3.Connection, location_id: str, target_date: date, through_slot: int
) -> int:
    """Revise the day using every service hour that has fully completed.

    Idempotent: re-running for the same slot writes the same rows. Revisions
    accept DO UPDATE on purpose. Unlike the opening call, a revision is a
    working number, and a late arriving order that changes what an already
    elapsed hour rang should correct that hour's revision rather than leave it
    wrong.
    """
    location = _load_location(conn, location_id)
    elapsed = [slot for slot in service_slots(location) if slot <= through_slot]
    if not elapsed:
        return 0
    calls = {
        key[1]: value
        for key, value in opening_calls(conn, location_id, target_date, target_date).items()
    }
    if not calls:
        return 0

    curve = location_hour_curve(conn, location, target_date, _settings(conn, location_id))
    if not curve["learned"] or curve["observed_days"] < MIN_CURVE_DAYS:
        # Without a learned shape there is no way to say what should have rung
        # by now, and a flat guess reads a perfectly ordinary morning as running
        # ahead. Nothing is published until the shape is real.
        return 0

    shares = curve["shares"]
    revisions, day = revise_menu(
        calls,
        item_hour_curves(conn, location, target_date, shares),
        sold_by_slot(conn, location, target_date),
        elapsed,
        _location_sigma(conn, location_id),
        shares,
        curve["observed_days"],
    )
    if day["expected_now"] < MIN_LOCATION_UNITS:
        return 0

    stamp = _utc_now()
    conn.executemany(
        """INSERT INTO forecast_revisions(
               location_id,date,slot,item_id,opening,sold_so_far,
               expected_share,pace,weight,revised,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(location_id,date,slot,item_id) DO UPDATE SET
               opening=excluded.opening, sold_so_far=excluded.sold_so_far,
               expected_share=excluded.expected_share, pace=excluded.pace,
               weight=excluded.weight, revised=excluded.revised,
               created_at=excluded.created_at""",
        [
            (
                location_id, target_date.isoformat(), through_slot, row.item_id,
                round(row.opening, 3), round(row.sold_so_far, 3),
                round(row.expected_share, 5), round(row.pace, 4),
                round(row.weight, 4), round(row.revised, 3), stamp,
            )
            for row in revisions
        ],
    )
    conn.commit()
    return len(revisions)


def _revision_payload(
    conn: sqlite3.Connection,
    location_id: str,
    target_date: date,
    slot: int,
    price: dict[str, float],
    name: dict[str, str],
) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT * FROM forecast_revisions WHERE location_id=? AND date=? AND slot=?",
        (location_id, target_date.isoformat(), slot),
    ).fetchall()
    if not rows:
        return {}
    opening_units = sum(float(row["opening"]) for row in rows)
    revised_units = sum(float(row["revised"]) for row in rows)
    sold_units = sum(float(row["sold_so_far"]) for row in rows)
    share = sum(
        float(row["expected_share"]) * float(row["opening"]) for row in rows
    ) / max(1e-6, opening_units)

    def money_of(key: str) -> float:
        return sum(float(row[key]) * price.get(row["item_id"], 0.0) for row in rows)

    items = sorted(
        (
            {
                "item_id": row["item_id"],
                "name": name.get(row["item_id"], row["item_id"]),
                "opening": int(round(float(row["opening"]))),
                "sold_so_far": int(round(float(row["sold_so_far"]))),
                "revised": int(round(float(row["revised"]))),
                "difference": int(round(float(row["revised"]) - float(row["opening"]))),
            }
            for row in rows
        ),
        key=lambda row: abs(row["difference"]), reverse=True,
    )
    return {
        "slot": slot, "label": hour_label(slot),
        "expected_share_percent": round(share * 100),
        "sold_units": int(round(sold_units)), "sold_sales": round(money_of("sold_so_far"), 2),
        "called_by_now_units": int(round(opening_units * share)),
        "called_by_now_sales": round(money_of("opening") * share, 2),
        "opening_units": int(round(opening_units)), "opening_sales": round(money_of("opening"), 2),
        "revised_units": int(round(revised_units)), "revised_sales": round(money_of("revised"), 2),
        "difference_units": int(round(revised_units - opening_units)),
        "difference_sales": round(money_of("revised") - money_of("opening"), 2),
        "items": [row for row in items if abs(row["difference"]) >= 2][:8],
    }


def day_state(
    conn: sqlite3.Connection, location_id: str, target_date: date, brief: dict[str, Any]
) -> dict[str, Any] | None:
    """What the interface needs to show the day as it is actually running.

    None on any date that is not this location's current trading date. A plan
    for next Thursday has no live pace and must not pretend to.
    """
    location = _load_location(conn, location_id)
    now = localtime.now(location["timezone"])
    if trading_date(location, now) != target_date:
        return None

    slots = service_slots(location)
    if not slots:
        return None
    done = set(completed_slots(location, now, target_date))
    current = (now.date() - target_date).days * 24 + now.hour

    price = {row["item_id"]: float(row["price"]) for row in brief["items"]}
    name = {row["item_id"]: row["name"] for row in brief["items"]}
    called = {row.get("slot", row["hour"]): row for row in brief["service_curve"]}

    rung_units: dict[int, float] = defaultdict(float)
    rung_sales: dict[int, float] = defaultdict(float)
    for item_id, by_slot in sold_by_slot(conn, location, target_date).items():
        for slot, quantity in by_slot.items():
            rung_units[slot] += quantity
            rung_sales[slot] += quantity * price.get(item_id, 0.0)

    strip = [
        {
            "slot": slot,
            "hour": slot % 24,
            "label": hour_label(slot),
            "called_units": int((called.get(slot) or {}).get("units") or 0),
            "called_sales": float((called.get(slot) or {}).get("revenue") or 0.0),
            "rung_units": round(rung_units.get(slot, 0.0), 1),
            "rung_sales": round(rung_sales.get(slot, 0.0), 2),
            "state": "done" if slot in done else ("now" if slot == current else "ahead"),
        }
        for slot in slots
    ]

    call_row = conn.execute(
        "SELECT locked_local FROM forecast_calls WHERE location_id=? AND date=? LIMIT 1",
        (location_id, target_date.isoformat()),
    ).fetchone()
    latest = conn.execute(
        "SELECT MAX(slot) AS slot FROM forecast_revisions WHERE location_id=? AND date=?",
        (location_id, target_date.isoformat()),
    ).fetchone()

    return {
        "in_service": slots[0] <= current <= slots[-1],
        "locked_local": call_row["locked_local"] if call_row else None,
        "hours": strip,
        "revision": (
            _revision_payload(conn, location_id, target_date, int(latest["slot"]), price, name)
            if latest and latest["slot"] is not None else None
        ),
    }


def catch_up(conn: sqlite3.Connection, location_id: str) -> int:
    """Write any revision this location is owed for the service running now.

    Called on a timer and after a register sync. Every completed hour that has
    no revision yet gets one, so a server that was down for an afternoon fills
    the gap rather than skipping it.
    """
    location = _load_location(conn, location_id)
    now = localtime.now(location["timezone"])
    target = trading_date(location, now)
    completed = completed_slots(location, now, target)
    written = 0
    for slot in slots_needing_revision(conn, location_id, target, completed):
        written += revise_day(conn, location_id, target, slot)
    return written
