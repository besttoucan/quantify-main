# Quantify Architecture

## System shape

```text
POS catalog + completed orders + live webhooks
                         │
                         ▼
              Normalization and deduplication
                         │
        ┌────────────────┼────────────────┐
        ▼                ▼                ▼
  item/day sales   item/hour sales   menu interpretation
        │                │                │
        └──────────────┬─┴────────────────┘
                       ▼
       weather + calendar + broad event context
                       │
                       ▼
       item-specific explainable ensemble model
                       │
          ┌────────────┼─────────────┐
          ▼            ▼             ▼
        Brief       Outlook       Results
          │
          ▼
   owner HTML/text email
```

## Runtime

- Python 3.11+ standard library
- SQLite with WAL mode
- Browser-native JavaScript
- Responsive HTML/CSS
- No npm runtime or external framework required

## Data ingestion

### POS catalog

Catalog variations are stored as stable `pos_item_id` values. The raw menu label remains the source of truth. The interpretation layer creates a normalized display label and broad operational family without overwriting the original.

### POS orders

`pos_order_lines` is the idempotent raw-normalization boundary. Its composite primary key prevents duplicate provider/order/line records. When an order changes, Quantify removes the prior normalized lines for that order, inserts the current completed state, and rebuilds only the affected item/day aggregates.

Daily and hourly aggregates are stored separately for fast model and service-curve access.

### Webhooks

Square webhook signatures are verified with HMAC-SHA256 over the public notification URL plus exact raw request body. Unverified webhook data is rejected before JSON processing.

### Weather

Historical weather is fetched in date-range chunks and upcoming weather by planning horizon. Records are cached by location/date. Model variables include high/low temperature, seasonal temperature anomaly, precipitation, snow, snowfall, UV, condition, and daylight.

### Events and day context

Event records retain name, raw type, date/time, distance, attendance, relevance, and source. Known provider types are mapped to broad groups:

- sports
- concerts
- conferences
- festivals
- performing arts
- community
- calendar
- disruptions
- other

Unknown categories map to `other` and remain eligible. Event impact is continuous:

```text
relevance × attendance scale × distance decay × service-time overlap
```

There is no hard two-mile cutoff. The model uses both group-specific loads and total event pressure.

Calendar context includes weekday, seasonal phase, holiday/occasion indicators, holiday eve/aftermath, long-weekend behavior, pay-cycle timing, month end, and daylight.

## Menu intelligence

`menu_intelligence.py` normalizes punctuation and common POS abbreviations, then scores broad restaurant-item families using label, category, and modifiers.

Output includes:

- Raw name
- Normalized name
- Broad item family
- Daypart
- Production unit
- Directional material families
- Confidence
- Optional-review flag

The layer never fabricates an exact recipe. Unknown labels remain `menu-item` and `forecast_ready=true`.

## Forecast model

For each item/date, Quantify builds:

1. A weighted same-weekday baseline
2. A regularized regression over calendar, weather, event, and trend features
3. A nearest-analog estimate from contextually similar historical days

Candidate components are evaluated on held-out history. Validation performance determines ensemble weights. The output includes expected quantity, lower/upper likely range, confidence, drivers, model diagnostics, and comparable dates.

The item models are cached by database identity, location, item, target horizon, and data version. Cache entries invalidate when relevant sales/context data changes.

## Hourly curve

The service curve learns the target weekday's historical hourly distribution, with fallbacks to broader history. It allocates expected units and revenue across the location's open hours.

## Results

Results executes a walk-forward backtest. Each evaluation date uses only prior observations, preventing future leakage. Accuracy is based on POS item units and weighted absolute percentage error.

## Database groups

### Tenant and operating data

- `organizations`
- `locations`
- `menu_items`
- `menu_interpretations`
- `sales`
- `sales_hourly`
- `pos_order_lines`
- `weather`
- `events`
- `context_daily`
- `forecast_overrides`
- `forecast_runs`
- `integrations`
- `settings`

### Email

- `email_preferences`
- `email_deliveries`

### Security

- `users`
- `sessions`
- `auth_challenges`
- `recovery_codes`
- `security_events`


## Authentication and authorization

The local package supports one organization and an owner account, but all operating lookups are scoped by organization and location. Passwords use PBKDF2-HMAC-SHA256 with a unique salt. TOTP secrets are generated per user, and recovery codes are stored only as hashes. The local database stores the TOTP secret because verification requires it; a hosted deployment should encrypt that field with managed application keys and rotate those keys operationally.

State-changing requests require a valid session and matching CSRF token. Sessions use opaque random bearer values; only their hashes are stored.

A hosted multi-tenant deployment must add formal tenant-isolation tests, role-based permissions, encrypted provider-secret storage, secure-cookie enforcement, key rotation, audit retention, and operational monitoring.

## Email scheduler

A lightweight local thread checks due email preferences while the application is running. It uses each location's IANA time zone and sends at most once per local forecast date.

Production should move this function into an always-on job queue or scheduled worker with retries, delivery metrics, dead-letter handling, and provider webhooks.
