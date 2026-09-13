# Current verification screenshots

These ten viewport images were selected and visually inspected on September 13, 2026. They show the local application using sample data or the public sample presentation. They contain no account profile, payment details, or live customer data. Each file is an unchanged copy of its source PNG; [manifest.json](manifest.json) records source paths, sizes, and SHA-256 hashes.

The source runs were captured during integration at different revisions and with separate database copies. Treat the numbers as sample values, not a business performance claim or a cross-image numeric comparison. Earlier screenshots elsewhere in `docs/screenshots/`, including `_small`, remain preserved and may show the previous interface.

| Image | Viewport | Source run | What to inspect |
| --- | --- | --- | --- |
| [Today](today-ipad-1024.png) | 1024x768 | `integrated-final/ipad-today.png` | Make, Expected, and Normal; stale-register date is visible |
| [Through the day](hours-ipad-1024.png) | 1024x768 | `integrated-final/ipad-today-hours.png` | Labelled hourly bars, distinct busiest hour, and explicit stale-data limit |
| [Order](order-phone-390.png) | 390x844 | `integrated-final/phone-order.png` | Count fields, purchase quantities, and five phone destinations |
| [Updates](updates-phone-390.png) | 390x844 | `integrated-final/phone-updates.png` | Ordinary local sample feed, evidence, effective windows, and read actions |
| [Next two weeks](ahead-portrait-820.png) | 820x1180 | `final-persona-weather/verified/820-ahead-labels.png` | Final labels fit; unavailable weather is omitted |
| [Costs references](costs-references-ipad-1024.png) | 1024x768 | `final-persona-weather/verified/1024-costs-references-open.png` | Expanded Westchester County reference and named sources |
| [Public landing](landing-desktop-1440.png) | 1440x900 | `current-public/1440-landing.png` | Public sample with five destinations |
| [Public pricing](pricing-desktop-1440.png) | 1440x900 | `current-public/1440-pricing.png` | $39 and $99 monthly plans and trial wording |
| [Historical day](history-day-ipad-1024.png) | 1024x768 | Latest `final-history/ipad-day-unknown-costs.png` | Historical item Expected is 46; missing labor costs remain explicit |
| [Historical item](history-item-ipad-1024.png) | 1024x768 | Latest `final-history/ipad-historical-item.png` | The same Expected 46 and date, with reconstructed plan/expectation labels |

The historical images come from the corrected final rerun, not the earlier mismatch captures. The Updates image is the ordinary sample feed, not the deliberately seeded coffee-note fixture used in the separate functional test.

See [Verification record](../../VERIFICATION_2026-09-13.md) for check counts, skipped coverage, test conditions, and external-service limits. No image here proves a live payment, provider sync, email delivery, supplier order, or customer outcome.
