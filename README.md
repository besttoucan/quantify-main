# Quantify

Quantify tells a restaurant what tomorrow is likely to sell, why, and how sure to
be about it, using nothing but that restaurant's own register history and the
conditions around it.

It is built on one rule: **a number without a comparison is not information.**
Nothing in this product shows you "up 8%" and stops. It shows you $3,884 against
$3,410 on a normal Friday, which is 61 more items, and then tells you how many
past Fridays that judgement rests on.

```
python server.py --open          # or double click start.bat on Windows
```

| Where | What it is |
|---|---|
| `/` | The landing page. Public, no account needed. |
| `/signup` | Create an account. |
| `/login` | Sign in. |
| `/app` | The product. |

**Locked out?** `python server.py --accounts` lists every account on the database, and
`python server.py --set-password you@yourdomain.com` sets a new one and signs every
other session out.

The database seeds itself with three sample locations carrying two years of
hourly sales, so the product is fully working the first time it opens. Nothing
is staged: every figure on screen is computed from that history by the same code
that will run on real data.

---

## The five screens

| Screen | What it answers |
|---|---|
| **Today** | What to make, what to staff, why the day looks the way it does, and how sure the model is. |
| **Forecast** | The next fourteen days as one list. Open any row to work that day. |
| **History** | Every order that was rung, day by day, and how close the forecast was on each closed day. |
| **Menu** | What each item is physically made of, so a change in demand turns into prep. |
| **Settings** | Location and hours, data connections, the morning email, account, plan, and billing. |

The screen updates itself. A background poll notices when new sales land or when
another day finishes scoring, and the open page refreshes in place without
losing your scroll position. Nobody has to press reload.

---

## How the forecast works

For every item, on every date:

1. **Baseline.** A recency-weighted average of the same weekday, adjusted for
   momentum and season.
2. **Ridge regression** over 34 features: weekday, annual cycle, trend, holidays
   and observances, holiday eves, long weekends, pay cycles, month end,
   temperature anomaly, cold anomaly, rain, snow, UV, daylight hours, and nine
   separate categories of nearby activity.
3. **Context analogs.** The eight most similar past days by conditions, weighted
   by how similar they were.

The three are blended by weights chosen from a **walk-forward holdout** on that
item's own history, so an item whose demand is driven by weather gets a
different blend from one that just follows the weekday.

The range shown next to each item is derived from the model's measured error on
that item, not from a fixed percentage.

### How many to make is a different question from how many will sell

Running out costs the whole margin and sometimes the customer. Throwing one away
costs the food. Those are not the same number, so the right quantity to produce is
not the average, and the Today table shows both: **Make**, and **Will sell**.

The production number is the newsvendor critical fractile, `Cu / (Cu + Co)`, read
off the item's own outcome distribution rather than off an assumed bell curve. That
distribution is built by converting each comparable past day into a ratio against
the level that was normal around it, which removes the item's growth or decline from
the spread, and then applying those ratios to today's forecast. An item that has been
growing all year is not thereby a riskier item.

---

## Every item, not the top five

Open any item anywhere in the product and you get its full record. The item that
sells four a day gets the same analysis as the one that sells four hundred.

| Section | Method |
|---|---|
| How many to make | Newsvendor fractile, plus the cost of being wrong at five levels around it |
| Which days it belongs to | One-way ANOVA across weekdays, with the share of variation weekday explains |
| What actually moves it | Twelve conditions by least squares against weekday-and-drift residuals |
| Whether it has changed | Welch's t-test, last four weeks against the four before, plus long-run slope |
| What it trades against | Pearson correlation on residuals, so busy days do not fake a relationship |
| How well we call it | Per-item WAPE from the scored record |
| Days it surprised us | Residual z-scores past 2.5, with what was happening |

Everything above is tested against the **residual**: what is left after weekday and
linear drift are removed by least squares. An effect that disappears once weekday is
accounted for was a weekday effect wearing a costume, and it does not get reported.

The twelve driver tests are one family, so their p-values are corrected together by
**Benjamini-Hochberg**. Twelve independent tests at the 5% level will hand you a
false positive roughly half the time; this refuses to print it.

Three verdicts, not two:

- **Established**, below a 5% false-discovery rate.
- **Leaning**, between 5% and 20%, shown with how many more days carrying that
  condition would settle it. Not something to act on.
- **Rejected**, listed with its numbers rather than quietly dropped.

Separately from all of that, an effect can clear every bar and still move the item by
less than one unit on a typical day. Those are labelled *real, but too small to change
what you make*, because statistical significance and operational significance are
different claims and conflating them is how a dashboard becomes noise.

Exact tail probabilities come from the regularised incomplete beta function evaluated
by continued fraction, so the Student's t and Fisher F p-values are computed rather
than looked up in a table. `quantify_app/statistics.py` has no dependencies and is
checked against published critical values in the test suite.

### Accuracy is measured, not asserted

A background worker replays every closed day: it fits on everything before that
day, predicts, then compares against what the registers actually rang. Nothing
from the day being scored is ever visible to the prediction for it. Those scores
appear in History, and once seven days are scored they **override the model's own
confidence figure**, because a location's real track record is more honest than
the model's opinion of itself.

---

## Which AI model, and what it is used for

**Claude Opus 5** (`claude-opus-5`) is the recommendation, and it is what
`QUANTIFY_AI_MODEL` defaults to.

Be clear about the division of labour: **the model never computes a forecast.**
Every number comes from the statistics above. The model is given the finished
record and asked to write it up. That is a task where reading several competing
drivers at once and being honest about uncertainty matters far more than raw
speed, which is exactly what the Opus tier is for. The daily write-up is
generated once per location per day and cached, so the cost is a few cents per
location per day.

```
pip install anthropic
set ANTHROPIC_API_KEY=sk-ant-...
```

Without a key, Quantify writes the same sections itself from the same structured
record. The product is complete either way; the model version is longer and more
specific. Settings shows which one is running.

### Skills

The model is given a set of skills rather than one long prompt. Each is a
markdown file in `quantify_app/skills/`, and `TASK_SKILLS` in
`quantify_app/ai.py` maps each task to the skills it loads:

| Task | Skills loaded |
|---|---|
| `day_narrative` | `plain-language`, `demand-analysis` |
| `item_composition` | `plain-language`, `menu-composition` |
| `day_review` | `plain-language`, `forecast-review` |

The skills block is identical for every request of a given task, so it is marked
for prompt caching and costs almost nothing after the first call of the day.
Editing a skill file changes how the product writes, with no code change.

---

## What Quantify will not claim

- It never reports exact ingredient quantities, waste avoided, or money saved
  without a source that can substantiate them. Menu composition is labelled as an
  estimate from the item label, with a confidence, until a recipe is connected.
- It never hides a miss. A 15% miss says so in the first sentence.
- Order-level history is labelled by source. When a register is connected these
  are real receipts. On the sample dataset, which has daily and hourly totals but
  not line-level tickets, orders are reconstructed from those totals with a fixed
  seed. They reconcile exactly, and the interface says which you are looking at.

---

## Connections

| Source | Status | What it brings |
|---|---|---|
| Square | Working | Item catalogue and every completed order, with the hour it landed in. Up to three years of history. |
| Weather | Working | Past and forecast temperature, rain, snow, and UV by coordinates, from Open-Meteo. |
| Nearby activity | Working | Concerts, sport, conferences, festivals, and public holidays inside a trade area that adapts. Ranked by size, distance, timing, and how this location has responded before. |
| Toast, Clover, Lightspeed | Waiting on provider approval | Same shape as Square once credentials are issued. |

There is no fixed event radius and no rule that says a school event matters. A
listing appears only if it earns its place against that location's own history.

## Payments

Stripe. Cards, invoices, tax, and dunning are handled there and no card number
ever reaches this application. Set `STRIPE_SECRET_KEY` and a price ID to switch
it on; without them the plan screens run in local mode so the whole flow,
including cancellation, can be reviewed before anyone is charged.

Cancelling sits at the bottom of Account and security, in plain sight. It asks
what went wrong, records the answer before anything is cancelled, offers a
conversation once, and gives an unmistakable way to decline that offer and
proceed. Access continues to the end of the paid period and can be resumed with
one button until then.

## Accounts

Signing up is name, email, password, then a six-digit code sent to that address.
Confirming the email is the only step nobody skips, because an unconfirmed address
means no password reset and no morning brief.

**Email is not being sent yet.** Until a provider is configured the message is
written to `data/outbox` as a real `.eml` file and the code is shown on the signup
screen so you can finish creating an account. Set `POSTMARK_SERVER_TOKEN` or
`SMTP_HOST` and the code stops appearing on screen and only arrives by email, with
no code change. Step by step: `docs/SENDING_EMAIL.md`.

The first account on a fresh install joins the pre-seeded sample workspace, so it
opens into a product that is already working. Every account after that gets its own
workspace and its own sample location. Two businesses never see each other's sales.

## Security

- **Two-step sign in is optional**, off by default, and lives in Account and
  security. Turning it on is a QR code generated locally with no external service.
  The key is 128 bits, the RFC 4226 floor, so the typed fallback is 26 characters
  rather than 32. Turning it off requires the account password.
- Backup codes appear only once two-step is on, and are optional even then.
- Passwords use PBKDF2-HMAC-SHA256 at 310,000 iterations. Sessions are HttpOnly,
  SameSite=Strict, with CSRF tokens on every write.
- Confirmation codes are stored hashed, expire after 20 minutes, cap at six attempts,
  and are retired the moment a new one is issued.

---

## Time zones

Owners do not think in IANA identifiers. Every place field accepts a city, a
state, a state abbreviation, a ZIP code, or a zone name, and shows back what it
understood so it can be corrected. "New York", "California", "Austin TX",
"10583", "brooklyn", and "Eastern" all resolve.

Windows ships no IANA time zone database, so `zoneinfo` fails there for every
named zone. `quantify_app/localtime.py` provides correct daylight saving rules
for the zones this product can produce, and steps aside automatically the moment
the real database is available. `pip install tzdata` upgrades every location to
the full database with no code change.

---

## Layout

```
server.py                    HTTP server, routes, background accuracy scorer
quantify_app/
  intelligence.py            forecasting, drivers, day assembly
  transactions.py            order history and day scoring
  explain.py                 turns a record into sentences, and the offline writer
  ai.py                      Claude client, skill loading, caching
  skills/*.md                how the model is told to write
  menu_intelligence.py       reading register labels
  connectors.py              Square, weather, nearby activity
  billing.py                 Stripe, plans, cancellation
  auth.py                    accounts, two-step sign in, sessions
  qr.py                      QR encoder for enrolment
  timezones.py               place to time zone
  localtime.py               daylight saving without a system database
  email_brief.py             the morning email and confirmation codes
  database.py                schema and migrations
  seed.py                    sample data
web/                         the interface: one stylesheet, one script
tests/                       behaviour tests
```

## Tests

```
python -m pytest tests/ -q
```

Covers the forecast shape, the walk-forward backtest, event ranking, register
ingestion being idempotent, Square webhook signatures, sign in with TOTP, and a
copy test that fails the build if marketing language or an em dash gets into
anything an operator reads.
