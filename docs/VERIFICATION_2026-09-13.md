# Verification record: September 13, 2026

The local integration has been checked with automated Python contracts, Chromium browser flows, and viewport screenshots. The app has five destinations: Today, Order, History, Updates, and Settings. This record describes the evidence available for this build and its limits.

## Automated checks

The final source at `c009e85` passed **142 tests and 5 subtests** in 45.36 seconds in the integration worktree. This includes the historical-item parity regression tests. `node --check web/app.js`, `node --check web/updates.js`, and the whitespace/diff check also pass. The documentation worktree independently passed the preceding 140-test suite before merging the same final source; its remaining changes are documentation and unchanged screenshot copies.

The suite covers forecast and Make contracts, new-item/empty states, cache invalidation, history scoring, menu ownership, recipe validation, count units, order recording, account/location authorization, Updates persistence, pricing rules, spend accounting, and personalization. Contract tests use temporary databases and controlled provider responses. Passing them does not establish live provider availability.

Documentation checks separately matched 68 listed route entries to server dispatch, parsed five JSON examples, and resolved the product documentation's local links.

## Browser evidence

The full scratch evidence is organized under `scratchpad/codex-design/<run>/`. Each named run below has a JSON report and its associated images. Selected, inspected copies are retained in [screenshots/current](screenshots/current/README.md). Reports describe the revision or working tree at capture time where recorded; these runs were taken during integration, not all from one frozen revision.

| Run | Completed evidence | Result and limit |
| --- | --- | --- |
| `integrated-final/report.json` | 75 viewport captures at 1024x768, 820x1180, 390x844, 1440x900, and 1707x1067 | Zero captured runtime errors. Covers Today tabs, item sheet, Order, History, Updates, and Settings. Five day-sheet captures were skipped because the initial recent dates contained no sample sales. This report alone does not verify day sheets. |
| `integrated-public/report.json` | 16 captures: landing, sign-in, account creation, and password reset at 1024, 390, 1440, and 1707 widths | Zero errors and zero skipped screens. Visual/form presentation coverage; no production account or provider delivery claim. |
| `current-public/report.json` | Four landing/pricing captures at 390 and 1440 widths | Five sample navigation tabs respond, the $39/$99 plans display, and no horizontal document overflow was found. |
| `updates-integrated-v3/report.json` | Functional checks at widths 320, 390, 1024, and 1440 | Local API/SQLite receipts survive reload; old notices stay suppressed; Earlier updates remain readable; refresh completes; all five navigation targets remain available. Zero recorded errors. The notes were deliberately seeded test observations in a disposable database. |
| `final-history/report.json` | Latest rerun: 12 captures and 10 checks at 390 and 1024 widths | Zero errors and zero findings. Earlier reaches August 26 sales; unknown labor totals remain unknown; Costs/Menu links close the sheet and navigate; historical item dates remain fixed and no Adjust action appears. Day Expected and item Expected both equal 46 in the checked example, with reconstruction labelled. This rerun supersedes the earlier partial report and mismatch captures. |
| `final-history/popup-report.json` | Explicit notice-plus-save checks at 390 and 1024 widths | Save remains reachable with a visible notice. The notice ends 32 px above the Save control in both cases. This dedicated run covers the phone popup that was not visible in one general History capture. |
| `final-persona-weather/verified/report.json` | Six captures at 820 and 1024 widths | All 14 outlook rows render. Unavailable weather has `available:false` and null values; no fabricated 68/52-degree weather or `null`/`undefined` temperature text appears. Labels are unclipped and wrap normally. Costs names Westchester County and shows dated wage, IRS, unemployment, and workers' compensation sources. Zero console/page errors; browser and server stopped. |

The broad run found horizontally clipped "Seasonal estimate" text in the portrait outlook. The final weather/personalization run confirms the later fix: unavailable weather is omitted from the rows and the remaining labels fit. The earlier broad screenshot is not used as final outlook evidence.

An additional component fixture run, `updates-ui-timely-qa/report.json`, passed seven behavior scenarios and three touch layouts. It covered delayed refresh with the old feed retained, retry after failure, late responses from another location/user, shown/read persistence, dismissal and expiry, modal/typing deferral, escaped feed text, and timer-only pattern notices. Those responses were Playwright fixtures. They supplement the real local API checks; they do not replace them.

The final source was also restarted in the existing local application at port 8787 after backing up its database and preserving its runtime environment. Health returned HTTP 200. Read-only navigation checked all five destinations at 390 and 1024 widths: ten checks, no JavaScript errors, and no horizontal overflow. Two Today screenshots were captured and the tablet view was visually reviewed. Bootstrap confirmed the local writer with no selected live model. This smoke check used the existing local database, unlike the isolated test runs; its images are not included in the curated sample collection.

## Data and execution conditions

Except for the final local smoke check described above, browser runs used isolated copies of the supplied sample SQLite database. Sample businesses, sales, recipes, counts, and dates are demonstration data. Deliberate test observations were identified as fixtures in the Updates functional runs. The curated ordinary feed image instead uses the sample data's actual local feed.

Isolated authenticated UI runs used the local auth bypass, disabled schedulers, and disabled external model calls. Public runs did not use the auth bypass. Relevant form tests saved only to disposable databases. No real supplier order, payment, or customer communication was submitted by these checks. Test browsers and temporary servers were stopped after the runs; the user's existing local service remains running on the integrated source.

Screenshots are viewport captures, preserving touch emulation at tablet/phone sizes. They show specific states, not every scroll position or every possible label. Numbers can differ between runs because the copied databases, dates, saved assumptions, and integration revisions differ. The images are layout and behavior evidence, not measured business outcomes.

## External services and remaining limits

- **Stripe:** local price/plan/capacity rules and signed-event handling have tests. Live checkout, provider portal configuration, real subscription changes, and charges were not exercised.
- **Email:** local validation, formatting, draft/outbox outcomes, and order-recording boundaries were checked. Actual SMTP/Postmark delivery, inbox rendering, spam placement, and supplier acceptance were not tested.
- **Models:** local fallback and recorded-spend rules have tests. No live model quality, latency, token bill, or provider limit was measured. The application spend guard counts completed calls; it is not an atomic hard billing cap under concurrency or billed failures. See [Architecture](ARCHITECTURE.md) and [Constitution](CONSTITUTION.md).
- **Square and context providers:** these runs did not connect a live register, fetch its history, or validate an actual production webhook. Weather/event behavior was checked with local data and controlled responses, not a production account's current coverage.
- **Geography and wages:** source coverage is bounded. Approximate US place points are not street-address verification. Public wage references do not establish each worker's applicable rules or the employer's actual payroll costs. Unknown event attendance and distance are not filled with invented values. See [Personalization](PERSONALIZATION.md).
- **Pricing:** $39 for one active location and $99 for up to three are launch hypotheses. This work did not establish willingness to pay, retention, savings, forecast superiority, or production unit costs. Legacy terms are retained. See [Pricing decision](PRICING_DECISION.md).
- **Deployment and accessibility:** Chromium with emulated touch was used. These checks are not a native Safari/device matrix, a screen-reader audit, a load test, or proof of hosted scheduler, secret-storage, backup, and monitoring readiness.

The original audit contained roughly 740 findings, including overlap. This record does not claim that each finding was independently retested or individually closed. It records the implemented behavior and concrete checks completed during this integration. The current [Product guide](PRODUCT_GUIDE.md) and [API reference](API_REFERENCE.md) describe the supported contracts.
