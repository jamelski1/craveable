"""Reasoning with constraints: the booking problem as a CSP.

Variables
    R - the restaurant            domain: retrieved restaurants
    T - the table                 domain: every table of every retrieved restaurant
    S - the arrival time slot     domain: 30-minute slots around the requested time

Constraints (all hard unless relaxed)
    unary  on R : cuisine(R), location(R), budget(R)
    unary  on T : capacity(T), window(T), view(T)
    binary R-T  : T belongs to R
    binary R-S  : R is open for the whole meal starting at S
    binary T-S  : T is not booked at S

Solving
    1. node consistency      - prune unary violations and log every rejection
    2. AC-3 arc consistency  - prune values with no support in a neighbour
    3. backtracking search   - MRV variable ordering + forward checking,
                               enumerating every consistent assignment
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from .eventlog import EventLog
from .geo import DEFAULT_RADIUS_MILES, canonical_area, in_area
from .models import Assignment, Preferences, Restaurant, Table, clock12, hhmm, minutes

RELATED_CUISINES = {
    "turkish": ["mediterranean", "greek", "lebanese"],
    "greek": ["mediterranean", "turkish"],
    "lebanese": ["mediterranean", "turkish"],
    "mediterranean": ["turkish", "greek", "lebanese"],
    "japanese": ["sushi", "ramen"],
    "italian": ["pizza"],
    "american": ["diner", "barbecue", "steakhouse"],
}
MEAL_DURATION_MIN = 90


@dataclass(frozen=True)
class RelaxationParams:
    """Knobs the relaxation search is allowed to turn. Defaults = strict query."""
    radius_miles: float = DEFAULT_RADIUS_MILES
    budget_multiplier: float = 1.0
    time_window_min: int = 0
    any_window_view: bool = False
    include_related_cuisines: bool = False

    def describe(self) -> List[str]:
        out = []
        if self.radius_miles != DEFAULT_RADIUS_MILES:
            out.append(f"search radius widened to {self.radius_miles} mi")
        if self.budget_multiplier != 1.0:
            out.append(f"budget stretched by {int((self.budget_multiplier - 1) * 100)}%")
        if self.time_window_min:
            out.append(f"arrival time flexible by ±{self.time_window_min} min")
        if self.any_window_view:
            out.append("any window table accepted regardless of view")
        if self.include_related_cuisines:
            out.append("related cuisines accepted")
        return out


@dataclass
class Constraint:
    name: str
    scope: Tuple[str, ...]                      # ("R",) | ("T",) | ("R","T") | ...
    test: Callable[..., Tuple[bool, str]]       # -> (satisfied?, reason)


@dataclass
class CSPStats:
    initial_domains: Dict[str, int] = field(default_factory=dict)
    after_node_consistency: Dict[str, int] = field(default_factory=dict)
    after_ac3: Dict[str, int] = field(default_factory=dict)
    ac3_revisions: int = 0
    nodes_visited: int = 0
    backtracks: int = 0
    solutions: int = 0


class BookingCSP:
    def __init__(self, prefs: Preferences, restaurants: Sequence[Restaurant],
                 params: RelaxationParams = RelaxationParams(), log: Optional[EventLog] = None):
        self.prefs = prefs
        self.params = params
        self.log = log
        self.stats = CSPStats()
        self.rejections: Dict[str, Dict[str, object]] = {}   # restaurant id -> {"restaurant": [...], "tables": {id: why}}
        self.domains: Dict[str, list] = {
            "R": list(restaurants),
            "T": [t for r in restaurants for t in r.tables],
            "S": self._slot_domain(),
        }
        self.stats.initial_domains = {k: len(v) for k, v in self.domains.items()}
        self.constraints = self._build_constraints()
        self._by_id = {r.id: r for r in restaurants}

    # ------------------------------------------------------------------ #
    # Domains & constraints
    # ------------------------------------------------------------------ #
    def _slot_domain(self) -> List[str]:
        t = self.prefs.time or "19:00"
        base = minutes(t)
        w = self.params.time_window_min
        slots = {base}
        step = 30
        k = step
        while k <= w:
            slots.add(base - k)
            slots.add(base + k)
            k += step
        return [hhmm(m) for m in sorted(slots, key=lambda m: (abs(m - base), m)) if 0 <= m < 24 * 60]

    def _build_constraints(self) -> List[Constraint]:
        p, prm = self.prefs, self.params
        cons: List[Constraint] = []

        if p.cuisine:
            wanted = {p.cuisine.lower()}
            if prm.include_related_cuisines:
                wanted |= set(RELATED_CUISINES.get(p.cuisine.lower(), []))

            def cuisine_ok(r: Restaurant) -> Tuple[bool, str]:
                if r.cuisine.lower() in wanted:
                    return True, f"cuisine {r.cuisine}"
                sec = [s for s in r.secondary_cuisines if s.lower() in wanted]
                if sec:
                    return True, f"serves {sec[0]} as a secondary cuisine"
                return False, f"cuisine is {r.cuisine}, not {p.cuisine}"
            cons.append(Constraint("cuisine", ("R",), cuisine_ok))

        area = canonical_area(p.area) if p.area else None
        if area:
            def location_ok(r: Restaurant) -> Tuple[bool, str]:
                ok, d, why = in_area(area, r.neighborhood, r.lat, r.lon, prm.radius_miles)
                return ok, why
            cons.append(Constraint("location", ("R",), location_ok))

        if p.budget_total:
            limit = p.budget_total * prm.budget_multiplier

            def budget_ok(r: Restaurant) -> Tuple[bool, str]:
                cost = r.cost_for(p.party_size)
                if cost <= limit:
                    return True, f"est. ${cost:.0f} for {p.party_size} <= ${limit:.0f}"
                return False, f"est. ${cost:.0f} for {p.party_size} exceeds ${limit:.0f}"
            cons.append(Constraint("budget", ("R",), budget_ok))

        def capacity_ok(t: Table) -> Tuple[bool, str]:
            if t.seats >= p.party_size:
                return True, f"{t.seats} seats"
            return False, f"only {t.seats} seats for a party of {p.party_size}"
        cons.append(Constraint("capacity", ("T",), capacity_ok))

        if p.window_seat:
            def window_ok(t: Table) -> Tuple[bool, str]:
                return (True, "window table") if t.window else (False, "not a window table")
            cons.append(Constraint("window", ("T",), window_ok))

        if p.views and not prm.any_window_view:
            def view_ok(t: Table) -> Tuple[bool, str]:
                if t.view in p.views or t.view == "unknown":
                    return True, f"{t.view} view"
                return False, f"view is '{t.view}', wanted {' or '.join(sorted(p.views))}"
            cons.append(Constraint("view", ("T",), view_ok))

        cons.append(Constraint("belongs", ("R", "T"),
                               lambda r, t: (t.restaurant_id == r.id, "table belongs to restaurant")))

        if p.day:
            def open_ok(r: Restaurant, s: str) -> Tuple[bool, str]:
                win = r.hours.get(p.day)
                if not win:
                    return False, f"closed on {p.day}"
                if r.is_open_for(p.day, s, MEAL_DURATION_MIN):
                    return True, f"open {hhmm(win[0])}-{hhmm(win[1])} on {p.day}"
                return False, (f"open {hhmm(win[0])}-{hhmm(win[1])} on {p.day}: a {MEAL_DURATION_MIN}-min "
                               f"dinner starting {clock12(s)} does not fit")
            cons.append(Constraint("open", ("R", "S"), open_ok))

            def free_ok(t: Table, s: str) -> Tuple[bool, str]:
                if t.is_free(p.day, s):
                    return True, f"free at {clock12(s)}"
                return False, f"already booked at {clock12(s)}"
            cons.append(Constraint("available", ("T", "S"), free_ok))
        return cons

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _reject(self, var: str, value, constraint: str, reason: str) -> None:
        """Remember why a value left a domain, keyed by restaurant, for explanations.

        'belongs' prunings are structural echoes (a table vanished because its
        restaurant did, or vice versa) and carry no new information, so they are
        not recorded.
        """
        if constraint == "belongs":
            return
        if var == "R":
            self.rejections.setdefault(value.id, {"restaurant": [], "tables": {}})["restaurant"].append(reason)
        elif var == "T":
            entry = self.rejections.setdefault(value.restaurant_id, {"restaurant": [], "tables": {}})
            entry["tables"].setdefault(value.table_id, reason)

    def _label(self, var: str, value) -> str:
        if var == "R":
            return value.name
        if var == "T":
            return f"{self._by_id[value.restaurant_id].name}/{value.table_id}"
        return clock12(value)

    def _check(self, c: Constraint, assignment: Dict[str, object]) -> Tuple[bool, str]:
        return c.test(*[assignment[v] for v in c.scope])

    # ------------------------------------------------------------------ #
    # 1. Node consistency
    # ------------------------------------------------------------------ #
    def node_consistency(self) -> None:
        for c in (c for c in self.constraints if len(c.scope) == 1):
            var = c.scope[0]
            keep, dropped = [], []
            for v in self.domains[var]:
                ok, why = c.test(v)
                if ok:
                    keep.append(v)
                else:
                    dropped.append((v, why))
                    self._reject(var, v, c.name, why)
            self.domains[var] = keep
            if self.log:
                self.log.decision(
                    "CSP",
                    f"unary constraint '{c.name}' on {var}: kept {len(keep)}, pruned {len(dropped)}",
                    pruned=[f"{self._label(var, v)} - {why}" for v, why in dropped][:12],
                    domain_size=len(keep),
                )
        self.stats.after_node_consistency = {k: len(v) for k, v in self.domains.items()}

    # ------------------------------------------------------------------ #
    # 2. AC-3
    # ------------------------------------------------------------------ #
    def ac3(self) -> bool:
        binary = [c for c in self.constraints if len(c.scope) == 2]
        queue = deque()
        for c in binary:
            x, y = c.scope
            queue.append((x, y, c))
            queue.append((y, x, c))
        while queue:
            xi, xj, c = queue.popleft()
            if self._revise(xi, xj, c):
                if not self.domains[xi]:
                    if self.log:
                        self.log.warn("CSP", f"AC-3 wiped out the domain of {xi}: no consistent assignment exists")
                    self.stats.after_ac3 = {k: len(v) for k, v in self.domains.items()}
                    return False
                for c2 in binary:
                    if xi in c2.scope:
                        other = c2.scope[0] if c2.scope[1] == xi else c2.scope[1]
                        if other != xj:
                            queue.append((other, xi, c2))
        self.stats.after_ac3 = {k: len(v) for k, v in self.domains.items()}
        if self.log:
            self.log.info("CSP", "AC-3 arc consistency complete",
                          revisions=self.stats.ac3_revisions, domains=self.stats.after_ac3)
        return True

    def _revise(self, xi: str, xj: str, c: Constraint) -> bool:
        revised = False
        keep = []
        structural = []     # values dropped through 'belongs' - echoes of earlier prunings
        for vi in self.domains[xi]:
            supported = False
            last_reason = ""
            for vj in self.domains[xj]:
                ok, why = self._check(c, {xi: vi, xj: vj})
                if ok:
                    supported = True
                    break
                last_reason = why
            if supported:
                keep.append(vi)
            else:
                revised = True
                self.stats.ac3_revisions += 1
                self._reject(xi, vi, c.name, last_reason)
                if c.name == "belongs":
                    structural.append(self._label(xi, vi))
                elif self.log:
                    self.log.decision("CSP", f"AC-3 pruned {xi}={self._label(xi, vi)}: no {xj} satisfies '{c.name}'",
                                      reason=last_reason)
        if structural and self.log:
            what = "table(s) whose restaurant was already pruned" if xi == "T" else "restaurant(s) with no remaining table"
            self.log.info("CSP", f"AC-3 ('belongs' {xi}-{xj}) dropped {len(structural)} {what}", values=structural)
        if revised:
            self.domains[xi] = keep
        return revised

    # ------------------------------------------------------------------ #
    # 3. Backtracking search with MRV + forward checking
    # ------------------------------------------------------------------ #
    def solve(self) -> List[Assignment]:
        solutions: List[Assignment] = []
        self._backtrack({}, {k: list(v) for k, v in self.domains.items()}, solutions)
        self.stats.solutions = len(solutions)
        if self.log:
            self.log.info("CSP", f"backtracking search found {len(solutions)} consistent assignment(s)",
                          nodes_visited=self.stats.nodes_visited, backtracks=self.stats.backtracks)
        return solutions

    def _consistent(self, assignment: Dict[str, object]) -> bool:
        for c in self.constraints:
            if all(v in assignment for v in c.scope):
                ok, _ = self._check(c, assignment)
                if not ok:
                    return False
        return True

    def _forward_check(self, var: str, value, assignment: Dict[str, object],
                       domains: Dict[str, list]) -> Optional[Dict[str, list]]:
        new = {k: list(v) for k, v in domains.items()}
        trial = dict(assignment, **{var: value})
        for other in new:
            if other in trial:
                continue
            pruned = []
            for v in new[other]:
                if self._consistent(dict(trial, **{other: v})):
                    pruned.append(v)
            new[other] = pruned
            if not pruned:
                return None
        return new

    def _backtrack(self, assignment: Dict[str, object], domains: Dict[str, list],
                   solutions: List[Assignment]) -> None:
        self.stats.nodes_visited += 1
        if len(assignment) == 3:
            solutions.append(Assignment(assignment["R"], assignment["T"], assignment["S"]))
            return
        # MRV: pick the unassigned variable with the fewest remaining values.
        var = min((v for v in domains if v not in assignment), key=lambda v: len(domains[v]))
        for value in domains[var]:
            if not self._consistent(dict(assignment, **{var: value})):
                self.stats.backtracks += 1
                continue
            fc = self._forward_check(var, value, assignment, domains)
            if fc is None:
                self.stats.backtracks += 1
                continue
            assignment[var] = value
            self._backtrack(assignment, fc, solutions)
            del assignment[var]

    # ------------------------------------------------------------------ #
    def run(self) -> List[Assignment]:
        """node consistency -> AC-3 -> backtracking. Returns every consistent assignment."""
        if self.log:
            self.log.info("CSP", "variables R (restaurant), T (table), S (slot)",
                          domains=self.stats.initial_domains,
                          constraints=[f"{c.name}{c.scope}" for c in self.constraints],
                          relaxations=self.params.describe() or ["none (strict query)"])
        self.node_consistency()
        if not all(self.domains.values()):
            empty = [k for k, v in self.domains.items() if not v]
            if self.log:
                self.log.warn("CSP", f"empty domain after node consistency: {empty}")
            return []
        if not self.ac3():
            return []
        return self.solve()
