from __future__ import annotations
from datetime import date
from typing import Any
from . import timezones

# ---------------------------------------------------------------------------
# Minimum wage
# ---------------------------------------------------------------------------
# General January 2026 references, with dated midyear changes below. Local,
# tipped, industry and employer-size rules are not exhaustively resolved.
# These references never replace the owner's actual average hourly pay.
FEDERAL_MINIMUM_WAGE = 7.25
MINIMUM_WAGE_AS_OF = "2026-09-13"
MINIMUM_WAGE_REVIEWED = "2026-09-13"
MINIMUM_WAGE_SOURCE = (
    "US Department of Labor, July 2026 table; state and city sources checked September 13, 2026"
)

STATE_MINIMUM_WAGE: dict[str, float] = {
    "AL": 7.25, "AK": 13.00, "AZ": 15.15, "AR": 11.00, "CA": 16.90,
    "CO": 15.16, "CT": 16.94, "DE": 15.00, "DC": 17.95, "FL": 14.00,
    "GA": 7.25, "HI": 16.00, "ID": 7.25, "IL": 15.00, "IN": 7.25,
    "IA": 7.25, "KS": 7.25, "KY": 7.25, "LA": 7.25, "ME": 15.10,
    "MD": 15.00, "MA": 15.00, "MI": 13.73, "MN": 11.41, "MS": 7.25,
    "MO": 15.00, "MT": 10.85, "NE": 15.00, "NV": 12.00, "NH": 7.25,
    "NJ": 15.92, "NM": 12.00, "NY": 16.00, "NC": 7.25, "ND": 7.25,
    "OH": 11.00, "OK": 7.25, "OR": 15.05, "PA": 7.25, "RI": 16.00,
    "SC": 7.25, "SD": 11.85, "TN": 7.25, "TX": 7.25, "UT": 7.25,
    "VT": 14.42, "VA": 12.77, "WA": 17.13, "WV": 8.75, "WI": 7.25,
    "WY": 7.25, "PR": 10.50, "GU": 9.25, "VI": 10.50,
}

# Alabama, Georgia, Louisiana, Mississippi, South Carolina, Tennessee and
# Wyoming either have no state minimum or set one below 7.25. The federal floor
# applies to them, so the copy names the federal minimum, not the state.
NO_STATE_MINIMUM = {"AL", "GA", "LA", "MS", "SC", "TN", "WY"}

# Where a city or county sets a minimum above its state's, and Quantify already
# knows the city from timezones.CITIES. This is not a complete list of local
# minimums in the United States and does not try to be.
LOCAL_MINIMUM_WAGE: dict[str, tuple[float, str]] = {
    "new york": (17.00, "New York City"),
    "new york city": (17.00, "New York City"),
    "nyc": (17.00, "New York City"),
    "manhattan": (17.00, "New York City"),
    "brooklyn": (17.00, "New York City"),
    "queens": (17.00, "New York City"),
    "bronx": (17.00, "New York City"),
    "staten island": (17.00, "New York City"),
    "yonkers": (17.00, "Westchester County"),
    "white plains": (17.00, "Westchester County"),
    "scarsdale": (17.00, "Westchester County"),
    "new rochelle": (17.00, "Westchester County"),
    "mount vernon": (17.00, "Westchester County"),
    "long island": (17.00, "Long Island"),
    "hempstead": (17.00, "Long Island"),
}

STATE_NAMES: dict[str, str] = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "DC": "the District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana",
    "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
    "PR": "Puerto Rico", "GU": "Guam", "VI": "the US Virgin Islands",
}


def resolve_state(region: str, city: str = "") -> str:
    """The two-letter state code for a location.

    locations.region already holds it for every location Quantify creates. The
    city fallback exists for rows imported from a register that filled the field
    with a full state name, or left it empty.
    """
    code = (region or "").strip().upper()
    if code in STATE_MINIMUM_WAGE:
        return code
    named = timezones.STATES.get((region or "").strip().lower())
    if named:
        return named[0]
    if code:
        return ""  # An explicit unknown region must not become a guessed US state.
    placed = timezones.CITIES.get((city or "").strip().lower())
    if placed and placed[0] in STATE_MINIMUM_WAGE:
        return placed[0]
    return ""


def state_minimum_wage(state: str, on_date: date | None = None) -> float | None:
    state = (state or "").upper()
    target = on_date or date.today()
    if target.year != 2026:
        return None
    rate = STATE_MINIMUM_WAGE.get(state)
    if state in {"AK", "DC", "OR"} and target >= date(2026, 7, 1):
        rate = {"AK": 14.0, "DC": 18.4, "OR": 15.55}[state]
    if state == "FL" and target >= date(2026, 9, 30):
        rate = 15.0
    return rate


def minimum_wage(state: str, city: str = "", on_date: date | None = None) -> tuple[float | None, str]:
    """A general reference, not a determination of a worker's applicable rate."""
    state = (state or "").upper()
    target = on_date or date.today()
    local = LOCAL_MINIMUM_WAGE.get((city or "").strip().lower())
    state_rate = state_minimum_wage(state, target)
    if state == "CO" and city.strip().lower() == "denver" and target.year in {2026, 2027}:
        return (19.29 if target.year == 2026 else 19.84), "City and County of Denver"
    if state_rate is None:
        return None, ""
    if state == "NY" and local and local[0] > state_rate:
        return local[0], local[1]
    if state in NO_STATE_MINIMUM:
        return FEDERAL_MINIMUM_WAGE, ""
    return state_rate, STATE_NAMES.get(state, state)


DOL_SOURCE = "https://www.dol.gov/agencies/whd/mw-consolidated"
NY_SOURCE = "https://dol.ny.gov/minimum-wage"
DENVER_SOURCE = "https://www.denvergov.org/Government/Agencies-Departments-Offices/Agencies-Departments-Offices-Directory/Auditors-Office/Denver-Labor/Citywide-Minimum-Wage"
IRS_SOURCE = "https://www.irs.gov/publications/p15"
NY_UI_SOURCE = "https://dol.ny.gov/unemployment-insurance-rate-information"
NY_COMP_SOURCE = "https://www.wcb.ny.gov/content/main/Employers/workers-compensation-insurance.jsp"


def wage_reference(state: str, city: str, on_date: date | None = None) -> dict[str, Any]:
    target = on_date or date.today()
    rate, place = minimum_wage(state, city, target)
    source = DOL_SOURCE
    effective = None
    scope = "General state or federal reference; local, tipped, industry and employer-size rules are not verified."
    if state == "NY":
        source = NY_SOURCE
        effective = f"{target.year}-01-01"
        scope = "General untipped reference for the named region. Tip credits and industry exceptions are not included."
        if place == "New York":
            scope = "New York state floor; NYC, Long Island and Westchester have a higher $17 reference. This city's county is not verified."
    elif state == "CO" and city.strip().lower() == "denver":
        source = DENVER_SOURCE
        effective = f"{target.year}-01-01"
        scope = "General untipped Denver reference. Confirm the business is inside city boundaries."
    elif state == "CA":
        source = "https://www.dir.ca.gov/dlse/minimum_wage.htm"
        effective = f"{target.year}-01-01"
        scope = "General California reference. Some cities are higher; covered fast-food workers have a separate $20 floor."
    elif state == "OR":
        source = "https://www.oregon.gov/boli/workers/pages/minimum-wage.aspx"
        scope = "Oregon standard-rate reference only; the Portland boundary and nonurban counties have different rates."
    elif state == "AK":
        source = "https://www.labor.alaska.gov/lss/whhome.htm"
    elif state == "FL":
        source = "https://floridajobs.org/florida-minimum-wage"
    if target.year == 2026 and state in {"AK", "DC", "OR"} and target >= date(2026, 7, 1):
        effective = "2026-07-01"
    if target.year == 2026 and state == "FL":
        effective = "2026-09-30" if target >= date(2026, 9, 30) else "2025-09-30"
    return {"rate": rate, "place": place or ("Federal" if state in NO_STATE_MINIMUM else ""),
            "effective_from": effective if rate is not None else None,
            "reference_year": target.year,
            "valid_through": f"{target.year}-12-31" if rate is not None else None,
            "checked_on": MINIMUM_WAGE_REVIEWED, "source_url": source,
            "scope": scope if rate is not None else "No verified wage reference for this place and date. Enter what you pay.",
            "status": "reference" if rate is not None else "unverified"}


def payroll_reference(state: str, on_date: date | None = None) -> dict[str, Any]:
    target = on_date or date.today()
    us = state in STATE_MINIMUM_WAGE and state not in {"PR", "GU", "VI"}
    components = []
    if us and target.year == 2026:
        components = [
            {"name": "Employer Social Security", "percent": 6.2, "wage_base": 184500,
             "detail": "Applies up to $184,500 of each employee's 2026 taxable wages."},
            {"name": "Employer Medicare", "percent": 1.45, "wage_base": None,
             "detail": "No wage cap. Additional Medicare withholding is employee-only."},
            {"name": "Federal unemployment", "percent": 6.0, "wage_base": 7000,
             "detail": "Gross rate on the first $7,000 per employee; credits can reduce it. Your eligibility and credit reduction must be checked."},
        ]
    notes = ["State unemployment, insurance, benefits and wage-cap effects depend on your payroll. Enter the employer cost from your own payroll report."]
    sources = [{"label": "IRS employer tax guide, 2026", "url": IRS_SOURCE}] if components else []
    if state == "NY":
        notes.append("New York sends each employer its unemployment rate. Workers' compensation depends on the policy and job classifications; there is no universal restaurant rate.")
        sources += [{"label": "New York unemployment rates", "url": NY_UI_SOURCE}, {"label": "New York workers' compensation", "url": NY_COMP_SOURCE}]
    return {"components": components, "notes": notes, "sources": sources,
            "effective_from": "2026-01-01" if components else None,
            "valid_through": "2026-12-31" if components else None,
            "checked_on": MINIMUM_WAGE_REVIEWED, "status": "reference_only" if components else "unverified"}
