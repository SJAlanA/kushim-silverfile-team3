"""Three documents fan out to parallel extract_one branches and all three records survive the join."""
from pathlib import Path

from src.agents import extraction
from tests.orchestration_helpers import config, finished_intake, new_graph, stub_agents

ROOT = Path(__file__).resolve().parent.parent
DOCS = [{"doc_id": "int_1", "doc_type": "1099-INT", "path": "data/fixtures/1099int_1.json"},
        {"doc_id": "div_1", "doc_type": "1099-DIV", "path": "data/fixtures/1099div_1.json"},
        {"doc_id": "ssa_1", "doc_type": "SSA-1099", "path": "data/fixtures/ssa1099_1.json"}]


def test_three_documents_fan_out_and_join(monkeypatch, tmp_path):
    monkeypatch.chdir(ROOT)
    calls = []
    real_run_one = extraction.run_one
    monkeypatch.setattr(extraction, "run_one", lambda payload: calls.append(payload["doc"]["doc_id"]) or
                        real_run_one(payload))
    stub_agents(monkeypatch, intake=finished_intake(DOCS))
    monkeypatch.setattr("src.form.render.OUTPUT_DIR", tmp_path)
    graph, cfg = new_graph(), config()

    steps = [list(step) for step in graph.stream({"session_id": "fan-out"}, cfg, stream_mode="updates")]
    values = graph.get_state(cfg).values

    assert sorted(calls) == sorted(doc["doc_id"] for doc in DOCS)
    assert sorted(values["extracted"]) == sorted(doc["doc_id"] for doc in DOCS)
    assert all(record["extractor"] == "json" for record in values["extracted"].values())
    assert sum(step == ["extract_aggregate"] for step in steps) == 1  # one join for all branches
    assert values["status"] == "complete"


def test_nothing_to_read_goes_straight_to_the_join():
    from src.graph import fan_out

    assert fan_out({"documents": []}) == "extract_aggregate"
