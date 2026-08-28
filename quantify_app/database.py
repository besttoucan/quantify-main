from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA_VERSION = "6"

SCHEMA = r"""
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS organizations (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    plan TEXT NOT NULL DEFAULT 'signal',
    created_at TEXT NOT NULL,
    concept TEXT NOT NULL DEFAULT '',
    location_count TEXT NOT NULL DEFAULT '',
    primary_goal TEXT NOT NULL DEFAULT '',
    pos_provider TEXT NOT NULL DEFAULT '',
    onboarded_at TEXT
);

CREATE TABLE IF NOT EXISTS locations (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    concept TEXT NOT NULL,
    address TEXT NOT NULL,
    city TEXT NOT NULL,
    region TEXT NOT NULL,
    postal_code TEXT NOT NULL,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    timezone TEXT NOT NULL,
    open_hour INTEGER NOT NULL,
    close_hour INTEGER NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD',
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS menu_items (
    id TEXT PRIMARY KEY,
    location_id TEXT NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
    pos_item_id TEXT,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    price REAL NOT NULL,
    base_daily_qty REAL NOT NULL DEFAULT 1,
    active INTEGER NOT NULL DEFAULT 1,
    UNIQUE(location_id, pos_item_id)
);

CREATE TABLE IF NOT EXISTS menu_interpretations (
    menu_item_id TEXT PRIMARY KEY REFERENCES menu_items(id) ON DELETE CASCADE,
    raw_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    item_family TEXT NOT NULL,
    daypart TEXT NOT NULL,
    production_unit TEXT NOT NULL,
    confidence REAL NOT NULL,
    source TEXT NOT NULL,
    inferred_json TEXT NOT NULL DEFAULT '{}',
    reviewed INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sales (
    location_id TEXT NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
    item_id TEXT NOT NULL REFERENCES menu_items(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    revenue REAL NOT NULL,
    dine_in_share REAL NOT NULL DEFAULT 0,
    delivery_share REAL NOT NULL DEFAULT 0,
    stockout_minutes INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(location_id, item_id, date)
);

CREATE TABLE IF NOT EXISTS sales_hourly (
    location_id TEXT NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
    item_id TEXT NOT NULL REFERENCES menu_items(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    hour INTEGER NOT NULL,
    quantity REAL NOT NULL,
    revenue REAL NOT NULL,
    channel TEXT NOT NULL DEFAULT 'unknown',
    PRIMARY KEY(location_id, item_id, date, hour, channel)
);

CREATE TABLE IF NOT EXISTS pos_order_lines (
    provider TEXT NOT NULL,
    location_id TEXT NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
    provider_order_id TEXT NOT NULL,
    provider_line_id TEXT NOT NULL,
    item_id TEXT NOT NULL REFERENCES menu_items(id) ON DELETE CASCADE,
    sale_date TEXT NOT NULL,
    sale_hour INTEGER NOT NULL,
    quantity REAL NOT NULL,
    revenue REAL NOT NULL,
    channel TEXT NOT NULL DEFAULT 'unknown',
    order_state TEXT NOT NULL DEFAULT 'OPEN',
    payload_hash TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(provider, location_id, provider_order_id, provider_line_id)
);

CREATE TABLE IF NOT EXISTS weather (
    location_id TEXT NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    temp_high REAL NOT NULL,
    temp_low REAL NOT NULL,
    precipitation_mm REAL NOT NULL,
    snowfall_cm REAL NOT NULL DEFAULT 0,
    uv_index REAL NOT NULL DEFAULT 0,
    condition TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'demo',
    PRIMARY KEY(location_id, date)
);

CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    location_id TEXT NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    event_type TEXT NOT NULL,
    date TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    distance_miles REAL NOT NULL,
    attendance INTEGER NOT NULL,
    relevance REAL NOT NULL,
    source TEXT NOT NULL,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS context_daily (
    location_id TEXT NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    features_json TEXT NOT NULL,
    sources_json TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(location_id, date)
);

CREATE TABLE IF NOT EXISTS forecast_overrides (
    location_id TEXT NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
    item_id TEXT NOT NULL REFERENCES menu_items(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    reason TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(location_id, item_id, date)
);

CREATE TABLE IF NOT EXISTS forecast_runs (
    id TEXT PRIMARY KEY,
    location_id TEXT NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
    target_date TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    model_version TEXT NOT NULL,
    history_days INTEGER NOT NULL,
    context_json TEXT NOT NULL,
    summary_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS integrations (
    location_id TEXT NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    status TEXT NOT NULL,
    last_sync TEXT,
    mode TEXT NOT NULL DEFAULT 'demo',
    details TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY(location_id, provider)
);

CREATE TABLE IF NOT EXISTS settings (
    location_id TEXT NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY(location_id, key)
);

CREATE TABLE IF NOT EXISTS email_preferences (
    location_id TEXT PRIMARY KEY REFERENCES locations(id) ON DELETE CASCADE,
    owner_email TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    send_time TEXT NOT NULL DEFAULT '05:30',
    timezone TEXT NOT NULL,
    include_week_ahead INTEGER NOT NULL DEFAULT 1,
    last_sent_date TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS email_deliveries (
    id TEXT PRIMARY KEY,
    location_id TEXT NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
    recipient TEXT NOT NULL,
    forecast_date TEXT NOT NULL,
    subject TEXT NOT NULL,
    status TEXT NOT NULL,
    provider TEXT NOT NULL,
    artifact_path TEXT,
    provider_message_id TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    sent_at TEXT
);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    email TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    totp_secret TEXT,
    totp_enabled INTEGER NOT NULL DEFAULT 0,
    email_verified INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS email_verifications (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    email TEXT NOT NULL,
    code_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    used_at TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    csrf_token TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    ip_hash TEXT,
    user_agent TEXT,
    revoked_at TEXT
);

CREATE TABLE IF NOT EXISTS auth_challenges (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    used_at TEXT
);

CREATE TABLE IF NOT EXISTS recovery_codes (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    code_hash TEXT NOT NULL,
    used_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS security_events (
    id TEXT PRIMARY KEY,
    user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    event_type TEXT NOT NULL,
    ip_hash TEXT,
    details TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pos_orders (
    provider_order_id TEXT NOT NULL,
    location_id TEXT NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
    provider TEXT NOT NULL DEFAULT 'square',
    order_number TEXT,
    sale_date TEXT NOT NULL,
    sale_time TEXT NOT NULL,
    sale_hour INTEGER NOT NULL,
    channel TEXT NOT NULL DEFAULT 'Counter',
    payment_type TEXT NOT NULL DEFAULT 'Card',
    subtotal REAL NOT NULL DEFAULT 0,
    tax REAL NOT NULL DEFAULT 0,
    tip REAL NOT NULL DEFAULT 0,
    total REAL NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(location_id, provider_order_id)
);

CREATE TABLE IF NOT EXISTS day_accuracy (
    location_id TEXT NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    predicted_units REAL NOT NULL,
    actual_units REAL NOT NULL,
    predicted_sales REAL NOT NULL,
    actual_sales REAL NOT NULL,
    accuracy REAL NOT NULL,
    items_json TEXT NOT NULL DEFAULT '[]',
    hourly_json TEXT NOT NULL DEFAULT '[]',
    conditions_json TEXT NOT NULL DEFAULT '{}',
    scored_at TEXT NOT NULL,
    call_source TEXT NOT NULL DEFAULT 'reconstructed',
    caught_slot INTEGER NOT NULL DEFAULT -1,
    revisions_used INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(location_id, date)
);

CREATE TABLE IF NOT EXISTS forecast_calls (
    location_id TEXT NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    item_id TEXT NOT NULL REFERENCES menu_items(id) ON DELETE CASCADE,
    expected REAL NOT NULL,
    lower REAL NOT NULL,
    upper REAL NOT NULL,
    price REAL NOT NULL,
    -- 1 when a manager override was in force when the call was made. The call
    -- is what Quantify said, override included, because that is the number the
    -- kitchen prepped to. The flag keeps it visible on the day sheet.
    overridden INTEGER NOT NULL DEFAULT 0,
    validation_wape REAL NOT NULL DEFAULT 0.3,
    model_version TEXT NOT NULL,
    locked_at TEXT NOT NULL,
    locked_local TEXT NOT NULL,
    PRIMARY KEY(location_id, date, item_id)
);

-- Blocks UPDATE and ON CONFLICT DO UPDATE. It does not block INSERT OR REPLACE,
-- which SQLite implements as DELETE then INSERT, and it does not block DELETE,
-- because seeding has to be able to truncate. It is a guard against the
-- ordinary mistake, not a vault.
CREATE TRIGGER IF NOT EXISTS forecast_calls_are_final
BEFORE UPDATE ON forecast_calls
BEGIN
    SELECT RAISE(ABORT, 'The call made before service is final and cannot be rewritten');
END;

CREATE TABLE IF NOT EXISTS forecast_revisions (
    location_id TEXT NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    slot INTEGER NOT NULL,
    item_id TEXT NOT NULL REFERENCES menu_items(id) ON DELETE CASCADE,
    opening REAL NOT NULL,
    sold_so_far REAL NOT NULL,
    expected_share REAL NOT NULL,
    pace REAL NOT NULL,
    weight REAL NOT NULL,
    revised REAL NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(location_id, date, slot, item_id)
);

CREATE TABLE IF NOT EXISTS item_composition (
    menu_item_id TEXT PRIMARY KEY REFERENCES menu_items(id) ON DELETE CASCADE,
    summary TEXT NOT NULL,
    confidence TEXT NOT NULL,
    verify_note TEXT NOT NULL DEFAULT '',
    components_json TEXT NOT NULL DEFAULT '[]',
    writer TEXT NOT NULL DEFAULT 'local',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_generations (
    task TEXT NOT NULL,
    subject TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    writer TEXT NOT NULL,
    model TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(task, subject)
);

CREATE TABLE IF NOT EXISTS subscriptions (
    organization_id TEXT PRIMARY KEY REFERENCES organizations(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'trialing',
    plan TEXT NOT NULL DEFAULT 'standard',
    seats INTEGER NOT NULL DEFAULT 1,
    provider TEXT NOT NULL DEFAULT 'local',
    provider_customer_id TEXT,
    provider_subscription_id TEXT,
    current_period_end TEXT,
    trial_end TEXT,
    cancel_at_period_end INTEGER NOT NULL DEFAULT 0,
    canceled_at TEXT,
    payment_brand TEXT,
    payment_last4 TEXT,
    payment_expires TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS billing_events (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cancellation_feedback (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id TEXT,
    reason_code TEXT NOT NULL,
    reason_detail TEXT NOT NULL DEFAULT '',
    wants_contact INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cost_settings (
    location_id TEXT PRIMARY KEY REFERENCES locations(id) ON DELETE CASCADE,
    -- 0 means "follow the local minimum". Storing the source separately would
    -- be a second copy of the same fact, and the two would drift.
    hourly_wage REAL NOT NULL DEFAULT 0,
    payroll_load_percent REAL NOT NULL DEFAULT 18,
    orders_per_person_per_hour REAL NOT NULL DEFAULT 6,
    min_staff INTEGER NOT NULL DEFAULT 3,
    max_staff INTEGER NOT NULL DEFAULT 14,
    prep_hours REAL NOT NULL DEFAULT 1.5,
    close_hours REAL NOT NULL DEFAULT 1,
    default_cost_share REAL NOT NULL DEFAULT 0.30,
    updated_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS category_costs (
    location_id TEXT NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    cost_share REAL NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(location_id, category)
);

CREATE TABLE IF NOT EXISTS recurring_costs (
    id TEXT PRIMARY KEY,
    location_id TEXT NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    amount REAL NOT NULL,
    period TEXT NOT NULL DEFAULT 'month',
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sales_location_date ON sales(location_id, date);
CREATE INDEX IF NOT EXISTS idx_sales_item_date ON sales(item_id, date);
CREATE INDEX IF NOT EXISTS idx_sales_hourly_location_date ON sales_hourly(location_id, date, hour);
CREATE INDEX IF NOT EXISTS idx_events_location_date ON events(location_id, date);
CREATE INDEX IF NOT EXISTS idx_pos_order_date ON pos_order_lines(location_id, sale_date);
CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(token_hash);
CREATE INDEX IF NOT EXISTS idx_auth_challenge_token ON auth_challenges(token_hash);
CREATE INDEX IF NOT EXISTS idx_email_due ON email_preferences(enabled, send_time);
CREATE INDEX IF NOT EXISTS idx_pos_orders_date ON pos_orders(location_id, sale_date, sale_time);
CREATE INDEX IF NOT EXISTS idx_day_accuracy_date ON day_accuracy(location_id, date DESC);
CREATE INDEX IF NOT EXISTS idx_email_verify_user ON email_verifications(user_id, used_at);
CREATE INDEX IF NOT EXISTS idx_forecast_calls_day ON forecast_calls(location_id, date);
CREATE INDEX IF NOT EXISTS idx_forecast_revisions_day ON forecast_revisions(location_id, date, slot);
CREATE INDEX IF NOT EXISTS idx_recurring_costs_location ON recurring_costs(location_id);
"""

# Columns added after the first release. SQLite has no "add column if missing",
# so each one is checked against the live table before it is applied.
ADDITIVE_COLUMNS: list[tuple[str, str, str]] = [
    ("weather", "snowfall_cm", "REAL NOT NULL DEFAULT 0"),
    ("weather", "uv_index", "REAL NOT NULL DEFAULT 0"),
    ("organizations", "concept", "TEXT NOT NULL DEFAULT ''"),
    ("organizations", "location_count", "TEXT NOT NULL DEFAULT ''"),
    ("organizations", "primary_goal", "TEXT NOT NULL DEFAULT ''"),
    ("organizations", "pos_provider", "TEXT NOT NULL DEFAULT ''"),
    ("organizations", "onboarded_at", "TEXT"),
    # Accounts that existed before email confirmation was added are already
    # trusted, so the added column defaults to verified for them. New rows are
    # inserted with 0 explicitly.
    ("users", "email_verified", "INTEGER NOT NULL DEFAULT 1"),
    # The day score is always the call made before service. These record what
    # the revisions during the day did, kept out of the score itself. There is
    # deliberately no second accuracy column: a revision that has already
    # watched half the day would score higher than the morning call every time,
    # and printing that beside the call would make the record worthless.
    # Rows that predate this default to 'reconstructed' correctly, because that
    # is exactly how they were produced.
    ("day_accuracy", "call_source", "TEXT NOT NULL DEFAULT 'reconstructed'"),
    ("day_accuracy", "caught_slot", "INTEGER NOT NULL DEFAULT -1"),
    ("day_accuracy", "revisions_used", "INTEGER NOT NULL DEFAULT 0"),
]


class QuantifyConnection(sqlite3.Connection):
    """SQLite connection that closes itself when used as a context manager."""

    def __enter__(self) -> "QuantifyConnection":
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        try:
            if exc_type is None:
                self.commit()
            else:
                self.rollback()
        finally:
            self.close()
        return False


def connect(db_path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30, factory=QuantifyConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def initialize(db_path: Path | str) -> None:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as conn:
        conn.executescript(SCHEMA)
        for table, column, definition in ADDITIVE_COLUMNS:
            existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if existing and column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        conn.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES('schema_version', ?)",
            (SCHEMA_VERSION,),
        )
        conn.commit()


@contextmanager
def transaction(db_path: Path | str) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def table_count(conn: sqlite3.Connection, table: str) -> int:
    allowed = {
        "organizations", "locations", "menu_items", "menu_interpretations",
        "weather", "events", "context_daily", "sales", "sales_hourly",
        "pos_order_lines", "integrations", "settings", "users", "sessions",
        "email_preferences", "email_deliveries", "forecast_runs", "pos_orders",
        "day_accuracy", "item_composition", "ai_generations", "subscriptions",
        "billing_events", "cancellation_feedback", "email_verifications",
        "forecast_calls", "forecast_revisions",
        "cost_settings", "category_costs", "recurring_costs",
    }
    if table not in allowed:
        raise ValueError(f"Unsupported table: {table}")
    row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
    return int(row["n"])
