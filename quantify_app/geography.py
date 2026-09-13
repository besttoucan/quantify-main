"""Resolve named US places without borrowing a sample restaurant's coordinates.

Census points represent places, not street addresses or tax jurisdiction checks.
An exact state/name match is required; ambiguous and unsupported places stay
unknown. Provider/user coordinates are usable only with explicit provenance.
"""
from __future__ import annotations

from functools import lru_cache
import json
import math
from pathlib import Path
import re
import unicodedata
from typing import Any

SOURCE = "https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.2025.html"
REVIEWED = "2026-09-13"


def _name(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode().lower()
    value = re.sub(r"\s+(city and borough|unified government|municipality|city|town|village|borough|cdp)$", "", value)
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


@lru_cache(maxsize=1)
def _places() -> dict[tuple[str, str], list[list[Any]]]:
    rows = json.loads(Path(__file__).with_name("reference").joinpath("us_places_2025.json").read_text(encoding="utf-8"))
    index: dict[tuple[str, str], list[list[Any]]] = {}
    for row in rows:
        index.setdefault((row[0], _name(row[1])), []).append(row)
    return index


def resolve_place(city: str, region: str) -> dict[str, Any]:
    """An approximate place point, never an invented street location."""
    from .timezones import STATES
    state = str(region or "").strip().upper()
    named = STATES.get(str(region or "").strip().lower())
    if named:
        state = named[0]
    key = _name(city)
    if state == "NY" and key in {"nyc", "new york city", "manhattan", "brooklyn", "queens", "bronx", "staten island"}:
        key = "new york"
    if key.startswith("saint "):
        key = "st " + key[6:]
    matches = _places().get((state, key), [])
    if not matches:
        matches = _places().get((state, key.replace("st ", "saint ", 1)), [])
    if len(matches) != 1:
        return {"status": "unverified", "latitude": None, "longitude": None, "source": "", "key": "", "label": "Location needs confirmation", "reviewed": REVIEWED}
    row = matches[0]
    return {"status": "city", "latitude": row[3], "longitude": row[4], "source": SOURCE,
            "key": f"census-2025:{row[2]}", "label": f"Approximate location for {row[1]}, {state}", "reviewed": REVIEWED}


def for_location(location: Any) -> dict[str, Any]:
    row = dict(location)
    status, source = row.get("geography_status"), str(row.get("geography_source") or "")
    if status in {"provider", "owner"} and source:
        try:
            lat, lon = float(row["latitude"]), float(row["longitude"])
            if math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180 and (lat or lon):
                return {"status": status, "latitude": lat, "longitude": lon, "source": source,
                        "key": f"{status}:{lat:.6f},{lon:.6f}", "label": "Coordinates supplied for this location", "reviewed": row.get("geography_reviewed", "")}
        except (TypeError, ValueError, KeyError):
            pass
    return resolve_place(str(row.get("city") or ""), str(row.get("region") or ""))


def external_location(location: Any) -> dict[str, Any]:
    row = dict(location)
    geo = for_location(row)
    if geo["status"] == "unverified":
        raise ValueError("Confirm the city and state before connecting weather or nearby events")
    return row | {"latitude": geo["latitude"], "longitude": geo["longitude"], "geography_key": geo["key"]}


def repair_locations(conn: Any) -> None:
    """Re-resolve legacy copied points; keep their original values for review."""
    for location in conn.execute("SELECT * FROM locations").fetchall():
        geo = for_location(location)
        if geo["status"] == "unverified":
            continue
        if location["geography_key"] == geo["key"]:
            continue
        previous = json.dumps({"latitude": location["latitude"], "longitude": location["longitude"], "city": location["city"], "region": location["region"]})
        conn.execute("INSERT OR IGNORE INTO settings(location_id,key,value) VALUES(?, 'geography_previous_coordinates', ?)", (location["id"], previous))
        conn.execute("UPDATE locations SET latitude=?,longitude=?,geography_status=?,geography_source=?,geography_key=? WHERE id=?",
                     (geo["latitude"], geo["longitude"], geo["status"], geo["source"], geo["key"], location["id"]))
