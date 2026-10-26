"""Searching for solutions.

Two search problems live here:

1. ``best_first_rank``  - A* / branch-and-bound over the assignment tree
   (restaurant -> table -> slot). Path cost is the utility *lost* so far
   (1 - U), and the heuristic is the utility that can still be lost at best
   (admissible: it never over-estimates the loss). The search pops complete
   assignments in non-increasing utility order, so the first k goals popped are
   exactly the top-k results - without scoring every leaf.

2. ``relaxation_search`` - uniform-cost search over *sets of constraint
   relaxations*. When the strict query has no solution the agent must decide
   which constraint to bend. Each relaxation has a regret cost; the cheapest
   relaxation set that yields at least one consistent assignment wins.
"""

from __future__ import annotations

import heapq
import itertools
from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Sequence, Tuple

from .constraints import BookingCSP, RelaxationParams
from .eventlog import EventLog
from .models import Assignment, Preferences, Relaxation, Restaurant
from .ranking import WEIGHTS, restaurant_upper_bound, score


# --------------------------------------------------------------------------- #
# 1. Best-first (A*) ranking over consistent assignments
# --------------------------------------------------------------------------- #
@dataclass
class SearchStats:
    nodes_generated: int = 0
    nodes_expanded: int = 0
    goals_popped: int = 0
    leaves_total: int = 0


def best_first_rank(solutions: Sequence[Assignment], prefs: Preferences, k: int,
                    log: Optional[EventLog] = None) -> Tuple[List[tuple], SearchStats]:
    """Return the k best restaurants by utility using A* over the assignment tree.

    Each result is ``(assignment, utility, components, raw_scores, alternatives)``
    where ``alternatives`` lists other consistent (table, slot) choices at the
    same restaurant, in utility order.
    """
    stats = SearchStats(leaves_total=len(solutions))
    by_restaurant: Dict[str, List[Assignment]] = {}
    for a in solutions:
        by_restaurant.setdefault(a.restaurant.id, []).append(a)

    counter = itertools.count()          # tie-breaker keeps heap deterministic
    frontier: list = []

    # Root -> one child per restaurant. f = g + h where g = 0 (nothing decided
    # yet) and h = 1 - (best possible utility for that restaurant).
    for rid, group in sorted(by_restaurant.items()):
        r = group[0].restaurant
        h = 1.0 - restaurant_upper_bound(r, prefs)
        heapq.heappush(frontier, (h, next(counter), "restaurant", r, group))
        stats.nodes_generated += 1
        if log:
            log.debug("SEARCH", f"frontier += {r.name} (f = h = {h:.3f}, optimistic U = {1 - h:.3f})")

    results = []
    alternatives: Dict[str, List[Assignment]] = {}
    while frontier and len(results) < k:
        f, _, kind, node, payload = heapq.heappop(frontier)
        if kind == "goal":
            stats.goals_popped += 1
            a, u, comps, raw = node, payload[0], payload[1], payload[2]
            if a.restaurant.id in alternatives:
                # Same restaurant already recommended with a better-or-equal table:
                # keep this one as an alternative seating rather than a new row.
                alternatives[a.restaurant.id].append(a)
                if log:
                    log.info("SEARCH", f"goal popped: {a.restaurant.name} / table {a.table.table_id} @ {a.slot} "
                             f"U = {u:.3f} - kept as an alternative seating")
                continue
            alternatives[a.restaurant.id] = []
            results.append((a, u, comps, raw, alternatives[a.restaurant.id]))
            if log:
                log.decision("SEARCH", f"goal popped #{len(results)}: {a.restaurant.name} / table {a.table.table_id} "
                             f"@ {a.slot} with U = {u:.3f} (f = {f:.3f})")
            continue
        stats.nodes_expanded += 1
        # Expand a restaurant node into its concrete (table, slot) leaves, each
        # a goal with exact cost g = 1 - U and h = 0.
        for a in payload:
            u, comps, raw = score(a, prefs)
            heapq.heappush(frontier, (1.0 - u, next(counter), "goal", a, (u, comps, raw)))
            stats.nodes_generated += 1
        if log:
            log.info("SEARCH", f"expanded {node.name}: {len(payload)} table/slot leaves pushed "
                     f"(optimistic U was {1 - f:.3f})")
    if log:
        log.info("SEARCH", "best-first search finished",
                 nodes_generated=stats.nodes_generated, nodes_expanded=stats.nodes_expanded,
                 goals_popped=stats.goals_popped, leaves_total=stats.leaves_total,
                 frontier_left=len(frontier))
    return results, stats


# --------------------------------------------------------------------------- #
# 2. Uniform-cost search over constraint relaxations
# --------------------------------------------------------------------------- #
RELAXATION_MOVES: List[Tuple[str, float, str]] = [
    # (name, regret cost, description)
    ("time+30", 1.0, "accept an arrival time within ±30 minutes"),
    ("time+60", 2.0, "accept an arrival time within ±60 minutes"),
    ("view:any-window", 1.0, "accept any window table, whatever the view"),
    ("budget+15%", 1.5, "stretch the budget by 15%"),
    ("radius:1.5mi", 1.5, "look up to 1.5 miles from the requested area"),
    ("budget+30%", 3.0, "stretch the budget by 30%"),
    ("radius:3mi", 3.0, "look up to 3 miles from the requested area"),
    ("cuisine:related", 2.5, "accept related cuisines (e.g. Mediterranean for Turkish)"),
]


def apply_move(params: RelaxationParams, name: str) -> RelaxationParams:
    if name == "time+30":
        return replace(params, time_window_min=max(params.time_window_min, 30))
    if name == "time+60":
        return replace(params, time_window_min=max(params.time_window_min, 60))
    if name == "view:any-window":
        return replace(params, any_window_view=True)
    if name == "budget+15%":
        return replace(params, budget_multiplier=max(params.budget_multiplier, 1.15))
    if name == "budget+30%":
        return replace(params, budget_multiplier=max(params.budget_multiplier, 1.30))
    if name == "radius:1.5mi":
        return replace(params, radius_miles=max(params.radius_miles, 1.5))
    if name == "radius:3mi":
        return replace(params, radius_miles=max(params.radius_miles, 3.0))
    if name == "cuisine:related":
        return replace(params, include_related_cuisines=True)
    raise ValueError(name)


def relaxation_search(prefs: Preferences, restaurants: Sequence[Restaurant],
                      start: RelaxationParams = RelaxationParams(), log: Optional[EventLog] = None,
                      max_expansions: int = 60) -> Tuple[Optional[RelaxationParams], List[Relaxation], List[Assignment], Optional[BookingCSP]]:
    """Uniform-cost search: states are relaxation sets, step cost is the move's regret.

    Returns (params, applied relaxations, solutions, csp) for the cheapest state
    that admits at least one consistent assignment, or (None, [], [], None).
    """
    moves = {m[0]: m for m in RELAXATION_MOVES}
    counter = itertools.count()
    frontier = [(0.0, next(counter), start, ())]
    seen = {start}
    expansions = 0
    while frontier and expansions < max_expansions:
        cost, _, params, applied = heapq.heappop(frontier)
        expansions += 1
        if applied:   # the start state was already proven infeasible by the caller
            csp = BookingCSP(prefs, restaurants, params, log=None)   # quiet: only the winner is traced
            sols = csp.run()
            if log:
                log.decision("RELAX", f"tried relaxation set {list(applied)} (regret {cost:.1f}): "
                             f"{len(sols)} consistent assignment(s)")
            if sols:
                relaxations = [Relaxation(n, moves[n][1], moves[n][2]) for n in applied]
                if log:
                    log.decision("RELAX", f"cheapest feasible relaxation set: {list(applied)} with total regret {cost:.1f}",
                                 relaxations=[r.description for r in relaxations])
                return params, relaxations, sols, csp
        for name, step_cost, _desc in RELAXATION_MOVES:
            if name in applied:
                continue
            nxt = apply_move(params, name)
            if nxt == params or nxt in seen:
                continue
            seen.add(nxt)
            heapq.heappush(frontier, (cost + step_cost, next(counter), nxt, applied + (name,)))
    if log:
        log.warn("RELAX", f"no feasible relaxation set found within {max_expansions} expansions")
    return None, [], [], None
