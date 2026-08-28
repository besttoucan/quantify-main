# Quantify Product Guide

## Product job

Quantify answers one operational question:

> What is this location likely to sell, when will demand arrive, and what deserves attention before service begins?

It does not recreate every back-office category as a separate page. Forecasting, context, menu understanding, email, and accuracy are combined into one operating workflow.

## 1. Brief

The Brief is the default screen and the owner-email source. It is an editorial operating sheet, not a generic KPI dashboard.

### Header

The headline states the expected demand level and the clearest item signal. The four summary measures are:

- Expected sales
- Expected item units
- Peak service hour
- Forecast confidence

### What matters

Priorities are ranked, not dumped into an alert grid. Typical priorities include:

- An item materially above its normal comparable-day level
- The service hour that should be protected
- A material but unstable item whose likely range has been widened

### Service curve

The service curve distributes the day's expected revenue and units across operating hours using the location's own historical hourly shape. It is useful for pacing, batch timing, and labor discussions without creating a separate labor-scheduling product.

### Demand ledger

Every active item appears in one dense ledger with:

- Expected quantity
- Likely range
- Change versus baseline
- Confidence
- Primary explanatory signal

A row can be opened for model detail and manager override. Overrides require a reason and remain auditable.

### Context

Quantify presents only context that materially affected the output. It may show weather, calendar behavior, recent momentum, or an impact-ranked public event. It also reports how many candidate events were reviewed, preventing one cherry-picked event from appearing as the whole analysis.

### Menu-mix pressure

This section summarizes directional pressure on broad material families derived from the expected menu mix. It is intentionally not a physical inventory count. Exact ingredient need requires a verified recipe/inventory source, which is outside the zero-entry core product.

### Week ahead

The bottom strip gives the owner a six-day extension without forcing a page change.

## 2. Outlook

Outlook is a 14-day decision ledger. It is designed for weekly planning and fast exception scanning.

Each day shows:

- Demand level
- Expected sales
- Change versus comparable baseline
- Confidence
- Peak hour
- Weather
- Top item
- Most material contextual signals

Selecting a date opens the complete Brief for that day rather than duplicating a second forecast interface.

## 3. Results

Results measures whether the forecasting process is credible.

It uses a walk-forward backtest: for each evaluation day, the model is limited to information that would have existed before that day, then compared with actual completed POS item units.

Results reports:

- Forecast accuracy
- Weighted absolute percentage error
- Days evaluated
- Items evaluated
- Daily actual versus predicted units
- Item-level error ranking

Quantify does not claim saved waste, food-cost reduction, or inventory savings unless a future connected source can substantiate those outcomes.

## 4. Setup

Setup uses four tabs so configuration remains compact.

### Connections

Shows provider readiness and last synchronization. Square is operational in this package. Toast and Clover show their genuine approval/OAuth boundaries rather than pretending a generic key is enough.

The weather and event sync actions backfill history in batches and refresh future context.

### Daily email

The owner sets:

- Recipient address
- Local send time
- Time zone
- Enabled/disabled status
- Week-ahead inclusion

The owner can preview the exact HTML email and send a test. Without Postmark or SMTP, the test is written as an `.eml` file in `data/outbox`.

### Menu understanding

The POS catalog is the normal input. A text import is available for demonstrations, scanned-menu OCR output, or a restaurant without a live catalog connection.

Quantify expands common abbreviations, considers the POS category and modifiers, assigns a broad operational family, and preserves the raw label. Low-confidence names are optional review items, not blockers.

### Account and plan

Shows security status and commercial plan. First-run TOTP is mandatory in the local application.

## Zero-entry rules

“Zero-entry” means:

- No daily sales entry
- No duplicate menu maintenance after POS connection
- No manual weather entry
- No fixed event-category selection
- No daily forecast prompt
- No inventory, expiration, or recipe ledger required for the core forecast

It does not mean the platform can know an unpublished private event or a physical refrigerator count without a data source. Quantify is explicit when information is unavailable.

## Daily operating rhythm

1. POS orders arrive through historical synchronization and live webhooks.
2. Weather and real-world context refresh on schedule.
3. Quantify generates the location/item forecast and likely ranges.
4. The owner receives the daily email.
5. The operator opens Brief only when more detail or an override is needed.
6. Results measures later performance against completed POS sales.

## Main product boundaries

Quantify 2.0 does:

- Item-level demand forecasting
- Hourly service pacing
- Context analysis
- Menu-label interpretation
- Daily email briefing
- Forecast accuracy measurement
- Reasoned overrides

Quantify 2.0 does not claim to do:

- Exact physical inventory without a verified source
- Expiration or lot tracking
- Autonomous food purchasing
- Full employee scheduling
- Payment processing
- Accounting
- Guaranteed knowledge of private or unpublished events
