# Live Setup

## Local configuration

Windows users should copy `config.example.bat` to `config.bat`. macOS/Linux users should copy `.env.example` to `.env`.

Never commit the live configuration file.

## Server and security

```text
QUANTIFY_HOST=127.0.0.1
QUANTIFY_PORT=8787
QUANTIFY_DB=/absolute/path/to/data/quantify.db
QUANTIFY_SECURITY_PEPPER=<long-random-server-secret>
QUANTIFY_SECURE_COOKIES=0
QUANTIFY_TRUST_PROXY=0
```

Use `QUANTIFY_SECURE_COOKIES=1` only behind HTTPS. Do not expose the local server directly to the public internet.

Leave `QUANTIFY_TRUST_PROXY=0` for local or direct deployments. Set it to `1` only when a controlled reverse proxy is the sole path to Quantify and it overwrites forwarded-client headers.

`QUANTIFY_AUTH_BYPASS=1` exists only for automated screenshot/testing workflows and must never be used in a live environment.

## Square

```text
SQUARE_ACCESS_TOKEN=
SQUARE_LOCATION_ID=
SQUARE_ENVIRONMENT=production
SQUARE_VERSION=2026-07-15
SQUARE_MAX_PAGES=250
SQUARE_WEBHOOK_SIGNATURE_KEY=
SQUARE_WEBHOOK_NOTIFICATION_URL=https://app.example.com/api/webhooks/square?location_id=<internal-location-id>
QUANTIFY_SQUARE_INTERNAL_LOCATION=<internal-location-id>
```

The access token and Square location ID must belong to the restaurant's authorized merchant account. The local package supports a directly configured merchant connection. A marketplace product must implement per-merchant OAuth, encrypted refreshable credentials, revocation, and installation state.

The notification URL must exactly match the URL registered with Square because it is part of signature verification.

## Weather

```text
OPEN_METEO_BASE_URL=https://api.open-meteo.com/v1/forecast
OPEN_METEO_ARCHIVE_URL=https://archive-api.open-meteo.com/v1/archive
OPEN_METEO_API_KEY=
```

The connector requests daily high/low temperature, precipitation, snowfall, UV, and weather code. Historical data is requested in chunks and cached by location/date.

Review the chosen provider's commercial-use terms before public launch.

## Events

### PredictHQ

```text
PREDICTHQ_ACCESS_TOKEN=
PREDICTHQ_EVENTS_URL=https://api.predicthq.com/v1/events/
QUANTIFY_EVENT_RADIUS_MILES=15
```

The configured radius is the retrieval area, not a hard model cutoff. Once retrieved, every event is ranked by distance, size, relevance, and service-time overlap. Unknown event categories remain eligible through the `other` feature family.

PredictHQ is the preferred path when historical and broad real-world context are required.

### Ticketmaster fallback

```text
TICKETMASTER_API_KEY=
```

Ticketmaster is useful for upcoming ticketed events but should not be treated as a complete historical neighborhood-context source.

## Daily email

### Postmark

```text
POSTMARK_SERVER_TOKEN=
QUANTIFY_FROM_EMAIL=briefs@yourdomain.com
```

### SMTP

```text
SMTP_HOST=
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_STARTTLS=1
SMTP_SSL=0
QUANTIFY_FROM_EMAIL=briefs@yourdomain.com
```

Configure SPF, DKIM, and DMARC for the sender domain. Without Postmark or SMTP, the application writes `.eml` messages to `data/outbox`.

## Provider synchronization order

For a new location:

1. Create/authorize the POS connection.
2. Synchronize the catalog.
3. Synchronize available historical orders, requesting up to 1,095 days.
4. Backfill historical weather for the actual sales period.
5. Backfill historical event context and obtain upcoming context.
6. Review menu-interpretation confidence; do not require exhaustive manual correction.
7. Configure and test the owner email.
8. Register and verify POS webhooks for live updates.
9. Review Results only after enough live forecast/actual dates exist.

## Hosted production requirements

A public product should add:

- HTTPS reverse proxy and secure cookies
- OAuth installation per merchant
- Managed relational database and encrypted backups
- Encrypted secret manager
- Background job queue
- Retry and idempotency policy for all providers
- Central logs, metrics, alerts, and tracing
- Provider rate-limit handling
- Multi-tenant authorization tests
- Email suppression/bounce processing
- Disaster recovery and incident response
