"""Builders for AgentMessage, the only way agents talk to each other.

Owner: Inter agent communication.

Agents never set `next_agent`; they append AgentMessages to `handoffs` and the orchestrator
reads them to decide the next hop. Building every message here means a typo in an agent
name or kind fails loudly at the sender instead of silently misrouting the graph.

    from src.messages import send_back, as_update
    return as_update(send_back("review", "computation", "Line 6b exceeds 6a", {"lines": ["6b"]}))
"""
from __future__ import annotations

import copy

from src.state import AgentMessage

# The graph's node names (contract section 12) are the only valid senders and recipients.
AGENTS: tuple[str, ...] = ("orchestrator", "intake", "extract_dispatch", "extract_one", "extract_aggregate",
                           "eligibility", "computation", "review", "human_review", "finalize")
KINDS: tuple[str, ...] = ("handoff", "send_back", "escalate", "info")  # AgentMessage.kind


def make_message(from_agent: str, to_agent: str, kind: str, reason: str, payload: dict | None = None) -> AgentMessage:
    """Build a validated AgentMessage. Raises ValueError on an unknown agent or kind, or an empty reason."""
    for role, name in (("from_agent", from_agent), ("to_agent", to_agent)):
        if name not in AGENTS:
            raise ValueError(f"{role} {name!r} is not a graph node; expected one of {', '.join(AGENTS)}")
    if kind not in KINDS:
        raise ValueError(f"kind {kind!r} is not one of {', '.join(KINDS)}")
    if not reason or not reason.strip():
        raise ValueError("every message needs a plain-language reason")
    return {"from_agent": from_agent, "to_agent": to_agent, "kind": kind, "reason": reason.strip(),
            "payload": copy.deepcopy(payload or {})}


def handoff(from_agent: str, to_agent: str, reason: str, payload: dict | None = None) -> AgentMessage:
    """Work is done and `to_agent` is the natural next step (a suggestion; the orchestrator decides)."""
    return make_message(from_agent, to_agent, "handoff", reason, payload)


def send_back(from_agent: str, to_agent: str, reason: str, payload: dict | None = None) -> AgentMessage:
    """Ask `to_agent` to redo its work, e.g. Review returning a line to Computation."""
    return make_message(from_agent, to_agent, "send_back", reason, payload)


def escalate(from_agent: str, reason: str, payload: dict | None = None,
             to_agent: str = "human_review") -> AgentMessage:
    """Tell the orchestrator a human is needed. Pair it with `src.hitl.raise_hitl` for the event itself."""
    return make_message(from_agent, to_agent, "escalate", reason, payload)


def info(from_agent: str, to_agent: str, reason: str, payload: dict | None = None) -> AgentMessage:
    """A note for the audit trail that should not change routing."""
    return make_message(from_agent, to_agent, "info", reason, payload)


def as_update(*messages: AgentMessage) -> dict:
    """Wrap messages as a partial state update for the `handoffs` channel."""
    return {"handoffs": list(messages)}


def messages_for(handoffs: list[AgentMessage], to_agent: str, kind: str | None = None) -> list[AgentMessage]:
    """Messages addressed to `to_agent` (optionally of one kind), oldest first."""
    return [m for m in handoffs if m["to_agent"] == to_agent and (kind is None or m["kind"] == kind)]
