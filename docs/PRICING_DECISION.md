# Quantify pricing decision

Decision date: 2026-09-13. USD. These launch prices are implemented locally; no payment provider prices, subscriptions or charges were changed by this work.

## Current offer

| Plan | Monthly account price | Active locations | Effective price when full |
| --- | ---: | ---: | ---: |
| One location | $39 | 1 | $39 per location |
| Up to three locations | $99 | 3 | $33 per location |

Both include Today, Order, counts, recipes, History, track record, Morning email and Updates. These are operating tools, not a promise of supplier API connectivity, invoice automation, or unconfigured integrations. Square is the implemented register connection. A new location starts empty and must connect its own register or add its own menu.

The trial lasts 14 days without a card. New workspaces default to One location. No annual price or discount is advertised for these plans. Monthly checkout must be configured separately for each price. The public page and Account read the same catalog.

Existing Standard ($79/month, prior annual equivalent $69) and Founding ($49/month, prior annual equivalent $44) records retain their stored offer and access. Existing workspaces lacking a subscription row retain Standard. They are not silently migrated or given new limits. An owner can choose a different trial plan; an existing paid subscription changes through the payment provider. A checkout response is pending, not proof of payment.

## Evidence and scope

Official pages checked on 2026-09-13:

| Product | Verified public price | Included scope | Comparison limit |
| --- | --- | --- | --- |
| [MarginEdge](https://www.marginedge.com/pricing/) | $350/location/month; 10% annual discount | Invoices, bill pay, inventory, recipes and cost reporting | Broader back office product. Toast-related fees and onboarding options can add costs. |
| [MarketMan pricing](https://www.marketman.com/pricing-for-restaurant-inventory-management-system) | Starter $249/month; Growth $299/month; Enterprise from $449 | Starter lists recipe costing, variance reporting, waste, one vendor connection and 50 invoice scans. Growth adds ordering and larger allowances. | Current primary pricing differs from older $199/$249 figures on the [partners page](https://www.marketman.com/partners) and in search summaries. Prefer current pricing; confirm a quote's location basis and add-ons. |
| [Tenzo](https://support.gotenzo.com/docs/how-much-does-it-cost-to-have-a-tenzo-subscription/) | Quote based on modules and requirements | Restaurant reporting and forecasting | No fixed public amount verified. |
| [Square US](https://squareup.com/us/en/point-of-sale/restaurants/pricing) | Free $0, Plus $49/location/month, Premium $149/location/month | Register and business software, with processing fees separate | Adjacent alternative and integration source, not an equivalent item-demand workflow. [Official plan announcement](https://squareup.com/us/en/press/unified-pricing-and-packaging). |

Quantify can offer a narrower entry below $79 without claiming feature equivalence or proven savings. Prices alone do not prove customers will pay, stay, or receive better forecasts. No customer interviews, conversion experiment, or production unit-cost dataset was available for this decision.

## Economics and rejected options

The previous commercial plan supplied a hypothetical steady-state cost of $19.60 per location per month. It was a planning assumption, not invoices or a verified quote:

| Component | Prior monthly assumption |
| --- | ---: |
| Hosting, database, jobs, backups and monitoring | $4.00 |
| Email | $0.50 |
| Weather | $0.50 |
| Event/context data | $5.00 |
| Payment processing allocation | $2.60 |
| Routine support | $7.00 |
| Total per location | $19.60 |

Holding those assumptions constant, $39 leaves $19.40 per location (49.7%) and a fully used $99 bundle leaves $40.20 (40.6%) before engineering, sales and other fixed costs. With variable costs 30% higher, contributions fall to $13.52 (34.7%) and $22.56 (22.8%). Support and commercial context costs are the largest uncertain allocations; this model is not proof of sustainability.

The initially considered $29 / $59-for-three option was rejected: three fully used locations leave only $0.20 before fixed costs under the same assumptions. No annual discount was added because it would reduce the unproven margin further.

$39 / $99 are testable hypotheses, not an optimal-price claim. Before broad rollout, measure actual cost per active location, onboarding/support minutes, provider usage, activation, paid conversion, 30/90-day retention and owner outcomes. Revise prices prospectively if results do not support the model; do not silently change existing agreements.

## Implementation

- `billing.public_catalog()` owns monthly prices and checkout availability.
- `location_allowance()` reports active usage. `require_location_capacity()` runs in the location write transaction. New plans allow one and three active locations. Downgrades below current usage are rejected; no location or history is deleted or hidden.
- Trial selection preserves dates and does not mark an account paid. Paid accounts use Manage billing. This change adds no global lockout for legacy or local workspaces after a trial.
- `STRIPE_PRICE_ID_SOLO` and `STRIPE_PRICE_ID_TEAM` must identify monthly USD prices of $39 and $99. Checkout verifies amount, currency and recurrence, rejects duplicate active subscriptions, and remains pending until a signed provider event arrives. Legacy IDs remain separate. No external prices were created.
- Signed subscription events map configured prices to plans. Unknown provider prices preserve the existing local plan for investigation. Provider portal products must be configured consistently before enabling plan changes there.
- New locations start with no sales, menu, forecasts or local context. Weather and nearby events require resolved geography. The geography integration handles unsupported places explicitly.

Tests cover catalog and legacy preservation, exact checkout configuration, pending state, nested provider metadata, unsigned webhooks, location caps and empty creation. Browser verification covers public/account pricing, trial selection and Add location. See the completion report for actual results.

Do not publish savings, forecasting superiority, or market-wide cheapest-price claims without measured evidence. The next decision should use paid-pilot retention and actual costs.
