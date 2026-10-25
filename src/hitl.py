"""Human-in-the-loop events.

Owner: Guardrails and ethics.

An event is raised by returning `raise_hitl(...)` as part of a node's update. The orchestrator
sees the open event and routes to `human_review`, which pauses on a LangGraph interrupt until a
Kushim volunteer resolves it (in the dashboard, or from a case file).

Event shape (contract section 7, plus `details` and `raised_at`):

    {"event_id", "condition", "reason", "raised_by", "status": "open" | "resolved",
     "resolution": None | {"decision", "note", "resolved_by", "resolved_at", "updates"},
     "details": {...}, "raised_at": ISO timestamp}

`details` always holds a dict. Keys other code reads: `fingerprint` (orchestrator), `doc_id`
and `issue` (Extraction), and `question_id` (Intake). Eligibility also writes `rule`, the policy
rule that triggered the event, so a volunteer can read what it means.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

CONDITIONS = ["OUT_OF_SCOPE", "DOC_CONFLICT", "R1099_TAXABLE_NOT_DETERMINED",
              "TAX_ADVICE", "FILING_STATUS_AMBIGUITY", "EXPLOITATION",
              "UNCERTAIN", "REVIEW_LIMIT", "LOOP_GUARD"]

DECISIONS = ("continue", "stop")
SEND_BACK = "send_back:"
# `details` keys that say what an event is about. The first one present is the event's subject.
SUBJECT_KEYS = ("doc_id", "question_id", "subject")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def raise_hitl(condition: str, reason: str, raised_by: str, details: dict | None = None) -> dict:
    """Build the state update that opens one HITL event.

    `reason` is plain language a Kushim employee can act on: what happened and why a person
    is needed, with no jargon and no blame. Raises ValueError for an unknown condition so a
    typo fails at the sender instead of reaching the dashboard.
    """
    if condition not in CONDITIONS:
        raise ValueError(f"condition {condition!r} is not one of {', '.join(CONDITIONS)}")
    if not reason or not reason.strip():
        raise ValueError("every HITL event needs a plain-language reason")
    event = {
        "event_id": f"{condition.lower()}-{uuid.uuid4().hex[:8]}",
        "condition": condition,
        "reason": reason.strip(),
        "raised_by": raised_by,
        "status": "open",
        "resolution": None,
        "details": dict(details or {}),
        "raised_at": _now(),
    }
    return {"hitl": [event], "status": "awaiting_human"}


def merge_updates(*updates: dict) -> dict:
    """Combine several `raise_hitl` updates into one: all events kept, in order."""
    events = [event for update in updates for event in update.get("hitl", [])]
    return {"hitl": events, "status": "awaiting_human"} if events else {}


def subject_of(event: dict) -> str | None:
    """What an event is about (a document id, a question id or a named subject), if it says."""
    details = event.get("details") or {}
    return next((str(details[key]) for key in SUBJECT_KEYS if details.get(key)), None)


def has_event(state: dict, condition: str, subject: str | None = None) -> bool:
    """True if the state already holds an event (open or resolved) for this condition and subject.

    Agents that rerun after a volunteer's edit use this so the same issue is never raised twice.
    With `subject=None` any event of the condition counts.
    """
    return any(event["condition"] == condition and (subject is None or subject_of(event) == subject)
               for event in state.get("hitl", []))


def resolve_event(event: dict, resume_payload: dict) -> dict:
    """The resolved copy of `event` for a volunteer's resume payload.

    Payload: {"decision": "continue" | "stop" | "send_back:<agent>", "note", "resolved_by",
    "updates": {question_id: value}}. The decision is checked here; `send_back:<agent>` is
    accepted for any agent name and checked against the real node names by the graph.
    """
    decision = str(resume_payload.get("decision") or "")
    if decision not in DECISIONS and not (decision.startswith(SEND_BACK) and decision[len(SEND_BACK):]):
        allowed = ", ".join([*DECISIONS, f"{SEND_BACK}<agent>"])
        raise ValueError(f"decision {decision!r} is not one of {allowed}")
    resolution = {"decision": decision, "note": resume_payload.get("note", ""),
                  "resolved_by": resume_payload.get("resolved_by", ""), "resolved_at": _now(),
                  "updates": dict(resume_payload.get("updates") or {})}
    return {**event, "status": "resolved", "resolution": resolution}
