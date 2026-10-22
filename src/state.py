"""Shared graph state and reducers.

Owner: Inter agent communication.
This is the contract every agent reads and writes. Agree on it before building agents.

# STUB: owned by Inter agent communication. Written by Agent memory so the Intake agent
# and memory module can run before PR A lands; the owner's real version replaces it.
# Agent memory adds three OPTIONAL fields (marked below): intake_meta, current_item,
# context_strategy.
"""
from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict

from langgraph.graph.message import add_messages


class Answer(TypedDict):
    value: Any
    raw_text: str            # user's words, PII-redacted
    answered_at: str         # ISO timestamp
    history: list[dict]      # previous values if the user changed their answer


class DocumentRef(TypedDict):
    doc_id: str
    doc_type: str
    path: str


class ExtractedRecord(TypedDict):
    doc_id: str
    doc_type: str
    fields: dict[str, Any]
    extractor: str
    confidence: float
    source_path: str


class Provenance(TypedDict):
    sources: list[dict]
    tool: str
    inputs: dict
    formula: str
    method_note: str
    entered_by: str
    at: str


class LineValue(TypedDict):
    line: str
    value: int
    provenance: Provenance


class HITLEvent(TypedDict):
    event_id: str
    condition: str
    reason: str
    raised_by: str
    status: Literal["open", "resolved"]
    resolution: dict | None


class AgentMessage(TypedDict):
    from_agent: str
    to_agent: str
    kind: Literal["handoff", "send_back", "escalate", "info"]
    reason: str
    payload: dict


def merge_answers(left: dict[str, Answer] | None, right: dict[str, Answer] | None) -> dict[str, Answer]:
    """Merge by question_id; when a value changes, the old version is appended to `history`."""
    merged = dict(left or {})
    for question_id, new in (right or {}).items():
        old = merged.get(question_id)
        history = list(old["history"]) if old else []
        if old is not None and old["value"] != new["value"]:
            history.append(_snapshot(old))
        merged[question_id] = {**new, "history": history}
    return merged


def _snapshot(answer: Answer) -> dict:
    return {"value": answer["value"], "raw_text": answer["raw_text"], "answered_at": answer["answered_at"]}


def merge_documents(left: list[DocumentRef] | None, right: list[DocumentRef] | None) -> list[DocumentRef]:
    by_id = {doc["doc_id"]: doc for doc in (left or [])}
    by_id.update({doc["doc_id"]: doc for doc in (right or [])})
    return [by_id[key] for key in sorted(by_id)]


def merge_extracted(left: dict | None, right: dict | None) -> dict:
    merged = {**(left or {}), **(right or {})}
    return {key: merged[key] for key in sorted(merged)}


def merge_lines(left: dict | None, right: dict | None) -> dict:
    return {**(left or {}), **(right or {})}


def merge_hitl(left: list[HITLEvent] | None, right: list[HITLEvent] | None) -> list[HITLEvent]:
    by_id = {event["event_id"]: event for event in (left or [])}
    by_id.update({event["event_id"]: event for event in (right or [])})
    return list(by_id.values())


class SilverFileState(TypedDict, total=False):
    session_id: str
    status: Literal["intake", "extracting", "rules", "computing", "review",
                    "awaiting_human", "complete", "escalated"]
    messages: Annotated[list, add_messages]
    intake: Annotated[dict[str, Answer], merge_answers]
    intake_complete: bool
    documents: Annotated[list[DocumentRef], merge_documents]
    extracted: Annotated[dict[str, ExtractedRecord], merge_extracted]
    rules: dict
    lines: Annotated[dict[str, LineValue], merge_lines]
    review: dict
    review_iterations: int
    hitl: Annotated[list[HITLEvent], merge_hitl]
    handoffs: Annotated[list[AgentMessage], operator.add]
    route_history: Annotated[list[str], operator.add]
    next_agent: str
    rerun: list[str]
    decisions_summary: str
    token_log: Annotated[list[dict], operator.add]
    outputs: dict
    # Optional additions by Agent memory:
    intake_meta: dict        # Intake bookkeeping: pending question, glossed terms, attempts, drafts
    current_item: str        # batch item id used by selective recall (defaults to session_id)
    context_strategy: bool   # False disables selective recall (run.py --no-context-strategy)
