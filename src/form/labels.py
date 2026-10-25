"""Words for the form: 1040-SR sections and line labels, and plain-language provenance.

Owner: Security.

Shared by the HTML form, the PDF and the audit record so all three describe a number the same
way. Labels follow the 2025 Form 1040-SR; line keys are contract section 6.
"""
from __future__ import annotations

from datetime import datetime

from src.agents.intake_questions import BY_ID

FILING_STATUS_LABELS = {"single": "Single", "mfj": "Married filing jointly", "mfs": "Married filing separately",
                        "hoh": "Head of household", "qss": "Qualifying surviving spouse"}

# (section title, [(line, label), ...]) in the order the lines appear on Form 1040-SR.
SECTIONS: list[tuple[str, list[tuple[str, str]]]] = [
    ("Income", [
        ("1a", "Total amount from Form(s) W-2, box 1"),
        ("1z", "Add lines 1a through 1h"),
        ("2a", "Tax-exempt interest"),
        ("2b", "Taxable interest"),
        ("3a", "Qualified dividends"),
        ("3b", "Ordinary dividends"),
        ("4a", "IRA distributions"),
        ("4b", "IRA distributions, taxable amount"),
        ("5a", "Pensions and annuities"),
        ("5b", "Pensions and annuities, taxable amount"),
        ("6a", "Social security benefits"),
        ("6b", "Social security benefits, taxable amount"),
        ("8", "Additional income from Schedule 1, line 10"),
        ("9", "Total income"),
        ("10", "Adjustments to income from Schedule 1, line 26"),
        ("11a", "Adjusted gross income"),
        ("11b", "Amount from line 11a (adjusted gross income)"),
        ("12e", "Standard deduction"),
        ("13b", "Additional deductions from Schedule 1-A (enhanced senior deduction)"),
        ("14", "Add lines 12e, 13a and 13b"),
        ("15", "Taxable income"),
    ]),
    ("Tax and Credits", [
        ("16", "Tax"),
        ("17", "Amount from Schedule 2, line 3"),
        ("18", "Add lines 16 and 17"),
        ("21", "Add lines 19 and 20 (credits)"),
        ("22", "Subtract line 21 from line 18"),
        ("23", "Other taxes, from Schedule 2, line 21"),
        ("24", "Total tax"),
    ]),
    ("Payments", [
        ("25a", "Federal income tax withheld from Form(s) W-2"),
        ("25b", "Federal income tax withheld from Form(s) 1099"),
        ("25c", "Federal income tax withheld from other forms"),
        ("25d", "Total federal income tax withheld"),
        ("26", "2025 estimated tax payments"),
        ("32", "Total other payments and refundable credits"),
        ("33", "Total payments"),
    ]),
    ("Refund / Amount you owe", [
        ("34", "Amount you overpaid (refund)"),
        ("37", "Amount you owe"),
    ]),
]
LINE_LABELS = {line: label for _, rows in SECTIONS for line, label in rows}

DOC_TYPE_LABELS = {"W2": "W-2", "1099-INT": "1099-INT", "1099-DIV": "1099-DIV", "1099-R": "1099-R",
                   "SSA-1099": "SSA-1099"}
FIELD_LABELS = {
    "box1_wages": "box 1, wages", "box2_fed_withheld": "box 2, federal tax withheld",
    "box1_interest": "box 1, interest", "box4_fed_withheld": "box 4, federal tax withheld",
    "box8_tax_exempt_interest": "box 8, tax-exempt interest", "box1a_ordinary": "box 1a, ordinary dividends",
    "box1b_qualified": "box 1b, qualified dividends", "box1_gross": "box 1, gross distribution",
    "box2a_taxable": "box 2a, taxable amount", "qcd_amount": "qualified charitable distribution",
    "box5_net_benefits": "box 5, net benefits", "box6_fed_withheld": "box 6, federal tax withheld",
}
RULE_LABELS = {"filing_status": "filing status", "box_count": "line 12d box count",
               "senior_qualifiers": "people 65 or older for Schedule 1-A"}
SCHEDULE_FIELD_LABELS = {"sch1_line10": "Schedule 1, line 10", "sch2_line3": "Schedule 2, line 3",
                         "sch2_line21": "Schedule 2, line 21", "sch3_line8": "Schedule 3, line 8",
                         "sch3_line15": "Schedule 3, line 15"}


def money(value: int) -> str:
    return f"{value:,}"


def friendly_time(value: str) -> str:
    """'2026-10-25T14:03:11+00:00' -> '25 Oct 2026, 14:03 UTC'. Anything unparseable is returned as is."""
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return str(value or "")
    zone = "UTC" if moment.utcoffset() is not None and not moment.utcoffset() else moment.strftime("%z")
    return f"{moment.day} {moment:%b %Y, %H:%M} {zone}".strip()


def describe_source(source: dict, extracted: dict) -> str:
    """One provenance source in plain words, e.g. 'W-2 w2_1: box 1, wages' or 'Line 9'."""
    if "doc_id" in source:
        record = extracted.get(source["doc_id"]) or {}
        doc_type = DOC_TYPE_LABELS.get(record.get("doc_type"), record.get("doc_type") or "Document")
        fields = [FIELD_LABELS.get(f, f) for f in str(source.get("field", "")).split(",") if f]
        return f"{doc_type} {source['doc_id']}: {' and '.join(fields)}"
    if "intake" in source:
        topic = BY_ID[source["intake"]].topic if source["intake"] in BY_ID else source["intake"]
        part = SCHEDULE_FIELD_LABELS.get(source.get("field"), source.get("field"))
        status = " (not answered, so 0)" if source.get("status") == "not answered" else ""
        return f"Taxpayer's answer about {topic}{f', {part}' if part else ''}{status}"
    if "line" in source:
        return f"Line {source['line']}"
    if "rules" in source:
        return f"Eligibility rules: {RULE_LABELS.get(source['rules'], source['rules'])}"
    if "none" in source:
        return str(source["none"])
    return "Unknown source"


def describe_provenance(line: dict, extracted: dict) -> dict:
    """A line's provenance as display strings: formula, sources, tool, method, who and when."""
    provenance = line.get("provenance") or {}
    tool = provenance.get("tool", "")
    who = "the taxpayer" if provenance.get("entered_by") == "user" else f"the {provenance.get('entered_by', '?')} agent"
    return {
        "formula": provenance.get("formula", ""),
        "sources": [describe_source(s, extracted) for s in provenance.get("sources", []) if isinstance(s, dict)],
        "tool": "Direct user input" if tool == "direct_user_input" else f"tax_math tool: {tool}",
        "method": provenance.get("method_note", ""),
        "entered": f"Entered by {who}, {friendly_time(provenance.get('at', ''))}",
    }
