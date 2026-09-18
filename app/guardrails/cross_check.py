"""Regex-vs-LLM time window cross-check (§4.7.3). Soft signal only: never blocks, only widens the
hedge candidate set when the deterministic parser confidently disagrees with the LLM's reading.
"""

from app.guardrails.normalizer import window_to_hours
from app.guardrails.time_expressions import find_windows


def regex_hours(note_text: str) -> list[int] | None:
    windows = find_windows(note_text)
    if not windows:
        return None
    hours: set[int] = set()
    for w in windows:
        try:
            hours.update(window_to_hours(w.start_hour, w.end_hour))
        except Exception:
            continue
    return sorted(hours) if hours else None


def disagrees(note_text: str, llm_hours: list[int]) -> list[int] | None:
    """Returns the regex parser's hour list if it found a confident, differing answer; else None."""
    parsed = regex_hours(note_text)
    if parsed is None:
        return None
    if set(parsed) == set(llm_hours):
        return None
    return parsed
