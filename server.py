from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import mimetypes
import os
import sqlite3
import threading
import time
import traceback
import uuid
import webbrowser
from datetime import date, datetime, timedelta, timezone
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from quantify_app import ai, billing, costs, intraday, localtime, ordering, supply, timezones, transactions
from quantify_app.auth import (
    EMAIL_CODE_MINUTES,
    auth_state,
    begin_login,
    change_password,
    clear_cookie_header,
    complete_login,
    complete_password_reset,
    confirm_email,
    cookie_header,
    create_owner,
    create_session,
    disable_totp,
    enable_totp,
    format_secret,
    issue_recovery_codes,
    onboarding_required,
    otpauth_uri,
    revoke_session,
    session_from_token,
    start_email_verification,
    start_password_reset,
)
from quantify_app.connectors import (
    process_square_webhook,
    provider_readiness,
    refresh_events,
    refresh_weather,
    save_square_credentials,
    square_status,
    sync_square_orders,
)
from quantify_app.database import connect, initialize
from quantify_app.email_brief import (
    build_email,
    deliver_brief,
    mail_provider,
    preferences as email_preferences,
    send_due_briefs,
    send_password_reset_code,
    send_verification_code,
    update_preferences,
)
from quantify_app.explain import (
    build_day_payload,
    local_composition,
    local_day_narrative,
    local_day_review,
)
from quantify_app.intelligence import (
    clear_override,
    daily_brief,
    forecast_range,
    performance,
    set_override,
)
from quantify_app.menu_intelligence import (
    menu_intelligence_view,
    parse_menu_text,
    upsert_interpretation,
)
from quantify_app.item_analysis import item_profile
from quantify_app.qr import svg as qr_svg
from quantify_app.seed import seed_demo, seed_if_empty, seed_workspace

ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
DB_PATH = Path(os.getenv("QUANTIFY_DB", ROOT / "data" / "quantify.db"))
VERSION = "3.0.0"
MAX_BODY_BYTES = 2_000_000


def parse_date(value: str | None, fallback: date | None = None) -> date:
    if not value:
        if fallback is None:
            raise ValueError("A date is required")
        return fallback
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Dates must look like 2026-08-11") from exc


def _int(value: Any, default: int | None, low: int, high: int, label: str) -> int:
    """A whole number from request input, or a sentence a person can act on.

    Blank means `default`. Anything else has to read as a number and sit
    between `low` and `high`, or the reply says so in the words the screen
    uses ("Days must be a whole number between 1 and 14"). Python's own
    wording for a bad int never reaches the interface.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        if default is None:
            raise ValueError(f"{label} is required")
        return default
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a whole number between {low} and {high}")
    try:
        number = float(str(value).strip().replace(",", ""))
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a whole number between {low} and {high}") from None
    if number != number or number != int(number) or not (low <= number <= high):
        raise ValueError(f"{label} must be a whole number between {low} and {high}")
    return int(number)


def _float(value: Any, default: float | None, low: float, high: float, label: str) -> float:
    """A number from request input, with the same manners as `_int`."""
    if value is None or (isinstance(value, str) and not value.strip()):
        if default is None:
            raise ValueError(f"{label} is required")
        return default
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a number between {low:g} and {high:g}")
    try:
        number = float(str(value).strip().replace(",", ""))
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a number between {low:g} and {high:g}") from None
    if number != number or not (low <= number <= high):
        raise ValueError(f"{label} must be a number between {low:g} and {high:g}")
    return number


# Fragments that mean an error message was written by Python, not for a person.
_PYTHON_TELLS = (
    "int()", "float(", "nonetype", "not subscriptable", "not iterable", "invalid literal",
    "could not convert", "has no attribute", "object is not", "unsupported operand",
    "traceback", "keyerror", "typeerror", "valueerror", "attributeerror",
)
NOT_UNDERSTOOD = "That request was not understood"


def _plain_error(exc: BaseException) -> str:
    """The message a 400 carries: the sentence that was written for a person, or a plain stand-in."""
    text = str(exc).strip()
    if not text or len(text) > 300 or "\n" in text:
        return NOT_UNDERSTOOD
    lowered = text.lower()
    if any(tell in lowered for tell in _PYTHON_TELLS):
        return NOT_UNDERSTOOD
    return text


def _json(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def _location(conn: sqlite3.Connection, location_id: str, organization_id: str | None = None) -> sqlite3.Row:
    if organization_id:
        row = conn.execute(
            "SELECT * FROM locations WHERE id=? AND organization_id=? AND active=1",
            (location_id, organization_id),
        ).fetchone()
    else:
        row = conn.execute("SELECT * FROM locations WHERE id=? AND active=1", (location_id,)).fetchone()
    if row is None:
        raise ValueError("That location is not on this account")
    return row


def _integration_view(conn: sqlite3.Connection, location_id: str) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM integrations WHERE location_id=? ORDER BY provider", (location_id,)).fetchall()
    output = []
    for row in rows:
        item = dict(row)
        try:
            item["details"] = json.loads(item.get("details") or "{}")
        except json.JSONDecodeError:
            item["details"] = {}
        output.append(item)
    return output


def _menu_import(conn: sqlite3.Connection, location_id: str, text: str, commit: bool) -> dict[str, Any]:
    if len(text) > 150_000:
        raise ValueError("That is more menu text than this can take at once")
    parsed = parse_menu_text(text)
    if not commit:
        return {"preview": parsed, "count": len(parsed), "committed": False}
    created = 0
    updated = 0
    for row in parsed:
        existing = conn.execute(
            "SELECT id FROM menu_items WHERE location_id=? AND lower(name)=lower(?)",
            (location_id, row["name"]),
        ).fetchone()
        if existing:
            item_id = existing["id"]
            conn.execute(
                "UPDATE menu_items SET category=?,price=CASE WHEN ?>0 THEN ? ELSE price END,active=1 WHERE id=?",
                (row["category"], row["price"], row["price"], item_id),
            )
            updated += 1
        else:
            item_id = f"item-import-{uuid.uuid4().hex}"
            conn.execute(
                """INSERT INTO menu_items(id,location_id,pos_item_id,name,category,price,base_daily_qty,active)
                   VALUES(?,?,?,?,?,?,1,1)""",
                (item_id, location_id, None, row["name"], row["category"], row["price"]),
            )
            created += 1
        upsert_interpretation(conn, item_id, row["name"], row["category"])
    conn.commit()
    return {"count": len(parsed), "created": created, "updated": updated, "committed": True}


def _data_version(conn: sqlite3.Connection, organization_id: str, location_id: str | None) -> str:
    """A short fingerprint of what this account's screens render.

    The browser polls this. When it changes, the open screen refreshes itself.
    Every part is scoped to the caller, so one account's activity never forces a
    refresh in another, and the value carries no information across accounts.
    """
    scope = "SELECT id FROM locations WHERE organization_id=?"
    parts: list[str] = [organization_id]
    for sql in (
        f"SELECT MAX(last_sync) AS a, COUNT(*) AS b FROM integrations WHERE location_id IN ({scope})",
        f"SELECT MAX(date) AS a, COUNT(*) AS b FROM sales WHERE location_id IN ({scope})",
        f"SELECT MAX(scored_at) AS a, COUNT(*) AS b FROM day_accuracy WHERE location_id IN ({scope})",
        f"SELECT MAX(updated_at) AS a, COUNT(*) AS b FROM forecast_overrides WHERE location_id IN ({scope})",
        f"SELECT MAX(counted_at) AS a, COUNT(*) AS b FROM stock_counts WHERE location_id IN ({scope})",
        f"SELECT MAX(sent_at) AS a, COUNT(*) AS b FROM purchase_orders WHERE location_id IN ({scope})",
    ):
        row = conn.execute(sql, (organization_id,)).fetchone()
        parts.append(f"{row['a']}:{row['b']}" if row else "")
    # Written text is keyed by menu item, or by "<location>:<date>" for a day.
    # Both are matched to this organisation's locations and nothing else.
    row = conn.execute(
        f"""SELECT MAX(created_at) AS a FROM ai_generations
            WHERE subject IN (SELECT id FROM menu_items WHERE location_id IN ({scope}))
               OR substr(subject, 1, instr(subject, ':') - 1) IN ({scope})""",
        (organization_id, organization_id),
    ).fetchone()
    parts.append(str(row["a"] if row else ""))
    if location_id:
        parts.append(location_id)
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


_SHOWCASE: dict[str, Any] = {"at": 0.0, "value": None}


def _location_today(conn: sqlite3.Connection, location_id: str) -> date:
    """The trading date this location is in, on its own clock.

    A place that shuts at two in the morning is still working on yesterday at
    one, so the date the interface defaults to has to come from the location
    and not from wherever the server happens to be running.
    """
    row = conn.execute(
        "SELECT timezone,open_hour,close_hour FROM locations WHERE id=?", (location_id,)
    ).fetchone()
    if row is None:
        return date.today()
    try:
        return intraday.trading_date(row, localtime.now(row["timezone"]))
    except Exception:  # a bad zone must never stop a page loading
        return date.today()


def _trading_hours(data: dict[str, Any]) -> tuple[int, int]:
    """Opening and closing hour, with a close after midnight stored past 24.

    Keeping "closes at 2 AM" as 26 instead of 2 means every span in the product
    stays a plain subtraction. A bar open 11 to 2 is a fifteen hour day, and
    nothing downstream needs a special case for it.
    """
    try:
        opens = int(data.get("open_hour", 7))
    except (TypeError, ValueError):
        opens = 7
    try:
        closes = int(data.get("close_hour", 21))
    except (TypeError, ValueError):
        closes = 21
    opens = max(0, min(14, opens))
    closes = max(0, min(28, closes))
    if closes <= opens:
        closes += 24
    if closes - opens > 24:
        closes = opens + 24
    return opens, closes


def _showcase(conn: sqlite3.Connection) -> dict[str, Any]:
    """Real numbers for the landing page, computed from the sample dataset.

    Nothing here is a marketing figure typed into a template. It is the same
    day, the same order list and the same closed days the product shows once
    you are in, which is the only claim worth making on a landing page. The
    browser draws them with the app's own screen code.
    """
    if _SHOWCASE["value"] is not None and time.time() - _SHOWCASE["at"] < 90:
        return _SHOWCASE["value"]

    # This endpoint is public, so it is restricted to the seeded sample
    # locations. A real customer's name and revenue must never be reachable
    # without signing in, whatever else changes around this function.
    location = conn.execute(
        """SELECT l.* FROM locations l
           LEFT JOIN (SELECT location_id, COUNT(*) AS n FROM day_accuracy GROUP BY location_id) a
             ON a.location_id = l.id
           WHERE l.active=1 AND l.id IN ('loc-bakery', 'loc-pizza', 'loc-burger')
           ORDER BY COALESCE(a.n, 0) DESC, l.name LIMIT 1"""
    ).fetchone()
    if location is None:
        _SHOWCASE.update({"at": time.time(), "value": {"available": False,
            "pricing": {"monthly": billing.PLANS["solo"]["monthly"], "plans": billing.public_catalog(), "trial_days": 14}}})
        return _SHOWCASE["value"]

    location_id = location["id"]
    today = _location_today(conn, location_id)
    trend = transactions.accuracy_trend(conn, location_id, days=30)
    history = conn.execute(
        "SELECT COUNT(DISTINCT date) AS days, MIN(date) AS first FROM sales WHERE location_id=?",
        (location_id,),
    ).fetchone()

    # The day, trimmed to what the landing draws. Items and actions keep their
    # full shape so the make list is painted by the same code as Today.
    brief: dict[str, Any] | None
    try:
        full = daily_brief(conn, location_id, today, week_days=7)
        keep = (
            "date", "date_label", "generated_at", "headline", "summary", "comparison", "trust",
            "service_curve", "context", "data_health", "data_note", "costs", "location", "week_ahead",
        )
        brief = {key: full[key] for key in keep if key in full}
        brief["items"] = full["items"][:6]
        brief["actions"] = full["actions"][:2]
        brief["no_history"] = bool(full.get("no_history"))
    except Exception:  # noqa: BLE001 - the landing page must never fail to load
        brief = None

    # One supplier's part of the order list, straight from the ordering code.
    # When the sample has no supplier yet the unassigned group is shown, which
    # is exactly what a new account sees.
    order: dict[str, Any] | None
    try:
        plan = ordering.order_plan(conn, location_id, today, 3)
        if plan.get("ready"):
            plan = supply.attach(conn, location_id, plan)
        lines = [row for row in plan.get("lines") or [] if row.get("orderable")]
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in lines:
            groups.setdefault(row.get("supplier_id") or "", []).append(row)
        supplier_id = max(groups, key=lambda key: (bool(key), len(groups[key])), default="")
        supplier = next((row for row in plan.get("suppliers") or [] if row["id"] == supplier_id), None)
        order = {
            "start": plan.get("start"), "end": plan.get("end"), "days": plan.get("days", 3),
            "supplier": supplier,
            "lines": groups.get(supplier_id, [])[:5],
            "counts_taken": plan.get("counts_taken", 0),
        } if lines else None
    except Exception:  # noqa: BLE001
        order = None

    email = conn.execute(
        "SELECT send_time FROM email_preferences WHERE location_id=?", (location_id,)
    ).fetchone()
    value = {
        "available": True,
        "accuracy": trend.get("average"),
        "days_scored": trend.get("days") or 0,
        "history_days": int(history["days"] or 0) if history else 0,
        "location": {
            "id": location_id, "name": location["name"], "concept": location["concept"],
            "city": location["city"], "region": location["region"],
            "timezone": location["timezone"], "timezone_label": timezones.describe(location["timezone"]),
            "open_hour": location["open_hour"], "close_hour": location["close_hour"],
            "email_time": email["send_time"] if email else "05:30",
        },
        "brief": brief,
        "order": order,
        "days": transactions.day_list(conn, location_id, limit=6)["days"],
        "pricing": {"monthly": billing.PLANS["solo"]["monthly"], "plans": billing.public_catalog(), "trial_days": 14},
    }
    _SHOWCASE.update({"at": time.time(), "value": value})
    return value


def _composition_for(conn: sqlite3.Connection, location_id: str, item_id: str, force: bool = False) -> dict[str, Any]:
    item = conn.execute(
        """SELECT m.id,m.name,m.category,m.price,i.normalized_name,i.item_family,i.daypart,i.confidence,i.inferred_json
           FROM menu_items m LEFT JOIN menu_interpretations i ON i.menu_item_id=m.id
           WHERE m.id=? AND m.location_id=?""",
        (item_id, location_id),
    ).fetchone()
    if item is None:
        raise ValueError("That menu item is not on this account")

    siblings = [
        row["name"]
        for row in conn.execute(
            "SELECT name FROM menu_items WHERE location_id=? AND active=1 LIMIT 30",
            (location_id,),
        ).fetchall()
    ]
    payload = {
        "raw_name": item["name"],
        "normalized_name": item["normalized_name"] or item["name"],
        "category": item["category"],
        "price": float(item["price"]),
        "family": item["item_family"] or "menu-item",
        "daypart": item["daypart"] or "all-day",
        "interpretation_confidence": float(item["confidence"] or 0),
        "other_items_on_this_menu": siblings,
    }
    result = ai.generate(
        conn,
        task="item_composition",
        subject=item_id,
        payload=payload,
        schema=ai.COMPOSITION_SCHEMA,
        local_writer=local_composition,
        force=force,
    )
    conn.execute(
        """INSERT INTO item_composition(menu_item_id,summary,confidence,verify_note,components_json,writer,updated_at)
           VALUES(?,?,?,?,?,?,?)
           ON CONFLICT(menu_item_id) DO UPDATE SET
             summary=excluded.summary, confidence=excluded.confidence, verify_note=excluded.verify_note,
             components_json=excluded.components_json, writer=excluded.writer, updated_at=excluded.updated_at""",
        (
            item_id, result.get("summary", ""), result.get("confidence", "low"), result.get("verify_note", ""),
            json.dumps(result.get("components", []), separators=(",", ":")),
            result.get("_writer", "local"), datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ),
    )
    conn.commit()
    return {
        "item_id": item_id,
        "name": payload["normalized_name"],
        "raw_name": item["name"],
        "category": item["category"],
        "price": payload["price"],
        "summary": result.get("summary", ""),
        "confidence": result.get("confidence", "low"),
        "verify_note": result.get("verify_note", ""),
        "components": result.get("components", []),
        "writer": result.get("_writer", "local"),
        "written_at": result.get("_written_at"),
    }


# Static files are read once per process and kept with their validator, so a
# revalidation from a tablet costs a dictionary lookup and a 304.
_STATIC: dict[str, tuple[float, bytes, str]] = {}
_STATIC_LOCK = threading.Lock()


def _static_file(target: Path) -> tuple[bytes, str]:
    """The bytes of one file under web/ and a short ETag for them."""
    stamp = target.stat().st_mtime
    key = str(target)
    with _STATIC_LOCK:
        cached = _STATIC.get(key)
        if cached and cached[0] == stamp:
            return cached[1], cached[2]
    content = target.read_bytes()
    etag = '"' + hashlib.sha1(content).hexdigest()[:16] + '"'
    with _STATIC_LOCK:
        _STATIC[key] = (stamp, content, etag)
    return content, etag


class QuantifyHandler(BaseHTTPRequestHandler):
    server_version = "Quantify/3.0"
    # Keep-alive. Every response carries a Content-Length, so a browser can
    # fetch the ten files and calls an open needs over one connection instead
    # of a handshake each, and never reuses a socket the server has dropped.
    protocol_version = "HTTP/1.1"
    # The request body, once read. Set per request in _dispatch.
    _body: bytes | None = None

    def log_message(self, fmt: str, *args: Any) -> None:
        if os.getenv("QUANTIFY_QUIET", "0") != "1":
            super().log_message(fmt, *args)

    def _write(self, body: bytes) -> None:
        """Send the body and flush it. A client that has gone is not an error worth logging."""
        try:
            if body:
                self.wfile.write(body)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            self.close_connection = True

    def _headers(self, content_type: str, content_length: int, status: int = 200, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(content_length))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self'; frame-ancestors 'self'; base-uri 'self'; form-action 'self'",
        )
        if not (extra and "Cache-Control" in extra):
            self.send_header("Cache-Control", "no-store" if content_type.startswith("application/json") else "no-cache")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()

    def json_response(self, payload: Any, status: int = 200, extra: dict[str, str] | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
        try:
            self._headers("application/json; charset=utf-8", len(body), status, extra)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            self.close_connection = True
            return
        self._write(body)

    def _cookie_token(self) -> str | None:
        raw = self.headers.get("Cookie", "")
        jar = cookies.SimpleCookie()
        try:
            jar.load(raw)
        except cookies.CookieError:
            return None
        morsel = jar.get("quantify_session")
        return morsel.value if morsel else None

    def _client_ip(self) -> str:
        # Forwarded addresses are only trusted when a known reverse proxy is the
        # single path to this process.
        if os.getenv("QUANTIFY_TRUST_PROXY", "0") == "1":
            forwarded = self.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
            if forwarded:
                return forwarded
        return self.client_address[0]

    def _public_origin(self) -> str:
        # Forwarded headers are read on the same condition as the client
        # address: only behind a proxy this process was told to trust.
        scheme = "http"
        host = self.headers.get("Host") or "localhost"
        if os.getenv("QUANTIFY_TRUST_PROXY", "0") == "1":
            scheme = (self.headers.get("X-Forwarded-Proto") or scheme).split(",", 1)[0].strip() or "http"
            host = (self.headers.get("X-Forwarded-Host") or host).split(",", 1)[0].strip() or host
        return f"{scheme}://{host}"

    def _auth_bypass(self, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
        if os.getenv("QUANTIFY_AUTH_BYPASS", "0") != "1":
            return None
        session = {
            "id": "bypass", "user_id": "bypass", "email": "demo@quantify.local",
            "display_name": "Demo Owner", "organization_id": "org-demo", "csrf_token": "bypass",
            "totp_enabled": False, "email_verified": True, "expires_at": "2099-01-01T00:00:00+00:00",
        }
        # When a real account exists in the sample workspace, act as it, so the
        # account screens can be exercised without a sign in.
        if conn is not None:
            row = conn.execute(
                "SELECT id,email,display_name,totp_enabled FROM users WHERE organization_id='org-demo' AND active=1 ORDER BY created_at LIMIT 1"
            ).fetchone()
            if row is not None:
                session.update({
                    "user_id": row["id"], "email": row["email"], "display_name": row["display_name"],
                    "totp_enabled": bool(row["totp_enabled"]),
                })
        return session

    def _session(self, conn: sqlite3.Connection, required: bool = True) -> dict[str, Any] | None:
        bypass = self._auth_bypass(conn)
        if bypass:
            return bypass
        session = session_from_token(conn, self._cookie_token())
        if required and session is None:
            raise PermissionError("Sign in to continue")
        return session.as_dict() if session else None

    def _csrf(self, session: dict[str, Any]) -> None:
        if session.get("id") == "bypass":
            return
        token = self.headers.get("X-CSRF-Token", "")
        expected = str(session.get("csrf_token") or "")
        if not token or not expected or not hmac.compare_digest(token, expected):
            raise PermissionError("Your session expired. Refresh the page and try again")

    def _read_raw(self) -> bytes:
        """The request body, read once. Later calls get the same bytes.

        On a kept-alive connection an unread body would be taken for the start
        of the next request, so the body is always consumed, even when the
        route that needed it was never reached.
        """
        if self._body is not None:
            return self._body
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError as exc:
            self.close_connection = True
            raise ValueError("Invalid request length") from exc
        if length < 0 or length > MAX_BODY_BYTES:
            self.close_connection = True
            raise ValueError("That request is too large")
        self._body = self.rfile.read(length) if length else b""
        return self._body

    def _drain(self) -> None:
        """Consume a body nobody read, so the connection stays usable."""
        if self._body is None and not self.close_connection:
            try:
                self._read_raw()
            except Exception:  # noqa: BLE001 - already marked to close
                self.close_connection = True

    def _read_json(self, raw: bytes | None = None) -> dict[str, Any]:
        raw = self._read_raw() if raw is None else raw
        if not raw:
            return {}
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Request body must be valid JSON") from exc
        if not isinstance(parsed, dict):
            raise ValueError("Request body must be a JSON object")
        return parsed

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch("PUT")

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        self._body = None
        try:
            if path == "/api/health":
                self.json_response({"ok": True, "version": VERSION, "time": datetime.now(timezone.utc).isoformat(timespec="seconds")})
                return
            if path.startswith("/api/"):
                self._api(method, path, query)
                return
            self.serve_static(path)
        except PermissionError as exc:
            self._drain()
            self.json_response({"error": str(exc)}, 403)
        except ValueError as exc:
            # Only a sentence written for a person passes through. Python's
            # own wording for a bad number is replaced, never shown.
            self._drain()
            self.json_response({"error": _plain_error(exc)}, 400)
        except (TypeError, AttributeError):
            # The body had the wrong shape somewhere a route did not check.
            # That is the caller's mistake, said plainly, not a server fault.
            if os.getenv("QUANTIFY_DEBUG", "0") == "1":
                traceback.print_exc()
            self._drain()
            self.json_response({"error": NOT_UNDERSTOOD}, 400)
        except RuntimeError as exc:
            self._drain()
            self.json_response({"error": str(exc)}, 502)
        except Exception as exc:  # pragma: no cover - final safety boundary
            traceback.print_exc()
            self._drain()
            detail = str(exc) if os.getenv("QUANTIFY_DEBUG", "0") == "1" else None
            self.json_response({"error": "Something went wrong on our side", "detail": detail}, 500)
        finally:
            self._drain()

    # -- API ---------------------------------------------------------------

    def _api(self, method: str, path: str, query: dict[str, list[str]]) -> None:
        if path == "/api/webhooks/square" and method == "POST":
            raw = self._read_raw()
            location_id = (query.get("location_id") or [os.getenv("QUANTIFY_SQUARE_INTERNAL_LOCATION", "")])[0]
            if not location_id:
                raise ValueError("The webhook is not mapped to a location yet")
            public_url = os.getenv("SQUARE_WEBHOOK_NOTIFICATION_URL") or f"{self._public_origin()}{path}?location_id={location_id}"
            with connect(DB_PATH) as conn:
                self.json_response(process_square_webhook(
                    conn, location_id, raw, self.headers.get("X-Square-HmacSha256-Signature"), public_url,
                ))
            return

        if path == "/api/webhooks/stripe" and method == "POST":
            raw = self._read_raw()
            billing.verify_webhook_signature(raw, self.headers.get("Stripe-Signature"))
            with connect(DB_PATH) as conn:
                self.json_response(billing.apply_stripe_event(conn, json.loads(raw.decode("utf-8") or "{}")))
            return

        with connect(DB_PATH) as conn:
            if self._public_routes(conn, method, path, query):
                return

            session = self._session(conn, required=True)
            assert session is not None
            if self._account_routes(conn, session, method, path, query):
                return

            # Confirming the email address is the one gate. Two-step sign in is
            # offered in Settings and is the account holder's choice.
            if not bool(session.get("email_verified")):
                raise PermissionError("Confirm your email address to continue")

            if method in {"POST", "PUT", "PATCH", "DELETE"}:
                self._csrf(session)

            if self._workspace_routes(conn, session, method, path, query):
                return

            location_id = (query.get("location_id") or [None])[0]
            if not location_id:
                raise ValueError("Choose a location first")
            _location(conn, location_id, session["organization_id"])
            if self._location_routes(conn, session, method, path, query, location_id):
                return
            self.json_response({"error": "Not found"}, 404)

    def _public_routes(self, conn: sqlite3.Connection, method: str, path: str, query: dict[str, list[str]]) -> bool:
        if path == "/api/auth/state" and method == "GET":
            bypass = self._auth_bypass()
            if bypass:
                self.json_response({
                    "setup_required": False, "authenticated": True, "user": bypass,
                    "email_verification_required": False, "mfa_enabled": False,
                    "onboarding_required": False,
                })
            else:
                self.json_response(auth_state(conn, self._cookie_token()))
            return True
        if path == "/api/auth/setup" and method == "POST":
            data = self._read_json()
            owner = create_owner(conn, str(data.get("email", "")), str(data.get("display_name", "")), str(data.get("password", "")), self._client_ip())
            session = create_session(conn, owner["user_id"], ip=self._client_ip(), user_agent=self.headers.get("User-Agent"))
            issued = start_email_verification(conn, owner["user_id"], self._client_ip())
            self.json_response(
                {
                    "authenticated": True,
                    "email_verification_required": True,
                    "user": {"email": owner["email"], "display_name": owner["display_name"]},
                    "csrf_token": session["csrf_token"],
                    "verification": self._deliver_code(owner["email"], issued),
                },
                201,
                {"Set-Cookie": cookie_header(session["session_token"])},
            )
            return True
        if path == "/api/auth/login" and method == "POST":
            data = self._read_json()
            result = begin_login(conn, str(data.get("email", "")), str(data.get("password", "")), self._client_ip())
            token = result.pop("session_token", None)
            self.json_response(result, extra={"Set-Cookie": cookie_header(token)} if token else None)
            return True
        if path == "/api/auth/verify" and method == "POST":
            data = self._read_json()
            result = complete_login(conn, str(data.get("challenge", "")), str(data.get("code", "")), self._client_ip(), self.headers.get("User-Agent"))
            token = result.pop("session_token")
            self.json_response(result, extra={"Set-Cookie": cookie_header(token)})
            return True
        if path == "/api/auth/password/reset/start" and method == "POST":
            # Always the same answer, so the form never says which addresses
            # have an account here.
            data = self._read_json()
            email = str(data.get("email", "")).strip().lower()
            if "@" not in email or len(email) > 254:
                raise ValueError("Enter the email address you sign in with")
            issued = start_password_reset(conn, email, self._client_ip())
            payload: dict[str, Any] = {"ok": True, "expires_in_minutes": EMAIL_CODE_MINUTES}
            if issued is not None:
                sent = self._deliver_code(issued["email"], issued, purpose="reset")
                if "preview_code" in sent:
                    payload["preview_code"] = sent["preview_code"]
                    payload["preview_note"] = sent["preview_note"]
            self.json_response(payload)
            return True
        if path == "/api/auth/password/reset/complete" and method == "POST":
            data = self._read_json()
            self.json_response(complete_password_reset(
                conn, str(data.get("email", "")), str(data.get("code", "")),
                str(data.get("new_password", "")), self._client_ip(),
            ))
            return True
        if path == "/api/timezone" and method == "GET":
            text = (query.get("q") or [""])[0]
            self.json_response({"match": timezones.resolve(text), "suggestions": timezones.suggest(text)})
            return True
        if path == "/api/showcase" and method == "GET":
            self.json_response(_showcase(conn))
            return True
        return False

    def _deliver_code(self, email: str, issued: dict[str, Any], purpose: str = "verify") -> dict[str, Any]:
        """Send a six-digit code, and say plainly how it went out.

        Until a mail provider is connected the message is kept on the server
        and the code is handed straight back to the browser, which is the only
        way an unlaunched product can let someone finish. The moment mail is
        connected, the code stops being returned and only arrives by email.
        """
        if issued.get("already_verified"):
            return {"already_verified": True, "sent_to": email}
        code = issued["code"]
        # Decided before the send, from configuration alone. A connected
        # provider that happens to fail must never fall back to showing the code.
        unconfigured = mail_provider() == "outbox"
        send = send_password_reset_code if purpose == "reset" else send_verification_code
        result = send(ROOT, email, code, issued["expires_in_minutes"])
        payload = {
            "sent_to": email,
            "provider": result["provider"],
            "delivered": bool(result.get("delivered")),
            "expires_in_minutes": issued["expires_in_minutes"],
        }
        if result["provider"] == "blocked":
            raise PermissionError("That address cannot receive mail from Quantify")
        if not unconfigured and not result.get("delivered"):
            raise RuntimeError("The email could not be sent. Try again in a moment")
        if unconfigured:
            payload["preview_code"] = code
            payload["preview_note"] = "Email is not switched on yet, so your code is shown here."
        return payload

    def _account_routes(self, conn: sqlite3.Connection, session: dict[str, Any], method: str, path: str, query: dict[str, list[str]]) -> bool:
        if path == "/api/auth/email/status" and method == "GET":
            self.json_response({
                "email": session["email"],
                "verified": bool(session.get("email_verified")),
                "provider": mail_provider(),
            })
            return True
        if path == "/api/auth/email/resend" and method == "POST":
            self._csrf(session)
            issued = start_email_verification(conn, session["user_id"], self._client_ip())
            self.json_response(self._deliver_code(session["email"], issued))
            return True
        if path == "/api/auth/email/confirm" and method == "POST":
            self._csrf(session)
            data = self._read_json()
            result = confirm_email(conn, session["user_id"], str(data.get("code", "")), self._client_ip())
            result["onboarding_required"] = onboarding_required(conn, session["organization_id"])
            self.json_response(result)
            return True
        if path == "/api/auth/mfa/disable" and method == "POST":
            self._csrf(session)
            data = self._read_json()
            self.json_response(disable_totp(conn, session["user_id"], str(data.get("password", "")), self._client_ip()))
            return True
        if path == "/api/auth/mfa/setup" and method == "GET":
            user = conn.execute("SELECT email,totp_secret,totp_enabled FROM users WHERE id=?", (session["user_id"],)).fetchone()
            if user is None:
                raise PermissionError("Account not found")
            enabled = bool(user["totp_enabled"])
            uri = otpauth_uri(user["email"], user["totp_secret"]) if not enabled else None
            self.json_response({
                "enabled": enabled,
                "secret": None if enabled else user["totp_secret"],
                "secret_grouped": None if enabled else format_secret(user["totp_secret"]),
                "otpauth_uri": uri,
                "qr_svg": qr_svg(uri) if uri else None,
                "recovery_codes_remaining": conn.execute(
                    "SELECT COUNT(*) AS n FROM recovery_codes WHERE user_id=? AND used_at IS NULL", (session["user_id"],)
                ).fetchone()["n"],
            })
            return True
        if path == "/api/auth/totp/enable" and method == "POST":
            self._csrf(session)
            data = self._read_json()
            self.json_response(enable_totp(conn, session["user_id"], str(data.get("code", "")), self._client_ip()))
            return True
        if path == "/api/auth/recovery-codes" and method == "POST":
            self._csrf(session)
            codes = issue_recovery_codes(conn, session["user_id"], self._client_ip())
            self.json_response({"codes": codes, "count": len(codes)})
            return True
        if path == "/api/auth/logout" and method == "POST":
            self._csrf(session)
            revoke_session(conn, self._cookie_token(), self._client_ip())
            self.json_response({"ok": True}, extra={"Set-Cookie": clear_cookie_header()})
            return True
        if path == "/api/auth/password/change" and method == "POST":
            self._csrf(session)
            data = self._read_json()
            self.json_response(change_password(
                conn, session["user_id"], str(data.get("current_password", "")),
                str(data.get("new_password", "")), session_id=session.get("id"), ip=self._client_ip(),
            ))
            return True
        if path == "/api/auth/profile" and method == "POST":
            # The name on the account, as shown in the rail and on the morning
            # email. The address is not changed here.
            self._csrf(session)
            data = self._read_json()
            name = str(data.get("display_name", "")).strip()[:80]
            if len(name) < 2:
                raise ValueError("Enter a name of at least two characters")
            changed = conn.execute("UPDATE users SET display_name=? WHERE id=? AND active=1", (name, session["user_id"]))
            if changed.rowcount == 0:
                raise PermissionError("Account not found")
            conn.commit()
            self.json_response({"ok": True, "display_name": name})
            return True
        return False

    def _workspace_routes(self, conn: sqlite3.Connection, session: dict[str, Any], method: str, path: str, query: dict[str, list[str]]) -> bool:
        organization_id = session["organization_id"]

        if path == "/api/onboarding" and method == "GET":
            organization = _json(conn.execute("SELECT * FROM organizations WHERE id=?", (organization_id,)).fetchone())
            locations = [dict(row) for row in conn.execute(
                "SELECT id,name,concept,city,region,timezone FROM locations WHERE organization_id=? AND active=1 ORDER BY name",
                (organization_id,),
            ).fetchall()]
            self.json_response({
                "required": onboarding_required(conn, organization_id),
                "organization": organization,
                "sample_locations": locations,
                "owner": {"name": session["display_name"], "email": session["email"]},
            })
            return True

        if path == "/api/onboarding" and method == "POST":
            data = self._read_json()
            company = str(data.get("company", "")).strip()
            if len(company) < 2:
                raise ValueError("Tell us the name of the business")
            billing.ensure_subscription(conn, organization_id)
            place = str(data.get("place", "")).strip()
            resolved = timezones.resolve(place) if place else timezones.resolve("")
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            conn.execute(
                """UPDATE organizations SET name=?,concept=?,location_count=?,primary_goal=?,pos_provider=?,onboarded_at=?
                   WHERE id=?""",
                (
                    company, str(data.get("concept", ""))[:120], str(data.get("location_count", ""))[:40],
                    str(data.get("goal", ""))[:120], str(data.get("pos", ""))[:60], now, organization_id,
                ),
            )
            created_location = None
            if data.get("add_location") and str(data.get("location_name", "")).strip():
                billing.require_location_capacity(conn, organization_id)
                location_id = f"loc-{uuid.uuid4().hex[:10]}"
                conn.execute(
                    """INSERT INTO locations(
                          id,organization_id,name,concept,address,city,region,postal_code,
                          latitude,longitude,timezone,open_hour,close_hour,currency,active)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'USD',1)""",
                    (
                        location_id, organization_id, str(data.get("location_name"))[:120],
                        str(data.get("concept", "Restaurant"))[:60], str(data.get("address", ""))[:200],
                        str(data.get("city", place))[:80], str(data.get("region", ""))[:40],
                        str(data.get("postal_code", ""))[:16],
                        _float(data.get("latitude"), 40.7128, -90.0, 90.0, "Latitude"),
                        _float(data.get("longitude"), -74.0060, -180.0, 180.0, "Longitude"),
                        resolved["timezone"],
                        _int(data.get("open_hour"), 7, 0, 23, "Opening hour"),
                        _int(data.get("close_hour"), 21, 0, 28, "Closing hour"),
                    ),
                )
                conn.execute(
                    """INSERT INTO email_preferences(location_id,owner_email,enabled,send_time,timezone,include_week_ahead,updated_at)
                       VALUES(?,?,1,'05:30',?,1,?)""",
                    (location_id, session["email"], resolved["timezone"], now),
                )
                created_location = location_id
            billing.ensure_subscription(conn, organization_id)
            conn.commit()
            # A brand new workspace gets one sample location so the product is
            # working the moment onboarding finishes, before any register is
            # connected. It is labelled as sample data everywhere it appears.
            has_location = conn.execute(
                "SELECT 1 FROM locations WHERE organization_id=? AND active=1 LIMIT 1", (organization_id,)
            ).fetchone()
            sample_location = None
            opens, closes = _trading_hours(data)
            if not has_location:
                billing.require_location_capacity(conn, organization_id)
                sample_location = seed_workspace(
                    conn, organization_id,
                    concept=str(data.get("concept", "")),
                    name=company,
                    owner_email=session["email"],
                    city=str(data.get("city", place))[:80],
                    region=str(data.get("region", ""))[:40],
                    timezone=resolved["timezone"],
                    latitude=resolved.get("latitude"),
                    longitude=resolved.get("longitude"),
                    open_hour=opens,
                    close_hour=closes,
                )
            target_location = created_location or sample_location
            if target_location:
                conn.execute(
                    "UPDATE locations SET open_hour=?,close_hour=? WHERE id=? AND organization_id=?",
                    (opens, closes, target_location, organization_id),
                )
                conn.commit()
            self.json_response({
                "ok": True, "timezone": resolved,
                "location_id": target_location,
                "sample_location": bool(sample_location),
                "open_hour": opens, "close_hour": closes,
            })
            return True

        if path == "/api/locations" and method == "POST":
            data = self._read_json()
            name = str(data.get("name", "")).strip()[:120]
            concept = str(data.get("concept", "")).strip()[:120]
            place = str(data.get("place", "")).strip()[:160]
            region = str(data.get("region", "")).strip()[:40]
            if len(name) < 2 or not concept or not place:
                raise ValueError("Add a location name, what you serve, and its city")
            place_query = f"{place}, {region}" if region and not place.upper().endswith(region.upper()) else place
            resolved = timezones.resolve(str(data.get("timezone") or place_query))
            if not resolved.get("confident"):
                raise ValueError("Choose a recognised city or time zone before adding the location")
            geo = timezones.resolve(place_query)
            opens, closes = _trading_hours(data)
            billing.ensure_subscription(conn, organization_id)
            billing.require_location_capacity(conn, organization_id)
            location_id = f"loc-{uuid.uuid4().hex[:10]}"
            latitude, longitude = geo.get("latitude"), geo.get("longitude")
            verified = latitude is not None and longitude is not None
            conn.execute(
                """INSERT INTO locations(id,organization_id,name,concept,address,city,region,postal_code,
                      latitude,longitude,timezone,open_hour,close_hour,currency,active)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'USD',1)""",
                (location_id, organization_id, name, concept, "", geo.get("city") or place,
                 geo.get("region") or region, "",
                 latitude if verified else 0, longitude if verified else 0, resolved["timezone"], opens, closes),
            )
            conn.execute("INSERT INTO settings(location_id,key,value) VALUES(?,'geography_status',?)",
                         (location_id, "verified" if verified else "unverified"))
            conn.commit()
            self.json_response({"location": dict(_location(conn, location_id, organization_id)),
                                "geography_status": "verified" if verified else "unverified"}, status=201)
            return True

        if path == "/api/bootstrap" and method == "GET":
            locations = [dict(row) for row in conn.execute(
                "SELECT * FROM locations WHERE organization_id=? AND active=1 ORDER BY name", (organization_id,)
            ).fetchall()]
            organization = _json(conn.execute("SELECT * FROM organizations WHERE id=?", (organization_id,)).fetchone())
            # The day the interface opens on is the chosen location's own day.
            chosen = (query.get("location_id") or [None])[0]
            if chosen and not any(row["id"] == chosen for row in locations):
                chosen = None
            first = chosen or (locations[0]["id"] if locations else None)
            self.json_response({
                "version": VERSION,
                "organization": organization,
                "locations": locations,
                "default_location_id": locations[0]["id"] if locations else None,
                "today": (_location_today(conn, first) if first else date.today()).isoformat(),
                "providers": provider_readiness(conn, first),
                "writer": ai.status(),
                "billing": {"connected": billing.connected(), "plan": billing.overview(conn, organization_id)["plan"]},
                "user": {"name": session["display_name"], "email": session["email"]},
            })
            return True

        if path == "/api/pulse" and method == "GET":
            location_id = (query.get("location_id") or [None])[0]
            if location_id:
                _location(conn, location_id, organization_id)
            self.json_response({
                "version": _data_version(conn, organization_id, location_id),
                "server_time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                # The location's own date, so the screen rolls over at its
                # midnight and not at the browser's.
                "today": (_location_today(conn, location_id) if location_id else date.today()).isoformat(),
                "scoring": SCORER.state(),
                "reforecasting": REFORECASTER.state(),
            })
            return True

        if path == "/api/billing" and method == "GET":
            self.json_response(billing.overview(conn, organization_id))
            return True
        if path == "/api/billing/checkout" and method == "POST":
            data = self._read_json()
            self.json_response(billing.start_checkout(
                conn, organization_id, session["email"], session["display_name"],
                str(data.get("plan", "solo")), f"{self._public_origin()}/",
            ))
            return True
        if path == "/api/billing/portal" and method == "POST":
            self.json_response(billing.payment_portal(
                conn, organization_id, session["email"], session["display_name"], f"{self._public_origin()}/",
            ))
            return True
        if path == "/api/billing/plan" and method == "POST":
            data = self._read_json()
            self.json_response(billing.change_plan(conn, organization_id, str(data.get("plan", "solo"))))
            return True
        if path == "/api/billing/cancel/reason" and method == "POST":
            data = self._read_json()
            self.json_response(billing.record_cancellation_intent(
                conn, organization_id, session["user_id"],
                str(data.get("reason", "")), str(data.get("detail", "")), bool(data.get("wants_contact")),
            ))
            return True
        if path == "/api/billing/cancel" and method == "POST":
            data = self._read_json()
            self.json_response(billing.cancel_plan(conn, organization_id, bool(data.get("immediate"))))
            return True
        if path == "/api/billing/resume" and method == "POST":
            self.json_response(billing.resume_plan(conn, organization_id))
            return True
        return False

    def _location_routes(
        self, conn: sqlite3.Connection, session: dict[str, Any], method: str,
        path: str, query: dict[str, list[str]], location_id: str,
    ) -> bool:
        # The default day is the one this location is actually in, not the one
        # the server is in. A server in another zone would otherwise ask for
        # tomorrow's brief all evening, and the opening call would never lock
        # because its date would never match.
        today = _location_today(conn, location_id)

        if path == "/api/brief" and method == "GET":
            target = parse_date((query.get("date") or [today.isoformat()])[0])
            brief = daily_brief(conn, location_id, target, week_days=7)
            # Before the doors open, what Quantify says is written down and
            # cannot be changed. Every accuracy figure in the product is
            # measured against that, so a forecast revised during service can
            # never be reported as a forecast of that day.
            intraday.lock_opening_call(conn, location_id, target, brief["items"])
            brief["costs"] = costs.forecast_costs(conn, location_id, brief)
            brief["intraday"] = intraday.day_state(conn, location_id, target, brief)
            brief["writer"] = ai.status()
            cached = ai.read_cache(conn, "day_narrative", f"{location_id}:{target.isoformat()}", ai.fingerprint(build_day_payload(brief)))
            brief["narrative"] = cached
            self.json_response(brief)
            return True

        if path == "/api/brief/narrative" and method == "GET":
            target = parse_date((query.get("date") or [today.isoformat()])[0])
            brief = daily_brief(conn, location_id, target, week_days=2)
            payload = build_day_payload(brief)
            subject = f"{location_id}:{target.isoformat()}"
            key = f"day_narrative:{subject}"
            if not ai.claim_inflight(key):
                cached = ai.read_cache(conn, "day_narrative", subject, ai.fingerprint(payload))
                self.json_response(cached or {"pending": True})
                return True
            try:
                self.json_response(ai.generate(
                    conn, "day_narrative", subject, payload, ai.DAY_NARRATIVE_SCHEMA, local_day_narrative,
                    force=(query.get("refresh") or ["0"])[0] == "1",
                ))
            finally:
                ai.release_inflight(key)
            return True

        if path == "/api/outlook" and method == "GET":
            start = parse_date((query.get("start") or [today.isoformat()])[0])
            days = _int((query.get("days") or ["14"])[0], 14, 1, 14, "Days")
            self.json_response(forecast_range(conn, location_id, start, days))
            return True

        if path == "/api/accuracy" and method == "GET":
            as_of = parse_date((query.get("as_of") or [today.isoformat()])[0])
            days = _int((query.get("days") or ["30"])[0], 30, 1, 365, "Days")
            result = performance(conn, location_id, as_of, days)
            result["trend"] = transactions.accuracy_trend(conn, location_id, days=60)
            self.json_response(result)
            return True

        if path == "/api/history/days" and method == "GET":
            before = query.get("before")
            start = query.get("start")
            self.json_response(transactions.day_list(
                conn, location_id,
                with_costs=True,
                before=parse_date(before[0]) if before else None,
                start=parse_date(start[0]) if start else None,
                limit=_int((query.get("limit") or ["18"])[0], 18, 1, 200, "Limit"),
            ))
            return True

        if path == "/api/history/orders" and method == "GET":
            before = query.get("before")
            start = query.get("start")
            self.json_response(transactions.order_page(
                conn, location_id,
                before_date=parse_date(before[0]) if before else None,
                start=parse_date(start[0]) if start else None,
                skip=_int((query.get("skip") or ["0"])[0], 0, 0, 1_000_000, "Skip"),
                limit=_int((query.get("limit") or ["40"])[0], 40, 1, 500, "Limit"),
            ))
            return True

        if path == "/api/history/day" and method == "GET":
            target = parse_date((query.get("date") or [(today - timedelta(days=1)).isoformat()])[0])
            detail = transactions.day_detail(conn, location_id, target)
            subject = f"{location_id}:{target.isoformat()}"
            payload = transactions.review_payload(detail)
            detail["review"] = ai.generate(conn, "day_review", subject, payload, ai.DAY_REVIEW_SCHEMA, local_day_review)
            self.json_response(detail)
            return True

        if path == "/api/item" and method == "GET":
            item_id = (query.get("item_id") or [""])[0]
            target = parse_date((query.get("date") or [today.isoformat()])[0])
            self.json_response(item_profile(conn, location_id, item_id, target))
            return True

        if path == "/api/menu" and method == "GET":
            view = menu_intelligence_view(conn, location_id)
            # Work out what every item is made of the first time the menu is
            # opened. Nobody should have to press a button to be told what a
            # cheeseburger contains.
            missing = [
                row["id"] for row in view["items"]
                if not conn.execute("SELECT 1 FROM item_composition WHERE menu_item_id=?", (row["id"],)).fetchone()
            ]
            for item_id in missing[:40]:
                try:
                    _composition_for(conn, location_id, item_id)
                except Exception:  # noqa: BLE001 - one bad item must not blank the page
                    if os.getenv("QUANTIFY_DEBUG", "0") == "1":
                        traceback.print_exc()
            stored = {
                row["menu_item_id"]: dict(row)
                for row in conn.execute(
                    """SELECT c.* FROM item_composition c JOIN menu_items m ON m.id=c.menu_item_id
                       WHERE m.location_id=?""",
                    (location_id,),
                ).fetchall()
            }
            # What each item costs to make, so the menu screen can say which of
            # its two dollar figures is the price and which is the cost. The
            # owner could not tell them apart, and they were forty pixels apart.
            try:
                settings = costs.cost_settings(conn, location_id)
                basis = costs.item_cost_basis(conn, location_id, settings)
            except Exception:  # noqa: BLE001 - the menu must render without costing
                basis = {}
            for item in view["items"]:
                record = stored.get(item["id"])
                item["composition"] = {
                    "summary": record["summary"],
                    "confidence": record["confidence"],
                    "verify_note": record["verify_note"],
                    "components": json.loads(record["components_json"] or "[]"),
                    "writer": record["writer"],
                } if record else None
                entry = basis.get(item["id"])
                if entry:
                    share = float(entry["share"])
                    item["cost_share_percent"] = round(share * 100)
                    item["food_cost"] = round(float(item["price"]) * share, 2)
                    item["margin"] = round(float(item["price"]) * (1.0 - share), 2)
                    item["cost_source"] = entry["source"]
            view["writer"] = ai.status()
            self.json_response(view)
            return True

        if path == "/api/menu/composition" and method == "GET":
            item_id = (query.get("item_id") or [""])[0]
            self.json_response(_composition_for(conn, location_id, item_id))
            return True

        if path == "/api/menu/composition" and method == "PUT":
            data = self._read_json()
            item_id = str(data.get("item_id", ""))
            owned = conn.execute(
                "SELECT 1 FROM menu_items WHERE id=? AND location_id=?", (item_id, location_id)
            ).fetchone()
            if owned is None:
                raise ValueError("That menu item is not at this location")
            components = [
                {
                    "name": str(row.get("name", ""))[:80],
                    "role": str(row.get("role", "other"))[:24],
                    "quantity": str(row.get("quantity", ""))[:48],
                    "share": int(round(_float(row.get("share"), 0.0, 0.0, 100.0, "Share"))),
                    "confidence": "high",
                }
                for row in (data.get("components") or []) if str(row.get("name", "")).strip()
            ]
            conn.execute(
                """INSERT INTO item_composition(menu_item_id,summary,confidence,verify_note,components_json,writer,updated_at)
                   VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(menu_item_id) DO UPDATE SET
                     summary=excluded.summary, confidence=excluded.confidence, verify_note=excluded.verify_note,
                     components_json=excluded.components_json, writer=excluded.writer, updated_at=excluded.updated_at""",
                (
                    item_id, str(data.get("summary", ""))[:400], "high",
                    "Confirmed by you.",
                    json.dumps(components, separators=(",", ":")), "owner",
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                ),
            )
            conn.commit()
            self.json_response({"ok": True, "components": len(components)})
            return True

        if path == "/api/menu/composition" and method == "POST":
            data = self._read_json()
            self.json_response(_composition_for(conn, location_id, str(data.get("item_id", "")), force=True))
            return True

        if path == "/api/menu/import" and method == "POST":
            data = self._read_json()
            self.json_response(_menu_import(conn, location_id, str(data.get("text", "")), bool(data.get("commit", False))))
            return True

        if path == "/api/ordering" and method == "GET":
            days = _int((query.get("days") or ["3"])[0], 3, 1, 14, "Days")
            start = parse_date((query.get("start") or [today.isoformat()])[0])
            plan = ordering.order_plan(conn, location_id, start, days)
            self.json_response(supply.attach(conn, location_id, plan) if plan.get("ready") else plan)
            return True

        # -- Supply: suppliers, how things are bought, what is on the shelf ----
        if path == "/api/supply" and method == "GET":
            self.json_response(supply.supplier_view(conn, location_id))
            return True

        if path == "/api/supply/supplier" and method in {"POST", "PUT"}:
            self.json_response(supply.save_supplier(conn, location_id, self._read_json()))
            return True

        if path == "/api/supply/supplier" and method == "DELETE":
            data = self._read_json()
            supply.delete_supplier(conn, location_id, str(data.get("id", "")))
            self.json_response({"ok": True})
            return True

        if path == "/api/supply/item" and method in {"POST", "PUT"}:
            self.json_response(supply.save_item(conn, location_id, self._read_json()))
            return True

        if path == "/api/supply/count" and method in {"POST", "PUT"}:
            data = self._read_json()
            rows = data.get("counts") if isinstance(data.get("counts"), list) else [data]
            saved = [supply.save_count(conn, location_id, row, session.get("display_name", "")) for row in rows]
            self.json_response({"saved": saved})
            return True

        if path == "/api/supply/attention" and method == "GET":
            self.json_response(supply.attention(conn, location_id))
            return True

        if path == "/api/supply/order" and method == "POST":
            self.json_response(supply.record_order(conn, ROOT, location_id, self._read_json(), session.get("display_name", "")))
            return True

        if path == "/api/supply/orders" and method == "GET":
            self.json_response({"orders": supply.recent_orders(conn, location_id)})
            return True

        if path == "/api/costs" and method == "GET":
            self.json_response(costs.cost_view(conn, location_id))
            return True

        if path == "/api/costs" and method == "PUT":
            self.json_response(costs.save_cost_settings(conn, location_id, self._read_json()))
            return True

        if path == "/api/setup" and method == "GET":
            location = dict(_location(conn, location_id, session["organization_id"]))
            # The email settings never carry their own zone: the location's is
            # the one that counts, and two fields for one fact would drift.
            email = email_preferences(conn, location_id)
            email.pop("timezone", None)
            email["provider_connected"] = mail_provider() != "outbox"
            self.json_response({
                "location": location,
                "timezone": timezones.resolve(location["timezone"]),
                "integrations": _integration_view(conn, location_id),
                "providers": provider_readiness(conn, location_id),
                "register": square_status(conn, location_id),
                "email": email,
                "menu": menu_intelligence_view(conn, location_id)["summary"],
                "writer": ai.status(),
                "security": {
                    "mfa_enabled": bool(session["totp_enabled"]),
                    "session_expires_at": session["expires_at"],
                    "recovery_codes_remaining": conn.execute(
                        "SELECT COUNT(*) AS n FROM recovery_codes WHERE user_id=? AND used_at IS NULL", (session["user_id"],)
                    ).fetchone()["n"],
                },
                "order_source": transactions.order_source(conn, location_id),
            })
            return True

        if path == "/api/location" and method in {"POST", "PUT"}:
            data = self._read_json()
            current = _location(conn, location_id, session["organization_id"])
            resolved = timezones.resolve(str(data.get("timezone") or data.get("place") or current["timezone"]), fallback=current["timezone"])
            conn.execute(
                "UPDATE locations SET name=?,concept=?,city=?,region=?,timezone=?,open_hour=?,close_hour=? WHERE id=?",
                (
                    str(data.get("name") or current["name"])[:120],
                    str(data.get("concept") or current["concept"])[:80],
                    str(data.get("city") or current["city"])[:80],
                    str(data.get("region") or current["region"])[:40],
                    resolved["timezone"],
                    *_trading_hours({
                        "open_hour": data.get("open_hour", current["open_hour"]),
                        "close_hour": data.get("close_hour", current["close_hour"]),
                    }),
                    location_id,
                ),
            )
            conn.commit()
            self.json_response({"ok": True, "timezone": resolved})
            return True

        if path == "/api/email/preview" and method == "GET":
            target = parse_date((query.get("date") or [today.isoformat()])[0])
            built = build_email(conn, location_id, target)
            self.json_response({"subject": built["subject"], "html": built["html"], "text": built["text"]})
            return True

        if path == "/api/email/preferences" and method in {"POST", "PUT"}:
            data = self._read_json()
            location = _location(conn, location_id, session["organization_id"])
            # The zone is the location's. A zone sent with the form is ignored.
            saved = update_preferences(
                conn, location_id, str(data.get("owner_email", "")), bool(data.get("enabled", True)),
                str(data.get("send_time", "05:30")), location["timezone"], bool(data.get("include_week_ahead", True)),
            )
            saved.pop("timezone", None)
            saved["provider_connected"] = mail_provider() != "outbox"
            self.json_response(saved)
            return True

        if path == "/api/email/send-test" and method == "POST":
            data = self._read_json()
            target = parse_date(str(data.get("date") or today.isoformat()))
            # Only addresses already on this account, checked on the address
            # the test would actually go to. Otherwise anyone who signs up can
            # send Quantify-branded mail to a stranger.
            saved_to = (email_preferences(conn, location_id)["owner_email"] or "").strip().lower()
            allowed = {session["email"].lower(), saved_to} - {""}
            requested = str(data.get("recipient") or "").strip().lower()
            recipient = requested or saved_to
            if not recipient:
                raise ValueError("Add an address for the morning email first")
            if recipient not in allowed:
                raise ValueError("Test emails only go to an address already on this account")
            self.json_response(deliver_brief(conn, ROOT, location_id, target, recipient))
            return True

        if path == "/api/forecast/override" and method in {"POST", "PATCH"}:
            data = self._read_json()
            self.json_response(set_override(
                conn, location_id, str(data.get("item_id", "")), parse_date(str(data.get("date", ""))),
                _int(data.get("quantity"), None, 0, 100_000, "The number to make"), str(data.get("reason", "")),
            ))
            return True

        if path == "/api/forecast/override" and method == "DELETE":
            data = self._read_json()
            clear_override(conn, location_id, str(data.get("item_id", "")), parse_date(str(data.get("date", ""))))
            self.json_response({"ok": True})
            return True

        if path.startswith("/api/integrations/") and path.endswith("/sync") and method == "POST":
            provider = path.split("/")[3]
            data = self._read_json()
            backfill = _int(data.get("backfill_days"), 1095, 1, 1825, "Days of history")
            if provider == "weather":
                self.json_response(refresh_weather(conn, location_id, days=_int(data.get("days"), 16, 1, 16, "Days"), backfill_days=backfill))
            elif provider == "events":
                self.json_response(refresh_events(conn, location_id, days=_int(data.get("days"), 90, 1, 365, "Days"), backfill_days=backfill))
            elif provider in {"pos", "square"}:
                self.json_response(sync_square_orders(conn, location_id, _int(data.get("days"), 1095, 1, 1095, "Days of history")))
            else:
                raise ValueError("That connector is not available in this build")
            return True

        if path == "/api/integrations/pos/credentials" and method == "POST":
            data = self._read_json()
            self.json_response(save_square_credentials(
                conn, location_id, str(data.get("access_token", "")), str(data.get("location_id", "")),
                str(data.get("environment") or "production"),
            ))
            return True

        if path == "/api/demo/reset" and method == "POST":
            # seed_demo() truncates every account in the database, so it is not
            # something a route should ever do. Kept behind an explicit flag and
            # refused outright once more than one account exists.
            if os.getenv("QUANTIFY_ALLOW_DEMO_RESET", "0") != "1":
                raise PermissionError("Sample data reset is switched off. Use: python server.py --reset")
            accounts = int(conn.execute("SELECT COUNT(*) AS n FROM users WHERE active=1").fetchone()["n"])
            if accounts > 1:
                raise PermissionError("This database has more than one account, so the sample reset is refused")
            seed_demo(conn, today=date.today())
            self.json_response({"ok": True})
            return True

        return False

    def serve_static(self, request_path: str) -> None:
        """A file under web/, or the shell for an in-app route.

        A path with an extension is a file: when it is not there the answer
        is a 404, so a stale or mistyped asset fails where it can be seen
        instead of loading the landing page as a script. A path without one
        is a screen the browser routes itself, and gets index.html.
        """
        relative = request_path.lstrip("/") or "index.html"
        if request_path == "/favicon.ico":
            relative = "assets/favicon.svg"
        target = (WEB_ROOT / relative).resolve()
        try:
            target.relative_to(WEB_ROOT.resolve())
        except ValueError:
            self.json_response({"error": "Not found"}, 404)
            return
        if not target.is_file():
            if Path(relative).suffix:
                self.json_response({"error": "Not found"}, 404)
                return
            target = WEB_ROOT / "index.html"
        content, etag = _static_file(target)
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {"application/javascript", "application/json", "image/svg+xml"}:
            content_type += "; charset=utf-8"
        validators = {"ETag": etag, "Cache-Control": "no-cache"}
        held = [tag.strip() for tag in self.headers.get("If-None-Match", "").split(",")]
        if etag in held or f"W/{etag}" in held:
            try:
                self._headers(content_type, 0, 304, validators)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                self.close_connection = True
                return
            self._write(b"")
            return
        try:
            self._headers(content_type, len(content), 200, validators)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            self.close_connection = True
            return
        self._write(content)


class BackgroundScorer:
    """Scores closed days against what the model said, a chunk at a time.

    This runs behind the interface so the accuracy record fills itself in
    without anyone pressing a button, and so the first page load is never
    waiting on a backtest.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._status = {"running": False, "scored": 0, "location": None, "finished": False}
        self._stop = threading.Event()

    def state(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._status)

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        while not self._stop.wait(2.0):
            try:
                with connect(DB_PATH) as conn:
                    work = next(transactions.iter_scoring_targets(conn, chunk=14), None)
                    if work is None:
                        with self._lock:
                            self._status.update({"running": False, "finished": True, "location": None})
                        self._stop.wait(120)
                        continue
                    location_id, start, end = work
                    with self._lock:
                        self._status.update({"running": True, "finished": False, "location": location_id})
                    scored = transactions.score_range(conn, location_id, start, end)
                    with self._lock:
                        self._status["scored"] = self._status.get("scored", 0) + scored
            except Exception:
                if os.getenv("QUANTIFY_DEBUG", "0") == "1":
                    traceback.print_exc()
                self._stop.wait(30)


class Reforecaster:
    """Revises the running day at the top of every service hour.

    Nothing is refitted here. What the model knows about Tuesdays has not
    changed since this morning. What has changed is whether this Tuesday is
    running to the shape a Tuesday has at this location, and that is the only
    thing the revision uses.

    The morning call is never touched. It is written once before the doors open
    and every accuracy figure in the product is measured against it, so a day
    that was called badly stays called badly on the record no matter how well
    the revisions caught up.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._status = {"revisions": 0, "at": None, "locations": 0}
        self._stop = threading.Event()

    def state(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._status)

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        # Every four minutes, so a completed hour is picked up within a few
        # minutes of finishing without the loop being busy.
        while not self._stop.wait(240):
            try:
                with connect(DB_PATH) as conn:
                    rows = conn.execute("SELECT id FROM locations WHERE active=1").fetchall()
                    written = 0
                    for row in rows:
                        try:
                            written += intraday.catch_up(conn, row["id"])
                        except Exception:
                            if os.getenv("QUANTIFY_DEBUG", "0") == "1":
                                traceback.print_exc()
                    with self._lock:
                        self._status = {
                            "revisions": self._status.get("revisions", 0) + written,
                            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                            "locations": len(rows),
                        }
            except Exception:
                if os.getenv("QUANTIFY_DEBUG", "0") == "1":
                    traceback.print_exc()
                self._stop.wait(60)


SCORER = BackgroundScorer()
REFORECASTER = Reforecaster()


class QuantifyServer(ThreadingHTTPServer):
    """The listening socket, tuned for a room full of tablets.

    A backlog of 64 instead of the library's 5 means the eleven-second polls
    from several devices lining up do not get their connection reset, and
    daemon threads mean a stuck request never keeps the process alive.
    """

    request_queue_size = 64
    daemon_threads = True
    allow_reuse_address = True


def _warm_up(db_path: Path) -> None:
    """Compute today's plan for every location once, so the first open of the day is warm.

    Runs on its own thread right after the socket opens. Nothing here can stop
    the server: every location is wrapped, and so is the import.
    """
    try:
        from quantify_app.intelligence import daily_brief as _brief
        with connect(db_path) as conn:
            ids = [row["id"] for row in conn.execute("SELECT id FROM locations WHERE active=1 ORDER BY name").fetchall()]
        for location_id in ids:
            try:
                with connect(db_path) as conn:
                    _brief(conn, location_id, _location_today(conn, location_id), week_days=7)
            except Exception:  # noqa: BLE001 - a bad location is skipped, the rest still warm
                if os.getenv("QUANTIFY_DEBUG", "0") == "1":
                    traceback.print_exc()
    except Exception:  # noqa: BLE001
        if os.getenv("QUANTIFY_DEBUG", "0") == "1":
            traceback.print_exc()


def _scheduler(stop: threading.Event) -> None:
    while not stop.wait(45):
        try:
            with connect(DB_PATH) as conn:
                send_due_briefs(conn, ROOT)
        except Exception:
            if os.getenv("QUANTIFY_DEBUG", "0") == "1":
                traceback.print_exc()


def _remove_account(conn: sqlite3.Connection, email: str) -> None:
    email = email.strip().lower()
    row = conn.execute(
        "SELECT id,display_name,organization_id FROM users WHERE email=?", (email,)
    ).fetchone()
    if row is None:
        print(f"\nNo account here uses {email}.\n")
        return
    others = int(conn.execute(
        "SELECT COUNT(*) AS n FROM users WHERE organization_id=? AND id<>?",
        (row["organization_id"], row["id"]),
    ).fetchone()["n"])
    conn.execute("DELETE FROM users WHERE id=?", (row["id"],))
    if not others and row["organization_id"] != "org-demo":
        # The workspace existed only for this account, so it goes too.
        conn.execute("DELETE FROM organizations WHERE id=?", (row["organization_id"],))
    conn.commit()
    print(f"\nRemoved {email} ({row['display_name']}).")
    print("Sessions, codes, and the workspace created for it are gone with it.\n")


def _accounts_report(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """SELECT u.email,u.display_name,u.email_verified,u.totp_enabled,u.last_login_at,o.name AS org
           FROM users u JOIN organizations o ON o.id=u.organization_id
           WHERE u.active=1 ORDER BY u.created_at""",
    ).fetchall()
    if not rows:
        print("\nNo accounts yet. Open the site and use Create account.\n")
        return
    print(f"\n{len(rows)} account(s) on this database:\n")
    for row in rows:
        flags = []
        flags.append("email confirmed" if row["email_verified"] else "email not confirmed")
        if row["totp_enabled"]:
            flags.append("two-step on")
        print(f"  {row['email']}")
        print(f"    {row['display_name']}, {row['org']}")
        print(f"    {', '.join(flags)}, last signed in {row['last_login_at'] or 'never'}")
    print("\nForgotten the password? Run:  python server.py --set-password EMAIL\n")


def _set_password(conn: sqlite3.Connection, email: str) -> None:
    import getpass
    from quantify_app.auth import hash_password, validate_password

    email = email.strip().lower()
    row = conn.execute("SELECT id,display_name FROM users WHERE email=? AND active=1", (email,)).fetchone()
    if row is None:
        print(f"\nNo account here uses {email}. Run --accounts to see what does exist.\n")
        return
    password = os.getenv("QUANTIFY_NEW_PASSWORD") or getpass.getpass("New password (12+ characters): ")
    try:
        validate_password(password)
    except ValueError as exc:
        print(f"\n{exc}.\n")
        return
    digest, salt = hash_password(password)
    conn.execute(
        "UPDATE users SET password_hash=?,password_salt=?,email_verified=1 WHERE id=?",
        (digest, salt, row["id"]),
    )
    conn.execute("UPDATE sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
                 (datetime.now(timezone.utc).isoformat(timespec="seconds"), row["id"]))
    conn.commit()
    print(f"\nPassword updated for {email}. Every other session was signed out.\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Quantify")
    parser.add_argument("--host", default=os.getenv("QUANTIFY_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("QUANTIFY_PORT", "8787")))
    parser.add_argument("--reset", action="store_true", help="Rebuild the sample dataset and clear all accounts")
    parser.add_argument("--open", action="store_true")
    parser.add_argument("--accounts", action="store_true", help="List the accounts on this database and exit")
    parser.add_argument("--set-password", metavar="EMAIL", help="Set a new password for an account and exit")
    parser.add_argument("--remove-account", metavar="EMAIL", help="Delete an account and exit")
    args = parser.parse_args()

    initialize(DB_PATH)
    if args.accounts:
        with connect(DB_PATH) as conn:
            _accounts_report(conn)
        return
    if args.set_password:
        with connect(DB_PATH) as conn:
            _set_password(conn, args.set_password)
        return
    if args.remove_account:
        with connect(DB_PATH) as conn:
            _remove_account(conn, args.remove_account)
        return

    with connect(DB_PATH) as conn:
        if args.reset:
            seed_demo(conn, today=date.today())
        else:
            seed_if_empty(conn, today=date.today())

    stop = threading.Event()
    if os.getenv("QUANTIFY_DISABLE_SCHEDULER", "0") != "1":
        threading.Thread(target=_scheduler, args=(stop,), name="quantify-email", daemon=True).start()
        threading.Thread(target=SCORER.run, name="quantify-scorer", daemon=True).start()
        threading.Thread(target=REFORECASTER.run, name="quantify-reforecast", daemon=True).start()

    server = QuantifyServer((args.host, args.port), QuantifyHandler)
    threading.Thread(target=_warm_up, args=(DB_PATH,), name="quantify-warm-up", daemon=True).start()
    writer = ai.status()
    print("\nQuantify is running.")
    print(f"  Open        http://{args.host}:{args.port}")
    print(f"  Database    {DB_PATH}")
    print(f"  Written by  {writer['model'] or 'Quantify (no AI key set)'}")
    print("  The morning email and the accuracy scorer run while this window is open.\n")
    if args.open:
        threading.Timer(0.7, lambda: webbrowser.open(f"http://{args.host}:{args.port}")).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Quantify.")
    finally:
        stop.set()
        SCORER.stop()
        REFORECASTER.stop()
        server.server_close()


if __name__ == "__main__":
    main()
