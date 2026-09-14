from __future__ import annotations

import copy
import hashlib
import json
import math
import sqlite3
import statistics
import threading
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from .menu_intelligence import ensure_location_interpretations, menu_intelligence_view

MODEL_VERSION = "quantify-context-ensemble-2.0"

_CACHE_LOCK = threading.RLock()
_CONTEXT_CACHE: dict[tuple[Any, ...], tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]] = {}
_MODEL_CACHE: dict[tuple[Any, ...], dict[str, Any]] = {}
_BRIEF_CACHE: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}
_BRIEF_LOCKS = [threading.RLock() for _ in range(32)]
_BRIEF_CACHE_SECONDS = 300


def _db_identity(conn: sqlite3.Connection) -> str:
    rows = conn.execute("PRAGMA database_list").fetchall()
    for row in rows:
        if row["name"] == "main":
            return row["file"] or f":memory:{id(conn)}"
    return f"connection:{id(conn)}"


def _data_version(conn: sqlite3.Connection, location_id: str, *, training: bool = False) -> str:
    # Include values as well as timestamps: two edits can land in one second,
    # and a register can correct an existing date without adding a sale row.
    revision = (conn.total_changes, conn.execute("PRAGMA data_version").fetchone()[0])
    memo = getattr(conn, "_quantify_data_versions", {})
    memo_key = (location_id, training)
    if memo_key in memo and memo[memo_key][0] == revision:
        return memo[memo_key][1]
    parts = []
    queries = [
        "SELECT COUNT(*),MAX(date),SUM(quantity),SUM(revenue),SUM(stockout_minutes) FROM sales WHERE location_id=?",
        "SELECT COUNT(*),MAX(date),SUM(quantity),SUM(quantity*hour) FROM sales_hourly WHERE location_id=?",
        "SELECT * FROM locations WHERE id=?",
        "SELECT * FROM menu_items WHERE location_id=? ORDER BY id",
        "SELECT * FROM integrations WHERE location_id=? ORDER BY provider",
        "SELECT * FROM settings WHERE location_id=? ORDER BY key",
        "SELECT * FROM weather WHERE location_id=? ORDER BY date",
        "SELECT * FROM events WHERE location_id=? ORDER BY id",
    ]
    if not training:
        queries += [
            "SELECT * FROM forecast_overrides WHERE location_id=? ORDER BY item_id,date",
            "SELECT * FROM stock_counts WHERE location_id=? ORDER BY ingredient",
            "SELECT COUNT(*),MAX(sent_at) FROM purchase_orders WHERE location_id=?",
            "SELECT COUNT(*),MAX(scored_at) FROM day_accuracy WHERE location_id=?",
            "SELECT * FROM cost_settings WHERE location_id=?",
            "SELECT * FROM category_costs WHERE location_id=? ORDER BY category",
            "SELECT c.* FROM item_composition c JOIN menu_items m ON m.id=c.menu_item_id WHERE m.location_id=? ORDER BY c.menu_item_id",
            "SELECT * FROM recurring_costs WHERE location_id=? ORDER BY id",
        ]
    for sql in queries:
        parts.append([tuple(row) for row in conn.execute(sql, (location_id,)).fetchall()])
    value = hashlib.sha256(json.dumps(parts, separators=(",", ":"), default=str).encode()).hexdigest()
    memo[memo_key] = (revision, value)
    try:
        conn._quantify_data_versions = memo
    except AttributeError:
        pass  # Plain sqlite connections used by integrations need no memo.
    return value


# An item with fewer selling days than this has no number yet. It is listed,
# flagged, and kept out of every total until it has sold for a week.
NEW_ITEM_DAYS = 7


def plural_name(name: str, count: int) -> str:
    """An item's name for a count, keeping the item's own capitalisation."""
    if abs(int(count)) == 1:
        return name
    lowered = name.lower()
    if lowered.endswith("s"):
        return name
    if lowered.endswith(("x", "z", "ch", "sh")):
        return name + "es"
    if lowered.endswith("y") and len(name) > 1 and lowered[-2] not in "aeiou":
        return name[:-1] + "ies"
    return name + "s"


def demand_level(change_percent: float) -> str:
    """One threshold for the level tag and the headline: 4% either side is normal."""
    change = float(change_percent or 0)
    if change >= 15:
        return "Well above normal"
    if change >= 4:
        return "Above normal"
    if change <= -15:
        return "Well below normal"
    if change <= -4:
        return "Below normal"
    return "Normal"


def _trim_cache(cache: dict[Any, Any], maximum: int) -> None:
    while len(cache) > maximum:
        cache.pop(next(iter(cache)))



def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def money_units(value: float) -> str:
    return f"${float(value or 0):,.0f}"


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def safe_mean(values: Iterable[float], fallback: float = 0.0) -> float:
    values = list(values)
    return statistics.mean(values) if values else fallback


def safe_median(values: Iterable[float], fallback: float = 0.0) -> float:
    values = list(values)
    return statistics.median(values) if values else fallback


def weighted_mean(values: list[float], decay: float = 0.90) -> float:
    if not values:
        return 0.0
    weights = [decay**index for index in range(len(values))]
    return sum(value * weight for value, weight in zip(values, weights)) / sum(weights)


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    first = date(year, month, 1)
    delta = (weekday - first.weekday()) % 7
    return first + timedelta(days=delta + 7 * (occurrence - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        cursor = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        cursor = date(year, month + 1, 1) - timedelta(days=1)
    return cursor - timedelta(days=(cursor.weekday() - weekday) % 7)


def _easter(year: int) -> date:
    # Gregorian computus (Anonymous Gregorian algorithm).
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def calendar_occasions(year: int) -> dict[date, tuple[str, str]]:
    thanksgiving = _nth_weekday(year, 11, 3, 4)
    memorial = _last_weekday(year, 5, 0)
    occasions: dict[date, tuple[str, str]] = {
        date(year, 1, 1): ("New Year's Day", "holiday"),
        _nth_weekday(year, 1, 0, 3): ("Martin Luther King Jr. Day", "holiday"),
        _nth_weekday(year, 2, 0, 3): ("Presidents Day", "holiday"),
        date(year, 2, 14): ("Valentine's Day", "occasion"),
        _easter(year): ("Easter Sunday", "occasion"),
        _nth_weekday(year, 5, 6, 2): ("Mother's Day", "occasion"),
        memorial: ("Memorial Day", "holiday"),
        date(year, 6, 19): ("Juneteenth", "holiday"),
        _nth_weekday(year, 6, 6, 3): ("Father's Day", "occasion"),
        date(year, 7, 4): ("Independence Day", "holiday"),
        _nth_weekday(year, 9, 0, 1): ("Labor Day", "holiday"),
        _nth_weekday(year, 10, 0, 2): ("Indigenous Peoples' Day", "holiday"),
        date(year, 10, 31): ("Halloween", "occasion"),
        date(year, 11, 11): ("Veterans Day", "holiday"),
        thanksgiving: ("Thanksgiving", "holiday"),
        thanksgiving + timedelta(days=1): ("Black Friday", "occasion"),
        date(year, 12, 24): ("Christmas Eve", "occasion"),
        date(year, 12, 25): ("Christmas Day", "holiday"),
        date(year, 12, 31): ("New Year's Eve", "occasion"),
    }
    return occasions


def _daylight_hours(latitude: float, target: date) -> float:
    # Compact astronomical approximation; sufficient as a seasonal context feature.
    lat = math.radians(clamp(latitude, -66.0, 66.0))
    declination = math.radians(-23.44 * math.cos(2 * math.pi * (target.timetuple().tm_yday + 10) / 365.25))
    cosine = clamp(-math.tan(lat) * math.tan(declination), -1.0, 1.0)
    return 24.0 * math.acos(cosine) / math.pi


EVENT_CATEGORY_MAP = {
    "sports": "sports", "sport": "sports",
    "concerts": "concerts", "concert": "concerts", "music": "concerts",
    "conferences": "conferences", "conference": "conferences",
    "expos": "conferences", "expo": "conferences", "convention": "conferences",
    "festivals": "festivals", "festival": "festivals",
    "performing-arts": "performing_arts", "performing arts": "performing_arts",
    "community": "community", "school": "community", "academic": "community",
    "public-holidays": "calendar", "observances": "calendar", "holiday": "calendar",
    "severe-weather": "disruptions", "disasters": "disruptions", "terror": "disruptions",
    "politics": "disruptions", "protests": "disruptions", "marathon": "festivals",
}
EVENT_GROUPS = ("sports", "concerts", "conferences", "festivals", "performing_arts", "community", "calendar", "disruptions", "other")


def canonical_event_group(event_type: str) -> str:
    normalized = (event_type or "").strip().lower().replace("_", "-")
    return EVENT_CATEGORY_MAP.get(normalized, "other")


def _time_minutes(value: str) -> int:
    try:
        hour, minute = value[:5].split(":")
        return int(hour) * 60 + int(minute)
    except (TypeError, ValueError):
        return 18 * 60


def event_impact(event: sqlite3.Row | dict[str, Any], open_hour: int, close_hour: int) -> float:
    if event["attendance"] is None or event["distance_miles"] is None:
        return 0.0  # A listing alone does not establish a crowd size or proximity.
    attendance = max(50, int(event["attendance"] or 0))
    distance = max(0.05, float(event["distance_miles"] or 0))
    relevance = clamp(float(event["relevance"] or 0.3), 0.05, 1.0)
    event_start = _time_minutes(event["start_time"])
    event_end = _time_minutes(event["end_time"])
    if event_end <= event_start:
        event_end += 24 * 60
    service_start = open_hour * 60
    service_end = close_hour * 60
    overlap = max(0, min(event_end, service_end) - max(event_start, service_start))
    overlap_factor = 0.45 + 0.55 * min(1.0, overlap / 180)
    # Distance decay is continuous. There is no artificial "two-mile event" rule.
    distance_factor = math.exp(-distance / 5.5)
    size_factor = clamp(math.log10(attendance + 10) / 4.6, 0.18, 1.0)
    return clamp(relevance * distance_factor * size_factor * overlap_factor, 0.0, 1.0)


def _load_location(conn: sqlite3.Connection, location_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM locations WHERE id=? AND active=1", (location_id,)).fetchone()
    if row is None:
        raise ValueError("Unknown location")
    return row


def _weather_map(conn: sqlite3.Connection, location_id: str, start: date, end: date) -> dict[str, dict[str, Any]]:
    from .geography import for_location
    location = conn.execute("SELECT * FROM locations WHERE id=?", (location_id,)).fetchone()
    geo = for_location(location)
    if not geo["key"]:
        return {}
    rows = conn.execute(
        "SELECT * FROM weather WHERE location_id=? AND date>=? AND date<=? AND geography_key=?",
        (location_id, start.isoformat(), end.isoformat(), geo["key"]),
    ).fetchall()
    return {row["date"]: dict(row) for row in rows}


def _events_map(conn: sqlite3.Connection, location_id: str, start: date, end: date) -> dict[str, list[dict[str, Any]]]:
    from .geography import for_location
    location = conn.execute("SELECT * FROM locations WHERE id=?", (location_id,)).fetchone()
    geo = for_location(location)
    if not geo["key"]:
        return {}
    rows = conn.execute(
        "SELECT * FROM events WHERE location_id=? AND date>=? AND date<=? AND geography_key=?",
        (location_id, start.isoformat(), end.isoformat(), geo["key"]),
    ).fetchall()
    output: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        event = dict(row)
        if event.get("attendance_source") not in {"provider_estimate", "owner"}:
            event["attendance"] = None
        if event.get("distance_source") not in {"city_point", "coordinates"}:
            event["distance_miles"] = None
        output[row["date"]].append(event)
    return output


def _seasonal_weather_norm(weather: dict[str, dict[str, Any]], target: date) -> tuple[float, float]:
    values_high: list[float] = []
    values_low: list[float] = []
    doy = target.timetuple().tm_yday
    for key, row in weather.items():
        candidate = date.fromisoformat(key)
        distance = abs(candidate.timetuple().tm_yday - doy)
        distance = min(distance, 365 - distance)
        if distance <= 28 and candidate < target:
            values_high.append(float(row["temp_high"]))
            values_low.append(float(row["temp_low"]))
    return safe_mean(values_high, 68.0), safe_mean(values_low, 52.0)


def build_context(
    location: sqlite3.Row | dict[str, Any],
    target: date,
    weather_row: dict[str, Any] | None,
    events: list[dict[str, Any]],
    weather_history: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    from .geography import for_location
    geo = for_location(location)
    if geo["status"] == "unverified":
        weather_row, events, weather_history = None, [], {}
    weather_available = bool(weather_row)
    weather_row = weather_row or {
        "temp_high": 68.0, "temp_low": 52.0, "precipitation_mm": 0.0,
        "snowfall_cm": 0.0, "uv_index": 0.0,
        "condition": "Seasonal estimate", "source": "fallback",
    }
    occasions = calendar_occasions(target.year)
    occasion = occasions.get(target)
    tomorrow_occasion = occasions.get(target + timedelta(days=1))
    yesterday_occasion = occasions.get(target - timedelta(days=1))
    month_end = int((target + timedelta(days=1)).month != target.month)
    last_day = (date(target.year + (target.month == 12), 1 if target.month == 12 else target.month + 1, 1) - timedelta(days=1)).day
    payday = int(target.day in {1, 15, last_day} or (target.weekday() == 4 and target.day in {13, 14, 28, 29, 30}))
    long_weekend = int(bool(occasion and occasion[1] == "holiday") or bool(tomorrow_occasion and target.weekday() in {4, 5}) or bool(yesterday_occasion and target.weekday() in {0, 1}))

    normal_high, normal_low = _seasonal_weather_norm(weather_history or {}, target)
    temp_high = float(weather_row["temp_high"]) if weather_row.get("temp_high") is not None else normal_high
    temp_low = float(weather_row["temp_low"]) if weather_row.get("temp_low") is not None else normal_low
    precipitation = max(0.0, float(weather_row.get("precipitation_mm") or 0.0))
    condition = str(weather_row.get("condition") or "Unknown")
    snowfall_cm = max(0.0, float(weather_row.get("snowfall_cm") or 0.0))
    uv_index = max(0.0, float(weather_row.get("uv_index") or 0.0))
    snow = int(snowfall_cm > 0 or "snow" in condition.lower() or "sleet" in condition.lower())

    event_groups = {group: 0.0 for group in EVENT_GROUPS}
    enriched_events: list[dict[str, Any]] = []
    for raw in events:
        impact = event_impact(raw, int(location["open_hour"]), int(location["close_hour"]))
        group = canonical_event_group(raw.get("event_type") or "")
        event_groups[group] += impact
        enriched_events.append(raw | {"impact": round(impact, 4), "group": group})
    for group in event_groups:
        event_groups[group] = clamp(event_groups[group], 0.0, 2.5)
    enriched_events.sort(key=lambda row: row["impact"], reverse=True)

    day_of_year = target.timetuple().tm_yday
    context = {
        "date": target.isoformat(),
        "weekday": target.strftime("%A"),
        "weekend": int(target.weekday() >= 5),
        "annual_sin": math.sin(2 * math.pi * day_of_year / 365.25),
        "annual_cos": math.cos(2 * math.pi * day_of_year / 365.25),
        "holiday": int(bool(occasion and occasion[1] == "holiday")),
        "occasion": int(bool(occasion)),
        "occasion_name": occasion[0] if occasion else None,
        "holiday_eve": int(bool(tomorrow_occasion and tomorrow_occasion[1] == "holiday")),
        "holiday_after": int(bool(yesterday_occasion and yesterday_occasion[1] == "holiday")),
        "long_weekend": long_weekend,
        "payday": payday,
        "month_end": month_end,
        "temp_high": temp_high,
        "temp_low": temp_low,
        "temp_anomaly": temp_high - normal_high,
        "cold_anomaly": normal_low - temp_low,
        "precipitation_mm": precipitation,
        "precipitation_scaled": math.log1p(precipitation) / math.log(21.0),
        "snow": snow,
        "snowfall_cm": snowfall_cm,
        "snowfall_scaled": math.log1p(snowfall_cm) / math.log(16.0),
        "uv_index": uv_index,
        "uv_scaled": clamp(uv_index / 10.0, 0.0, 1.5),
        "weather_condition": condition,
        "weather_source": weather_row.get("source", "unknown"),
        "weather_available": weather_available,
        "geography": geo,
        "daylight_hours": _daylight_hours(geo["latitude"], target) if geo["latitude"] is not None else 12.0,
        "event_groups": event_groups,
        "event_total": clamp(sum(event_groups.values()), 0.0, 5.0),
        "events": enriched_events,
    }
    return context


FEATURE_NAMES = [
    "intercept", "dow_mon", "dow_tue", "dow_wed", "dow_thu", "dow_fri", "dow_sat",
    "annual_sin", "annual_cos", "time_index", "holiday", "occasion", "holiday_eve",
    "holiday_after", "long_weekend", "payday", "month_end", "temp_anomaly",
    "cold_anomaly", "precipitation", "snow", "snowfall", "uv_index", "daylight",
    "event_sports", "event_concerts", "event_conferences", "event_festivals",
    "event_performing_arts", "event_community", "event_calendar", "event_disruptions",
    "event_other", "event_total",
]

FEATURE_GROUPS = {
    "calendar": set(range(1, 17)) | {23},
    "weather": {17, 18, 19, 20, 21, 22},
    "events": set(range(24, 34)),
    "trend": {9},
}


def feature_vector(context: dict[str, Any], target: date, first_date: date) -> list[float]:
    dow = target.weekday()
    groups = context["event_groups"]
    return [
        1.0,
        *[1.0 if dow == index else 0.0 for index in range(6)],
        float(context["annual_sin"]), float(context["annual_cos"]),
        (target - first_date).days / 365.25,
        float(context["holiday"]), float(context["occasion"]), float(context["holiday_eve"]),
        float(context["holiday_after"]), float(context["long_weekend"]), float(context["payday"]),
        float(context["month_end"]), clamp(float(context["temp_anomaly"]) / 20.0, -2.0, 2.0),
        clamp(float(context["cold_anomaly"]) / 20.0, -2.0, 2.0),
        clamp(float(context["precipitation_scaled"]), 0.0, 2.0), float(context["snow"]),
        clamp(float(context["snowfall_scaled"]), 0.0, 2.0), clamp(float(context["uv_scaled"]), 0.0, 1.5),
        clamp((float(context["daylight_hours"]) - 12.0) / 4.0, -1.5, 1.5),
        *[float(groups[group]) for group in EVENT_GROUPS],
        float(context["event_total"]),
    ]


def _solve_linear(matrix: list[list[float]], vector: list[float]) -> list[float]:
    n = len(vector)
    augmented = [row[:] + [vector[index]] for index, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda row: abs(augmented[row][col]))
        if abs(augmented[pivot][col]) < 1e-10:
            augmented[pivot][col] = 1e-8
        augmented[col], augmented[pivot] = augmented[pivot], augmented[col]
        divisor = augmented[col][col]
        augmented[col] = [value / divisor for value in augmented[col]]
        for row in range(n):
            if row == col:
                continue
            factor = augmented[row][col]
            if abs(factor) < 1e-12:
                continue
            augmented[row] = [left - factor * right for left, right in zip(augmented[row], augmented[col])]
    return [augmented[row][-1] for row in range(n)]


def fit_ridge(x_rows: list[list[float]], y: list[float], weights: list[float] | None = None, alpha: float = 5.0) -> list[float]:
    if not x_rows:
        return [0.0] * len(FEATURE_NAMES)
    p = len(x_rows[0])
    matrix = [[0.0] * p for _ in range(p)]
    vector = [0.0] * p
    weights = weights or [1.0] * len(x_rows)
    for x, target, weight in zip(x_rows, y, weights):
        for i in range(p):
            vector[i] += weight * x[i] * target
            for j in range(i, p):
                matrix[i][j] += weight * x[i] * x[j]
    for i in range(p):
        for j in range(i):
            matrix[i][j] = matrix[j][i]
        if i != 0:
            matrix[i][i] += alpha
    matrix[0][0] += 1e-6
    return _solve_linear(matrix, vector)


def dot(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


@dataclass
class HistoryRow:
    target_date: date
    quantity: float
    revenue: float
    stockout_minutes: int
    context: dict[str, Any]
    x: list[float]


def _history_for_item(
    conn: sqlite3.Connection,
    location: sqlite3.Row,
    item_id: str,
    target_date: date,
    lookback_days: int = 1095,
) -> tuple[list[HistoryRow], dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    start = target_date - timedelta(days=lookback_days)
    sales_rows = conn.execute(
        """SELECT date,quantity,revenue,stockout_minutes FROM sales
           WHERE location_id=? AND item_id=? AND date>=? AND date<? ORDER BY date""",
        (location["id"], item_id, start.isoformat(), target_date.isoformat()),
    ).fetchall()
    if not sales_rows:
        return [], {}, {}
    first = date.fromisoformat(sales_rows[0]["date"])
    end = target_date + timedelta(days=31)
    context_key = (
        _db_identity(conn), location["id"], start.isoformat(), end.isoformat(),
        _data_version(conn, location["id"], training=True),
    )
    with _CACHE_LOCK:
        cached = _CONTEXT_CACHE.get(context_key)
    if cached is None:
        weather = _weather_map(conn, location["id"], start, end)
        events = _events_map(conn, location["id"], start, end)
        context_by_date: dict[str, dict[str, Any]] = {}
        cursor = start
        while cursor <= end:
            key = cursor.isoformat()
            context_by_date[key] = build_context(location, cursor, weather.get(key), events.get(key, []), weather)
            cursor += timedelta(days=1)
        cached = (weather, events, context_by_date)
        with _CACHE_LOCK:
            _CONTEXT_CACHE[context_key] = cached
            _trim_cache(_CONTEXT_CACHE, 12)
    weather, events, context_by_date = cached
    output: list[HistoryRow] = []
    for row in sales_rows:
        day = date.fromisoformat(row["date"])
        context = context_by_date[row["date"]]
        output.append(HistoryRow(
            target_date=day,
            quantity=float(row["quantity"]),
            revenue=float(row["revenue"]),
            stockout_minutes=int(row["stockout_minutes"] or 0),
            context=context,
            x=feature_vector(context, day, first),
        ))
    return output, weather, events

def _baseline_prediction(history: list[HistoryRow], target: date) -> float:
    eligible = [row for row in history if row.target_date < target]
    same = [row.quantity for row in reversed(eligible) if row.target_date.weekday() == target.weekday()][:16]
    recent = [row.quantity for row in eligible if row.target_date >= target - timedelta(days=28)]
    prior = [row.quantity for row in eligible if target - timedelta(days=56) <= row.target_date < target - timedelta(days=28)]
    base = weighted_mean(same, 0.91) if same else safe_mean(recent, safe_mean([row.quantity for row in eligible], 1.0))
    recent_mean = safe_mean(recent, base)
    prior_mean = safe_mean(prior, recent_mean)
    momentum = clamp(recent_mean / prior_mean if prior_mean > 0 else 1.0, 0.78, 1.28)
    month_same = [row.quantity for row in eligible if row.target_date.month == target.month and row.target_date.weekday() == target.weekday()][-12:]
    season = clamp(safe_mean(month_same, base) / max(1.0, base), 0.84, 1.18)
    return max(0.0, base * (momentum**0.45) * (season**0.35))


def _context_distance(left: dict[str, Any], right: dict[str, Any]) -> float:
    distance = 0.0
    distance += abs(float(left["temp_anomaly"]) - float(right["temp_anomaly"])) / 16.0
    distance += abs(float(left["precipitation_scaled"]) - float(right["precipitation_scaled"])) * 1.5
    distance += abs(float(left.get("snowfall_scaled", 0)) - float(right.get("snowfall_scaled", 0))) * 1.2
    distance += abs(float(left.get("uv_scaled", 0)) - float(right.get("uv_scaled", 0))) * 0.5
    distance += abs(float(left["event_total"]) - float(right["event_total"])) * 0.9
    distance += abs(float(left["annual_sin"]) - float(right["annual_sin"])) * 0.45
    distance += abs(float(left["annual_cos"]) - float(right["annual_cos"])) * 0.45
    for key in ("holiday", "occasion", "long_weekend", "payday", "snow"):
        distance += abs(float(left[key]) - float(right[key])) * 0.7
    for group in EVENT_GROUPS:
        distance += abs(float(left["event_groups"][group]) - float(right["event_groups"][group])) * 0.28
    return distance


def _analog_prediction(history: list[HistoryRow], target: date, context: dict[str, Any], k: int = 8) -> tuple[float, list[HistoryRow]]:
    candidates = [row for row in history if row.target_date < target and row.target_date.weekday() == target.weekday()]
    if not candidates:
        return _baseline_prediction(history, target), []
    scored = sorted(candidates, key=lambda row: _context_distance(row.context, context))[:k]
    weights = [1.0 / (0.35 + _context_distance(row.context, context)) for row in scored]
    expected = sum(row.quantity * weight for row, weight in zip(scored, weights)) / max(1e-9, sum(weights))
    return max(0.0, expected), scored


def _wape(actual: list[float], predicted: list[float]) -> float:
    denominator = sum(abs(value) for value in actual)
    return sum(abs(a - p) for a, p in zip(actual, predicted)) / denominator if denominator else 1.0


def _model_calibration(history: list[HistoryRow]) -> dict[str, Any]:
    if len(history) < 42:
        return {"weights": {"baseline": 0.62, "ridge": 0.18, "analogs": 0.20}, "wape": 0.34, "errors": {}}
    holdout_size = min(35, max(21, len(history) // 10))
    train = history[:-holdout_size]
    holdout = history[-holdout_size:]
    if len(train) < 28:
        return {"weights": {"baseline": 0.62, "ridge": 0.18, "analogs": 0.20}, "wape": 0.34, "errors": {}}
    x_train = [row.x for row in train]
    y_train = [math.log1p(max(0.0, row.quantity)) for row in train]
    recency = [0.994 ** (len(train) - index - 1) * (0.72 if row.stockout_minutes > 0 else 1.0) for index, row in enumerate(train)]
    coefficients = fit_ridge(x_train, y_train, recency, alpha=6.5)

    actual: list[float] = []
    baseline_pred: list[float] = []
    ridge_pred: list[float] = []
    analog_pred: list[float] = []
    available = train[:]
    for row in holdout:
        actual.append(row.quantity)
        baseline_pred.append(_baseline_prediction(available, row.target_date))
        ridge_pred.append(max(0.0, math.expm1(dot(row.x, coefficients))))
        analog_value, _ = _analog_prediction(available, row.target_date, row.context)
        analog_pred.append(analog_value)
        available.append(row)
    errors = {
        "baseline": _wape(actual, baseline_pred),
        "ridge": _wape(actual, ridge_pred),
        "analogs": _wape(actual, analog_pred),
    }
    inverse = {name: 1.0 / max(0.08, error) ** 2 for name, error in errors.items()}
    total = sum(inverse.values())
    weights = {name: value / total for name, value in inverse.items()}
    ensemble = [
        baseline_pred[index] * weights["baseline"] + ridge_pred[index] * weights["ridge"] + analog_pred[index] * weights["analogs"]
        for index in range(len(actual))
    ]
    return {"weights": weights, "wape": _wape(actual, ensemble), "errors": errors}


def _cold_start_estimate(conn: sqlite3.Connection, item: sqlite3.Row, target: date) -> float:
    interpretation = conn.execute("SELECT item_family FROM menu_interpretations WHERE menu_item_id=?", (item["id"],)).fetchone()
    family = interpretation["item_family"] if interpretation else None
    query = """SELECT AVG(s.quantity) AS avg_qty FROM sales s
               JOIN menu_items m ON m.id=s.item_id
               LEFT JOIN menu_interpretations i ON i.menu_item_id=m.id
               WHERE s.location_id=? AND s.date>=? AND s.date<? AND s.item_id<>?"""
    params: list[Any] = [item["location_id"], (target - timedelta(days=42)).isoformat(), target.isoformat(), item["id"]]
    if family:
        query += " AND i.item_family=?"
        params.append(family)
    row = conn.execute(query, tuple(params)).fetchone()
    return max(1.0, float(row["avg_qty"] or item["base_daily_qty"] or 1.0))


def _normal_context_vector(history: list[HistoryRow], target: date) -> list[float]:
    peers = [row.x for row in history if row.target_date.weekday() == target.weekday()]
    if not peers:
        peers = [row.x for row in history]
    if not peers:
        return [0.0] * len(FEATURE_NAMES)
    return [safe_mean(row[index] for row in peers) for index in range(len(FEATURE_NAMES))]


DRIVER_LABELS = {
    "calendar": "Day of the week",
    "weather": "Weather",
    "events": "What is on nearby",
    "trend": "Recent trend",
}


def _driver_effects(
    coefficients: list[float],
    target_x: list[float],
    normal_x: list[float],
    context: dict[str, Any],
    trend_factor: float,
    evidence: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    evidence = evidence or {}
    effects: list[dict[str, Any]] = []
    for group, indices in FEATURE_GROUPS.items():
        if group == "trend":
            percent = round((trend_factor - 1.0) * 100)
        else:
            log_delta = sum(coefficients[index] * (target_x[index] - normal_x[index]) for index in indices)
            percent = round((math.exp(clamp(log_delta, -0.35, 0.35)) - 1.0) * 100)
        if abs(percent) < 2:
            continue

        detail = ""
        based_on = ""
        label = DRIVER_LABELS[group]
        if group == "calendar":
            direction = "above" if percent > 0 else "below"
            if context.get("occasion_name"):
                label = str(context["occasion_name"])
                detail = f"{context['occasion_name']} falls on this date, and it runs {direction} a normal {context['weekday']} here."
            elif context.get("long_weekend"):
                label = "Long weekend"
                detail = f"This date sits inside a long weekend, which runs {direction} normal here."
            elif context.get("payday"):
                label = "Pay day"
                detail = f"Pay dates land around now, and they run {direction} normal here."
            else:
                detail = f"{context['weekday']}s usually run {direction} the rest of the week here."
            based_on = f"{evidence.get('weekday_samples', 0)} past {context['weekday']}s here"
        elif group == "weather":
            if not context.get("weather_available"):
                continue
            bits = [f"{context['weather_condition'].lower()}", f"high {round(context['temp_high'])}°"]
            if context.get("precipitation_mm", 0) >= 0.5:
                bits.append(f"{context['precipitation_mm'] / 25.4:.1f} in of rain")
            if context.get("snowfall_cm", 0) > 0:
                bits.append(f"{context['snowfall_cm'] / 2.54:.1f} in of snow")
            anomaly = float(context.get("temp_anomaly") or 0)
            comparison = (
                f"about {abs(round(anomaly))}° {'warmer' if anomaly > 0 else 'cooler'} than usual for the date"
                if abs(anomaly) >= 3 else "close to the usual temperature for the date"
            )
            detail = f"Forecast is {', '.join(bits)}, {comparison}."
            based_on = f"{evidence.get('weather_samples', 0)} past days here with similar weather"
        elif group == "events":
            relevant = [event for event in context["events"] if event["impact"] >= 0.08]
            if relevant:
                top = relevant[0]
                distance_basis = "the city reference point" if top.get("distance_source") == "city_point" else "this location"
                crowd_source = "The provider estimates" if top.get("attendance_source") == "provider_estimate" else "Your saved estimate is"
                detail = (f"{top['name']} is listed about {float(top['distance_miles']):.1f} miles from {distance_basis}. "
                          f"{crowd_source} {int(top['attendance']):,} attendees for the event; that is not a count of visitors to your business.")
                based_on = f"{len(relevant)} of {len(context['events'])} nearby listings big enough to matter"
            else:
                detail = "Several smaller things are on nearby. None of them dominates, but together they add up."
                based_on = f"{len(context['events'])} nearby listings reviewed"
        else:
            recent = evidence.get("recent_average")
            prior = evidence.get("prior_average")
            if recent is not None and prior is not None:
                detail = (
                    f"The last four weeks averaged {recent:,.0f} a day against {prior:,.0f} "
                    "in the four weeks before."
                )
            else:
                detail = "The last four weeks sit at a different level from the four before them."
            based_on = "the last eight weeks of sales"
        effects.append({
            "key": group,
            "label": label,
            "effect": percent,
            "detail": detail,
            "based_on": based_on,
        })
    effects.sort(key=lambda row: abs(row["effect"]), reverse=True)
    return effects


def _fit_model_bundle(history: list[HistoryRow]) -> tuple[list[float], dict[str, Any]]:
    if len(history) < 28:
        return [0.0] * len(FEATURE_NAMES), {
            "weights": {"baseline": 0.70, "ridge": 0.10, "analogs": 0.20},
            "wape": 0.42,
            "errors": {},
        }
    y = [math.log1p(max(0.0, row.quantity)) for row in history]
    weights = [0.995 ** (len(history) - index - 1) * (0.72 if row.stockout_minutes else 1.0) for index, row in enumerate(history)]
    coefficients = fit_ridge([row.x for row in history], y, weights, alpha=6.5)
    return coefficients, _model_calibration(history)


def _future_model_bundle(
    conn: sqlite3.Connection,
    location: sqlite3.Row,
    item: sqlite3.Row,
    target_date: date,
) -> dict[str, Any] | None:
    sale_meta = conn.execute(
        "SELECT MAX(date) AS latest,COUNT(*) AS n FROM sales WHERE location_id=? AND item_id=?",
        (location["id"], item["id"]),
    ).fetchone()
    if not sale_meta or not sale_meta["latest"]:
        return None
    latest = date.fromisoformat(sale_meta["latest"])
    if target_date <= latest:
        return None
    cutoff = latest + timedelta(days=1)
    key = (
        _db_identity(conn), item["id"], sale_meta["latest"], int(sale_meta["n"] or 0),
        _data_version(conn, location["id"], training=True),
    )
    with _CACHE_LOCK:
        bundle = _MODEL_CACHE.get(key)
    if bundle is None:
        history, weather_map, events_map = _history_for_item(conn, location, item["id"], cutoff)
        coefficients, calibration = _fit_model_bundle(history)
        bundle = {
            "history": history,
            "weather": weather_map,
            "events": events_map,
            "coefficients": coefficients,
            "calibration": calibration,
            "cutoff": cutoff,
        }
        with _CACHE_LOCK:
            _MODEL_CACHE[key] = bundle
            _trim_cache(_MODEL_CACHE, 96)
    return bundle


def cost_share_for(conn: sqlite3.Connection, location_id: str, item_id: str) -> float:
    """The owner's food cost share for one item, or the best available fallback.

    Deferred import because costs.py reads from this module. Callers working
    through a whole menu should resolve the basis once and pass it down rather
    than calling this per item.
    """
    try:
        from .costs import DEFAULT_COST_SHARE, cost_settings, item_cost_basis

        settings = cost_settings(conn, location_id)
        entry = item_cost_basis(conn, location_id, settings).get(item_id)
        return float(entry["share"]) if entry else float(settings.get("default_cost_share") or DEFAULT_COST_SHARE)
    except Exception:  # a costing failure must never stop a forecast
        return 0.30


def forecast_item(
    conn: sqlite3.Connection, item: sqlite3.Row, target_date: date, cost_share: float | None = None
) -> dict[str, Any]:
    location = _load_location(conn, item["location_id"])
    ensure_location_interpretations(conn, item["location_id"])
    cached = _future_model_bundle(conn, location, item, target_date)
    if cached:
        history = cached["history"]
        weather_map = cached["weather"]
        events_map = cached["events"]
        coefficients = cached["coefficients"]
        calibration = cached["calibration"]
    else:
        history, weather_map, events_map = _history_for_item(conn, location, item["id"], target_date)
        coefficients, calibration = _fit_model_bundle(history)
    if history:
        first_date = history[0].target_date
    else:
        first_date = target_date - timedelta(days=365)
    target_context = build_context(location, target_date, weather_map.get(target_date.isoformat()), events_map.get(target_date.isoformat(), []), weather_map)
    target_x = feature_vector(target_context, target_date, first_date)

    baseline = _baseline_prediction(history, target_date) if history else _cold_start_estimate(conn, item, target_date)
    analog_expected, analog_days = _analog_prediction(history, target_date, target_context) if history else (baseline, [])
    ridge_expected = max(0.0, math.expm1(dot(target_x, coefficients))) if len(history) >= 28 else baseline

    model_expected = (
        baseline * calibration["weights"]["baseline"]
        + ridge_expected * calibration["weights"]["ridge"]
        + analog_expected * calibration["weights"]["analogs"]
    )
    model_expected = max(0.0, model_expected)

    # An adjustment is the number to make, never the forecast. What is expected
    # to sell stays the same whoever typed what, so the advice, the totals and
    # the score all keep reading the same number.
    override_row = conn.execute(
        "SELECT * FROM forecast_overrides WHERE location_id=? AND item_id=? AND date=?",
        (item["location_id"], item["id"], target_date.isoformat()),
    ).fetchone()
    override = None
    if override_row is not None:
        override = {
            "quantity": int(override_row["quantity"]),
            "reason": override_row["reason"] or "",
            "updated_at": override_row["updated_at"],
            "updated_by": (override_row["updated_by"] if "updated_by" in override_row.keys() else "") or "",
        }
    expected = model_expected
    new_item = len(history) < NEW_ITEM_DAYS
    if new_item:
        # Fewer than a week of sales: no number yet, and nothing built on one.
        expected = 0.0
        baseline = 0.0

    recent = [row.quantity for row in history if row.target_date >= target_date - timedelta(days=28)]
    prior = [row.quantity for row in history if target_date - timedelta(days=56) <= row.target_date < target_date - timedelta(days=28)]
    trend_factor = clamp(safe_mean(recent, baseline) / max(1.0, safe_mean(prior, safe_mean(recent, baseline))), 0.75, 1.35)
    normal_x = _normal_context_vector(history, target_date)
    weekday_rows = [row for row in history if row.target_date.weekday() == target_date.weekday()]
    weather_rows = [
        row for row in history
        if abs(float(row.context["temp_anomaly"]) - float(target_context["temp_anomaly"])) <= 6
        and abs(float(row.context["precipitation_scaled"]) - float(target_context["precipitation_scaled"])) <= 0.25
    ]
    evidence = {
        "weekday_samples": len(weekday_rows),
        "weather_samples": len(weather_rows),
        "recent_average": safe_mean(recent) if recent else None,
        "prior_average": safe_mean(prior) if prior else None,
    }
    drivers = [] if new_item else _driver_effects(coefficients, target_x, normal_x, target_context, trend_factor, evidence)

    data_coverage = clamp(len(history) / 365.0, 0.0, 1.0)
    confidence = 0 if new_item else int(round(clamp(100 - calibration["wape"] * 88 + data_coverage * 8, 48, 95)))
    production = _production_quantity(
        history if not new_item else [], target_date, target_context, int(round(expected)), int(round(baseline)),
        float(item["price"]),
        cost_share if cost_share is not None else cost_share_for(conn, item["location_id"], item["id"]),
        override["quantity"] if override else None,
    )

    interpretation = conn.execute(
        "SELECT * FROM menu_interpretations WHERE menu_item_id=?", (item["id"],)
    ).fetchone()
    analog_summary = [
        {"date": row.target_date.isoformat(), "quantity": int(round(row.quantity)), "weather": row.context["weather_condition"], "event_total": round(row.context["event_total"], 2)}
        for row in analog_days[:3]
    ]
    return {
        "item_id": item["id"],
        "name": interpretation["normalized_name"] if interpretation else item["name"],
        "raw_name": item["name"],
        "category": item["category"],
        "family": interpretation["item_family"] if interpretation else "menu-item",
        "production_unit": interpretation["production_unit"] if interpretation else "unit",
        "interpretation_confidence": float(interpretation["confidence"]) if interpretation else 0.0,
        "price": float(item["price"]),
        "baseline": int(round(baseline)),
        "model_expected": int(round(expected)),
        "expected": int(round(expected)),
        "new_item": new_item,
        "selling_days": len(history),
        "lower": production.pop("lower"),
        "upper": production.pop("upper"),
        "confidence": confidence,
        "vs_baseline_percent": 0 if new_item else round((expected / max(1.0, baseline) - 1.0) * 100),
        "vs_baseline_units": int(round(expected)) - int(round(baseline)),
        "evidence": evidence,
        "drivers": drivers,
        "override": override,
        "model": {
            "version": MODEL_VERSION,
            "history_days": (target_date - history[0].target_date).days if history else 0,
            "history_points": len(history),
            "validation_wape": round(calibration["wape"], 4),
            "blend": {key: round(value, 3) for key, value in calibration["weights"].items()},
            "component_predictions": {
                "baseline": round(baseline, 1), "ridge": round(ridge_expected, 1), "analogs": round(analog_expected, 1),
            },
        },
        "analog_days": analog_summary,
        "context": target_context,
        **production,
    }


def _production_quantity(
    history: list[HistoryRow], target_date: date, context: dict[str, Any], expected: int,
    baseline: int, price: float, cost_share: float | None = None, override_quantity: int | None = None,
) -> dict[str, Any]:
    """How many to actually make, which is not the same as how many will sell.

    Imported inside the function because the analysis module reads from this
    one. The cost is a few milliseconds an item.

    `cost_share` is the owner's own food cost figure for this item, resolved by
    the caller. It decides how far above the forecast the make number sits, so
    it has to be their number and not a constant.

    Returns the make number, the make number a normal day would get (so the
    advice can say "12 more than a normal Tuesday"), the number that would have
    been suggested when an adjustment is in place, the chance of running out at
    the make number, and the range of a normal such day (`lower`, `upper`), the
    same range the item sheet shows.
    """
    make = override_quantity if override_quantity is not None else expected
    empty = {
        "make": make, "suggested_make": expected, "normal_make": baseline, "make_reason": "",
        "sell_out_percent": None, "lower": expected, "upper": expected,
    }
    if not history:
        return empty
    try:
        from .item_analysis import comparable_days, prep_advice
        from .statistics import exceedance

        distribution = comparable_days(history, target_date, context, expected)
        prep = prep_advice(distribution, price, cost_share)
    except Exception:  # the forecast must survive a failure in the extra detail
        return empty
    if not prep.get("quantity"):
        return empty
    values = distribution.get("today_values") or []
    suggested = max(int(prep["quantity"]), expected)
    normal_make = baseline
    if values and expected > 0 and baseline > 0:
        scale = baseline / expected
        try:
            normal_prep = prep_advice({"today_values": [value * scale for value in values]}, price, cost_share)
            normal_make = max(int(normal_prep.get("quantity") or baseline), baseline)
        except Exception:  # noqa: BLE001 - the normal make is advice, never a blocker
            normal_make = baseline
    make = override_quantity if override_quantity is not None else suggested
    if override_quantity is not None and values:
        sell_out = round(exceedance(values, float(override_quantity)) * 100)
    else:
        sell_out = prep.get("sell_out_percent")
    if override_quantity is not None:
        reason = ""
    elif suggested > expected:
        reason = f"{suggested - expected} more than the {expected} expected to sell, because running out costs more than a leftover"
    else:
        reason = "the same as expected to sell"
    return {
        "make": make,
        "suggested_make": suggested,
        "normal_make": normal_make,
        "make_reason": reason,
        "sell_out_percent": sell_out,
        "lower": int(distribution.get("today_low", expected)),
        "upper": int(distribution.get("today_high", expected)),
    }


def _learned_hour_curve(conn: sqlite3.Connection, location_id: str, target_date: date, settings: dict[str, str]) -> dict[int, float]:
    rows = conn.execute(
        """SELECT hour,SUM(quantity) AS qty FROM sales_hourly
           WHERE location_id=? AND date>=? AND date<?
           AND CAST(strftime('%w', date) AS INTEGER)=?
           GROUP BY hour""",
        (location_id, (target_date - timedelta(days=112)).isoformat(), target_date.isoformat(), (target_date.weekday() + 1) % 7),
    ).fetchall()
    learned = {int(row["hour"]): float(row["qty"]) for row in rows if float(row["qty"] or 0) > 0}
    try:
        fallback_raw = json.loads(settings.get("hour_curve", "{}"))
        fallback = {int(hour): float(weight) for hour, weight in fallback_raw.items()}
    except (ValueError, TypeError):
        fallback = {}
    if not learned:
        return fallback
    learned_total = sum(learned.values()) or 1.0
    fallback_total = sum(fallback.values()) or 1.0
    hours = sorted(set(learned) | set(fallback))
    return {
        hour: 0.78 * learned.get(hour, 0.0) / learned_total + 0.22 * fallback.get(hour, 0.0) / fallback_total
        for hour in hours
    }


def _settings(conn: sqlite3.Connection, location_id: str) -> dict[str, str]:
    return {row["key"]: row["value"] for row in conn.execute("SELECT key,value FROM settings WHERE location_id=?", (location_id,)).fetchall()}


CURVE_WINDOW_DAYS = 112
# Same-weekday services the shape-noise constant in intraday.py is scaled
# against. A location live for three weeks has three of them, not sixteen, and
# its curve has to be treated as the guess it is.
CURVE_REFERENCE_DAYS = 16
# Units of an item's own hourly history before its own clock is trusted at half
# weight against the location's.
ITEM_CURVE_PSEUDO_UNITS = 40.0


def service_slots(location):
    """Every hour this location is open, as day_offset * 24 + clock hour.

    A close past midnight is stored past 24, so a bar open 11 to 2 has slots 11
    through 25. Slot 25 is one in the morning on the day after the trading date.

    This exists alongside `service_hours`, which returns plain clock hours and
    is what the interface uses. The two must not be merged. `sales_hourly` is
    keyed on the calendar date and the clock hour, so one in the morning on a
    Friday night is stored under Saturday. Only a slot can say which service a
    row belongs to, and anything reading hourly rows for a location that trades
    past midnight has to go through one.
    """
    opens = int(location["open_hour"])
    closes = int(location["close_hour"])
    if closes <= opens:
        closes += 24
    closes = min(closes, opens + 24)
    return list(range(opens, closes))


def slot_parts(slot):
    """(days after the trading date, clock hour) for a slot."""
    return divmod(int(slot), 24)


def hour_label(slot):
    offset, clock = slot_parts(slot)
    shown = 12 if clock % 12 == 0 else clock % 12
    return f"{shown} {'AM' if clock < 12 else 'PM'}" + (" next day" if offset else "")


def opening_calls(conn, location_id, start, end):
    """What was said before service on each of these days, keyed (date, item_id).

    A bare read, kept here instead of in intraday so both the day scorer and
    the results backtest can use it without either importing the other.
    """
    return {
        (row["date"], row["item_id"]): dict(row)
        for row in conn.execute(
            "SELECT * FROM forecast_calls WHERE location_id=? AND date>=? AND date<=?",
            (location_id, start.isoformat(), end.isoformat()),
        ).fetchall()
    }


def location_hour_curve(conn, location, target_date, settings):
    """Share of the day by slot, on exactly the hours this location is open.

    Returns {"shares": {slot: share}, "observed_days": n, "learned": bool}.

    Three things this does that a plain read of `sales_hourly` cannot.

    It attributes an hour to the service it belongs to. A one in the morning row
    is stored under the next calendar date, so grouping by the stored date puts
    Friday night's last hour into Saturday's pattern, and into a day that was
    shut at that hour. The slot mapping puts it back.

    It emits every open hour, including ones that have never rung. A missing
    hour reads as missing data; a quiet hour reads as a quiet hour, and they are
    not the same thing.

    It reports how many same-weekday services it is built from, and whether it
    was learned at all. A curve invented from nothing is a flat one, and
    treating a flat curve as a measurement is how a perfectly normal morning
    gets reported as running ahead.
    """
    slots = service_slots(location)
    if not slots:
        return {"shares": {}, "observed_days": 0, "learned": False}

    first = (target_date - timedelta(days=CURVE_WINDOW_DAYS)).isoformat()
    rows = conn.execute(
        """SELECT date, hour, SUM(quantity) AS qty FROM sales_hourly
           WHERE location_id=? AND date>=? AND date<=?
           GROUP BY date, hour""",
        (location["id"], first, target_date.isoformat()),
    ).fetchall()

    weight = {slot: 0.0 for slot in slots}
    seen = set()
    for row in rows:
        stored = date.fromisoformat(row["date"])
        clock = int(row["hour"])
        for offset in (0, 1):
            trading = stored - timedelta(days=offset)
            slot = offset * 24 + clock
            if slot not in weight or trading >= target_date or trading.isoformat() < first:
                continue
            if trading.weekday() != target_date.weekday():
                continue
            weight[slot] += max(0.0, float(row["qty"] or 0))
            seen.add(trading.isoformat())

    total = sum(weight.values())
    if total <= 0:
        # Nothing learned. A flat curve is the only honest placeholder, and it
        # is labelled as one so nothing downstream treats it as evidence.
        return {
            "shares": {slot: 1.0 / len(slots) for slot in slots},
            "observed_days": 0,
            "learned": False,
        }

    try:
        raw = json.loads(settings.get("hour_curve", "{}"))
        fallback = {int(hour): max(0.0, float(value)) for hour, value in raw.items()}
    except (ValueError, TypeError):
        fallback = {}
    fallback_total = sum(fallback.values())
    if fallback_total > 0:
        blended = {
            slot: 0.78 * weight[slot] / total + 0.22 * fallback.get(slot % 24, 0.0) / fallback_total
            for slot in slots
        }
    else:
        blended = {slot: weight[slot] / total for slot in slots}
    scale = sum(blended.values()) or 1.0
    return {
        "shares": {slot: value / scale for slot, value in blended.items()},
        "observed_days": len(seen),
        "learned": True,
    }


def item_hour_curves(conn, location, target_date, base):
    """One share-of-day curve per item, pulled toward the location's when thin.

    A croissant and a lunch sandwich do not follow the same clock. Judged
    against the location's average, the croissant reads far ahead at ten in the
    morning and the sandwich far behind, when both are exactly on their own
    pattern and the day is normal. Each item therefore gets its own curve,
    blended toward the location's in proportion to how much of its own hourly
    history there is to go on.

    One grouped query covers the whole menu, so the cost does not grow with the
    number of items.
    """
    slots = service_slots(location)
    if not slots or not base:
        return {}
    first = (target_date - timedelta(days=CURVE_WINDOW_DAYS)).isoformat()
    rows = conn.execute(
        """SELECT item_id, date, hour, SUM(quantity) AS qty FROM sales_hourly
           WHERE location_id=? AND date>=? AND date<=?
           GROUP BY item_id, date, hour""",
        (location["id"], first, target_date.isoformat()),
    ).fetchall()

    observed = defaultdict(lambda: defaultdict(float))
    allowed = set(slots)
    for row in rows:
        stored = date.fromisoformat(row["date"])
        clock = int(row["hour"])
        for offset in (0, 1):
            trading = stored - timedelta(days=offset)
            slot = offset * 24 + clock
            if slot not in allowed or trading >= target_date or trading.isoformat() < first:
                continue
            if trading.weekday() != target_date.weekday():
                continue
            observed[row["item_id"]][slot] += max(0.0, float(row["qty"] or 0))

    curves = {}
    for item_id, by_slot in observed.items():
        units = sum(by_slot.values())
        if units <= 0:
            continue
        own = units / (units + ITEM_CURVE_PSEUDO_UNITS)
        blended = {
            slot: own * by_slot.get(slot, 0.0) / units + (1.0 - own) * base.get(slot, 0.0)
            for slot in slots
        }
        scale = sum(blended.values()) or 1.0
        curves[item_id] = {slot: value / scale for slot, value in blended.items()}
    return curves


def _data_health(conn: sqlite3.Connection, location_id: str, target_date: date) -> dict[str, Any]:
    span = conn.execute(
        "SELECT MIN(date) AS first_date,MAX(date) AS latest_date,COUNT(DISTINCT date) AS days FROM sales WHERE location_id=?",
        (location_id,),
    ).fetchone()
    latest = date.fromisoformat(span["latest_date"]) if span and span["latest_date"] else None
    # Freshness is measured against the day the forecast is for, capped at today.
    # Looking at a brief from last March should not report the feed as stale, and
    # a forecast two weeks out should not either.
    reference = min(target_date, date.today())
    lag_days = max(0, (reference - latest).days) if latest else None
    if lag_days is None:
        freshness = "missing"
    elif lag_days <= 1:
        freshness = "current"
    elif lag_days <= 3:
        freshness = "delayed"
    else:
        freshness = "stale"
    hourly_days = int(conn.execute(
        "SELECT COUNT(DISTINCT date) AS n FROM sales_hourly WHERE location_id=?", (location_id,)
    ).fetchone()["n"] or 0)
    integrations = {
        row["provider"]: {"status": row["status"], "mode": row["mode"], "last_sync": row["last_sync"]}
        for row in conn.execute(
            "SELECT provider,status,mode,last_sync FROM integrations WHERE location_id=?", (location_id,)
        ).fetchall()
    }
    weather = _weather_map(conn, location_id, target_date, target_date).get(target_date.isoformat())
    return {
        "history_start": span["first_date"] if span else None,
        "latest_sale_date": span["latest_date"] if span else None,
        "history_days": int(span["days"] or 0) if span else 0,
        "hourly_days": hourly_days,
        "pos_freshness": freshness,
        "pos_lag_days": lag_days,
        "pos_mode": integrations.get("pos", {}).get("mode", "unknown"),
        "pos_last_sync": integrations.get("pos", {}).get("last_sync"),
        "weather_source": weather["source"] if weather else None,
        "weather_available": bool(weather),
    }


def service_hours(location: sqlite3.Row | dict[str, Any]) -> list[int]:
    """Every clock hour this location is open, in service order.

    A close after midnight is stored past 24, so a bar open 11 to 2 has
    close_hour 26. That keeps spans a plain subtraction everywhere, and this is
    the one place that turns it back into real clock hours: 11, 12, ... 23, 0, 1.
    """
    opens = int(location["open_hour"])
    closes = int(location["close_hour"])
    if closes <= opens:
        closes += 24
    closes = min(closes, opens + 24)
    return [hour % 24 for hour in range(opens, closes)]


def _service_curve(conn: sqlite3.Connection, location: sqlite3.Row, target_date: date, revenue: float, units: int, context: dict[str, Any]) -> list[dict[str, Any]]:
    hours = service_hours(location)
    slots = service_slots(location)
    if not slots:
        return []
    curve = location_hour_curve(conn, location, target_date, _settings(conn, location["id"]))["shares"]
    # Shift a small amount of demand toward event-overlap hours only when the event
    # has material impact; raw event names never become hard-coded rules.
    adjusted = dict(curve)
    for event in context["events"]:
        if event["impact"] < 0.12:
            continue
        start_hour = _time_minutes(event["start_time"]) // 60
        for slot in adjusted:
            if abs((slot % 24) - start_hour) <= 2:
                adjusted[slot] *= 1.0 + min(0.18, event["impact"] * 0.16)
    total = sum(adjusted.values()) or 1.0
    return [
        {
            "hour": slot % 24,
            "slot": slot,
            "label": hour_label(slot),
            "revenue": round(revenue * adjusted[slot] / total, 2),
            "units": int(round(units * adjusted[slot] / total)),
            "share": round(adjusted[slot] / total, 4),
        }
        for slot in slots
    ]


def _material_pressure(items: list[dict[str, Any]], conn: sqlite3.Connection) -> list[dict[str, Any]]:
    expected: dict[str, float] = defaultdict(float)
    baseline: dict[str, float] = defaultdict(float)
    for item in items:
        row = conn.execute("SELECT inferred_json FROM menu_interpretations WHERE menu_item_id=?", (item["item_id"],)).fetchone()
        try:
            families = json.loads(row["inferred_json"] or "{}").get("material_families", []) if row else []
        except json.JSONDecodeError:
            families = []
        for family in families:
            expected[family] += item["expected"]
            baseline[family] += item["baseline"]
    output = []
    for family, amount in expected.items():
        normal = baseline.get(family, amount)
        output.append({
            "family": family.replace("-", " ").title(),
            "demand_index": int(round(amount)),
            "baseline_index": int(round(normal)),
            "change_units": int(round(amount - normal)),
            "change_percent": round((amount / max(1.0, normal) - 1.0) * 100),
            "note": (
                f"{int(round(amount)):,} portions across the menu today against {int(round(normal)):,} "
                "on a normal day. "
            ),
        })
    return sorted(output, key=lambda row: abs(row["change_percent"]), reverse=True)[:8]


def _day_drivers(
    items: list[dict[str, Any]],
    context: dict[str, Any],
    baseline_units: float,
    baseline_sales: float,
) -> list[dict[str, Any]]:
    """Roll per-item drivers up to the day, in units and money as well as a percentage.

    The recent trend is written from store totals, so the sentence and the
    percentage next to it come from the same two numbers. Everything else
    takes the median item effect and the sentence of the biggest item.
    """
    grouped: dict[str, list[int]] = defaultdict(list)
    details: dict[str, str] = {}
    labels: dict[str, str] = {}
    based_on: dict[str, str] = {}
    for item in items:
        for driver in item["drivers"]:
            if driver["key"] in {"override", "trend"}:
                continue
            grouped[driver["key"]].append(int(driver["effect"]))
            details.setdefault(driver["key"], driver["detail"])
            labels.setdefault(driver["key"], driver.get("label") or DRIVER_LABELS.get(driver["key"], driver["key"].title()))
            based_on.setdefault(driver["key"], driver.get("based_on", ""))

    output = []
    for key, values in grouped.items():
        median = int(round(safe_median(values)))
        if abs(median) < 2:
            continue
        share = median / 100.0
        output.append({
            "key": key,
            "label": labels.get(key, DRIVER_LABELS.get(key, key.title())),
            "effect": median,
            "units": int(round(baseline_units * share)),
            "sales": round(baseline_sales * share, 2),
            "detail": details.get(key, ""),
            "based_on": based_on.get(key, ""),
        })

    recent = sum(float(item["evidence"].get("recent_average") or 0) for item in items)
    prior = sum(float(item["evidence"].get("prior_average") or 0) for item in items)
    if recent > 0 and prior > 0:
        percent = round((recent / prior - 1.0) * 100)
        if abs(percent) >= 2:
            output.append({
                "key": "trend",
                "label": DRIVER_LABELS["trend"],
                "effect": percent,
                "units": int(round(baseline_units * percent / 100.0)),
                "sales": round(baseline_sales * percent / 100.0, 2),
                "detail": (
                    f"The last four weeks averaged {recent:,.0f} items a day against {prior:,.0f} "
                    "in the four weeks before."
                ),
                "based_on": "the last eight weeks of sales",
            })
    output.sort(key=lambda row: abs(row["effect"]), reverse=True)
    return output[:5]


def forecast_day(
    conn: sqlite3.Connection, location_id: str, target_date: date, record_run: bool = False,
    data_version: str | None = None,
) -> dict[str, Any]:
    location = _load_location(conn, location_id)
    ensure_location_interpretations(conn, location_id)
    # Menu order is the order items were put on the menu, and categories follow
    # the first item of theirs that appears. The make list keeps that order, so
    # a station reads its own items together.
    item_rows = conn.execute("SELECT * FROM menu_items WHERE location_id=? AND active=1 ORDER BY rowid", (location_id,)).fetchall()
    category_rank: dict[str, int] = {}
    for row in item_rows:
        category_rank.setdefault(row["category"], len(category_rank))
    # Resolved once for the whole menu, so the make number follows the owner's
    # costs screen without a query per row.
    shares: dict[str, float] = {}
    try:
        from .costs import cost_settings, item_cost_basis

        settings = cost_settings(conn, location_id)
        shares = {
            key: float(value["share"])
            for key, value in item_cost_basis(conn, location_id, settings).items()
        }
    except Exception:  # a costing failure must never stop a forecast
        shares = {}
    items = [forecast_item(conn, item, target_date, shares.get(item["id"])) for item in item_rows]
    items.sort(key=lambda row: (category_rank.get(row["category"], len(category_rank)), -(row["model_expected"] * row["price"])))
    # Items with under a week of sales are listed but stay out of every total.
    live = [row for row in items if not row.get("new_item")]
    expected_revenue = round(sum(row["expected"] * row["price"] for row in live), 2)
    baseline_revenue = round(sum(row["baseline"] * row["price"] for row in live), 2)
    expected_units = sum(row["expected"] for row in live)
    make_units = sum(row["make"] for row in live)
    confidence = int(round(safe_mean(row["confidence"] for row in live))) if live else 0
    context = live[0]["context"] if live else (items[0]["context"] if items else build_context(location, target_date, None, []))
    curve = _service_curve(conn, location, target_date, expected_revenue, expected_units, context)
    peak = max(curve, key=lambda row: row["revenue"], default=None)
    change = round((expected_revenue / max(1.0, baseline_revenue) - 1.0) * 100)
    level = demand_level(change)

    movers = sorted(live, key=lambda row: abs(row["vs_baseline_units"]), reverse=True)
    top_surges = [row for row in movers if row["vs_baseline_units"] >= 2 and row["vs_baseline_percent"] >= 4][:5]
    top_drops = [row for row in movers if row["vs_baseline_units"] <= -2 and row["vs_baseline_percent"] <= -4][:4]
    top_volume = sorted(live, key=lambda row: row["make"], reverse=True)

    weekday_name = target_date.strftime("%A")
    baseline_units = sum(row["baseline"] for row in live)
    average_price = safe_mean([row["price"] for row in live], 8.0)
    basket = 2.4 if average_price <= 6 else (2.1 if average_price <= 11 else 1.9)
    expected_orders = max(1, int(round(expected_units / basket))) if expected_units else 0
    baseline_orders = max(1, int(round(baseline_units / basket))) if baseline_units else 0
    comparable_days = int(round(safe_median([row["evidence"]["weekday_samples"] for row in live]))) if live else 0
    data_health = _data_health(conn, location_id, target_date)
    history_days = data_health["history_days"]
    recent = conn.execute(
        "SELECT AVG(accuracy) AS accuracy, COUNT(*) AS days FROM (SELECT accuracy FROM day_accuracy WHERE location_id=? ORDER BY date DESC LIMIT 21)",
        (location_id,),
    ).fetchone()
    measured_accuracy = float(recent["accuracy"]) if recent and recent["accuracy"] is not None else None
    days_tested = int(recent["days"] or 0) if recent else 0
    # Once there are enough closed days, the score people see is grounded in how
    # this location's forecasts have actually landed, not in an estimate
    # of itself. Claiming 95% confidence beside a measured 9% error is the kind
    # of thing that costs trust the first time someone checks.
    if measured_accuracy is not None and days_tested >= 7:
        confidence = int(round(clamp(0.35 * confidence + 0.65 * measured_accuracy, 40, 97)))

    # What to do: only where the make number differs from what a normal such
    # day would make. Same number, nothing to say. The busiest hour lives in
    # the summary and is not repeated here.
    actions: list[dict[str, Any]] = []
    moved = [row for row in live if abs(int(row["make"]) - int(row["normal_make"])) >= 2]
    moved.sort(key=lambda row: abs((row["make"] - row["normal_make"]) * row["price"]), reverse=True)
    for row in moved[:4]:
        diff = int(row["make"]) - int(row["normal_make"])
        word = "more" if diff > 0 else "fewer"
        override = row.get("override") or {}
        if override:
            who = override.get("updated_by") or "a manager"
            why = override.get("reason") or ""
            detail = f"Set by {who}" + (f": {why}." if why else ".") + f" Quantify suggested {row['suggested_make']}."
        else:
            detail = (
                f"About {row['expected']} are expected to sell against {row['baseline']} on a normal "
                f"{weekday_name}, which usually means making {row['normal_make']}."
            )
        actions.append({
            "type": "prep",
            "title": f"Make {abs(diff)} {word} {plural_name(row['name'], abs(diff))} than a normal {weekday_name}",
            "detail": detail,
            "metric": f"{row['make']} to make",
            "item_id": row["item_id"],
            "make": int(row["make"]),
            "normal_make": int(row["normal_make"]),
            "expected": int(row["expected"]),
            "normal": int(row["baseline"]),
            "adjusted": bool(override),
        })

    difference_sales = round(expected_revenue - baseline_revenue, 2)
    difference_units = expected_units - int(round(baseline_units))
    if abs(change) < 4:
        headline = f"A normal {weekday_name}"
    else:
        word = "busier" if change > 0 else "quieter"
        headline = f"{abs(change)}% {word} than a normal {weekday_name}"
    context_signals = _day_drivers(live, context, baseline_units, baseline_revenue)
    menu_summary = menu_intelligence_view(conn, location_id)["summary"]

    result = {
        "location": dict(location),
        "date": target_date.isoformat(),
        "date_label": target_date.strftime("%A, %B %d, %Y").replace(" 0", " "),
        "generated_at": utc_now(),
        "headline": headline,
        "no_history": history_days == 0,
        "summary": {
            "demand_level": level,
            "expected_revenue": expected_revenue,
            "baseline_revenue": baseline_revenue,
            "revenue_change_percent": change,
            "expected_units": expected_units,
            "make_units": make_units,
            "baseline_units": int(round(baseline_units)),
            "expected_orders": expected_orders,
            "baseline_orders": baseline_orders,
            "average_order": round(expected_revenue / expected_orders, 2) if expected_orders else 0.0,
            "difference_sales": difference_sales,
            "difference_units": difference_units,
            "difference_orders": expected_orders - baseline_orders,
            "confidence": confidence,
            "peak_hour": peak["label"] if peak else None,
            "peak_revenue": peak["revenue"] if peak else 0,
            "peak_units": peak["units"] if peak else 0,
            "peak_share_percent": round((peak["revenue"] / max(1.0, expected_revenue)) * 100) if peak else 0,
            "model_version": MODEL_VERSION,
        },
        "comparison": {
            "label": f"a normal {weekday_name}",
            "sales": baseline_revenue,
            "units": int(round(baseline_units)),
            "orders": baseline_orders,
            "based_on_days": comparable_days,
        },
        "trust": {
            "score": confidence,
            "history_days": history_days,
            "comparable_days": comparable_days,
            "days_tested": days_tested,
            "measured_accuracy": round(measured_accuracy, 1) if measured_accuracy is not None else None,
            "recent_error_percent": round(100 - measured_accuracy, 1) if measured_accuracy is not None else None,
        },
        "actions": actions,
        "priorities": actions,  # kept so the daily email keeps working unchanged
        "top_volume": top_volume,
        "top_surges": top_surges,
        "top_drops": top_drops,
        "items": items,
        "service_curve": curve,
        "context": {
            "weather": ({
                "condition": context["weather_condition"], "high": round(context["temp_high"]),
                "low": round(context["temp_low"]), "precipitation_mm": round(context["precipitation_mm"], 1),
                "snowfall_cm": round(context.get("snowfall_cm", 0.0), 1), "uv_index": round(context.get("uv_index", 0.0), 1),
                "source": context["weather_source"], "available": True,
            } if context["weather_available"] else {"available": False, "source": "", "condition": None,
                "high": None, "low": None, "precipitation_mm": None, "snowfall_cm": None, "uv_index": None}),
            "occasion_name": context["occasion_name"],
            "signals": context_signals,
            "material_events": [event for event in context["events"] if event["impact"] >= 0.12][:5],
            "event_candidates_reviewed": len(context["events"]),
        },
        "material_pressure": _material_pressure(live, conn),
        "menu_intelligence": menu_summary,
        "data_health": data_health,
        "data_note": "",
    }
    if record_run:
        _record_run(conn, location_id, target_date, result, items, data_version or _data_version(conn, location_id))
    return result


def _record_run(
    conn: sqlite3.Connection, location_id: str, target_date: date, result: dict[str, Any],
    items: list[dict[str, Any]], data_version: str,
) -> bool:
    """Write the run once per (location, date, data version, model version).

    Reading the plan is not an event. A row is written the first time this
    combination is computed and never again, so the table records what changed
    and not how many tablets were open.
    """
    saved = conn.execute(
        "INSERT OR IGNORE INTO forecast_runs(id,location_id,target_date,generated_at,model_version,history_days,context_json,summary_json,data_version) VALUES(?,?,?,?,?,?,?,?,?)",
        (
            f"run-{uuid.uuid4().hex}", location_id, target_date.isoformat(), result["generated_at"], MODEL_VERSION,
            max((row["model"]["history_days"] for row in items), default=0),
            json.dumps(result["context"], separators=(",", ":")),
            json.dumps(result["summary"] | {"data_version": data_version}, separators=(",", ":")),
            data_version,
        ),
    )
    conn.commit()
    return saved.rowcount > 0


def forecast_range(conn: sqlite3.Connection, location_id: str, start_date: date, days: int = 14) -> dict[str, Any]:
    days = max(1, min(31, days))
    forecasts = [forecast_day(conn, location_id, start_date + timedelta(days=index)) for index in range(days)]
    return {
        "location_id": location_id,
        "start_date": start_date.isoformat(),
        "days": [
            {
                "date": row["date"], "date_label": row["date_label"],
                **row["summary"],
                "weather": row["context"]["weather"],
                "occasion_name": row["context"]["occasion_name"],
                "top_surges": row["top_surges"][:3],
                "top_item": row["top_volume"][0]["name"] if row["top_volume"] else None,
                "top_item_units": row["top_volume"][0]["expected"] if row["top_volume"] else 0,
                "signals": row["context"]["signals"][:3],
            }
            for row in forecasts
        ],
        "selected": forecasts[0],
    }


def daily_brief(
    conn: sqlite3.Connection, location_id: str, target_date: date, week_days: int = 7,
    data_version: str | None = None, refresh: bool = False,
) -> dict[str, Any]:
    version = data_version or _data_version(conn, location_id)
    key = (_db_identity(conn), location_id, target_date.isoformat(), version, max(2, min(14, week_days)), MODEL_VERSION)
    # Serialize just this cache shard, so simultaneous tablets share one build.
    with _BRIEF_LOCKS[hash(key) % len(_BRIEF_LOCKS)]:
        with _CACHE_LOCK:
            cached = _BRIEF_CACHE.get(key)
        if not refresh and cached and time.monotonic() - cached[0] < _BRIEF_CACHE_SECONDS:
            return copy.deepcopy(cached[1])
        value = _build_daily_brief(conn, location_id, target_date, week_days, version)
        with _CACHE_LOCK:
            _BRIEF_CACHE[key] = (time.monotonic(), value)
            _trim_cache(_BRIEF_CACHE, 96)
        return copy.deepcopy(value)


def _build_daily_brief(
    conn: sqlite3.Connection, location_id: str, target_date: date, week_days: int,
    data_version: str,
) -> dict[str, Any]:
    today = forecast_day(conn, location_id, target_date, record_run=True, data_version=data_version)
    week = []
    for index in range(1, max(2, min(14, week_days))):
        row = forecast_day(conn, location_id, target_date + timedelta(days=index))
        week.append({
            "date": row["date"], "date_label": row["date_label"],
            "expected_revenue": row["summary"]["expected_revenue"],
            "expected_units": row["summary"]["expected_units"],
            "change_percent": row["summary"]["revenue_change_percent"],
            "demand_level": row["summary"]["demand_level"],
            "confidence": row["summary"]["confidence"],
            "peak_hour": row["summary"]["peak_hour"],
            "top_item": row["top_volume"][0]["name"] if row["top_volume"] else None,
            "top_item_units": row["top_volume"][0]["expected"] if row["top_volume"] else 0,
            "occasion_name": row["context"]["occasion_name"],
        })
    today["week_ahead"] = week
    return today


def set_override(
    conn: sqlite3.Connection, location_id: str, item_id: str, target_date: date, quantity: int,
    reason: str = "", updated_by: str = "",
) -> dict[str, Any]:
    if quantity < 0 or quantity > 100_000:
        raise ValueError("The number to make must be between 0 and 100,000")
    reason = (reason or "").strip()[:200]
    item = conn.execute("SELECT * FROM menu_items WHERE id=? AND location_id=?", (item_id, location_id)).fetchone()
    if item is None:
        raise ValueError("That item is not on this location")
    conn.execute(
        """INSERT INTO forecast_overrides(location_id,item_id,date,quantity,reason,updated_at,updated_by)
           VALUES(?,?,?,?,?,?,?)
           ON CONFLICT(location_id,item_id,date) DO UPDATE SET
             quantity=excluded.quantity,reason=excluded.reason,updated_at=excluded.updated_at,
             updated_by=excluded.updated_by""",
        (location_id, item_id, target_date.isoformat(), quantity, reason, utc_now(), (updated_by or "").strip()[:80]),
    )
    conn.commit()
    return forecast_item(conn, item, target_date)


def clear_override(conn: sqlite3.Connection, location_id: str, item_id: str, target_date: date) -> None:
    conn.execute("DELETE FROM forecast_overrides WHERE location_id=? AND item_id=? AND date=?", (location_id, item_id, target_date.isoformat()))
    conn.commit()


ACCURACY_WINDOW_DAYS = 30


def lean_words(bias: float) -> str:
    """Which way the calls usually miss, in words."""
    if bias > 0.5:
        return "usually low"
    if bias < -0.5:
        return "usually high"
    return "no steady lean"


def performance(conn: sqlite3.Connection, location_id: str, as_of: date, days: int = ACCURACY_WINDOW_DAYS) -> dict[str, Any]:
    """The track record over the last `days` closed trading days, item by item.

    One window for the tiles, the chart and the table, counted in days the
    place actually traded, so a gap in register data does not shrink the
    sample. Every figure is read from the same day scores the History rows and
    the day sheet show, so no two screens can disagree about a day. Days in the
    window that have not been scored yet are scored here, once, with the same
    walk-forward the background scorer uses.
    """
    days = max(7, min(int(days), 90))
    closed = [
        row["date"] for row in conn.execute(
            "SELECT DISTINCT date FROM sales WHERE location_id=? AND date<? ORDER BY date DESC LIMIT ?",
            (location_id, as_of.isoformat(), days),
        ).fetchall()
    ]
    dates = sorted(closed)
    empty = {
        "location_id": location_id,
        "start_date": dates[0] if dates else None,
        "end_date": dates[-1] if dates else None,
        "summary": {
            "forecast_accuracy": None, "wape": None, "average_day_accuracy": None,
            "days_evaluated": 0, "days_pending": len(dates), "items_evaluated": 0,
            "days_from_stored_call": 0, "window_days": days, "manual_inventory_required": False,
        },
        "daily": [],
        "item_accuracy": [],
        "trend": {"series": [], "average": None, "days": 0, "best": None, "worst": None,
                  "best_date": None, "worst_date": None, "within_ten": None},
        "note": "Accuracy is measured against what the registers actually rang.",
    }
    if not dates:
        return empty

    def scored_rows() -> dict[str, dict[str, Any]]:
        from .transactions import normalized_score
        marks = ",".join("?" * len(dates))
        return {
            row["date"]: normalized_score(dict(row))
            for row in conn.execute(
                f"SELECT * FROM day_accuracy WHERE location_id=? AND date IN ({marks})",
                (location_id, *dates),
            ).fetchall()
        }

    scored = scored_rows()
    missing = [day for day in dates if day not in scored]
    if missing:
        from .transactions import score_range  # imported here: transactions reads this module

        # Contiguous runs, so each run is one walk-forward fit.
        start = previous = date.fromisoformat(missing[0])
        runs: list[tuple[date, date]] = []
        for day in missing[1:]:
            current = date.fromisoformat(day)
            if (current - previous).days > 3:
                runs.append((start, previous))
                start = current
            previous = current
        runs.append((start, previous))
        for first, last in runs:
            try:
                score_range(conn, location_id, first, last)
            except Exception:  # noqa: BLE001 - a day that will not score is left pending
                continue
        scored = scored_rows()

    per_item: dict[str, dict[str, Any]] = {}
    daily_rows: list[dict[str, Any]] = []
    total_actual = 0.0
    total_error = 0.0
    stored_calls = 0
    for day in dates:
        row = scored.get(day)
        if row is None:
            continue
        try:
            entries = json.loads(row["items_json"] or "[]")
        except (TypeError, ValueError):
            entries = []
        for entry in entries:
            actual = float(entry.get("actual") or 0)
            predicted = float(entry.get("predicted") or 0)
            bucket = per_item.setdefault(entry.get("item_id"), {
                "item_id": entry.get("item_id"), "name": entry.get("name"),
                "actual": 0.0, "predicted": 0.0, "error": 0.0, "days": 0, "bias": 0.0,
            })
            bucket["actual"] += actual
            bucket["predicted"] += predicted
            bucket["error"] += abs(actual - predicted)
            bucket["bias"] += actual - predicted
            bucket["days"] += 1
            total_actual += actual
            total_error += abs(actual - predicted)
        if row.get("call_source") == "stored":
            stored_calls += 1
        daily_rows.append({
            "date": day,
            "actual": int(round(float(row["actual_units"] or 0))),
            "predicted": int(round(float(row["predicted_units"] or 0))),
            "revenue": round(float(row["actual_sales"] or 0), 2),
            "accuracy": round(float(row["accuracy"] or 0), 1),
            "call_source": row.get("call_source") or "reconstructed",
        })
    if not daily_rows:
        return empty

    wape_total = total_error / max(1.0, total_actual) * 100
    accuracy = round(max(0.0, 100 - wape_total), 1)
    item_errors = []
    for bucket in per_item.values():
        if bucket["actual"] <= 0 or bucket["days"] == 0:
            continue
        wape = bucket["error"] / bucket["actual"] * 100
        item_errors.append({
            "item_id": bucket["item_id"],
            "name": bucket["name"],
            "wape": round(wape, 1),
            "accuracy": round(max(0.0, 100 - wape), 1),
            "actual_units": int(round(bucket["actual"])),
            "days": bucket["days"],
            "average_miss": round(bucket["error"] / bucket["days"], 1),
            "usually": lean_words(bucket["bias"] / bucket["days"]),
        })
    item_errors.sort(key=lambda row: row["wape"], reverse=True)

    values = [row["accuracy"] for row in daily_rows]
    best = max(daily_rows, key=lambda row: row["accuracy"])
    worst = min(daily_rows, key=lambda row: row["accuracy"])
    within = sum(1 for value in values if value >= 90)
    return {
        "location_id": location_id,
        "start_date": daily_rows[0]["date"],
        "end_date": daily_rows[-1]["date"],
        "summary": {
            "forecast_accuracy": accuracy,
            "wape": round(wape_total, 1),
            "average_day_accuracy": round(sum(values) / len(values), 1),
            "days_evaluated": len(daily_rows),
            "days_pending": len(dates) - len(daily_rows),
            "items_evaluated": len(item_errors),
            "days_from_stored_call": stored_calls,
            "window_days": days,
            "manual_inventory_required": False,
        },
        "daily": daily_rows,
        "item_accuracy": item_errors,
        "trend": {
            "series": [
                {"date": row["date"], "accuracy": row["accuracy"], "predicted": row["predicted"], "actual": row["actual"]}
                for row in daily_rows
            ],
            "average": round(sum(values) / len(values), 1),
            "days": len(values),
            "best": best["accuracy"],
            "best_date": best["date"],
            "worst": worst["accuracy"],
            "worst_date": worst["date"],
            "within_ten": round(within / len(values) * 100),
        },
        "note": "Accuracy is measured against what the registers actually rang.",
    }
