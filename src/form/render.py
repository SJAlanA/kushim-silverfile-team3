"""Form 1040-SR outputs for a finished session (contract section 14).

Owner: Security.

    render_all(state) -> {"html": path, "pdf": path, "audit": path}

writes to `outputs/<session_id>/`:
- form_1040sr.html: the draft with click/hover provenance on every amount (src/form/html_form.py)
- form_1040sr.pdf: the official IRS fillable form, filled and watermarked (src/form/pdf_form.py)
- audit.json: every line with provenance, cited rules, HITL events and review rounds (src/form/audit.py)

`finalize` calls this once Review has passed (or a volunteer chose to continue past the review
limit), so it reports what is on the return and never changes it.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from src.form.audit import build_audit
from src.form.html_form import render_html
from src.form.pdf_form import fill_pdf

OUTPUT_DIR = Path("outputs")
HTML_NAME, PDF_NAME, AUDIT_NAME = "form_1040sr.html", "form_1040sr.pdf", "audit.json"


def session_folder(state: dict) -> Path:
    """outputs/<session_id>, with the id reduced to safe characters so it can't escape the folder."""
    session_id = re.sub(r"[^A-Za-z0-9._-]", "_", str(state.get("session_id") or "session")).lstrip(".") or "session"
    return OUTPUT_DIR / session_id


def render_all(state: dict) -> dict:
    """Write the HTML form, the filled PDF and audit.json; return their paths."""
    folder = session_folder(state)
    folder.mkdir(parents=True, exist_ok=True)
    outputs = {"html": str(folder / HTML_NAME), "pdf": str(folder / PDF_NAME), "audit": str(folder / AUDIT_NAME)}
    (folder / HTML_NAME).write_text(render_html(state), encoding="utf-8")
    fill_pdf(state, folder / PDF_NAME)
    audit = build_audit(state, outputs)
    (folder / AUDIT_NAME).write_text(json.dumps(audit, indent=2, default=str) + "\n", encoding="utf-8")
    return outputs
