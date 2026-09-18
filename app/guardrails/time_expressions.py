"""Best-effort deterministic time-window parser used only as a cross-check against the LLM's own
reading (§4.7.3). Generic patterns/keywords, not tuned to specific note wording. Returns None
(no opinion) far more often than a confident wrong answer — false "disagreements" cost an extra
repair round, but a false negative here just means we skip the cross-check, which is safe.
"""

import re
from dataclasses import dataclass

_HOUR_WORD = {"midnight": 0, "noon": 12, "midday": 12}


@dataclass
class ParsedWindow:
    start_hour: int
    end_hour: int


def _to_hour(h: int, meridiem: str | None, twelve_word: str | None) -> int | None:
    if twelve_word:
        return _HOUR_WORD.get(twelve_word.lower())
    if h is None:
        return None
    if meridiem:
        m = meridiem.lower().replace(".", "")
        if h == 12:
            h = 0
        if m == "pm":
            h += 12
        return h
    if 0 <= h <= 23:
        return h
    return None


_TIME_TOKEN = r"(?:midnight|noon|midday|\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)?|\d{3,4}\s*(?:hrs?|h))"


def _parse_single_time(token: str) -> int | None:
    token = token.strip().lower()
    if token in _HOUR_WORD:
        return _HOUR_WORD[token]
    m = re.match(r"^(\d{1,2}):?(\d{2})?\s*(am|pm|a\.m\.|p\.m\.)?$", token)
    if m:
        h = int(m.group(1))
        mer = m.group(3)
        if mer:
            mer = mer.replace(".", "")
            if h == 12:
                h = 0
            h += 12 if mer == "pm" else 0
            return h % 24
        if m.group(2) is not None:
            return h % 24
        return None
    m = re.match(r"^(\d{3,4})\s*(?:hrs?|h)$", token)
    if m:
        digits = m.group(1).zfill(4)
        return int(digits[:2]) % 24
    return None


def find_windows(text: str) -> list[ParsedWindow]:
    """Returns confidently-parsed [start,end) windows; empty list means "no opinion", not "no window"."""
    windows: list[ParsedWindow] = []
    lower = text.lower()

    if re.search(r"\ball day\b|\bentire day\b|\b24 hours\b|\bthroughout the day\b", lower):
        windows.append(ParsedWindow(0, 24))

    range_pattern = re.compile(
        rf"(?:from|between)?\s*({_TIME_TOKEN})\s*(?:to|until|through|-|–|and)\s*({_TIME_TOKEN})",
        re.IGNORECASE,
    )
    for m in range_pattern.finditer(text):
        a, b = _parse_single_time(m.group(1)), _parse_single_time(m.group(2))
        if a is None and m.group(1).lower() in _HOUR_WORD:
            a = _HOUR_WORD[m.group(1).lower()]
        if b is None and m.group(2).lower() in _HOUR_WORD:
            b = _HOUR_WORD[m.group(2).lower()]
        if a is not None and b is None and re.search(r"pm|am", m.group(1), re.IGNORECASE) is None:
            suffix = re.search(r"(am|pm)", m.group(2), re.IGNORECASE)
            if suffix and re.search(r"pm|am", m.group(1), re.IGNORECASE) is None:
                a = _apply_shared_suffix(m.group(1), suffix.group(1))
        if a is not None and b is not None:
            windows.append(ParsedWindow(a, b))

    for_hours = re.search(rf"from\s+({_TIME_TOKEN})\s+for\s+(\d+)\s+hours?", lower)
    if for_hours:
        a = _parse_single_time(for_hours.group(1))
        n = int(for_hours.group(2))
        if a is not None:
            windows.append(ParsedWindow(a, (a + n) % 24 or 24 if a + n <= 24 else (a + n) % 24))

    starting_at = re.search(rf"(\d+)\s+hours?\s+starting\s+at\s+({_TIME_TOKEN})", lower)
    if starting_at:
        n = int(starting_at.group(1))
        a = _parse_single_time(starting_at.group(2))
        if a is not None:
            windows.append(ParsedWindow(a, a + n if a + n <= 24 else (a + n) % 24))

    after = re.search(rf"(?:after|from)\s+({_TIME_TOKEN})\s+onwards?\b", lower) or re.search(
        rf"\bafter\s+({_TIME_TOKEN})\b", lower
    )
    if after:
        a = _parse_single_time(after.group(1))
        if a is not None:
            windows.append(ParsedWindow(a, 24))

    until = re.search(rf"\b(?:until|before)\s+({_TIME_TOKEN})\b", lower)
    if until and not range_pattern.search(text):
        b = _parse_single_time(until.group(1))
        if b is not None:
            windows.append(ParsedWindow(0, b))

    at_single = re.search(rf"\bat\s+({_TIME_TOKEN})\b", lower)
    if at_single and not windows:
        a = _parse_single_time(at_single.group(1))
        if a is not None:
            windows.append(ParsedWindow(a, (a + 1) % 24 or 24))

    return windows


def _apply_shared_suffix(token: str, meridiem: str) -> int | None:
    m = re.match(r"^(\d{1,2})", token.strip())
    if not m:
        return None
    h = int(m.group(1))
    mer = meridiem.lower()
    if h == 12:
        h = 0
    return (h + 12) % 24 if mer == "pm" else h
