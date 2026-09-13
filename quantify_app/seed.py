from __future__ import annotations

import hashlib
import json
import math
import random
import sqlite3
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

from .database import table_count
from .intelligence import calendar_occasions, event_impact
from .menu_intelligence import upsert_interpretation


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _stable_seed(*parts: object) -> int:
    text = "|".join(str(part) for part in parts)
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:16], 16)


def _rng(*parts: object) -> random.Random:
    return random.Random(_stable_seed(*parts))


LOCATIONS: list[dict[str, Any]] = [{'id': 'loc-bakery',
  'name': 'Juniper Bakehouse',
  'concept': 'Bakery & café',
  'address': '104 Main Street',
  'city': 'Scarsdale',
  'region': 'NY',
  'postal_code': '10583',
  'latitude': 40.9893,
  'longitude': -73.7974,
  'timezone': 'America/New_York',
  'open_hour': 6,
  'close_hour': 18,
  'day_multipliers': [0.82, 0.88, 0.93, 1.0, 1.12, 1.34, 1.22],
  'hour_curve': {'6': 0.03,
                 '7': 0.09,
                 '8': 0.16,
                 '9': 0.16,
                 '10': 0.12,
                 '11': 0.09,
                 '12': 0.1,
                 '13': 0.08,
                 '14': 0.06,
                 '15': 0.05,
                 '16': 0.04,
                 '17': 0.02},
  'menu': [{'key': 'butter-croissant',
            'name': 'Butter croissant',
            'category': 'Viennoiserie',
            'price': 4.5,
            'base': 66,
            'lead': 720,
            'hold': 14,
            'temp': -0.02,
            'rain': 0.04,
            'event': 0.1},
           {'key': 'choc-croissant',
            'name': 'Chocolate croissant',
            'category': 'Viennoiserie',
            'price': 5.25,
            'base': 39,
            'lead': 720,
            'hold': 14,
            'temp': -0.03,
            'rain': 0.05,
            'event': 0.12},
           {'key': 'cinnamon-roll',
            'name': 'Cinnamon roll',
            'category': 'Pastry',
            'price': 5.5,
            'base': 31,
            'lead': 540,
            'hold': 12,
            'temp': -0.09,
            'rain': 0.1,
            'event': 0.08},
           {'key': 'blueberry-muffin',
            'name': 'Blueberry muffin',
            'category': 'Pastry',
            'price': 4.25,
            'base': 28,
            'lead': 180,
            'hold': 20,
            'temp': 0.04,
            'rain': -0.02,
            'event': 0.06},
           {'key': 'sourdough',
            'name': 'Country sourdough',
            'category': 'Bread',
            'price': 8.5,
            'base': 24,
            'lead': 1080,
            'hold': 48,
            'temp': -0.03,
            'rain': 0.02,
            'event': 0.04},
           {'key': 'baguette',
            'name': 'Baguette',
            'category': 'Bread',
            'price': 4.75,
            'base': 34,
            'lead': 720,
            'hold': 18,
            'temp': 0.01,
            'rain': 0.01,
            'event': 0.05},
           {'key': 'egg-sandwich',
            'name': 'Egg & cheddar sandwich',
            'category': 'Breakfast',
            'price': 9.5,
            'base': 42,
            'lead': 20,
            'hold': 1.5,
            'temp': -0.04,
            'rain': 0.08,
            'event': 0.15},
           {'key': 'turkey-club',
            'name': 'Turkey club',
            'category': 'Lunch',
            'price': 13.0,
            'base': 23,
            'lead': 15,
            'hold': 1,
            'temp': 0.07,
            'rain': -0.05,
            'event': 0.18},
           {'key': 'iced-latte',
            'name': 'Iced latte',
            'category': 'Beverage',
            'price': 5.75,
            'base': 58,
            'lead': 3,
            'hold': 0.25,
            'temp': 0.32,
            'rain': -0.11,
            'event': 0.12},
           {'key': 'hot-coffee',
            'name': 'Drip coffee',
            'category': 'Beverage',
            'price': 3.25,
            'base': 72,
            'lead': 8,
            'hold': 1,
            'temp': -0.25,
            'rain': 0.12,
            'event': 0.08}]},
 {'id': 'loc-pizza',
  'name': 'Northline Pizza',
  'concept': 'Pizzeria',
  'address': '41 Central Avenue',
  'city': 'White Plains',
  'region': 'NY',
  'postal_code': '10606',
  'latitude': 41.0242,
  'longitude': -73.7698,
  'timezone': 'America/New_York',
  'open_hour': 11,
  'close_hour': 23,
  'day_multipliers': [0.78, 0.82, 0.88, 0.96, 1.24, 1.38, 1.1],
  'hour_curve': {'11': 0.05,
                 '12': 0.1,
                 '13': 0.08,
                 '14': 0.04,
                 '15': 0.03,
                 '16': 0.05,
                 '17': 0.1,
                 '18': 0.16,
                 '19': 0.17,
                 '20': 0.11,
                 '21': 0.07,
                 '22': 0.04},
  'menu': [{'key': 'cheese-pie',
            'name': 'Classic cheese pie',
            'category': 'Whole pies',
            'price': 20.0,
            'base': 43,
            'lead': 18,
            'hold': 0.4,
            'temp': -0.03,
            'rain': 0.11,
            'event': 0.18},
           {'key': 'pepperoni-pie',
            'name': 'Pepperoni pie',
            'category': 'Whole pies',
            'price': 24.0,
            'base': 37,
            'lead': 18,
            'hold': 0.4,
            'temp': -0.02,
            'rain': 0.12,
            'event': 0.22},
           {'key': 'margherita',
            'name': 'Margherita pie',
            'category': 'Whole pies',
            'price': 23.0,
            'base': 22,
            'lead': 18,
            'hold': 0.4,
            'temp': 0.11,
            'rain': -0.04,
            'event': 0.14},
           {'key': 'cheese-slice',
            'name': 'Cheese slice',
            'category': 'Slices',
            'price': 3.75,
            'base': 91,
            'lead': 10,
            'hold': 0.7,
            'temp': 0.03,
            'rain': 0.02,
            'event': 0.26},
           {'key': 'pepperoni-slice',
            'name': 'Pepperoni slice',
            'category': 'Slices',
            'price': 4.5,
            'base': 72,
            'lead': 10,
            'hold': 0.7,
            'temp': 0.02,
            'rain': 0.03,
            'event': 0.28},
           {'key': 'garlic-knots',
            'name': 'Garlic knots (6)',
            'category': 'Sides',
            'price': 6.0,
            'base': 38,
            'lead': 14,
            'hold': 1.5,
            'temp': -0.07,
            'rain': 0.08,
            'event': 0.19},
           {'key': 'chicken-parm',
            'name': 'Chicken parm hero',
            'category': 'Heroes',
            'price': 13.5,
            'base': 29,
            'lead': 12,
            'hold': 0.5,
            'temp': -0.06,
            'rain': 0.1,
            'event': 0.21},
           {'key': 'caesar',
            'name': 'Caesar salad',
            'category': 'Salads',
            'price': 11.0,
            'base': 21,
            'lead': 7,
            'hold': 0.4,
            'temp': 0.19,
            'rain': -0.12,
            'event': 0.1}]},
 {'id': 'loc-burger',
  'name': 'Foundry Burger',
  'concept': 'Burger shop',
  'address': '8 Mamaroneck Avenue',
  'city': 'White Plains',
  'region': 'NY',
  'postal_code': '10601',
  'latitude': 41.032,
  'longitude': -73.765,
  'timezone': 'America/New_York',
  'open_hour': 11,
  'close_hour': 22,
  'day_multipliers': [0.76, 0.82, 0.89, 0.98, 1.22, 1.36, 1.08],
  'hour_curve': {'11': 0.05,
                 '12': 0.12,
                 '13': 0.1,
                 '14': 0.04,
                 '15': 0.03,
                 '16': 0.05,
                 '17': 0.1,
                 '18': 0.17,
                 '19': 0.16,
                 '20': 0.11,
                 '21': 0.07},
  'menu': [{'key': 'classic',
            'name': 'Foundry classic',
            'category': 'Burgers',
            'price': 11.5,
            'base': 73,
            'lead': 12,
            'hold': 0.35,
            'temp': 0.04,
            'rain': 0.02,
            'event': 0.22},
           {'key': 'double',
            'name': 'Double foundry',
            'category': 'Burgers',
            'price': 15.5,
            'base': 39,
            'lead': 14,
            'hold': 0.35,
            'temp': 0.02,
            'rain': 0.04,
            'event': 0.25},
           {'key': 'bacon',
            'name': 'Smokehouse burger',
            'category': 'Burgers',
            'price': 14.5,
            'base': 34,
            'lead': 14,
            'hold': 0.35,
            'temp': -0.01,
            'rain': 0.06,
            'event': 0.24},
           {'key': 'chicken',
            'name': 'Crispy chicken sandwich',
            'category': 'Chicken',
            'price': 12.5,
            'base': 47,
            'lead': 12,
            'hold': 0.35,
            'temp': 0.06,
            'rain': 0.01,
            'event': 0.2},
           {'key': 'fries',
            'name': 'Foundry fries',
            'category': 'Sides',
            'price': 4.75,
            'base': 142,
            'lead': 5,
            'hold': 0.18,
            'temp': 0.02,
            'rain': 0.02,
            'event': 0.27},
           {'key': 'loaded-fries',
            'name': 'Loaded fries',
            'category': 'Sides',
            'price': 8.5,
            'base': 31,
            'lead': 8,
            'hold': 0.18,
            'temp': -0.03,
            'rain': 0.05,
            'event': 0.23},
           {'key': 'vanilla-shake',
            'name': 'Vanilla shake',
            'category': 'Shakes',
            'price': 6.5,
            'base': 36,
            'lead': 4,
            'hold': 0.2,
            'temp': 0.31,
            'rain': -0.1,
            'event': 0.14},
           {'key': 'choc-shake',
            'name': 'Chocolate shake',
            'category': 'Shakes',
            'price': 6.75,
            'base': 32,
            'lead': 4,
            'hold': 0.2,
            'temp': 0.29,
            'rain': -0.09,
            'event': 0.14}]}]


def seed_if_empty(conn: sqlite3.Connection, today: date | None = None) -> None:
    if table_count(conn, "locations"):
        return
    seed_demo(conn, today=today)


def _allocate_hourly(quantity: int, curve: dict[str, float], rng: random.Random) -> dict[int, int]:
    if quantity <= 0:
        return {}
    normalized = {int(hour): max(0.0, float(weight)) for hour, weight in curve.items()}
    total = sum(normalized.values()) or 1.0
    exact = {hour: quantity * weight / total for hour, weight in normalized.items()}
    allocated = {hour: int(value) for hour, value in exact.items()}
    remainder = quantity - sum(allocated.values())
    ranked = sorted(normalized, key=lambda hour: (exact[hour] - allocated[hour]) + rng.random() * 0.02, reverse=True)
    for hour in ranked[:remainder]:
        allocated[hour] += 1
    return {hour: value for hour, value in allocated.items() if value > 0}


EVENT_CATALOG: dict[str, list[tuple[str, str, int, float]]] = {
    "loc-bakery": [
        ("Weekend farmers market", "festivals", 1800, 0.68),
        ("Regional business conference", "conferences", 2400, 0.55),
        ("Outdoor concert", "concerts", 3200, 0.72),
        ("Theater matinee", "performing-arts", 900, 0.48),
        ("Road closure and parade", "community", 4100, 0.60),
        ("College athletics meet", "sports", 1900, 0.50),
    ],
    "loc-pizza": [
        ("Arena basketball game", "sports", 5200, 0.82),
        ("Downtown music night", "concerts", 3600, 0.74),
        ("Trade exposition", "conferences", 4700, 0.66),
        ("Film festival screening", "festivals", 1400, 0.51),
        ("Touring comedy show", "performing-arts", 1600, 0.58),
        ("Transit disruption", "disruptions", 9000, 0.44),
    ],
    "loc-burger": [
        ("Arena hockey game", "sports", 4800, 0.84),
        ("Street food festival", "festivals", 6200, 0.88),
        ("Live concert", "concerts", 3500, 0.76),
        ("Professional conference", "conferences", 2800, 0.57),
        ("Performing arts premiere", "performing-arts", 1300, 0.54),
        ("Marathon route closure", "disruptions", 11000, 0.62),
    ],
}

FUTURE_EVENTS: list[tuple[int, str, str, str, str, float, int, float]] = [
    (1, "Regional championship game", "sports", "19:00", "22:30", 0.9, 6200, 0.90),
    (3, "Downtown summer festival", "festivals", "11:00", "20:00", 2.4, 8500, 0.82),
    (5, "Medical industry conference", "conferences", "08:00", "17:30", 4.8, 3700, 0.66),
    (8, "Touring concert", "concerts", "19:30", "23:00", 1.7, 5100, 0.84),
    (11, "Major road closure", "disruptions", "06:00", "16:00", 0.5, 12000, 0.61),
]


def _seed_location(
    conn: sqlite3.Connection,
    template: dict[str, Any],
    organization_id: str,
    location_id: str,
    today: date,
    history_days: int,
    owner_email: str = "owner@demo.example",
    seed_key: str | None = None,
) -> None:
    """Build one location and its whole history in a single pass.

    Every random draw is keyed by the seed key, the item, and the date, so the
    result is identical no matter what order locations are built in.
    """
    now = _utc_now()
    key = seed_key or location_id
    suffix = key[4:]
    weather_cache: dict[str, tuple[float, float, float, str]] = {}
    event_loads: dict[str, float] = {}

    conn.execute(
        """INSERT INTO locations(id,organization_id,name,concept,address,city,region,postal_code,
           latitude,longitude,timezone,open_hour,close_hour,currency,active)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
        (location_id, organization_id, template["name"], template["concept"], template["address"],
         template["city"], template["region"], template["postal_code"], template["latitude"],
         template["longitude"], template["timezone"], template["open_hour"], template["close_hour"], "USD"),
    )
    settings = {
        "hour_curve": json.dumps(template["hour_curve"], separators=(",", ":")),
        "day_multipliers": json.dumps(template["day_multipliers"], separators=(",", ":")),
        "history_days": str(history_days),
        "forecast_horizon_days": "14",
        "event_search_radius_miles": "15",
        "context_strategy": "rank-all-material-signals",
    }
    conn.executemany(
        "INSERT INTO settings(location_id,key,value) VALUES(?,?,?)",
        [(location_id, name, value) for name, value in settings.items()],
    )
    for provider, status, details in (
        ("pos", "connected", {"label": "Sample register history", "read_only": True, "webhook_ready": True}),
        ("weather", "connected", {"label": "Sample past and forecast weather", "features": ["temperature", "rain", "snow", "condition"]}),
        ("events", "connected", {"label": "Sample nearby activity", "strategy": "all categories ranked by impact", "hard_radius_limit": False}),
    ):
        conn.execute(
            "INSERT INTO integrations(location_id,provider,status,last_sync,mode,details) VALUES(?,?,?,?,?,?)",
            (location_id, provider, status, now, "demo", json.dumps(details, separators=(",", ":"))),
        )
    conn.execute(
        """INSERT INTO email_preferences(location_id,owner_email,enabled,send_time,timezone,
           include_week_ahead,last_sent_date,updated_at) VALUES(?,?,?,?,?,?,?,?)""",
        (location_id, owner_email, 0, "05:30", template["timezone"], 1, None, now),
    )

    for menu_item in template["menu"]:
        item_id = f"item-{location_id[4:]}-{menu_item['key']}"
        conn.execute(
            """INSERT INTO menu_items(id,location_id,pos_item_id,name,category,price,base_daily_qty,active)
               VALUES(?,?,?,?,?,?,?,1)""",
            (item_id, location_id, f"demo-{menu_item['key']}", menu_item["name"], menu_item["category"],
             menu_item["price"], menu_item["base"]),
        )
        upsert_interpretation(conn, item_id, menu_item["name"], menu_item["category"])

    start_weather = today - timedelta(days=history_days + 30)
    end_weather = today + timedelta(days=45)
    cursor = start_weather
    weather_rows: list[tuple[Any, ...]] = []
    while cursor <= end_weather:
        wrng = _rng("weather-v2", key, cursor.isoformat())
        day_of_year = cursor.timetuple().tm_yday
        normal_high = 57 + 25 * math.sin(2 * math.pi * (day_of_year - 105) / 365.25)
        high = round(normal_high + wrng.gauss(0, 5.5), 1)
        low = round(high - wrng.uniform(8, 16), 1)
        roll = wrng.random()
        if high < 35 and roll < 0.12:
            precipitation, condition = round(wrng.uniform(2, 18), 1), "Snow"
        elif roll < 0.16:
            precipitation, condition = round(wrng.uniform(9, 34), 1), "Heavy rain"
        elif roll < 0.35:
            precipitation, condition = round(wrng.uniform(1, 9), 1), "Rain"
        elif roll < 0.56:
            precipitation, condition = 0.0, "Cloudy"
        else:
            precipitation, condition = 0.0, "Clear"
        snowfall = round(precipitation * 0.75, 1) if "Snow" in condition else 0.0
        seasonal_uv = max(0.2, 5.6 + 3.6 * math.sin(2 * math.pi * (day_of_year - 80) / 365.25))
        uv_index = round(seasonal_uv * (0.42 if condition in {"Rain", "Heavy rain", "Snow"} else 0.70 if condition == "Cloudy" else 1.0), 1)
        weather_cache[cursor.isoformat()] = (high, low, precipitation, condition)
        weather_rows.append((location_id, cursor.isoformat(), high, low, precipitation, snowfall, uv_index, condition, "demo-weather-v2"))
        cursor += timedelta(days=1)
    conn.executemany(
        "INSERT INTO weather(location_id,date,temp_high,temp_low,precipitation_mm,snowfall_cm,uv_index,condition,source) VALUES(?,?,?,?,?,?,?,?,?)",
        weather_rows,
    )

    catalog = EVENT_CATALOG.get(key) or EVENT_CATALOG["loc-bakery"]
    erng = _rng("events-v2", key)
    cursor = today - timedelta(days=history_days)
    index = 1
    while cursor < today:
        cursor += timedelta(days=erng.randint(7, 16))
        if cursor >= today:
            break
        name, event_type, attendance, relevance = erng.choice(catalog)
        distance = round(erng.uniform(0.15, 11.0), 2)
        start_hour = erng.choice([7, 9, 11, 13, 17, 18, 19, 20])
        event = {
            "attendance": int(attendance * erng.uniform(0.70, 1.30)),
            "distance_miles": distance,
            "relevance": relevance,
            "start_time": f"{start_hour:02d}:00",
            "end_time": f"{min(23, start_hour + erng.choice([2, 3, 5])):02d}:00",
        }
        conn.execute(
            """INSERT INTO events(id,location_id,name,event_type,date,start_time,end_time,distance_miles,
               attendance,relevance,source,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (f"evt-{location_id[4:]}-hist-{index}", location_id, name, event_type, cursor.isoformat(),
             event["start_time"], event["end_time"], distance, event["attendance"], relevance,
             "demo-context-feed", "Past event"),
        )
        event_loads[cursor.isoformat()] = event_loads.get(cursor.isoformat(), 0.0) + event_impact(event, template["open_hour"], template["close_hour"])
        index += 1

    for idx, (offset, name, event_type, start_time, end_time, distance, attendance, relevance) in enumerate(FUTURE_EVENTS, 1):
        target = today + timedelta(days=offset + (0 if key == "loc-burger" else idx % 2))
        event = {"attendance": attendance, "distance_miles": distance, "relevance": relevance, "start_time": start_time, "end_time": end_time}
        conn.execute(
            """INSERT INTO events(id,location_id,name,event_type,date,start_time,end_time,distance_miles,
               attendance,relevance,source,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (f"evt-{location_id[4:]}-future-{idx}", location_id, name, event_type, target.isoformat(),
             start_time, end_time, distance, attendance, relevance, "demo-context-feed", "Upcoming event"),
        )
        event_loads[target.isoformat()] = event_loads.get(target.isoformat(), 0.0) + event_impact(event, template["open_hour"], template["close_hour"])

    # Item sales and the hourly shape of service. In production this same
    # structure is filled idempotently from register orders and webhooks.
    sales_start = today - timedelta(days=history_days)
    sales_end = today - timedelta(days=1)
    menu_rows = conn.execute("SELECT * FROM menu_items WHERE location_id=?", (location_id,)).fetchall()
    profile = {f"item-{location_id[4:]}-{entry['key']}": entry for entry in template["menu"]}
    sales_batch: list[tuple[Any, ...]] = []
    hourly_batch: list[tuple[Any, ...]] = []
    cursor = sales_start
    while cursor <= sales_end:
        day_index = (cursor - sales_start).days
        high, _low, precipitation, _condition = weather_cache[cursor.isoformat()]
        event_load = event_loads.get(cursor.isoformat(), 0.0)
        annual_trend = 0.94 + 0.00016 * day_index
        dow_multiplier = template["day_multipliers"][cursor.weekday()]
        occasion = calendar_occasions(cursor.year).get(cursor)
        occasion_multiplier = 1.0 + (0.10 if occasion and occasion[1] == "occasion" else -0.06 if occasion else 0.0)
        for item in menu_rows:
            fixture = profile[item["id"]]
            srng = _rng("sale-v2", key, f"item-{suffix}-{fixture['key']}", cursor.isoformat())
            temp_delta = (high - 65.0) / 25.0
            rain_indicator = min(1.0, precipitation / 8.0)
            # These coefficients synthesize believable sample history only. The
            # forecasting engine never reads them; on live data every effect is
            # learned from the merchant's own register history.
            learned_truth = (
                1.0
                + float(fixture["temp"]) * temp_delta
                + float(fixture["rain"]) * rain_indicator
                + float(fixture["event"]) * event_load
            )
            seasonal = 1.0 + 0.045 * math.sin(2 * math.pi * (cursor.timetuple().tm_yday - 82) / 365.25)
            noise = max(0.64, min(1.45, srng.gauss(1.0, 0.105)))
            quantity = max(0, int(round(float(item["base_daily_qty"]) * dow_multiplier * annual_trend * seasonal * occasion_multiplier * learned_truth * noise)))
            delivery_share = min(0.72, max(0.06, 0.22 + 0.18 * rain_indicator + srng.gauss(0, 0.035)))
            dine_in_share = max(0.0, min(0.90, 0.68 - 0.13 * rain_indicator + srng.gauss(0, 0.04)))
            sales_batch.append((
                location_id, item["id"], cursor.isoformat(), quantity,
                round(quantity * float(item["price"]), 2), round(dine_in_share, 3), round(delivery_share, 3), 0,
            ))
            for hour, hour_qty in _allocate_hourly(quantity, template["hour_curve"], srng).items():
                hourly_batch.append((location_id, item["id"], cursor.isoformat(), hour, hour_qty,
                                     round(hour_qty * float(item["price"]), 2), "mixed"))
        if len(hourly_batch) >= 5000:
            conn.executemany(
                "INSERT INTO sales(location_id,item_id,date,quantity,revenue,dine_in_share,delivery_share,stockout_minutes) VALUES(?,?,?,?,?,?,?,?)",
                sales_batch,
            )
            conn.executemany(
                "INSERT INTO sales_hourly(location_id,item_id,date,hour,quantity,revenue,channel) VALUES(?,?,?,?,?,?,?)",
                hourly_batch,
            )
            sales_batch.clear()
            hourly_batch.clear()
        cursor += timedelta(days=1)
    if sales_batch:
        conn.executemany(
            "INSERT INTO sales(location_id,item_id,date,quantity,revenue,dine_in_share,delivery_share,stockout_minutes) VALUES(?,?,?,?,?,?,?,?)",
            sales_batch,
        )
    if hourly_batch:
        conn.executemany(
            "INSERT INTO sales_hourly(location_id,item_id,date,hour,quantity,revenue,channel) VALUES(?,?,?,?,?,?,?)",
            hourly_batch,
        )


SAMPLE_TEMPLATES = {
    "bakery": "loc-bakery",
    "cafe": "loc-bakery",
    "coffee": "loc-bakery",
    "deli": "loc-bakery",
    "sandwich": "loc-bakery",
    "pizza": "loc-pizza",
    "burger": "loc-burger",
    "grill": "loc-burger",
    "casual": "loc-burger",
}


def pick_template(concept: str) -> dict[str, Any]:
    """Choose the sample location closest to what this business actually serves."""
    text = (concept or "").lower()
    for word, template_id in SAMPLE_TEMPLATES.items():
        if word in text:
            return next(row for row in LOCATIONS if row["id"] == template_id)
    return LOCATIONS[0]


def seed_workspace(
    conn: sqlite3.Connection,
    organization_id: str,
    concept: str = "",
    name: str | None = None,
    owner_email: str = "",
    history_days: int = 400,
    today: date | None = None,
    city: str = "",
    region: str = "",
    timezone: str = "",
    latitude: float | None = None,
    longitude: float | None = None,
    open_hour: int | None = None,
    close_hour: int | None = None,
) -> str:
    """Give a brand new account one sample location so the product works on day one.

    This is not the same as the sample reset. It adds a single location to one
    organization and touches nothing else, so a new signup never disturbs an
    account that already exists.

    The location carries what the owner typed: their business name, what they
    serve, their city, state, time zone and hours. Only the sales history is
    borrowed from the closest sample template, and the register row is marked
    as sample data so the interface can say so.
    """
    today = today or date.today()
    template = pick_template(concept)
    location_id = f"loc-{uuid.uuid4().hex[:10]}"
    local = dict(template)
    if name:
        local["name"] = str(name).strip()[:120]
    if concept and concept.strip():
        local["concept"] = concept.strip()[:80]
    if city and city.strip():
        from .timezones import resolve
        place = resolve(city)
        local["city"] = str(place.get("city") or city).strip()[:80]
        local["region"] = str(region or place.get("region") or "").strip()[:40]
        local["timezone"] = str(timezone or place["timezone"])
        # A new city never inherits a street address from the sample template.
        local["address"] = ""
        local["postal_code"] = ""
    if region and region.strip():
        local["region"] = region.strip()[:40]
    if timezone and timezone.strip():
        local["timezone"] = timezone.strip()
    if latitude is not None and longitude is not None:
        local["latitude"] = float(latitude)
        local["longitude"] = float(longitude)
    if open_hour is not None and close_hour is not None:
        opens = max(0, min(23, int(open_hour)))
        closes = max(1, min(28, int(close_hour)))
        if closes <= opens:
            closes += 24
        if closes - opens > 24:
            closes = opens + 24
        local["open_hour"] = opens
        local["close_hour"] = closes
        # The sample hour curve follows the owner's hours, so the day shape is
        # theirs and not the template's.
        hours = list(range(opens, closes))
        template_curve = sorted((int(k), float(v)) for k, v in template["hour_curve"].items())
        if hours and template_curve:
            weights = [v for _, v in template_curve]
            local["hour_curve"] = {
                str(hour % 24): weights[min(len(weights) - 1, int(index * len(weights) / len(hours)))]
                for index, hour in enumerate(hours)
            }
    _seed_location(
        conn, local, organization_id, location_id, today, history_days,
        owner_email=owner_email, seed_key=template["id"],
    )
    conn.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('seeded_at',?)", (_utc_now(),))
    conn.commit()
    return location_id


def seed_demo(conn: sqlite3.Connection, today: date | None = None) -> None:
    """Create the deterministic two-year sample dataset."""
    today = today or date.today()
    now = _utc_now()

    # A sample reset intentionally clears local accounts too. Hosted migrations
    # never use this destructive helper.
    for table in [
        "security_events", "recovery_codes", "auth_challenges", "email_verifications", "sessions", "users",
        "cancellation_feedback", "billing_events", "subscriptions", "ai_generations", "item_composition",
        "day_accuracy", "forecast_revisions", "forecast_calls", "pos_orders",
        "email_deliveries", "email_preferences", "forecast_runs",
        "context_daily", "forecast_overrides", "pos_order_lines", "sales_hourly", "sales", "events",
        "weather", "menu_interpretations", "menu_items", "integrations",
        "settings", "locations", "organizations",
    ]:
        try:
            conn.execute(f"DELETE FROM {table}")
        except sqlite3.OperationalError:
            pass  # a table added after this database was created

    conn.execute(
        "INSERT INTO organizations(id,name,plan,created_at) VALUES(?,?,?,?)",
        ("org-demo", "Quantify Demo Group", "signal", now),
    )
    for template in LOCATIONS:
        _seed_location(conn, template, "org-demo", template["id"], today, 730)

    conn.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('seeded_at',?)", (now,))
    conn.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('seed_date',?)", (today.isoformat(),))
    conn.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('product_version','3.0.0')")
    conn.commit()
