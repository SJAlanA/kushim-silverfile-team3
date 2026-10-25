"""Review / Audit: a correct return passes; every seeded defect is caught and sent to its owner."""
import copy
import json
from pathlib import Path

import pytest

from src.agents import computation, review
from src.agents.eligibility_rules import decide
from src.agents.extraction import extract_document
from src.hitl import raise_hitl

ROOT = Path(__file__).resolve().parent.parent
CASE_1 = json.loads((ROOT / "demo" / "eval_case_1" / "case.json").read_text())
INTAKE_1 = {
    "who_is_answering": {"is_taxpayer": True, "relationship": None, "has_authority": None},
    "taxpayer_first_name": "Mara", "taxpayer_last_name": "Lindqvist", "taxpayer_ssn": "901-11-1037",
    "taxpayer_dob": "1958-06-21", "taxpayer_blind": False, "can_be_claimed_as_dependent": False,
    "filing_status": "single", "dual_status_alien": False, "dependents": [],
    "other_income": {"has_other_income": False, "description": ""}, "adjustments": 0,
    "schedule_values": {"sch1_line10": 0, "sch2_line3": 0, "sch2_line21": 0, "sch3_line8": 0, "sch3_line15": 0},
    "estimated_payments": 0, "refund_destination": {"description": "My own checking account", "described": True},
}


def answer(value):
    return {"value": value, "raw_text": "", "answered_at": "2026-10-01T12:00:00+00:00", "history": []}


def build_state(intake_values: dict, documents: list[dict]) -> dict:
    """A return built by the real agents: Extraction, the Eligibility table and Computation."""
    documents = [{**doc, "path": str(ROOT / doc["path"])} for doc in documents]
    extracted = {doc["doc_id"]: extract_document(doc) for doc in documents}
    state = {"session_id": "review-test", "intake": {qid: answer(v) for qid, v in intake_values.items()},
             "documents": documents, "extracted": extracted, "hitl": [], "handoffs": []}
    state["rules"] = decide(intake_values, documents, extracted).rules
    state["lines"] = computation.run(state)["lines"]
    return state


@pytest.fixture(scope="module")
def correct_state() -> dict:
    return build_state(INTAKE_1, CASE_1["documents"])


@pytest.fixture
def state(correct_state) -> dict:
    return copy.deepcopy(correct_state)


def issues(state: dict) -> list[dict]:
    return review.run(state)["review"]["issues"]


def found(state: dict, check: str, line: str | None = None) -> list[dict]:
    return [i for i in issues(state) if i["check"] == check and (line is None or i["line"] == line)]


def set_line(state: dict, key: str, value: int) -> None:
    state["lines"][key] = {**state["lines"][key], "value": value}


# --- a correct return ---------------------------------------------------------------------

def test_the_correct_eval_case_1_return_passes(correct_state):
    assert {k: correct_state["lines"][k]["value"] for k in CASE_1["expected"]["lines"]} == CASE_1["expected"]["lines"]
    update = review.run(copy.deepcopy(correct_state))
    assert update["review"]["passed"] is True and update["review"]["issues"] == []
    assert update["review_iterations"] == 1 and update["review"]["iteration"] == 1
    assert [(m["kind"], m["to_agent"]) for m in update["handoffs"]] == [("handoff", "finalize")]


def test_review_recomputes_with_tax_math_tools(correct_state, monkeypatch):
    calls = []
    real = review.call_tool
    monkeypatch.setattr(review, "call_tool", lambda name, **kw: calls.append(name) or real(name, **kw))
    review.run(copy.deepcopy(correct_state))
    assert {"add", "subtract", "max0", "standard_deduction", "senior_deduction", "taxable_social_security",
            "tax_from_rate_schedule", "overpayment_or_owed"} <= set(calls)


def test_review_never_modifies_the_return(state):
    before = copy.deepcopy(state)
    set_line(state, "9", 1)
    tampered = copy.deepcopy(state)
    update = review.run(state)
    assert state == tampered and before["lines"]["9"]["value"] != 1
    assert not {"lines", "rules", "extracted", "intake", "rerun"} & set(update)


def test_iterations_count_up(state):
    state["review_iterations"] = 2
    update = review.run(state)
    assert update["review_iterations"] == 3 and update["review"]["iteration"] == 3


# --- seeded defects ------------------------------------------------------------------------

def test_taxable_social_security_above_benefits_is_caught(state):
    set_line(state, "6b", 13000)
    assert found(state, "not_more_than", "6b")[0]["send_to"] == "computation"
    assert found(state, "taxable_social_security", "6b")


def test_wrong_standard_deduction_is_caught(state):
    set_line(state, "12e", 15750)
    issue = found(state, "standard_deduction_chart", "12e")[0]
    assert issue["expected"] == 17750 and issue["found"] == 15750 and issue["send_to"] == "computation"


def test_missing_provenance_is_caught(state):
    del state["lines"]["9"]["provenance"]["formula"]
    assert "formula" in found(state, "provenance_complete", "9")[0]["found"]


def test_a_missing_line_is_caught(state):
    del state["lines"]["25c"]
    assert found(state, "line_present", "25c")


def test_a_tampered_total_is_caught(state):
    set_line(state, "9", 36000)
    issue = found(state, "recomputed_total", "9")[0]
    assert issue["expected"] == 36940 and issue["found"] == 36000 and issue["send_to"] == "computation"


def test_a_line_that_disagrees_with_its_documents_is_caught(state):
    set_line(state, "1a", 31000)
    assert found(state, "matches_documents", "1a")[0]["expected"] == 30000


def test_an_out_of_scope_1099_nec_that_was_never_escalated_is_caught(state):
    nec = {"doc_id": "nec_1", "doc_type": "1099-NEC", "path": str(ROOT / "data/fixtures/1099nec_1.json")}
    state["extracted"]["nec_1"] = extract_document(nec)
    assert found(state, "out_of_scope_document")[0]["send_to"] == "eligibility"
    state["hitl"] += raise_hitl("OUT_OF_SCOPE", "1099-NEC", "eligibility", {"doc_id": "nec_1"})["hitl"]
    assert not found(state, "out_of_scope_document")


def test_an_amount_from_an_out_of_scope_document_is_caught(state):
    state["extracted"]["nec_1"] = {"doc_id": "nec_1", "doc_type": "1099-NEC", "fields": {"box1": 4200},
                                   "extractor": "json", "confidence": 1.0, "source_path": ""}
    state["lines"]["1a"]["provenance"]["sources"].append({"doc_id": "nec_1", "field": "box1"})
    assert found(state, "out_of_scope_source", "1a")


def test_box_2b_without_an_escalation_is_caught(state):
    state["extracted"]["r_1"] = extract_document(
        {"doc_id": "r_1", "doc_type": "1099-R", "path": str(ROOT / "data/fixtures/1099r_2b_checked.json")})
    assert found(state, "box_2b_escalated")[0]["send_to"] == "eligibility"


def test_out_of_scope_other_income_without_an_escalation_is_caught(state):
    state["intake"]["other_income"] = answer({"has_other_income": True, "description": "I sold some bitcoin"})
    assert found(state, "out_of_scope_income")[0]["send_to"] == "eligibility"


def test_an_invalid_ssn_on_the_return_is_sent_to_intake(state):
    state["intake"]["taxpayer_ssn"] = answer("123-45-6789")
    issue = found(state, "valid_test_ssn")[0]
    assert issue["send_to"] == "intake" and "6789" not in json.dumps(issue)


def test_a_line_fed_by_an_injection_flagged_document_fails(state):
    state["extracted"]["w2_1"]["flags"] = ["ignore-instructions"]
    issue = found(state, "untrusted_document", "1a")[0]
    assert issue["send_to"] == "extract_aggregate"


def test_a_volunteer_can_accept_a_flagged_document(state):
    state["extracted"]["w2_1"]["flags"] = ["ignore-instructions"]
    event = raise_hitl("UNCERTAIN", "flagged", "extraction", {"doc_id": "w2_1", "issue": "untrusted_text"})["hitl"][0]
    state["hitl"] = [{**event, "status": "resolved", "resolution": {"decision": "continue"}}]
    assert not found(state, "untrusted_document")


def test_missing_rules_are_sent_to_eligibility(state):
    state["rules"] = {**state["rules"], "box_count": None}
    assert found(state, "rules_complete")[0]["send_to"] == "eligibility"


def test_refund_and_amount_owed_together_is_caught(state):
    set_line(state, "37", 5)
    assert found(state, "refund_or_owed", "37")


def test_negative_and_non_integer_values_are_caught(state):
    set_line(state, "26", -1)
    set_line(state, "23", 1.5)
    assert found(state, "not_negative", "26") and found(state, "whole_dollars", "23")


def test_double_counted_deduction_is_caught(state):
    state["lines"]["13b"]["provenance"]["tool"] = "standard_deduction"
    assert found(state, "deduction_not_double_counted", "13b")


def test_line_16_must_name_the_rate_schedule(state):
    state["lines"]["16"]["provenance"]["method_note"] = "tax table"
    assert found(state, "method_note", "16")


# --- messages -----------------------------------------------------------------------------

def test_one_send_back_per_owner_with_the_lines_to_redo(state):
    set_line(state, "12e", 15750)
    state["rules"] = {**state["rules"], "boxes": {**state["rules"]["boxes"], "tp_blind": True}}
    update = review.run(state)
    messages = {m["to_agent"]: m for m in update["handoffs"]}
    assert set(messages) == {"computation", "eligibility"}
    assert all(m["kind"] == "send_back" and m["from_agent"] == "review" for m in messages.values())
    assert "12e" in messages["computation"]["payload"]["lines"]
    assert computation.requested_lines({"handoffs": update["handoffs"]}) == messages["computation"]["payload"]["lines"]


# --- in the graph -------------------------------------------------------------------------

def _graph_run(monkeypatch, tmp_path, computation_run):
    from src.agents import eligibility
    from tests.orchestration_helpers import config, new_graph, stub_agents

    monkeypatch.setattr("src.form.render.OUTPUT_DIR", tmp_path)
    documents = [{**doc, "path": str(ROOT / doc["path"])} for doc in CASE_1["documents"]]

    def intake_done(state):
        return {"intake": {qid: answer(v) for qid, v in INTAKE_1.items()}, "intake_complete": True,
                "documents": documents}

    stub_agents(monkeypatch, intake=intake_done, eligibility=eligibility.run, computation=computation_run,
                review=review.run)
    graph, cfg = new_graph(), config("review-graph")
    graph.invoke({"session_id": "review-graph"}, cfg)
    return graph, cfg


def test_in_the_graph_a_correct_return_passes_review_once_and_completes(monkeypatch, tmp_path):
    real = computation.run
    graph, cfg = _graph_run(monkeypatch, tmp_path, real)
    values = graph.get_state(cfg).values
    assert values["status"] == "complete" and values["review"]["passed"] and values["review_iterations"] == 1


def test_in_the_graph_a_computation_that_keeps_a_wrong_total_hits_the_review_limit(monkeypatch, tmp_path):
    from tests.orchestration_helpers import pending

    real, runs = computation.run, []

    def wrong_total(state):
        runs.append(1)
        update = real(state)
        if "9" in update.get("lines", {}):
            update["lines"]["9"] = {**update["lines"]["9"], "value": 1}
        return update

    graph, cfg = _graph_run(monkeypatch, tmp_path, wrong_total)
    event = pending(graph, cfg)["event"]
    values = graph.get_state(cfg).values
    assert event["condition"] == "REVIEW_LIMIT" and values["review_iterations"] == 3
    assert len(runs) == 4  # first run plus one rerun per failed review (the orchestrator reruns before the bound)
    sent = [m for m in values["handoffs"] if m["from_agent"] == "review"]
    assert all(m["kind"] == "send_back" and m["to_agent"] == "computation" and "9" in m["payload"]["lines"]
               for m in sent)
