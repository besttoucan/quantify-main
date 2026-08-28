"""Turn whatever an owner types into a real time zone.

Owners do not think in IANA identifiers. They type "New York", "NYC", "Austin
TX", "California", "PST", or a ZIP code. This module accepts all of those and
returns one zone, plus a plain-English label to show back so the owner can
confirm the app understood them.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

EASTERN = "America/New_York"
CENTRAL = "America/Chicago"
MOUNTAIN = "America/Denver"
ARIZONA = "America/Phoenix"
PACIFIC = "America/Los_Angeles"
ALASKA = "America/Anchorage"
HAWAII = "Pacific/Honolulu"

ZONE_LABELS: dict[str, str] = {
    EASTERN: "Eastern time",
    CENTRAL: "Central time",
    MOUNTAIN: "Mountain time",
    ARIZONA: "Arizona time (no daylight saving)",
    PACIFIC: "Pacific time",
    ALASKA: "Alaska time",
    HAWAII: "Hawaii time",
    "America/Detroit": "Eastern time",
    "America/Indiana/Indianapolis": "Eastern time",
    "America/Boise": "Mountain time",
    "America/Juneau": "Alaska time",
    "America/Puerto_Rico": "Atlantic time (no daylight saving)",
    "Pacific/Guam": "Chamorro time",
    "America/Toronto": "Eastern time",
    "America/Vancouver": "Pacific time",
    "America/Edmonton": "Mountain time",
    "America/Winnipeg": "Central time",
    "America/Halifax": "Atlantic time",
    "America/St_Johns": "Newfoundland time",
    "America/Mexico_City": "Central time",
    "Europe/London": "United Kingdom time",
    "Europe/Dublin": "Ireland time",
    "Europe/Paris": "Central European time",
    "Australia/Sydney": "Eastern Australia time",
    "UTC": "Coordinated Universal Time",
}

# The zone every state uses, or the zone the large majority of the state uses.
# Split states are corrected by the city list below, which is checked first.
STATES: dict[str, tuple[str, str]] = {
    "alabama": ("AL", CENTRAL),
    "alaska": ("AK", ALASKA),
    "arizona": ("AZ", ARIZONA),
    "arkansas": ("AR", CENTRAL),
    "california": ("CA", PACIFIC),
    "colorado": ("CO", MOUNTAIN),
    "connecticut": ("CT", EASTERN),
    "delaware": ("DE", EASTERN),
    "district of columbia": ("DC", EASTERN),
    "washington dc": ("DC", EASTERN),
    "florida": ("FL", EASTERN),
    "georgia": ("GA", EASTERN),
    "hawaii": ("HI", HAWAII),
    "idaho": ("ID", MOUNTAIN),
    "illinois": ("IL", CENTRAL),
    "indiana": ("IN", EASTERN),
    "iowa": ("IA", CENTRAL),
    "kansas": ("KS", CENTRAL),
    "kentucky": ("KY", EASTERN),
    "louisiana": ("LA", CENTRAL),
    "maine": ("ME", EASTERN),
    "maryland": ("MD", EASTERN),
    "massachusetts": ("MA", EASTERN),
    "michigan": ("MI", EASTERN),
    "minnesota": ("MN", CENTRAL),
    "mississippi": ("MS", CENTRAL),
    "missouri": ("MO", CENTRAL),
    "montana": ("MT", MOUNTAIN),
    "nebraska": ("NE", CENTRAL),
    "nevada": ("NV", PACIFIC),
    "new hampshire": ("NH", EASTERN),
    "new jersey": ("NJ", EASTERN),
    "new mexico": ("NM", MOUNTAIN),
    "new york": ("NY", EASTERN),
    "north carolina": ("NC", EASTERN),
    "north dakota": ("ND", CENTRAL),
    "ohio": ("OH", EASTERN),
    "oklahoma": ("OK", CENTRAL),
    "oregon": ("OR", PACIFIC),
    "pennsylvania": ("PA", EASTERN),
    "rhode island": ("RI", EASTERN),
    "south carolina": ("SC", EASTERN),
    "south dakota": ("SD", CENTRAL),
    "tennessee": ("TN", CENTRAL),
    "texas": ("TX", CENTRAL),
    "utah": ("UT", MOUNTAIN),
    "vermont": ("VT", EASTERN),
    "virginia": ("VA", EASTERN),
    "washington": ("WA", PACIFIC),
    "west virginia": ("WV", EASTERN),
    "wisconsin": ("WI", CENTRAL),
    "wyoming": ("WY", MOUNTAIN),
    "puerto rico": ("PR", "America/Puerto_Rico"),
    "guam": ("GU", "Pacific/Guam"),
    "us virgin islands": ("VI", "America/Puerto_Rico"),
    "virgin islands": ("VI", "America/Puerto_Rico"),
}

STATE_ABBREVIATIONS: dict[str, str] = {abbr.lower(): name for name, (abbr, _zone) in STATES.items()}

# Cities are checked before states so that split states resolve correctly.
# Format: city -> (state abbreviation, zone).
CITIES: dict[str, tuple[str, str]] = {}


def _add_cities(zone: str, entries: dict[str, str]) -> None:
    for city, state in entries.items():
        CITIES[city] = (state, zone)


_add_cities(EASTERN, {
    "new york": "NY", "new york city": "NY", "nyc": "NY", "manhattan": "NY", "brooklyn": "NY",
    "queens": "NY", "bronx": "NY", "staten island": "NY", "buffalo": "NY", "rochester": "NY",
    "yonkers": "NY", "syracuse": "NY", "albany": "NY", "white plains": "NY", "scarsdale": "NY",
    "new rochelle": "NY", "mount vernon": "NY", "long island": "NY", "hempstead": "NY",
    "brookhaven": "NY", "ithaca": "NY", "poughkeepsie": "NY", "schenectady": "NY", "utica": "NY",
    "philadelphia": "PA", "pittsburgh": "PA", "allentown": "PA", "erie": "PA", "reading": "PA",
    "scranton": "PA", "bethlehem": "PA", "lancaster": "PA", "harrisburg": "PA", "state college": "PA",
    "boston": "MA", "worcester": "MA", "springfield": "MA", "cambridge": "MA", "lowell": "MA",
    "somerville": "MA", "brockton": "MA", "quincy": "MA", "newton": "MA", "provincetown": "MA",
    "providence": "RI", "warwick": "RI", "cranston": "RI", "newport": "RI",
    "hartford": "CT", "new haven": "CT", "stamford": "CT", "bridgeport": "CT", "waterbury": "CT",
    "norwalk": "CT", "greenwich": "CT", "danbury": "CT",
    "newark": "NJ", "jersey city": "NJ", "paterson": "NJ", "elizabeth": "NJ", "trenton": "NJ",
    "edison": "NJ", "hoboken": "NJ", "atlantic city": "NJ", "princeton": "NJ", "camden": "NJ",
    "washington": "DC", "washington dc": "DC", "dc": "DC",
    "baltimore": "MD", "annapolis": "MD", "rockville": "MD", "silver spring": "MD", "bethesda": "MD",
    "frederick": "MD", "columbia": "MD", "gaithersburg": "MD",
    "virginia beach": "VA", "norfolk": "VA", "richmond": "VA", "arlington": "VA", "alexandria": "VA",
    "chesapeake": "VA", "newport news": "VA", "charlottesville": "VA", "roanoke": "VA", "reston": "VA",
    "charlotte": "NC", "raleigh": "NC", "greensboro": "NC", "durham": "NC", "winston salem": "NC",
    "fayetteville": "NC", "cary": "NC", "wilmington": "NC", "asheville": "NC", "chapel hill": "NC",
    "columbia": "SC", "charleston": "SC", "greenville": "SC", "myrtle beach": "SC", "rock hill": "SC",
    "atlanta": "GA", "augusta": "GA", "savannah": "GA", "athens": "GA", "macon": "GA",
    "sandy springs": "GA", "roswell": "GA", "alpharetta": "GA", "marietta": "GA",
    "jacksonville": "FL", "miami": "FL", "tampa": "FL", "orlando": "FL", "st petersburg": "FL",
    "saint petersburg": "FL", "hialeah": "FL", "fort lauderdale": "FL", "tallahassee": "FL",
    "cape coral": "FL", "port st lucie": "FL", "sarasota": "FL", "naples": "FL", "boca raton": "FL",
    "west palm beach": "FL", "key west": "FL", "gainesville": "FL", "clearwater": "FL", "miami beach": "FL",
    "columbus": "OH", "cleveland": "OH", "cincinnati": "OH", "toledo": "OH", "akron": "OH",
    "dayton": "OH", "parma": "OH", "canton": "OH", "youngstown": "OH",
    "detroit": "MI", "grand rapids": "MI", "warren": "MI", "sterling heights": "MI", "ann arbor": "MI",
    "lansing": "MI", "flint": "MI", "dearborn": "MI", "traverse city": "MI",
    "indianapolis": "IN", "fort wayne": "IN", "carmel": "IN", "bloomington": "IN", "south bend": "IN",
    "fishers": "IN", "muncie": "IN",
    "louisville": "KY", "lexington": "KY", "frankfort": "KY", "covington": "KY",
    "charleston": "WV", "huntington": "WV", "morgantown": "WV",
    "wilmington": "DE", "dover": "DE", "newark de": "DE",
    "portland me": "ME", "bangor": "ME", "augusta me": "ME", "portland maine": "ME",
    "manchester": "NH", "nashua": "NH", "concord": "NH", "portsmouth": "NH",
    "burlington": "VT", "montpelier": "VT",
    "knoxville": "TN", "chattanooga": "TN", "kingsport": "TN", "johnson city": "TN",
})

_add_cities(CENTRAL, {
    "chicago": "IL", "aurora": "IL", "naperville": "IL", "joliet": "IL", "rockford": "IL",
    "springfield il": "IL", "peoria": "IL", "elgin": "IL", "evanston": "IL", "champaign": "IL",
    "houston": "TX", "san antonio": "TX", "dallas": "TX", "austin": "TX", "fort worth": "TX",
    "arlington tx": "TX", "corpus christi": "TX", "plano": "TX", "laredo": "TX", "lubbock": "TX",
    "garland": "TX", "irving": "TX", "amarillo": "TX", "frisco": "TX", "mckinney": "TX",
    "waco": "TX", "killeen": "TX", "college station": "TX", "round rock": "TX", "galveston": "TX",
    "san marcos": "TX", "denton": "TX", "sugar land": "TX", "the woodlands": "TX",
    "san jose tx": "TX",
    "milwaukee": "WI", "madison": "WI", "green bay": "WI", "kenosha": "WI", "racine": "WI",
    "appleton": "WI", "eau claire": "WI",
    "minneapolis": "MN", "saint paul": "MN", "st paul": "MN", "rochester mn": "MN", "duluth": "MN",
    "bloomington mn": "MN", "brooklyn park": "MN",
    "kansas city": "MO", "st louis": "MO", "saint louis": "MO", "springfield mo": "MO",
    "columbia mo": "MO", "independence": "MO", "jefferson city": "MO", "branson": "MO",
    "wichita": "KS", "overland park": "KS", "olathe": "KS", "topeka": "KS", "lawrence": "KS",
    "omaha": "NE", "lincoln": "NE", "bellevue ne": "NE",
    "des moines": "IA", "cedar rapids": "IA", "davenport": "IA", "iowa city": "IA", "ames": "IA",
    "oklahoma city": "OK", "tulsa": "OK", "norman": "OK", "broken arrow": "OK", "stillwater": "OK",
    "little rock": "AR", "fayetteville ar": "AR", "fort smith": "AR", "bentonville": "AR",
    "new orleans": "LA", "baton rouge": "LA", "shreveport": "LA", "lafayette": "LA", "metairie": "LA",
    "jackson": "MS", "gulfport": "MS", "biloxi": "MS", "hattiesburg": "MS", "oxford ms": "MS",
    "birmingham": "AL", "montgomery": "AL", "mobile": "AL", "huntsville": "AL", "tuscaloosa": "AL",
    "auburn": "AL",
    "nashville": "TN", "memphis": "TN", "clarksville": "TN", "murfreesboro": "TN", "franklin": "TN",
    "fargo": "ND", "bismarck": "ND", "grand forks": "ND",
    "sioux falls": "SD", "rapid city": "SD",
    "mexico city": "MX",
})

_add_cities(MOUNTAIN, {
    "denver": "CO", "colorado springs": "CO", "aurora co": "CO", "fort collins": "CO",
    "lakewood": "CO", "boulder": "CO", "pueblo": "CO", "thornton": "CO", "aspen": "CO",
    "vail": "CO", "durango": "CO",
    "salt lake city": "UT", "west valley city": "UT", "provo": "UT", "orem": "UT", "ogden": "UT",
    "park city": "UT", "st george": "UT", "moab": "UT",
    "albuquerque": "NM", "las cruces": "NM", "santa fe": "NM", "rio rancho": "NM", "roswell nm": "NM",
    "billings": "MT", "missoula": "MT", "bozeman": "MT", "great falls": "MT", "helena": "MT",
    "whitefish": "MT",
    "boise": "ID", "meridian": "ID", "nampa": "ID", "idaho falls": "ID", "coeur d alene": "ID",
    "sun valley": "ID",
    "cheyenne": "WY", "casper": "WY", "jackson hole": "WY", "laramie": "WY",
    "el paso": "TX",
})

_add_cities(ARIZONA, {
    "phoenix": "AZ", "tucson": "AZ", "mesa": "AZ", "chandler": "AZ", "scottsdale": "AZ",
    "glendale az": "AZ", "gilbert": "AZ", "tempe": "AZ", "peoria az": "AZ", "surprise": "AZ",
    "flagstaff": "AZ", "sedona": "AZ", "yuma": "AZ",
})

_add_cities(PACIFIC, {
    "los angeles": "CA", "la": "CA", "san diego": "CA", "san jose": "CA", "san francisco": "CA",
    "sf": "CA", "fresno": "CA", "sacramento": "CA", "long beach": "CA", "oakland": "CA",
    "bakersfield": "CA", "anaheim": "CA", "santa ana": "CA", "riverside": "CA", "stockton": "CA",
    "irvine": "CA", "chula vista": "CA", "fremont": "CA", "san bernardino": "CA", "modesto": "CA",
    "oxnard": "CA", "fontana": "CA", "moreno valley": "CA", "huntington beach": "CA", "glendale": "CA",
    "santa clarita": "CA", "garden grove": "CA", "oceanside": "CA", "rancho cucamonga": "CA",
    "santa rosa": "CA", "ontario": "CA", "elk grove": "CA", "corona": "CA", "palmdale": "CA",
    "salinas": "CA", "pomona": "CA", "torrance": "CA", "pasadena": "CA", "hayward": "CA",
    "escondido": "CA", "sunnyvale": "CA", "orange": "CA", "fullerton": "CA", "thousand oaks": "CA",
    "visalia": "CA", "roseville": "CA", "concord": "CA", "santa clara": "CA", "berkeley": "CA",
    "santa monica": "CA", "burbank": "CA", "beverly hills": "CA", "santa barbara": "CA",
    "palo alto": "CA", "mountain view": "CA", "cupertino": "CA", "napa": "CA", "monterey": "CA",
    "carmel": "CA", "palm springs": "CA", "malibu": "CA", "venice": "CA", "west hollywood": "CA",
    "culver city": "CA", "redondo beach": "CA", "manhattan beach": "CA", "san mateo": "CA",
    "redwood city": "CA", "walnut creek": "CA", "davis": "CA", "chico": "CA", "san luis obispo": "CA",
    "seattle": "WA", "spokane": "WA", "tacoma": "WA", "vancouver wa": "WA", "bellevue": "WA",
    "kent": "WA", "everett": "WA", "renton": "WA", "olympia": "WA", "bellingham": "WA",
    "kirkland": "WA", "redmond": "WA", "yakima": "WA", "walla walla": "WA",
    "portland": "OR", "eugene": "OR", "salem": "OR", "gresham": "OR", "hillsboro": "OR",
    "bend": "OR", "beaverton": "OR", "medford": "OR", "corvallis": "OR", "astoria": "OR",
    "las vegas": "NV", "henderson": "NV", "reno": "NV", "north las vegas": "NV", "sparks": "NV",
    "carson city": "NV", "lake tahoe": "NV",
})

_add_cities(ALASKA, {
    "anchorage": "AK", "fairbanks": "AK", "juneau": "AK", "sitka": "AK", "ketchikan": "AK",
})

_add_cities(HAWAII, {
    "honolulu": "HI", "hilo": "HI", "kailua": "HI", "maui": "HI", "lahaina": "HI", "kona": "HI",
})

_add_cities("America/Puerto_Rico", {"san juan": "PR", "ponce": "PR", "bayamon": "PR"})

_add_cities("America/Toronto", {"toronto": "ON", "ottawa": "ON", "mississauga": "ON", "hamilton": "ON", "montreal": "QC", "quebec city": "QC"})
_add_cities("America/Vancouver", {"vancouver": "BC", "victoria": "BC", "surrey": "BC", "burnaby": "BC"})
_add_cities("America/Edmonton", {"calgary": "AB", "edmonton": "AB"})
_add_cities("America/Winnipeg", {"winnipeg": "MB", "regina": "SK", "saskatoon": "SK"})
_add_cities("America/Halifax", {"halifax": "NS", "moncton": "NB", "charlottetown": "PE"})
_add_cities("Europe/London", {"london": "UK", "manchester uk": "UK", "birmingham uk": "UK", "edinburgh": "UK", "glasgow": "UK", "bristol": "UK", "leeds": "UK", "liverpool": "UK"})
_add_cities("Europe/Dublin", {"dublin": "IE", "cork": "IE", "galway": "IE"})
_add_cities("Europe/Paris", {"paris": "FR", "lyon": "FR", "marseille": "FR", "berlin": "DE", "munich": "DE", "madrid": "ES", "barcelona": "ES", "rome": "IT", "milan": "IT", "amsterdam": "NL"})
_add_cities("Australia/Sydney", {"sydney": "AU", "melbourne": "AU", "brisbane": "AU", "canberra": "AU"})

ABBREVIATIONS: dict[str, str] = {
    "et": EASTERN, "est": EASTERN, "edt": EASTERN, "eastern": EASTERN, "eastern time": EASTERN,
    "ct": CENTRAL, "cst": CENTRAL, "cdt": CENTRAL, "central": CENTRAL, "central time": CENTRAL,
    "mt": MOUNTAIN, "mst": MOUNTAIN, "mdt": MOUNTAIN, "mountain": MOUNTAIN, "mountain time": MOUNTAIN,
    "pt": PACIFIC, "pst": PACIFIC, "pdt": PACIFIC, "pacific": PACIFIC, "pacific time": PACIFIC,
    "akst": ALASKA, "akdt": ALASKA, "alaska": ALASKA, "alaska time": ALASKA,
    "hst": HAWAII, "hawaii time": HAWAII,
    "ast": "America/Puerto_Rico", "atlantic": "America/Puerto_Rico",
    "gmt": "Europe/London", "bst": "Europe/London", "utc": "UTC", "z": "UTC",
    "cet": "Europe/Paris", "cest": "Europe/Paris",
}

# ZIP prefixes give a usable zone when nothing else matches. Keyed by the first
# three digits, which is enough resolution for time zone purposes.
_ZIP_RANGES: list[tuple[int, int, str]] = [
    (0, 219, EASTERN), (220, 299, EASTERN), (300, 349, EASTERN), (350, 369, CENTRAL),
    (370, 385, CENTRAL), (386, 397, CENTRAL), (398, 399, EASTERN), (400, 427, EASTERN),
    (430, 459, EASTERN), (460, 479, EASTERN), (480, 499, EASTERN), (500, 528, CENTRAL),
    (530, 549, CENTRAL), (550, 567, CENTRAL), (570, 577, CENTRAL), (580, 588, CENTRAL),
    (590, 599, MOUNTAIN), (600, 629, CENTRAL), (630, 658, CENTRAL), (660, 679, CENTRAL),
    (680, 693, CENTRAL), (700, 714, CENTRAL), (716, 729, CENTRAL), (730, 749, CENTRAL),
    (750, 799, CENTRAL), (800, 816, MOUNTAIN), (820, 831, MOUNTAIN), (832, 838, MOUNTAIN),
    (840, 847, MOUNTAIN), (850, 865, ARIZONA), (870, 884, MOUNTAIN), (889, 898, PACIFIC),
    (900, 961, PACIFIC), (967, 968, HAWAII), (970, 986, PACIFIC), (988, 994, PACIFIC),
    (995, 999, ALASKA),
]

ALL_ZONES: list[str] = sorted(ZONE_LABELS)


def _normalize(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    plain = plain.lower().replace(".", " ").replace(",", " ").replace("/", " ")
    plain = re.sub(r"[^a-z0-9 ]+", " ", plain)
    return re.sub(r"\s+", " ", plain).strip()


def _zip_zone(digits: str) -> str | None:
    try:
        prefix = int(digits[:3])
    except ValueError:
        return None
    for low, high, zone in _ZIP_RANGES:
        if low <= prefix <= high:
            return zone
    return None


def describe(zone: str) -> str:
    if zone in ZONE_LABELS:
        return ZONE_LABELS[zone]
    tail = zone.split("/")[-1].replace("_", " ")
    return f"{tail} time"


def resolve(text: str, fallback: str = EASTERN) -> dict[str, Any]:
    """Resolve free text to one time zone.

    Returns the zone, a readable label, what the match was based on, and how
    sure the match is, so the interface can show the owner what it understood.
    """
    raw = (text or "").strip()
    if not raw:
        return {"timezone": fallback, "label": describe(fallback), "matched": "default", "confident": False, "input": raw}

    # An exact IANA identifier is always honoured as typed.
    if "/" in raw and " " not in raw:
        candidate = raw.replace(" ", "_")
        return {"timezone": candidate, "label": describe(candidate), "matched": "time zone name", "confident": True, "input": raw}

    query = _normalize(raw)
    if not query:
        return {"timezone": fallback, "label": describe(fallback), "matched": "default", "confident": False, "input": raw}

    if query in ABBREVIATIONS:
        zone = ABBREVIATIONS[query]
        return {"timezone": zone, "label": describe(zone), "matched": "time zone", "confident": True, "input": raw}

    if query in CITIES:
        state, zone = CITIES[query]
        return {"timezone": zone, "label": describe(zone), "matched": f"{query.title()}, {state}", "confident": True, "input": raw}

    if query in STATES:
        abbr, zone = STATES[query]
        return {"timezone": zone, "label": describe(zone), "matched": f"the state of {raw.strip()}", "confident": True, "input": raw}

    if query in STATE_ABBREVIATIONS:
        name = STATE_ABBREVIATIONS[query]
        abbr, zone = STATES[name]
        return {"timezone": zone, "label": describe(zone), "matched": f"the state of {name.title()}", "confident": True, "input": raw}

    digits = re.sub(r"\D", "", query)
    if len(digits) >= 5:
        zone = _zip_zone(digits)
        if zone:
            return {"timezone": zone, "label": describe(zone), "matched": f"ZIP code {digits[:5]}", "confident": True, "input": raw}

    # "Austin, TX" or "Brooklyn New York": try the pieces from longest to shortest.
    words = query.split()
    for size in range(len(words), 0, -1):
        for start in range(0, len(words) - size + 1):
            piece = " ".join(words[start:start + size])
            if piece in CITIES:
                state, zone = CITIES[piece]
                return {"timezone": zone, "label": describe(zone), "matched": f"{piece.title()}, {state}", "confident": True, "input": raw}
            if piece in STATES:
                abbr, zone = STATES[piece]
                return {"timezone": zone, "label": describe(zone), "matched": f"the state of {piece.title()}", "confident": True, "input": raw}
            if piece in STATE_ABBREVIATIONS:
                name = STATE_ABBREVIATIONS[piece]
                abbr, zone = STATES[name]
                return {"timezone": zone, "label": describe(zone), "matched": f"the state of {name.title()}", "confident": True, "input": raw}
            if piece in ABBREVIATIONS:
                zone = ABBREVIATIONS[piece]
                return {"timezone": zone, "label": describe(zone), "matched": "time zone", "confident": True, "input": raw}

    return {
        "timezone": fallback,
        "label": describe(fallback),
        "matched": "no match",
        "confident": False,
        "input": raw,
    }


def suggest(text: str, limit: int = 6) -> list[dict[str, str]]:
    """Suggestions for the place field as the owner types."""
    query = _normalize(text)
    if not query:
        return []
    hits: list[tuple[int, dict[str, str]]] = []
    for city, (state, zone) in CITIES.items():
        if city.startswith(query):
            rank = 0
        elif query in city:
            rank = 1
        else:
            continue
        hits.append((rank, {"place": f"{city.title()}, {state}", "timezone": zone, "label": describe(zone)}))
    for name, (abbr, zone) in STATES.items():
        if name.startswith(query):
            hits.append((2, {"place": name.title(), "timezone": zone, "label": describe(zone)}))
    hits.sort(key=lambda row: (row[0], row[1]["place"]))
    seen: set[str] = set()
    output: list[dict[str, str]] = []
    for _rank, row in hits:
        if row["place"] in seen:
            continue
        seen.add(row["place"])
        output.append(row)
        if len(output) >= limit:
            break
    return output
