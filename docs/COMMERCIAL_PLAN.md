# Quantify Commercial Plan

Historical proposal, superseded on 2026-09-13 by [the pricing decision](PRICING_DECISION.md). The prices, trial length and feature priorities below are retained as prior planning assumptions, not the current offer. Existing customer subscription records remain unchanged.

## Final price

### Founding cohort

**$49 per location/month**, locked for the first 12 months, limited to the first 50 qualifying locations.

Purpose: reduce purchase friction, build reference accounts, and learn real onboarding exceptions without permanently anchoring the product at an unsustainable price.

### Standard

**$79 per location/month**.

### Annual

**$828 per location/year**, equivalent to **$69/month**.

### Trial

**21 days**, with a connected POS or a structured historical export. There is no permanent free plan because a useful trial consumes onboarding, data, model, context, and support capacity.

## Why $35 is not the standard price

At $35, annual recurring revenue is only $420 per location. Payment processing, email, hosting, commercial event/context data, support, provider maintenance, security operations, and merchant-specific data problems would leave insufficient room for a reliable product.

Weather and email alone are inexpensive. The expensive parts are:

- Supporting changing POS APIs and marketplace requirements
- Historical data backfills and rate-limit handling
- Commercial event/context licensing at scale
- Onboarding malformed catalogs and merchant-specific exceptions
- Monitoring, backups, security, and incident response
- Explaining results when an operator challenges a forecast
- Retaining customers whose business changes seasonally

The product should be sold on measurable operating value, not as the cheapest analytics widget.

## Target customer

Initial wedge:

- One to five locations
- Repeat-menu, prep-sensitive food concept
- Square-first technical fit
- At least six months of itemized POS history
- Owner or operator currently using intuition/spreadsheets for expected demand
- Meaningful overproduction, sellouts, or labor/prep volatility

Best initial concepts:

- Bakeries
- Pizza shops
- Burger and sandwich restaurants
- Cafés with prepared food
- Barbecue or other advance-prep concepts

Avoid at launch:

- Fine dining with constantly changing menus
- Event-only caterers with little walk-in history
- Highly seasonal businesses with insufficient prior seasons
- Concepts whose POS item labels are not itemized
- Operators demanding autonomous procurement or exact inventory without an inventory source

## Sales promise

Primary message:

> Tomorrow's demand brief, built automatically from the POS you already use.

Supporting promise:

> Know what will likely sell, when demand will arrive, and why the day looks different—before service begins.

Do not lead with “AI.” Lead with the decision and proof. AI is the mechanism.

## Acquisition motion

1. **Square-first self-serve installation** for the long-term product.
2. **Founder-led pilot sales** for the first 25–50 locations.
3. Require enough history before promising a calibrated forecast.
4. Deliver the first owner brief as the activation moment.
5. Review forecast accuracy after 14 and 30 days.
6. Ask for a reference only after Quantify has a defensible record.

## Demonstrating ROI

Quantify 2.0 measures forecast accuracy directly. It should not invent savings.

During pilots, collect optional owner outcomes separately:

- Units produced versus sold for a small set of high-waste items
- Sellout incidents
- Manual prep changes made because of Quantify
- Time spent preparing the daily plan before and after

Only publish savings claims when the source and calculation are auditable.

## Cost model and price floor

The following is a planning model, not a vendor quote. Per-location steady-state monthly targets at meaningful scale are:

| Cost component | Target |
|---|---:|
| Hosting, database, jobs, backups, and monitoring | $4.00 |
| Email delivery | $0.50 |
| Weather allocation | $0.50 |
| Event/context allocation | $5.00 |
| Payment processing allocation | $2.60 |
| Routine support and success | $7.00 |
| **Target variable cost** | **$19.60** |

At the $79 standard price, that model leaves approximately $59.40 of contribution per location, or a 75% gross margin before company-wide engineering, sales, legal, and administrative expense. At the $69 annual equivalent, contribution is approximately $49.40, or 72%. The $49 founding plan is intentionally lower-margin while the team learns onboarding exceptions.

At $35, the same cost model leaves only about $15.40 per location before engineering and sales. That is too little room for the support and provider maintenance required by a product whose output affects daily restaurant operations.

Pilot support will be higher than the steady-state target and should be treated as product development rather than hidden inside an unrealistic permanent price.

## Revenue scale

At the $79 standard monthly price:

| Active locations | Monthly recurring revenue | Annualized recurring revenue |
|---|---:|---:|
| 100 | $7,900 | $94,800 |
| 500 | $39,500 | $474,000 |
| 1,000 | $79,000 | $948,000 |

The first commercial objective is not thousands of accounts. It is 25–50 locations with verified data coverage, repeated daily-email engagement, and a forecast record strong enough to support references and case studies.

## Feature priority

### Required for launch

- Square OAuth and webhooks
- Two-year history request with honest coverage reporting
- Broad historical/upcoming context
- Daily owner email
- Brief, Outlook, Results, Setup
- TOTP security
- Accuracy monitoring
- Menu-label interpretation
- New-item cold-start behavior
- Data-freshness and provider-failure alerts

### Next

- Weekly owner recap
- Multi-location comparison
- Promotion and closure calendar integrations
- Delivery-channel segmentation
- Anomaly detection for POS outage, closure, and unusual availability
- Toast and Clover approved integrations

### Not core

- Expiration tracking
- Lot tracking
- Manual inventory counting
- Supplier ordering
- Full scheduling
- Full POS replacement

Those features would pull Quantify away from its zero-entry demand-intelligence advantage.
