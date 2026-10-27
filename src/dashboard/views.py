"""Turn state into what the draft page shows, in words a volunteer or a taxpayer can follow.

Owner: Task division and management.

Each return line gets one plain sentence saying where its amount came from ("Copied from W-2
(w2_1), box 1: Wages, tips, other compensation"). The full calculation is kept, laid out step
by step, for anyone who opens it. Formulas come from the tax_math tools as one string: steps
are joined with "; ", and an addition reads "30,000 (line 1z wages) + 300 (line 2b interest)
= 30,300". This module only re-lays that text out; it never recomputes or rounds a number.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from src.agents.intake_questions import BY_ID
from src.form.labels import DOC_TYPE_LABELS, FILING_STATUS_LABELS, SCHEDULE_FIELD_LABELS, SECTIONS

_TERM = re.compile(r"^(-?[\d,]+(?:\.\d+)?) \((.+)\)$")
_STEP_NUMBER = re.compile(r"^(\d+)\. (.*)$")
_TRAILING_AMOUNT = re.compile(r"^(.*\D) (-?[\d,]+(?:\.\d+)?)$")
# "w2_1 W-2 box 1" or "div_1 1099-DIV withheld", as Computation writes a document term.
_DOC_TERM = re.compile(r"^(\w+) (W-?2|1099-INT|1099-DIV|1099-R|SSA-1099) (box (\w+)|withheld)$")
# Method notes that only restate what the plain sentence already says.
GENERIC_NOTES = {"Sum of the listed amounts", "Difference of two amounts", "Floor at zero", "Direct user input",
                 "Line 33 compared with line 24"}

KEY_LABELS = {"is_taxpayer": "Answering for themselves", "relationship": "Relationship",
              "has_authority": "Has authority to answer", "has_other_income": "Has other income",
              "description": "Description", "described": "Can describe the account", "doc_type": "Type",
              "first_name": "First name", "last_name": "Last name", "dob": "Date of birth", "ssn": "SSN",
              **SCHEDULE_FIELD_LABELS}

# Figures a reviewer checks first, in the order they read down the form.
KEY_FIGURES = (("9", "Total income"), ("11a", "Adjusted gross income"), ("24", "Total tax"),
               ("33", "Total payments"))

# Every value read from a document: the box it is printed in and the name the form gives that box.
BOXES = {
    "box1_wages": ("1", "Wages, tips, other compensation"),
    "box2_fed_withheld": ("2", "Federal income tax withheld"),
    "box1_interest": ("1", "Interest income"),
    "box4_fed_withheld": ("4", "Federal income tax withheld"),
    "box8_tax_exempt_interest": ("8", "Tax-exempt interest"),
    "box1a_ordinary": ("1a", "Total ordinary dividends"),
    "box1b_qualified": ("1b", "Qualified dividends"),
    "box1_gross": ("1", "Gross distribution"),
    "box2a_taxable": ("2a", "Taxable amount"),
    "box2b_taxable_not_determined": ("2b", "Taxable amount not determined"),
    "box7_code": ("7", "Distribution code"),
    "box5_net_benefits": ("5", "Net benefits for 2025"),
    "box6_fed_withheld": ("6", "Voluntary federal income tax withheld"),
    "ira_sep_simple": ("", "IRA/SEP/SIMPLE box checked"),
    "qcd_amount": ("", "Qualified charitable distribution"),
    "employee_name": ("", "Employee's name"),
    "employee_ssn_last4": ("", "Employee's SSN, last 4 digits"),
    "employer_name": ("", "Employer's name"),
}
# The same box names keyed by form and box number, to rewrite "W-2 box 1" inside a calculation.
_BOX_BY_NUMBER = {("W-2", "1"): "box1_wages", ("W-2", "2"): "box2_fed_withheld",
                  ("1099-INT", "1"): "box1_interest", ("1099-INT", "4"): "box4_fed_withheld",
                  ("1099-INT", "8"): "box8_tax_exempt_interest", ("1099-DIV", "1a"): "box1a_ordinary",
                  ("1099-DIV", "1b"): "box1b_qualified", ("1099-DIV", "4"): "box4_fed_withheld",
                  ("1099-R", "1"): "box1_gross", ("1099-R", "2a"): "box2a_taxable", ("1099-R", "4"): "box4_fed_withheld",
                  ("SSA-1099", "5"): "box5_net_benefits", ("SSA-1099", "6"): "box6_fed_withheld"}
_WITHHELD_BOX = {"W-2": "2", "1099-INT": "4", "1099-DIV": "4", "1099-R": "4", "SSA-1099": "6"}
_RULES = {"filing_status": "the filing status", "box_count": "the number of boxes checked on line 12d",
          "senior_qualifiers": "how many people on the return are 65 or older"}


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def outcome(values: dict) -> dict | None:
    """The bottom line of the return: a refund, an amount owed, or neither. None before line 34/37 exist."""
    lines = values.get("lines") or {}
    refund, owed = (lines.get(key, {}).get("value") for key in ("34", "37"))
    if refund is None and owed is None:
        return None
    if refund:
        return {"kind": "refund", "label": "Refund", "line": "34", "value": refund}
    if owed:
        return {"kind": "owed", "label": "Amount owed", "line": "37", "value": owed}
    return {"kind": "even", "label": "No refund and nothing owed", "line": "34", "value": 0}


def key_figures(values: dict) -> list[dict]:
    lines = values.get("lines") or {}
    return [{"line": key, "label": label, "value": lines[key]["value"]} for key, label in KEY_FIGURES if key in lines]


# ---------------------------------------------------------------------------
# Return lines
# ---------------------------------------------------------------------------

def line_sections(values: dict) -> list[dict]:
    """Lines in form order under their 1040-SR section; any line the form map doesn't know goes last.

    A section's lines that come to 0 are listed apart (`zero_rows`), so the page shows the lines
    that carry amounts and folds the empty ones away without hiding them.
    """
    lines, extracted = values.get("lines") or {}, values.get("extracted") or {}
    groups, seen = [], set()
    for title, rows in SECTIONS:
        groups.append((title, [line_row(key, label, lines[key], extracted) for key, label in rows if key in lines]))
        seen.update(key for key, _ in rows)
    groups.append(("Other lines", [line_row(key, "", line, extracted) for key, line in lines.items() if key not in seen]))
    return [{"title": title, "rows": [r for r in rows if r["value"] != 0],
             "zero_rows": [r for r in rows if r["value"] == 0]} for title, rows in groups if rows]


def line_row(key: str, label: str, line: dict, extracted: dict) -> dict:
    provenance = line.get("provenance") or {}
    sources = [s for s in provenance.get("sources", []) if isinstance(s, dict)]
    steps = calculation(provenance.get("formula", ""))
    return {"key": key, "label": label, "value": line.get("value"),
            "how": how(provenance.get("tool", ""), sources, extracted),
            "steps": steps, "worked": any(s["kind"] == "text" or len(s.get("terms", [])) > 1 for s in steps),
            "method": "; ".join(n for n in str(provenance.get("method_note", "")).split("; ")
                                if n and n not in GENERIC_NOTES)}


def how(tool: str, sources: list[dict], extracted: dict) -> str:
    """One plain sentence on where a line's amount came from."""
    named = [describe(s, extracted) for s in sources if "none" not in s]
    lines = [s["line"] for s in sources if "line" in s]
    if not named:
        return "Nothing was reported for this line, so it is 0."
    if tool == "direct_user_input" or all("intake" in s for s in sources):
        asked = join([n.replace("their answer about ", "", 1) for n in named])
        return f"Entered by the taxpayer when asked about {asked}."
    if tool == "overpayment_or_owed":
        return ("Total payments (line 33) minus total tax (line 24). Paying more than the tax gives a refund "
                "on line 34; paying less leaves an amount owed on line 37.")
    if tool == "add":
        return f"Copied from {named[0]}." if len(named) == 1 else f"Added up {join(named)}."
    if tool in ("subtract", "max0") and len(lines) == 2:
        floor = ", and never less than 0" if tool == "max0" else ""
        return f"Line {lines[0]} minus line {lines[1]}{floor}."
    sentences = {
        "taxable_social_security": "Worked out with the IRS Social Security Benefits Worksheet, which decides "
                                   "how much of the benefits are taxed, using {}.",
        "standard_deduction": "Looked up in the 2025 standard deduction chart, using {}.",
        "senior_deduction": "The extra deduction for people 65 or older (Schedule 1-A), using {}.",
        "tax_from_rate_schedule": "Tax on the taxable income from the 2025 tax rate schedule, with qualified "
                                  "dividends taxed at their lower rate, using {}.",
    }
    return sentences.get(tool, "Worked out from {}.").format(join(named))


def describe(source: dict, extracted: dict) -> str:
    """A provenance source in plain words: 'W-2 (w2_1), box 1: Wages, tips, other compensation'."""
    if "doc_id" in source:
        record = extracted.get(source["doc_id"]) or {}
        boxes = [box_label(f) for f in str(source.get("field", "")).split(",") if f]
        return f"{doc_type_label(record.get('doc_type'))} ({source['doc_id']}), {' and '.join(boxes)}"
    if "intake" in source:
        topic = BY_ID[source["intake"]].topic if source["intake"] in BY_ID else source["intake"]
        part = SCHEDULE_FIELD_LABELS.get(source.get("field"), source.get("field"))
        status = " (not answered, so 0)" if source.get("status") == "not answered" else ""
        return f"their answer about {topic}{f' ({part})' if part else ''}{status}"
    if "line" in source:
        return f"line {source['line']}"
    if "rules" in source:
        return _RULES.get(source["rules"], source["rules"].replace("_", " "))
    return "an unknown source"


def box_label(field: str) -> str:
    """'box1_wages' -> 'box 1: Wages, tips, other compensation'; a field with no box keeps its name."""
    number, name = BOXES.get(field, ("", field.replace("_", " ").capitalize()))
    return f"box {number}: {name}" if number else name


def join(parts: list[str]) -> str:
    """'line 1z, line 2b and line 3b' reads as 'lines 1z, 2b and 3b'."""
    if parts and all(p.startswith("line ") for p in parts) and len(parts) > 1:
        numbers = [p[5:] for p in parts]
        return f"lines {', '.join(numbers[:-1])} and {numbers[-1]}"
    return parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])} and {parts[-1]}"


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
                          "terms": [(t.group(1), term_label(t.group(2))) for t in terms]})
        else:
            steps.append({"kind": "text", "number": number, "text": expression, "result": result})
    return steps


def term_label(text: str) -> str:
    """'w2_1 W-2 box 1' -> 'W-2 (w2_1), box 1: Wages, tips, other compensation'. Other terms are kept."""
    match = _DOC_TERM.match(text)
    if not match:
        return text
    doc_type = "W-2" if match.group(2) in ("W2", "W-2") else match.group(2)
    number = match.group(4) or _WITHHELD_BOX[doc_type]
    field = _BOX_BY_NUMBER.get((doc_type, number))
    return f"{doc_type} ({match.group(1)}), {box_label(field) if field else f'box {number}'}"


# ---------------------------------------------------------------------------
# Documents and review
# ---------------------------------------------------------------------------

def documents(values: dict) -> list[dict]:
    """Each document read: its values by box, and which file they were read from and how."""
    rows = []
    for doc_id, record in (values.get("extracted") or {}).items():
        fields = [(box_label(name)[0].upper() + box_label(name)[1:], field_value(value))
                  for name, value in (record.get("fields") or {}).items()]
        rows.append({"doc_id": doc_id, "type": doc_type_label(record.get("doc_type")), "fields": fields,
                     "file": Path(record.get("source_path") or "").name, "how": read_how(record.get("extractor")),
                     "confidence": round(float(record.get("confidence", 0)) * 100),
                     "warnings": record.get("warnings") or []})
    return rows


def read_how(extractor: str | None) -> str:
    return {"vision": "Read from the image by Claude", "ocr": "Read from the image with text recognition (OCR)",
            "json": "Taken from the data file", "failed": "Could not be read"}.get(extractor or "", extractor or "")


def review_summary(values: dict) -> dict | None:
    """Review's verdict in plain words, and only the notes a reader needs (what was sent back and why)."""
    review = values.get("review")
    if not review:
        return None
    rounds = values.get("review_iterations") or review.get("iteration") or 1
    when = "on the first check" if rounds == 1 else f"after {rounds} checks"
    if review.get("passed"):
        text = (f"Passed {when}. SilverFile worked out every line a second time, separately from the first, "
                "and got the same numbers.")
    else:
        text = f"Found problems {when}."
    sent_back = [m for m in values.get("handoffs", []) if m["from_agent"] == "review" and m["kind"] == "send_back"]
    return {"passed": bool(review.get("passed")), "text": text,
            "issues": [i if isinstance(i, str) else str(i) for i in review.get("issues") or []],
            "sent_back": [{"to": m["to_agent"], "reason": m["reason"]} for m in sent_back]}


def field_value(value) -> str:
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value) if value not in (None, "") else "Blank"


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
    """'W-2 (w2_1)'. The file path is left out: it means nothing to a volunteer."""
    return f"{doc_type_label(doc.get('doc_type'))} ({doc.get('doc_id', '?')})"


def doc_type_label(doc_type: str | None) -> str:
    return DOC_TYPE_LABELS.get(doc_type, doc_type or "Document")


def scalar(value) -> str:
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if value in (None, ""):
        return "—"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, str) and (day := _iso_date(value)):
        return f"{day:%B} {day.day}, {day.year}"
    return str(value)


def _iso_date(text: str) -> date | None:
    """A date answer stored as YYYY-MM-DD, read for display as 'June 21, 1958'."""
    try:
        return date.fromisoformat(text) if len(text) == 10 else None
    except ValueError:
        return None


def key_label(key: str) -> str:
    return KEY_LABELS.get(key, key.replace("_", " ").capitalize())
