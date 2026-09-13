# Interface design decisions

## Five destinations

| Destination | Purpose |
|---|---|
| Today | Make quantities, reasons, hourly demand, and the next two weeks |
| Order | Counts, supplier assignments, prepared orders, and records of orders placed |
| History | Closed days and expected-versus-sold accuracy |
| Updates | Current operating observations and recent earlier updates |
| Settings | Location, menu, costs, and account |

The user explicitly requested Updates as a separate destination. It supplements the daily plan with persistent, dated observations. A person can request a new check with **Update me** without navigating through every other screen.

## Today puts the work within reach

The headline and three figures establish the day. A short stock strip and a small set of actions follow. One tabbed panel contains the make list, reasons, hourly demand, and two-week outlook. Item and day sheets expose details when requested. The default panel is the make list.

## Stable dimensions and visible data

Content is bounded on a wide display. Headline typography does not scale with viewport width. The header and content share alignment; sticky navigation uses `overflow-x: clip` so an ancestor does not become an unintended scroll container. At 900px and below, five labeled buttons form a bottom navigation with a safe-area inset and matching reserved page space. Touch controls receive at least 44px height. Sheets fit the viewport and preserve close controls and keyboard focus.

Charts use blue expected values, dark actual values, and a green current/peak value. Line charts combine solid/dashed strokes with written keys, quantities, units, and periods. Color is never the only distinction. Necessary chart labels use readable text rather than low-contrast border colors.

## Updates stay relevant

Important, unseen, unexpired conditions can appear in a bottom-right notice on opening or returning. Repeated item/time patterns may notify during a visible session near their window; opening the app does not replay them. These patterns describe aggregate sales, not an identified customer's expected return.

Read and seen state persist per person. Expired or resolved observations move to recent history and lose their action buttons. An optional model can rank supplied observations; it cannot invent numerical claims or customer identities. The feed still works when a model is not configured.

## Supplier handoff is explicit

Known supplier choices prefill website shortcuts. Other suppliers can use the same saved website, email, and representative details. Opening a supplier site or copying a prepared list does not place an order. The user confirms an order placed outside Quantify; configured email records success only after the mail provider accepts it. Failed or local-outbox mail keeps the prepared lines available. Connection help opens a draft in the user's mail app.

## Location and cost claims are inspectable

Use matched geographic context for the selected location. Unknown geography must not borrow another city's weather or events. Statutory wage references include jurisdiction, effective date, and official source. Employer-specific payroll additions require real inputs. Missing costs stay unknown throughout the interface rather than appearing as zero.

## Verification

Use isolated copied databases for browser review. Check navigation, saves, timing, expiry, failed requests, keyboard use, and narrow/wide layouts. Viewport screenshots are required for touch emulation with the installed Chromium; full-page screenshots disable its coarse-pointer mode. Automated geometry checks identify review candidates and do not establish accessibility conformance.
