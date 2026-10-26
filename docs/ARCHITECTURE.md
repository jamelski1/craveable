# Craveable - agent architecture

## 1. The task environment (PEAS)

| | |
|---|---|
| **Performance measure** | recommend bookings that satisfy every hard constraint in the request; among those, maximise a utility over price slack, rating, proximity, seating quality, time fit and popularity; explain each recommendation against each criterion |
| **Environment** | a catalogue of restaurants with hours, prices, ratings and coordinates, each with a set of tables that have capacity, window/view attributes and booked slots. Fully observable, deterministic, static, discrete |
| **Actuators** | the ranked report (terminal, JSON, chart) and the decision trace |
| **Sensors** | the natural-language query and the dataset / search API |

The agent is **goal-based** at the top level (a planner chooses actions to
reach a goal state) and **utility-based** inside the `RankSolutions` action
(a weighted additive utility orders the feasible bookings).

## 2. States, actions and goals

Craveable contains four searches. Each is a well-defined state space.

### 2.1 Planning level (what to do next)

| | |
|---|---|
| **State** | a set of propositional fluents describing what the agent knows: `query_received`, `preferences_known`, `candidates_retrieved`, `constraints_propagated`, `solutions_enumerated`, `solutions_found` / `no_solution`, `constraints_relaxed`, `results_ranked`, `explanations_ready`, `results_presented` |
| **Initial state** | `{query_received}` |
| **Actions** | STRIPS operators with preconditions, negative preconditions, add and delete lists (`planner.OPERATORS`): `ParseQuery`, `RetrieveCandidates`, `PropagateConstraints`, `SearchSolutions`, `RelaxConstraints`, `RankSolutions`, `ExplainResults`, `PresentResults` |
| **Goal** | `{results_ranked, explanations_ready, results_presented}` |
| **Algorithm** | breadth-first forward state-space search, which returns the shortest plan (7 steps). `SearchSolutions` is planned *optimistically* (it is assumed to add `solutions_found`). After executing each step the agent observes the world; if the observed state differs from the predicted one it re-plans. With `no_solution` observed, the only applicable operator that restores `solutions_found` is `RelaxConstraints`, so the new plan becomes `RelaxConstraints -> RankSolutions -> ExplainResults -> PresentResults`. |

### 2.2 Constraint level (which bookings are acceptable)

| | |
|---|---|
| **Variables** | `R` restaurant, `T` table, `S` arrival slot |
| **Domains** | retrieved restaurants; every table of those restaurants; 30-minute slots around the requested time (just `{19:30}` for the strict query) |
| **State** (for search) | a partial assignment of the three variables plus the current domains |
| **Actions** | assign a value to the unassigned variable with the fewest remaining values (MRV), then forward-check the other domains |
| **Goal** | a complete assignment that satisfies every constraint |
| **Constraints** | unary on `R`: `cuisine`, `location`, `budget`; unary on `T`: `capacity`, `window`, `view`; binary: `belongs(R,T)`, `open(R,S)`, `available(T,S)` |
| **Inference** | node consistency removes unary violations (and records *why* each value left); AC-3 removes values with no support across a binary constraint, e.g. a restaurant whose every window table is booked at 19:30 |
| **Search** | backtracking with MRV and forward checking enumerates *all* consistent assignments, so the ranker sees the full feasible set |

### 2.3 Relaxation level (which constraint to bend when nothing fits)

| | |
|---|---|
| **State** | a `RelaxationParams` record: search radius, budget multiplier, time window, "any window view", "related cuisines" |
| **Actions** | apply one relaxation move: `time+30`, `time+60`, `view:any-window`, `budget+15%`, `radius:1.5mi`, `budget+30%`, `radius:3mi`, `cuisine:related` - each with a *regret* cost |
| **Goal** | a state whose CSP has at least one solution |
| **Algorithm** | uniform-cost search; the first goal popped is the cheapest-regret relaxation set. Example: with a $45 budget the strict CSP is empty, `time+30` and `view:any-window` (regret 1.0) still yield nothing, and `budget+15%` (regret 1.5) admits Olive & Thyme |

### 2.4 Ranking level (which feasible booking is best)

| | |
|---|---|
| **Nodes** | root -> one node per restaurant -> one leaf per consistent (table, slot) of that restaurant |
| **Path cost** | utility lost so far: `g = 1 - U` at a leaf, `0` at a restaurant node |
| **Heuristic** | `h(restaurant) = 1 - U_max(restaurant)`, where `U_max` scores the restaurant-level criteria exactly and assumes perfect seating and time. It never over-estimates the loss, so it is admissible and A* pops leaves in non-increasing utility |
| **Goal** | pop `k` leaves; a second leaf for an already-recommended restaurant is kept as an *alternative seating* instead of a new row |

## 3. Pipeline

```
            ┌────────────┐   Preferences   ┌──────────────────┐  Restaurant[]  ┌──────────────────────┐
query ─────▶│ ParseQuery │────────────────▶│RetrieveCandidates│───────────────▶│ PropagateConstraints │
            └────────────┘                 └──────────────────┘                │  node consistency    │
             rule-based NLP                  pandas CSV / Yelp                 │  AC-3                │
                                                                               └──────────┬───────────┘
                                                                                          │ pruned domains
                                   re-plan if empty  ┌──────────────────┐      ┌──────────▼───────────┐
                                 ┌──────────────────▶│ RelaxConstraints │      │   SearchSolutions    │
                                 │                   │ uniform-cost over│      │ backtracking + MRV + │
                                 │                   │ relaxation sets  │      │  forward checking    │
                                 │                   └────────┬─────────┘      └──────────┬───────────┘
                                 │                            │ Assignment[]              │ Assignment[]
                                 │                            ▼                           ▼
                                 │                   ┌──────────────────┐      ┌──────────────────────┐
                                 └───────────────────│ (monitor effect) │◀─────│    RankSolutions     │
                                                     └──────────────────┘      │  A* over assignment  │
                                                                               │  tree, utility costs │
                                                                               └──────────┬───────────┘
                                                                                          │ RankedResult[]
                                                     ┌──────────────────┐      ┌──────────▼───────────┐
                                     terminal/JSON ◀─│  PresentResults  │◀─────│    ExplainResults    │
                                                     └──────────────────┘      └──────────────────────┘
```

### 3.1 Natural-language understanding (`nlp.py`)

The default parser is deterministic slot filling: gazetteers for cuisines,
neighbourhoods and view words; regular expressions for party size ("for two
people", "party of 4"), budget ("under $65", "$40 per person" is multiplied by
the party size), weekday, clock time ("7:30 pm", "19:30", or a default for the
meal word), and seating ("near a window", "view of the garden or the street").
Every slot is logged with the text span that justified it, so the trace shows
*why* the agent believes the user wants a Thursday 19:30 table.

An optional `LLMParser` asks Claude for the same fields as structured JSON and
falls back to the rule-based parser on any error, keeping the run
reproducible when no key is present.

### 3.2 Retrieval (`datasource.py`)

`CsvSource` loads the bundled dataset with pandas into immutable `Restaurant`
and `Table` records. Retrieval is deliberately coarse (everything in the
city) so that the constraint solver, not the loader, is what narrows the
field - that is where the interesting reasoning is and where the log shows
it. `YelpSource` implements the same `search()` interface against the Yelp
Fusion API.

### 3.3 Constraint propagation and search (`constraints.py`)

Described in 2.2. Two design points worth noting:

* **Rejection memory.** Every pruned value is stored with its reason, keyed by
  restaurant, and `belongs` prunings (structural echoes) are ignored. That is
  what powers the *near misses* table: "Ephesus Bistro - table W1: already
  booked at 7:30 PM; table W2: already booked at 7:30 PM; table M1: not a
  window table".
* **Hard vs. soft.** Only user-stated requirements are hard constraints. The
  degree to which a booking satisfies them (price slack, exact time, garden vs
  street) is pushed into the utility, which is what makes relaxation safe:
  when a constraint is loosened, the utility still prefers bookings that would
  have met the original request.

### 3.4 Utility (`ranking.py`)

`U = Σ w_i · s_i` with `s_i ∈ [0,1]` and weights summing to 1:

| criterion | weight | score |
|---|---|---|
| cuisine | 0.20 | 1.0 primary match, 0.6 secondary cuisine, 0.3 related cuisine |
| rating | 0.25 | `(rating - 3) / 2`, clamped |
| price | 0.20 | `(budget - cost) / (0.5 · budget)`, clamped: full marks at half the budget, zero at the limit |
| distance | 0.10 | `1 - miles / 1.0`, clamped |
| seating | 0.10 | 1.0 window with requested view, 0.4 window with another view, 0.0 interior |
| time | 0.10 | `1 - |Δ minutes| / 60`, clamped |
| popularity | 0.05 | `log10(reviews + 1) / 3`, clamped |

For the reference query Bosphorus Table (U = 0.793) beats Anatolia Garden
Kitchen (0.747) despite a lower rating because its $54 estimate leaves far
more budget slack than Anatolia's $62; the explanation panel shows both
numbers so the user can disagree with the weighting.

### 3.5 Explanation (`explain.py`)

For each result the agent writes one sentence per criterion using the actual
numbers that the constraint tests and utility used (distance in miles,
`2 x $27 = $54`, opening hours, table zone and view), plus the utility
arithmetic and any relaxations applied. A compact checklist (`✓` met exactly,
`~` met only after relaxation) summarises the same facts in the results table.

### 3.6 Trace (`eventlog.py`)

Every stage appends events with a phase (`PLAN`, `NLP`, `RETRIEVE`, `CSP`,
`SEARCH`, `RELAX`, `RANK`, `EXPLAIN`, `ACT`), a level (`info`, `decision`,
`warn`, `plan`) and structured data. The CLI streams them live; `--log-json`
writes them out for inspection.

## 4. Mapping to the course concepts

| Concept | Technique | Code |
|---|---|---|
| Searching for solutions | breadth-first search (planner) | `planner.plan` |
| | depth-first backtracking with MRV ordering and forward checking | `BookingCSP._backtrack`, `_forward_check` |
| | A* / best-first with an admissible heuristic; branch-and-bound flavour | `search.best_first_rank`, `ranking.restaurant_upper_bound` |
| | uniform-cost search over relaxation sets | `search.relaxation_search` |
| Reasoning with constraints | CSP formulation (variables, domains, unary/binary constraints) | `BookingCSP.__init__`, `_build_constraints` |
| | node consistency | `BookingCSP.node_consistency` |
| | arc consistency (AC-3, `revise`) | `BookingCSP.ac3`, `_revise` |
| | soft constraints as utility; constraint relaxation with regret costs | `ranking.py`, `RELAXATION_MOVES` |
| Deterministic planning | STRIPS operators (pre / pre-not / add / delete) | `planner.Operator`, `OPERATORS` |
| | forward state-space planning, shortest plan | `planner.plan` |
| | execution monitoring and re-planning | `CraveableAgent.run`, `_observe` |

## 5. Extending the agent

* **New hard constraint** - add a `Constraint` in `_build_constraints` and, if
  it should be relaxable, a move in `RELAXATION_MOVES` plus a field on
  `RelaxationParams`.
* **New preference** - add a scorer in `ranking.py`, a weight in `WEIGHTS`
  (keep the sum at 1) and a sentence in `explain.py`.
* **New data source** - implement `search(prefs, log) -> list[Restaurant]`.
* **New operator** - add it to `OPERATORS` and bind it in `CraveableAgent._actions`;
  the planner will place it automatically wherever its preconditions allow.
