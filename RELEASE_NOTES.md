# Quantify 3.3

## The forecast is revised every hour, and the record is honest about it

At the top of every service hour Quantify reads what the register has actually
rung today and revises what it expects the day to finish at. The model is not
refitted. Nothing has been learned about Tuesdays in the last sixty minutes.
What has been learned is whether this Tuesday is running to the shape a Tuesday
has here, so the morning call is held as the prior and only the pace is updated.

The part that took the most care is what a revision is **not** allowed to do.

What Quantify says before the doors open is written to a table that a database
trigger refuses to let anything update. Every accuracy figure in the product,
the day score in History and the track record on the Results tab, is measured
against that call and nothing else. A revision made at nine in the evening has
watched almost the whole day; reporting its error as accuracy would make the
record worthless. So a day that was called badly stays called badly, no matter
how well the revisions caught up. There is a test that sets up a day where the
morning call was half of what sold and every revision was perfect, and fails if
the score comes out above 55%.

The one thing a revision is credited with is warning time: the hour at which the
running call first came within ten percent of where the day actually finished.

**Two real bugs were found and fixed on the way:**

- A connected register writes today's partial totals into `sales` as orders
  arrive, and the scorer had no upper bound, so it would compare a whole day's
  forecast against three hours of trade and store the miss permanently. Nothing
  rescored a day once it had a row. A day is now only scored once it is over
  where the location is, not where the server is.
- Hourly sales are keyed on the calendar date, so a one in the morning sale from
  Friday night was being filed under Saturday. A bar open until 2 AM would have
  read its last two hours as zero every night of its life, and its hour curve
  would have pulled the wrong day's early morning trade into Saturday. Hours are
  now read and written as slots against the trading date.

## Opening and closing hours

Asked during signup, changeable in Settings. A close after midnight is stored
past 24, so a bar open 11 AM to 2 AM is a fifteen hour day and every span in the
codebase stays a plain subtraction. The hour strip on Today now follows each
location's own hours instead of a fixed range.

## What a day actually kept

History showed what the register rang and stopped there. It now shows what was
left after the food and the hours were paid for, beside it.

- **Food** comes from a cost share per category, defaulted from what the menu
  interpreter already worked out each item is, and editable per category.
- **Wages** come from the hours the doors were open, staffed against the orders
  that actually landed in each hour, at a wage that defaults to the local
  minimum and is meant to be typed over. Payroll tax, unemployment and workers'
  comp are added on top at a rate you set.
- **Everything else** is a list you write: rent, insurance, the card machine,
  whatever only your business pays for, each spread across the days it covers.

On the seeded burger restaurant, a $3,138 day comes out at $924 of food, $913 of
wages, and $1,302 kept. It is labelled an estimate built from settings you
control, everywhere it appears, because that is what it is.

## Smaller things

- The landing page no longer fades things in as they scroll past. Two elements
  now move as a function of scroll position, which means scrolling back up runs
  the motion backwards. Nothing else on the page moves.
- The demo panel appends each line once instead of rebuilding itself on every
  tick, which is what made it look like it was reloading. Every figure in it now
  comes from live data, so its title bar can no longer say Tuesday while its body
  says Friday.
- The landing page menu table showed "Read from the till label" for any item a
  hardcoded keyword list did not recognise. It now shows the real components
  from the database.
- The city and ZIP typeahead existed only on a signup screen an account can
  reach exactly once. It is now on the Settings location fields too.
- Inputs written without a `type` attribute matched none of the field styling,
  because an attribute selector reads the markup and not the IDL default. That
  was thirteen inputs, including the six digit code boxes.
- A close after midnight was printed on Today as "2:00 PM".

## Quantify 3.2

## Any item, opened

Click any item anywhere in the product and you get its whole record. Not the top
sellers, any of them. The one that sells four a day is analysed exactly as hard as
the one that sells four hundred, because the operator who wants to know how the
crème brûlée is doing is the operator who will keep paying for this.

What opens:

- **How many to make**, and what it costs to be wrong at five levels around that
  number. Leftovers costed at food cost, missed sales at lost margin, both read off
  the item's own outcome distribution rather than a bell curve.
- **Which days it belongs to.** A one-way ANOVA across the seven weekdays with the
  share of variation the weekday actually explains, then a per-day table of typical,
  middle half, quietest, busiest, and how many days each rests on.
- **What actually moves it.** Twelve conditions tested against what is left once
  weekday and long-run drift are removed by least squares. Every test carries a
  t-statistic and a p-value, and the whole family is corrected for false discovery
  with Benjamini-Hochberg, so one weak result cannot get in by being one of twelve.
- **Whether it has changed**, by Welch's t-test on the last four weeks against the
  four before, plus the long-run slope and the same weeks last year.
- **What it trades against.** Pearson correlation on residuals, so "busy days are
  busy" does not masquerade as a relationship. A negative pairing means customers
  are choosing between the two.
- **How well we have called it before**, per item, from the scored record.
- **The days it did something nobody expected**, and what was happening on them.

Three things this refuses to do, all of them things a dashboard usually does:

1. **It reports what did not hold up.** Conditions that were tested and rejected are
   listed with their numbers. Silence about a failed test is how a tool builds a
   reputation it has not earned.
2. **It separates real from useful.** An effect can clear every significance bar and
   still move the item by less than one unit. Those are labelled "real, but too small
   to change what you make" rather than dressed up as a finding.
3. **It has a middle verdict.** Below a 5% false-discovery rate a condition is
   established; between 5% and 20% it is shown as leaning, not proven, with how many
   more days would settle it. Everything else is rejected.

## The number to make is now the number to make

The Today table used to show what the model expected to sell and call that column
"Make". Those are different questions. Running out costs the whole margin and
sometimes the customer; throwing one away costs the food. So the table now has both
a **Make** column, set at the newsvendor critical fractile for that item's own
economics, and a **Will sell** column, with the chance you still run out underneath.

## The honest range got tighter and more honest

The predictive spread used to be read straight off two years of the same weekday. For
an item that has been growing all year, that mixed genuine day-to-day risk with the
growth itself and reported a range wider than reality. Each past day is now converted
to a ratio against the level that was normal around it, which cancels the drift, and
those ratios are applied to today's forecast. Same method, narrower and correct.

## Quantify 3.1

## There is a landing page now

`/` is a real landing page rather than a sign-in form. It explains what the product
does, and every figure on it is computed live from the sample dataset by the same
code that runs inside the app: today's forecast, the comparison against a normal day
of that weekday, and the measured accuracy over the last scored days. Nothing on it
is a marketing number typed into a template.

Routes are now `/` for the landing page, `/signup`, `/login`, and `/app`. Sign in and
sign up link to each other, which they did not before.

## Signing up works for more than one account

Creating an account used to be a one-time first-run screen: once a single account
existed, there was no way to make another. Now anyone can sign up. The first account
on a fresh install joins the pre-seeded sample workspace so it opens into a working
product. Every account after that gets its own workspace and its own sample location,
sized to what they say they serve. Two businesses never see each other's sales.

## Two-step sign in is optional

It used to be mandatory and it was the second screen of signup, which is the wrong
place for it. It now lives in Settings under Account and security, off by default,
with a QR code to turn it on and a password check to turn it off. Backup codes only
appear once it is on.

## Signup confirms your email instead

The second screen is now a six-digit code sent to the address you signed up with.
Confirming it is the one gate on operating data, because an unconfirmed address means
no password reset and no morning brief.

**Email is not being sent yet**, which is deliberate for an unlaunched product. The
message is written to `data/outbox` as a real `.eml` file and the code is shown on
screen so signup can be completed. Setting `POSTMARK_SERVER_TOKEN` or `SMTP_HOST`
stops the code appearing on screen and sends it for real, with no code change.
`docs/SENDING_EMAIL.md` walks through it.

## Locked out of your own database

`python server.py --accounts` lists every account, and
`python server.py --set-password you@yourdomain.com` sets a new one and signs every
other session out.

## The panel on the sign-in page behaves

It plays once, settles, and stops. It no longer loops, no longer restarts when the
form beside it changes, and cannot be selected or clicked.

## Fixed

- The background accuracy scorer took one location all the way back through two years
  before starting the next. It now gives every location a recent record first.
- The landing page showed whichever location sorted first alphabetically, which could
  be one with no accuracy record. It now shows the best-evidenced one.
- A redirect could loop forever if the history API were blocked.

---

# Quantify 3.0

A rebuild of the interface and a widening of what the product actually reports.

## Every number now arrives with a comparison

The rule this release is built on: a percentage with nothing beside it is not
information. Anywhere a figure appears, the thing it is being measured against
appears with it, in the units a kitchen counts.

- The four headline tiles each carry the same figure for a normal day of that
  weekday, the difference in money and in items, and the number of comparable
  days the judgement rests on.
- Every driver in "Why today looks this way" reports its effect as a percentage,
  a unit count, and a money amount, plus the evidence behind it: "104 past
  Tuesdays at this location", "44 past days here with similar conditions".
- Actions name quantities. "Prep 7 more drip coffee than usual, 68 expected
  against a normal Tuesday of 61" instead of a coloured arrow.

## Confidence is grounded in the measured record

A background worker replays closed days: it fits on everything before the day,
predicts, and compares against what the registers rang. Once seven days are
scored, that measured accuracy overrides the model's own confidence figure. A
location running at 90.7% no longer displays 95% confidence.

## History

A new screen, in three parts.

- **By day.** Closed days newest first, with sales, orders, items, average order,
  and the accuracy of the forecast for that day. Filter by month, quarter, year,
  or all time. It loads more as you scroll rather than fetching years at once, so
  it stays usable on a tablet.
- **Orders.** Every ticket: time, what was ordered, channel, payment, and total.
  Same scrolling behaviour, reading one day at a time.
- **Track record.** Called against sold, and which items keep missing.

Opening a day gives the full post mortem: hour by hour called against sold, item
by item, where the orders came from, and a written verdict that names the items
carrying the error and says plainly whether the miss would have changed anything.

## Menu composition

Open any item to see what it is physically made of, with a share, a kitchen
quantity, and a confidence per component. This is what turns a demand change into
a prep decision. It is labelled as an estimate from the register label until a
recipe is connected, and it says so.

## The writing layer

Quantify computes every number itself. A language model is used only to put those
numbers into sentences, and is given a set of skills rather than one prompt:
`plain-language`, `demand-analysis`, `menu-composition`, `forecast-review`, each
a file in `quantify_app/skills/`. Claude Opus 5 is the default. Without a key,
Quantify writes the same sections itself from the same record.

## Plans, payment, and cancelling

Stripe handles cards, invoices, tax, and dunning. No card number reaches this
application.

Cancelling sits at the bottom of Account and security, visible, with a real
button. It asks what went wrong and records the answer before anything is
cancelled, offers a conversation once, and gives an unmistakable way to decline
and proceed. Access continues to the end of the paid period and can be resumed
until then.

## Signing up

- Enrolment is a QR code generated locally, with no external service. The key is
  128 bits, so the typed fallback is 26 characters instead of 32, grouped in
  fours.
- Backup codes moved out of signup and into Account and security, where they are
  optional.
- A short onboarding asks for the business name, where it is, and what matters
  most, then opens straight into working software.

## Time zones

Place fields accept a city, a state, an abbreviation, a ZIP code, or a zone name,
and show back what was understood. Windows ships no IANA database, which
previously broke the morning email scheduler on every Windows install;
`quantify_app/localtime.py` now carries correct daylight saving rules and steps
aside when the real database is present.

## The interface

Rebuilt. One neutral surface family, one accent, hairline borders, soft radii,
tabular numerals throughout. The sidebar is a tint of the same palette rather
than a slab of a different one. Inputs are rounded. The page keeps itself current
without anyone reloading it.

## Fixed

- `zoneinfo` failing on Windows, which broke the morning email scheduler and
  Square order ingestion on every Windows install.
- Register freshness was measured against the wall clock, so opening a brief for
  a past date wrongly reported the feed as stale.
- Order counts on the history list disagreed with the day detail because one
  estimated and the other counted.
