"""Eligibility decision table: the source of truth for what the return's rules are.

Owner: Guardrails and ethics.

`decide()` is a pure function of the taxpayer's answers and the documents. It does no I/O and no
arithmetic (box counts are counts of booleans; dollars are Computation's job), so the whole
table is testable without a graph. Every finding it returns names a rule ID from data/policy.md.
The Eligibility agent (eligibility.py) wraps this with state, HITL dedupe and the optional
model cross-check.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from src.guardrails import OUT_OF_SCOPE_RULES, scope_items

# R-ELIG-01 / R-SD-02: "65 or older in 2025" and "may use 1040-SR" are the same birth-date test.
SENIOR_BORN_BEFORE = date(1961, 1, 2)
FILING_STATUSES = ("single", "mfj", "mfs", "hoh", "qss")
IN_SCOPE_DOC_TYPES = ("W2", "1099-INT", "1099-DIV", "1099-R", "SSA-1099")
# Which line family each in-scope document feeds (a 1099-R depends on its IRA flag, see _family).
DOC_FAMILY = {"W2": "1a", "1099-INT": "2b", "1099-DIV": "3b", "SSA-1099": "6a"}
# Out-of-scope document types and the rule that excludes them; anything else falls to R-SCOPE-11.
OUT_OF_SCOPE_DOC_RULES = {"1099-NEC": "R-SCOPE-04", "1099-K": "R-SCOPE-04", "SCHEDULE C": "R-SCOPE-04",
                          "SCHEDULE E": "R-SCOPE-05", "1099-B": "R-SCOPE-06", "1099-S": "R-SCOPE-06",
                          "SCHEDULE D": "R-SCOPE-06", "1099-DA": "R-SCOPE-07", "SCHEDULE A": "R-SCOPE-03",
                          "1040-X": "R-SCOPE-09"}
SCHEDULE_ANSWERS = {"1": ("adjustments", "sch1_line10"), "2": ("sch2_line3", "sch2_line21"),
                    "3": ("sch3_line8", "sch3_line15")}
SCHEDULE_VALUES = "schedule_values"  # Intake keeps the Schedule 1-3 amounts together in this one answer


@dataclass(frozen=True)
class Escalation:
    """One reason a person must look at the return. `subject_key` says what `subject` names."""
    condition: str
    reason: str
    rule: str
    subject: str
    subject_key: str = "subject"      # "doc_id" | "question_id" | "subject"
    extra: dict = field(default_factory=dict)

    def details(self) -> dict:
        return {self.subject_key: self.subject, "rule": self.rule, "citation": citation(self.rule), **self.extra}


@dataclass
class Decision:
    rules: dict
    escalations: list[Escalation]


def citation(rule_id: str) -> str:
    return f"policy.md#{rule_id.lower()}"


def finding(rule_id: str, detail: str, passed: bool = True, **extra: Any) -> dict:
    """One cited finding (contract section 8). `passed` is False when the rule found a problem or could not run."""
    return {"rule_id": rule_id, "citation": citation(rule_id), "passed": passed, "detail": detail, **extra}


def parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def is_senior(dob: date | None) -> bool | None:
    """True if born before 2 January 1961, None if the date of birth isn't known."""
    return None if dob is None else dob < SENIOR_BORN_BEFORE


def normalize_doc_type(doc_type: str) -> str:
    return str(doc_type or "").strip().upper().replace("W-2", "W2")


# ---------------------------------------------------------------------------
# Filing status (R-FS-*)
# ---------------------------------------------------------------------------

def check_filing_status(answers: dict, findings: list[dict], escalations: list[Escalation]) -> str | None:
    """The validated filing status, or None if it is missing or invalid (and why, in findings)."""
    status = answers.get("filing_status")
    if status is None:
        findings.append(finding("R-FS-01", "No filing status has been given yet, so none is set.", passed=False))
        return None
    if status not in FILING_STATUSES:
        escalations.append(Escalation(
            "FILING_STATUS_AMBIGUITY", "The filing status on file isn't one of the five SilverFile handles "
            "(single, joint, separate, head of household, surviving spouse), so a person needs to confirm it.",
            "R-FS-01", "filing_status", "question_id"))
        return None
    findings.append(finding("R-FS-01", f"Filing status {status} is one of the five in scope."))
    if answers.get("spouse_died_in_2025") is True:
        escalations.append(Escalation(
            "FILING_STATUS_AMBIGUITY", "The taxpayer's spouse died during 2025. Joint, qualifying surviving "
            "spouse and single can all look possible, and the right one depends on facts SilverFile doesn't "
            "collect (remarriage, a dependent child). A volunteer needs to choose and record the filing "
            "status.", "R-FS-03", "spouse_died_in_2025", "question_id"))
    if status in ("qss", "hoh") and answers.get("dependents") == []:
        label = "qualifying surviving spouse" if status == "qss" else "head of household"
        escalations.append(Escalation(
            "FILING_STATUS_AMBIGUITY", f"The filing status is {label}, which normally needs a dependent, but "
            "none was listed. A volunteer needs to confirm the status or the dependents.",
            "R-FS-04" if status == "qss" else "R-FS-05", "filing_status", "question_id"))
    return status


# ---------------------------------------------------------------------------
# Eligibility, boxes and senior qualifiers (R-ELIG-01, R-SD-02, R-SR-01)
# ---------------------------------------------------------------------------

def check_eligibility(answers: dict, status: str | None, findings: list[dict],
                      escalations: list[Escalation]) -> bool | None:
    """Whether the taxpayer (or, on a joint return, either spouse) may use Form 1040-SR."""
    taxpayer = is_senior(parse_date(answers.get("taxpayer_dob")))
    spouse = is_senior(parse_date(answers.get("spouse_dob"))) if status == "mfj" else None
    if taxpayer or spouse:
        who = "The taxpayer" if taxpayer else "The spouse"
        findings.append(finding("R-ELIG-01", f"{who} was born before 2 January 1961, so Form 1040-SR may be used."))
        return True
    unknown = taxpayer is None or (status == "mfj" and spouse is None)
    if unknown:
        findings.append(finding("R-ELIG-01", "A date of birth is missing, so 1040-SR eligibility is not decided.", passed=False))
        return None
    escalations.append(Escalation(
        "OUT_OF_SCOPE", "Nobody on this return was born before 2 January 1961, so Form 1040-SR can't be used. "
        "SilverFile only drafts the 1040-SR.", "R-ELIG-01", "taxpayer_dob", "question_id"))
    findings.append(finding("R-ELIG-01", "Nobody on the return was born before 2 January 1961.", passed=False))
    return False


def count_boxes(answers: dict, status: str | None, findings: list[dict]) -> tuple[dict | None, int | None]:
    """The line 12d boxes and how many are checked. (None, None) if something needed is missing."""
    spouse_on_return = status in ("mfj", "mfs")
    taxpayer_65 = is_senior(parse_date(answers.get("taxpayer_dob")))
    spouse_65 = is_senior(parse_date(answers.get("spouse_dob"))) if spouse_on_return else False
    taxpayer_blind = answers.get("taxpayer_blind")
    spouse_blind = answers.get("spouse_blind") if spouse_on_return else False
    if status is None or None in (taxpayer_65, spouse_65, taxpayer_blind, spouse_blind):
        findings.append(finding("R-SD-02", "A birth date or eyesight answer is missing, so the line 12d boxes "
                                           "are not counted.", passed=False))
        return None, None
    boxes = {"tp_65": taxpayer_65, "tp_blind": bool(taxpayer_blind),
             "sp_65": spouse_65, "sp_blind": bool(spouse_blind)}
    count = sum(1 for checked in boxes.values() if checked)
    findings.append(finding("R-SD-02", f"{count} of the 4 boxes on line 12d are checked.", boxes=boxes))
    if status == "mfs" and spouse_on_return and (spouse_65 or spouse_blind):
        findings.append(finding("R-SD-02", "On a separate return the spouse's boxes were counted as the brief "
                                           "says. A volunteer should confirm the spouse qualifies for them."))
    return boxes, count


def count_senior_qualifiers(answers: dict, status: str | None, findings: list[dict]) -> int | None:
    """People who qualify for the Schedule 1-A senior deduction: 0 for MFS, spouse counted on MFJ only."""
    if status is None:
        return None
    if status == "mfs":
        findings.append(finding("R-SR-01", "Separate filers can't take the Schedule 1-A senior deduction."))
        return 0
    people = [is_senior(parse_date(answers.get("taxpayer_dob")))]
    if status == "mfj":
        people.append(is_senior(parse_date(answers.get("spouse_dob"))))
    if None in people:
        findings.append(finding("R-SR-01", "A birth date is missing, so senior qualifiers are not counted.", passed=False))
        return None
    qualifiers = sum(1 for person in people if person)
    findings.append(finding("R-SR-01", f"{qualifiers} person(s) on the return are 65 or older and qualify."))
    return qualifiers


# ---------------------------------------------------------------------------
# Standard deduction chart and other-income scope (R-SD-04, R-SCOPE-*)
# ---------------------------------------------------------------------------

def check_chart_conditions(answers: dict, status: str | None, findings: list[dict],
                           escalations: list[Escalation]) -> None:
    """The three cases where the Standard Deduction Chart does not apply."""
    cases = [("can_be_claimed_as_dependent", "The taxpayer can be claimed as someone else's dependent"),
             ("spouse_itemizes_separately", "The spouse is itemizing on a separate return"),
             ("dual_status_alien", "The taxpayer was a dual-status alien in 2025")]
    for question_id, what in cases:
        if answers.get(question_id) is True and (question_id != "spouse_itemizes_separately" or status == "mfs"):
            escalations.append(Escalation(
                "OUT_OF_SCOPE", f"{what}, so the Standard Deduction Chart doesn't apply and SilverFile can't "
                "work out the deduction.", "R-SD-04", question_id, "question_id"))
            findings.append(finding("R-SD-04", f"{what}; the chart does not apply.", passed=False))


def check_other_income(answers: dict, findings: list[dict], escalations: list[Escalation]) -> None:
    """Decide scope from the 'other income' answer. Intake only records it."""
    other = answers.get("other_income")
    if not isinstance(other, dict) or not other.get("has_other_income"):
        return
    items = scope_items(other.get("description", ""), about_income=True)
    if items:
        rules = [OUT_OF_SCOPE_RULES[item] for item in items]
        escalations.append(Escalation(
            "OUT_OF_SCOPE", f"The taxpayer reported other income that SilverFile doesn't handle ({', '.join(items)}).",
            rules[0], "other_income", "question_id", {"items": items, "rules": rules}))
        findings.extend(finding(rule, f"Other income: {item} is out of scope.", passed=False) for item, rule in zip(items, rules))
    else:
        escalations.append(Escalation(
            "UNCERTAIN", "The taxpayer said there was other income, but SilverFile can't tell what kind or whether "
            "it belongs on the return. A volunteer needs to decide.", "R-SCOPE-12", "other_income", "question_id"))
        findings.append(finding("R-SCOPE-12", "Other income was reported that could not be classified.", passed=False))


# ---------------------------------------------------------------------------
# Documents (R-SCOPE-01, R-SCOPE-11, R-1099R-01, R-DOC-01)
# ---------------------------------------------------------------------------

def _doc_types(documents: list[dict], extracted: dict) -> dict[str, str]:
    """doc_id -> normalized type, from the taxpayer's list and the readings (a reading wins)."""
    types = {doc["doc_id"]: normalize_doc_type(doc.get("doc_type")) for doc in documents}
    for doc_id, record in extracted.items():
        types[doc_id] = normalize_doc_type(record.get("doc_type") or types.get(doc_id))
    return types


def _family(doc_type: str, record: dict | None) -> str | None:
    if doc_type in DOC_FAMILY:
        return DOC_FAMILY[doc_type]
    if doc_type == "1099-R" and record is not None:
        return "4a" if record.get("fields", {}).get("ira_sep_simple") else "5a"
    return None


def check_documents(documents: list[dict], extracted: dict, findings: list[dict],
                    escalations: list[Escalation]) -> dict[str, str]:
    """Scope-check every document and return `doc_line_map` (in-scope, readable documents only)."""
    doc_line_map: dict[str, str] = {}
    for doc_id, doc_type in _doc_types(documents, extracted).items():
        record = extracted.get(doc_id)
        readable = record is not None and record.get("extractor") != "failed"
        if doc_type not in IN_SCOPE_DOC_TYPES:
            rule = OUT_OF_SCOPE_DOC_RULES.get(doc_type, "R-SCOPE-11")
            escalations.append(Escalation(
                "OUT_OF_SCOPE", f"Document {doc_id} is a {doc_type or 'form of unknown type'}, which SilverFile "
                "doesn't handle.", rule, doc_id, "doc_id", {"doc_type": doc_type}))
            findings.append(finding(rule, f"{doc_id} ({doc_type or 'unknown'}) is out of scope.", passed=False))
            continue
        if doc_type == "1099-R" and readable and record["fields"].get("box2b_taxable_not_determined"):
            escalations.append(Escalation(
                "R1099_TAXABLE_NOT_DETERMINED", f"1099-R {doc_id} has box 2b 'taxable amount not determined' "
                "checked. Working out the taxable part needs the Simplified Method or General Rule, which "
                "SilverFile doesn't do, so a person must take this case.", "R-1099R-01", doc_id, "doc_id"))
            findings.append(finding("R-1099R-01", f"{doc_id} has box 2b checked; the taxable amount is not known.", passed=False))
        family = _family(doc_type, record if readable else None)
        if family:
            doc_line_map[doc_id] = family
            findings.append(finding("R-SCOPE-01", f"{doc_id} ({doc_type}) is in scope and feeds line {family}."))
    return doc_line_map


def _words(name: str) -> list[str]:
    """First-to-last name words, lower case, no punctuation ('Brandt, Theo R.' -> ['theo', 'r', 'brandt'])."""
    if "," in name:
        last, _, first = name.partition(",")
        name = f"{first} {last}"
    return re.findall(r"[a-z]+", name.lower())


def name_matches(full_name: str, first: str, last: str) -> bool | None:
    """First and last words match, ignoring case and middle names. None if either side is unknown."""
    words = _words(full_name or "")
    if not words or not _words(first or "") or not _words(last or ""):
        return None
    return words[0] == _words(first)[0] and words[-1] == _words(last)[-1]


def _last4(ssn: Any) -> str | None:
    digits = re.sub(r"\D", "", str(ssn or ""))
    return digits[-4:] if len(digits) >= 4 else None


def check_conflicts(answers: dict, status: str | None, extracted: dict, findings: list[dict],
                    escalations: list[Escalation]) -> None:
    """A W-2 must belong to the taxpayer, or on a joint return to either spouse (R-DOC-01)."""
    people = [(answers.get("taxpayer_first_name"), answers.get("taxpayer_last_name"), answers.get("taxpayer_ssn"))]
    if status == "mfj":
        people.append((answers.get("spouse_first_name"), answers.get("spouse_last_name"), answers.get("spouse_ssn")))
    for doc_id, record in extracted.items():
        if record.get("extractor") == "failed" or normalize_doc_type(record.get("doc_type")) != "W2":
            continue
        fields = record.get("fields", {})
        results = [_person_matches(fields, first, last, ssn) for first, last, ssn in people]
        if None in results or [] in results:
            continue  # someone couldn't be compared (Intake already flagged what's missing), or someone matches
        mismatches = min(results, key=len)
        escalations.append(Escalation(
            "DOC_CONFLICT", f"W-2 {doc_id} doesn't match what the taxpayer gave: its {' and '.join(mismatches)} "
            f"{'differs' if len(mismatches) == 1 else 'differ'}. SilverFile can't tell which is right, so a volunteer needs to check.", "R-DOC-01", doc_id,
            "doc_id", {"issue": "mismatch", "fields": mismatches}))
        findings.append(finding("R-DOC-01", f"W-2 {doc_id} conflicts with the taxpayer's answers "
                                            f"({', '.join(mismatches)}).", passed=False))


def _person_matches(fields: dict, first: Any, last: Any, ssn: Any) -> list[str] | None:
    """What differs between a W-2 and one person: [] if it matches, None if nothing was comparable."""
    name_ok = name_matches(fields.get("employee_name", ""), first or "", last or "")
    document_last4, given_last4 = _last4(fields.get("employee_ssn_last4")), _last4(ssn)
    ssn_ok = None if document_last4 is None or given_last4 is None else document_last4 == given_last4
    checks = {"employee name": name_ok, "Social Security number": ssn_ok}
    if all(result is None for result in checks.values()):
        return None
    return [problem for problem, result in checks.items() if result is False]


# ---------------------------------------------------------------------------
# Schedules and the whole table
# ---------------------------------------------------------------------------

def applicable_schedules(answers: dict, senior_qualifiers: int | None) -> list[str]:
    """Schedules the return needs: 1-A for any senior qualifier; 1, 2, 3 when the taxpayer typed in amounts."""
    schedules = ["1-A"] if senior_qualifiers else []
    typed = answers.get(SCHEDULE_VALUES) if isinstance(answers.get(SCHEDULE_VALUES), dict) else {}
    values = {**typed, "adjustments": answers.get("adjustments")}
    for number, keys in SCHEDULE_ANSWERS.items():
        if any(isinstance(values.get(key), int) and values[key] != 0 for key in keys):
            schedules.append(number)
    return schedules


def decide(answers: dict, documents: list[dict], extracted: dict) -> Decision:
    """Run the whole table. `answers` maps question_id to the answer's value."""
    findings: list[dict] = []
    escalations: list[Escalation] = []
    status = check_filing_status(answers, findings, escalations)
    eligible = check_eligibility(answers, status, findings, escalations)
    boxes, box_count = count_boxes(answers, status, findings)
    qualifiers = count_senior_qualifiers(answers, status, findings)
    check_chart_conditions(answers, status, findings, escalations)
    check_other_income(answers, findings, escalations)
    doc_line_map = check_documents(documents, extracted, findings, escalations)
    check_conflicts(answers, status, extracted, findings, escalations)
    rules = {
        "in_scope": not any(e.condition == "OUT_OF_SCOPE" for e in escalations),
        "eligible_1040sr": eligible,
        "filing_status": status,
        "box_count": box_count,
        "boxes": boxes,
        "senior_qualifiers": qualifiers,
        "standard_deduction_chart_applies": not any(e.rule == "R-SD-04" for e in escalations),
        "schedules": applicable_schedules(answers, qualifiers),
        "doc_line_map": doc_line_map,
        "findings": findings,
    }
    return Decision(rules, escalations)
