# Local API reference

Contracts reviewed September 13, 2026.

The five browser destinations are Today, Order, History, Updates, and Settings. Their transport routes retain descriptive API names; for example, Today uses `/api/brief`.

JSON requests use Content-Type: application/json. Authenticated state-changing requests require the session's X-CSRF-Token header. Workspace access requires confirmed email. Location routes also require location_id in the query and verify that it belongs to the session's organization. Dates default to the selected location's local date where stated. Errors return an error message with a non-success HTTP status.

## Public and authentication routes

| Method | Route | Purpose |
| --- | --- | --- |
| GET | `/api/health` | Version and health |
| GET | `/api/showcase` | Public sample presentation and launch pricing catalog |
| GET | `/api/timezone?q=` | Time zone match and suggestions |
| GET | `/api/auth/state` | Session, email-confirmation, and onboarding state |
| POST | `/api/auth/setup` | Create owner and session; begin email confirmation |
| POST | `/api/auth/login` | Sign in with email/password; request TOTP only if enabled |
| POST | `/api/auth/verify` | Complete a pending TOTP or recovery-code login |
| POST | `/api/auth/password/reset/start` | Request a reset code |
| POST | `/api/auth/password/reset/complete` | Complete reset using email, code, and new_password |

The remaining account routes require a session:

| Method | Route | Purpose |
| --- | --- | --- |
| GET | `/api/auth/email/status` | Read confirmation status |
| POST | `/api/auth/email/resend` | Send another confirmation code |
| POST | `/api/auth/email/confirm` | Confirm email with a code |
| GET | `/api/auth/mfa/setup` | Read TOTP setup details |
| POST | `/api/auth/totp/enable` | Verify and enable TOTP |
| POST | `/api/auth/mfa/disable` | Disable TOTP after verification |
| POST | `/api/auth/recovery-codes` | Replace recovery codes |
| POST | `/api/auth/password/change` | Change password |
| POST | `/api/auth/profile` | Save name and account email |
| POST | `/api/auth/logout` | Revoke the current session |

Email confirmation is required; TOTP is optional. When email is unconfigured locally, code responses identify the preview mode. A configured provider failure does not expose a code as a fallback.

## Workspace and locations

| Method | Route | Purpose |
| --- | --- | --- |
| GET | `/api/bootstrap` | Organization, active locations, local date, provider readiness, writer status, and billing summary |
| GET | `/api/pulse?location_id=` | Data version, local date, and background processing state |
| GET/POST | `/api/onboarding` | Read onboarding state or save initial workspace details |
| POST | `/api/locations` | Create an empty location within the current plan's capacity |

POST `/api/locations` accepts name, concept, place, region, optional timezone, open_hour, and close_hour. It returns HTTP 201 with location and geography_status. A place match is not street-address verification. Unsupported geography must remain explicit, even if a valid time zone is selected. Creation does not copy another location's sales, menu, forecasts, or context.

## Forecast and history

All routes in this section require `?location_id=<id>`, in addition to any listed parameters.

| Method | Route | Parameters and response |
| --- | --- | --- |
| GET | `/api/brief` | date=YYYY-MM-DD, default local today; complete day with items, summary, comparisons, week_ahead, costs, intraday state, and cached narrative |
| GET | `/api/brief/narrative` | date and optional refresh=1; structured narrative or pending response |
| GET | `/api/outlook` | start, default today; days default 14, range 1 through 14 |
| GET | `/api/item` | item_id and optional date; item forecast, preparation, history, and explanation |
| GET | `/api/history/days` | before, start, limit default 14 and maximum 60; calendar days, has_more, next_before, and source |
| GET | `/api/history/orders` | before, start, skip, limit default 40 and maximum 500; orders with next_before_date and next_skip |
| GET | `/api/history/day` | date, default yesterday; sold/expected detail, costs, orders, and review unless closed |
| GET | `/api/accuracy` | as_of, default today; days default 30, range 1 through 365 |

The old `/api/results` route is not part of this API. History uses `/api/history/*` and `/api/accuracy`. next_before is exclusive. History Days includes calendar gaps; an empty location returns an empty list. Order cursors can stop partway through a date, so clients must preserve both next_before_date and next_skip.

Item fields distinguish expected/model_expected (predicted sales), baseline (normal sales), and make (preparation). new_item=true means fewer than seven selling days; the client must display "No number yet" and not interpret placeholder zeros as measured demand. These items are excluded from forecast totals and ingredient demand.

The item endpoint's `today` object also includes `call_source` (`live`, `stored`, or `reconstructed`), `call_label`, `call_recorded_at`, and `recomputed_expected`. For closed dates, `expected` and `model_expected` use the recorded opening call or the rounded scored History value when available. `recomputed_expected` preserves the current reanalysis separately. Make, preparation analysis, and a saved quantity override remain separate from that historical Expected.

When location-matched weather is unavailable, the brief's `context.weather` has `available:false` with `null` conditions and numeric measurements. Clients must show the missing context rather than substituting a temperature. Event attendance and distance likewise require provenance; missing values remain `null`.

### Make adjustment

POST or PATCH `/api/forecast/override?location_id=<id>`:

    {
      "item_id": "example-item",
      "date": "2026-09-14",
      "quantity": 72,
      "reason": "Confirmed catering pickup"
    }

quantity is an integer from 0 through 100000. reason is optional. The signed-in display name is recorded as author. The response is the updated item forecast. The saved quantity changes Make exactly, including zero, while Expected and Normal remain separate. DELETE at the same route with item_id and date removes the adjustment.

## Updates

| Method | Route | Purpose |
| --- | --- | --- |
| GET | `/api/updates?location_id=` | Read the feed; refresh local observations if the prior check is older than five minutes |
| POST | `/api/updates/refresh?location_id=` | Body {}; explicitly recheck current sales/counts and optionally rank supplied observations |
| POST | `/api/updates/read?location_id=` | Save shown/read receipts for this user and return the feed |

Feed response:

    {
      "location_id": "example-location",
      "checked_at": "2026-09-13T15:00:00+00:00",
      "notes": [],
      "earlier": [],
      "unread_count": 0,
      "notification": null,
      "timely_notification": null
    }

Each note contains id, kind, severity, title, body, evidence, action, created_at, starts_at, expires_at, state, unread, and seen_at. Timestamps are UTC ISO strings. evidence is plain text. severity is important or notice. state is active, scheduled, expired, or resolved. Additional storage fields may be present.

action contains view, label, and optional tab or item_id. Supported observation destinations are today, ordering, and settings. Clients render all note text as text, not trusted HTML.

notes contains active/upcoming observations. earlier contains up to 20 expired/resolved observations from the bounded feed history. unread_count counts unread active/upcoming notes for the current user. notification is at most one important, active, unread, unseen note. timely_notification is an eligible active, unread, unseen pattern for a visible-session check. Neither field makes an expired note eligible.

Read body:

    {"ids": ["example-note-id"], "read": true}

ids must be a list of up to 100 strings belonging to the requested location. read:true persists shown and read timestamps. read:false persists that a notice was shown while preserving unread state. It does not mark an already-read note unread.

Opening or returning to the app considers notification only. A five-minute visible-session poll may also consider timely_notification. Clients must recheck expiry, defer notices while typing or a modal is open, and post read:false only when a notice is actually displayed. This persistence prevents dismiss/reload repeats. The browser keeps existing notes during refresh and discards responses for an old user/location.

Refresh uses existing connected sales and counts. It is not a register-sync endpoint. Optional model assistance can rank only the supplied observation IDs; it cannot add facts or customer identity.

## Menu and recipes

| Method | Route | Purpose |
| --- | --- | --- |
| GET | `/api/menu?location_id=` | Menu, recipe summaries, cost labels, and pending_compositions; queue missing recipes |
| GET | `/api/menu/composition?location_id=&item_id=` | Read or generate one item's composition |
| PUT | `/api/menu/composition?location_id=` | Save owner-confirmed recipe |
| POST | `/api/menu/composition?location_id=` | Request a fresh recipe using body item_id |
| POST | `/api/menu/import?location_id=` | Preview or commit text rows |

Import body:

    {"text": "Cheeseburger | Burgers | 12.50", "commit": false}

Use commit:false for review, then commit:true to save accepted rows. Missing prices are skipped. Register-owned rows are preserved. Opening Menu schedules missing compositions in the background; a late generated result cannot overwrite an owner-confirmed recipe.

A recipe PUT accepts item_id, summary, and components. Each component has name, role, quantity (recipe-unit text), and share (percentage). Named parts are required and shares must total from 95 through 105 inclusive.

## Order and supply

All routes here require `?location_id=<id>`.

| Method | Route | Purpose |
| --- | --- | --- |
| GET | `/api/ordering` | start and days, default 3 and range 1 through 14; usage, recipe backlog, and supplier/count/pack-enriched lines when ready |
| GET | `/api/supply` | Suppliers, ingredient purchase settings, recent orders, mail-provider status, and supported labels |
| POST/PUT | `/api/supply/supplier` | Create/update supplier |
| DELETE | `/api/supply/supplier` | Delete supplier using body id |
| POST/PUT | `/api/supply/item` | Save supplier and purchase pack for an ingredient |
| POST/PUT | `/api/supply/count` | Save one or more shelf counts and return recalculated lines |
| GET | `/api/supply/attention` | Count-based runout and order-deadline attention |
| POST | `/api/supply/order` | Send or record an order through the explicitly chosen channel |
| GET | `/api/supply/orders` | Recent recorded orders |

Supplier fields include optional id, name, rep_name, order_email, phone, website, account_number, delivery_days, cutoff_time, lead_days, and notes. Delivery days use weekday keys such as mon and thu. An update id must already belong to the location. Websites must use a permitted HTTP(S) URL.

Purchase settings accept ingredient, supplier_id, pack_size, pack_unit, pack_label, and product_code. A supplier must belong to the same location. Unset pack size means no supported conversion to packs yet.

A count accepts ingredient, on_hand, unit, optional kind, and optional days for the displayed order window. A batch uses counts:[...] with the same outer days. unit:"pack" requires a configured pack. on_hand:null or an empty string clears the count; numeric zero records an empty shelf. Invalid or negative counts are rejected. Counts are stored in base units. The response has saved, line (first recalculated line or null), and lines. A count can save even when no recipe makes a corresponding forecast line available.

Order example:

    {
      "supplier_id": "example-supplier",
      "channel": "mail-app",
      "confirmed": false,
      "window_start": "2026-09-14",
      "window_end": "2026-09-16",
      "requested_delivery": "2026-09-14",
      "lines": [
        {"name": "Burger buns", "quantity": 2, "unit": "case",
         "pack_size": 24, "pack_unit": "each", "product_code": "B24"}
      ],
      "note": ""
    }

The response contains order, recorded, text, mailto, and message. Positive order lines and a supported channel are required.

| Channel | Result |
| --- | --- |
| email | Attempts server delivery; records sent, failed, or outbox according to provider outcome |
| mail-app, confirmed:false | Returns status drafted, recorded:false, and mailto; no purchase order is written |
| mail-app, confirmed:true | Records the operator's confirmation as sent |
| site | Records the operator's explicit "I placed this order" action, with status opened |
| copy | Rejected as an order channel; copying text is a client action |

Do not call site merely because a website opened. Sent/opened order records represent expected deliveries, not supplier acceptance or physical receipt. Failed/outbox records must not be presented as delivered orders.

## Location, costs, and email

All routes here require `?location_id=<id>`.

| Method | Route | Purpose |
| --- | --- | --- |
| GET | `/api/setup` | Location, zone resolution, integration/register readiness, email preferences, menu summary, and security summary |
| POST/PUT | `/api/location` | Save location identity, geography, time zone, and trading hours |
| GET | `/api/costs` | Cost settings, wage references/provenance, categories, recurring costs, and a worked example |
| PUT | `/api/costs` | Replace saved cost assumptions and category/recurring lists |
| GET | `/api/email/preview` | Optional date; return subject, html, and text |
| POST/PUT | `/api/email/preferences` | Save owner_email, enabled, send_time, and include_week_ahead |
| POST | `/api/email/send-test` | Optional date and recipient; send or save an outbox artifact |

Costs PUT sends percentages as percentages, such as 33, not 0.33. Fields include hourly_wage, payroll_load_percent, orders_per_person_per_hour, min_staff, max_staff, prep_hours, close_hours, default_cost_share, categories:[{category,percent}], and recurring:[{name,amount,period}]. This is a whole-form replacement, not a partial list patch. Blank pay or employer payroll percentage means unknown. Missing labor inputs yield incomplete totals rather than an assumed wage or payroll rate. See [Personalization](PERSONALIZATION.md) for response provenance.

Email preferences derive the time zone from the location; a separate submitted zone is ignored. An enabled email needs a valid recipient. A disabled email may have an empty address. Send test allows only the signed-in address or saved recipient. Without a provider the response is a saved outbox message, not a successful external delivery.

## Connections and webhooks

| Method | Route | Purpose |
| --- | --- | --- |
| POST | `/api/integrations/pos/credentials?location_id=` | Save Square access_token, provider location_id, and environment |
| POST | `/api/integrations/pos/sync?location_id=` | Synchronize register catalog/orders |
| POST | `/api/integrations/weather/sync?location_id=` | Refresh matched local weather |
| POST | `/api/integrations/events/sync?location_id=` | Refresh matched local events/context |
| POST | `/api/webhooks/square?location_id=` | Public webhook authenticated by Square signature |
| POST | `/api/webhooks/stripe` | Public webhook authenticated by Stripe signature |

Sync bodies accept days and, for context, backfill_days within route limits. Provider credentials and data retention still apply. Unsupported geography prevents weather/event lookup rather than querying default coordinates. Supplier website links do not create a provider sync or ordering API.

## Billing

| Method | Route | Purpose |
| --- | --- | --- |
| GET | `/api/billing` | Subscription, shared catalog, capacity, and provider readiness |
| POST | `/api/billing/plan` | Select a trial plan using body plan; preserve trial dates |
| POST | `/api/billing/checkout` | Start configured checkout using body plan; response is pending |
| POST | `/api/billing/portal` | Open hosted billing management |
| POST | `/api/billing/cancel/reason` | Save reason, detail, and wants_contact |
| POST | `/api/billing/cancel` | Request cancellation; optional immediate |
| POST | `/api/billing/resume` | Resume according to the current subscription state |

Launch identifiers are solo ($39/month, one active location) and team ($99/month, up to three). Existing standard and founding records retain legacy terms. Paid plan changes use the hosted portal. Creating locations and trial downgrades enforce capacity without deleting existing data. Checkout verifies configured USD monthly prices and does not mark an account paid before a signed provider event. See [Pricing decision](PRICING_DECISION.md).

## Local-only reset boundary

POST `/api/demo/reset` is disabled unless QUANTIFY_ALLOW_DEMO_RESET=1. It also refuses a database with more than one active account. It replaces sample data and is not a normal application workflow.
