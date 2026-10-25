"""audit.json: the complete, PII-safe record of how a draft return was produced.

Owner: Security.

Holds every line with its provenance, the rules and their cited findings, the documents and how
each was read, every HITL event with its resolution, and the review result plus every review
round's messages. SSNs appear only as ***-**-1234, and every string passes through
`redact_pii` as a last line of defence.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.form.labels import FILING_STATUS_LABELS
from src.security import mask_ssn, redact_pii


def _answer(state: dict, qid: str):
    return ((state.get("intake") or {}).get(qid) or {}).get("value")


def _scrub(value: Any) -> Any:
    """Redact every string in a JSON-like value."""
    if isinstance(value, str):
        return redact_pii(value)
    if isinstance(value, dict):
        return {key: _scrub(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return value


def _taxpayer(state: dict) -> dict:
    status = (state.get("rules") or {}).get("filing_status")
    people = {"taxpayer": {"first_name": _answer(state, "taxpayer_first_name"),
                           "last_name": _answer(state, "taxpayer_last_name"),
                           "ssn": mask_ssn(_answer(state, "taxpayer_ssn"))}}
    if status in ("mfj", "mfs"):
        people["spouse"] = {"first_name": _answer(state, "spouse_first_name"),
                            "last_name": _answer(state, "spouse_last_name"), "ssn": mask_ssn(_answer(state, "spouse_ssn"))}
    people["dependents"] = [{"first_name": p.get("first_name"), "last_name": p.get("last_name"),
                             "ssn": mask_ssn(p.get("ssn")), "relationship": p.get("relationship")}
                            for p in _answer(state, "dependents") or []]
    people["filing_status"] = {"code": status, "label": FILING_STATUS_LABELS.get(status)}
    return people


def _documents(state: dict) -> list[dict]:
    extracted = state.get("extracted") or {}
    return [{"doc_id": doc["doc_id"], "doc_type": doc.get("doc_type"),
             "extractor": (extracted.get(doc["doc_id"]) or {}).get("extractor", "not read"),
             "confidence": (extracted.get(doc["doc_id"]) or {}).get("confidence"),
             "fields": (extracted.get(doc["doc_id"]) or {}).get("fields", {}),
             "flags": (extracted.get(doc["doc_id"]) or {}).get("flags", []),
             "warnings": (extracted.get(doc["doc_id"]) or {}).get("warnings", [])}
            for doc in state.get("documents") or []]


def build_audit(state: dict, outputs: dict) -> dict:
    """The audit record for one session (a JSON-ready dict)."""
    review_rounds = [m for m in state.get("handoffs", []) if m.get("from_agent") == "review"]
    record = {
        "session_id": state.get("session_id"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "notice": "CLASSROOM DRAFT - DO NOT FILE",
        "status": "complete",  # written by the finalize node, which marks the session complete as it returns
        "people": _taxpayer(state),
        "documents": _documents(state),
        "rules": state.get("rules") or {},
        "lines": state.get("lines") or {},
        "hitl": state.get("hitl") or [],
        "review": {"result": state.get("review") or {}, "iterations": state.get("review_iterations", 0),
                   "rounds": review_rounds},
        "route": state.get("route_history") or [],
        "outputs": outputs,
    }
    return _scrub(record)
