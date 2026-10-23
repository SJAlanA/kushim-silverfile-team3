"""Shared graph state and reducers.

Owner: Inter agent communication.

This module is the contract every agent reads and writes (prompts/00_shared_contract.md,
section 3). Agents return partial updates; LangGraph folds them into the state with the
reducers below.

Every reducer is pure and order-independent: the result depends only on the *set* of writes,
never on the order in which parallel branches finish. That matters because the Send fan-out
in the extraction step runs `extract_one` branches concurrently, and the review loop can
re-run agents that rewrite the same keys. Ties are broken by a canonical JSON encoding, so
even two writes with identical timestamps resolve the same way every time.

Contract additions (all optional, nothing renamed):
- Answer: `_versions` (written only by merge_answers).
- ExtractedRecord: `flags`, `warnings`, `_conflicts`.
- HITLEvent: `details`, `raised_at` (already written by src/hitl.py).
- SilverFileState: `intake_meta`, `current_item`, `context_strategy` (added by Agent memory).
- SilverFileState: `routing` (added by Task division and management; orchestrator bookkeeping).
"""
from __future__ import annotations

import json
import operator
from typing import Annotated, Any, Literal, NotRequired, TypedDict

from langgraph.graph.message import add_messages


class Answer(TypedDict):
    value: Any
    raw_text: str            # user's words, PII-redacted
    answered_at: str         # ISO timestamp
    history: list[dict]      # previous values if the user changed their answer
    _versions: NotRequired[list[dict]]  # reducer-owned lossless log that `history` is derived from


class DocumentRef(TypedDict):
    doc_id: str              # e.g. "w2_1", "ssa_1"
    doc_type: str            # "W2" | "1099-INT" | "1099-DIV" | "1099-R" | "SSA-1099" | other (e.g. "1099-NEC")
    path: str                # image (.jpg/.png) or .json fixture


class ExtractedRecord(TypedDict):
    doc_id: str
    doc_type: str
    fields: dict[str, Any]   # contract section 5
    extractor: str           # "vision" | "ocr" | "json" | "failed"
    confidence: float
    source_path: str
    flags: NotRequired[list[str]]        # prompt-injection patterns found in the document's text
    warnings: NotRequired[list[str]]     # plain-language notes for Review and the Kushim employee
    _conflicts: NotRequired[list[dict]]  # other readings of the same doc_id that disagreed


class Provenance(TypedDict):
    sources: list[dict]      # [{"doc_id": "w2_1", "field": "box1_wages"}] or [{"intake": "estimated_payments"}]
    tool: str                # tax_math tool name, or "direct_user_input"
    inputs: dict
    formula: str             # human-readable, e.g. "30,000 + 300 + 100 + 6,540 = 36,940"
    method_note: str         # e.g. "2025 rate schedule, qualified dividends at 0%"
    entered_by: str          # agent name or "user"
    at: str                  # ISO timestamp


class LineValue(TypedDict):
    line: str                # "1z", "6b", ... (contract section 6)
    value: int               # whole dollars
    provenance: Provenance


class HITLEvent(TypedDict):
    event_id: str
    condition: str           # contract section 7
    reason: str              # plain-language for the Kushim employee
    raised_by: str
    status: Literal["open", "resolved"]
    resolution: dict | None  # {"decision", "note", "resolved_by", "resolved_at", "updates"}
    details: NotRequired[dict]
    raised_at: NotRequired[str]


class AgentMessage(TypedDict):
    from_agent: str
    to_agent: str
    kind: Literal["handoff", "send_back", "escalate", "info"]
    reason: str
    payload: dict


# ---------------------------------------------------------------------------
# Reducers
# ---------------------------------------------------------------------------

def _canonical(obj: Any) -> str:
    """Stable text form of any JSON-like value, used for dedupe keys and tie-breaks."""
    return json.dumps(obj, sort_keys=True, default=str)


def _snapshot(answer: dict) -> dict:
    return {"value": answer.get("value"), "raw_text": answer.get("raw_text", ""),
            "answered_at": answer.get("answered_at", "")}


def _answer_versions(answer: dict) -> list[dict]:
    """Every version an Answer knows about: its full version log, its history and its current value."""
    entries = answer.get("_versions", []) + answer.get("history", []) + [answer]
    return [_snapshot(entry) for entry in entries]


def _merge_one_answer(old: dict, new: dict) -> Answer:
    """Fold two writes of the same question into one Answer.

    The reducer keeps a lossless `_versions` log (every distinct version, oldest first) and
    derives the visible fields from it: the latest version is current, and `history` holds
    each earlier value that was later changed. A version immediately followed by the same
    value is a repeat, not a change, so it stays in `_versions` but not in `history`.

    Deriving from a lossless log is what makes this order-independent: dropping the repeat
    outright would lose information a later merge needs, so the outcome would depend on
    which writes had already been folded together.
    """
    versions = {_canonical(v): v for v in _answer_versions(old) + _answer_versions(new)}
    ordered = sorted(versions.values(), key=lambda v: (v["answered_at"], _canonical(v)))
    current = ordered[-1]
    history = [v for v, nxt in zip(ordered, ordered[1:]) if _canonical(v["value"]) != _canonical(nxt["value"])]
    source = new if _snapshot(new) == current else old
    extras = {k: v for k, v in source.items() if k not in ("value", "raw_text", "answered_at", "history", "_versions")}
    return {**extras, **current, "history": history, "_versions": ordered}


def merge_answers(left: dict[str, Answer] | None, right: dict[str, Answer] | None) -> dict[str, Answer]:
    """Merge intake answers by question_id. Never deletes a key; a changed value goes to `history`."""
    merged = dict(left or {})
    for question_id, new in (right or {}).items():
        merged[question_id] = _merge_one_answer(merged.get(question_id, new), new)
    return merged


def merge_documents(left: list[DocumentRef] | None, right: list[DocumentRef] | None) -> list[DocumentRef]:
    """Dedupe by doc_id, keeping first-seen order; a later entry for the same doc_id updates it in place."""
    by_id: dict[str, DocumentRef] = {}
    for doc in (left or []) + (right or []):
        by_id[doc["doc_id"]] = doc  # dicts keep insertion order, so the first position is kept
    return list(by_id.values())


_EXTRACTOR_RANK = {"vision": 3, "ocr": 2, "json": 1, "failed": 0}


def _record_rank(record: dict) -> tuple:
    """Total order over readings of one document: confidence, then extractor, then content."""
    return (float(record.get("confidence", 0.0)), _EXTRACTOR_RANK.get(record.get("extractor", ""), 0),
            _canonical(_without_conflicts(record)))


def _without_conflicts(record: dict) -> dict:
    return {k: v for k, v in record.items() if k != "_conflicts"}


def _merge_one_record(a: dict, b: dict) -> ExtractedRecord:
    """Keep the higher-ranked reading; record readings with different fields under `_conflicts`.

    Candidates are every reading either side has seen (including earlier conflicts). The
    winner is the top-ranked one; for every other distinct set of fields the best reading is
    kept as a conflict. This is a pure function of the candidate set, so it is commutative
    and associative, which is what makes concurrent `extract_one` writes safe.
    """
    candidates = [_without_conflicts(r) for r in (a, b)]
    candidates += [c for r in (a, b) for c in r.get("_conflicts", [])]
    winner = max(candidates, key=_record_rank)
    best_per_fields: dict[str, dict] = {}
    for candidate in candidates:
        key = _canonical(candidate.get("fields", {}))
        if key == _canonical(winner.get("fields", {})):
            continue
        if key not in best_per_fields or _record_rank(candidate) > _record_rank(best_per_fields[key]):
            best_per_fields[key] = candidate
    merged = dict(winner)
    if best_per_fields:
        merged["_conflicts"] = sorted(best_per_fields.values(), key=_record_rank, reverse=True)
    return merged


def merge_extracted(left: dict[str, ExtractedRecord] | None,
                    right: dict[str, ExtractedRecord] | None) -> dict[str, ExtractedRecord]:
    """Merge extracted records by doc_id. Output is sorted by doc_id so it never depends on arrival order."""
    merged = dict(left or {})
    for doc_id, record in (right or {}).items():
        merged[doc_id] = _merge_one_record(merged[doc_id], record) if doc_id in merged else record
    return {doc_id: merged[doc_id] for doc_id in sorted(merged)}


def _line_rank(line: dict) -> tuple:
    return (line.get("provenance", {}).get("at", ""), _canonical(line))


def merge_lines(left: dict[str, LineValue] | None, right: dict[str, LineValue] | None) -> dict[str, LineValue]:
    """Merge return lines by line key; the value with the newer `provenance.at` wins."""
    merged = dict(left or {})
    for key, line in (right or {}).items():
        if key not in merged or _line_rank(line) > _line_rank(merged[key]):
            merged[key] = line
    return merged


def _hitl_rank(event: dict) -> tuple:
    """A resolved event beats its open version; otherwise the later resolution or raise wins."""
    resolution = event.get("resolution") or {}
    return (event.get("status") == "resolved", resolution.get("resolved_at", ""),
            event.get("raised_at", ""), _canonical(event))


def merge_hitl(left: list[HITLEvent] | None, right: list[HITLEvent] | None) -> list[HITLEvent]:
    """Upsert HITL events by event_id, keeping first-seen order. A resolved event is never reopened."""
    by_id: dict[str, HITLEvent] = {}
    for event in (left or []) + (right or []):
        event_id = event["event_id"]
        if event_id not in by_id or _hitl_rank(event) > _hitl_rank(by_id[event_id]):
            by_id[event_id] = event
    return list(by_id.values())


class SilverFileState(TypedDict, total=False):
    session_id: str
    status: Literal["intake", "extracting", "rules", "computing", "review",
                    "awaiting_human", "complete", "escalated"]
    messages: Annotated[list, add_messages]                             # intake dialogue only
    intake: Annotated[dict[str, Answer], merge_answers]                 # keyed by question_id
    intake_complete: bool
    documents: Annotated[list[DocumentRef], merge_documents]            # dedupe by doc_id
    extracted: Annotated[dict[str, ExtractedRecord], merge_extracted]   # keyed by doc_id; deterministic
    rules: dict                                                         # contract section 8
    lines: Annotated[dict[str, LineValue], merge_lines]                 # keyed by line
    review: dict                                                        # {"passed", "issues", "iteration"}
    review_iterations: int
    hitl: Annotated[list[HITLEvent], merge_hitl]                        # upsert by event_id
    handoffs: Annotated[list[AgentMessage], operator.add]
    route_history: Annotated[list[str], operator.add]
    next_agent: str                                                     # ONLY the orchestrator writes this
    rerun: list[str]                                                    # agents the orchestrator must re-run
    decisions_summary: str                                              # rolling summary for the context strategy
    token_log: Annotated[list[dict], operator.add]                      # {"agent", "item", "input_tokens", "strategy"}
    outputs: dict                                                       # {"html", "pdf", "audit"}
    # Optional additions by Agent memory:
    intake_meta: dict        # Intake bookkeeping: pending question, glossed terms, attempts, drafts
    current_item: str        # batch item id used by selective recall (defaults to session_id)
    context_strategy: bool   # False disables selective recall (run.py --no-context-strategy)
    # Optional addition by Task division and management (written by orchestrator and human_review only):
    routing: dict            # {"inputs_seen": {agent: hash}, "handoff_cursor": int, "trail": [[agent, hash]]}
