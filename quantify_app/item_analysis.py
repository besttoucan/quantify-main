"""Everything Quantify knows about one item.

The point of the product is not the top sellers. Anyone can name those. The
point is that you can pick the crème brûlée nobody orders, open it, and get a
straight, thorough account of how it behaves: which days it belongs to, what
actually moves it, whether that has changed, what it trades against, how many to
make when running out costs more than throwing away, and how well this tool has
predicted it in the past.

Method, in order:

1. Strip out the two things that explain most of any food business: which day of
   the week it is, and the slow drift over the months. What is left is the
   residual, and every claim about weather or events is tested against that.
   An effect that vanishes once weekday is accounted for was a weekday effect
   wearing a costume.
2. Test each candidate driver against the residual with a proper t-test, then
   correct the whole family of tests for false discovery. A dozen tests at the
   5% level will hand you a false positive; this refuses to report it.
3. Build the predictive distribution from genuinely comparable past days, not
   from an assumed bell curve, and read the prep quantity off it at the fractile
   the economics actually call for.
"""

from __future__ import annotations

import math
import json
import sqlite3
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from .intelligence import (
    _history_for_item,
    _load_location,
    build_context,
    calendar_occasions,
    cost_share_for,
    forecast_item,
)
from .statistics import (
    benjamini_hochberg,
    describe_significance,
    exceedance,
    least_squares,
    mean,
    newsvendor_quantity,
    one_way_anova,
    pearson,
    quantile,
    variance,
    welch,
)

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# Last resort only. Every real call resolves the share through
# costs.item_cost_basis so the kitchen's number follows the costs screen.
DEFAULT_COST_SHARE = 0.30

# Each driver is (key into the context, human label, how to say it in a sentence).
DRIVERS: list[tuple[str, str, str]] = [
    ("temp_anomaly", "Temperature against normal", "each degree above the normal for this date here"),
    ("precipitation_scaled", "Rain", "a wet day against a dry one"),
    ("snow", "Snow", "a day with snow"),
    ("uv_scaled", "Sunshine", "a bright day against an overcast one"),
    ("daylight_hours", "Daylight", "each extra hour of daylight"),
    ("event_total", "Nearby activity", "a day with something big on nearby"),
    ("holiday", "Public holiday", "a public holiday"),
    ("occasion", "Named occasion", "a named occasion such as Valentine's Day"),
    ("holiday_eve", "Day before a holiday", "the day before a public holiday"),
    ("long_weekend", "Long weekend", "a day inside a long weekend"),
    ("payday", "Pay cycle", "a pay date"),
    ("month_end", "Month end", "the last day of a month"),
]


def _driver_value(context: dict[str, Any], key: str) -> float:
    value = context.get(key, 0.0)
    if key == "daylight_hours":
        return float(value) - 12.0
    return float(value or 0.0)


def _design_row(target: date, index: float) -> list[float]:
    """Intercept, six weekday indicators, and a linear trend in years."""
    weekday = target.weekday()
    return [1.0] + [1.0 if weekday == day else 0.0 for day in range(6)] + [index]


def _residualise(history: list[Any]) -> tuple[list[float], list[date], Any] | None:
    """Remove weekday and drift, and hand back what neither of them explains."""
    if len(history) < 40:
        return None
    first = history[0].target_date
    rows = [_design_row(row.target_date, (row.target_date - first).days / 365.25) for row in history]
    targets = [float(row.quantity) for row in history]
    model = least_squares(rows, targets)
    if model is None:
        return None
    return model.residuals, [row.target_date for row in history], model


def weekday_profile(history: list[Any]) -> dict[str, Any]:
    grouped: dict[int, list[float]] = defaultdict(list)
    for row in history:
        grouped[row.target_date.weekday()].append(float(row.quantity))
    test = one_way_anova([grouped.get(day, []) for day in range(7)])
    days = []
    overall = mean([float(row.quantity) for row in history]) or 1.0
    for day in range(7):
        values = sorted(grouped.get(day, []))
        if not values:
            days.append({"weekday": WEEKDAYS[day], "days": 0})
            continue
        days.append({
            "weekday": WEEKDAYS[day],
            "days": len(values),
            "typical": round(mean(values), 1),
            "median": round(quantile(values, 0.5), 1),
            "low": round(quantile(values, 0.25), 1),
            "high": round(quantile(values, 0.75), 1),
            "quietest": round(values[0], 1),
            "busiest": round(values[-1], 1),
            "vs_all_days_percent": round((mean(values) / overall - 1.0) * 100),
            "swing_percent": round((quantile(values, 0.75) - quantile(values, 0.25)) / max(1.0, mean(values)) * 100),
        })
    best = max((row for row in days if row["days"]), key=lambda row: row.get("typical", 0), default=None)
    worst = min((row for row in days if row["days"]), key=lambda row: row.get("typical", 0), default=None)
    return {
        "days": days,
        "matters": test["p"] < 0.05,
        "p": test["p"],
        "strength": describe_significance(test["p"]),
        "explains_percent": round(test["between_share"] * 100),
        "busiest_day": best["weekday"] if best else None,
        "quietest_day": worst["weekday"] if worst else None,
        "spread_percent": round((best["typical"] / max(0.1, worst["typical"]) - 1) * 100) if best and worst and worst.get("typical") else 0,
    }


def driver_effects(history: list[Any], target_context: dict[str, Any]) -> list[dict[str, Any]]:
    """Test every candidate driver against what weekday and drift left behind."""
    residualised = _residualise(history)
    if residualised is None:
        return []
    residuals, _dates, _model = residualised
    average = mean([float(row.quantity) for row in history]) or 1.0

    raw: list[dict[str, Any]] = []
    for key, label, phrase in DRIVERS:
        values = [_driver_value(row.context, key) for row in history]
        if variance(values) <= 1e-9:
            continue
        active = sum(1 for value in values if abs(value) > 1e-9)
        if active < 20:
            continue  # too few days carrying the condition to say anything at all
        model = least_squares([[1.0, value] for value in values], residuals)
        if model is None:
            continue
        coefficient = model.coefficients[1]
        p = model.p_value(1)
        low, high = model.confidence_interval(1)
        today = _driver_value(target_context, key)
        centre = mean(values)
        raw.append({
            "key": key, "label": label, "phrase": phrase,
            "per_unit": coefficient,
            "p": p,
            "days_observed": active,
            "today_value": today,
            "today_effect_units": coefficient * (today - centre),
            "range_low": low, "range_high": high,
            "sample": len(values),
        })

    if not raw:
        return []
    qvalues = benjamini_hochberg([row["p"] for row in raw])
    for row, q in zip(raw, qvalues):
        row["q"] = q
        thin = row["days_observed"] < 30
        row["provisional"] = thin
        row["strength"] = describe_significance(row["p"], q)
        if thin and row["strength"] in {"strong", "clear"}:
            # A real-looking effect on twenty-odd days is still twenty-odd days.
            row["strength"] = "suggestive"

        # Three verdicts, not two. Calling a one-in-seven result a driver is how
        # a forecast fills up with weather trivia nobody can act on.
        if q < 0.05:
            row["verdict"] = "established"
        elif q < 0.20:
            row["verdict"] = "watching"
        else:
            row["verdict"] = "rejected"
        row["established"] = row["verdict"] == "established"

        # Being real and being worth acting on are different questions. An
        # effect the size of a rounding error changes nothing you bake.
        typical_swing = abs(row["today_effect_units"])
        row["matters"] = typical_swing >= max(1.0, average * 0.03)
        if row["verdict"] == "established" and not row["matters"]:
            row["note"] = "Real, but too small to change what you make."
        elif row["verdict"] == "watching":
            need = max(0, 40 - row["days_observed"])
            row["note"] = (
                f"Leaning this way, not proven. About {need} more days carrying it would settle it."
                if need else "Leaning this way, but the effect is not clean enough to call yet."
            )
        else:
            row["note"] = ""

        binary = key in {"snow", "holiday", "occasion", "holiday_eve", "long_weekend", "payday", "month_end"}
        row["evidence"] = (
            f"{row['days_observed']} of {row['sample']} days carried this condition"
            if binary else f"measured on {row['sample']} days"
        )
        row["effect_percent"] = round(row["per_unit"] / average * 100, 1)
        row["today_effect_units"] = round(row["today_effect_units"], 1)
        row["today_effect_percent"] = round(row["today_effect_units"] / average * 100, 1)
        row["per_unit"] = round(row["per_unit"], 2)
        row["range_low"] = round(row["range_low"], 2)
        row["range_high"] = round(row["range_high"], 2)
    order = {"established": 0, "watching": 1, "rejected": 2}
    raw.sort(key=lambda row: (order[row["verdict"]], not row["matters"], -abs(row["today_effect_units"])))
    return raw


def trend(history: list[Any], target: date) -> dict[str, Any]:
    recent = [float(row.quantity) for row in history if row.target_date >= target - timedelta(days=28)]
    prior = [float(row.quantity) for row in history if target - timedelta(days=56) <= row.target_date < target - timedelta(days=28)]
    year_ago = [
        float(row.quantity) for row in history
        if target - timedelta(days=393) <= row.target_date <= target - timedelta(days=337)
    ]
    test = welch(recent, prior)
    long_run = None
    if len(history) >= 120:
        first = history[0].target_date
        model = least_squares(
            [[1.0, (row.target_date - first).days / 365.25] for row in history],
            [float(row.quantity) for row in history],
        )
        if model is not None:
            long_run = {
                "per_year": round(model.coefficients[1], 2),
                "p": model.p_value(1),
                "strength": describe_significance(model.p_value(1)),
            }
    return {
        "recent_average": round(mean(recent), 1) if recent else None,
        "prior_average": round(mean(prior), 1) if prior else None,
        "difference": round(test["difference"], 1),
        "p": test["p"],
        "strength": describe_significance(test["p"]),
        "moved": test["p"] < 0.05,
        "recent_days": len(recent),
        "prior_days": len(prior),
        "same_weeks_last_year": round(mean(year_ago), 1) if year_ago else None,
        "long_run": long_run,
    }


def seasonality(history: list[Any]) -> list[dict[str, Any]]:
    grouped: dict[int, list[float]] = defaultdict(list)
    for row in history:
        grouped[row.target_date.month].append(float(row.quantity))
    overall = mean([float(row.quantity) for row in history]) or 1.0
    names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    output = []
    for month in range(1, 13):
        values = grouped.get(month, [])
        output.append({
            "month": names[month - 1],
            "days": len(values),
            "typical": round(mean(values), 1) if values else None,
            "vs_year_percent": round((mean(values) / overall - 1.0) * 100) if values else None,
        })
    return output


def hourly_shape(conn: sqlite3.Connection, location_id: str, item_id: str, target: date) -> dict[str, Any]:
    rows = conn.execute(
        """SELECT hour, SUM(quantity) AS units, COUNT(DISTINCT date) AS days
           FROM sales_hourly WHERE location_id=? AND item_id=? AND date>=? AND date<?
           GROUP BY hour ORDER BY hour""",
        (location_id, item_id, (target - timedelta(days=180)).isoformat(), target.isoformat()),
    ).fetchall()
    total = sum(float(row["units"] or 0) for row in rows) or 1.0
    hours = []
    running = 0.0
    half = None
    most = None
    for row in rows:
        units = float(row["units"] or 0)
        share = units / total
        running += share
        hour = int(row["hour"])
        hours.append({
            "hour": hour,
            "label": f"{12 if hour % 12 == 0 else hour % 12} {'AM' if hour < 12 else 'PM'}",
            "share_percent": round(share * 100, 1),
            "per_day": round(units / max(1, int(row["days"] or 1)), 1),
            "cumulative_percent": round(running * 100),
        })
        if half is None and running >= 0.5:
            half = hours[-1]["label"]
        if most is None or share > most[1]:
            most = (hours[-1]["label"], share)
    return {
        "hours": hours,
        "busiest": most[0] if most else None,
        "busiest_share": round(most[1] * 100) if most else 0,
        "half_sold_by": half,
        "window_days": 180,
    }


def comparable_days(
    history: list[Any], target: date, context: dict[str, Any], expected: float | None = None
) -> dict[str, Any]:
    """The predictive distribution, built from days that genuinely resemble today.

    Past same-weekday trading tells you the *shape* of the spread: how wide it
    is, how lopsided, where the long tail sits. It does not know that today is
    sunny and last Tuesday was not, and if the item has been growing all year
    then the raw pool is stretched by that growth and not by real day-to-day
    risk. So each past day is turned into a ratio against its own local level,
    which cancels the drift, and those ratios are then applied to what the model
    expects today. Reading a prep quantity straight off the unshifted pool would
    hand back a number that ignores both the forecast and the trend.
    """
    same_weekday = [row for row in history if row.target_date.weekday() == target.weekday()]
    enough_weekdays = len(same_weekday) >= 26
    pool = same_weekday[-104:] if enough_weekdays else history[-120:]
    values = sorted(float(row.quantity) for row in pool)
    if not values:
        return {"days": 0}
    buckets = 9
    low, high = values[0], values[-1]
    width = max(1.0, (high - low) / buckets)
    histogram = []
    for index in range(buckets):
        start = low + index * width
        end = start + width
        count = sum(1 for value in values if (start <= value < end) or (index == buckets - 1 and value == high))
        histogram.append({"from": round(start), "to": round(end), "days": count})

    # Each pool day against the level that was normal around it, so an item that
    # has doubled over two years does not read as twice as unpredictable.
    ordered = [(row.target_date, float(row.quantity)) for row in pool]
    window = timedelta(days=56)
    ratios: list[float] = []
    for when, quantity in ordered:
        nearby = [other for moment, other in ordered if abs((moment - when).days) <= window.days]
        level = quantile(sorted(nearby), 0.5) if len(nearby) >= 5 else quantile(values, 0.5)
        if level > 0.5:
            ratios.append(quantity / level)
    if len(ratios) < 12:
        centre = quantile(values, 0.5) or 1.0
        ratios = [value / centre for value in values]

    base = float(expected) if expected is not None else quantile(values, 0.5)
    today_values = sorted(max(0.0, base * ratio) for ratio in ratios)
    scale = (base / quantile(values, 0.5)) if quantile(values, 0.5) > 0.5 else 1.0

    return {
        "days": len(values),
        "scale": round(scale, 3),
        "shift_percent": round((scale - 1.0) * 100),
        "spread_percent": round(
            (quantile(today_values, 0.75) - quantile(today_values, 0.25)) / max(1.0, base) * 100
        ),
        "today_low": round(quantile(today_values, 0.10)),
        "today_p25": round(quantile(today_values, 0.25)),
        "today_median": round(quantile(today_values, 0.50)),
        "today_p75": round(quantile(today_values, 0.75)),
        "today_high": round(quantile(today_values, 0.90)),
        "today_values": today_values,
        "basis": f"{len(values)} past {WEEKDAYS[target.weekday()]}s" if len(same_weekday) >= 26 else f"the last {len(values)} trading days",
        "lowest": round(values[0]),
        "highest": round(values[-1]),
        "median": round(quantile(values, 0.5)),
        "p10": round(quantile(values, 0.10)),
        "p25": round(quantile(values, 0.25)),
        "p75": round(quantile(values, 0.75)),
        "p90": round(quantile(values, 0.90)),
        "histogram": histogram,
        "values": values,
    }


def prep_advice(
    distribution: dict[str, Any], price: float, food_cost_share: float | None = None
) -> dict[str, Any]:
    """How many to make, once you account for it costing more to run out.

    Throwing a portion away loses its food cost. Missing a sale loses the whole
    margin, and sometimes the customer. Those are not the same number, so the
    right quantity is not the average.

    `food_cost_share` must come from `costs.item_cost_basis`, which resolves the
    owner's own figure for the item's category before falling back to anything
    built in. This number decides how many the kitchen makes every morning, so
    it has to be the one the owner entered on the costs screen, not a
    constant that quietly disagrees with it.
    """
    values = distribution.get("today_values") or distribution.get("values") or []
    if not values:
        return {}
    share = DEFAULT_COST_SHARE if food_cost_share is None else float(food_cost_share)
    share = min(0.95, max(0.01, share))
    cost_of_over = max(0.01, price * share)
    cost_of_under = max(0.01, price * (1.0 - share))
    result = newsvendor_quantity(values, cost_of_over, cost_of_under)
    quantity = int(round(result["quantity"]))
    levels = []
    for label, offset in (("Ten fewer", -10), ("Five fewer", -5), ("This number", 0), ("Five more", 5), ("Ten more", 10)):
        level = max(0, quantity + offset)
        chance = exceedance(values, level)
        leftover = mean([max(0.0, level - value) for value in values])
        missed = mean([max(0.0, value - level) for value in values])
        levels.append({
            "label": label,
            "quantity": level,
            "sell_out_percent": round(chance * 100),
            "typical_leftover": round(leftover, 1),
            "typical_missed": round(missed, 1),
            "cost_of_being_wrong": round(leftover * cost_of_over + missed * cost_of_under, 2),
        })
    at_quantity = next((row for row in levels if row["quantity"] == quantity), None)
    return {
        "quantity": quantity,
        "sell_out_percent": at_quantity["sell_out_percent"] if at_quantity else None,
        "typical_leftover": at_quantity["typical_leftover"] if at_quantity else None,
        # One number derived from the other, so the two never add to 101.
        "fractile_percent": (100 - at_quantity["sell_out_percent"]) if at_quantity else round(result["fractile"] * 100),
        "median": int(round(result["median"])),
        "cost_share_percent": round(share * 100),
        "reason": (
            f"Wasting one costs about ${cost_of_over:,.2f} in food. "
            f"Missing a sale costs about ${cost_of_under:,.2f} in margin."
        ),
        "levels": levels,
    }


def item_accuracy(conn: sqlite3.Connection, location_id: str, item_id: str,
                  as_of: date | None = None) -> dict[str, Any]:
    import json

    rows = conn.execute(
        "SELECT date, items_json FROM day_accuracy WHERE location_id=? AND date<? ORDER BY date DESC LIMIT 60",
        (location_id, (as_of or date.max).isoformat()),
    ).fetchall()
    called: list[float] = []
    sold: list[float] = []
    sold_out_days = 0
    for row in rows:
        try:
            entries = json.loads(row["items_json"] or "[]")
        except (ValueError, TypeError):
            continue
        for entry in entries:
            if entry.get("item_id") == item_id:
                called.append(float(entry.get("predicted") or 0))
                sold.append(float(entry.get("actual") or 0))
                sold_out_days += 1 if entry.get("sold_out") else 0
    if not sold:
        return {"days": 0}
    error = sum(abs(a - b) for a, b in zip(sold, called))
    total = sum(sold) or 1.0
    bias = mean([a - b for a, b in zip(sold, called)])
    return {
        "days": len(sold),
        "accuracy": round(max(0.0, 100 - error / total * 100), 1),
        "average_miss": round(error / len(sold), 1),
        "bias": round(bias, 1),
        "bias_direction": "usually low" if bias > 0.5 else "usually high" if bias < -0.5 else "no steady lean",
        "sold_out_days": sold_out_days,
    }


def related_items(
    conn: sqlite3.Connection,
    location_id: str,
    item_id: str,
    history: list[Any],
) -> list[dict[str, Any]]:
    """Items whose day-to-day movement lines up with this one, once weekday is removed.

    A negative pairing means people are choosing between them, so one going up is
    the other going down, not extra trade. That is the kind of thing
    nobody spots by eye and it changes how you prep both.
    """
    residualised = _residualise(history)
    if residualised is None:
        return []
    residuals, dates, _model = residualised
    mine = dict(zip((d.isoformat() for d in dates), residuals))
    start, end = min(dates).isoformat(), max(dates).isoformat()
    others = conn.execute(
        """SELECT m.id, m.name FROM menu_items m
           WHERE m.location_id=? AND m.id<>? AND EXISTS (
               SELECT 1 FROM sales s WHERE s.item_id=m.id AND s.date>=? AND s.date<=?)""",
        (location_id, item_id, start, end),
    ).fetchall()
    if not others:
        return []

    candidates: list[dict[str, Any]] = []
    for other in others:
        rows = conn.execute(
            "SELECT date, quantity FROM sales WHERE location_id=? AND item_id=? AND date>=? AND date<=? ORDER BY date",
            (location_id, other["id"], start, end),
        ).fetchall()
        if len(rows) < 60:
            continue
        theirs_dates = [date.fromisoformat(row["date"]) for row in rows]
        theirs_rows = [[1.0] + [1.0 if d.weekday() == day else 0.0 for day in range(6)]
                       + [(d - theirs_dates[0]).days / 365.25] for d in theirs_dates]
        model = least_squares(theirs_rows, [float(row["quantity"]) for row in rows])
        if model is None:
            continue
        theirs = dict(zip((d.isoformat() for d in theirs_dates), model.residuals))
        shared = sorted(set(mine) & set(theirs))
        if len(shared) < 60:
            continue
        result = pearson([mine[day] for day in shared], [theirs[day] for day in shared])
        candidates.append({
            "item_id": other["id"], "name": other["name"],
            "r": round(result["r"], 3), "p": result["p"], "days": result["n"],
        })

    if not candidates:
        return []
    qvalues = benjamini_hochberg([row["p"] for row in candidates])
    for row, q in zip(candidates, qvalues):
        row["q"] = q
        row["established"] = q < 0.10 and abs(row["r"]) >= 0.15
        row["kind"] = "moves with it" if row["r"] > 0 else "traded against it"
        row["strength"] = describe_significance(row["p"], q)
    candidates = [row for row in candidates if row["established"]]
    candidates.sort(key=lambda row: -abs(row["r"]))
    return candidates[:6]


def unusual_days(history: list[Any], limit: int = 3) -> list[dict[str, Any]]:
    """Days this item did something its own record cannot account for.

    A busy Saturday swings by more units than a quiet Monday simply because it is
    bigger, so scoring every day against one pooled spread fills the list with
    Saturdays and calls that a discovery. Each day is measured against how much
    *that weekday* normally varies instead.
    """
    residualised = _residualise(history)
    if residualised is None:
        return []
    residuals, dates, _model = residualised
    pooled = math.sqrt(variance(residuals)) or 1.0

    by_weekday: dict[int, list[float]] = defaultdict(list)
    for value, day in zip(residuals, dates):
        by_weekday[day.weekday()].append(value)
    spreads = {
        weekday: (math.sqrt(variance(values)) or pooled) if len(values) >= 20 else pooled
        for weekday, values in by_weekday.items()
    }

    flagged = []
    for value, day, row in zip(residuals, dates, history):
        score = value / spreads.get(day.weekday(), pooled)
        if abs(score) < 2.5:
            continue
        occasion = calendar_occasions(day.year).get(day)
        note = f"{occasion[0]}." if occasion else None
        if not note and row.context.get("precipitation_mm", 0) >= 8:
            note = f"{row.context['precipitation_mm'] / 25.4:.1f} in of rain."
        if not note and row.context.get("event_total", 0) >= 0.6:
            note = "Something big on nearby."
        if not note and row.stockout_minutes:
            note = "Ran out during service."
        flagged.append({
            "date": day.isoformat(),
            "weekday": WEEKDAYS[day.weekday()],
            "sold": int(round(row.quantity)),
            "above_normal": int(round(value)),
            "sigma": round(score, 1),
            "explained": bool(note),
            "note": note or "Nothing in the record explains it.",
        })
    # Days with a cause first, then the biggest swings.
    flagged.sort(key=lambda row: (not row["explained"], -abs(row["sigma"])))
    return flagged[:limit]


def _profile_call(conn: sqlite3.Connection, location_id: str, item_id: str,
                  target: date, forecast: dict[str, Any]) -> dict[str, Any]:
    """Read the same historical Expected as History, never the quantity to make."""
    from .transactions import ensure_day_scored, last_closed_day, normalized_score
    if target > last_closed_day(conn, location_id):
        return {"expected": forecast["expected"], "source": "live", "label": "Expected", "recorded_at": None}
    opening = conn.execute(
        "SELECT expected,locked_at FROM forecast_calls WHERE location_id=? AND item_id=? AND date=?",
        (location_id, item_id, target.isoformat()),
    ).fetchone()
    if opening:
        return {"expected": int(round(float(opening["expected"]))), "source": "stored",
                "label": "Recorded opening call", "recorded_at": opening["locked_at"]}
    score = ensure_day_scored(conn, location_id, target)
    if score:
        entries = json.loads(normalized_score(score)["items_json"] or "[]")
        saved = next((entry for entry in entries if entry.get("item_id") == item_id), None)
        if saved is not None:
            return {"expected": saved["predicted"], "source": "reconstructed",
                    "label": "Reconstructed expectation", "recorded_at": score["scored_at"]}
    return {"expected": forecast["expected"], "source": "reconstructed",
            "label": "Reconstructed expectation", "recorded_at": None}


def item_profile(conn: sqlite3.Connection, location_id: str, item_id: str, target: date) -> dict[str, Any]:
    """The full account of one item. Every figure carries what it rests on."""
    item = conn.execute(
        "SELECT * FROM menu_items WHERE id=? AND location_id=?", (item_id, location_id)
    ).fetchone()
    if item is None:
        raise ValueError("That item is not at this location")

    location = _load_location(conn, location_id)
    history, weather_map, events_map = _history_for_item(conn, location, item_id, target)
    forecast = forecast_item(conn, item, target)
    call = _profile_call(conn, location_id, item_id, target, forecast)
    context = build_context(location, target, weather_map.get(target.isoformat()), events_map.get(target.isoformat(), []), weather_map)

    distribution = (
        comparable_days(history, target, context, forecast["expected"]) if history else {"days": 0}
    )
    price = float(item["price"])
    first, cutoff = (target - timedelta(days=90)).isoformat(), target.isoformat()
    totals = conn.execute(
        """SELECT SUM(revenue) AS revenue, SUM(quantity) AS units FROM sales
           WHERE location_id=? AND date>=? AND date<?""",
        (location_id, first, cutoff),
    ).fetchone()
    mine = conn.execute(
        """SELECT SUM(revenue) AS revenue, SUM(quantity) AS units, COUNT(*) AS days,
                  MIN(date) AS first, MAX(date) AS last
           FROM sales WHERE location_id=? AND item_id=? AND date>=? AND date<?""",
        (location_id, item_id, first, cutoff),
    ).fetchone()
    rank_row = conn.execute(
        """SELECT COUNT(*) + 1 AS rank FROM (
              SELECT item_id, SUM(revenue) AS r FROM sales
              WHERE location_id=? AND date>=? AND date<? GROUP BY item_id
           ) WHERE r > (SELECT COALESCE(SUM(revenue), 0) FROM sales
                        WHERE location_id=? AND item_id=? AND date>=? AND date<?)""",
        (location_id, first, cutoff, location_id, item_id, first, cutoff),
    ).fetchone()
    item_count = int(conn.execute(
        "SELECT COUNT(DISTINCT item_id) AS n FROM sales WHERE location_id=? AND date>=? AND date<?", (location_id, first, cutoff)
    ).fetchone()["n"])

    revenue_share = 0.0
    if totals and float(totals["revenue"] or 0) > 0:
        revenue_share = float(mine["revenue"] or 0) / float(totals["revenue"]) * 100

    composition = conn.execute(
        "SELECT summary, confidence, components_json FROM item_composition WHERE menu_item_id=?", (item_id,)
    ).fetchone()
    last_sale = conn.execute(
        "SELECT MAX(date) AS last FROM sales WHERE location_id=? AND item_id=? AND date<?", (location_id, item_id, cutoff)
    ).fetchone()
    last_sale_date = last_sale["last"] if last_sale else None
    new_item = bool(forecast.get("new_item"))

    return {
        "item": {
            "id": item_id,
            "name": forecast["name"],
            "raw_name": item["name"],
            "category": item["category"],
            "price": price,
            "family": forecast["family"],
            "unit": forecast["production_unit"],
        },
        "date": target.isoformat(),
        "weekday": WEEKDAYS[target.weekday()],
        "new_item": new_item,
        "selling_days": int(forecast.get("selling_days") or len(history)),
        "last_sale_date": last_sale_date,
        # What a normal such weekday sells, the number every other figure is read against.
        "normal": {"weekday": WEEKDAYS[target.weekday()], "units": forecast["baseline"], "make": forecast.get("normal_make", forecast["baseline"])},
        "today": {
            "date": target.isoformat(),
            "weekday": WEEKDAYS[target.weekday()],
            "expected": call["expected"],
            "model_expected": call["expected"],
            "recomputed_expected": forecast["expected"],
            "call_source": call["source"],
            "call_label": call["label"],
            "call_recorded_at": call["recorded_at"],
            "normal": forecast["baseline"],
            "difference": call["expected"] - forecast["baseline"],
            "low": forecast["lower"],
            "high": forecast["upper"],
            "make": forecast["make"],
            "suggested_make": forecast.get("suggested_make", forecast["make"]),
            "normal_make": forecast.get("normal_make", forecast["baseline"]),
            "sell_out_percent": forecast.get("sell_out_percent"),
            "confidence": forecast["confidence"],
            "revenue": round(call["expected"] * price, 2),
            "override": forecast["override"],
        },
        "standing": {
            "revenue_rank": int(rank_row["rank"]) if rank_row else None,
            "of_items": item_count,
            "revenue_share_percent": round(revenue_share, 1),
            "units_90_days": int(mine["units"] or 0) if mine else 0,
            "revenue_90_days": round(float(mine["revenue"] or 0), 2) if mine else 0.0,
            "trading_days": int(mine["days"] or 0) if mine else 0,
            "history_days": len(history),
            "first_sold": mine["first"] if mine else None,
        },
        "weekday_profile": weekday_profile(history) if history else {"days": []},
        "drivers": driver_effects(history, context) if history else [],
        "trend": trend(history, target) if history else {},
        "seasonality": seasonality(history) if history else [],
        "hourly": hourly_shape(conn, location_id, item_id, target),
        "distribution": {key: value for key, value in distribution.items() if key != "values"},
        "prep": prep_advice(distribution, price, cost_share_for(conn, location_id, item_id)),
        "accuracy": item_accuracy(conn, location_id, item_id, target),
        "related": related_items(conn, location_id, item_id, history) if history else [],
        "unusual": unusual_days(history) if history else [],
        "composition": {
            "summary": composition["summary"],
            "confidence": composition["confidence"],
            "components": __import__("json").loads(composition["components_json"] or "[]"),
        } if composition else None,
    }
