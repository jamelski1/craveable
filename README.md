# Craveable

Craveable is a small, fully inspectable AI agent that turns a natural-language
dinner request into a ranked, explained set of restaurant recommendations.
It is built to demonstrate three classic AI techniques working together:

| Course concept | Where it lives | What it does |
|---|---|---|
| **Searching for solutions** | `craveable/search.py`, `constraints.py`, `planner.py` | backtracking search (MRV + forward checking) enumerates bookings; A* / best-first search ranks them with an admissible heuristic; uniform-cost search picks the cheapest constraint relaxation; BFS finds the plan |
| **Reasoning with constraints** | `craveable/constraints.py` | the booking is a CSP over *restaurant*, *table* and *time slot*; node consistency and AC-3 prune domains before search; every rejection is logged with its reason |
| **Deterministic planning** | `craveable/planner.py`, `agent.py` | STRIPS-style operators with preconditions and effects; forward state-space planning; execution monitoring with re-planning when an assumed effect fails |

The reference scenario:

> *Find a Turkish restaurant in Downtown Baltimore, MD for two people to have dinner under $65 on Thursday night at 7:30 pm with a table for two near a window with a view of the garden or the street.*

```
$ craveable
┌─ Craveable v1.0.0 - query ────────────────────────────────────────────────────┐
│ Find a Turkish restaurant in Downtown Baltimore, MD for two people to have    │
│ dinner under $65 on Thursday night at 7:30 pm with a table for two near a     │
│ window with a view of the garden or the street.                               │
└───────────────────────────────────────────────────────────────────────────────┘
[   1.4 ms] PLAN     ▶ plan found: ParseQuery -> RetrieveCandidates -> PropagateConstraints
                       -> SearchSolutions -> RankSolutions -> ExplainResults -> PresentResults
[   6.9 ms] NLP      • slot cuisine = 'Turkish'            evidence: turkish
[   8.0 ms] NLP      • slot location = 'Downtown / Baltimore / MD'
[  54.5 ms] CSP      ◆ unary constraint 'cuisine' on R: kept 10, pruned 16
[  57.7 ms] CSP      ◆ unary constraint 'location' on R: kept 7, pruned 3
                       - Istanbul Ocakbasi - Mount Vernon is 0.69 mi from the Downtown centre (> 0.6 mi)
[  59.1 ms] CSP      ◆ unary constraint 'budget' on R: kept 6, pruned 1
                       - Meze on Light Street - est. $96 for 2 exceeds $65
[  73.8 ms] CSP      ◆ AC-3 pruned R=Sultan's Grill Downtown: no S satisfies 'open'
[  75.4 ms] CSP      ◆ AC-3 pruned T=Ephesus Bistro/W1: no S satisfies 'available'
[  80.4 ms] CSP      • backtracking search found 4 consistent assignment(s)
[  85.1 ms] SEARCH   ◆ goal popped #1: Bosphorus Table / table W1 @ 19:30 with U = 0.793
...
┌───┬─────────────────────────┬──────────┬──────────────────────────┬─────────┬──────┬───────────┬─────────┬────────────────────┐
│ # │ Restaurant              │ Area     │ Table / view             │ Arrive  │ Est. │ Rating    │ Utility │ loc cui $ time view│
├───┼─────────────────────────┼──────────┼──────────────────────────┼─────────┼──────┼───────────┼─────────┼────────────────────┤
│ 1 │ Bosphorus Table         │ Downtown │ W1 · window · street     │ 7:30 PM │  $54 │ 4.6 (412) │   0.793 │  ✓   ✓  ✓   ✓   ✓  │
│ 2 │ Anatolia Garden Kitchen │ Downtown │ G1 · window · garden     │ 7:30 PM │  $62 │ 4.7 (538) │   0.747 │  ✓   ✓  ✓   ✓   ✓  │
│ 3 │ Olive & Thyme           │ Downtown │ W1 · window · street     │ 7:30 PM │  $50 │ 4.4 (377) │   0.704 │  ✓   ✓  ✓   ✓   ✓  │
└───┴─────────────────────────┴──────────┴──────────────────────────┴─────────┴──────┴───────────┴─────────┴────────────────────┘
```

Each result is followed by a criterion-by-criterion explanation (location,
cuisine, price, time/availability, view, rating) and the utility arithmetic.
The complete transcript of a run is in [`docs/sample_run.txt`](docs/sample_run.txt);
the utility breakdown chart is [`docs/utility_chart.png`](docs/utility_chart.png).

---

## Quick start (from source)

Requirements: Python 3.10+ (tested on 3.11), no API keys.

```bash
git clone https://github.com/jamelski1/craveable.git
cd craveable
python -m venv .venv
# Windows:  .venv\Scripts\activate      macOS/Linux:  source .venv/bin/activate
pip install -e ".[dev]"

craveable                      # run the reference scenario
craveable "Italian dinner for 4 in Fells Point on Saturday at 8 pm under $150 with a harbor view"
python -m craveable --help     # same thing without the console script
```

Useful flags:

| Flag | Effect |
|---|---|
| `--top N` | number of recommendations (default 5) |
| `--json out/report.json` | full report (preferences, plan, results, explanations, near misses, statistics) |
| `--log-json out/log.json` | the complete event log as JSON |
| `--chart out/utility.png` | stacked bar chart of the utility breakdown (needs matplotlib) |
| `--quiet` / `--verbose` | hide the live reasoning log / include debug events |
| `--delay 0.05` | pause after each log event, for screen recordings |
| `--no-relax` | fail instead of relaxing constraints when nothing satisfies the query |
| `--nlp llm` | parse the query with Claude instead of the rule-based parser (needs `ANTHROPIC_API_KEY`) |
| `--source yelp` | retrieve live businesses from Yelp Fusion (needs `YELP_API_KEY`); table data is unavailable there |
| `--data DIR` | use your own `restaurants.csv` + `tables.csv` |

## Building the stand-alone binary

The build script creates a virtual environment, installs the package, runs
the tests and packages a single-file executable with PyInstaller.

```powershell
# Windows (PowerShell)
.\scripts\build.ps1            # -> dist\craveable.exe
.\dist\craveable.exe --version
.\dist\craveable.exe
```

```bash
# macOS / Linux
./scripts/build.sh             # -> dist/craveable
./dist/craveable
```

The binary (about 33 MB) bundles the dataset. It leaves out matplotlib to stay
small, so `--chart` is a source-install feature. A one-file PyInstaller
executable unpacks itself on launch, so expect a few seconds of start-up
before the log begins; the agent's own reasoning takes under 100 ms.

## Running the demo

`scripts/demo.ps1` (or `demo.sh`) runs the reference scenario with a slowed
log, then a deliberately infeasible variant (budget $45) that forces the agent
to re-plan and relax a constraint, writing JSON, logs and a chart to `out/`.

## Tests

```bash
python -m pytest -q
```

Covers the parser, hours parsing, CSP propagation and rejection reasons,
heuristic admissibility, A* ordering against brute force, planner output and
re-planning, relaxation search, and the end-to-end agent and CLI.

## How it works

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full write-up:
state/action/goal definitions, the agent type (goal-based planner wrapping a
utility-based ranker), each pipeline stage, the utility function, and how the
three course concepts map onto the code.

In one paragraph: the agent plans a sequence of operators with a BFS forward
planner, then executes them. `ParseQuery` fills a goal specification from the
text; `RetrieveCandidates` loads restaurants and tables; `PropagateConstraints`
builds a CSP over (restaurant, table, slot) and runs node consistency and
AC-3; `SearchSolutions` enumerates consistent bookings with backtracking
search; `RankSolutions` runs A* over the assignment tree with utility-based
costs; `ExplainResults` writes a sentence per criterion; `PresentResults`
renders tables. If `SearchSolutions` finds nothing, execution monitoring
detects the failed effect and the planner inserts `RelaxConstraints`, a
uniform-cost search over relaxation sets that bends the cheapest constraint.

## Dataset

`craveable/data/restaurants.csv` and `tables.csv` hold a **synthetic** set of
26 Baltimore-area restaurants and 53 tables. Names, prices, hours, table
layouts and bookings are fictional and were designed so the scenario exercises
every constraint: a Turkish place that closes too early, one over budget, one
just outside Downtown, one whose window tables face the harbor, and one whose
window tables are already booked at 7:30. Coordinates are real Baltimore
locations so distances are meaningful.

## Project layout

```
craveable/
  agent.py        plan -> execute -> monitor -> re-plan loop; binds operators to actions
  planner.py      STRIPS operators, BFS forward planner
  nlp.py          rule-based parser (+ optional Claude parser)
  datasource.py   CSV loader (pandas) and Yelp Fusion adapter
  constraints.py  BookingCSP: node consistency, AC-3, backtracking with MRV + forward checking
  search.py       A* ranking, uniform-cost relaxation search
  ranking.py      weighted additive utility and admissible heuristic
  explain.py      per-criterion explanations and checklist
  eventlog.py     structured trace of events and decisions
  cli.py          rich terminal output, JSON/chart export
  viz.py          utility breakdown chart
  data/           synthetic dataset
tests/            pytest suite
scripts/          build.ps1 / build.sh / demo.ps1 / demo.sh
docs/             ARCHITECTURE.md, sample_run.txt, utility_chart.png
```

## Limitations

* Prices are average spend per person times party size, before tax and tip.
* "Downtown" means the Downtown / Inner Harbor neighbourhoods or anything
  within 0.6 miles of Charles Center; the relaxation search can widen that.
* The CSV dataset is synthetic. The Yelp adapter fetches real businesses but
  Yelp exposes no table or view data, so seating constraints become soft there.
