import json

import pytest

from craveable.agent import CraveableAgent
from craveable.cli import main
from tests.test_nlp import SCENARIO


def test_end_to_end_scenario():
    agent = CraveableAgent(top_k=5)
    report = agent.run(SCENARIO)
    assert report.plan == ["ParseQuery", "RetrieveCandidates", "PropagateConstraints", "SearchSolutions",
                           "RankSolutions", "ExplainResults", "PresentResults"]
    assert not report.replanned and report.relaxations == []
    names = [r.assignment.restaurant.name for r in report.results]
    assert names == ["Bosphorus Table", "Anatolia Garden Kitchen", "Olive & Thyme"]
    top = report.results[0]
    assert top.assignment.table.table_id == "W1" and top.assignment.slot == "19:30"
    for crit in ("location", "cuisine", "price", "time", "view", "rating"):
        assert crit in top.explanations and top.explanations[crit]
    assert "within the $65 budget" in top.explanations["price"]
    assert "Downtown" in top.explanations["location"]
    assert "window table with a street view" in top.explanations["view"]
    assert "Ephesus Bistro" in report.near_misses and "Meze on Light Street" in report.near_misses
    assert report.log.counts()["CSP"] > 0 and report.log.counts()["PLAN"] > 0
    json.dumps(report.to_dict())   # serialisable


def test_end_to_end_replans_when_infeasible():
    report = CraveableAgent().run(SCENARIO.replace("$65", "$45"))
    assert report.replanned
    assert "RelaxConstraints" in report.plan
    assert [r.name for r in report.relaxations] == ["budget+15%"]
    assert report.results[0].assignment.restaurant.name == "Olive & Thyme"
    assert "relaxed" in report.results[0].explanations


def test_no_relax_raises():
    with pytest.raises(RuntimeError):
        CraveableAgent(allow_relaxation=False).run(SCENARIO.replace("$65", "$45"))


def test_cli_writes_json(tmp_path):
    out = tmp_path / "report.json"
    code = main([SCENARIO, "--quiet", "--no-color", "--json", str(out), "--log-json", str(tmp_path / "log.json")])
    assert code == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["results"][0]["restaurant"] == "Bosphorus Table"
    assert data["preferences"]["cuisine"] == "Turkish"
    assert (tmp_path / "log.json").exists()
