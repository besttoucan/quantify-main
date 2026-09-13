"""A location's recorded model usage and application spend guard.

Completed successful calls are priced using standard API rates. A recorded
monthly or daily limit switches subsequent requests to the deterministic writer.
This is not a provider invoice cap: parallel in-flight calls, billed failures,
missing usage and estimated rates can exceed the recorded allowance. Production
hard limits require reservations and provider usage reconciliation.

The $3 monthly usage scenario in the commercial plan is an assumption, not a
measured production average. Rates are USD per million tokens, reviewed on
13 September 2026. Cache multipliers describe standard five-minute caching;
the unknown-model rate is an estimate. Review before using a different service.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from typing import Any

PRICES_AS_OF = "2026-09-13"
PRICES_REVIEWED = "2026-09-13"
PRICES_SOURCE = "https://platform.claude.com/docs/en/about-claude/pricing"

# (input, output) dollars per million tokens.
PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-fable-5-1": (10.00, 50.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
}
UNKNOWN_MODEL_PRICE = (10.00, 50.00)  # cost an unknown model at the dearest rate

CACHE_READ_SHARE = 0.10
CACHE_WRITE_SHARE = 1.25

# Expected spend is about $3.00 per location per month. Six is twice that: an
# ordinary location never approaches it, and one that reaches it keeps working.
MONTHLY_CEILING_USD = 6.00
# Nothing stops here. This is the number that means a human should look.
ALERT_USD = 12.00
# Manual regenerations. Twelve is more than an operator correcting a real problem
# ever needs in one day, and far fewer than a loop makes in a minute.
FORCED_PER_DAY = 12
# One pathological catalogue should not cost more than a whole ordinary month.
MAX_PAYLOAD_TOKENS = 60_000
# Rough characters per token for the payload guard. Deliberately conservative:
# this only has to catch records that are enormous, not measure them.
CHARS_PER_TOKEN = 3.5

UNATTRIBUTED = "_unattributed"


def ensure_schema(conn: sqlite3.Connection) -> None:
    """One row per model call, so every dollar has a location and a reason."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS ai_spend (
             id INTEGER PRIMARY KEY AUTOINCREMENT,
             location_id TEXT NOT NULL,
             task TEXT NOT NULL,
             subject TEXT NOT NULL DEFAULT '',
             model TEXT NOT NULL,
             input_tokens INTEGER NOT NULL DEFAULT 0,
             cache_read_tokens INTEGER NOT NULL DEFAULT 0,
             cache_write_tokens INTEGER NOT NULL DEFAULT 0,
             output_tokens INTEGER NOT NULL DEFAULT 0,
             cost_usd REAL NOT NULL DEFAULT 0,
             forced INTEGER NOT NULL DEFAULT 0,
             created_at TEXT NOT NULL
           )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ai_spend_by_location ON ai_spend(location_id, created_at)"
    )
    conn.commit()


# ---------------------------------------------------------------------------
# What a call cost
# ---------------------------------------------------------------------------

def price_for(model: str) -> tuple[float, float]:
    return PRICES.get(model, UNKNOWN_MODEL_PRICE)


def tokens_from(usage: Any) -> dict[str, int]:
    """Read the four token counts off a response usage object or a dict.

    Every field is optional on the wire, so each one falls back to zero rather
    than raising. An undercount is a reporting problem; an exception here would
    lose a brief the customer is waiting for.
    """
    def field(name: str) -> int:
        if usage is None:
            return 0
        value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    return {
        "input_tokens": field("input_tokens"),
        "cache_read_tokens": field("cache_read_input_tokens"),
        "cache_write_tokens": field("cache_creation_input_tokens"),
        "output_tokens": field("output_tokens"),
    }


def cost_of(model: str, usage: Any) -> float:
    """Dollars for one call, cache reads and writes priced at their own rates."""
    rate_in, rate_out = price_for(model)
    counts = tokens_from(usage)
    dollars = (
        counts["input_tokens"] * rate_in
        + counts["cache_read_tokens"] * rate_in * CACHE_READ_SHARE
        + counts["cache_write_tokens"] * rate_in * CACHE_WRITE_SHARE
        + counts["output_tokens"] * rate_out
    ) / 1_000_000
    return round(dollars, 6)


def estimate_tokens(payload: Any) -> int:
    """A rough size for the record, used only to catch a pathological one."""
    import json

    try:
        text = json.dumps(payload, default=str)
    except (TypeError, ValueError):
        text = str(payload)
    return int(len(text) / CHARS_PER_TOKEN)


# ---------------------------------------------------------------------------
# What has been spent
# ---------------------------------------------------------------------------

def _month_start(today: date | None = None) -> str:
    day = today or datetime.now(timezone.utc).date()
    return day.replace(day=1).isoformat()


def record(
    conn: sqlite3.Connection,
    location_id: str,
    task: str,
    subject: str,
    model: str,
    usage: Any,
    forced: bool = False,
) -> float:
    """Write one call to the ledger and return what it cost."""
    ensure_schema(conn)
    counts = tokens_from(usage)
    dollars = cost_of(model, usage)
    conn.execute(
        """INSERT INTO ai_spend(location_id,task,subject,model,input_tokens,cache_read_tokens,
                                cache_write_tokens,output_tokens,cost_usd,forced,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (
            location_id or UNATTRIBUTED, task, subject, model,
            counts["input_tokens"], counts["cache_read_tokens"],
            counts["cache_write_tokens"], counts["output_tokens"],
            dollars, 1 if forced else 0,
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ),
    )
    conn.commit()
    return dollars


def month_to_date(conn: sqlite3.Connection, location_id: str) -> float:
    """Spend for this location since the first of the month, in UTC."""
    ensure_schema(conn)
    row = conn.execute(
        "SELECT COALESCE(SUM(cost_usd),0) AS total FROM ai_spend WHERE location_id=? AND created_at>=?",
        (location_id or UNATTRIBUTED, _month_start()),
    ).fetchone()
    return round(float(row["total"] if row else 0.0), 6)


def forced_today(conn: sqlite3.Connection, location_id: str) -> int:
    """How many manual regenerations this location has asked for today."""
    ensure_schema(conn)
    row = conn.execute(
        "SELECT COUNT(*) AS runs FROM ai_spend WHERE location_id=? AND forced=1 AND created_at>=?",
        (location_id or UNATTRIBUTED, datetime.now(timezone.utc).date().isoformat()),
    ).fetchone()
    return int(row["runs"] if row else 0)


def summary(conn: sqlite3.Connection, location_id: str) -> dict[str, Any]:
    """What this location has cost, for an operator screen or a support answer."""
    spent = month_to_date(conn, location_id)
    return {
        "spent_this_month": round(spent, 2),
        "ceiling": MONTHLY_CEILING_USD,
        "share_of_ceiling": round(min(1.0, spent / MONTHLY_CEILING_USD), 3) if MONTHLY_CEILING_USD else 0.0,
        "forced_today": forced_today(conn, location_id),
        "forced_limit": FORCED_PER_DAY,
        "needs_a_look": spent >= ALERT_USD,
    }


# ---------------------------------------------------------------------------
# Whether the next call is allowed
# ---------------------------------------------------------------------------

def decide(
    conn: sqlite3.Connection,
    location_id: str,
    forced: bool = False,
    payload: Any = None,
) -> tuple[bool, str]:
    """Say whether to call the model, and why not when the answer is no.

    The reason is recorded with the generated result so support can answer the
    question "why does this brief read differently today" without guessing. It is
    never shown to an operator: the brief they get is complete either way, and
    Article 2 of the constitution says we do not describe ourselves in the
    product.
    """
    if payload is not None:
        size = estimate_tokens(payload)
        if size > MAX_PAYLOAD_TOKENS:
            return False, f"record is {size} tokens, over the {MAX_PAYLOAD_TOKENS} limit"

    if forced and forced_today(conn, location_id) >= FORCED_PER_DAY:
        return False, f"{FORCED_PER_DAY} manual refreshes already today"

    spent = month_to_date(conn, location_id)
    if spent >= MONTHLY_CEILING_USD:
        return False, f"${spent:.2f} spent this month, ceiling is ${MONTHLY_CEILING_USD:.2f}"

    return True, ""
