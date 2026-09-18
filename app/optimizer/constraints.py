"""Directive -> per-hour bounds (§5.1). Two combination rules are involved:

- WITHIN one note's own candidates (primary + hedge/alternative readings of the *same* event):
  take the more-restrictive per-hour value (§4.9) — e.g. min(factor), union(no-charge hours).
- ACROSS different notes (independent directives): combine per §5.1 — product(factor), max(reserve),
  union(no-charge/no-discharge), min(grid_cap).

For solar_reduction these two rules differ (min vs product), so factor is computed in two passes:
first collapse each note's own candidates to one effective per-hour factor, then multiply across
notes. The other four directive types use an associative/idempotent combinator (max, union, union,
min), so within-note and across-note collapse to the same single pass.
"""

from dataclasses import dataclass, field

from app.schemas.directives import AppliedDirective


@dataclass
class HourlyBounds:
    factor: list[float] = field(default_factory=lambda: [1.0] * 24)
    reserve: list[float] = field(default_factory=lambda: [0.0] * 24)
    charge_ok: list[bool] = field(default_factory=lambda: [True] * 24)
    discharge_ok: list[bool] = field(default_factory=lambda: [True] * 24)
    grid_cap: list[float] = field(default_factory=lambda: [float("inf")] * 24)


def build_bounds(
    applied: list[AppliedDirective],
    base_min_kwh: float,
    include_hedges: bool = True,
) -> HourlyBounds:
    bounds = HourlyBounds(reserve=[base_min_kwh] * 24)

    directives = applied if include_hedges else [a for a in applied if not a.is_hedge]

    # Pass 1: within-note collapse for solar_reduction (min per hour across that note's own candidates).
    by_note_solar: dict[int, list[float]] = {}
    for d in directives:
        if d.type != "solar_reduction":
            continue
        note_factor = by_note_solar.setdefault(d.note_index, [1.0] * 24)
        f = d.factor if d.factor is not None else 1.0
        for h in d.hours:
            note_factor[h] = min(note_factor[h], f)

    # Pass 2: across-note combination for solar (product).
    for note_factor in by_note_solar.values():
        for h in range(24):
            bounds.factor[h] *= note_factor[h]

    for d in directives:
        if d.type == "minimum_battery_reserve" and d.minimum_energy_kwh is not None:
            for h in d.hours:
                bounds.reserve[h] = max(bounds.reserve[h], d.minimum_energy_kwh)
        elif d.type == "no_charge_window":
            for h in d.hours:
                bounds.charge_ok[h] = False
        elif d.type == "no_discharge_window":
            for h in d.hours:
                bounds.discharge_ok[h] = False
        elif d.type == "max_grid_window" and d.max_grid_kwh is not None:
            for h in d.hours:
                bounds.grid_cap[h] = min(bounds.grid_cap[h], d.max_grid_kwh)

    return bounds
