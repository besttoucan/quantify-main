# Local API Reference

All routes except `/api/health`, initial authentication routes, and the verified Square webhook require an authenticated session. State-changing routes require the session CSRF token in `X-CSRF-Token`.

## Health

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/health` | Version and server health |

## Authentication

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/auth/state` | First-run/authentication state |
| POST | `/api/auth/setup` | Create the first owner account |
| POST | `/api/auth/login` | Verify email/password and begin MFA |
| POST | `/api/auth/verify` | Complete TOTP/recovery-code login |
| GET | `/api/auth/mfa/setup` | Authenticator setup details |
| POST | `/api/auth/totp/enable` | Verify and enable TOTP |
| POST | `/api/auth/logout` | Revoke current session |

## Product

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/bootstrap` | Organization, locations, providers, pricing |
| GET | `/api/brief?location_id=&date=YYYY-MM-DD` | Complete daily operating brief |
| GET | `/api/outlook?location_id=&start=YYYY-MM-DD&days=14` | Forecast horizon, 1–31 days |
| GET | `/api/results?location_id=&as_of=YYYY-MM-DD&days=30` | Walk-forward accuracy record |
| GET | `/api/menu?location_id=` | Interpreted menu and confidence |
| GET | `/api/setup?location_id=` | Connection, email, menu, and security setup |

## Forecast overrides

```text
POST /api/forecast/override?location_id=<id>
```

```json
{
  "item_id": "item-bakery-butter-croissant",
  "date": "2026-08-11",
  "quantity": 72,
  "reason": "Confirmed 40-person catering pickup"
}
```

Delete the same override with `DELETE` and the item/date body.

## Menu import

```text
POST /api/menu/import?location_id=<id>
```

```json
{
  "text": "DBL CHZ BRGR | Burgers | 12.50\nMARG SLC | Slices | 4.25",
  "commit": false
}
```

Use `commit=false` to preview interpretations. The normal production source is the POS catalog.

## Email

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/email/preview?location_id=&date=` | Render HTML/text owner brief |
| GET | `/api/email/deliveries?location_id=` | Last 25 delivery records |
| POST/PUT | `/api/email/preferences?location_id=` | Save recipient/time/time zone |
| POST | `/api/email/send-test?location_id=` | Deliver or create safe `.eml` test |

Preference body:

```json
{
  "owner_email": "owner@example.com",
  "enabled": true,
  "send_time": "05:30",
  "timezone": "America/New_York",
  "include_week_ahead": true
}
```

## Connections

| Method | Route | Purpose |
|---|---|---|
| POST | `/api/integrations/pos/sync?location_id=` | Square catalog/order history sync |
| POST | `/api/integrations/weather/sync?location_id=` | Historical/upcoming weather sync |
| POST | `/api/integrations/events/sync?location_id=` | Historical/upcoming context sync |
| POST | `/api/webhooks/square?location_id=` | Verified Square order webhook |

Typical sync body:

```json
{
  "days": 1095,
  "backfill_days": 1095
}
```

Provider history remains subject to account availability and provider retention.
