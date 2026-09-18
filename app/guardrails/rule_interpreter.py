"""Last-resort degraded interpreter (§4.10 step 4): keyword + regex only, used ONLY when every
model in the Gemini fallback chain has failed and time remains. Deliberately conservative — it
returns None (=> the pipeline falls back to no_op) unless it is confident, since an under-restrictive
guess is far worse than admitting we couldn't interpret the note (§4.9).
"""

import re
from dataclasses import dataclass

from app.guardrails.cross_check import regex_hours
from app.guardrails.quantities import extract_numbers
from app.schemas.directives import DirectiveType

_KEYWORDS: dict[DirectiveType, list[str]] = {
    "solar_reduction": [
        "solar", "pv", "photovoltaic", "panel", "rooftop", "inverter", "generation",
    ],
    "minimum_battery_reserve": [
        "reserve", "keep at least", "maintain", "backup", "state of charge", "soc", "floor",
        "do not let it drop", "half full", "full",
    ],
    "no_charge_window": [
        "not charge", "no charging", "charging disabled", "charging prohibited", "charging paused",
        "charger isolated", "charger offline", "cannot accept", "cannot absorb",
    ],
    "no_discharge_window": [
        "not discharge", "no discharging", "discharge disabled", "cannot supply", "cannot feed",
        "output locked", "must not discharge",
    ],
    "max_grid_window": [
        "grid import", "feeder", "transformer", "substation", "intake", "draw", "grid draw",
        "not exceed", "at most", "no more than",
    ],
}

_IRRELEVANT_HINTS = [
    "menu", "deadline", "club", "booking", "library", "last night", "last week", "next week",
    "next month", "yesterday", "tomorrow's meeting", "ignore all", "ignore previous",
]


@dataclass
class RuleInterpretation:
    directive_type: DirectiveType
    hours: list[int]
    factor: float | None = None
    minimum_energy_kwh: float | None = None
    max_grid_kwh: float | None = None
    explanation: str = "Degraded rule-based interpretation (LLM unavailable)."


def interpret(note_text: str, capacity_kwh: float, base_min_kwh: float) -> RuleInterpretation | None:
    lower = note_text.lower()
    if any(hint in lower for hint in _IRRELEVANT_HINTS):
        return None

    scores = {dt: sum(1 for kw in kws if kw in lower) for dt, kws in _KEYWORDS.items()}
    best_type = max(scores, key=lambda k: scores[k])
    if scores[best_type] == 0:
        return None

    hours = regex_hours(note_text)
    if not hours:
        return None

    if best_type == "no_charge_window" or best_type == "no_discharge_window":
        return RuleInterpretation(directive_type=best_type, hours=hours)

    numbers = extract_numbers(note_text)
    if not numbers:
        return None

    if best_type == "solar_reduction":
        pct_match = re.search(r"(\d+(?:\.\d+)?)\s*%", note_text)
        if not pct_match:
            return None
        v = float(pct_match.group(1))
        reduction = bool(re.search(r"\breduc|\bcut|\blose|\bby\b", lower))
        factor = round((1 - v / 100) if reduction else (v / 100), 6)
        if not (0.0 <= factor <= 1.0):
            return None
        return RuleInterpretation(directive_type=best_type, hours=hours, factor=factor)

    if best_type == "minimum_battery_reserve":
        kwh_match = re.search(r"(\d+(?:\.\d+)?)\s*kwh", lower)
        if not kwh_match:
            return None
        return RuleInterpretation(
            directive_type=best_type, hours=hours, minimum_energy_kwh=float(kwh_match.group(1))
        )

    if best_type == "max_grid_window":
        kwh_match = re.search(r"(\d+(?:\.\d+)?)\s*k?wh?", lower)
        if not kwh_match:
            return None
        return RuleInterpretation(
            directive_type=best_type, hours=hours, max_grid_kwh=float(kwh_match.group(1))
        )

    return None
