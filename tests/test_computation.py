"""Computation agent: every contract line, provenance on every line, and no Python arithmetic."""
import ast
import itertools
import json
from pathlib import Path

import pytest

from src.agents import computation
from src.tools.tax_math_server import TOOLS

ROOT = Path(__file__).resolve().parent.parent
ANSWERED_AT = "2026-10-20T15:30:00+00:00"
DIRECT_LINES = {"8", "10", "17", "23", "26"}


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("TAX_MATH_INPROCESS", "1")
    monkeypatch.setenv("SILVERFILE_TRACE_DIR", str(tmp_path))


def _record(doc_id: str, doc_type: str, fields: dict) -> dict:
    return {"doc_id": doc_id, "doc_type": doc_type, "fields": fields, "extractor": "json", "confidence": 1.0,
            "source_path": f"{doc_id}.json"}


def _fixture(name: str) -> dict:
    return json.loads((ROOT / name).read_text())["fields"]


def eval_case_1_state(**changes) -> dict:
    """The state Computation sees for demo/eval_case_1 once Extraction and Eligibility have run."""
    case = json.loads((ROOT / "demo/eval_case_1/case.json").read_text())
    intake = {qid: {"value": value, "raw_text": "", "answered_at": ANSWERED_AT, "history": []}
              for qid, value in case["answers"].items()}
    state = {
        "session_id": "test-computation",
        "intake": intake,
        "extracted": {
            "w2_1": _record("w2_1", "W2", _fixture("demo/w2_image_1.json")),
            "ssa_1": _record("ssa_1", "SSA-1099", _fixture("data/fixtures/ssa1099_1.json")),
            "int_1": _record("int_1", "1099-INT", _fixture("data/fixtures/1099int_1.json")),
            "div_1": _record("div_1", "1099-DIV", _fixture("data/fixtures/1099div_1.json")),
        },
        "rules": {"eligible_1040sr": True, "filing_status": "single", "box_count": 1, "senior_qualifiers": 1,
                  "in_scope": True, "doc_line_map": {"w2_1": "1a", "ssa_1": "6a", "int_1": "2b", "div_1": "3b"}},
        "lines": {},
        "handoffs": [],
    }
    state.update(changes)
    return state


def with_retirement_docs(state: dict) -> dict:
    state["extracted"].update({
        "r_ira": _record("r_ira", "1099-R", _fixture("data/fixtures/1099r_ira_qcd_1.json")),
        "r_pension": _record("r_pension", "1099-R", _fixture("data/fixtures/1099r_pension_1.json")),
    })
    return state


# --- Every line, with provenance ---------------------------------------------------------------

def test_every_contract_line_exists_with_full_provenance():
    lines = computation.run(eval_case_1_state())["lines"]
    assert set(lines) == set(computation.LINE_KEYS)
    for key, line in lines.items():
        assert line["line"] == key and isinstance(line["value"], int)
        provenance = line["provenance"]
        assert set(provenance) >= {"sources", "tool", "inputs", "formula", "method_note", "entered_by", "at"}
        assert provenance["sources"] and provenance["formula"] and provenance["method_note"]
        assert provenance["tool"] in TOOLS or provenance["tool"] == "direct_user_input"


def test_eval_case_1_expected_lines():
    case = json.loads((ROOT / "demo/eval_case_1/case.json").read_text())
    lines = computation.run(eval_case_1_state())["lines"]
    assert {key: lines[key]["value"] for key in case["expected"]["lines"]} == case["expected"]["lines"]
    assert (lines["16"]["value"], lines["24"]["value"], lines["33"]["value"]) == (1_338, 1_338, 2_000)
    assert (lines["34"]["value"], lines["37"]["value"]) == (662, 0)
    assert lines["16"]["provenance"]["method_note"] == "2025 rate schedule with qualified dividends worksheet"


def test_line_sources_point_at_documents():
    lines = computation.run(eval_case_1_state())["lines"]
    assert lines["1a"]["provenance"]["sources"] == [{"doc_id": "w2_1", "field": "box1_wages"}]
    assert lines["25a"]["provenance"]["sources"] == [{"doc_id": "w2_1", "field": "box2_fed_withheld"}]
    assert {"line": "6b"} in lines["9"]["provenance"]["sources"]


# --- No arithmetic in Python -------------------------------------------------------------------

def test_no_line_value_is_computed_in_python(monkeypatch):
    """A fake call_tool returns a unique sentinel per call. Every tool-derived line must carry one of
    those sentinels, so nothing was added or subtracted outside a tool call."""
    counter = itertools.count(1_000_001)
    returned: list[int] = []
    calls: list[str] = []

    def fake_call_tool(name, **kwargs):
        value = next(counter)
        returned.append(value)
        calls.append(name)
        extra = {"34": next(counter), "37": next(counter)} if name == "overpayment_or_owed" else {}
        returned.extend(extra.values())
        return {"value": value, "formula": f"sentinel {value}", "inputs": kwargs, "tool": name,
                "method_note": "sentinel", **extra}

    monkeypatch.setattr(computation, "call_tool", fake_call_tool)
    lines = computation.run(with_retirement_docs(eval_case_1_state()))["lines"]

    tool_lines = {key: line for key, line in lines.items() if line["provenance"]["tool"] != "direct_user_input"}
    assert set(lines) - set(tool_lines) == DIRECT_LINES
    for key, line in tool_lines.items():
        assert line["value"] in returned, f"line {key} = {line['value']} did not come from a tool call"
    # Lines 34 and 37 share one call; every other tool-derived line needs at least one call of its own.
    assert len(calls) >= len(tool_lines) - 1
    assert len({line["value"] for line in tool_lines.values()}) == len(tool_lines)


def test_one_tool_call_per_derived_line_on_eval_case_1(monkeypatch):
    real = computation.call_tool
    calls: list[str] = []
    monkeypatch.setattr(computation, "call_tool", lambda name, **kw: calls.append(name) or real(name, **kw))
    lines = computation.run(eval_case_1_state())["lines"]
    derived = [key for key, line in lines.items() if line["provenance"]["tool"] != "direct_user_input"]
    # 32 derived lines; 6b, 15 and 22 take two calls each, and 34/37 share one: 32 + 3 - 1 = 34.
    assert len(derived) == 32 and len(calls) == 34


def test_source_has_no_arithmetic_builtins():
    tree = ast.parse((ROOT / "src/agents/computation.py").read_text())
    names = {node.func.id for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert not names & {"sum", "min", "max", "abs", "round", "divmod", "pow"}


# --- Ordering ----------------------------------------------------------------------------------

def test_social_security_runs_after_other_income_and_before_total_income():
    order = [key for step in computation.STEPS for key in step.produces]
    for key in ("1z", "2b", "3b", "4b", "5b", "8", "10"):
        assert order.index(key) < order.index("6b")
    assert order.index("6b") < order.index("9") < order.index("11a") < order.index("13b") < order.index("16")


def test_order_check_rejects_a_step_that_reads_ahead():
    bad = (computation.STEPS[9], *computation.STEPS[:9], *computation.STEPS[10:])  # line 9 before 6b
    with pytest.raises(AssertionError):
        computation._check_order(bad)


# --- 1099-R handling ---------------------------------------------------------------------------

def test_ira_qcd_and_pension_lines():
    lines = computation.run(with_retirement_docs(eval_case_1_state()))["lines"]
    assert (lines["4a"]["value"], lines["4b"]["value"]) == (8_000, 5_000)     # 8,000 box 2a less 3,000 QCD
    assert (lines["5a"]["value"], lines["5b"]["value"]) == (20_000, 20_000)
    assert lines["25b"]["value"] == 2_800                                      # 800 IRA + 2,000 pension
    assert "subtract" in [s["tool"] for s in lines["4b"]["provenance"]["inputs"]["steps"]]


def test_doc_line_map_overrides_the_default_family():
    state = with_retirement_docs(eval_case_1_state())
    state["rules"]["doc_line_map"]["r_ira"] = "5a"
    lines = computation.run(state)["lines"]
    assert lines["4a"]["value"] == 0 and lines["5a"]["value"] == 28_000


def test_box2b_checked_refuses_and_raises_hitl():
    state = eval_case_1_state()
    state["extracted"]["r_1"] = _record("r_1", "1099-R", _fixture("data/fixtures/1099r_2b_checked.json"))
    update = computation.run(state)
    assert "lines" not in update
    assert [event["condition"] for event in update["hitl"]] == ["R1099_TAXABLE_NOT_DETERMINED"]
    assert update["status"] == "awaiting_human" and update["handoffs"][0]["kind"] == "escalate"


def test_missing_rules_raise_uncertain_instead_of_guessing():
    update = computation.run(eval_case_1_state(rules={"in_scope": True, "citations": []}))
    assert "lines" not in update and update["hitl"][0]["condition"] == "UNCERTAIN"


def test_unreadable_amount_raises_uncertain():
    state = eval_case_1_state()
    state["intake"]["estimated_payments"]["value"] = "about a thousand"
    assert computation.run(state)["hitl"][0]["condition"] == "UNCERTAIN"


# --- Direct user input -------------------------------------------------------------------------

def test_direct_user_input_provenance():
    state = eval_case_1_state()
    state["intake"]["estimated_payments"]["value"] = 1_500
    lines = computation.run(state)["lines"]
    provenance = lines["26"]["provenance"]
    assert lines["26"]["value"] == 1_500
    assert (provenance["tool"], provenance["entered_by"], provenance["at"]) == ("direct_user_input", "user",
                                                                                ANSWERED_AT)
    assert provenance["sources"] == [{"intake": "estimated_payments"}]
    assert lines["33"]["value"] == 3_500


def test_unanswered_direct_question_is_zero_from_a_tool():
    state = eval_case_1_state()
    del state["intake"]["estimated_payments"]
    line = computation.run(state)["lines"]["26"]
    assert line["value"] == 0 and line["provenance"]["tool"] == "add"
    assert line["provenance"]["sources"] == [{"intake": "estimated_payments", "status": "not answered"}]


# --- Send-back from Review ---------------------------------------------------------------------

def test_send_back_recomputes_only_asked_lines_and_downstream():
    first = computation.run(eval_case_1_state())
    send_back = {"from_agent": "review", "to_agent": "computation", "kind": "send_back",
                 "reason": "Check line 16", "payload": {"lines": ["16"]}}
    state = eval_case_1_state(lines=first["lines"], handoffs=first["handoffs"] + [send_back])
    second = computation.run(state)
    assert set(second["lines"]) == {"16", "18", "22", "24", "34", "37"}
    assert second["handoffs"][0]["payload"]["recomputed_for"] == ["16"]


def test_old_send_back_is_not_replayed():
    first = computation.run(eval_case_1_state())
    send_back = {"from_agent": "review", "to_agent": "computation", "kind": "send_back",
                 "reason": "Check line 16", "payload": {"lines": ["16"]}}
    handled = [send_back, *first["handoffs"]]  # Computation already handed off after the send-back
    assert computation.requested_lines({"handoffs": handled}) is None


def test_hands_off_to_review():
    message = computation.run(eval_case_1_state())["handoffs"][0]
    assert (message["from_agent"], message["to_agent"], message["kind"]) == ("computation", "review", "handoff")


def test_trace_written_for_session(tmp_path):
    computation.run(eval_case_1_state())
    events = [json.loads(line) for line in (tmp_path / "test-computation.jsonl").read_text().splitlines()]
    assert events[0]["kind"] == "node_start" and events[-1]["kind"] == "node_end"
    assert sum(event["kind"] == "tool_call" for event in events) == 34
