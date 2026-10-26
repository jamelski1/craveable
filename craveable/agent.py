"""The Craveable agent: plan -> execute -> monitor -> (re-plan) loop.

Agent type
    * goal-based   - the goal is a set of fluents (ranked, explained, presented
                     results); a deterministic planner chooses the actions.
    * utility-based - among all assignments that satisfy the hard constraints,
                     a weighted additive utility picks and orders the best.

Each operator from ``planner.OPERATORS`` is bound to a method below. The agent
executes the plan step by step and observes the resulting state; when a step
does not deliver the effect the planner assumed (no consistent assignment), it
re-plans from the observed state, which introduces ``RelaxConstraints``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from . import planner
from .constraints import BookingCSP, RelaxationParams
from .datasource import get_source
from .eventlog import EventLog
from .explain import criteria_checklist, explain
from .models import Assignment, Preferences, RankedResult, Relaxation, Restaurant
from .nlp import get_parser
from .search import best_first_rank, relaxation_search


@dataclass
class AgentReport:
    query: str
    preferences: Preferences
    plan: List[str]
    replanned: bool
    results: List[RankedResult]
    near_misses: Dict[str, List[str]]        # restaurant name -> why it was rejected
    relaxations: List[Relaxation]
    csp_stats: dict
    search_stats: dict
    log: EventLog

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "preferences": self.preferences.describe(),
            "plan": self.plan,
            "replanned": self.replanned,
            "relaxations": [r.description for r in self.relaxations],
            "results": [r.to_dict() for r in self.results],
            "near_misses": self.near_misses,
            "csp_stats": self.csp_stats,
            "search_stats": self.search_stats,
        }


class CraveableAgent:
    def __init__(self, nlp: str = "rules", source: str = "csv", data_dir: Optional[Path] = None,
                 top_k: int = 5, allow_relaxation: bool = True, log: Optional[EventLog] = None):
        self.parser = get_parser(nlp)
        self.source = get_source(source, data_dir)
        self.top_k = top_k
        self.allow_relaxation = allow_relaxation
        self.log = log or EventLog()
        # working memory ---------------------------------------------------
        self.prefs: Optional[Preferences] = None
        self.candidates: List[Restaurant] = []
        self.csp: Optional[BookingCSP] = None
        self.params = RelaxationParams()
        self.solutions: List[Assignment] = []
        self.relaxations: List[Relaxation] = []
        self.ranked: List[RankedResult] = []
        self.search_stats: dict = {}
        self._actions: Dict[str, Callable[[], None]] = {
            "ParseQuery": self._parse_query,
            "RetrieveCandidates": self._retrieve,
            "PropagateConstraints": self._propagate,
            "SearchSolutions": self._search,
            "RelaxConstraints": self._relax,
            "RankSolutions": self._rank,
            "ExplainResults": self._explain,
            "PresentResults": self._present,
        }

    # ------------------------------------------------------------------ #
    # Plan / execute / monitor
    # ------------------------------------------------------------------ #
    def run(self, query: str) -> AgentReport:
        self._query = query
        state: planner.State = frozenset({"query_received"})
        self.log.plan("PLAN", "goal", fluents=sorted(planner.GOAL))
        executed: List[str] = []
        replanned = False
        for attempt in range(3):
            result = planner.plan(state, log=self.log)
            if result is None:
                raise RuntimeError("planner could not find a plan from state %s" % sorted(state))
            for op in result.plan:
                self.log.plan("PLAN", f"execute {op.name}", pre=sorted(op.pre), effects=sorted(op.add))
                self._actions[op.name]()
                executed.append(op.name)
                observed = self._observe(op, op.apply(state))
                if observed != op.apply(state):
                    self.log.warn("PLAN", f"effect of {op.name} did not hold: observed "
                                  f"{sorted(observed - state)} - re-planning", expected=sorted(op.add))
                    state = observed
                    replanned = True
                    break
                state = observed
            else:
                break
        return AgentReport(
            query=query,
            preferences=self.prefs,
            plan=executed,
            replanned=replanned,
            results=self.ranked,
            near_misses=self._near_misses(),
            relaxations=self.relaxations,
            csp_stats=self.csp.stats.__dict__ if self.csp else {},
            search_stats=self.search_stats,
            log=self.log,
        )

    def _observe(self, op: planner.Operator, expected: planner.State) -> planner.State:
        """Execution monitoring: check the world against the planner's assumption."""
        if op.name == "SearchSolutions" and not self.solutions:
            if not self.allow_relaxation:
                raise RuntimeError("no restaurant satisfies every constraint and relaxation is disabled")
            return frozenset((expected - {"solutions_found"}) | {"no_solution"})
        return expected

    # ------------------------------------------------------------------ #
    # Bound actions
    # ------------------------------------------------------------------ #
    def _parse_query(self) -> None:
        self.log.info("NLP", f"parsing with the {self.parser.name} parser", query=self._query)
        self.prefs = self.parser.parse(self._query, self.log)
        self.log.decision("NLP", "goal specification extracted", **self.prefs.describe())

    def _retrieve(self) -> None:
        self.candidates = self.source.search(self.prefs, self.log)

    def _propagate(self) -> None:
        self.csp = BookingCSP(self.prefs, self.candidates, self.params, log=self.log)
        self.log.info("CSP", "variables R (restaurant), T (table), S (slot)",
                      domains=self.csp.stats.initial_domains,
                      constraints=[f"{c.name}{c.scope}" for c in self.csp.constraints],
                      relaxations=self.params.describe() or ["none (strict query)"])
        self.csp.node_consistency()
        if all(self.csp.domains.values()):
            self.csp.ac3()
        else:
            empty = [k for k, v in self.csp.domains.items() if not v]
            self.log.warn("CSP", f"empty domain after node consistency: {empty}")

    def _search(self) -> None:
        self.solutions = self.csp.solve() if all(self.csp.domains.values()) else []
        if self.solutions:
            names = sorted({a.restaurant.name for a in self.solutions})
            self.log.decision("SEARCH", f"{len(self.solutions)} consistent assignment(s) across "
                              f"{len(names)} restaurant(s)", restaurants=names)
        else:
            self.log.warn("SEARCH", "no assignment satisfies all hard constraints")

    def _relax(self) -> None:
        self.log.info("RELAX", "uniform-cost search over constraint relaxations (cheapest regret first)")
        params, relaxations, sols, csp = relaxation_search(self.prefs, self.candidates, self.params, self.log)
        if params is None:
            raise RuntimeError("no relaxation of the query produces a feasible booking")
        self.params, self.relaxations, self.solutions, self.csp = params, relaxations, sols, csp
        # Re-run the winning CSP with logging so the trace shows the relaxed reasoning.
        self.csp = BookingCSP(self.prefs, self.candidates, params, log=self.log)
        self.solutions = self.csp.run()

    def _rank(self) -> None:
        self.log.info("RANK", "best-first (A*) search over consistent assignments, ordered by utility")
        top, stats = best_first_rank(self.solutions, self.prefs, self.top_k, self.log)
        self.search_stats = stats.__dict__
        self.ranked = [
            RankedResult(rank=i + 1, assignment=a, utility=u, components=comps, raw_scores=raw,
                         explanations={}, relaxations=list(self.relaxations), alternatives=alts)
            for i, (a, u, comps, raw, alts) in enumerate(top)
        ]
        for r in self.ranked:
            self.log.decision("RANK", f"#{r.rank} {r.assignment.restaurant.name} U={r.utility:.3f}",
                              **{k: round(v, 3) for k, v in r.raw_scores.items()})

    def _explain(self) -> None:
        for r in self.ranked:
            r.explanations = explain(r.assignment, self.prefs, r.raw_scores, self.relaxations)
            checks = criteria_checklist(r.assignment, self.prefs)
            self.log.info("EXPLAIN", f"#{r.rank} {r.assignment.restaurant.name}: "
                          + ", ".join(f"{k}={'ok' if v else 'relaxed'}" for k, v in checks.items()))

    def _present(self) -> None:
        self.log.plan("ACT", f"presenting {len(self.ranked)} ranked recommendation(s)")

    # ------------------------------------------------------------------ #
    def _near_misses(self) -> Dict[str, List[str]]:
        """Restaurants of the requested cuisine that were pruned, with reasons."""
        if not self.csp:
            return {}
        by_id = {r.id: r for r in self.candidates}
        chosen = {r.assignment.restaurant.id for r in self.ranked}
        out: Dict[str, List[str]] = {}
        for rid, entry in self.csp.rejections.items():
            r = by_id.get(rid)
            if not r or rid in chosen:
                continue
            if self.prefs.cuisine and r.serves(self.prefs.cuisine) == "no":
                continue
            reasons: List[str] = []
            for why in entry["restaurant"]:
                if why not in reasons:
                    reasons.append(why)
            if not reasons:   # every table was ruled out: say why, table by table
                reasons = [f"table {tid}: {why}" for tid, why in entry["tables"].items()]
            out[r.name] = reasons
        return out
