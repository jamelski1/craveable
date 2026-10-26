from craveable.nlp import RuleBasedParser

SCENARIO = (
    "Find a Turkish restaurant in Downtown Baltimore, MD for two people to have dinner under $65 "
    "on Thursday night at 7:30 pm with a table for two near a window with a view of the garden or the street."
)


def test_scenario_slots():
    p = RuleBasedParser().parse(SCENARIO)
    assert p.cuisine == "Turkish"
    assert p.area == "Downtown"
    assert p.city == "Baltimore"
    assert p.state == "MD"
    assert p.party_size == 2
    assert p.budget_total == 65.0
    assert p.day == "Thu"
    assert p.time == "19:30"
    assert p.meal == "dinner"
    assert p.window_seat is True
    assert p.views == {"garden", "street"}
    fields = {f for f, _, _ in p.extraction_trace}
    assert {"cuisine", "location", "party_size", "budget_total", "day", "time", "views"} <= fields


def test_variants():
    p = RuleBasedParser().parse("Italian lunch for 4 in Fells Point tomorrow around $120, patio with a harbor view")
    assert p.cuisine == "Italian" and p.party_size == 4 and p.area == "Fells Point"
    assert p.budget_total == 120.0 and p.meal == "lunch" and p.time == "12:30"
    assert "patio" in p.seating_notes and p.views == {"harbor"}


def test_per_person_budget_and_24h_time():
    p = RuleBasedParser().parse("sushi for two at 19:00 on Friday, under $40 per person")
    assert p.cuisine == "Japanese" and p.time == "19:00" and p.day == "Fri"
    assert p.budget_total == 80.0


def test_compact_phrasing():
    p = RuleBasedParser().parse("Turkish dinner for two in Downtown Baltimore under $45 on Thursday at 7:30 pm, window table with a garden or street view")
    assert p.window_seat and p.views == {"garden", "street"} and p.budget_total == 45.0
