"""Reducers must give the same state whatever order concurrent writes arrive in."""
from functools import reduce
from itertools import permutations
from operator import add
from typing import Annotated, TypedDict

import pytest
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from src.state import (merge_answers, merge_documents, merge_extracted, merge_hitl, merge_lines)


def fold(reducer, updates, start=None):
    return reduce(reducer, updates, start)


def record(doc_id, confidence, extractor="ocr", **fields):
    return {doc_id: {"doc_id": doc_id, "doc_type": "W2", "fields": fields, "extractor": extractor,
                     "confidence": confidence, "source_path": f"demo/{doc_id}.jpg"}}


EXTRACTED_WRITES = [
    record("w2_1", 0.92, box1_wages=30000, box2_fed_withheld=2000),
    record("ssa_1", 1.0, "json", box5_net_benefits=12000),
    record("int_1", 1.0, "json", box1_interest=300),
    record("w2_1", 0.55, box1_wages=38000, box2_fed_withheld=2000),     # a worse, disagreeing OCR read
    record("w2_1", 0.40, box1_wages=38000, box2_fed_withheld=2000),     # same disagreement, even worse
    record("w2_1", 0.92, "json", box1_wages=30000, box2_fed_withheld=2000),  # agrees with the winner
]


def test_merge_extracted_is_identical_in_every_permutation():
    results = {repr(fold(merge_extracted, order)) for order in permutations(EXTRACTED_WRITES)}
    assert len(results) == 1


def test_merge_extracted_keeps_best_reading_and_notes_conflict():
    merged = fold(merge_extracted, EXTRACTED_WRITES)
    w2 = merged["w2_1"]
    assert w2["fields"]["box1_wages"] == 30000
    assert w2["extractor"] == "ocr"  # same confidence: ocr outranks json
    assert [c["fields"]["box1_wages"] for c in w2["_conflicts"]] == [38000]
    assert w2["_conflicts"][0]["confidence"] == 0.55  # best reading of the disagreeing fields
    assert list(merged) == sorted(merged)


def test_merge_extracted_is_associative():
    a, b, c = EXTRACTED_WRITES[0], EXTRACTED_WRITES[3], EXTRACTED_WRITES[1]
    assert merge_extracted(merge_extracted(a, b), c) == merge_extracted(a, merge_extracted(b, c))


def test_merge_extracted_without_disagreement_has_no_conflicts_key():
    merged = fold(merge_extracted, [EXTRACTED_WRITES[0], EXTRACTED_WRITES[5]])
    assert "_conflicts" not in merged["w2_1"]


class FanOutState(TypedDict, total=False):
    documents: list
    extracted: Annotated[dict, merge_extracted]
    trail: Annotated[list, add]


def _fan_out_graph():
    def dispatch(state):
        return [Send("extract_one", {"doc": doc}) for doc in state["documents"]]

    def extract_one(payload):
        doc = payload["doc"]
        return {"extracted": record(doc["doc_id"], doc["confidence"], **doc["fields"]), "trail": [doc["doc_id"]]}

    graph = StateGraph(FanOutState)
    graph.add_node("extract_one", extract_one)
    graph.add_node("aggregate", lambda state: {})
    graph.add_conditional_edges(START, dispatch, ["extract_one"])
    graph.add_edge("extract_one", "aggregate")
    graph.add_edge("aggregate", END)
    return graph.compile()


FAN_OUT_DOCS = [
    {"doc_id": "w2_1", "confidence": 0.9, "fields": {"box1_wages": 30000}},
    {"doc_id": "w2_1", "confidence": 0.6, "fields": {"box1_wages": 30800}},
    {"doc_id": "ssa_1", "confidence": 1.0, "fields": {"box5_net_benefits": 12000}},
]


def test_three_branch_send_fan_out_gives_same_state_in_every_dispatch_order():
    graph = _fan_out_graph()
    finals = []
    for order in permutations(FAN_OUT_DOCS):
        result = graph.invoke({"documents": list(order)})
        assert sorted(result["trail"]) == ["ssa_1", "w2_1", "w2_1"]  # all three branches ran
        finals.append(result["extracted"])
    assert all(final == finals[0] for final in finals)
    assert finals[0]["w2_1"]["fields"]["box1_wages"] == 30000
    assert finals[0]["w2_1"]["_conflicts"][0]["fields"]["box1_wages"] == 30800


def answer(value, at, history=None):
    return {"filing_status": {"value": value, "raw_text": str(value), "answered_at": at, "history": history or []}}


def test_merge_answers_history_is_identical_in_every_permutation():
    writes = [answer("single", "2025-02-01T10:00"), answer("mfj", "2025-02-01T10:05"),
              answer("single", "2025-02-04T09:00"), answer("single", "2025-02-04T09:01")]
    results = [fold(merge_answers, order) for order in permutations(writes)]
    assert all(r == results[0] for r in results)
    merged = results[0]["filing_status"]
    assert merged["value"] == "single" and merged["answered_at"] == "2025-02-04T09:01"
    assert [h["value"] for h in merged["history"]] == ["single", "mfj"]  # the repeat is not a change
    assert len(merged["_versions"]) == 4  # but nothing is thrown away


def test_merge_answers_never_drops_a_previous_answer_or_key():
    state = fold(merge_answers, [answer("single", "t1"),
                                 {"taxpayer_blind": {"value": False, "raw_text": "no", "answered_at": "t1",
                                                     "history": []}},
                                 answer("hoh", "t2")])
    assert set(state) == {"filing_status", "taxpayer_blind"}
    assert state["filing_status"]["history"][0]["value"] == "single"


def test_merge_answers_matches_intake_carrying_history_forward():
    """Intake sends the old history with a new answer; the reducer must not double it up."""
    first = answer("single", "t1")
    old_history = []
    second = answer("mfj", "t2", old_history)
    state = merge_answers(first, second)
    third = answer("single", "t3", state["filing_status"]["history"])
    state = merge_answers(state, third)
    assert [h["value"] for h in state["filing_status"]["history"]] == ["single", "mfj"]


def event(status, resolved_at=None, event_id="doc_conflict-1"):
    resolution = {"decision": "continue", "resolved_at": resolved_at} if status == "resolved" else None
    return [{"event_id": event_id, "condition": "DOC_CONFLICT", "reason": "W-2 differs from answer",
             "raised_by": "eligibility", "status": status, "resolution": resolution, "raised_at": "t0"}]


def test_merge_hitl_resolved_event_wins_in_every_permutation():
    writes = [event("open"), event("resolved", "t5"), event("open"), event("open", event_id="uncertain-2")]
    results = [{e["event_id"]: e for e in fold(merge_hitl, order)} for order in permutations(writes)]
    assert all(r == results[0] for r in results)
    assert results[0]["doc_conflict-1"]["status"] == "resolved"
    assert results[0]["uncertain-2"]["status"] == "open"


def test_merge_hitl_keeps_first_seen_order():
    merged = fold(merge_hitl, [event("open", event_id="a"), event("open", event_id="b"), event("resolved", "t9", "a")])
    assert [e["event_id"] for e in merged] == ["a", "b"]
    assert merged[0]["status"] == "resolved"


def line(key, value, at):
    return {key: {"line": key, "value": value, "provenance": {"at": at, "tool": "add"}}}


def test_merge_lines_newer_provenance_wins_in_every_permutation():
    writes = [line("1z", 30000, "t2"), line("1z", 31000, "t1"), line("9", 36940, "t1")]
    results = [fold(merge_lines, order) for order in permutations(writes)]
    assert all(r == results[0] for r in results)
    assert results[0]["1z"]["value"] == 30000


@pytest.mark.parametrize("updates, expected", [
    ([[{"doc_id": "w2_1", "path": "a"}], [{"doc_id": "ssa_1", "path": "b"}, {"doc_id": "w2_1", "path": "c"}]],
     [("w2_1", "c"), ("ssa_1", "b")]),
])
def test_merge_documents_dedupes_and_keeps_first_seen_order(updates, expected):
    merged = fold(merge_documents, updates)
    assert [(d["doc_id"], d["path"]) for d in merged] == expected


def test_reducers_accept_empty_sides():
    assert merge_extracted(None, None) == {}
    assert merge_answers(None, {}) == {}
    assert merge_hitl(None, []) == []
    assert merge_lines({}, None) == {}
    assert merge_documents(None, None) == []
