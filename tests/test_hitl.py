"""Tests for src/hitl.py and for how the Eligibility agent raises events."""
import pytest

from src import hitl
from src.agents import eligibility


def test_raise_hitl_builds_an_open_event_with_the_contract_fields():
    update = hitl.raise_hitl("DOC_CONFLICT", "  The W-2 name differs.  ", "eligibility", {"doc_id": "w2_1"})
    (event,) = update["hitl"]
    assert update["status"] == "awaiting_human"
    assert event["event_id"].startswith("doc_conflict-")
    assert event["status"] == "open" and event["resolution"] is None
    assert event["reason"] == "The W-2 name differs."
    assert event["details"] == {"doc_id": "w2_1"} and event["raised_at"]
    assert hitl.raise_hitl("UNCERTAIN", "x", "a")["hitl"][0]["details"] == {}


def test_raise_hitl_rejects_unknown_conditions_and_empty_reasons():
    with pytest.raises(ValueError, match="NOT_A_CONDITION"):
        hitl.raise_hitl("NOT_A_CONDITION", "reason", "a")
    with pytest.raises(ValueError, match="reason"):
        hitl.raise_hitl("UNCERTAIN", "  ", "a")


def test_event_ids_are_unique():
    ids = {hitl.raise_hitl("UNCERTAIN", "x", "a")["hitl"][0]["event_id"] for _ in range(50)}
    assert len(ids) == 50


@pytest.mark.parametrize("details, subject", [
    ({"doc_id": "w2_1", "question_id": "q"}, "w2_1"), ({"question_id": "filing_status"}, "filing_status"),
    ({"subject": "other_income"}, "other_income"), ({}, None), ({"fingerprint": "abc"}, None),
])
def test_subject_of(details, subject):
    assert hitl.subject_of({"details": details}) == subject


def test_has_event_matches_condition_and_subject_open_or_resolved():
    open_event = hitl.raise_hitl("DOC_CONFLICT", "x", "a", {"doc_id": "w2_1"})["hitl"][0]
    resolved = hitl.resolve_event(hitl.raise_hitl("OUT_OF_SCOPE", "x", "a", {"question_id": "q"})["hitl"][0],
                                  {"decision": "continue"})
    state = {"hitl": [open_event, resolved]}
    assert hitl.has_event(state, "DOC_CONFLICT", "w2_1") and hitl.has_event(state, "OUT_OF_SCOPE", "q")
    assert not hitl.has_event(state, "DOC_CONFLICT", "w2_2")
    assert not hitl.has_event(state, "TAX_ADVICE", "w2_1")
    assert hitl.has_event(state, "OUT_OF_SCOPE") and not hitl.has_event({}, "OUT_OF_SCOPE")


def test_resolve_event_records_the_decision_and_keeps_the_event():
    event = hitl.raise_hitl("FILING_STATUS_AMBIGUITY", "x", "intake")["hitl"][0]
    resolved = hitl.resolve_event(event, {"decision": "continue", "note": "joint", "resolved_by": "Sam",
                                          "updates": {"filing_status": "mfj"}})
    assert resolved["status"] == "resolved" and resolved["event_id"] == event["event_id"]
    assert resolved["resolution"]["updates"] == {"filing_status": "mfj"}
    assert resolved["resolution"]["resolved_by"] == "Sam" and resolved["resolution"]["resolved_at"]
    assert event["status"] == "open"   # the original is not changed


@pytest.mark.parametrize("decision", ["continue", "stop", "send_back:computation"])
def test_resolve_event_accepts_the_three_decisions(decision):
    event = hitl.raise_hitl("UNCERTAIN", "x", "a")["hitl"][0]
    assert hitl.resolve_event(event, {"decision": decision})["resolution"]["decision"] == decision


@pytest.mark.parametrize("decision", ["", "approve", "send_back:", None])
def test_resolve_event_rejects_anything_else(decision):
    event = hitl.raise_hitl("UNCERTAIN", "x", "a")["hitl"][0]
    with pytest.raises(ValueError, match="decision"):
        hitl.resolve_event(event, {"decision": decision})


def test_merge_updates_keeps_every_event_in_order():
    first, second = hitl.raise_hitl("UNCERTAIN", "a", "x"), hitl.raise_hitl("DOC_CONFLICT", "b", "x")
    merged = hitl.merge_updates(first, second)
    assert [e["condition"] for e in merged["hitl"]] == ["UNCERTAIN", "DOC_CONFLICT"]
    assert merged["status"] == "awaiting_human" and hitl.merge_updates() == {}


# ---------------------------------------------------------------------------
# Eligibility agent: state in, update out
# ---------------------------------------------------------------------------

def answer(value) -> dict:
    return {"value": value, "raw_text": "", "answered_at": "2026-01-01T00:00:00", "history": []}


def widow_state(**extra) -> dict:
    values = {"filing_status": "mfj", "taxpayer_dob": "1957-03-14", "taxpayer_blind": False,
              "spouse_dob": "1959-09-02", "spouse_blind": False, "spouse_died_in_2025": True,
              "can_be_claimed_as_dependent": False, "dual_status_alien": False, "dependents": []}
    return {"session_id": "t", "intake": {qid: answer(v) for qid, v in values.items()}, "documents": [],
            "extracted": {}, **extra}


def test_agent_raises_the_event_when_intake_has_not():
    update = eligibility.run(widow_state())
    assert [e["condition"] for e in update["hitl"]] == ["FILING_STATUS_AMBIGUITY"]
    assert update["hitl"][0]["raised_by"] == "eligibility"
    assert update["hitl"][0]["details"]["question_id"] == "spouse_died_in_2025"
    assert update["hitl"][0]["details"]["citation"] == "policy.md#r-fs-03"
    assert update["handoffs"][0]["kind"] == "escalate" and update["rules"]["box_count"] == 2


@pytest.mark.parametrize("status", ["open", "resolved"])
def test_agent_does_not_raise_what_intake_already_raised(status):
    """Intake raises FILING_STATUS_AMBIGUITY itself; open or resolved, Eligibility must not repeat it."""
    event = hitl.raise_hitl("FILING_STATUS_AMBIGUITY", "x", "intake", {"question_id": "spouse_died_in_2025"})["hitl"][0]
    event = {**event, "status": status}
    update = eligibility.run(widow_state(hitl=[event]))
    assert "hitl" not in update and update["handoffs"][0]["kind"] == "handoff"
    assert update["rules"]["filing_status"] == "mfj"


def test_agent_reruns_cleanly_after_the_volunteer_changes_an_answer():
    resolved = hitl.resolve_event(
        hitl.raise_hitl("FILING_STATUS_AMBIGUITY", "x", "intake", {"question_id": "spouse_died_in_2025"})["hitl"][0],
        {"decision": "continue", "updates": {"filing_status": "mfj"}})
    state = widow_state(hitl=[resolved])
    state["intake"]["filing_status"] = answer("mfj")
    assert "hitl" not in eligibility.run(state)


def test_agent_still_raises_a_different_issue_after_one_was_resolved():
    resolved = hitl.resolve_event(
        hitl.raise_hitl("FILING_STATUS_AMBIGUITY", "x", "intake", {"question_id": "spouse_died_in_2025"})["hitl"][0],
        {"decision": "continue"})
    state = widow_state(hitl=[resolved])
    state["intake"]["dual_status_alien"] = answer(True)
    assert [e["condition"] for e in eligibility.run(state)["hitl"]] == ["OUT_OF_SCOPE"]


def test_agent_always_returns_rules_even_when_it_escalates():
    state = widow_state()
    state["intake"]["can_be_claimed_as_dependent"] = answer(True)
    update = eligibility.run(state)
    assert update["rules"]["in_scope"] is False and update["rules"]["filing_status"] == "mfj"
    assert {e["condition"] for e in update["hitl"]} == {"FILING_STATUS_AMBIGUITY", "OUT_OF_SCOPE"}


def test_two_documents_get_two_events_not_one():
    state = widow_state()
    state["documents"] = [{"doc_id": "n1", "doc_type": "1099-NEC", "path": "a"},
                          {"doc_id": "n2", "doc_type": "1099-NEC", "path": "b"}]
    subjects = [e["details"]["doc_id"] for e in eligibility.run(state)["hitl"] if e["condition"] == "OUT_OF_SCOPE"]
    assert subjects == ["n1", "n2"]


# ---------------------------------------------------------------------------
# Model cross-check (online only; the table always wins)
# ---------------------------------------------------------------------------

@pytest.fixture
def online(monkeypatch):
    """Pretend a model is configured and let each test choose what it replies."""
    reply = {"text": ""}
    monkeypatch.setattr(eligibility.llm, "get_llm", lambda: object())
    monkeypatch.setattr(eligibility.llm, "invoke_with_usage", lambda messages: (reply["text"], 100))
    return reply


def clean_state() -> dict:
    return widow_state(hitl=[hitl.resolve_event(
        hitl.raise_hitl("FILING_STATUS_AMBIGUITY", "x", "intake", {"question_id": "spouse_died_in_2025"})["hitl"][0],
        {"decision": "continue"})])


def test_offline_there_is_no_cross_check():
    assert "llm_check" not in eligibility.run(clean_state())["rules"]


def test_model_that_agrees_is_recorded(online):
    online["text"] = ('Here: {"eligible_1040sr": true, "box_count": 2, "senior_qualifiers": 2, '
                      '"citations": ["R-ELIG-01", "R-SD-02", "R-SR-01"]}')
    check = eligibility.run(clean_state())["rules"]["llm_check"]
    assert check == {"agreed": True, "differences": [], "citations": ["R-ELIG-01", "R-SD-02", "R-SR-01"]}


def test_model_that_disagrees_is_recorded_and_the_table_still_wins(online):
    online["text"] = '{"eligible_1040sr": true, "box_count": 4, "senior_qualifiers": 2, "citations": ["R-SD-02"]}'
    rules = eligibility.run(clean_state())["rules"]
    assert rules["box_count"] == 2
    assert rules["llm_check"]["agreed"] is False
    assert rules["llm_check"]["differences"] == [{"field": "box_count", "table": 2, "model": 4, "rule": "R-SD-02"}]


@pytest.mark.parametrize("citations", [[], ["R-MADE-UP-99"]])
def test_model_that_cites_nothing_real_is_not_grounded(online, citations):
    online["text"] = ('{"eligible_1040sr": true, "box_count": 2, "senior_qualifiers": 2, "citations": %s}'
                      % str(citations).replace("'", '"'))
    check = eligibility.run(clean_state())["rules"]["llm_check"]
    assert check["agreed"] is False and check["differences"][0]["field"] == "citations"


@pytest.mark.parametrize("text", ["not json at all", "{broken", ""])
def test_unusable_model_output_means_no_cross_check_and_no_failure(online, text):
    online["text"] = text
    rules = eligibility.run(clean_state())["rules"]
    assert "llm_check" not in rules and rules["box_count"] == 2


def test_model_never_sees_names_or_numbers(online, monkeypatch):
    seen = []
    monkeypatch.setattr(eligibility.llm, "invoke_with_usage", lambda messages: (seen.append(messages) or "{}", 1))
    state = clean_state()
    state["intake"]["taxpayer_ssn"] = answer("901-11-1037")
    state["intake"]["taxpayer_first_name"] = answer("Mara")
    eligibility.run(state)
    facts = seen[0][1].content
    assert "Mara" not in facts and "1037" not in facts and "1957" not in facts
