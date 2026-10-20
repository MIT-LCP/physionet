"""Find dates and Unix timestamps that fall in a realistic, possibly real, range.

De-identified datasets usually shift dates, often far into the future. A date
between `lookback_years` ago and today may be a real date, so the privacy check
asks the submitter to confirm it was shifted. Format alone cannot tell shifted
dates from real ones, so findings are warnings to review, not errors.

Recognized forms:
- Year-first dates: 2023-01-13, 2023/01/13, 2023.01.13, with an optional time.
- Day- or month-first dates with a four-digit year: 13/01/2023, 01-13-2023.
- Month names: 13 Jan 2023, January 13, 2023, Jan 2023.
- Unix seconds (9-10 digits) or milliseconds (12-13 digits), but only in
  columns whose name suggests a time (or that a layout names), because IDs and
  counters have the same shape.

Two-digit years, eight-digit compact dates (20230113), times with no date, and
years on their own are not reported.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Iterator, Optional

from physionet.validate.config import validate_lookback_years


_MONTH_NAME = (
    r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
)
_MONTHS = {name: number for number, name in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1
)}

_YEAR_FIRST = re.compile(r"(?<!\d)(\d{4})([-/.])(\d{1,2})\2(\d{1,2})(?!\d)")
# A trailing period is allowed (sentence end), but not a decimal like 13.01.2023.5.
_YEAR_LAST = re.compile(r"(?<!\d)(?<!\d\.)(\d{1,2})([-/.])(\d{1,2})\2(\d{4})(?!\d|\.\d)")
_DAY_MONTH_YEAR = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+{_MONTH_NAME},?\s+(\d{{4}})\b", re.IGNORECASE
)
_MONTH_DAY_YEAR = re.compile(
    rf"\b{_MONTH_NAME}\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.IGNORECASE
)
_MONTH_YEAR = re.compile(rf"\b{_MONTH_NAME},?\s+(\d{{4}})\b", re.IGNORECASE)

# Cheap pre-check: a written date needs two separators between digits, or a word.
_MAYBE_WRITTEN_DATE = re.compile(r"\d[-/.]\d{1,2}[-/.]\d|[a-z]{3}", re.IGNORECASE)

_UNIX = re.compile(r"^([1-9]\d{8,9}|[1-9]\d{11,12})(?:\.\d+)?$")
_TIME_LIKE_COLUMN = re.compile(r"time|date|epoch|(?:^|[^a-z])ts(?:[^a-z]|$)", re.IGNORECASE)


def _make_date(year: int, month: int, day: int) -> Optional[date]:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _month(name: str) -> int:
    return _MONTHS[name.lower()[:3]]


def dates_in_text(text: str) -> Iterator[date]:
    """Yield every calendar date written in text."""
    if not _MAYBE_WRITTEN_DATE.search(text) or not any(char.isdigit() for char in text):
        return
    for match in _YEAR_FIRST.finditer(text):
        found = _make_date(int(match[1]), int(match[3]), int(match[4]))
        if found:
            yield found
    for match in _YEAR_LAST.finditer(text):
        first, second, year = int(match[1]), int(match[3]), int(match[4])
        # Ambiguous order: accept month-first or day-first, whichever is valid.
        found = _make_date(year, first, second) or _make_date(year, second, first)
        if found:
            yield found
    if not any(char.isalpha() for char in text):
        return
    full_date_spans = []
    for match in _DAY_MONTH_YEAR.finditer(text):
        found = _make_date(int(match[3]), _month(match[2]), int(match[1]))
        if found:
            full_date_spans.append(match.span())
            yield found
    for match in _MONTH_DAY_YEAR.finditer(text):
        found = _make_date(int(match[3]), _month(match[1]), int(match[2]))
        if found:
            full_date_spans.append(match.span())
            yield found
    for match in _MONTH_YEAR.finditer(text):
        # "5 May 2019" also contains "May 2019"; count only the full date.
        if any(start < match.end() and match.start() < end for start, end in full_date_spans):
            continue
        found = _make_date(int(match[2]), _month(match[1]), 1)
        if found:
            yield found


def unix_timestamp_date(value: str) -> Optional[date]:
    """Return the UTC date of Unix seconds or milliseconds, if value is one."""
    match = _UNIX.match(value.strip())
    if not match:
        return None
    digits = match[1]
    seconds = int(digits) / 1000 if len(digits) > 10 else int(digits)
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc).date()
    except (OverflowError, OSError, ValueError):
        return None


def is_time_like_column(name: Optional[str]) -> bool:
    """Guess from a column name whether it holds times (e.g. charttime, ts, date)."""
    return bool(name) and bool(_TIME_LIKE_COLUMN.search(name))


@dataclass
class DateRange:
    """Earliest and latest realistic dates seen in one place."""

    earliest: date
    latest: date

    def add(self, found: date) -> None:
        self.earliest = min(self.earliest, found)
        self.latest = max(self.latest, found)

    def describe(self) -> str:
        if self.earliest == self.latest:
            return self.earliest.isoformat()
        return f"{self.earliest.isoformat()} to {self.latest.isoformat()}"


class RealisticDateFinder:
    """Collect dates between lookback_years ago and today."""

    def __init__(self, lookback_years: int, today: Optional[date] = None):
        validate_lookback_years(lookback_years)
        today = today or datetime.now(timezone.utc).date()
        self.lookback_years = lookback_years
        self.start = date(today.year - lookback_years, 1, 1)
        self.end = today + timedelta(days=1)  # Allow for time-zone differences.

    def in_range(self, found: date) -> bool:
        return self.start <= found <= self.end

    def scan(self, value: str, unix_allowed: bool, ranges: dict, key: str) -> None:
        """Record realistic dates in value under key in ranges."""
        candidates = list(dates_in_text(value))
        if unix_allowed:
            unix_date = unix_timestamp_date(value)
            if unix_date:
                candidates.append(unix_date)
        for found in candidates:
            if not self.in_range(found):
                continue
            if key in ranges:
                ranges[key].add(found)
            else:
                ranges[key] = DateRange(found, found)
