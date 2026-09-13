# The Quantify Constitution

This is the document Quantify abides by. It states what the product is, how it works,
what it is allowed to cost us, what it is allowed to claim, who it competes with and
why it wins. Everything here is binding on the people and the agents who work in this
repository.

Three rules govern the document itself:

1. **A claim without a source is not a claim.** Every number about a competitor, a wage,
   a tax rate or our own accuracy carries where it came from and when it was read.
2. **When the document and the code disagree, that is a bug in one of them.** Say which.
3. **It changes by amendment, not by drift.** Add a dated entry at the bottom.

Written 13 September 2026. House style applies: no em dashes, no marketing words, every
number with something to compare it against.

---

## Article 1. The one question

Quantify answers one operational question for one kind of business:

> What is this location likely to sell tomorrow, when will demand arrive, and what
> deserves attention before service begins?

The business is a repeat-menu, prep-sensitive food concept with one to five locations:
bakeries, pizza shops, burger and sandwich restaurants, cafes with prepared food,
barbecue. It runs on Square. It has at least six months of itemized history. Today the
owner forecasts by memory and a spreadsheet.

We do not answer a second question. Every request to add one is measured against this
article first. The market is full of products that answer fifteen questions badly in
fifteen modules, and Article 8 explains why that is our opening rather than our roadmap.

**What we do:** item-level demand forecasting, hourly service pacing, context analysis,
menu-label interpretation, the daily owner email, accuracy measurement on closed days,
reasoned overrides, and the ordering and supplier layer that turns a forecast into a
draft order.

**What we do not do, and say so plainly:** exact physical inventory without a verified
source, expiration or lot tracking, autonomous purchasing, employee scheduling, payment
processing, accounting, or knowledge of a private event nobody published.

---

## Article 2. What we are not allowed to claim

This article exists because the market we are entering lies, and the lying is the
opening. See Article 8, pattern 3.

1. **We never claim a saving we cannot audit.** Not waste reduced, not food cost cut,
   not hours saved, unless a connected source substantiates it and the calculation is
   inspectable. Competitors publish fleet averages. We publish this location's record.
2. **We never state a precision the model does not have.** Ranges and confidence, never
   a false decimal.
3. **We never invent a cause.** If the data does not show why a day moved, the product
   says the day is unexplained.
4. **We say when we do not know.** A new item with no history gets "not enough history
   yet", not a guess. This is the single most likely place to lose a customer's trust,
   because it is where a guess is easiest and most wrong.
5. **We never describe ourselves inside the product.** The operator is looking at their
   own business, not at our software. No mention of the model, the method, or Quantify.
6. **Green means more than normal. It never means good.** More is not always good, and
   the sentence beside the colour always says which.
7. **An accuracy number we publish may look worse than a competitor's marketing number.**
   We publish it anyway. A true 71 percent within range beats a fleet-average 98 percent
   the buyer cannot check, and it is the only claim in this category that survives contact
   with a skeptical owner.

The build enforces part of this. `tests/test_quantify.py` fails on an em dash or a
marketing word reaching anything an operator reads. That test is not decoration. Do not
weaken it to pass a build.

---

## Article 3. How the product works

### The path a number takes

```
Square catalog + completed orders + live webhooks
              |
              v
    normalization and deduplication        (pos_order_lines, idempotent)
              |
   +----------+----------+
   v          v          v
item/day  item/hour   menu interpretation
   |          |          |
   +----------+----------+
              v
   weather + calendar + event context
              v
   item-specific explainable ensemble
              v
   +----------+----------+
   v          v          v
 Today     History     Order        and the daily owner email
```

### Ingestion

`pos_order_lines` is the idempotent boundary. Its composite primary key prevents
duplicate provider, order and line records. When an order changes we delete the prior
normalized lines for that order, insert the current completed state, and rebuild only
the affected item and day aggregates. Daily and hourly aggregates are stored separately
so the model and the service curve read fast.

Square webhooks are verified with HMAC-SHA256 over the notification URL plus the exact
raw body. Unverified data is rejected before JSON parsing, not after.

### The forecast

For each item and date the model builds three candidates and blends them:

1. A weighted same-weekday baseline.
2. A regularized regression over calendar, weather, event and trend features.
3. A nearest-analog estimate from contextually similar historical days.

Candidates are scored on held-out history, and validation performance sets the ensemble
weights. Output is expected quantity, a lower and upper likely range, confidence,
drivers, diagnostics and the comparable dates used. Models cache by database identity,
location, item, horizon and data version, and invalidate when the underlying data moves.

Event impact is continuous, not a cutoff:

```
relevance  x  attendance scale  x  distance decay  x  service-time overlap
```

There is no two-mile rule. Unknown event categories map to `other` and stay eligible,
because a category we have not seen before is not the same as an event that does not
matter.

### Accuracy

History runs a walk-forward backtest. Each evaluation date sees only what existed before
it. Accuracy is measured against completed POS item units with weighted absolute
percentage error. No future leakage, no retrofitted wins.

### The screens

Four destinations, in kitchen words. Today (the brief and what to do), Order (suppliers,
optional counts, order drafts), History (the accuracy record), Settings. The Today screen
reads like an operating memo: headline, the decisive measures, ranked priorities, service
curve, the dense demand ledger, context, menu-mix pressure, week ahead. Decisions before
charts.

### Zero-entry

Zero-entry means no daily sales entry, no duplicate menu maintenance after the POS is
connected, no manual weather, no event category picking, no daily prompt, and no
inventory or recipe ledger required for the core forecast.

It does not mean we can know a fridge count or a private party without a source. The
distinction is stated in the product, not hidden.

**Constitutional limit on the supply layer.** Counts, supplier catalogs, pack sizes and
pars are exactly where every incumbent lost its operators. If a count or a supplier setup
ever becomes required before the Today screen produces value, we have inherited the
complaints we sell against. Counts add the running-low strip. They never gate the
forecast.

---

## Article 4. The writing layer, and which model writes

### The division of labour

Quantify computes every number. The model only turns those numbers into sentences. It
never recomputes, adjusts or contradicts a figure. This is enforced in the system prompt
and in the shape of the call: the record goes in as final JSON, and a JSON schema
constrains what comes back.

Two writers produce the same shape of output:

- `claude` when an API key and the `anthropic` package are present.
- `local`, a deterministic writer built from the same record, with no network and no key.

**The local writer is not a degraded mode. It is the floor.** Everything in Article 5
depends on this: because a complete brief exists without the model, a spend cap can be
enforced hard without ever producing an outage.

### The model

**Recommended: `claude-opus-5`.** It is what `quantify_app/ai.py` uses today and it should
stay.

The task is the one Opus is best at and the one where a cheaper model shows: reading a
structured record, holding several competing drivers in mind, deciding which one actually
explains the day, and writing four sentences a busy operator will act on. The output is
the product. It is the thing in the owner's inbox at six in the morning, and it is the
only part of Quantify a customer reads every single day.

Current configuration, all correct, do not change without a reason written into this
article:

| Setting | Value | Why |
|---|---|---|
| Model | `claude-opus-5` | Strongest at the judgment this task needs |
| Effort | `medium` (`QUANTIFY_AI_EFFORT`) | The quality lever, tuned before the model is |
| Thinking | adaptive, on by default | Opus 5 runs adaptive when `thinking` is omitted |
| Output | `json_schema` structured output | The writer cannot return prose we did not ask for |
| Caching | `ephemeral` on the system block | Role plus skills are identical per task |
| Fallback | `server-side-fallback-2026-07-01`, `"default"` | A policy decline routes rather than fails |
| Failure | any exception falls to `local` | The brief always ships |

### What it costs

Measured against the published rate for `claude-opus-5`, five dollars per million input
tokens and twenty-five per million output, with cache reads at one tenth of input:

| Item | Tokens | Cost |
|---|---:|---:|
| System block, cached after the first call | ~2,450 read | $0.001 |
| The day record as JSON | ~6,000 in | $0.030 |
| Narrative plus adaptive thinking | ~2,000 out | $0.050 |
| **One daily brief** | | **~$0.08** |

Thirty briefs is $2.40. Day reviews add roughly $0.60 a month. Item compositions are
written once per item and cached, so a sixty-item menu costs about $1.20 in its first
month and close to nothing after.

**Expected: about $3.00 per location per month. First month about $4.20.**

**Finding, and it needs fixing.** `docs/COMMERCIAL_PLAN.md` lists a target variable cost
of $19.60 per location per month across hosting, email, weather, events, payments and
support. There is no line for model inference. It is not a rounding error at $3.00
against a $19.60 base. The table should carry a **$3.00 model line**, making the target
**$22.60** and the contribution at the $79 standard price **$56.40**, a 71 percent gross
margin rather than 75. That is still a good business. Publishing 75 when the true figure
is 71 is the same species of error Article 2 forbids us elsewhere.

### On choosing a cheaper model

A cheaper model would save roughly two dollars per location per month and would be
visible in the one artifact the customer reads daily. That trade is the owner's decision
to make, not an engineer's to make quietly. If it is ever made, it gets written into this
article with the date and the reasoning, and the accuracy of the prose gets measured
before and after on the same set of days.

The cheaper levers come first and none of them touch quality:

1. **Caching.** Already in place. Verify it: `usage.cache_read_input_tokens` must be
   non-zero across repeated calls. A timestamp or an unsorted dict in the system block
   silently kills it and nobody notices except the invoice.
2. **Effort.** `medium` today. The lever to move before the model is.
3. **The cache is the product's real cost control.** A brief is written once per location
   per day, keyed by a fingerprint of the record. Article 5 is about what happens when
   something bypasses that.

---

## Article 5. What one customer is allowed to cost us

### The exposure

At $79 a month with about $3.00 of expected model spend, a location has roughly twenty
six times its model cost in revenue. That is comfortable until something regenerates in
a loop. The cache protects us only while the fingerprint holds. Two paths deliberately
bypass it:

| Path | Where | What it does |
|---|---|---|
| `?refresh=1` on the day narrative | `server.py:1188` | `force=True`, skips the cache, writes a new brief |
| Composition refresh | `server.py:1340` | `force=True` on a single item |

Both reach `ai.generate(..., force=True)`, which skips `read_cache` and calls Opus 5. A
frustrated owner clicking refresh, a stuck browser tab, a retry loop in a script, or a
single curious customer who discovers the button, can each run the bill without limit.
Nothing in the codebase today counts tokens, records spend, or stops at a number.
`response.usage` is discarded on every call.

A third exposure is quieter. A pathological location, a menu of two thousand items, or a
malformed catalog, produces a record that is large on every single call. The cost is per
brief and it is permanent until someone notices.

### The guard

`quantify_app/budget.py` closes all three. It rests on the fact established in Article 4:
the local writer is a complete floor, so the cap can be strict.

| Control | Value | Behaviour at the limit |
|---|---:|---|
| Monthly ceiling per location | $6.00 | Fall back to the local writer |
| Manual regenerations per location per day | 12 | Serve the cached brief |
| Record size per call | 60,000 tokens | Local writer for that record |
| Tell a human | $12.00 in a month | Nothing has stopped, but something is wrong |

Six dollars is twice the expected spend. An ordinary location never approaches it. A
location that hits it keeps working, because the brief it gets is the deterministic one
and the deterministic one is complete. The customer is not cut off, the bill is.

Every call records what it used: location, task, model, input tokens, cache reads, cache
writes, output tokens, computed cost, and whether it was forced. That table is the answer
to "what is this customer actually costing us", which today has no answer at all.

### The principle

**A customer who behaves badly degrades to the deterministic product. A customer is never
cut off, and we are never surprised by an invoice.**

Three properties follow, and all three should hold before this is called done:

- No individual location can cost more than its ceiling in a month, whatever it does.
- No customer sees an error page because of a budget. They see a brief.
- Every dollar is attributable to a location, a task and a day.

---

## Article 6. Personalization, and the national average wearing a local badge

A number that is true on average and presented as though it were true here is worse than
no number. It is wrong quietly, in the one direction the owner cannot check, and it
appears in the profit figure they are deciding on.

### The standing violation

`quantify_app/costs.py` sets `payroll_load_percent: 18.0` as a default for every location
in the country. The comment explaining it is honest and correct about the components:
7.65 percent for Social Security and Medicare, plus unemployment insurance and workers
compensation on top. But 7.65 is federal and fixed, while the rest are neither.
Unemployment insurance is a state rate against a state wage base and varies by the
employer's own experience rating. Workers compensation for restaurant classifications
varies by state by a factor of several, and in a monopoly state it is not a market rate
at all.

So 18.0 is a plausible national figure presented per location, in a screen that tells an
owner what their day kept. That is precisely what this article forbids.

The wage side of the same file already does this correctly and should be the model for
fixing it. `STATE_MINIMUM_WAGE` carries all fifty states and DC, `LOCAL_MINIMUM_WAGE`
carries the named cities and counties that run higher, `MINIMUM_WAGE_AS_OF` and
`MINIMUM_WAGE_SOURCE` record what was read and from where, `MINIMUM_WAGE_REVIEWED` dates
the last check, and a test fails when that date is more than a year old. The comment names
the four things the table deliberately does not model and explains that this is why the
interface calls it an estimate, prints the date, and lets the owner type over it.

That is the standard. Every assumption that varies by location meets it or it does not
ship.

### The rule

Any figure that varies by location carries four things:

1. **A value resolved as locally as we can resolve it.** State first, city or county
   where we know one, the owner's own number above all.
2. **A source and a date.** Named, and readable by the operator, not buried in a comment.
3. **A review deadline enforced by a test.** A stale table fails the build. It does not
   quietly keep being wrong.
4. **An override the owner can type, which wins, and which the screen says it used.**

And the presentation rule: while a figure is a national default it is labelled as one.
The word for a number we have not localized is not "typical". Say what it is: a national
figure, the date it was read, and that entering the real one will replace it.

### The order of work

| Assumption | Where | Now | Required |
|---|---|---|---|
| Minimum wage | `costs.py` | State and named localities, sourced and dated | Meets the standard |
| Payroll load | `costs.py` | One national 18.0 | State UI rate and state workers compensation, sourced and dated |
| Cost share by item family | `costs.py` | Category defaults | Labelled as defaults, owner override prominent |
| Orders per person per hour | `costs.py` | 6.0, reasoned in the comment | Labelled as a default, calibrate from the location's own hours |

---

## Article 7. Freshness, and what goes stale

A forecasting product decays in silence. Everything below has a clock on it, and the
clock is enforced by something that fails rather than by somebody remembering.

| Thing | Goes stale when | How we find out |
|---|---|---|
| POS history | Webhook stops, or the connection expires | Connection readiness and last sync on Settings, plus a data-freshness alert |
| Weather | Provider fails or the horizon rolls | Provider-failure alert |
| Event context | Provider changes categories, or coverage drops | Unknown categories map to `other` and stay eligible, so a change degrades rather than disappears |
| Minimum wage table | Roughly a third of states move every January | `MINIMUM_WAGE_REVIEWED` plus a test that fails after a year |
| Payroll load | Annually, per state | Not yet built. Article 6 |
| Competitor facts | Constantly | Article 8, every row dated, re-verified before external use |
| Forecast accuracy | Menu change, season change, a closure nobody logged | Walk-forward backtest on closed days, visible on History |
| Model spend | Any pricing or usage change | `ai_spend`, month to date per location, Article 5 |
| The model itself | A new model, a deprecation, an API change | Article 4 records the choice and the date it was made |

Two standing commitments:

- **A provider failure is announced, not absorbed.** If weather is missing for tomorrow,
  the brief says the weather is missing. It does not quietly forecast without it and
  present the same confidence.
- **A forecast is scored on the day it can be scored.** Accuracy is not a launch metric
  that stops being computed once the case study is written.

---

## Article 8. The market, and how we compete

Twenty four products were researched in depth in September 2026 across inventory suites,
back office, forecasting tools, ordering apps, POS-native tooling, recipe and labor tools,
and supplier marketplaces, alongside operator-voice reports from Capterra, GetApp,
Software Advice, the app stores, Trustpilot, the Square Seller Community, ChefTalk and
trade press. The synthesis is the binding version. Reddit and G2 were unreachable, so the
operator voice skews to Square's forum, Capterra and the app stores, and that limitation
travels with any claim built on it.

### Who is actually across the table

| Category | Who | Price as read | What they do about demand |
|---|---|---|---|
| Free and bundled | Square for Restaurants | $0, Plus $49, Premium $149 | Nothing native forecasts item demand. Help article 6433 says reports do not project future sales |
| Inventory suites | MarketMan, MarginEdge, xtraCHEF | $249 to $449, $350, quote | Pars and consumption. MarketMan states its ordering "doesn't apply any forecasting logic independently" |
| Back office | Restaurant365, Crunchtime, Craftable | $469 to $749, $5,000+, quote | Revenue and labor. Crunchtime does suggested prep, for chains |
| Forecasting tools | Tenzo, 5-Out, Lineup.ai | $175 to $250, quote, $79 | The real fight. See below |
| Spreadsheets and paper | The incumbent | Time | Gut feel and buffering |

### The two that matter

**5-Out** is the closest functional match. Item-level forecasting, a daily prep email with
forecasted volumes per item for opening, peak and closing, batch rounding, Square and
Toast. Its weaknesses are commercial, not functional: no published price, almost no
independent reviews anywhere, and an App Store listing with no release since July 2024.

**Lineup.ai** publishes $79 per location for forecasts only, including item-level, with a
refund guarantee on the trial. It matches our price and our headline on a comparison page.
It has no ordering, no verified user reviews on any of the four major sites, needs roughly
a year of history, and may be folding into TimeForge after the August 2024 acquisition.

Neither is a reason to change course. Both are a reason not to believe we are alone.

### Eleven patterns, and the four we are built on

The research found eleven patterns. Four of them are our position:

**1. Setup is the wall.** Every inventory suite needs every ingredient, every unit
conversion, every recipe, every par and an opening count before it produces anything.
Vendors say 24 to 48 hours. Owners say otherwise: "Setup isn't for the non-technical, it
took me weeks to fully set it up" (MarketMan, Capterra); "Set up is extremely time
consuming" (R365, Capterra); "5 months later still unable to use the inventory side"
(Nory, Trustpilot). Our first forecast needs the POS connection and nothing else.

**2. Forecasting mostly means total sales, for labor.** R365, Crunchtime, Tenzo, 7shifts,
Toast and Nory headline revenue or covers. Item-level exists at 5-Out, Lineup.ai, Supy,
Nory and Crunchtime. Of those, only 5-Out and Crunchtime clearly say how many of each item
to make, and Crunchtime does it for a chain at over $5,000 a month.

**3. Accuracy is a marketing average, not a record.** Crunchtime "98-99%", Nory "up to
97%", 5-Out "98% confidence", Lineup "35% more accurate", MarginEdge "~15%" in one article
and "4%" in another. A vendor's own blog admits operators asked about accuracy give "a
shrug, a confident 'pretty good,' or a number invented on the spot." Our History screen
scores each closed day against completed POS units, item by item.

**4. Counting apps fail at the moment of counting.** MarketMan 2.5/5 on the App Store,
"it will crash and close out and nothing that I already input will be saved". MarginEdge,
"a good 30 second wait" per save. Craftable logs users out. Supy, "one if the worst
software in the world if you want ton ruined your inventory". Our counts are optional and
they are never the gate.

The other seven are the conditions we operate in: supplier integration is an email to a
rep and no distributor publishes a buyer-side API; pricing is opaque and high; the market
is built for multi-site and several competitors decline single venues outright;
interfaces run eight to fifteen modules in accounting words; contracts and cancellation
are a recurring wound, with 49 percent of buyers naming opaque pricing as the first thing
they would change; trust in a number depends on the reason printed beside it; and the POS
vendors are moving in.

### What we sell, and the evidence for each claim

Every one of these is defensible with a source. None of them is a superlative.

1. **Useful in the first week, with nothing to type.** Against six to twelve week setups.
2. **It tells you how many of each item to make, which your POS cannot.** Square's own
   help article says its reports do not forecast item demand, and the seller request for
   per-item prep numbers has been open since January 2024 with a moderator reply that
   "there are not any workarounds".
3. **It shows you how right it was, on your own sales, every closed day.** Nobody else
   shows the buyer their own item-level error.
4. **A fraction of the price of an inventory suite.** Three to seven times cheaper.
5. **No contract, no setup fee, cancel from the account page.** Against "flaming hoops",
   charges after cancellation, and two to four year terms.
6. **Built for one to five locations.** Where Nory replies that they only do multi-site.
7. **Counts are optional and are never lost.**
8. **The order goes to your rep the way you already order, and the cutoff is on screen.**
   Honest about the absence of a buyer API rather than pretending to integrate.
9. **Four screens in kitchen words.**
10. **Every number carries its reason.**
11. **It sees modifiers and shared components.** The top-kudoed open request on Square's
    forum, which Square's item-level inventory cannot do.

### What could take this away

Written down so nobody is surprised:

- **5-Out publishes a price near $79.** Then the kitchen story is not unique and we
  compete on accuracy transparency, ordering and price.
- **Square ships it for free.** Square AI chat already claims to forecast sales, Managerbot
  flags shortages and drafts purchase orders in free beta, and MarketMan sits inside Square
  at $99. A free assistant answering "how much dough should I make" from last year's same
  weekday is good enough for some owners even with the "won't necessarily be exact" caveat.
- **The honest accuracy number loses to the marketing number.** A true 71 percent can read
  worse than a fleet-average 98 percent to a buyer comparing headlines. Article 2 says we
  publish it anyway. This article says be ready to explain it in one sentence.
- **Willingness to pay is not proven.** Only 28 percent of operators say technology
  improved profitability, implementation cost is the top barrier, and Square sellers say
  "I need so little of it". Ordering is free everywhere else and cannot carry the price.
- **One bad number ends trust.** Reviewers punish a single outlandish suggestion. New items
  and short histories are exactly where we must say "not enough history yet".
- **The supply layer pulls us into the setup wall.** Article 3 makes this a constitutional
  limit rather than a preference.

### How we talk about competitors

No comparison leaves this building with an undated price or an unsourced quote. Prices in
this article were read on or before 13 September 2026 and several vendors block fetches,
so those figures are third-party. Re-verify before any external use. We do not repeat a
competitor's marketing accuracy figure as though it were measured, and we do not invent
one of our own to match.

---

## Article 9. How we would know we are wrong

A constitution that cannot be violated is decoration. These are the tests.

| Commitment | Fails when |
|---|---|
| The forecast is useful without data entry | A location needs counts, recipes or pars before Today is worth reading |
| Accuracy is honest | A published figure is not reproducible from the walk-forward backtest |
| We do not claim savings | A savings number appears anywhere without an auditable source |
| The brief always ships | An operator sees an error instead of a brief, for any reason including budget |
| Spend is bounded | A location exceeds its monthly ceiling |
| Numbers are local | A national default is shown as though it were this location's number |
| Assumptions stay fresh | A sourced table passes its review date |
| The product stays four screens | A fifth destination appears without an amendment here |

---

## Amendments

**13 September 2026, adopted.** Written against commit `726d86c`. Findings recorded at
adoption, each with its article:

1. Article 4: `docs/COMMERCIAL_PLAN.md` carries no model inference line. Target variable
   cost should be $22.60, not $19.60, and gross margin at the standard price 71 percent,
   not 75.
2. Article 5: two uncapped regeneration paths at `server.py:1188` and `server.py:1340`.
   `response.usage` is discarded on every call and no spend is recorded.
3. Article 6: `payroll_load_percent: 18.0` in `quantify_app/costs.py` is a national figure
   presented per location.
