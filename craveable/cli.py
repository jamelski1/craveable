"""Command-line interface for the Craveable agent."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table as RichTable
from rich.text import Text

from . import __version__
from .agent import AgentReport, CraveableAgent
from .eventlog import Event, EventLog
from .explain import criteria_checklist
from .models import clock12

DEFAULT_QUERY = (
    "Find a Turkish restaurant in Downtown Baltimore, MD for two people to have dinner "
    "under $65 on Thursday night at 7:30 pm with a table for two near a window with a view "
    "of the garden or the street."
)

PHASE_STYLE = {
    "PLAN": "bold magenta", "NLP": "bold cyan", "RETRIEVE": "bold blue", "CSP": "bold yellow",
    "SEARCH": "bold green", "RELAX": "bold red", "RANK": "bold green", "EXPLAIN": "bold cyan", "ACT": "bold magenta",
}
LEVEL_GLYPH = {"debug": "·", "info": "•", "decision": "◆", "warn": "!", "plan": "▶"}


def make_sink(console: Console, delay: float = 0.0):
    def sink(ev: Event) -> None:
        line = Text()
        line.append(f"[{ev.t_ms:8.1f} ms] ", style="dim")
        line.append(f"{ev.phase:<8}", style=PHASE_STYLE.get(ev.phase, "bold"))
        glyph = LEVEL_GLYPH.get(ev.level, "•")
        style = {"decision": "bold", "warn": "bold red", "plan": "magenta"}.get(ev.level, "")
        line.append(f" {glyph} {ev.message}", style=style)
        console.print(line, highlight=False)
        for k, v in ev.data.items():
            if isinstance(v, (list, tuple)):
                if not v:
                    continue
                console.print(f"{'':16}  {k}:", style="dim", highlight=False)
                for item in v[:12]:
                    console.print(f"{'':20}- {item}", style="dim", highlight=False)
                if len(v) > 12:
                    console.print(f"{'':20}… {len(v) - 12} more", style="dim", highlight=False)
            elif isinstance(v, dict):
                console.print(f"{'':16}  {k}: " + ", ".join(f"{a}={b}" for a, b in v.items()), style="dim", highlight=False)
            else:
                console.print(f"{'':16}  {k}: {v}", style="dim", highlight=False)
        if delay:
            time.sleep(delay)
    return sink


def render_report(console: Console, report: AgentReport, show_near_misses: bool = True) -> None:
    prefs = report.preferences
    console.rule("[bold]Craveable - goal specification")
    t = RichTable(box=box.SIMPLE, show_header=False, pad_edge=False)
    t.add_column("field", style="bold")
    t.add_column("value")
    for k, v in prefs.describe().items():
        t.add_row(k, v)
    console.print(t)

    console.rule("[bold]Plan executed")
    plan_txt = " -> ".join(report.plan)
    console.print(plan_txt + ("   (re-planned after SearchSolutions found nothing)" if report.replanned else ""))
    if report.relaxations:
        console.print("[bold red]Relaxations applied:[/] " + "; ".join(r.description for r in report.relaxations))

    console.rule("[bold]Ranked results")
    if not report.results:
        console.print("[red]No recommendation could be produced.[/]")
        return
    rt = RichTable(box=box.ROUNDED, header_style="bold")
    rt.add_column("#", justify="right")
    rt.add_column("Restaurant")
    rt.add_column("Area")
    rt.add_column("Table / view")
    rt.add_column("Arrive")
    rt.add_column("Est. for party", justify="right")
    rt.add_column("Rating", justify="right")
    rt.add_column("Utility", justify="right")
    rt.add_column("loc  cui  $  time view", justify="center")
    for r in report.results:
        a = r.assignment
        checks = criteria_checklist(a, prefs)
        marks = "   ".join(("[green]✓[/]" if checks.get(k) else "[yellow]~[/]") if k in checks else "[dim]-[/]"
                           for k in ("location", "cuisine", "price", "time", "view"))
        rt.add_row(
            str(r.rank), a.restaurant.name, a.restaurant.neighborhood,
            f"{a.table.table_id} · {'window' if a.table.window else 'interior'} · {a.table.view} view",
            clock12(a.slot),
            f"${a.restaurant.cost_for(prefs.party_size):.0f}",
            f"{a.restaurant.rating:.1f} ({a.restaurant.review_count})",
            f"{r.utility:.3f}", marks,
        )
    console.print(rt)
    console.print("[dim]✓ criterion met exactly   ~ met only after relaxation   - not requested[/]")

    console.rule("[bold]Why these match")
    for r in report.results:
        a = r.assignment
        body = Text()
        for crit, sentence in r.explanations.items():
            body.append(f"{crit:<9}", style="bold")
            body.append(f"{sentence}\n")
        if r.alternatives:
            body.append("also     ", style="bold")
            body.append("other consistent seatings here: " + "; ".join(
                f"table {x.table.table_id} ({x.table.view} view) at {clock12(x.slot)}" for x in r.alternatives) + "\n")
        body.append("utility  ", style="bold")
        body.append(" + ".join(f"{k} {r.components[k]:.3f}" for k in r.components) + f" = {r.utility:.3f}", style="dim")
        console.print(Panel(body, title=f"#{r.rank}  {a.restaurant.name}  -  table {a.table.table_id} at {clock12(a.slot)}",
                            title_align="left", border_style="green" if r.rank == 1 else "white"))

    if show_near_misses and report.near_misses:
        console.rule("[bold]Near misses (same cuisine, rejected)")
        nm = RichTable(box=box.SIMPLE, header_style="bold")
        nm.add_column("Restaurant")
        nm.add_column("Why it was pruned")
        for name, reasons in report.near_misses.items():
            nm.add_row(name, "\n".join(reasons[:3]))
        console.print(nm)

    cs, ss = report.csp_stats, report.search_stats
    console.rule("[bold]Reasoning statistics")
    if cs:
        console.print(f"CSP domains |R|,|T|,|S|: {cs.get('initial_domains')} -> node consistency "
                      f"{cs.get('after_node_consistency')} -> AC-3 {cs.get('after_ac3')}; "
                      f"backtracking visited {cs.get('nodes_visited')} nodes, {cs.get('backtracks')} dead ends, "
                      f"{cs.get('solutions')} solutions")
    if ss:
        console.print(f"A* ranking: {ss.get('nodes_generated')} nodes generated, {ss.get('nodes_expanded')} expanded, "
                      f"{ss.get('goals_popped')} goals popped out of {ss.get('leaves_total')} leaves")
    console.print(f"Events logged: {sum(report.log.counts().values())} "
                  + " ".join(f"{k}={v}" for k, v in report.log.counts().items()), style="dim")


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="craveable",
        description="Craveable - an AI agent that finds restaurants under natural-language constraints.",
    )
    ap.add_argument("query", nargs="?", default=DEFAULT_QUERY, help="natural-language request (default: the Turkish/Downtown Baltimore scenario)")
    ap.add_argument("--top", type=int, default=5, help="number of recommendations (default 5)")
    ap.add_argument("--nlp", choices=["rules", "llm"], default="rules", help="query parser (default rules; llm needs ANTHROPIC_API_KEY)")
    ap.add_argument("--source", choices=["csv", "yelp"], default="csv", help="retrieval source (default csv; yelp needs YELP_API_KEY)")
    ap.add_argument("--data", type=Path, default=None, help="directory containing restaurants.csv and tables.csv")
    ap.add_argument("--no-relax", action="store_true", help="fail instead of relaxing constraints when nothing matches")
    ap.add_argument("--json", type=Path, default=None, help="write the full report as JSON to this path")
    ap.add_argument("--log-json", type=Path, default=None, help="write the event log as JSON to this path")
    ap.add_argument("--chart", type=Path, default=None, help="save a utility-breakdown chart (PNG) to this path")
    ap.add_argument("--quiet", action="store_true", help="suppress the live reasoning log")
    ap.add_argument("--verbose", action="store_true", help="include debug-level events in the live log")
    ap.add_argument("--delay", type=float, default=0.0, help="seconds to pause after each log event (for demos)")
    ap.add_argument("--no-color", action="store_true", help="plain text output")
    ap.add_argument("--width", type=int, default=None, help="force console width")
    ap.add_argument("--version", action="version", version=f"craveable {__version__}")
    return ap


def _utf8_streams() -> None:
    """Windows consoles and frozen executables often default to cp1252, which
    cannot encode the box-drawing and check-mark glyphs used in the report."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # pragma: no cover - exotic streams
                pass


def main(argv: Optional[list] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    _utf8_streams()
    piped = not sys.stdout.isatty()
    console = Console(
        no_color=args.no_color,
        width=args.width,
        force_terminal=not args.no_color or None,
        # Use the ANSI renderer (Unicode boxes) when output is piped or uncoloured;
        # rich's legacy Win32 renderer is only useful for an interactive old console.
        legacy_windows=False if (piped or args.no_color) else None,
    )

    console.print(Panel(Text(args.query, style="italic"), title=f"[bold]Craveable v{__version__}[/] - query", title_align="left"))
    if not args.quiet:
        console.rule("[bold]Agent reasoning log")
    log = EventLog(sink=None if args.quiet else make_sink(console, args.delay),
                   min_level="debug" if args.verbose else "info")
    agent = CraveableAgent(nlp=args.nlp, source=args.source, data_dir=args.data, top_k=args.top,
                           allow_relaxation=not args.no_relax, log=log)
    try:
        report = agent.run(args.query)
    except RuntimeError as e:
        console.print(f"[bold red]Agent stopped:[/] {e}")
        return 2

    console.print()
    render_report(console, report)

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
        console.print(f"[dim]report written to {args.json}[/]")
    if args.log_json:
        args.log_json.parent.mkdir(parents=True, exist_ok=True)
        args.log_json.write_text(log.to_json(), encoding="utf-8")
        console.print(f"[dim]event log written to {args.log_json}[/]")
    if args.chart:
        try:
            from .viz import save_utility_chart
            save_utility_chart(report.results, args.chart)
            console.print(f"[dim]chart written to {args.chart}[/]")
        except ImportError:
            console.print("[yellow]matplotlib is not installed; skipping chart (pip install matplotlib)[/]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
