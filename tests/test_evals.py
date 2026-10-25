"""Regression run of every case in evals/cases.json: the expected HITL conditions must fire, in order.

Each case is a base case file plus overrides (see the file's own description). The run mirrors
`run.py --case`: intake questions are answered from the case, and HITL events are resolved from
its `hitl_resolutions`.
"""
import copy
import json
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from run import match_resolution
from src.agents.intake import case_reply
from src.form import render
from src.graph import build_graph

ROOT = Path(__file__).resolve().parent.parent
CASES = json.loads((ROOT / "evals" / "cases.json").read_text())["cases"]
MAX_STEPS = 200


def load_case(entry: dict) -> dict:
    """The full case file for an eval entry: base case, answer overrides and extra documents."""
    case = copy.deepcopy(json.loads((ROOT / entry["base"]).read_text()))
    case["answers"].update(entry.get("answers", {}))
    case["documents"] = case["documents"] + entry.get("add_documents", [])
    case["hitl_resolutions"] = entry.get("hitl_resolutions", case.get("hitl_resolutions", []))
    return case


def run_case(case: dict, session_id: str) -> dict:
    """Replay a case on a fresh in-memory graph and return the final state."""
    graph, config = build_graph(InMemorySaver()), {"configurable": {"thread_id": session_id}}
    value: object = {"session_id": session_id}
    used: list[dict] = []
    for _ in range(MAX_STEPS):
        graph.invoke(value, config)
        snapshot = graph.get_state(config)
        payload = next((i.value for task in snapshot.tasks for i in task.interrupts), None)
        if payload is None:
            return snapshot.values
        if payload["type"] == "intake_question":
            value = Command(resume=case_reply(case, payload))
            continue
        event = payload["event"]
        resolution = match_resolution(event, case.get("hitl_resolutions", []), used)
        assert resolution is not None, f"no resolution in the case for {event['condition']}"
        value = Command(resume={**resolution, "event_id": event["event_id"]})
    raise AssertionError("the case did not finish within the step limit")


@pytest.fixture(autouse=True)
def isolated_run(monkeypatch, tmp_path):
    monkeypatch.chdir(ROOT)  # document paths in cases are relative to the repo root
    monkeypatch.setattr(render, "OUTPUT_DIR", tmp_path / "outputs")
    monkeypatch.setenv("SILVERFILE_TRACE_DIR", str(tmp_path / "traces"))


@pytest.mark.parametrize("entry", CASES, ids=[entry["id"] for entry in CASES])
def test_expected_conditions_fire(entry):
    state = run_case(load_case(entry), f"eval-{entry['id']}")
    assert [event["condition"] for event in state["hitl"]] == entry["expected"]["hitl_conditions"]
    assert all(event["status"] == "resolved" for event in state["hitl"])
    assert state["status"] == entry["expected"]["status"]


def test_every_condition_a_person_must_handle_is_covered():
    """The brief's six escalation conditions (4.4) each have at least one case."""
    covered = {c for entry in CASES for c in entry["expected"]["hitl_conditions"]}
    assert {"OUT_OF_SCOPE", "DOC_CONFLICT", "R1099_TAXABLE_NOT_DETERMINED", "TAX_ADVICE",
            "FILING_STATUS_AMBIGUITY", "EXPLOITATION"} <= covered
