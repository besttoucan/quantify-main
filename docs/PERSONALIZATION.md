# Location and cost references

Reviewed September 13, 2026. Quantify separates public references, approximate geography, and figures entered by the business. It does not infer an employer's payroll from its city.

## Pay and employer costs

Average hourly pay and employer payroll costs start blank. Wages, total costs and money kept remain unknown until both are entered. An unknown figure is returned as JSON `null`, never zero. Previously saved payroll loads have unverified provenance and must be confirmed through the Costs form before reuse. A saved percentage is the owner's planning input, not a tax rate certified by Quantify. Staffing hours and food percentages remain visible planning assumptions; resulting costs are estimates.

The Costs disclosure shows dated general wage references. The January 2026 state table is adjusted for Alaska, Washington DC and Oregon on July 1, and Florida on September 30. White Plains and the listed Westchester/Long Island/NYC places use the New York regional reference. Denver has its own 2026 and announced 2027 references. Other municipal, industry, tipped, employer-size and worker-specific rules are not exhaustively resolved. Unknown places or years return no reference; the app does not assign them the US federal wage. State alone cannot establish a workplace's exact wage jurisdiction.

Sources:

- [US Department of Labor state references](https://www.dol.gov/agencies/whd/minimum-wage/state), updated July 1, 2026.
- [New York minimum wage](https://dol.ny.gov/minimum-wage): $17 general untipped reference in NYC, Long Island and Westchester during 2026; $16 elsewhere in New York.
- [Denver Labor](https://www.denvergov.org/Government/Agencies-Departments-Offices/Agencies-Departments-Offices-Directory/Auditors-Office/Denver-Labor/Citywide-Minimum-Wage): $19.29 in 2026 and $19.84 in 2027, with jurisdiction and worker exceptions.
- [Alaska](https://www.labor.alaska.gov/lss/whhome.htm), [Oregon](https://www.oregon.gov/boli/workers/pages/minimum-wage.aspx), [Florida](https://floridajobs.org/florida-minimum-wage), and [California](https://www.dir.ca.gov/dlse/minimum_wage.htm) supply the scoped state details. Oregon's standard rate does not establish its Portland or nonurban rate; California has local and covered fast-food exceptions.
- [IRS Publication 15, 2026](https://www.irs.gov/publications/p15): employer Social Security is 6.2% up to $184,500 of each employee's taxable wages; employer Medicare is 1.45% without a wage cap. FUTA's gross 6% on the first $7,000 can be reduced by applicable credits. Additional Medicare withholding is employee-only. These components are references and are never added into an assumed total payroll load.
- [New York unemployment rates](https://dol.ny.gov/unemployment-insurance-rate-information) and [workers' compensation](https://www.wcb.ny.gov/content/main/Employers/workers-compensation-insurance.jsp): use the employer's rate notice and insurance policy. Unemployment experience, wage caps, tax credits, classification and insurance terms prevent a universal restaurant percentage.

## Geography

`quantify_app/reference/us_places_2025.json` contains 32,350 place records derived from the [2025 US Census Gazetteer](https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.2025.html), downloaded from its national places ZIP. Columns are state, published place name, GEOID, representative latitude and representative longitude. These public-domain records cover the 50 states, DC and Puerto Rico; they do not establish street addresses, municipal tax boundaries or locations elsewhere in the world.

`geography.resolve_place(city, region)` requires an unambiguous exact normalized city/state match. A match has status `city` and an explicit approximate-place label. Provider or owner coordinates require explicit provenance. Unsupported or ambiguous places have status `unverified`; their coordinates are not used for outside requests. A `(0,0)` placeholder is never a usable location.

On startup, legacy points without explicit provenance are resolved from city/state. Original values are preserved in the location's `geography_previous_coordinates` setting. Sample coordinates are not copied into a relocated account. Weather and event rows carry the geography key used for their request. Rows without a matching key are excluded from models and history annotations until re-synced; old source rows are retained. Unknown geography also neutralizes the internal daylight feature and prevents a daylight claim.

Weather and event providers receive the resolved point, and the same external event has a separate row for each business location. A city point is useful for area weather but is not an exact premises point; nearby-event distance remains approximate until the location has provider or owner coordinates. No global geography or payroll coverage is claimed.

Event attendance and distance have separate provenance. Ticketmaster venue capacity is not treated as attendance, and missing distances are not invented from the search radius. PredictHQ attendance is labelled as a provider estimate when supplied; its rank is never converted into a crowd count. Unknown values are exposed as `null` and contribute no crowd-based event impact. The legacy non-null database columns keep zero sentinels plus an `unverified` source; callers receive the provenance-checked values. Event estimates do not claim to count visitors to the business or only those attending during service hours.
