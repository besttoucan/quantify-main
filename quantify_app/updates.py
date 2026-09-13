"""Evidence-backed operating notes, with per-person read and alert history.

The optional writing service ranks supplied observations. It cannot add a
customer, number, claim, deadline, or action that is absent from those facts.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from . import ai, supply
from .intelligence import daily_brief
from .localtime import zone


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS operating_updates (
            id TEXT PRIMARY KEY,
            location_id TEXT NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
            kind TEXT NOT NULL, severity TEXT NOT NULL,
            title TEXT NOT NULL, body TEXT NOT NULL, evidence TEXT NOT NULL,
            action_json TEXT NOT NULL, created_at TEXT NOT NULL,
            starts_at TEXT NOT NULL, expires_at TEXT NOT NULL,
            resolved_at TEXT, rank INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_operating_updates_location
            ON operating_updates(location_id, created_at DESC);
        CREATE TABLE IF NOT EXISTS operating_update_receipts (
            update_id TEXT NOT NULL REFERENCES operating_updates(id) ON DELETE CASCADE,
            user_id TEXT NOT NULL, seen_at TEXT, read_at TEXT,
            PRIMARY KEY(update_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS operating_update_checks (
            location_id TEXT PRIMARY KEY REFERENCES locations(id) ON DELETE CASCADE,
            checked_at TEXT NOT NULL, review TEXT NOT NULL DEFAULT 'record'
        );
    """)


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _local(conn: sqlite3.Connection, location_id: str, now: datetime | None) -> datetime:
    row = conn.execute("SELECT timezone FROM locations WHERE id=?", (location_id,)).fetchone()
    if row is None:
        raise ValueError("That location could not be found")
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(zone(row["timezone"]))


def _day_end(now: datetime) -> datetime:
    return datetime.combine(now.date() + timedelta(days=1), time.min, now.tzinfo)


def _short_date(value: str) -> str:
    try:
        day = date.fromisoformat(str(value)[:10])
        return f"{day.strftime('%b')} {day.day}"
    except (TypeError, ValueError):
        return "an earlier date"


def _clock(hour: int) -> str:
    return f"{hour % 12 or 12} {'AM' if hour % 24 < 12 else 'PM'}"


def _note(key: str, kind: str, severity: str, title: str, body: str,
          evidence: str, now: datetime, *, action: dict | None = None,
          starts: datetime | None = None, expires: datetime | None = None) -> dict[str, Any]:
    return {"key": key, "kind": kind, "severity": severity, "title": title, "body": body,
            "evidence": evidence, "action": action or {"view": "today", "label": "Open today"},
            "starts_at": _utc(starts or now.replace(hour=0, minute=0, second=0, microsecond=0)),
            "expires_at": _utc(expires or _day_end(now))}


def time_patterns(conn: sqlite3.Connection, location_id: str, now: datetime) -> list[dict]:
    """Aggregate repeat item/time demand; there is no customer identity inference."""
    first = (now.date() - timedelta(days=56)).isoformat()
    today = now.date().isoformat()
    days = {row["date"] for row in conn.execute(
        "SELECT DISTINCT date FROM sales WHERE location_id=? AND date>=? AND date<? AND quantity>0",
        (location_id, first, today),
    ) if date.fromisoformat(row["date"]).weekday() == now.weekday()}
    if len(days) < 4:
        return []
    buckets: dict[tuple[str, int], dict] = {}
    totals: dict[str, float] = defaultdict(float)
    rows = conn.execute("""SELECT h.item_id, m.name, h.date, h.hour, SUM(h.quantity) AS quantity
        FROM sales_hourly h JOIN menu_items m ON m.id=h.item_id AND m.location_id=h.location_id
        WHERE h.location_id=? AND h.date>=? AND h.date<? AND m.active=1
        GROUP BY h.item_id,h.date,h.hour""", (location_id, first, today)).fetchall()
    for row in rows:
        if row["date"] not in days or not 0 <= row["hour"] < 24:
            continue
        quantity = max(0, float(row["quantity"] or 0))
        totals[row["item_id"]] += quantity
        if quantity <= 0:
            continue
        key = row["item_id"], (row["hour"] // 2) * 2
        entry = buckets.setdefault(key, {"name": row["name"], "quantity": 0, "days": set()})
        entry["quantity"] += quantity
        entry["days"].add(row["date"])
    candidates = []
    for (item_id, hour), entry in buckets.items():
        average = round(entry["quantity"] / len(days))
        share = entry["quantity"] / max(1, totals[item_id])
        if len(entry["days"]) < len(days) * .8 or average < 3 or share < .35:
            continue
        end = datetime.combine(now.date(), time.min, now.tzinfo) + timedelta(hours=hour + 2)
        if end <= now:
            continue
        window = f"{_clock(hour)} to {_clock(hour + 2)}"
        evidence = (f"Sold in this window on {len(entry['days'])} of the last {len(days)} "
                    f"{now.strftime('%A')}s with sales, from {_short_date(min(days))} to {_short_date(max(days))}.")
        candidates.append(_note(f"pattern:{item_id}:{today}:{hour}", "pattern", "notice",
            f"{entry['name']} often sells at {window}",
            f"About {average} sell in this window, {round(share * 100)}% of this item's sales on those days. "
            "Check what is ready before that time.", evidence, now,
            action={"view": "today", "item_id": item_id, "label": "See item"}, expires=end))
    return sorted(candidates, key=lambda row: row["expires_at"])[:3]


def observations(conn: sqlite3.Connection, location_id: str, brief: dict, stock: dict,
                 now: datetime) -> list[dict]:
    day = now.date().isoformat()
    if brief.get("no_history"):
        return [_note("no-sales", "setup", "notice", "Your first updates start with sales",
            "Connect the register or add your menu. Item and time patterns appear once there is enough history.",
            "No sales are recorded for this location yet.", now,
            action={"view": "settings", "tab": "location", "label": "Open location settings"})]
    notes = []
    latest = str(brief.get("data_health", {}).get("latest_sale_date") or "")[:10]
    fresh = bool(latest and latest >= (now.date() - timedelta(days=1)).isoformat())
    if not fresh:
        notes.append(_note(f"register:{latest or 'missing'}", "register", "important",
            "Check the register connection", f"The latest sales are from {_short_date(latest)}. "
            "Refresh the register before relying on today's pace.",
            f"Last recorded sales: {_short_date(latest)}.", now,
            action={"view": "settings", "tab": "location", "label": "Check register"}))
    for line in (stock.get("lines") or [])[:3]:
        cover = line.get("days_of_cover")
        if cover is None or float(cover) > 2:
            continue
        name = str(line.get("name") or "An ingredient")
        unit = str(line.get("unit") or "units")
        count = float(line.get("on_hand") or 0)
        when = str(line.get("runs_out_label") or _short_date(line.get("runs_out_on", "")))
        order = "Choose a supplier on the Order page." if line.get("no_supplier") else f"Order {line.get('order_by_label') or 'now'}."
        notes.append(_note(f"stock:{name}:{line.get('runs_out_on')}:{line.get('counted_at')}", "stock",
            "important" if float(cover) <= 1 else "notice", f"{name} may run out {when}",
            f"{count:g} {unit} counted on hand. {order}",
            f"Based on the count from {_short_date(line.get('counted_at', ''))} and the current buying list.",
            now, action={"view": "ordering", "label": "Open order"}))
    summary = brief.get("summary") or {}
    expected = float(summary.get("expected_revenue") or 0)
    normal = float(summary.get("baseline_revenue") or 0)
    change = (expected / normal - 1) * 100 if normal else 0
    important = fresh and abs(change) >= 25
    title = "A busier day is expected" if change >= 15 else "A quieter day is expected" if change <= -15 else "Today's outlook"
    notes.append(_note(f"outlook:{day}", "outlook", "important" if important else "notice", title,
        f"${expected:,.0f} expected against ${normal:,.0f} on a normal {now.strftime('%A')}. "
        "Review the Make list before service.", "Compared with this location's own sales history.", now))
    if fresh:
        notes.extend(time_patterns(conn, location_id, now))
    return notes


def save_observations(conn: sqlite3.Connection, location_id: str, notes: list[dict],
                      now: datetime, review: str = "record") -> None:
    ensure_tables(conn)
    active_ids = []
    for rank, note in enumerate(notes):
        note_id = hashlib.sha256(f"{location_id}:{note['key']}:{note['severity']}".encode()).hexdigest()[:24]
        active_ids.append(note_id)
        conn.execute("""INSERT INTO operating_updates
            (id,location_id,kind,severity,title,body,evidence,action_json,created_at,starts_at,expires_at,rank)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
            severity=excluded.severity,title=excluded.title,body=excluded.body,evidence=excluded.evidence,
            action_json=excluded.action_json,starts_at=excluded.starts_at,expires_at=excluded.expires_at,
            resolved_at=NULL,rank=excluded.rank""",
            (note_id, location_id, note["kind"], note["severity"], note["title"], note["body"],
             note["evidence"], json.dumps(note["action"]), _utc(now), note["starts_at"], note["expires_at"], rank))
    query = "UPDATE operating_updates SET resolved_at=? WHERE location_id=? AND resolved_at IS NULL"
    args: list[Any] = [_utc(now), location_id]
    if active_ids:
        query += " AND id NOT IN (" + ",".join("?" for _ in active_ids) + ")"
        args.extend(active_ids)
    conn.execute(query, args)
    conn.execute("""INSERT INTO operating_update_checks VALUES(?,?,?)
        ON CONFLICT(location_id) DO UPDATE SET checked_at=excluded.checked_at,review=excluded.review""",
        (location_id, _utc(now), review))
    conn.commit()


def refresh(conn: sqlite3.Connection, location_id: str, *, force: bool = False,
            now: datetime | None = None) -> None:
    now = _local(conn, location_id, now)
    ensure_tables(conn)
    last = conn.execute("SELECT checked_at FROM operating_update_checks WHERE location_id=?", (location_id,)).fetchone()
    if not force and last and datetime.fromisoformat(last["checked_at"]) > now - timedelta(minutes=5):
        return
    key = f"operating-updates:{location_id}"
    if not ai.claim_inflight(key):
        return
    try:
        brief = daily_brief(conn, location_id, now.date(), week_days=1, refresh=force)
        stock = supply.attention(conn, location_id, now)
        notes = observations(conn, location_id, brief, stock, now)
        review = "record"
        if force and notes and ai.available():
            ids = [str(index) for index in range(len(notes))]
            schema = {"type": "object", "properties": {"order": {"type": "array", "items": {"type": "string", "enum": ids}}},
                      "required": ["order"], "additionalProperties": False}
            result = ai.generate(conn, "operating_updates", location_id,
                {"instruction": "Rank these supplied observations for a counter assistant. Time-critical problems first. Return only their IDs; do not add claims.",
                 "observations": [{"id": str(i), **n} for i, n in enumerate(notes)]},
                schema, lambda _: {"order": ids}, force=True)
            order = list(dict.fromkeys([value for value in result.get("order", []) if value in ids] + ids))
            notes = [notes[int(value)] for value in order]
            review = "assisted" if result.get("_writer") != "local" else "record"
        save_observations(conn, location_id, notes, now, review)
    finally:
        ai.release_inflight(key)


def feed(conn: sqlite3.Connection, location_id: str, user_id: str,
         now: datetime | None = None) -> dict:
    now = _local(conn, location_id, now)
    ensure_tables(conn)
    rows = conn.execute("""SELECT n.*,r.seen_at,r.read_at FROM operating_updates n
        LEFT JOIN operating_update_receipts r ON r.update_id=n.id AND r.user_id=?
        WHERE n.location_id=? ORDER BY n.created_at DESC,n.rank LIMIT 100""", (user_id, location_id)).fetchall()
    active, earlier = [], []
    stamp = _utc(now)
    for row in rows:
        note = dict(row)
        note["action"] = json.loads(note.pop("action_json"))
        note["state"] = "resolved" if note["resolved_at"] else "expired" if note["expires_at"] <= stamp else "scheduled" if note["starts_at"] > stamp else "active"
        note["unread"] = not bool(note.pop("read_at"))
        (active if note["state"] in {"active", "scheduled"} else earlier).append(note)
    active.sort(key=lambda n: (n["severity"] != "important", n["rank"]))
    alerts = [n for n in active if n["state"] == "active" and n["severity"] == "important" and not n["seen_at"] and n["unread"]]
    check = conn.execute("SELECT checked_at FROM operating_update_checks WHERE location_id=?", (location_id,)).fetchone()
    return {"location_id": location_id, "checked_at": check["checked_at"] if check else None,
            "notes": active, "earlier": earlier[:20], "unread_count": sum(n["unread"] for n in active),
            "notification": alerts[0] if alerts else None}


def acknowledge(conn: sqlite3.Connection, location_id: str, user_id: str,
                ids: list[str], *, read: bool = False, now: datetime | None = None) -> None:
    ensure_tables(conn)
    if not isinstance(ids, list) or len(ids) > 100 or any(not isinstance(v, str) for v in ids):
        raise ValueError("Choose the updates to mark as read")
    stamp = _utc(_local(conn, location_id, now))
    for note_id in ids:
        if not conn.execute("SELECT 1 FROM operating_updates WHERE id=? AND location_id=?", (note_id, location_id)).fetchone():
            raise ValueError("That update is not on this location")
    for note_id in ids:
        conn.execute("""INSERT INTO operating_update_receipts VALUES(?,?,?,?)
            ON CONFLICT(update_id,user_id) DO UPDATE SET seen_at=excluded.seen_at,
            read_at=COALESCE(excluded.read_at,operating_update_receipts.read_at)""",
            (note_id, user_id, stamp, stamp if read else None))
    conn.commit()
