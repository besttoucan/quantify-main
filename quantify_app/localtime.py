"""Local time that works on a machine with no time zone database.

Python's `zoneinfo` reads the IANA database from the operating system. Windows
does not ship one, and on those machines every named zone raises
`ZoneInfoNotFoundError`. Quantify has to send a morning email at a local hour
and has to place register timestamps in the right hour of the right day, so a
missing database is not something to shrug at.

`zone()` returns the real `ZoneInfo` when the database is present, and a small
built-in zone with the correct daylight saving rule when it is not. Installing
the `tzdata` package upgrades every location to the full database automatically,
with no code change.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo

try:  # pragma: no cover - depends on the host
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]

    class ZoneInfoNotFoundError(Exception):
        pass


ZERO = timedelta(0)
HOUR = timedelta(hours=1)


def _first_sunday_on_or_after(moment: datetime) -> datetime:
    return moment + timedelta(days=(6 - moment.weekday()) % 7)


class _Rule:
    """When daylight saving starts and ends, evaluated on naive local time."""

    def active(self, moment: datetime) -> bool:  # pragma: no cover - overridden
        raise NotImplementedError


class _NoRule(_Rule):
    def active(self, moment: datetime) -> bool:
        return False


class _UnitedStates(_Rule):
    """Second Sunday in March to the first Sunday in November, at 2am."""

    def active(self, moment: datetime) -> bool:
        year = moment.year
        start = _first_sunday_on_or_after(datetime(year, 3, 8, 2))
        end = _first_sunday_on_or_after(datetime(year, 11, 1, 2))
        return start <= moment.replace(tzinfo=None) < end


class _Europe(_Rule):
    """Last Sunday in March to the last Sunday in October."""

    def active(self, moment: datetime) -> bool:
        year = moment.year
        start = _first_sunday_on_or_after(datetime(year, 3, 25, 1))
        end = _first_sunday_on_or_after(datetime(year, 10, 25, 1))
        return start <= moment.replace(tzinfo=None) < end


class _Australia(_Rule):
    """Southern hemisphere: first Sunday in October to the first Sunday in April."""

    def active(self, moment: datetime) -> bool:
        year = moment.year
        start = _first_sunday_on_or_after(datetime(year, 10, 1, 2))
        end = _first_sunday_on_or_after(datetime(year, 4, 1, 3))
        naive = moment.replace(tzinfo=None)
        return naive >= start or naive < end


class FallbackZone(tzinfo):
    def __init__(self, key: str, standard_hours: float, rule: _Rule, standard: str, daylight: str) -> None:
        self._key = key
        self._standard = timedelta(hours=standard_hours)
        self._rule = rule
        self._standard_name = standard
        self._daylight_name = daylight

    @property
    def key(self) -> str:
        return self._key

    def utcoffset(self, dt: datetime | None) -> timedelta:
        return self._standard + self.dst(dt)

    def dst(self, dt: datetime | None) -> timedelta:
        if dt is None:
            return ZERO
        return HOUR if self._rule.active(dt) else ZERO

    def tzname(self, dt: datetime | None) -> str:
        return self._daylight_name if self.dst(dt) else self._standard_name

    def __repr__(self) -> str:
        return f"FallbackZone({self._key!r})"

    def __str__(self) -> str:
        return self._key


US = _UnitedStates()
EU = _Europe()
AU = _Australia()
NONE = _NoRule()

# Every zone Quantify's own place lookup can produce, plus the common ones an
# owner might type in by hand.
FALLBACKS: dict[str, tuple[float, _Rule, str, str]] = {
    "UTC": (0, NONE, "UTC", "UTC"),
    "America/New_York": (-5, US, "EST", "EDT"),
    "America/Detroit": (-5, US, "EST", "EDT"),
    "America/Indiana/Indianapolis": (-5, US, "EST", "EDT"),
    "America/Toronto": (-5, US, "EST", "EDT"),
    "America/Chicago": (-6, US, "CST", "CDT"),
    "America/Winnipeg": (-6, US, "CST", "CDT"),
    "America/Mexico_City": (-6, NONE, "CST", "CST"),
    "America/Denver": (-7, US, "MST", "MDT"),
    "America/Boise": (-7, US, "MST", "MDT"),
    "America/Edmonton": (-7, US, "MST", "MDT"),
    "America/Phoenix": (-7, NONE, "MST", "MST"),
    "America/Los_Angeles": (-8, US, "PST", "PDT"),
    "America/Vancouver": (-8, US, "PST", "PDT"),
    "America/Anchorage": (-9, US, "AKST", "AKDT"),
    "America/Juneau": (-9, US, "AKST", "AKDT"),
    "Pacific/Honolulu": (-10, NONE, "HST", "HST"),
    "America/Puerto_Rico": (-4, NONE, "AST", "AST"),
    "America/Halifax": (-4, US, "AST", "ADT"),
    "America/St_Johns": (-3.5, US, "NST", "NDT"),
    "Pacific/Guam": (10, NONE, "ChST", "ChST"),
    "Europe/London": (0, EU, "GMT", "BST"),
    "Europe/Dublin": (0, EU, "GMT", "IST"),
    "Europe/Paris": (1, EU, "CET", "CEST"),
    "Europe/Berlin": (1, EU, "CET", "CEST"),
    "Europe/Madrid": (1, EU, "CET", "CEST"),
    "Europe/Rome": (1, EU, "CET", "CEST"),
    "Europe/Amsterdam": (1, EU, "CET", "CEST"),
    "Australia/Sydney": (10, AU, "AEST", "AEDT"),
    "Australia/Melbourne": (10, AU, "AEST", "AEDT"),
    "Australia/Brisbane": (10, NONE, "AEST", "AEST"),
}

_CACHE: dict[str, tzinfo] = {}
_NATIVE_AVAILABLE: bool | None = None


def database_available() -> bool:
    """Whether the operating system has the full IANA database."""
    global _NATIVE_AVAILABLE
    if _NATIVE_AVAILABLE is None:
        if ZoneInfo is None:
            _NATIVE_AVAILABLE = False
        else:
            try:
                ZoneInfo("America/New_York")
                _NATIVE_AVAILABLE = True
            except Exception:  # noqa: BLE001 - any failure means it is unusable
                _NATIVE_AVAILABLE = False
    return _NATIVE_AVAILABLE


def zone(name: str | None) -> tzinfo:
    """Return a usable time zone for `name`, never raising."""
    key = (name or "UTC").strip() or "UTC"
    if key in _CACHE:
        return _CACHE[key]

    resolved: tzinfo | None = None
    if ZoneInfo is not None:
        try:
            resolved = ZoneInfo(key)
        except Exception:  # noqa: BLE001 - missing database or unknown key
            resolved = None

    if resolved is None:
        if key.upper() == "UTC":
            resolved = timezone.utc
        elif key in FALLBACKS:
            offset, rule, standard, daylight = FALLBACKS[key]
            resolved = FallbackZone(key, offset, rule, standard, daylight)
        else:
            # An unknown name is better served by a stable offset than by an
            # exception in a background thread nobody is watching.
            resolved = FallbackZone(key, -5, US, "EST", "EDT")

    _CACHE[key] = resolved
    return resolved


def is_known(name: str | None) -> bool:
    """True when `name` resolves to a real zone rather than the last-resort default."""
    key = (name or "").strip()
    if not key:
        return False
    if ZoneInfo is not None:
        try:
            ZoneInfo(key)
            return True
        except Exception:  # noqa: BLE001
            pass
    return key in FALLBACKS or key.upper() == "UTC"


def now(name: str | None) -> datetime:
    return datetime.now(timezone.utc).astimezone(zone(name))
