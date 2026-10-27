"""Turn state into what the session page shows: return lines grouped like the form, each
calculation laid out step by step, and answers as short readable lines instead of JSON.

Owner: Task division and management.

Formulas come from the tax_math tools as one string. Steps are joined with "; ", and an
addition reads "30,000 (line 1z wages) + 300 (line 2b interest) = 30,300". This module only
re-lays that text out; it never recomputes or rounds a number.
"""
from __future__ import annotations

import re

from src.form.labels import (DOC_TYPE_LABELS, FILING_STATUS_LABELS, SCHEDULE_FIELD_LABELS, SECTIONS,
                             describe_source)

_TERM = re.compile(r"^(-?[\d,]+(?:\.\d+)?) \((.+)\)$")
_STEP_NUMBER = re.compile(r"^(\d+)\. (.*)$")
_LINE_SOURCE = re.compile(r"^Line (\S+)$")
_TRAILING_AMOUNT = re.compile(r"^(.*\D) (-?[\d,]+(?:\.\d+)?)$")
# Method notes that only restate the tool; the page already names the tool, so these are dropped.
GENERIC_NOTES = {"Sum of the listed amounts", "Difference of two amounts", "Floor at zero", "Direct user input"}

KEY_LABELS = {"is_taxpayer": "Answering for themselves", "relationship": "Relationship",
              "has_authority": "Has authority to answer", "has_other_income": "Has other income",
              "description": "Description", "described": "Can describe the account", "doc_type": "Type",
              "first_name": "First name", "last_name": "Last name", "dob": "Date of birth", "ssn": "SSN",
              **SCHEDULE_FIELD_LABELS}


# ---------------------------------------------------------------------------
# Return lines
# ---------------------------------------------------------------------------

def line_sections(values: dict) -> list[tuple[str, list[dict]]]:
    """Lines in form order under their 1040-SR section; any line the form map doesn't know goes last."""
    lines, extracted = values.get("lines") or {}, values.get("extracted") or {}
    sections, seen = [], set()
    for title, rows in SECTIONS:
        shown = [line_row(key, label, lines[key], extracted) for key, label in rows if key in lines]
        seen.update(key for key, _ in rows)
        if shown:
            sections.append((title, shown))
    extra = [line_row(key, "", line, extracted) for key, line in lines.items() if key not in seen]
    if extra:
        sections.append(("Other lines", extra))
    return sections


def line_row(key: str, label: str, line: dict, extracted: dict) -> dict:
    provenance = line.get("provenance") or {}
    tool = provenance.get("tool", "")
    sources = [describe_source(s, extracted) for s in provenance.get("sources", []) if isinstance(s, dict)]
    return {"key": key, "label": label, "value": line.get("value"),
            "steps": calculation(provenance.get("formula", "")),
            "sources": join_lines(sources),
            "tool": "entered by the taxpayer" if tool == "direct_user_input" else f"tool: {tool}" if tool else "",
            "method": "; ".join(n for n in str(provenance.get("method_note", "")).split("; ")
                                if n and n not in GENERIC_NOTES)}


def calculation(formula: str) -> list[dict]:
    """Split a formula into steps. An addition of terms becomes a 'sum' step (an adding-machine
    tape); anything else is a 'text' step with its result pulled out to the right."""
    steps = []
    for part in (p.strip() for p in str(formula or "").split("; ")):
        if not part:
            continue
        number, body = (m.group(1), m.group(2)) if (m := _STEP_NUMBER.match(part)) else ("", part)
        expression, eq, result = body.rpartition(" = ")
        if not eq:
            # "Benefits 12,000": a worksheet step whose amount ends the text lines up with the results.
            amount = _TRAILING_AMOUNT.match(body) if number else None
            expression, result = (amount.group(1), amount.group(2)) if amount else (body, "")
        terms = [_TERM.match(t) for t in expression.split(" + ")] if eq else [None]
        if all(terms):
            steps.append({"kind": "sum", "number": number, "result": result,
                          "terms": [(t.group(1), t.group(2)) for t in terms]})
        else:
            steps.append({"kind": "text", "number": number, "text": expression, "result": result})
    return steps


def join_lines(sources: list[str]) -> list[str]:
    """['Line 1z', 'Line 2b', 'W-2 w2_1: box 1'] -> ['Lines 1z, 2b', 'W-2 w2_1: box 1']."""
    out, run = [], []
    for source in sources + [None]:
        match = _LINE_SOURCE.match(source) if source else None
        if match:
            run.append(match.group(1))
            continue
        if run:
            out.append(f"Line {run[0]}" if len(run) == 1 else f"Lines {', '.join(run)}")
            run = []
        if source:
            out.append(source)
    return out


# ---------------------------------------------------------------------------
# Answers
# ---------------------------------------------------------------------------

def readable(value, qid: str = "") -> str | list[str]:
    """An answer as one short string, or a list of short lines for structured answers."""
    if qid == "filing_status" and value in FILING_STATUS_LABELS:
        return FILING_STATUS_LABELS[value]
    if isinstance(value, dict):
        parts = [f"{key_label(k)}: {scalar(v)}" for k, v in value.items() if v not in (None, "")]
        return parts or "—"
    if isinstance(value, list):
        return [document_line(v) if isinstance(v, dict) and "doc_type" in v else
                ", ".join(f"{key_label(k)}: {scalar(i)}" for k, i in v.items() if i not in (None, ""))
                if isinstance(v, dict) else scalar(v) for v in value] or "None"
    return scalar(value)


def document_line(doc: dict) -> str:
    doc_type = DOC_TYPE_LABELS.get(doc.get("doc_type"), doc.get("doc_type") or "Document")
    return f"{doc_type} ({doc.get('doc_id', '?')}): {doc.get('path', '')}"


def scalar(value) -> str:
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if value in (None, ""):
        return "—"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def key_label(key: str) -> str:
    return KEY_LABELS.get(key, key.replace("_", " ").capitalize())
