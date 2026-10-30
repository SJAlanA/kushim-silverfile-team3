"""Grader's dashboard: fill the taxpayer page from an eval case, then do the HITL steps by hand.

Owner: Task division and management.

A grader picks a case; SilverFile starts a return on the taxpayer page and answers every
question from the case file (the same `case_reply` that `run.py --case` uses). The replay stops
at the first HITL event and never resolves it: the grader does that in the staff dashboard,
then comes back and continues the replay. Finished drafts still need a volunteer's release
before the taxpayer view shows the form.

Cases are the two demo cases (demo/eval_case_N/case.json) and the regression cases in
evals/cases.json, which are a base case plus overrides.
"""
from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path

from langgraph.types import Command

from src.agents.intake import case_reply

ROOT = Path(__file__).resolve().parents[2]
MAX_STEPS = 200  # questions answered in one replay; a safety net, never reached by a sound case


@lru_cache(maxsize=1)
def catalog() -> dict[str, dict]:
    """Every case a grader can run, by id: title, what it shows, and what should happen."""
    cases: dict[str, dict] = {}
    for path in sorted(ROOT.glob("demo/eval_case_*/case.json")):
        case = json.loads(path.read_text())
        number = case["case_id"].rsplit("_", 1)[-1]
        cases[case["case_id"]] = {"id": case["case_id"], "group": "demo", "title": f"Eval case {number}",
                                  "description": case.get("description", ""), "case": case}
    regression = json.loads((ROOT / "evals" / "cases.json").read_text())
    for entry in regression["cases"]:
        base = json.loads((ROOT / entry["base"]).read_text())
        case = copy.deepcopy(base)
        case["case_id"] = entry["id"]
        case["answers"].update(entry.get("answers", {}))
        case["documents"] = case["documents"] + entry.get("add_documents", [])
        case["hitl_resolutions"] = entry.get("hitl_resolutions", [])
        case["expected"] = entry.get("expected", {})
        cases[entry["id"]] = {"id": entry["id"], "group": "negative", "title": entry["id"].replace("_", " ").capitalize(),
                              "description": entry.get("description", ""), "case": case}
    return cases


def replay(graph, session_id: str, case: dict) -> str:
    """Answer questions from the case until a HITL event, the end, or the step limit.
    Returns "hitl", "done" or "stuck"."""
    config = {"configurable": {"thread_id": session_id}}
    for _ in range(MAX_STEPS):
        payload = pending(graph, session_id)
        if payload is None:
            return "done"
        if payload.get("type") != "intake_question":
            return "hitl"
        graph.invoke(Command(resume=case_reply(case, payload)), config)
    return "stuck"


def pending(graph, session_id: str) -> dict | None:
    snapshot = graph.get_state({"configurable": {"thread_id": session_id}})
    return next((i.value for task in snapshot.tasks for i in task.interrupts), None)


def expected_resolution(case: dict, condition: str) -> dict | None:
    """What the case file says a volunteer should decide for this condition, as a hint for the grader."""
    return next((r for r in case.get("hitl_resolutions", []) if r.get("condition") == condition), None)


def comparison(case: dict, values: dict) -> dict:
    """Expected against actual: line values for the demo cases, HITL conditions and status for all."""
    expected = case.get("expected", {})
    lines = values.get("lines") or {}
    rows = [{"line": key, "expected": want, "actual": (lines.get(key) or {}).get("value")}
            for key, want in (expected.get("lines") or {}).items()]
    raised = [event["condition"] for event in values.get("hitl", [])]
    return {"lines": rows, "lines_match": bool(rows) and all(r["expected"] == r["actual"] for r in rows),
            "expected_conditions": expected.get("hitl_conditions", []), "raised": raised,
            "expected_status": expected.get("status", "complete" if rows else ""), "status": values.get("status")}
