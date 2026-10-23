"""A Review that always fails runs exactly MAX_REVIEW_ITERATIONS times, then REVIEW_LIMIT."""
import pytest

from src.messages import send_back
from tests.orchestration_helpers import config, new_graph, pending, resume, stub_agents


@pytest.mark.parametrize("limit", [2, 3])
def test_failing_review_stops_at_the_bound(monkeypatch, limit):
    monkeypatch.setenv("MAX_REVIEW_ITERATIONS", str(limit))
    runs = []

    def always_fails(state):
        runs.append(state.get("review_iterations", 0))
        return {"review": {"passed": False, "issues": ["Line 6b exceeds 6a"]},
                "handoffs": [send_back("review", "computation", "Line 6b exceeds 6a")]}

    stub_agents(monkeypatch, review=always_fails)
    graph, cfg = new_graph(), config()
    graph.invoke({"session_id": "review-bound"}, cfg)

    assert len(runs) == limit
    payload = pending(graph, cfg)
    assert payload["type"] == "hitl" and payload["event"]["condition"] == "REVIEW_LIMIT"
    assert graph.get_state(cfg).values["review_iterations"] == limit


def test_continue_after_review_limit_finalizes(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    stub_agents(monkeypatch, review=lambda state: {"review": {"passed": False, "issues": ["x"]}})
    graph, cfg = new_graph(), config()
    graph.invoke({"session_id": "continue"}, cfg)
    resume(graph, cfg, "continue", note="Checked by hand")
    assert graph.get_state(cfg).values["status"] == "complete"
