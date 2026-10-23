"""Two agents passing work back and forth with no progress trip the loop guard, and the run stops."""
from src.agents import orchestrator as orch
from tests.orchestration_helpers import config, new_graph, pending, ping_pong, resume, stub_agents


def test_ping_pong_trips_loop_guard_and_stop_ends_the_run(monkeypatch):
    calls = {"eligibility": 0, "computation": 0}

    def counted(name, inner):
        def run(state):
            calls[name] += 1
            return inner(state)
        return run

    stub_agents(monkeypatch,
                eligibility=counted("eligibility", ping_pong("eligibility", "computation", {"rules": {"in_scope": True}})),
                computation=counted("computation", ping_pong("computation", "eligibility")))
    graph, cfg = new_graph(), config()
    graph.invoke({"session_id": "loop"}, cfg)

    payload = pending(graph, cfg)
    assert payload["type"] == "hitl" and payload["event"]["condition"] == "LOOP_GUARD"
    assert payload["event"]["details"]["pattern"] in (["eligibility", "computation"], ["computation", "eligibility"])
    assert calls["eligibility"] + calls["computation"] <= 2 * orch.LOOP_REPEATS + 1

    resume(graph, cfg, "stop")
    values = graph.get_state(cfg).values
    assert values["status"] == "escalated"
    assert pending(graph, cfg) is None and not graph.get_state(cfg).next


def test_hop_budget_trips_without_a_repeating_pattern(monkeypatch):
    monkeypatch.setenv("MAX_HOPS", "4")
    trail = [[agent, f"h{i}"] for i, agent in enumerate(["eligibility", "computation", "review", "eligibility",
                                                         "computation"])]
    loop = orch.detect_loop(trail, "h9")
    assert loop and loop["details"]["hops"] == 5


def test_intake_hops_do_not_count_toward_the_budget(monkeypatch):
    monkeypatch.setenv("MAX_HOPS", "4")
    trail = [["intake", f"h{i}"] for i in range(20)]
    assert orch.detect_loop(trail, "h99") is None


def test_repeats_with_progress_are_not_a_loop():
    trail = [[agent, f"h{i}"] for i, agent in enumerate(["computation", "review"] * 3)]
    assert orch.detect_loop(trail, "h6") is None
