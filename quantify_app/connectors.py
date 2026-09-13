from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from .localtime import zone

from .menu_intelligence import upsert_interpretation


SQUARE_API_VERSION = "2026-07-15"
WEATHER_DAILY_FIELDS = "temperature_2m_max,temperature_2m_min,precipitation_sum,snowfall_sum,uv_index_max,weather_code"


def _request_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=payload, method=method)
    request.add_header("Accept", "application/json")
    request.add_header("User-Agent", "Quantify/2.0")
    if payload is not None:
        request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1200]
        raise RuntimeError(f"Provider returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach provider: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("Provider returned an unreadable response") from exc


def _location(conn: sqlite3.Connection, location_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM locations WHERE id=? AND active=1", (location_id,)).fetchone()
    if row is None:
        raise ValueError("Unknown location")
    return row


def _record_integration(
    conn: sqlite3.Connection,
    location_id: str,
    provider: str,
    *,
    status: str = "connected",
    mode: str = "live",
    details: dict[str, Any] | None = None,
) -> str:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        """INSERT INTO integrations(location_id,provider,status,last_sync,mode,details)
           VALUES(?,?,?,?,?,?)
           ON CONFLICT(location_id,provider) DO UPDATE SET
             status=excluded.status,last_sync=excluded.last_sync,mode=excluded.mode,details=excluded.details""",
        (location_id, provider, status, now, mode, json.dumps(details or {})),
    )
    return now


def _condition(code: Any) -> str:
    condition_map = {
        0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Cloudy",
        45: "Fog", 48: "Fog", 51: "Drizzle", 53: "Drizzle", 55: "Drizzle",
        56: "Freezing drizzle", 57: "Freezing drizzle", 61: "Rain", 63: "Rain",
        65: "Heavy rain", 66: "Freezing rain", 67: "Freezing rain", 71: "Snow",
        73: "Snow", 75: "Heavy snow", 77: "Snow grains", 80: "Rain showers",
        81: "Rain showers", 82: "Heavy showers", 85: "Snow showers",
        86: "Heavy snow showers", 95: "Thunderstorm", 96: "Thunderstorm",
        99: "Thunderstorm",
    }
    try:
        return condition_map.get(int(code), "Weather update")
    except (TypeError, ValueError):
        return "Weather update"


def _upsert_weather_payload(
    conn: sqlite3.Connection,
    location_id: str,
    payload: dict[str, Any],
    source: str,
) -> int:
    daily = payload.get("daily") or {}
    dates = daily.get("time") or []
    highs = daily.get("temperature_2m_max") or []
    lows = daily.get("temperature_2m_min") or []
    precipitation = daily.get("precipitation_sum") or []
    snowfall = daily.get("snowfall_sum") or []
    uv_index = daily.get("uv_index_max") or []
    codes = daily.get("weather_code") or daily.get("weathercode") or []
    updated = 0
    for index, day in enumerate(dates):
        high = highs[index] if index < len(highs) else None
        low = lows[index] if index < len(lows) else None
        precip = precipitation[index] if index < len(precipitation) else 0
        snow = snowfall[index] if index < len(snowfall) else 0
        uv = uv_index[index] if index < len(uv_index) else 0
        code = codes[index] if index < len(codes) else None
        if high is None or low is None:
            continue
        conn.execute(
            """INSERT INTO weather(location_id,date,temp_high,temp_low,precipitation_mm,snowfall_cm,uv_index,condition,source)
               VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(location_id,date) DO UPDATE SET
                 temp_high=excluded.temp_high,temp_low=excluded.temp_low,
                 precipitation_mm=excluded.precipitation_mm,snowfall_cm=excluded.snowfall_cm,
                 uv_index=excluded.uv_index,condition=excluded.condition,source=excluded.source""",
            (location_id, str(day), float(high), float(low), float(precip or 0), float(snow or 0), float(uv or 0), _condition(code), source),
        )
        updated += 1
    return updated


def _date_chunks(start: date, end: date, chunk_days: int = 365) -> Iterable[tuple[date, date]]:
    cursor = start
    while cursor <= end:
        chunk_end = min(end, cursor + timedelta(days=chunk_days - 1))
        yield cursor, chunk_end
        cursor = chunk_end + timedelta(days=1)


def refresh_weather(
    conn: sqlite3.Connection,
    location_id: str,
    start: date | None = None,
    days: int = 16,
    backfill_days: int = 730,
) -> dict[str, Any]:
    """Backfill historical observations once, then refresh the operating forecast.

    Historical records are fetched in date-range batches and cached by location/date.
    This intentionally avoids one external request per sale or transaction.
    """
    location = _location(conn, location_id)
    today = date.today()

    min_sale = conn.execute(
        "SELECT MIN(date) AS first_date FROM sales WHERE location_id=?",
        (location_id,),
    ).fetchone()["first_date"]
    history_start = date.fromisoformat(min_sale) if min_sale else today - timedelta(days=max(1, min(backfill_days, 1825)))
    history_start = max(history_start, today - timedelta(days=max(1, min(backfill_days, 1825))))
    history_end = today - timedelta(days=1)

    archive_url = os.getenv("OPEN_METEO_ARCHIVE_URL", "https://archive-api.open-meteo.com/v1/archive")
    forecast_url = os.getenv("OPEN_METEO_BASE_URL", "https://api.open-meteo.com/v1/forecast")
    api_key = os.getenv("OPEN_METEO_API_KEY")
    common = {
        "latitude": location["latitude"],
        "longitude": location["longitude"],
        "daily": WEATHER_DAILY_FIELDS,
        "timezone": location["timezone"],
        "temperature_unit": "fahrenheit",
    }
    if api_key:
        common["apikey"] = api_key

    historical_updated = 0
    if history_start <= history_end:
        for chunk_start, chunk_end in _date_chunks(history_start, history_end):
            params = common | {"start_date": chunk_start.isoformat(), "end_date": chunk_end.isoformat()}
            payload = _request_json(f"{archive_url}?{urllib.parse.urlencode(params)}", timeout=45)
            historical_updated += _upsert_weather_payload(conn, location_id, payload, "open-meteo-history")

    forecast_start = start or today
    forecast_end = forecast_start + timedelta(days=max(1, min(days, 16)) - 1)
    params = common | {"start_date": forecast_start.isoformat(), "end_date": forecast_end.isoformat()}
    forecast_payload = _request_json(f"{forecast_url}?{urllib.parse.urlencode(params)}", timeout=30)
    forecast_updated = _upsert_weather_payload(conn, location_id, forecast_payload, "open-meteo-forecast")

    now = _record_integration(
        conn,
        location_id,
        "weather",
        details={
            "label": "Open-Meteo",
            "historical_days": historical_updated,
            "forecast_days": forecast_updated,
            "history_start": history_start.isoformat(),
            "history_end": history_end.isoformat(),
            "temperature_unit": "fahrenheit",
        },
    )
    conn.commit()
    return {
        "provider": "weather",
        "historical_days": historical_updated,
        "forecast_days": forecast_updated,
        "last_sync": now,
    }


def _distance_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 3958.8
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1 - a)))


def _geohash(latitude: float, longitude: float, precision: int = 7) -> str:
    """Encode a coordinate for Ticketmaster's geoPoint filter without a dependency."""
    alphabet = "0123456789bcdefghjkmnpqrstuvwxyz"
    lat_range = [-90.0, 90.0]
    lon_range = [-180.0, 180.0]
    bits = [16, 8, 4, 2, 1]
    even = True
    bit = 0
    value = 0
    output: list[str] = []
    while len(output) < precision:
        target = lon_range if even else lat_range
        coordinate = longitude if even else latitude
        midpoint = (target[0] + target[1]) / 2
        if coordinate >= midpoint:
            value |= bits[bit]
            target[0] = midpoint
        else:
            target[1] = midpoint
        even = not even
        if bit < 4:
            bit += 1
        else:
            output.append(alphabet[value])
            bit = 0
            value = 0
    return "".join(output)


def _event_local_parts(raw_start: str | None, raw_end: str | None, timezone_name: str) -> tuple[str, str, str]:
    tz = zone(timezone_name)

    def parse(value: str | None, fallback: datetime) -> datetime:
        if not value:
            return fallback
        normalized = value.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return fallback
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=tz)
        return parsed.astimezone(tz)

    fallback = datetime.now(tz).replace(hour=18, minute=0, second=0, microsecond=0)
    starts = parse(raw_start, fallback)
    ends = parse(raw_end, starts + timedelta(hours=3))
    return starts.date().isoformat(), starts.strftime("%H:%M"), ends.strftime("%H:%M")


def _refresh_predicthq_events(
    conn: sqlite3.Connection,
    location: sqlite3.Row,
    start: date,
    end: date,
    radius_miles: int,
) -> tuple[int, str]:
    token = os.getenv("PREDICTHQ_ACCESS_TOKEN")
    if not token:
        raise RuntimeError(EVENTS_NOT_CONNECTED)
    base_url = os.getenv("PREDICTHQ_EVENTS_URL", "https://api.predicthq.com/v1/events/")
    headers = {"Authorization": f"Bearer {token}"}
    limit = 500
    offset = 0
    collected: list[dict[str, Any]] = []
    while True:
        params = {
            "within": f"{radius_miles}mi@{location['latitude']},{location['longitude']}",
            "start.gte": start.isoformat(),
            "start.lte": f"{end.isoformat()}T23:59:59",
            "limit": limit,
            "offset": offset,
            "sort": "start",
        }
        payload = _request_json(f"{base_url}?{urllib.parse.urlencode(params)}", headers=headers, timeout=45)
        results = payload.get("results") or []
        collected.extend(result for result in results if isinstance(result, dict))
        offset += len(results)
        count = int(payload.get("count") or len(collected))
        if not results or offset >= count or offset >= 10000:
            break

    # Replace only provider-owned records in the successfully retrieved interval.
    conn.execute(
        "DELETE FROM events WHERE location_id=? AND source='predicthq' AND date>=? AND date<=?",
        (location["id"], start.isoformat(), end.isoformat()),
    )
    updated = 0
    for raw in collected:
        event_id_raw = str(raw.get("id") or uuid.uuid4().hex[:12])
        event_date, start_time, end_time = _event_local_parts(raw.get("start"), raw.get("end"), location["timezone"])
        coordinates = raw.get("location") or []
        try:
            event_lon = float(coordinates[0])
            event_lat = float(coordinates[1])
            distance = _distance_miles(float(location["latitude"]), float(location["longitude"]), event_lat, event_lon)
        except (TypeError, ValueError, IndexError):
            distance = float(radius_miles) / 2
        rank = float(raw.get("local_rank") or raw.get("rank") or 35)
        attendance_raw = raw.get("phq_attendance") or raw.get("predicted_attendance")
        try:
            attendance = max(0, int(float(attendance_raw)))
        except (TypeError, ValueError):
            attendance = int(max(100, min(50000, 250 + rank * rank * 1.8)))
        relevance = max(0.18, min(1.0, rank / 100))
        entities = raw.get("entities") or []
        venue = next((entity.get("name") for entity in entities if isinstance(entity, dict) and entity.get("type") in {"venue", "place"}), None)
        conn.execute(
            """INSERT INTO events(
                id,location_id,name,event_type,date,start_time,end_time,distance_miles,
                attendance,relevance,source,notes
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,event_type=excluded.event_type,date=excluded.date,
                start_time=excluded.start_time,end_time=excluded.end_time,
                distance_miles=excluded.distance_miles,attendance=excluded.attendance,
                relevance=excluded.relevance,source=excluded.source,notes=excluded.notes""",
            (
                f"evt-predicthq-{event_id_raw}", location["id"], raw.get("title") or "Public event",
                raw.get("category") or "event", event_date, start_time, end_time,
                round(distance, 2), attendance, relevance, "predicthq", venue,
            ),
        )
        updated += 1
    return updated, "PredictHQ"


def _refresh_ticketmaster_events(
    conn: sqlite3.Connection,
    location: sqlite3.Row,
    start: date,
    end: date,
    radius_miles: int,
) -> tuple[int, str]:
    api_key = os.getenv("TICKETMASTER_API_KEY")
    if not api_key:
        raise RuntimeError(EVENTS_NOT_CONNECTED)
    all_events: list[dict[str, Any]] = []
    page = 0
    while page < 5:  # Discovery API deep paging is limited to the first 1,000 results.
        params = {
            "apikey": api_key,
            "geoPoint": _geohash(float(location["latitude"]), float(location["longitude"])),
            "radius": radius_miles,
            "unit": "miles",
            "startDateTime": f"{start.isoformat()}T00:00:00Z",
            "endDateTime": f"{end.isoformat()}T23:59:59Z",
            "size": 200,
            "page": page,
            "sort": "date,asc",
        }
        payload = _request_json(
            f"https://app.ticketmaster.com/discovery/v2/events.json?{urllib.parse.urlencode(params)}",
            timeout=35,
        )
        events = payload.get("_embedded", {}).get("events", [])
        all_events.extend(event for event in events if isinstance(event, dict))
        total_pages = int(payload.get("page", {}).get("totalPages") or 1)
        page += 1
        if not events or page >= total_pages:
            break

    conn.execute(
        "DELETE FROM events WHERE location_id=? AND source='ticketmaster' AND date>=? AND date<=?",
        (location["id"], start.isoformat(), end.isoformat()),
    )
    updated = 0
    for raw in all_events:
        dates = raw.get("dates") or {}
        start_info = dates.get("start") or {}
        event_date = start_info.get("localDate")
        if not event_date:
            continue
        start_time = (start_info.get("localTime") or "18:00:00")[:5]
        venue = (raw.get("_embedded", {}).get("venues") or [{}])[0]
        location_data = venue.get("location") or {}
        try:
            event_lat = float(location_data.get("latitude"))
            event_lon = float(location_data.get("longitude"))
            distance = _distance_miles(float(location["latitude"]), float(location["longitude"]), event_lat, event_lon)
        except (TypeError, ValueError):
            distance = float(radius_miles) / 2
        classification = (raw.get("classifications") or [{}])[0]
        segment = (classification.get("segment") or {}).get("name", "event").lower()
        # Ticketmaster does not consistently expose venue capacity in Discovery results.
        # A conservative proxy is used until a richer provider or manual attendance is available.
        attendance = 1500
        venue_capacity = venue.get("capacity")
        try:
            if venue_capacity is not None:
                attendance = max(100, int(venue_capacity))
        except (TypeError, ValueError):
            pass
        relevance = max(0.25, min(0.9, 0.82 - distance * 0.025))
        event_id = f"evt-ticketmaster-{raw.get('id', uuid.uuid4().hex[:12])}"
        conn.execute(
            """INSERT INTO events(
                id,location_id,name,event_type,date,start_time,end_time,distance_miles,
                attendance,relevance,source,notes
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,event_type=excluded.event_type,date=excluded.date,
                start_time=excluded.start_time,end_time=excluded.end_time,
                distance_miles=excluded.distance_miles,attendance=excluded.attendance,
                relevance=excluded.relevance,source=excluded.source,notes=excluded.notes""",
            (
                event_id, location["id"], raw.get("name", "Public event"), segment,
                event_date, start_time, "23:00", round(distance, 2), attendance,
                relevance, "ticketmaster", venue.get("name"),
            ),
        )
        updated += 1
    return updated, "Ticketmaster Discovery API"


def refresh_events(
    conn: sqlite3.Connection,
    location_id: str,
    start: date | None = None,
    days: int = 60,
    backfill_days: int = 730,
) -> dict[str, Any]:
    location = _location(conn, location_id)
    radius_miles = max(1, min(int(os.getenv("QUANTIFY_EVENT_RADIUS_MILES", "15")), 100))
    today = date.today()
    if os.getenv("PREDICTHQ_ACCESS_TOKEN"):
        min_sale = conn.execute("SELECT MIN(date) AS first_date FROM sales WHERE location_id=?", (location_id,)).fetchone()["first_date"]
        history_start = date.fromisoformat(min_sale) if min_sale else today - timedelta(days=backfill_days)
        event_start = start or max(history_start, today - timedelta(days=max(1, min(backfill_days, 1825))))
        event_end = today + timedelta(days=max(1, min(days, 365)))
        updated, label = _refresh_predicthq_events(conn, location, event_start, event_end, radius_miles)
        coverage = "historical-and-future"
    else:
        # Ticketmaster is a useful low-cost upcoming-events feed. PredictHQ is used when
        # historical event backfill and broader demand-oriented coverage are required.
        event_start = start or today
        event_end = event_start + timedelta(days=max(1, min(days, 60)))
        updated, label = _refresh_ticketmaster_events(conn, location, event_start, event_end, radius_miles)
        coverage = "upcoming-only"

    now = _record_integration(
        conn,
        location_id,
        "events",
        details={
            "label": label,
            "events": updated,
            "start": event_start.isoformat(),
            "end": event_end.isoformat(),
            "radius_miles": radius_miles,
            "coverage": coverage,
            "all_categories": True,
            "hard_radius_limit": False,
            "ranking": "attendance × relevance × distance decay × service overlap; item effects are learned from POS history",
        },
    )
    conn.commit()
    return {
        "provider": "events",
        "updated_events": updated,
        "coverage": coverage,
        "source": label,
        "last_sync": now,
    }


# What the interface is told when a sync is pressed and nothing is connected.
# It names the button, never a variable on the server.
SQUARE_NOT_CONNECTED = "Connect Square in Settings > Location first"
EVENTS_NOT_CONNECTED = "Nearby events are not connected yet"

SQUARE_SETTING_KEYS = ("square_access_token", "square_location_id", "square_environment")


def _square_credentials(conn: sqlite3.Connection | None, location_id: str | None) -> dict[str, str] | None:
    """The Square credentials for one location: what was saved in Settings first, the environment second.

    Every Square call goes through here, so a location connected from the
    screen and one connected from the server's configuration behave the same.
    """
    if conn is not None and location_id:
        rows = conn.execute(
            "SELECT key,value FROM settings WHERE location_id=? AND key IN (?,?,?)",
            (location_id, *SQUARE_SETTING_KEYS),
        ).fetchall()
        stored = {row["key"]: row["value"] for row in rows}
        if stored.get("square_access_token") and stored.get("square_location_id"):
            return {
                "token": stored["square_access_token"],
                "location_id": stored["square_location_id"],
                "environment": (stored.get("square_environment") or "production").lower(),
                "source": "settings",
            }
    token = os.getenv("SQUARE_ACCESS_TOKEN")
    square_location_id = os.getenv("SQUARE_LOCATION_ID")
    if token and square_location_id:
        return {
            "token": token,
            "location_id": square_location_id,
            "environment": os.getenv("SQUARE_ENVIRONMENT", "production").lower(),
            "source": "environment",
        }
    return None


def save_square_credentials(
    conn: sqlite3.Connection,
    location_id: str,
    access_token: str,
    square_location_id: str,
    environment: str = "production",
) -> dict[str, Any]:
    """Keep a location's Square credentials and mark the register as configured.

    No call is made to Square here. The first Sync now proves the token, and
    its own message says what happened, so a bad paste is a one-line fix in
    the same place it was typed.
    """
    access_token = str(access_token or "").strip()
    square_location_id = str(square_location_id or "").strip()
    environment = str(environment or "production").strip().lower() or "production"
    if len(access_token) < 16 or any(char.isspace() for char in access_token):
        raise ValueError("Paste the whole access token from the Square developer dashboard")
    if len(square_location_id) < 4 or any(char.isspace() for char in square_location_id):
        raise ValueError("Enter the location ID exactly as Square shows it")
    if environment not in {"production", "sandbox"}:
        raise ValueError("Environment must be production or sandbox")
    _location(conn, location_id)
    for key, value in (
        ("square_access_token", access_token),
        ("square_location_id", square_location_id),
        ("square_environment", environment),
    ):
        conn.execute(
            """INSERT INTO settings(location_id,key,value) VALUES(?,?,?)
               ON CONFLICT(location_id,key) DO UPDATE SET value=excluded.value""",
            (location_id, key, value),
        )
    existing = conn.execute(
        "SELECT last_sync FROM integrations WHERE location_id=? AND provider='pos'", (location_id,)
    ).fetchone()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        """INSERT INTO integrations(location_id,provider,status,last_sync,mode,details)
           VALUES(?,?,?,?,?,?)
           ON CONFLICT(location_id,provider) DO UPDATE SET
             status=excluded.status,mode=excluded.mode,details=excluded.details""",
        (
            location_id, "pos", "configured", existing["last_sync"] if existing else None, "live",
            json.dumps({"label": "Square", "square_location_id": square_location_id,
                        "environment": environment, "configured_at": now}),
        ),
    )
    conn.commit()
    return {
        "ok": True,
        "status": "configured",
        "register": square_status(conn, location_id),
        "message": "Square is connected. Press Sync now to bring your sales in",
    }


def square_status(conn: sqlite3.Connection, location_id: str) -> dict[str, Any]:
    """What Settings says about the register: connected or not, and on what."""
    credentials = _square_credentials(conn, location_id)
    row = conn.execute(
        "SELECT status,last_sync,mode FROM integrations WHERE location_id=? AND provider='pos'", (location_id,)
    ).fetchone()
    connected = credentials is not None
    return {
        "provider": "Square",
        "connected": connected,
        "mode": "live" if connected else "sample",
        "status": (row["status"] if row else None) if connected else "not-connected",
        "environment": credentials["environment"] if credentials else None,
        "square_location_id": credentials["location_id"] if credentials else None,
        "source": credentials["source"] if credentials else None,
        "last_sync": row["last_sync"] if row else None,
    }


def _square_context(conn: sqlite3.Connection | None = None, location_id: str | None = None) -> tuple[str, dict[str, str], str]:
    credentials = _square_credentials(conn, location_id)
    if credentials is None:
        raise RuntimeError(SQUARE_NOT_CONNECTED)
    base = "https://connect.squareupsandbox.com" if credentials["environment"] == "sandbox" else "https://connect.squareup.com"
    headers = {
        "Authorization": f"Bearer {credentials['token']}",
        "Square-Version": os.getenv("SQUARE_VERSION", SQUARE_API_VERSION),
    }
    return base, headers, credentials["location_id"]


def _square_item_id(variation_id: str) -> str:
    return f"sq-{hashlib.sha1(variation_id.encode('utf-8')).hexdigest()[:18]}"


def _sync_square_catalog(conn: sqlite3.Connection, location_id: str, base: str, headers: dict[str, str]) -> dict[str, int]:
    cursor: str | None = None
    objects: list[dict[str, Any]] = []
    while True:
        params: dict[str, Any] = {"types": "ITEM,CATEGORY"}
        if cursor:
            params["cursor"] = cursor
        payload = _request_json(f"{base}/v2/catalog/list?{urllib.parse.urlencode(params)}", headers=headers, timeout=35)
        objects.extend(obj for obj in payload.get("objects", []) if isinstance(obj, dict))
        cursor = payload.get("cursor")
        if not cursor:
            break

    categories = {
        obj["id"]: (obj.get("category_data") or {}).get("name", "Imported")
        for obj in objects if obj.get("type") == "CATEGORY" and obj.get("id")
    }
    imported = 0
    updated = 0
    for obj in objects:
        if obj.get("type") != "ITEM":
            continue
        item_data = obj.get("item_data") or {}
        item_name = str(item_data.get("name") or "Square item").strip()
        category_id = item_data.get("category_id")
        if not category_id:
            category_refs = item_data.get("categories") or []
            if category_refs and isinstance(category_refs[0], dict):
                category_id = category_refs[0].get("id")
        category = categories.get(category_id, "Imported")
        variations = item_data.get("variations") or []
        for variation in variations:
            variation_id = variation.get("id")
            if not variation_id:
                continue
            variation_data = variation.get("item_variation_data") or {}
            variation_name = str(variation_data.get("name") or "").strip()
            display_name = item_name
            if len(variations) > 1 and variation_name and variation_name.lower() not in {"regular", "standard"}:
                display_name = f"{item_name}, {variation_name}"
            money = variation_data.get("price_money") or {}
            price = float(money.get("amount") or 0) / 100
            existing = conn.execute(
                "SELECT id FROM menu_items WHERE location_id=? AND pos_item_id=?",
                (location_id, variation_id),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE menu_items SET name=?,category=?,price=?,active=1 WHERE id=?",
                    (display_name, category, price, existing["id"]),
                )
                updated += 1
            else:
                conn.execute(
                    """INSERT INTO menu_items(
                        id,location_id,pos_item_id,name,category,price,base_daily_qty,active
                    ) VALUES(?,?,?,?,?,?,1,1)""",
                    (_square_item_id(variation_id), location_id, variation_id, display_name, category, price),
                )
                imported += 1
            saved = conn.execute(
                "SELECT id FROM menu_items WHERE location_id=? AND pos_item_id=?",
                (location_id, variation_id),
            ).fetchone()
            if saved:
                upsert_interpretation(conn, saved["id"], display_name, category)
    return {"catalog_imported": imported, "catalog_updated": updated}


def _parse_square_local_parts(value: str, timezone_name: str) -> tuple[str, int]:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        local = parsed.astimezone(zone(timezone_name))
        return local.date().isoformat(), local.hour
    except (ValueError, TypeError):
        return str(value)[:10], 12


def _square_channel(order: dict[str, Any]) -> str:
    fulfillment_types = {
        str((fulfillment or {}).get("type") or "").upper()
        for fulfillment in (order.get("fulfillments") or [])
        if isinstance(fulfillment, dict)
    }
    if "DELIVERY" in fulfillment_types:
        return "delivery"
    if "PICKUP" in fulfillment_types or "SHIPMENT" in fulfillment_types:
        return "pickup"
    source_name = str((order.get("source") or {}).get("name") or "").lower()
    if "delivery" in source_name:
        return "delivery"
    if "online" in source_name or "pickup" in source_name:
        return "pickup"
    return "dine_in"


def _rebuild_sales_pairs(
    conn: sqlite3.Connection,
    location_id: str,
    pairs: set[tuple[str, str]],
) -> None:
    for item_id, sale_date in pairs:
        total = conn.execute(
            """SELECT SUM(quantity) AS quantity,SUM(revenue) AS revenue,
                      SUM(CASE WHEN channel='delivery' THEN quantity ELSE 0 END) AS delivery,
                      SUM(CASE WHEN channel='dine_in' THEN quantity ELSE 0 END) AS dine_in
               FROM pos_order_lines
               WHERE location_id=? AND item_id=? AND sale_date=? AND order_state='COMPLETED'""",
            (location_id, item_id, sale_date),
        ).fetchone()
        quantity = float(total["quantity"] or 0)
        if quantity <= 0:
            conn.execute("DELETE FROM sales WHERE location_id=? AND item_id=? AND date=?", (location_id, item_id, sale_date))
            conn.execute("DELETE FROM sales_hourly WHERE location_id=? AND item_id=? AND date=?", (location_id, item_id, sale_date))
            continue
        conn.execute(
            """INSERT INTO sales(location_id,item_id,date,quantity,revenue,dine_in_share,delivery_share,stockout_minutes)
               VALUES(?,?,?,?,?,?,?,0)
               ON CONFLICT(location_id,item_id,date) DO UPDATE SET
                 quantity=excluded.quantity,revenue=excluded.revenue,
                 dine_in_share=excluded.dine_in_share,delivery_share=excluded.delivery_share""",
            (
                location_id, item_id, sale_date, int(round(quantity)), round(float(total["revenue"] or 0), 2),
                round(float(total["dine_in"] or 0) / quantity, 4),
                round(float(total["delivery"] or 0) / quantity, 4),
            ),
        )
        conn.execute("DELETE FROM sales_hourly WHERE location_id=? AND item_id=? AND date=?", (location_id, item_id, sale_date))
        hourly = conn.execute(
            """SELECT sale_hour,channel,SUM(quantity) AS quantity,SUM(revenue) AS revenue
               FROM pos_order_lines
               WHERE location_id=? AND item_id=? AND sale_date=? AND order_state='COMPLETED'
               GROUP BY sale_hour,channel""",
            (location_id, item_id, sale_date),
        ).fetchall()
        conn.executemany(
            "INSERT INTO sales_hourly(location_id,item_id,date,hour,quantity,revenue,channel) VALUES(?,?,?,?,?,?,?)",
            [(location_id, item_id, sale_date, int(row["sale_hour"]), float(row["quantity"]), round(float(row["revenue"]), 2), row["channel"]) for row in hourly],
        )


def ingest_square_orders(
    conn: sqlite3.Connection,
    location_id: str,
    orders: Iterable[dict[str, Any]],
) -> dict[str, int]:
    """Idempotently normalize Square orders and rebuild only affected item-days."""
    location = _location(conn, location_id)
    affected: set[tuple[str, str]] = set()
    orders_seen = 0
    lines_written = 0
    unmapped = 0
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for order in orders:
        if not isinstance(order, dict):
            continue
        provider_order_id = str(order.get("id") or "").strip()
        if not provider_order_id:
            continue
        orders_seen += 1
        prior = conn.execute(
            "SELECT item_id,sale_date FROM pos_order_lines WHERE provider='square' AND location_id=? AND provider_order_id=?",
            (location_id, provider_order_id),
        ).fetchall()
        affected.update((row["item_id"], row["sale_date"]) for row in prior)
        conn.execute(
            "DELETE FROM pos_order_lines WHERE provider='square' AND location_id=? AND provider_order_id=?",
            (location_id, provider_order_id),
        )
        state = str(order.get("state") or "COMPLETED").upper()
        if state != "COMPLETED":
            continue
        occurred_at = order.get("closed_at") or order.get("updated_at") or order.get("created_at")
        if not occurred_at:
            continue
        sale_date, sale_hour = _parse_square_local_parts(str(occurred_at), location["timezone"])
        channel = _square_channel(order)
        for index, line in enumerate(order.get("line_items") or []):
            if not isinstance(line, dict):
                continue
            variation_id = str(line.get("catalog_object_id") or "").strip()
            if not variation_id:
                unmapped += 1
                continue
            item = conn.execute(
                "SELECT id FROM menu_items WHERE location_id=? AND pos_item_id=?",
                (location_id, variation_id),
            ).fetchone()
            if item is None:
                unmapped += 1
                continue
            try:
                quantity = float(line.get("quantity") or 0)
            except (TypeError, ValueError):
                quantity = 0.0
            if quantity <= 0:
                continue
            money = line.get("total_money") or line.get("gross_sales_money") or {}
            revenue = float(money.get("amount") or 0) / 100
            line_id = str(line.get("uid") or line.get("id") or f"{index}-{variation_id}")
            digest = hashlib.sha256(json.dumps(line, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
            conn.execute(
                """INSERT INTO pos_order_lines(provider,location_id,provider_order_id,provider_line_id,item_id,
                   sale_date,sale_hour,quantity,revenue,channel,order_state,payload_hash,updated_at)
                   VALUES('square',?,?,?,?,?,?,?,?,?,?,?,?)""",
                (location_id, provider_order_id, line_id, item["id"], sale_date, sale_hour,
                 quantity, round(revenue, 2), channel, state, digest, now),
            )
            affected.add((item["id"], sale_date))
            lines_written += 1
    _rebuild_sales_pairs(conn, location_id, affected)
    return {
        "orders_seen": orders_seen,
        "lines_written": lines_written,
        "unmapped_lines": unmapped,
        "item_days_rebuilt": len(affected),
    }


def verify_square_webhook_signature(
    notification_url: str,
    raw_body: bytes,
    signature: str | None,
    signature_key: str,
) -> bool:
    """Verify Square's HMAC-SHA256 webhook signature over URL + raw request body."""
    if not signature or not signature_key or not notification_url:
        return False
    digest = hmac.new(signature_key.encode("utf-8"), notification_url.encode("utf-8") + raw_body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode("ascii")
    return hmac.compare_digest(expected, signature.strip())


def _fetch_square_order(conn: sqlite3.Connection, location_id: str, order_id: str) -> dict[str, Any]:
    base, headers, _ = _square_context(conn, location_id)
    payload = _request_json(f"{base}/v2/orders/{urllib.parse.quote(order_id)}", headers=headers, timeout=30)
    order = payload.get("order")
    if not isinstance(order, dict):
        raise RuntimeError("Square did not return the referenced order")
    return order


def process_square_webhook(
    conn: sqlite3.Connection,
    location_id: str,
    raw_body: bytes,
    signature: str | None,
    notification_url: str,
) -> dict[str, Any]:
    key = os.getenv("SQUARE_WEBHOOK_SIGNATURE_KEY")
    if not key:
        raise RuntimeError("Square live updates are not switched on for this server")
    if not verify_square_webhook_signature(notification_url, raw_body, signature, key):
        raise PermissionError("Square webhook signature is not valid")
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Webhook body is not valid JSON") from exc
    event_type = str(payload.get("type") or "")
    if event_type not in {"order.created", "order.updated", "order.fulfillment.updated"}:
        return {"accepted": True, "ignored": True, "event_type": event_type}
    obj = ((payload.get("data") or {}).get("object") or {})
    order = obj.get("order") if isinstance(obj, dict) else None
    if not isinstance(order, dict):
        detail = obj.get("order_updated") or obj.get("order_created") or obj.get("order_fulfillment_updated") or {}
        order_id = detail.get("order_id") if isinstance(detail, dict) else None
        if not order_id:
            raise ValueError("Square webhook did not include an order identifier")
        order = _fetch_square_order(conn, location_id, str(order_id))
    counts = ingest_square_orders(conn, location_id, [order])
    now = _record_integration(
        conn,
        location_id,
        "pos",
        details={"label": "Square", "webhook": True, "last_event_type": event_type, **counts},
    )
    conn.commit()
    return {"accepted": True, "event_type": event_type, **counts, "last_sync": now}


def sync_square_orders(conn: sqlite3.Connection, location_id: str, days: int = 1095) -> dict[str, Any]:
    location = _location(conn, location_id)
    base, headers, square_location_id = _square_context(conn, location_id)
    catalog_counts = _sync_square_catalog(conn, location_id, base, headers)

    requested_days = max(1, min(days, 1095))
    start_at = datetime.now(timezone.utc) - timedelta(days=requested_days)
    body: dict[str, Any] = {
        "location_ids": [square_location_id],
        "query": {
            "filter": {
                "date_time_filter": {"closed_at": {"start_at": start_at.isoformat().replace("+00:00", "Z")}},
                "state_filter": {"states": ["COMPLETED"]},
            },
            "sort": {"sort_field": "CLOSED_AT", "sort_order": "ASC"},
        },
        "limit": 500,
    }
    cursor: str | None = None
    pages = 0
    orders: list[dict[str, Any]] = []
    max_pages = max(1, min(int(os.getenv("SQUARE_MAX_PAGES", "500")), 1000))
    while True:
        if cursor:
            body["cursor"] = cursor
        else:
            body.pop("cursor", None)
        payload = _request_json(f"{base}/v2/orders/search", method="POST", headers=headers, body=body, timeout=45)
        pages += 1
        orders.extend(order for order in payload.get("orders", []) if isinstance(order, dict))
        cursor = payload.get("cursor")
        if not cursor or pages >= max_pages:
            break

    counts = ingest_square_orders(conn, location_id, orders)
    details = {
        "label": "Square",
        "square_location_id": square_location_id,
        "history_days_requested": requested_days,
        "history_coverage_note": "Actual availability depends on the merchant account and provider retention.",
        "pages": pages,
        "webhook_ready": bool(os.getenv("SQUARE_WEBHOOK_SIGNATURE_KEY")),
        **counts,
        **catalog_counts,
    }
    now = _record_integration(conn, location_id, "pos", details=details)
    conn.commit()
    return {"provider": "pos", **details, "last_sync": now}


def provider_readiness(conn: sqlite3.Connection | None = None, location_id: str | None = None) -> list[dict[str, Any]]:
    return [
        {
            "provider": "Square",
            "status": "configured" if _square_credentials(conn, location_id) else "credentials-required",
            "history": "Up to three years, depending on what the provider kept.",
            "live_updates": "Webhook ready" if os.getenv("SQUARE_WEBHOOK_SIGNATURE_KEY") else "Webhook signature key required",
        },
        {
            "provider": "Toast",
            "status": "partner-approval-required",
            "history": "Available after restaurant authorization and Toast partner access.",
            "live_updates": "order_updated webhook after approved integration setup",
        },
        {
            "provider": "Clover",
            "status": "developer-app-required",
            "history": "Available after merchant OAuth authorization.",
            "live_updates": "merchant webhook after app installation",
        },
    ]
