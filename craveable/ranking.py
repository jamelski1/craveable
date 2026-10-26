"""Utility-based ranking.

Every consistent assignment satisfies the hard constraints, so the agent needs
a *preference* ordering. We use a weighted additive utility over seven
criteria, each normalised to [0, 1]:

    U = Σ_i  w_i · s_i(assignment)

The weights sum to 1, so U is also in [0, 1] and is easy to read.
"""

from __future__ import annotations

import math
from typing import Dict, Tuple

from .geo import canonical_area, distance_to_area
from .models import Assignment, Preferences, Restaurant, Table, minutes

WEIGHTS: Dict[str, float] = {
    "cuisine": 0.20,      # primary match beats secondary / related
    "rating": 0.25,       # diner satisfaction
    "price": 0.20,        # slack under the budget
    "distance": 0.10,     # proximity to the requested area's centre
    "seating": 0.10,      # window + requested view
    "time": 0.10,         # closeness to the requested arrival time
    "popularity": 0.05,   # review volume (confidence in the rating)
}
assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def restaurant_scores(r: Restaurant, prefs: Preferences) -> Dict[str, float]:
    """Criteria that depend on the restaurant only (used by the A* heuristic too)."""
    s: Dict[str, float] = {}
    if prefs.cuisine:
        how = r.serves(prefs.cuisine)
        s["cuisine"] = 1.0 if how == "primary" else 0.6 if how == "secondary" else 0.3
    else:
        s["cuisine"] = 1.0
    s["rating"] = _clamp((r.rating - 3.0) / 2.0)
    if prefs.budget_total:
        cost = r.cost_for(prefs.party_size)
        # full marks when the meal costs half the budget or less; zero at/over budget
        s["price"] = _clamp((prefs.budget_total - cost) / (0.5 * prefs.budget_total))
    else:
        s["price"] = 1.0 - _clamp(r.avg_dinner_pp / 100.0)
    area = canonical_area(prefs.area) if prefs.area else None
    if area:
        d = distance_to_area(area, r.lat, r.lon)
        s["distance"] = _clamp(1.0 - d / 1.0)   # 0 beyond 1 mile
    else:
        s["distance"] = 1.0
    s["popularity"] = _clamp(math.log10(r.review_count + 1) / 3.0)   # 1000 reviews -> 1.0
    return s


def table_score(t: Table, prefs: Preferences) -> float:
    if not prefs.window_seat and not prefs.views:
        return 1.0
    if not t.window:
        return 0.0
    if not prefs.views:
        return 1.0
    if t.view in prefs.views:
        return 1.0
    if t.view == "unknown":
        return 0.5
    return 0.4   # window, but the wrong view


def slot_score(slot: str, prefs: Preferences) -> float:
    if not prefs.time:
        return 1.0
    delta = abs(minutes(slot) - minutes(prefs.time))
    return _clamp(1.0 - delta / 60.0)


def score(a: Assignment, prefs: Preferences) -> Tuple[float, Dict[str, float], Dict[str, float]]:
    raw = restaurant_scores(a.restaurant, prefs)
    raw["seating"] = table_score(a.table, prefs)
    raw["time"] = slot_score(a.slot, prefs)
    components = {k: WEIGHTS[k] * raw[k] for k in WEIGHTS}
    return sum(components.values()), components, raw


def restaurant_upper_bound(r: Restaurant, prefs: Preferences) -> float:
    """Admissible heuristic for best-first search: the best utility any table/slot
    of this restaurant could possibly reach (seating and time assumed perfect)."""
    raw = restaurant_scores(r, prefs)
    return sum(WEIGHTS[k] * raw[k] for k in raw) + WEIGHTS["seating"] + WEIGHTS["time"]
