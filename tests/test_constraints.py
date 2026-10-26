from craveable.constraints import BookingCSP, RelaxationParams
from craveable.datasource import CsvSource, parse_hours
from craveable.nlp import RuleBasedParser
from tests.test_nlp import SCENARIO


def test_parse_hours_wraps_days():
    h = parse_hours("Mon-Thu 11:30-22:00; Fri-Sat 11:30-23:00; Sun 12:00-21:00")
    assert h["Mon"] == (690, 1320) and h["Thu"] == (690, 1320)
    assert h["Sat"] == (690, 1380) and h["Sun"] == (720, 1260)
    assert set(h) == {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}
    assert "Mon" not in parse_hours("Tue-Sun 12:00-22:00")


def test_strict_csp_solutions():
    prefs = RuleBasedParser().parse(SCENARIO)
    csp = BookingCSP(prefs, CsvSource().load_all())
    sols = csp.run()
    names = {a.restaurant.name for a in sols}
    assert names == {"Bosphorus Table", "Anatolia Garden Kitchen", "Olive & Thyme"}
    for a in sols:
        assert a.table.window and a.table.view in {"garden", "street"}
        assert a.slot == "19:30" and a.table.is_free("Thu", "19:30")
        assert a.restaurant.cost_for(2) <= 65
        assert a.restaurant.is_open_for("Thu", "19:30")
    # domains shrink monotonically through the propagation stages
    s = csp.stats
    assert s.initial_domains["R"] > s.after_node_consistency["R"] >= s.after_ac3["R"] == 3
    assert s.after_ac3["T"] == 4 and s.solutions == 4


def test_rejection_reasons_are_recorded():
    prefs = RuleBasedParser().parse(SCENARIO)
    csp = BookingCSP(prefs, CsvSource().load_all())
    csp.run()
    rej = csp.rejections
    assert any("exceeds $65" in r for r in rej["R04"]["restaurant"])        # Meze: over budget
    assert any("0.69 mi" in r for r in rej["R05"]["restaurant"])            # Istanbul: Mount Vernon
    assert any("does not fit" in r for r in rej["R03"]["restaurant"])       # Sultan's: closes 20:00
    assert "booked" in rej["R07"]["tables"]["W1"]                           # Ephesus: window tables taken
    assert "harbor" in rej["R06"]["tables"]["H1"]                           # Kebab Harbor: wrong view


def test_relaxed_params_widen_domains():
    prefs = RuleBasedParser().parse(SCENARIO)
    rows = CsvSource().load_all()
    strict = BookingCSP(prefs, rows).run()
    relaxed = BookingCSP(prefs, rows, RelaxationParams(time_window_min=30, radius_miles=1.5)).run()
    assert len(relaxed) > len(strict)
    names = {a.restaurant.name for a in relaxed}
    assert "Istanbul Ocakbasi" in names and "Ephesus Bistro" in names
    assert {a.slot for a in relaxed} <= {"19:00", "19:30", "20:00"}
