# Quantify product guide

Quantify helps a location decide what to make, when demand is likely to arrive, and what to buy before supplies run short. Its five destinations are Today, Order, History, Updates, and Settings. The location picker changes the workspace for all five.

## Today

Today opens on the selected location's date. The headline compares expected sales with a normal day of the same weekday. It shows the expected units and busiest hour alongside the comparison. The date controls also open a future plan or a past day's result.

Running low uses saved shelf counts and recipe usage. It names the count date and relevant supplier deadline. If nothing has been counted, it asks for a count on Order.

The main panel has four tabs:

| Tab | What it answers |
| --- | --- |
| What to make | How much of each menu item to prepare, compared with expected sales and a normal day |
| Why | Which available sales and local context explain the change, and how uncertain it is |
| Through the day | When sales are expected, with each trading hour labelled |
| Next two weeks | Which upcoming days need attention; selecting a day opens its plan |

**Make**, **Expected**, and **Normal** have different jobs. Expected is the sales forecast. Normal is the comparable weekday baseline. Make is the suggested preparation quantity, including the cost of running out or having leftovers where the data supports that calculation.

Open an item for its range, comparable days, and preparation detail. Adjust saves an exact Make quantity for that item and date, including zero. A reason is optional; the adjustment records who saved it and when. Back to suggested removes it. Changing Make does not rewrite Expected or Normal.

Items with fewer than seven selling days remain visible with "New item. No number yet." They do not contribute an invented quantity to forecast totals or the buying list. A location without sales shows the steps to connect its register or add its menu.

## Order

Order turns Make quantities for the selected planning window into ingredient usage. The default window is three days. A recipe needs usable quantities before it can produce a buying quantity; missing recipes and uncertain quantities remain visible for review.

Assign an ingredient to a supplier and enter how it is bought, such as a case of 24 units or a 20 lb bag. Enter an On hand count in the shown unit or in configured packs. Zero means the shelf was counted empty. Clearing the field removes the count, leaving it unknown. Count dates stay visible because old counts can make a buying suggestion unreliable.

The buying list combines forecast usage, saved counts, recorded incoming orders, pack sizes, delivery days, lead time, and cutoff time. Counts use common units so changing the displayed unit does not change what was counted. Suggested purchases round to the configured pack. Ingredients expressed only as a share of another ingredient do not receive a made-up case count.

Suppliers are managed on Order. Familiar supplier choices provide a starting point for contact details. "My supplier isn't listed" keeps a manual path available, with help requesting a connection. A supplier website is an external link; it does not mean a direct ordering integration is connected.

Review quantities and delivery date before placing an order.

| Action | What gets recorded |
| --- | --- |
| Email order through a configured mail provider | Sent, failed, or saved to the local outbox, according to the actual result |
| Open in a mail app | A draft first; a sent record only after "I sent this order" |
| Open the supplier's site | An external page first; a placed record only after "I placed this order" |
| Copy the list | Clipboard text only |

Orders placed keeps the supplier, lines, expected delivery, channel, status, time, and person who recorded the order. Opening a draft or copying text does not establish an incoming delivery. Quantify does not purchase autonomously or verify supplier acceptance of an order made elsewhere.

## History

History has Days and Track record tabs. Days starts with yesterday in the location's time zone and loads earlier calendar days in pages. Quiet or missing dates remain visible. Date filtering and loading more preserve the current list.

Open a day to compare Sold with Expected by item and through the day. The day also shows available costs, orders, and a short review. A day marked closed has no sales review. Order records identify whether they came from the register or were rebuilt from aggregate sales; rebuilt tickets are not original receipts.

Track record measures forecast error against completed sales. When an opening forecast was saved before service, the record uses it. Otherwise the comparison is reconstructed using prior sales and labelled accordingly. A forecast revised after seeing part of the day's sales does not replace the opening comparison. Whole-item quantities agree across the score, day detail, item sheet, and chart.

Cost estimates use the location's saved assumptions. If average pay or employer payroll costs are missing, wages and the amount left after costs remain unknown. No cost estimate is proof of accounting profit, avoided waste, or savings.

## Updates

Updates keeps a persistent list of stock concerns, an old register feed, a changed outlook, or repeat item-and-time sales patterns. Each note includes its evidence, when it applies, and a relevant action.

**Update me** checks the latest available sales and counts. The current feed stays visible during the check. If it fails, Try again keeps the previous notes available. An optional writing service can rank supplied observations; the recorded facts, quantities, and deadlines remain the source of the message.

The unread badge counts current and upcoming notes for this person and location. Mark read or Mark all read saves read status. A notice being shown is recorded separately from it being read. Both states survive reloading the app.

On opening or returning to the app, only important, active, unseen updates can appear in the corner notice. During a visible session, a five-minute check may also surface an active sales pattern before its window ends. One notice appears at a time. Typing or an open sheet defers it. Dismiss closes it; View update opens its note. Expired or resolved notes remain under Earlier updates and never generate a new notice. A visible notice disappears when its time window ends.

Time patterns describe aggregate item sales on comparable weekdays. They do not identify a customer or infer who will visit.

## Settings

Settings has Location, Menu, Costs, and Account tabs.

**Location** holds the name, concept, city/region, time zone, trading hours, register connection, and Morning email. Dates, supplier cutoffs, and the email schedule use the location's clock. Geographic matches are approximate place points, not street-address verification. Unresolved geography is shown explicitly and cannot use another city's weather or events. See [Personalization](PERSONALIZATION.md) for resolver coverage and dated sources.

Square is the implemented register connector. Other provider names may describe an availability or approval boundary; they do not establish a working connection. Each added location starts empty and needs its own register or menu. Sample onboarding data is labelled as sample data.

**Morning email** uses the saved recipient, local send time, and enabled setting. Its time zone is the location's time zone. Preview shows the HTML and text that will be sent. Send test uses an address already on the account. Without a mail provider, the result is a saved email file, not a delivered email. Disabling email allows an empty recipient.

**Menu** keeps the register's raw item name and price, with a readable name and suggested recipe alongside them. Text import previews rows before saving and requires a usable price. It does not replace register-owned items. Missing recipes fill in the background while the menu stays usable. Suggested recipes are estimates to review. An owner's saved recipe is identified as confirmed and is not overwritten by a late background result. Recipe shares must total about 100%, accepted from 95% through 105%.

**Costs** separates food-cost assumptions, staffing, average pay, employer payroll costs, and recurring expenses. Named and dated wage references help review the location, but a legal wage floor does not become its average pay. Average pay and employer payroll percentage need the owner's values before wages and complete totals can be calculated. The screen shows which assumptions or saved figures it uses. See [Personalization](PERSONALIZATION.md).

**Account** holds the profile, password, optional two-step sign-in, recovery codes, and plan. Email confirmation is required to enter a new workspace. Two-step sign-in can be enabled from Account.

## Plans and setup boundaries

The monthly launch plans are $39 for one active location and $99 for up to three. Both include the five destinations and Morning email. The trial lasts 14 days without a card. Existing Standard and Founding agreements retain their stored offers. Payment provider configuration is required before checkout can take payment; a pending checkout session does not mark an account paid. See [Pricing decision](PRICING_DECISION.md) for exact plan and legacy rules.

The core forecast does not require daily sales entry, a daily prompt, or a physical inventory ledger. Counts, pack sizes, confirmed recipes, and supplier details improve buying suggestions because sales alone do not provide those facts. Private events, physical shelf contents, exact ingredient costs, and supplier acceptance require their own evidence.
