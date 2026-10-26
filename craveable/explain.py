"""Turn a ranked assignment into criterion-by-criterion explanations."""

from __future__ import annotations

from typing import Dict, List

from .geo import canonical_area, distance_to_area, in_area, pretty_area
from .models import Assignment, Preferences, Relaxation, clock12, hhmm, minutes
from .constraints import MEAL_DURATION_MIN


def explain(a: Assignment, prefs: Preferences, raw: Dict[str, float],
            relaxations: List[Relaxation]) -> Dict[str, str]:
    r, t = a.restaurant, a.table
    out: Dict[str, str] = {}
    relaxed = {x.name for x in relaxations}

    # location -----------------------------------------------------------
    if prefs.area:
        area = canonical_area(prefs.area)
        if area:
            inside, d, why = in_area(area, r.neighborhood, r.lat, r.lon)
            out["location"] = (
                f"{r.name} is at {r.address} in {r.neighborhood}, {d:.2f} mi from the "
                f"{pretty_area(area)} centre"
                + (f" - inside the requested area ({why})." if inside
                   else " - outside the strict area; included because the search radius was widened.")
            )
        else:
            out["location"] = f"{r.name} is in {r.neighborhood} ({prefs.area} could not be geocoded, so no distance test)."
    else:
        out["location"] = f"{r.name} is in {r.neighborhood}."

    # cuisine ------------------------------------------------------------
    if prefs.cuisine:
        how = r.serves(prefs.cuisine)
        if how == "primary":
            out["cuisine"] = f"Primary cuisine is {r.cuisine} - an exact match for '{prefs.cuisine}'."
        elif how == "secondary":
            out["cuisine"] = (f"Primary cuisine is {r.cuisine}; {prefs.cuisine} is listed as a secondary cuisine "
                              f"({r.notes.lower()}), so it scores 0.6 instead of 1.0.")
        else:
            out["cuisine"] = f"Cuisine is {r.cuisine}, a related cuisine accepted because the query was relaxed."
    else:
        out["cuisine"] = f"Cuisine: {r.cuisine} (no cuisine was requested)."

    # price --------------------------------------------------------------
    cost = r.cost_for(prefs.party_size)
    if prefs.budget_total:
        slack = prefs.budget_total - cost
        verdict = "within" if slack >= 0 else "over"
        out["price"] = (
            f"Estimated dinner for {prefs.party_size}: {prefs.party_size} x ${r.avg_dinner_pp:.0f} = ${cost:.0f} "
            f"({r.price_tier}), {verdict} the ${prefs.budget_total:.0f} budget by ${abs(slack):.0f} "
            f"(before tax and tip)."
        )
    else:
        out["price"] = f"Estimated dinner for {prefs.party_size}: ${cost:.0f} ({r.price_tier})."

    # time / availability -----------------------------------------------
    if prefs.day and prefs.time:
        win = r.hours.get(prefs.day)
        hours_txt = f"open {hhmm(win[0])}-{hhmm(win[1])} on {prefs.day}" if win else f"closed {prefs.day}"
        delta = minutes(a.slot) - minutes(prefs.time)
        when = (f"at the requested {clock12(a.slot)}" if delta == 0
                else f"at {clock12(a.slot)} ({'+' if delta > 0 else ''}{delta} min from the requested {clock12(prefs.time)})")
        out["time"] = (f"{r.name} is {hours_txt}; table {t.table_id} is free {when} and a "
                       f"{MEAL_DURATION_MIN}-minute dinner fits before closing.")
    else:
        out["time"] = f"Slot {clock12(a.slot)} is available."

    # view / seating -----------------------------------------------------
    if prefs.window_seat or prefs.views:
        wanted = " or ".join(sorted(prefs.views)) if prefs.views else "any"
        if t.view in prefs.views:
            out["view"] = (f"Table {t.table_id} ({t.zone}) seats {t.seats}, is a window table with a {t.view} view - "
                           f"matches the request for a window seat with a view of the {wanted}.")
        elif t.window:
            out["view"] = (f"Table {t.table_id} ({t.zone}) seats {t.seats} and is a window table, but its view is "
                           f"'{t.view}' rather than the requested {wanted}; accepted only because the view constraint was relaxed.")
        else:
            out["view"] = f"Table {t.table_id} is not a window table."
    else:
        out["view"] = f"Table {t.table_id} ({t.zone}) seats {t.seats}."

    # quality ------------------------------------------------------------
    out["rating"] = f"Rated {r.rating:.1f}/5 from {r.review_count} reviews."

    if relaxed:
        out["relaxed"] = "Constraints relaxed to find this: " + "; ".join(x.description for x in relaxations) + "."
    return out


def criteria_checklist(a: Assignment, prefs: Preferences) -> Dict[str, bool]:
    """Boolean verdict per criterion, for the summary table."""
    r, t = a.restaurant, a.table
    checks: Dict[str, bool] = {}
    if prefs.area and canonical_area(prefs.area):
        checks["location"] = in_area(canonical_area(prefs.area), r.neighborhood, r.lat, r.lon)[0]
    if prefs.cuisine:
        checks["cuisine"] = r.serves(prefs.cuisine) != "no"
    if prefs.budget_total:
        checks["price"] = r.cost_for(prefs.party_size) <= prefs.budget_total
    if prefs.time:
        checks["time"] = a.slot == prefs.time
    if prefs.window_seat or prefs.views:
        checks["view"] = t.window and (not prefs.views or t.view in prefs.views)
    return checks
