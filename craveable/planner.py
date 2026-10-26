"""Deterministic planning: a STRIPS-style planner that decides *which pipeline
steps to run, in which order*.

States are sets of propositional fluents describing what the agent knows;
operators have preconditions (positive and negative) and add/delete effects.
A breadth-first forward search over state space returns the shortest plan
that reaches the goal. The agent then executes the plan step by step and
monitors the outcome of each step; if an effect did not materialise (for
example ``SearchSolutions`` found nothing), it re-plans from the observed
state, which brings ``RelaxConstraints`` into the plan.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

from .eventlog import EventLog

State = FrozenSet[str]


@dataclass(frozen=True)
class Operator:
    name: str
    pre: FrozenSet[str] = frozenset()
    pre_not: FrozenSet[str] = frozenset()
    add: FrozenSet[str] = frozenset()
    delete: FrozenSet[str] = frozenset()

    def applicable(self, s: State) -> bool:
        return self.pre <= s and not (self.pre_not & s)

    def apply(self, s: State) -> State:
        return frozenset((s - self.delete) | self.add)


def _op(name: str, pre=(), pre_not=(), add=(), delete=()) -> Operator:
    return Operator(name, frozenset(pre), frozenset(pre_not), frozenset(add), frozenset(delete))


# Fluents ------------------------------------------------------------------
# query_received, preferences_known, candidates_retrieved, constraints_propagated,
# solutions_enumerated, solutions_found, no_solution, constraints_relaxed,
# results_ranked, explanations_ready, results_presented

OPERATORS: List[Operator] = [
    _op("ParseQuery", pre=["query_received"], add=["preferences_known"]),
    _op("RetrieveCandidates", pre=["preferences_known"], add=["candidates_retrieved"]),
    _op("PropagateConstraints", pre=["candidates_retrieved"], add=["constraints_propagated"]),
    # Planned optimistically: we assume solutions will be found. Execution monitoring
    # replaces 'solutions_found' with 'no_solution' when that assumption fails.
    _op("SearchSolutions", pre=["constraints_propagated"], pre_not=["solutions_enumerated"],
        add=["solutions_enumerated", "solutions_found"]),
    _op("RelaxConstraints", pre=["solutions_enumerated", "no_solution"],
        add=["constraints_relaxed", "solutions_found"], delete=["no_solution"]),
    _op("RankSolutions", pre=["solutions_found"], add=["results_ranked"]),
    _op("ExplainResults", pre=["results_ranked"], add=["explanations_ready"]),
    _op("PresentResults", pre=["explanations_ready"], add=["results_presented"]),
]

GOAL: FrozenSet[str] = frozenset({"results_ranked", "explanations_ready", "results_presented"})


@dataclass
class PlanResult:
    plan: List[Operator]
    states_expanded: int
    final_state: State


def plan(initial: State, goal: FrozenSet[str] = GOAL, operators: List[Operator] = OPERATORS,
         log: Optional[EventLog] = None) -> Optional[PlanResult]:
    """Breadth-first forward state-space search: returns the shortest plan."""
    frontier = deque([(initial, [])])
    seen: Set[State] = {initial}
    expanded = 0
    while frontier:
        s, path = frontier.popleft()
        if goal <= s:
            if log:
                log.plan("PLAN", f"plan found: {' -> '.join(o.name for o in path)}",
                         length=len(path), states_expanded=expanded)
            return PlanResult(list(path), expanded, s)
        expanded += 1
        for op in operators:
            if op.applicable(s):
                nxt = op.apply(s)
                if nxt not in seen:
                    seen.add(nxt)
                    frontier.append((nxt, path + [op]))
    if log:
        log.warn("PLAN", "no plan reaches the goal", states_expanded=expanded)
    return None
