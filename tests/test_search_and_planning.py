from craveable import planner
from craveable.constraints import BookingCSP, RelaxationParams
from craveable.datasource import CsvSource
from craveable.nlp import RuleBasedParser
from craveable.ranking import WEIGHTS, restaurant_upper_bound, score
from craveable.search import best_first_rank, relaxation_search
from tests.test_nlp import SCENARIO


def _solutions(prefs):
    return BookingCSP(prefs, CsvSource().load_all()).run()


def test_heuristic_is_admissible():
    prefs = RuleBasedParser().parse(SCENARIO)
    for a in _solutions(prefs):
        u, comps, raw = score(a, prefs)
        assert u <= restaurant_upper_bound(a.restaurant, prefs) + 1e-9
        assert abs(sum(comps.values()) - u) < 1e-9
        assert all(0.0 <= v <= 1.0 for v in raw.values())
    assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9


def test_best_first_matches_brute_force_order():
    prefs = RuleBasedParser().parse(SCENARIO)
    sols = _solutions(prefs)
    top, stats = best_first_rank(sols, prefs, k=10)
    utilities = [u for _, u, _, _, _ in top]
    assert utilities == sorted(utilities, reverse=True)
    best_per_restaurant = {}
    for a in sols:
        u = score(a, prefs)[0]
        best_per_restaurant[a.restaurant.id] = max(u, best_per_restaurant.get(a.restaurant.id, 0))
    assert utilities == sorted(best_per_restaurant.values(), reverse=True)
    assert top[0][0].restaurant.name == "Bosphorus Table"
    assert stats.goals_popped <= stats.leaves_total


def test_planner_finds_shortest_plan_and_replans():
    res = planner.plan(frozenset({"query_received"}))
    assert [o.name for o in res.plan] == [
        "ParseQuery", "RetrieveCandidates", "PropagateConstraints", "SearchSolutions",
        "RankSolutions", "ExplainResults", "PresentResults",
    ]
    stuck = frozenset({"query_received", "preferences_known", "candidates_retrieved",
                       "constraints_propagated", "solutions_enumerated", "no_solution"})
    res2 = planner.plan(stuck)
    assert [o.name for o in res2.plan] == ["RelaxConstraints", "RankSolutions", "ExplainResults", "PresentResults"]


def test_relaxation_search_picks_cheapest_regret():
    prefs = RuleBasedParser().parse(SCENARIO.replace("$65", "$45"))
    rows = CsvSource().load_all()
    assert BookingCSP(prefs, rows).run() == []
    params, relaxations, sols, _ = relaxation_search(prefs, rows, RelaxationParams())
    assert [r.name for r in relaxations] == ["budget+15%"]
    assert params.budget_multiplier == 1.15
    assert {a.restaurant.name for a in sols} == {"Olive & Thyme"}
