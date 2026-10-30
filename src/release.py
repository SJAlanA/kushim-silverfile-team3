"""A volunteer's sign-off that releases a finished draft to the taxpayer.

SilverFile never files, and every draft is checked by a person before the taxpayer sees it,
whether or not a HITL event was raised. The staff dashboard calls `release_draft`; the
taxpayer page only serves the form when `is_released` is true.

The release is written to the session's state (as the last step of the finished graph, so no
node runs again) and added to the session's audit.json, so the audit record shows who
released the draft and when.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def is_released(values: dict) -> bool:
    return values.get("status") == "complete" and bool(values.get("release"))


def can_release(values: dict) -> bool:
    """A draft can be released once the run is complete and its form files exist."""
    outputs = values.get("outputs") or {}
    return (values.get("status") == "complete" and not values.get("release")
            and all(outputs.get(kind) and Path(outputs[kind]).is_file() for kind in ("html", "pdf")))


def release_draft(graph, session_id: str, released_by: str, note: str = "") -> dict:
    """Record the sign-off. Raises ValueError when the draft isn't ready or has no name."""
    released_by = released_by.strip()
    if not released_by:
        raise ValueError("Add your name so the audit record shows who released this draft.")
    config = {"configurable": {"thread_id": session_id}}
    values = graph.get_state(config).values
    if not can_release(values):
        raise ValueError("This draft can't be released: it isn't finished, or it was already released.")
    release = {"released_by": released_by, "released_at": datetime.now(timezone.utc).isoformat(),
               "note": note.strip()}
    graph.update_state(config, {"release": release}, as_node="finalize")
    _add_to_audit(values.get("outputs", {}).get("audit"), release)
    return release


def _add_to_audit(location: str | None, release: dict) -> None:
    path = Path(location) if location else None
    if not path or not path.is_file():
        return
    record = json.loads(path.read_text())
    record["release"] = release
    path.write_text(json.dumps(record, indent=2))
