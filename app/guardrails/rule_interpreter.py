"""Last-resort degraded interpreter (§4.10 step 4): keyword + regex only, used ONLY when every
model in the LLM fallback chain has failed (outage / quota exhausted) within the request deadline.

It is generic (topic keywords, negation words, time/number patterns), never tuned to specific
note sentences. It returns None (=> safe no_op) unless it finds BOTH a clear directive topic and a
parseable time window (and a value where the directive needs one).
"""

import re
from dataclasses import dataclass

from app.guardrails.cross_check import regex_hours
from app.schemas.directives import DirectiveType

_OTHER_PERIOD = re.compile(
    r"\b(next|last)\s+(week|month|year|night|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b"
    r"|\byesterday\b|\blast\s+time\b|\bwas\b.*\b(offline|isolated|disabled)\b"
)
_INJECTION = re.compile(r"ignore (all |any )?(previous|prior|above) (instructions|rules)", re.IGNORECASE)

_SOLAR = re.compile(r"\b(solar|pv|photovoltaic|panels?|rooftop array|inverter)\b")
_CHARGE = re.compile(r"\b(?<!dis)(charg(e|es|ed|ing|er|ers)|recharg\w*)\b")
_DISCHARGE = re.compile(r"\bdischarg\w*|\b(supply|feed|power)\s+the\s+campus\b|\bbattery output\b")
_BLOCK = re.compile(
    r"\b(not|no|never|cannot|can't|must not|mustn't|disabled?|isolated|unavailable|offline|out of service|"
    r"prohibited|forbidden|suspended|paused|blocked|halted|stopped|locked|down|maintenance|inspect\w*|"
    r"testing|test)\b"
)
_RESERVE = re.compile(
    r"\b(reserve|keep|maintain|hold|remain|retain|backup|at least|minimum|not (drop|fall|go) below|"
    r"state of charge|soc)\b"
)
_BATTERY = re.compile(r"\b(battery|batteries|storage|bess|soc|state of charge)\b")
_GRID = re.compile(r"\b(grid|import|feeder|transformer|substation|intake|utility|mains)\b")
_CAP = re.compile(
    r"\b(exceed|cap(ped)?|limit(ed)?|at most|no more than|not more than|maximum|max|below|under|"
    r"stay at or below|restricted|constrained)\b"
)

_FRACTION_WORDS = [
    (r"\bhalf\b|\bhalved\b|\b50 ?percent\b", 0.5),
    (r"\bone[- ]?third\b|\ba third\b", 1 / 3),
    (r"\btwo[- ]?thirds\b", 2 / 3),
    (r"\bone[- ]?quarter\b|\ba quarter\b", 0.25),
    (r"\bthree[- ]?quarters\b", 0.75),
    (r"\bone[- ]?fifth\b|\ba fifth\b", 0.2),
    (r"\bone[- ]?tenth\b|\ba tenth\b", 0.1),
]
_REDUCTION_WORDS = re.compile(r"\b(reduc\w*|cut|lose|loss|drop(s|ped)? by|decreas\w*|lower by|down by|by)\b")
_REMAINING_WORDS = re.compile(r"\b(to|only|leave|leaves|leaving|remain\w*|available|usable|of (the )?(forecast|normal))\b")


@dataclass
class RuleInterpretation:
    directive_type: DirectiveType
    hours: list[int]
    factor: float | None = None
    minimum_energy_kwh: float | None = None
    max_grid_kwh: float | None = None
    explanation: str = "Interpreted by the degraded rule-based fallback (LLM unavailable)."


def _kwh_values(lower: str) -> list[float]:
    vals = []
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(mwh|kwh|kw|mw)\b", lower):
        v = float(m.group(1))
        vals.append(v * 1000 if m.group(2) in ("mwh", "mw") else v)
    return vals


def _solar_factor(lower: str) -> float | None:
    pct = re.search(r"(\d+(?:\.\d+)?)\s*(%|percent)", lower)
    if pct:
        v = float(pct.group(1)) / 100
        before = lower[: pct.start()]
        after = lower[pct.end(): pct.end() + 20]
        reduction = bool(re.search(r"\b(reduction|reduced by|cut by|drop(s|ped)? by|lose|loss|lower)\b", before[-25:] + " " + after))
        if re.search(r"\b(to|at|only|about|roughly|around)\s*$", before.strip() + " ") and not re.search(r"by\s*$", before):
            reduction = False
        return round(1 - v if reduction else v, 6)
    for pattern, val in _FRACTION_WORDS:
        m = re.search(pattern, lower)
        if m:
            before = lower[max(0, m.start() - 25): m.start()]
            after = lower[m.end(): m.end() + 15]
            if re.search(r"^\s*(less|lower|fewer)\b", after):
                return round(1 - val, 6)
            if re.search(r"\b(by|cut|reduc\w*|lose)\b", before) and not re.search(r"\b(to|leave|leaves|only)\b", before):
                return round(1 - val, 6)
            return round(val, 6)
    if re.search(r"\b(no solar|zero solar|completely (offline|unavailable)|fully offline|disconnected)\b", lower):
        return 0.0
    return None


def _reserve_kwh(lower: str, capacity_kwh: float, base_min_kwh: float) -> float | None:
    pct = re.search(r"(\d+(?:\.\d+)?)\s*(%|percent)", lower)
    if pct and re.search(r"capacity|state of charge|soc|charged|full", lower):
        return round(float(pct.group(1)) / 100 * capacity_kwh, 6)
    kwh = _kwh_values(lower)
    if kwh:
        if re.search(r"\babove (the )?(usual|normal|base|regular)? ?minimum\b", lower):
            return round(base_min_kwh + kwh[0], 6)
        return kwh[0]
    if re.search(r"\bhalf[- ]full\b|\bhalf (of )?(its|the) capacity\b", lower):
        return round(0.5 * capacity_kwh, 6)
    if re.search(r"\bfully charged\b|\bfull\b", lower):
        return float(capacity_kwh)
    return None


def interpret(note_text: str, capacity_kwh: float, base_min_kwh: float) -> RuleInterpretation | None:
    lower = " ".join(note_text.lower().split())
    if _OTHER_PERIOD.search(lower) or _INJECTION.search(lower):
        return None

    hours = regex_hours(note_text)
    if not hours:
        return None

    discharge = bool(_DISCHARGE.search(lower))
    charge = bool(_CHARGE.search(lower))
    blocked = bool(_BLOCK.search(lower))

    # Grid cap first: it carries its own explicit number and vocabulary.
    if _GRID.search(lower) and _CAP.search(lower):
        kwh = _kwh_values(lower)
        if kwh:
            return RuleInterpretation("max_grid_window", hours, max_grid_kwh=kwh[0])

    if _SOLAR.search(lower) and not _BATTERY.search(lower):
        factor = _solar_factor(lower)
        if factor is not None and 0.0 <= factor <= 1.0:
            return RuleInterpretation("solar_reduction", hours, factor=factor)
        return None

    if (_BATTERY.search(lower) or re.search(r"\b(in reserve|reserve of|kept in store)\b", lower)) and _RESERVE.search(lower) and not (blocked and (charge or discharge) and not re.search(r"\bat least|\bminimum|\breserve", lower)):
        value = _reserve_kwh(lower, capacity_kwh, base_min_kwh)
        if value is not None and 0.0 <= value <= capacity_kwh:
            return RuleInterpretation("minimum_battery_reserve", hours, minimum_energy_kwh=value)

    if discharge and blocked:
        return RuleInterpretation("no_discharge_window", hours)
    if charge and blocked:
        return RuleInterpretation("no_charge_window", hours)
    return None
