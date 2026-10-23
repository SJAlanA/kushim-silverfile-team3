"""The orchestrator picks the next node from state alone (conditional routing)."""
import pytest

from src.agents import orchestrator as orch
from src.messages import send_back
from tests.orchestration_helpers import answer

DOC = {"doc_id": "int_1", "doc_type": "1099-INT", "path": "data/fixtures/1099int_1.json"}
RECORD = {"doc_id": "int_1", "doc_type": "1099-INT", "fields": {"box1_interest": 300},
          "extractor": "json", "confidence": 1.0, "source_path": DOC["path"]}
LINE = {"1z": {"line": "1z", "value": 300, "provenance": {"at": "t1"}}}


def event(condition="UNCERTAIN", status="open", **extra) -> dict:
    return {"event_id": f"{condition.lower()}-1", "condition": condition, "reason": "r", "raised_by": "test",
            "status": status, "resolution": None, **extra}


def base(**overrides) -> dict:
    """Intake finished with one document read."""
    state = {"session_id": "s", "intake": {"filing_status": answer("single")}, "intake_complete": True,
             "documents": [DOC], "extracted": {"int_1": RECORD}}
    state.update(overrides)
    return state


def seen(state: dict, *agents: str) -> dict:
    """Mark `agents` as having run on the state's current inputs."""
    state["routing"] = {"inputs_seen": {agent: orch.INPUTS[agent](state) for agent in agents},
                        "handoff_cursor": len(state.get("handoffs", [])), "trail": []}
    return state


def ruled(**overrides):
    return seen(base(rules={"in_scope": True}, **overrides), "eligibility")


def computed(**overrides):
    return seen(base(rules={"in_scope": True}, lines=LINE, **overrides), "eligibility", "computation")


def reviewed(passed: bool, iterations: int = 1, **overrides):
    state = computed(review={"passed": passed, "issues": [] if passed else ["6b > 6a"]},
                     review_iterations=iterations, **overrides)
    return seen(state, "eligibility", "computation", "review")


CASES = [
    ("empty state starts intake", {"session_id": "s"}, "intake"),
    ("an open event goes to a human first", base(hitl=[event()], intake_complete=False), "human_review"),
    ("a rerun beats unfinished intake", base(intake_complete=False, rerun=["computation"]), "computation"),
    ("unfinished intake", base(intake_complete=False), "intake"),
    ("an unread document is extracted", base(documents=[DOC, {**DOC, "doc_id": "div_1"}]), "extract_dispatch"),
    ("a failed read is not retried without a send_back", base(extracted={"int_1": {**RECORD, "extractor": "failed"}}),
     "eligibility"),
    ("read documents go to eligibility", base(), "eligibility"),
    ("a changed answer makes the rules stale",
     {**ruled(), "intake": {"filing_status": answer("mfj")}}, "eligibility"),
    ("rules then computation", ruled(), "computation"),
    ("new rules make the lines stale", {**computed(), "rules": {"in_scope": True, "boxes": 1}}, "computation"),
    ("lines then review", computed(), "review"),
    ("a failed review runs again under the bound", reviewed(False, iterations=2), "review"),
    ("a passed review finalizes", reviewed(True), "finalize"),
    ("a passed review is stale once the lines change", {**reviewed(True), "lines": {**LINE, "9": {"value": 1}}},
     "review"),
    ("a resolved event does not block routing", {**ruled(), "hitl": [event(status="resolved")]}, "computation"),
]


@pytest.mark.parametrize("name, state, expected", CASES, ids=[case[0] for case in CASES])
def test_next_node(name, state, expected):
    assert orch.run(state)["next_agent"] == expected


def test_review_limit_raises_an_event_at_the_bound(monkeypatch):
    monkeypatch.setenv("MAX_REVIEW_ITERATIONS", "3")
    update = orch.run(reviewed(False, iterations=3))
    assert update["next_agent"] == "human_review"
    assert [e["condition"] for e in update["hitl"]] == ["REVIEW_LIMIT"]


def test_review_limit_resolved_with_continue_finalizes():
    state = reviewed(False, iterations=3)
    fingerprint = orch.review_inputs(state)
    state["hitl"] = [event("REVIEW_LIMIT", status="resolved", details={"fingerprint": fingerprint},
                           resolution={"decision": "continue"})]
    assert orch.run(state)["next_agent"] == "finalize"


def test_out_of_scope_rules_raise_an_event_once():
    state = ruled(rules={"in_scope": False})
    seen(state, "eligibility")
    update = orch.run(state)
    assert update["next_agent"] == "human_review"
    assert update["hitl"][0]["condition"] == "OUT_OF_SCOPE"
    state["hitl"] = [{**update["hitl"][0], "status": "resolved", "resolution": {"decision": "continue"}}]
    assert orch.run(state)["next_agent"] == "computation"


def test_missing_rules_after_eligibility_ran_asks_a_human():
    state = seen(base(), "eligibility")
    update = orch.run(state)
    assert update["next_agent"] == "human_review"
    assert update["hitl"][0]["condition"] == "UNCERTAIN"


def test_new_send_back_message_reruns_its_target_once():
    state = computed(handoffs=[send_back("review", "computation", "Line 6b exceeds 6a")])
    state["routing"]["handoff_cursor"] = 0
    update = orch.run(state)
    assert update["next_agent"] == "computation" and update["rerun"] == []
    state.update(routing=update["routing"], rerun=update["rerun"])
    assert orch.run(state)["next_agent"] == "review"  # the message was consumed


def test_send_back_to_an_extraction_node_reruns_the_dispatch():
    assert orch.new_send_backs([send_back("extract_aggregate", "extract_one", "Blurry")], 0) == ["extract_dispatch"]


def test_dispatch_records_inputs_and_status():
    update = orch.run(base())
    assert update["status"] == "rules"
    assert update["route_history"] == ["eligibility"]
    assert update["routing"]["inputs_seen"]["eligibility"] == orch.eligibility_inputs(base())
