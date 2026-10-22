"""Human-in-the-loop events.

# STUB: owned by Guardrails and ethics. Minimal version with the contract signature; the
# owner's real version replaces it.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

CONDITIONS = ["OUT_OF_SCOPE", "DOC_CONFLICT", "R1099_TAXABLE_NOT_DETERMINED",
              "TAX_ADVICE", "FILING_STATUS_AMBIGUITY", "EXPLOITATION",
              "UNCERTAIN", "REVIEW_LIMIT", "LOOP_GUARD"]


def raise_hitl(condition: str, reason: str, raised_by: str, details: dict | None = None) -> dict:
    event = {
        "event_id": f"{condition.lower()}-{uuid.uuid4().hex[:8]}",
        "condition": condition,
        "reason": reason,
        "raised_by": raised_by,
        "status": "open",
        "resolution": None,
        "details": details or {},
        "raised_at": datetime.now(timezone.utc).isoformat(),
    }
    return {"hitl": [event], "status": "awaiting_human"}
