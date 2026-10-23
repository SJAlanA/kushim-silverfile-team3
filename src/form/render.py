"""Form 1040-SR rendering: HTML form, PDF and audit record.

# STUB: owned by the area that owns src/form/ in the contract (section 2). Minimal version
# with the contract signature: it writes only audit.json; the owner's real version replaces it.
"""
from __future__ import annotations

import json
from pathlib import Path

OUTPUT_DIR = Path("outputs")


def render_all(state: dict) -> dict:
    """Write the outputs for one session and return their paths as {"html", "pdf", "audit"}."""
    folder = OUTPUT_DIR / state.get("session_id", "session")
    folder.mkdir(parents=True, exist_ok=True)
    audit = folder / "audit.json"
    audit.write_text(json.dumps({"lines": state.get("lines", {}), "hitl": state.get("hitl", [])},
                                indent=2, default=str))
    return {"html": "", "pdf": "", "audit": str(audit)}
