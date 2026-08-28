"""What a day kept, once the food and the people are paid for.

The register says what was rung up. It says nothing about what any of it cost.
Quantify works that out from three things the owner controls: what a portion
costs as a share of its price, what an hour of somebody's time costs, and
whatever else the business pays for whether it trades or not.

This is an estimate assembled from settings, not accounting, and every screen
that shows it says so in those words. When an owner enters a real figure, the
real figure is used and the interface says which one it used.
"""

from __future__ import annotations

import math
import sqlite3
import uuid
from datetime import date, datetime, timezone
from typing import Any, Iterable, Sequence

from . import timezones
from .intelligence import service_hours

# ---------------------------------------------------------------------------
# Minimum wage
# ---------------------------------------------------------------------------
# Rates in force on 1 January 2026, read from the US Department of Labor state
# minimum wage table and from each state's own labor department. Twenty states
# either set no minimum of their own or set one below the federal floor, so they
# carry the federal 7.25.
#
# Four things are deliberately not modelled here, and all four are why the
# interface calls this an estimate, prints the date it was read, and lets the
# owner type over it:
#   1. Alaska, Florida and Oregon step up in the middle of the year, not in
#      January. Florida goes to 15.00 on 30 September 2026.
#   2. Cities and counties in California, Colorado, Illinois, Maryland,
#      Minnesota, New York and Washington run higher local minimums.
#      LOCAL_MINIMUM_WAGE below covers the ones Quantify already knows by name.
#   3. Roughly a third of these figures move every January because they are tied
#      to inflation. MINIMUM_WAGE_REVIEWED records when the table was last
#      checked, and a test fails when that date is more than a year old.
#   4. A tipped minimum is a different number again, and a kitchen almost never
#      pays anybody the minimum.
FEDERAL_MINIMUM_WAGE = 7.25
MINIMUM_WAGE_AS_OF = "2026-01-01"
MINIMUM_WAGE_REVIEWED = "2026-01-01"
MINIMUM_WAGE_SOURCE = (
    "US Department of Labor and state labor departments, rates in force 1 January 2026"
)

STATE_MINIMUM_WAGE: dict[str, float] = {
    "AL": 7.25, "AK": 13.00, "AZ": 15.15, "AR": 11.00, "CA": 16.90,
    "CO": 15.16, "CT": 16.94, "DE": 15.00, "DC": 17.95, "FL": 14.00,
    "GA": 7.25, "HI": 16.00, "ID": 7.25, "IL": 15.00, "IN": 7.25,
    "IA": 7.25, "KS": 7.25, "KY": 7.25, "LA": 7.25, "ME": 15.10,
    "MD": 15.00, "MA": 15.00, "MI": 13.73, "MN": 11.41, "MS": 7.25,
    "MO": 15.00, "MT": 10.85, "NE": 15.00, "NV": 12.00, "NH": 7.25,
    "NJ": 15.92, "NM": 12.00, "NY": 16.00, "NC": 7.25, "ND": 7.25,
    "OH": 11.00, "OK": 7.25, "OR": 15.05, "PA": 7.25, "RI": 16.00,
    "SC": 7.25, "SD": 11.85, "TN": 7.25, "TX": 7.25, "UT": 7.25,
    "VT": 14.42, "VA": 12.77, "WA": 17.13, "WV": 8.75, "WI": 7.25,
    "WY": 7.25, "PR": 10.50, "GU": 9.25, "VI": 10.50,
}

# Alabama, Georgia, Louisiana, Mississippi, South Carolina, Tennessee and
# Wyoming either have no state minimum or set one below 7.25. The federal floor
# applies to them, so the copy names the federal minimum rather than the state.
NO_STATE_MINIMUM = {"AL", "GA", "LA", "MS", "SC", "TN", "WY"}

# Where a city or county sets a minimum above its state's, and Quantify already
# knows the city from timezones.CITIES. This is not a complete list of local
# minimums in the United States and does not try to be.
LOCAL_MINIMUM_WAGE: dict[str, tuple[float, str]] = {
    "new york": (17.00, "New York City"),
    "new york city": (17.00, "New York City"),
    "nyc": (17.00, "New York City"),
    "manhattan": (17.00, "New York City"),
    "brooklyn": (17.00, "New York City"),
    "queens": (17.00, "New York City"),
    "bronx": (17.00, "New York City"),
    "staten island": (17.00, "New York City"),
    "yonkers": (17.00, "Westchester County"),
    "white plains": (17.00, "Westchester County"),
    "scarsdale": (17.00, "Westchester County"),
    "new rochelle": (17.00, "Westchester County"),
    "mount vernon": (17.00, "Westchester County"),
    "long island": (17.00, "Long Island"),
    "hempstead": (17.00, "Long Island"),
}

STATE_NAMES: dict[str, str] = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "DC": "the District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana",
    "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
    "PR": "Puerto Rico", "GU": "Guam", "VI": "the US Virgin Islands",
}


def resolve_state(region: str, city: str = "") -> str:
    """The two-letter state code for a location.

    locations.region already holds it for every location Quantify creates. The
    city fallback exists for rows imported from a register that filled the field
    with a full state name, or left it empty.
    """
    code = (region or "").strip().upper()
    if code in STATE_MINIMUM_WAGE:
        return code
    named = timezones.STATES.get((region or "").strip().lower())
    if named:
        return named[0]
    placed = timezones.CITIES.get((city or "").strip().lower())
    if placed and placed[0] in STATE_MINIMUM_WAGE:
        return placed[0]
    return ""


def state_minimum_wage(state: str) -> float:
    return STATE_MINIMUM_WAGE.get((state or "").upper(), FEDERAL_MINIMUM_WAGE)


def minimum_wage(state: str, city: str = "") -> tuple[float, str]:
    """The floor that applies here, and the name of the place that sets it."""
    local = LOCAL_MINIMUM_WAGE.get((city or "").strip().lower())
    state_rate = state_minimum_wage(state)
    if local and local[0] > state_rate:
        return local[0], local[1]
    if not state:
        return FEDERAL_MINIMUM_WAGE, ""
    if state in NO_STATE_MINIMUM:
        return FEDERAL_MINIMUM_WAGE, ""
    return state_rate, STATE_NAMES.get(state, state)


# ---------------------------------------------------------------------------
# Cost of goods
# ---------------------------------------------------------------------------
# Share of the menu price that the ingredients and packaging cost, by the family
# the menu interpreter already assigns. "menu-item" is deliberately absent: an
# item the interpreter could not place falls through to the location's own
# fallback percentage, which is the field the owner edits on the Costs screen.
DEFAULT_COST_SHARE = 0.30

FAMILY_COST_SHARE: dict[str, float] = {
    "burger": 0.33,
    "chicken-sandwich": 0.32,
    "pizza-whole": 0.26,
    "pizza-slice": 0.24,
    "fries-side": 0.22,
    "bread-loaf": 0.24,
    "pastry": 0.25,
    "bagel": 0.22,
    "breakfast-sandwich": 0.30,
    "sandwich": 0.31,
    "salad": 0.30,
    "coffee-hot": 0.16,
    "coffee-cold": 0.18,
    "shake": 0.26,
    "smoothie": 0.28,
    "dessert": 0.25,
    "beverage": 0.14,
    "entree": 0.32,
}

PERIOD_DAYS: dict[str, float] = {"day": 1.0, "week": 7.0, "month": 365.0 / 12.0}
PERIOD_LABELS: dict[str, str] = {"day": "a day", "week": "a week", "month": "a month"}

# Six orders an hour is roughly ten minutes of one person's time per order,
# counting the register, the line, expo and clean-down together. Twelve, which
# is what a first pass suggests, puts three people on a burger restaurant doing
# 295 covers and lands the wage bill near 13 percent of sales against a real
# 28 to 35. The payroll load is 7.65 for Social Security and Medicare, plus
# unemployment insurance and workers compensation on top.
SETTING_DEFAULTS: dict[str, Any] = {
    "hourly_wage": 0.0,               # 0 means follow the local minimum wage
    "payroll_load_percent": 18.0,
    "orders_per_person_per_hour": 6.0,
    "min_staff": 3,
    "max_staff": 14,
    "prep_hours": 1.5,
    "close_hours": 1.0,
    "default_cost_share": DEFAULT_COST_SHARE,
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clamp(value: Any, low: float, high: float, fallback: float) -> float:
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return fallback


# ---------------------------------------------------------------------------
# Stored settings
# ---------------------------------------------------------------------------

def cost_settings(conn: sqlite3.Connection, location_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM cost_settings WHERE location_id=?", (location_id,)).fetchone()
    if row is None:
        return {**SETTING_DEFAULTS, "configured": False, "updated_at": ""}
    stored = dict(row)
    return {
        "hourly_wage": float(stored["hourly_wage"] or 0),
        "payroll_load_percent": float(stored["payroll_load_percent"]),
        "orders_per_person_per_hour": float(stored["orders_per_person_per_hour"]),
        "min_staff": int(stored["min_staff"]),
        "max_staff": int(stored["max_staff"]),
        "prep_hours": float(stored["prep_hours"]),
        "close_hours": float(stored["close_hours"]),
        "default_cost_share": float(stored["default_cost_share"]),
        "configured": True,
        "updated_at": stored["updated_at"] or "",
    }


def wage_for_location(location: sqlite3.Row | dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    """The hourly figure, and a sentence saying where it came from."""
    city = str(location["city"] or "")
    state = resolve_state(str(location["region"] or ""), city)
    floor, place = minimum_wage(state, city)
    own = float(settings.get("hourly_wage") or 0)
    load = max(0.0, float(settings.get("payroll_load_percent") or 0))
    hourly = own if own > 0 else floor
    if own > 0:
        source = "your figure"
        detail = f"You set ${hourly:,.2f} an hour."
    elif place:
        source = "local minimum"
        detail = (
            f"The minimum wage in {place} is ${floor:,.2f} an hour. "
            f"Change it to what you pay."
        )
    else:
        source = "federal minimum"
        detail = (
            f"No state minimum applies here. The federal minimum is "
            f"${floor:,.2f} an hour. Change it to what you pay."
        )
    return {
        "state": state,
        "state_name": STATE_NAMES.get(state, ""),
        "place": place,
        "state_minimum": round(floor, 2),
        "hourly": round(hourly, 2),
        "payroll_load_percent": round(load, 1),
        "loaded": round(hourly * (1.0 + load / 100.0), 2),
        "source": source,
        "detail": detail,
        "as_of": MINIMUM_WAGE_AS_OF,
        "table_source": MINIMUM_WAGE_SOURCE,
    }


def item_cost_basis(conn: sqlite3.Connection, location_id: str, settings: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Per item: what a portion costs as a share of price, and which rule decided it.

    Order of precedence, most specific first:
      1. a percentage the owner typed for that item's register category
      2. the built-in share for the family the menu interpreter assigned
      3. this location's own fallback percentage
    """
    fallback = float(settings.get("default_cost_share") or DEFAULT_COST_SHARE)
    categories = {
        str(row["category"]).strip().lower(): float(row["cost_share"])
        for row in conn.execute(
            "SELECT category, cost_share FROM category_costs WHERE location_id=?", (location_id,)
        ).fetchall()
    }
    rows = conn.execute(
        """SELECT m.id, m.category, COALESCE(i.item_family,'menu-item') AS family
           FROM menu_items m
           LEFT JOIN menu_interpretations i ON i.menu_item_id = m.id
           WHERE m.location_id=?""",
        (location_id,),
    ).fetchall()
    basis: dict[str, dict[str, Any]] = {}
    for row in rows:
        category_share = categories.get(str(row["category"] or "").strip().lower())
        if category_share is not None:
            share, source = category_share, "your figure for this category"
        elif row["family"] in FAMILY_COST_SHARE:
            share, source = FAMILY_COST_SHARE[row["family"]], "the built-in figure for this kind of item"
        else:
            share, source = fallback, "this location's fallback percentage"
        basis[row["id"]] = {"share": share, "source": source}
    return basis


def recurring_costs(conn: sqlite3.Connection, location_id: str) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT id, name, amount, period FROM recurring_costs WHERE location_id=? ORDER BY amount DESC",
        (location_id,),
    ).fetchall()
    items = []
    daily = 0.0
    for row in rows:
        period = row["period"] if row["period"] in PERIOD_DAYS else "month"
        per_day = float(row["amount"] or 0) / PERIOD_DAYS[period]
        daily += per_day
        items.append({
            "id": row["id"],
            "name": row["name"],
            "amount": round(float(row["amount"] or 0), 2),
            "period": period,
            "period_label": PERIOD_LABELS[period],
            "per_day": round(per_day, 2),
        })
    return {"items": items, "daily": round(daily, 2)}


def cost_context(conn: sqlite3.Connection, location_id: str) -> dict[str, Any]:
    """Everything needed to cost any number of days, loaded once."""
    location = conn.execute(
        "SELECT id, city, region, open_hour, close_hour FROM locations WHERE id=?", (location_id,)
    ).fetchone()
    if location is None:
        raise ValueError("Unknown location")
    settings = cost_settings(conn, location_id)
    return {
        "location": location,
        "settings": settings,
        "wage": wage_for_location(location, settings),
        "basis": item_cost_basis(conn, location_id, settings),
        "recurring": recurring_costs(conn, location_id),
        "configured": settings["configured"],
    }


# ---------------------------------------------------------------------------
# Staffing
# ---------------------------------------------------------------------------

def staffing(orders_by_hour: dict[int, int], settings: dict[str, Any],
             location: sqlite3.Row | dict[str, Any] | None = None) -> dict[str, Any]:
    """How many people each hour needed, read off how many orders landed in it.

    Quantify does not have the schedule. It has the order times, hour by hour.
    A flat headcount across the week would put the same wage bill on a quiet
    Monday and a full Saturday.

    Every hour the location is open is charged, not only the hours that had
    orders, because a quiet hour is still paid for. service_hours() handles a
    close after midnight, which this codebase stores as close_hour 26.
    """
    rate = max(1.0, float(settings.get("orders_per_person_per_hour") or 6))
    floor = max(1, int(settings.get("min_staff") or 3))
    ceiling = max(floor, int(settings.get("max_staff") or 14))
    open_hours = service_hours(location) if location is not None else []
    hours: list[dict[str, int]] = []
    staff_hours = 0.0
    for hour in sorted(set(open_hours) | set(orders_by_hour)):
        count = int(orders_by_hour.get(hour, 0))
        needed = max(floor, min(ceiling, math.ceil(count / rate)))
        staff_hours += needed
        hours.append({"hour": hour, "orders": count, "staff": needed})
    edges = max(0.0, float(settings.get("prep_hours") or 0)) + max(0.0, float(settings.get("close_hours") or 0))
    staff_hours += edges * floor
    return {
        "hours": hours,
        "trading_hours": len(hours),
        "prep_and_close_hours": round(edges, 1),
        "staff_hours": round(staff_hours, 1),
        "busiest_staff": max((row["staff"] for row in hours), default=floor),
        "orders_per_person_per_hour": rate,
    }


# ---------------------------------------------------------------------------
# One day
# ---------------------------------------------------------------------------

def _cogs(rows: Iterable[sqlite3.Row], basis: dict[str, dict[str, Any]], fallback: float) -> float:
    total = 0.0
    for row in rows:
        entry = basis.get(row["item_id"])
        total += float(row["revenue"] or 0) * (entry["share"] if entry else fallback)
    return total


def _assemble(revenue: float, cogs: float, labour: float, other: float,
              shift: dict[str, Any]) -> dict[str, Any]:
    """The per-day figures only. Location-level detail is merged by day_costs."""
    total = cogs + labour + other
    profit = revenue - total
    return {
        "revenue": round(revenue, 2),
        "cogs": round(cogs, 2),
        "labour": round(labour, 2),
        "other": round(other, 2),
        "costs": round(total, 2),
        "gross_profit": round(profit, 2),
        "margin_percent": round(profit / revenue * 100) if revenue > 0 else None,
        "cogs_percent": round(cogs / revenue * 100) if revenue > 0 else None,
        "staff_hours": shift["staff_hours"],
        "busiest_staff": shift["busiest_staff"],
        "trading_hours": shift["trading_hours"],
        "prep_and_close_hours": shift["prep_and_close_hours"],
        "estimate": True,
    }


def _location_detail(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "hourly_wage": context["wage"]["hourly"],
        "loaded_wage": context["wage"]["loaded"],
        "wage_source": context["wage"]["source"],
        "wage_detail": context["wage"]["detail"],
        "recurring": context["recurring"]["items"],
        "configured": context["configured"],
    }


def closed_day(context: dict[str, Any]) -> dict[str, Any]:
    """A day with no trade. Nobody was on, but the recurring costs still ran."""
    other = context["recurring"]["daily"]
    return {
        "revenue": 0.0, "cogs": 0.0, "labour": 0.0, "other": round(other, 2),
        "costs": round(other, 2), "gross_profit": round(-other, 2),
        "margin_percent": None, "cogs_percent": None,
        "staff_hours": 0.0, "busiest_staff": 0, "trading_hours": 0,
        "prep_and_close_hours": 0.0, "estimate": True,
    }


def forecast_costs(
    conn: sqlite3.Connection,
    location_id: str,
    brief: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """What a day that has not happened yet is expected to cost, and keep.

    Same arithmetic as a closed day, with two substitutions. The orders per hour
    come from the forecast's own service curve rather than from the register,
    and the cost of goods is applied to expected revenue per item rather than to
    what was rung. Everything else, the wage, the staffing rule, the recurring
    costs, is identical, so the number on a Tuesday forecast is comparable to
    the number on last Tuesday's history.
    """
    context = context or cost_context(conn, location_id)
    settings = context["settings"]
    basis = context["basis"]
    fallback = float(settings["default_cost_share"])

    revenue = float(brief["summary"]["expected_revenue"])
    cogs = 0.0
    for item in brief["items"]:
        line = float(item["expected"]) * float(item["price"])
        entry = basis.get(item["item_id"])
        cogs += line * (entry["share"] if entry else fallback)

    # Spread the day's expected orders across the hours in the shape the
    # forecast expects trade to arrive in.
    curve = brief.get("service_curve") or []
    total_share = sum(float(row.get("share") or 0) for row in curve) or 1.0
    expected_orders = max(1, int(brief["summary"].get("expected_orders") or 1))
    orders_by_hour = {
        int(row["hour"]): int(round(expected_orders * float(row.get("share") or 0) / total_share))
        for row in curve
    }
    shift = staffing(orders_by_hour, settings, context["location"])
    labour = shift["staff_hours"] * context["wage"]["loaded"]
    return {
        **_assemble(revenue, cogs, labour, context["recurring"]["daily"], shift),
        **_location_detail(context),
        "forecast": True,
    }


def _hours_of(orders: Sequence[dict[str, Any]]) -> dict[int, int]:
    by_hour: dict[int, int] = {}
    for order in orders:
        hour = int(order["hour"])
        by_hour[hour] = by_hour.get(hour, 0) + 1
    return by_hour


def day_costs(
    conn: sqlite3.Connection,
    location_id: str,
    target: date,
    orders: Sequence[dict[str, Any]],
    revenue: float,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The cost of one closed day, using the order list the caller already has."""
    context = context or cost_context(conn, location_id)
    rows = conn.execute(
        "SELECT item_id, revenue FROM sales WHERE location_id=? AND date=?",
        (location_id, target.isoformat()),
    ).fetchall()
    cogs = _cogs(rows, context["basis"], float(context["settings"]["default_cost_share"]))
    shift = staffing(_hours_of(orders), context["settings"], context["location"])
    labour = shift["staff_hours"] * context["wage"]["loaded"]
    return {
        **_assemble(revenue, cogs, labour, context["recurring"]["daily"], shift),
        **_location_detail(context),
    }


def day_costs_many(
    conn: sqlite3.Connection,
    location_id: str,
    days: dict[str, dict[str, Any]],
    context: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """The same thing for a page of days, in one pass over `sales`.

    `days` maps an ISO date to {"orders": [...], "revenue": float}. The
    location-level block is left off deliberately: the caller sends it once
    beside the list instead of repeating it on every row.
    """
    context = context or cost_context(conn, location_id)
    if not days:
        return {}
    keys = list(days)
    rows = conn.execute(
        f"""SELECT date, item_id, revenue FROM sales
            WHERE location_id=? AND date IN ({','.join('?' * len(keys))})""",
        (location_id, *keys),
    ).fetchall()
    grouped: dict[str, list[sqlite3.Row]] = {key: [] for key in keys}
    for row in rows:
        grouped[row["date"]].append(row)
    fallback = float(context["settings"]["default_cost_share"])
    output: dict[str, dict[str, Any]] = {}
    for key, payload in days.items():
        shift = staffing(_hours_of(payload["orders"]), context["settings"], context["location"])
        labour = shift["staff_hours"] * context["wage"]["loaded"]
        cogs = _cogs(grouped.get(key, []), context["basis"], fallback)
        output[key] = _assemble(
            float(payload["revenue"]), cogs, labour, context["recurring"]["daily"], shift
        )
    return output


# ---------------------------------------------------------------------------
# The costs screen
# ---------------------------------------------------------------------------

def cost_view(conn: sqlite3.Connection, location_id: str) -> dict[str, Any]:
    """Everything the Costs screen shows, including a worked example."""
    context = cost_context(conn, location_id)
    fallback = float(context["settings"]["default_cost_share"])
    saved = {
        str(row["category"]).strip().lower(): float(row["cost_share"])
        for row in conn.execute(
            "SELECT category, cost_share FROM category_costs WHERE location_id=?", (location_id,)
        ).fetchall()
    }
    rows = conn.execute(
        """SELECT m.category AS category, COALESCE(i.item_family,'menu-item') AS family,
                  COUNT(*) AS items, AVG(m.price) AS price
           FROM menu_items m LEFT JOIN menu_interpretations i ON i.menu_item_id = m.id
           WHERE m.location_id=? AND m.active=1
           GROUP BY m.category, family""",
        (location_id,),
    ).fetchall()
    tally: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = row["category"] or "Uncategorized"
        entry = tally.setdefault(name, {"category": name, "items": 0, "price": 0.0, "families": {}})
        entry["items"] += int(row["items"])
        entry["price"] += float(row["price"] or 0) * int(row["items"])
        entry["families"][row["family"]] = entry["families"].get(row["family"], 0) + int(row["items"])
    categories = []
    for entry in sorted(tally.values(), key=lambda row: (-row["items"], row["category"])):
        # The figure shown is the blend actually applied across the items in
        # this category, not the share of whichever family happens to be most
        # common. On a mixed category those two are different numbers, and the
        # screen must not show one while the engine uses the other.
        default = sum(
            FAMILY_COST_SHARE.get(family, fallback) * count
            for family, count in entry["families"].items()
        ) / max(1, entry["items"])
        own = saved.get(entry["category"].strip().lower())
        categories.append({
            "category": entry["category"],
            "items": entry["items"],
            "average_price": round(entry["price"] / max(1, entry["items"]), 2),
            "default_percent": round(default * 100),
            "percent": round((own if own is not None else default) * 100),
            "set_by_owner": own is not None,
        })

    latest = conn.execute(
        "SELECT date, SUM(revenue) AS revenue FROM sales WHERE location_id=? GROUP BY date ORDER BY date DESC LIMIT 1",
        (location_id,),
    ).fetchone()
    example = None
    if latest:
        from .transactions import day_orders  # imported here so costs.py stays free of a cycle
        target = date.fromisoformat(latest["date"])
        example = day_costs(conn, location_id, target, day_orders(conn, location_id, target),
                            float(latest["revenue"] or 0), context)
        example["date"] = latest["date"]
    return {
        "settings": context["settings"],
        "wage": context["wage"],
        "categories": categories,
        "recurring": context["recurring"]["items"],
        "recurring_daily": context["recurring"]["daily"],
        "example": example,
    }


def save_cost_settings(conn: sqlite3.Connection, location_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Write the whole screen back. Percentages arrive as percentages.

    Operators think and talk in percent, so the form sends 33, not 0.33. The
    conversion happens once, here, rather than in five places in the browser.
    """
    now = _utc_now()
    conn.execute(
        """INSERT INTO cost_settings(
               location_id, hourly_wage, payroll_load_percent, orders_per_person_per_hour,
               min_staff, max_staff, prep_hours, close_hours, default_cost_share, updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(location_id) DO UPDATE SET
               hourly_wage=excluded.hourly_wage,
               payroll_load_percent=excluded.payroll_load_percent,
               orders_per_person_per_hour=excluded.orders_per_person_per_hour,
               min_staff=excluded.min_staff, max_staff=excluded.max_staff,
               prep_hours=excluded.prep_hours, close_hours=excluded.close_hours,
               default_cost_share=excluded.default_cost_share, updated_at=excluded.updated_at""",
        (
            location_id,
            _clamp(payload.get("hourly_wage"), 0.0, 200.0, 0.0),
            _clamp(payload.get("payroll_load_percent"), 0.0, 60.0, 18.0),
            _clamp(payload.get("orders_per_person_per_hour"), 1.0, 120.0, 6.0),
            int(_clamp(payload.get("min_staff"), 1, 40, 3)),
            int(_clamp(payload.get("max_staff"), 1, 80, 14)),
            _clamp(payload.get("prep_hours"), 0.0, 12.0, 1.5),
            _clamp(payload.get("close_hours"), 0.0, 12.0, 1.0),
            _clamp(payload.get("default_cost_share"), 1.0, 95.0, 30.0) / 100.0,
            now,
        ),
    )

    conn.execute("DELETE FROM category_costs WHERE location_id=?", (location_id,))
    for row in (payload.get("categories") or []):
        name = str(row.get("category", "")).strip()[:80]
        if not name:
            continue
        conn.execute(
            "INSERT OR REPLACE INTO category_costs(location_id, category, cost_share, updated_at) VALUES(?,?,?,?)",
            (location_id, name, _clamp(row.get("percent"), 0.0, 95.0, 30.0) / 100.0, now),
        )

    # The list is replaced wholesale because the form submits the whole list.
    # Nothing else in the database points at a recurring cost row, so there is
    # no identity worth preserving and a diff would only add ways to get it wrong.
    conn.execute("DELETE FROM recurring_costs WHERE location_id=?", (location_id,))
    for row in (payload.get("recurring") or []):
        name = str(row.get("name", "")).strip()[:80]
        amount = _clamp(row.get("amount"), 0.0, 5_000_000.0, 0.0)
        if not name or amount <= 0:
            continue
        period = str(row.get("period", "month"))
        conn.execute(
            "INSERT INTO recurring_costs(id, location_id, name, amount, period, updated_at) VALUES(?,?,?,?,?,?)",
            (uuid.uuid4().hex, location_id, name, amount, period if period in PERIOD_DAYS else "month", now),
        )
    conn.commit()
    return cost_view(conn, location_id)
