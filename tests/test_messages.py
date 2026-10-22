"""AgentMessage builders validate agents and kinds; llm stays offline in tests."""
import pytest

from src import llm
from src.messages import AGENTS, as_update, escalate, handoff, info, make_message, messages_for, send_back


def test_builders_produce_contract_messages():
    msg = send_back("review", "computation", "Line 6b exceeds 6a", {"lines": ["6b"]})
    assert msg == {"from_agent": "review", "to_agent": "computation", "kind": "send_back",
                   "reason": "Line 6b exceeds 6a", "payload": {"lines": ["6b"]}}
    assert handoff("extract_aggregate", "eligibility", "All documents read")["kind"] == "handoff"
    assert escalate("extract_aggregate", "Unreadable W-2")["to_agent"] == "human_review"
    assert info("extract_aggregate", "review", "Sidecar used")["payload"] == {}


@pytest.mark.parametrize("sender, recipient, kind", [
    ("extraction", "review", "info"),        # not a node name
    ("review", "Computation", "send_back"),  # names are case-sensitive
    ("review", "computation", "retry"),      # not a kind
])
def test_invalid_messages_are_rejected(sender, recipient, kind):
    with pytest.raises(ValueError):
        make_message(sender, recipient, kind, "some reason")


def test_reason_is_required():
    with pytest.raises(ValueError):
        handoff("intake", "orchestrator", "   ")


def test_payload_is_copied_not_shared():
    payload = {"doc_ids": ["w2_1"]}
    msg = info("extract_aggregate", "review", "note", payload)
    payload["doc_ids"].append("x")
    assert msg["payload"] == {"doc_ids": ["w2_1"]}


def test_as_update_and_messages_for():
    msgs = [send_back("review", "computation", "a"), info("review", "computation", "b"),
            send_back("review", "eligibility", "c")]
    assert as_update(*msgs) == {"handoffs": msgs}
    assert [m["reason"] for m in messages_for(msgs, "computation", "send_back")] == ["a"]
    assert "orchestrator" in AGENTS and "human_review" in AGENTS


def test_llm_is_offline_under_tests(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert llm.is_offline() and llm.get_llm() is None
    with pytest.raises(RuntimeError):
        llm.invoke_with_usage([])
    monkeypatch.setenv("SILVERFILE_OFFLINE", "0")
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert llm.is_offline()
