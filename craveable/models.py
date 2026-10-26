"""Core data types shared by every stage of the Craveable pipeline.

The agent's *world* is small and fully observable:

* ``Preferences``  - the goal specification extracted from the user's query.
* ``Restaurant`` / ``Table`` - the environment the agent searches over.
* ``Assignment``   - a complete CSP solution (restaurant, table, time slot).
* ``RankedResult`` - an assignment plus its utility breakdown and explanations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def minutes(hhmm: str) -> int:
    """'19:30' -> 1170 minutes after midnight."""
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def hhmm(total_minutes: int) -> str:
    """1170 -> '19:30'."""
    return f"{total_minutes // 60:02d}:{total_minutes % 60:02d}"


def clock12(hhmm_str: str) -> str:
    """'19:30' -> '7:30 PM'."""
    m = minutes(hhmm_str)
    h, mm = divmod(m, 60)
    suffix = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"{h12}:{mm:02d} {suffix}"


# --------------------------------------------------------------------------- #
# Goal specification
# --------------------------------------------------------------------------- #
@dataclass
class Preferences:
    """What the user asked for. Hard constraints are the fields the CSP enforces;
    the remaining fields shape the utility function."""

    raw_query: str = ""
    cuisine: Optional[str] = None
    area: Optional[str] = None            # e.g. "Downtown"
    city: Optional[str] = None            # e.g. "Baltimore"
    state: Optional[str] = None           # e.g. "MD"
    party_size: int = 2
    budget_total: Optional[float] = None  # dollars for the whole party
    day: Optional[str] = None             # "Thu"
    time: Optional[str] = None            # "19:30"
    meal: Optional[str] = None            # "dinner"
    window_seat: bool = False
    views: Set[str] = field(default_factory=set)   # {"garden", "street"}
    seating_notes: List[str] = field(default_factory=list)
    extraction_trace: List[Tuple[str, str, str]] = field(default_factory=list)
    # (field, value, evidence span)

    def describe(self) -> Dict[str, str]:
        return {
            "cuisine": self.cuisine or "any",
            "location": ", ".join(x for x in [self.area, self.city, self.state] if x) or "any",
            "party_size": str(self.party_size),
            "budget_total": f"${self.budget_total:.0f}" if self.budget_total else "none",
            "day": self.day or "any",
            "time": clock12(self.time) if self.time else "any",
            "meal": self.meal or "any",
            "window_seat": "yes" if self.window_seat else "no preference",
            "views": " or ".join(sorted(self.views)) if self.views else "no preference",
        }


# --------------------------------------------------------------------------- #
# Environment
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Table:
    restaurant_id: str
    table_id: str
    seats: int
    window: bool
    view: str                       # "garden" | "street" | "harbor" | "none" | ...
    zone: str
    booked: frozenset               # of "Thu 19:30" strings

    def is_free(self, day: str, time: str) -> bool:
        return f"{day} {time}" not in self.booked


@dataclass(frozen=True)
class Restaurant:
    id: str
    name: str
    cuisine: str
    secondary_cuisines: Tuple[str, ...]
    neighborhood: str
    address: str
    lat: float
    lon: float
    price_tier: str
    avg_dinner_pp: float
    rating: float
    review_count: int
    accepts_reservations: bool
    hours: Dict[str, Tuple[int, int]]   # day -> (open_minutes, close_minutes)
    notes: str
    tables: Tuple[Table, ...]

    def cost_for(self, party_size: int) -> float:
        return self.avg_dinner_pp * party_size

    def serves(self, cuisine: str) -> str:
        """'primary' | 'secondary' | 'no'."""
        c = cuisine.lower()
        if self.cuisine.lower() == c:
            return "primary"
        if c in (s.lower() for s in self.secondary_cuisines):
            return "secondary"
        return "no"

    def open_window(self, day: str) -> Optional[Tuple[int, int]]:
        return self.hours.get(day)

    def is_open_for(self, day: str, time: str, duration_min: int = 90) -> bool:
        win = self.hours.get(day)
        if not win:
            return False
        start = minutes(time)
        return win[0] <= start and start + duration_min <= win[1]


# --------------------------------------------------------------------------- #
# Solutions
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Assignment:
    """A complete assignment of the CSP variables R (restaurant), T (table), S (slot)."""
    restaurant: Restaurant
    table: Table
    slot: str   # "19:30"

    def key(self) -> Tuple[str, str, str]:
        return (self.restaurant.id, self.table.table_id, self.slot)


@dataclass
class Relaxation:
    """A deliberate loosening of one hard constraint, with a cost (regret)."""
    name: str
    cost: float
    description: str


@dataclass
class RankedResult:
    rank: int
    assignment: Assignment
    utility: float
    components: Dict[str, float]            # weighted contribution per criterion
    raw_scores: Dict[str, float]            # unweighted 0..1 score per criterion
    explanations: Dict[str, str]            # criterion -> human sentence
    relaxations: List[Relaxation] = field(default_factory=list)
    alternatives: List[Assignment] = field(default_factory=list)   # other tables/slots at the same restaurant

    def to_dict(self) -> dict:
        r, t = self.assignment.restaurant, self.assignment.table
        return {
            "alternatives": [f"table {a.table.table_id} ({a.table.view} view) at {a.slot}" for a in self.alternatives],
            "rank": self.rank,
            "restaurant": r.name,
            "restaurant_id": r.id,
            "cuisine": r.cuisine,
            "neighborhood": r.neighborhood,
            "address": r.address,
            "table": t.table_id,
            "table_zone": t.zone,
            "view": t.view,
            "slot": self.assignment.slot,
            "utility": round(self.utility, 4),
            "components": {k: round(v, 4) for k, v in self.components.items()},
            "raw_scores": {k: round(v, 4) for k, v in self.raw_scores.items()},
            "explanations": self.explanations,
            "relaxations": [r.name for r in self.relaxations],
        }
