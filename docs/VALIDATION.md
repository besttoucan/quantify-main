# Quantify 2.0 Validation

## Automated suite

Run:

```text
python -m unittest discover -s tests -v
```

Validated behaviors:

1. Two years of daily item history and complete hourly service data
2. Broad event-category coverage
3. Unknown event categories remain eligible
4. Continuous distance decay with no two-mile cutoff
5. Rough POS menu labels remain forecast-ready
6. Fourteen-day outlook generation
7. Daily owner email HTML/text generation
8. Safe `.eml` outbox delivery
9. Owner password, mandatory TOTP, login challenge, and session creation
10. Official Square-style webhook signature verification
11. Idempotent Square order-line ingestion and daily/hourly rebuild
12. Walk-forward accuracy reporting without invented savings

## Static checks

```text
python -m py_compile server.py quantify_app/*.py
node --check web/api.js
node --check web/app.js
```

## Visual checks

Regenerate screenshots with:

```text
python tools/render_screenshots.py
```

Review:

- `docs/screenshots/brief.png`
- `docs/screenshots/brief-ipad.png`
- `docs/screenshots/outlook.png`
- `docs/screenshots/results.png`
- `docs/screenshots/setup-connections.png`
- `docs/screenshots/setup-email.png`
- `docs/screenshots/setup-menu.png`

The screenshot harness renders the production interface with deterministic fixture APIs and fails on browser console/page errors.

## Release checklist

- [x] Automated tests pass
- [x] Python and JavaScript syntax checks pass
- [x] Clean database first run succeeds
- [x] Owner setup and TOTP flow succeeds
- [x] Operating APIs remain blocked until TOTP is enabled
- [x] TOTP enrollment secret is redacted after activation
- [x] Brief, Outlook, Results, and Setup load
- [x] Daily email preview renders
- [x] Safe outbox test creates a readable `.eml`
- [x] Square signature test passes
- [x] No legacy inventory/expiry/order page remains in navigation
- [x] Documentation matches current routes and environment variables
- [x] Archive integrity test passes
- [x] SHA-256 checksum generated

## Live pilot acceptance

Before calling a restaurant live:

- Confirm catalog mapping and completed-order coverage
- Confirm local time-zone conversion
- Confirm historical start/end dates
- Confirm weather and context coverage
- Confirm webhook delivery and signature verification
- Confirm email delivery, SPF/DKIM, and send time
- Compare at least 14 forecast dates with actuals
- Document closures, promotions, major menu changes, and data outages
- Never claim waste savings without an auditable source
