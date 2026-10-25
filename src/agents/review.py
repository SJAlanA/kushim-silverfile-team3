"""Review / Audit agent.

Responsibility: Adversarially checks the assembled return before a human sees it.
Constraint: Can send work back; must not fix things itself.
Owner: Security

Strategy: try to falsify the return. Nothing on it is trusted. Every total is recomputed from
its parts with the tax_math tools (`call_tool`), the document lines are re-summed straight from
the extracted records, and the rule-driven lines (6b, 12e, 13b, 16) are recomputed from the
rules. Anything that disagrees, is missing, is unsourced, is out of scope or came from a
document that tried to instruct the system becomes an issue.

Review never edits `lines` or `rules`. Each issue names the agent that owns the problem; Review
sends one `send_back` message per agent and the orchestrator turns those into reruns (it reads
`send_back` messages into `rerun` itself, so Review does not write `rerun` and nothing runs
twice). The orchestrator also enforces the review bound (MAX_REVIEW_ITERATIONS, then REVIEW_LIMIT).

Output (contract section 3): `review = {"passed", "issues": [{"line", "check", "expected", "found",
"send_to"}], "iteration"}` and `review_iterations`, plus the messages.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from src.agents.computation import LINE_KEYS
from src.agents.eligibility_rules import IN_SCOPE_DOC_TYPES, normalize_doc_type
from src.guardrails import scope_items
from src.hitl import has_event
from src.messages import handoff, send_back
from src.security import validate_ssn
from src.tools.tax_math_client import TaxMathError, call_tool
from src.tools.tracing import session, traced_node

AGENT = "review"
PROVENANCE_KEYS = ("sources", "tool", "inputs", "formula", "method_note", "entered_by", "at")
# Totals a person can check on the paper form: (line, tool, parts). "max0 of subtract" floors at 0.
TOTALS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("1z", "add", ("1a",)),
    ("9", "add", ("1z", "2b", "3b", "4b", "5b", "6b", "8")),
    ("11a", "subtract", ("9", "10")),
    ("14", "add", ("12e", "13b")),
    ("15", "max0_subtract", ("11b", "14")),
    ("18", "add", ("16", "17")),
    ("22", "max0_subtract", ("18", "21")),
    ("24", "add", ("22", "23")),
    ("25d", "add", ("25a", "25b", "25c")),
    ("33", "add", ("25d", "26", "32")),
)
# Lines read straight off documents: line -> {doc_type: field}. Re-summed from `extracted`.
DOCUMENT_LINES: dict[str, dict[str, str]] = {
    "1a": {"W2": "box1_wages"},
    "2a": {"1099-INT": "box8_tax_exempt_interest"},
    "2b": {"1099-INT": "box1_interest"},
    "3a": {"1099-DIV": "box1b_qualified"},
    "3b": {"1099-DIV": "box1a_ordinary"},
    "6a": {"SSA-1099": "box5_net_benefits"},
    "25a": {"W2": "box2_fed_withheld"},
    "25b": {"1099-INT": "box4_fed_withheld", "1099-DIV": "box4_fed_withheld", "1099-R": "box4_fed_withheld",
            "SSA-1099": "box6_fed_withheld"},
}
# (smaller line, larger line): the first can never exceed the second.
NOT_MORE_THAN = (("6b", "6a"), ("4b", "4a"), ("5b", "5a"), ("3a", "3b"))


@dataclass
class Review:
    """What one review pass reads, and the issues it finds."""
    state: dict
    issues: list[dict] = field(default_factory=list)
    tool_calls: int = 0

    @property
    def lines(self) -> dict:
        return self.state.get("lines") or {}

    @property
    def rules(self) -> dict:
        return self.state.get("rules") or {}

    @property
    def extracted(self) -> dict:
        return self.state.get("extracted") or {}

    def value(self, key: str) -> Any:
        return (self.lines.get(key) or {}).get("value")

    def has(self, *keys: str) -> bool:
        """True when every line exists with a whole-dollar value (otherwise other checks already report it)."""
        return all(_is_dollars(self.value(key)) for key in keys)

    def flag(self, line: str | None, check: str, expected: Any, found: Any, send_to: str) -> None:
        self.issues.append({"line": line, "check": check, "expected": expected, "found": found, "send_to": send_to})

    def tool(self, name: str, **kwargs: Any) -> int | None:
        """Recompute with a tax_math tool. A tool error is itself an issue, never a guessed number."""
        self.tool_calls += 1
        try:
            return call_tool(name, **kwargs)["value"]
        except TaxMathError as error:
            self.flag(None, "recompute_failed", f"{name} to run", str(error), "computation")
            return None

    def compare(self, line: str, check: str, expected: Any, send_to: str = "computation") -> None:
        if expected is not None and expected != self.value(line):
            self.flag(line, check, expected, self.value(line), send_to)


def _is_dollars(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_lines_and_provenance(r: Review) -> None:
    """Every contract line exists, is whole dollars, not negative, with a complete provenance record."""
    for key in LINE_KEYS:
        line = r.lines.get(key)
        if line is None:
            r.flag(key, "line_present", "a value with provenance", "missing", "computation")
            continue
        value = line.get("value")
        if not _is_dollars(value):
            r.flag(key, "whole_dollars", "a whole-dollar integer", value, "computation")
        elif value < 0:
            r.flag(key, "not_negative", "0 or more", value, "computation")
        provenance = line.get("provenance") or {}
        missing = [name for name in PROVENANCE_KEYS if name not in provenance
                   or (name != "inputs" and not provenance[name])]
        if missing:
            r.flag(key, "provenance_complete", "sources, tool, inputs, formula, method_note, entered_by, at",
                   f"missing {', '.join(missing)}", "computation")
            continue
        for source in provenance["sources"]:
            problem = _source_problem(r, key, source)
            if problem:
                r.flag(key, "source_exists", "a source that points at a real document field, answer or line",
                       problem, "computation")
        if provenance["tool"] == "direct_user_input" and provenance["entered_by"] != "user":
            r.flag(key, "direct_input_attribution", "entered_by user", provenance["entered_by"], "computation")


def _source_problem(r: Review, key: str, source: Any) -> str | None:
    """Why a provenance source doesn't point at anything real, or None if it does."""
    if not isinstance(source, dict):
        return f"unreadable source {source!r}"
    if "doc_id" in source:
        record = r.extracted.get(source["doc_id"])
        if record is None:
            return f"document {source['doc_id']} was never read"
        absent = [f for f in str(source.get("field", "")).split(",") if f and f not in (record.get("fields") or {})]
        return f"{source['doc_id']} has no field {', '.join(absent)}" if absent else None
    if "intake" in source:
        answered = source["intake"] in (r.state.get("intake") or {})
        return None if answered or source.get("status") == "not answered" else f"no answer to {source['intake']}"
    if "line" in source:
        return None if source["line"] in r.lines else f"line {source['line']} does not exist"
    if "rules" in source:
        return None if source["rules"] in r.rules else f"rules have no {source['rules']}"
    if "none" in source:
        return None if r.value(key) == 0 else "a line with no source must be 0"
    return f"unknown kind of source {sorted(source)}"


def check_totals(r: Review) -> None:
    """Recompute every total from its parts with the tax_math tools."""
    for key, tool, parts in TOTALS:
        if not r.has(key, *parts):
            continue
        values = [r.value(part) for part in parts]
        if tool == "add":
            expected = r.tool("add", values=values, labels=[f"line {part}" for part in parts])
        elif tool == "subtract":
            expected = r.tool("subtract", a=values[0], b=values[1])
        else:
            difference = r.tool("subtract", a=values[0], b=values[1])
            expected = None if difference is None else r.tool("max0", a=difference)
        r.compare(key, "recomputed_total", expected)
    if r.has("24", "33", "34", "37"):
        r.tool_calls += 1
        try:
            result = call_tool("overpayment_or_owed", total_tax=r.value("24"), total_payments=r.value("33"))
        except TaxMathError as error:
            r.flag(None, "recompute_failed", "overpayment_or_owed to run", str(error), "computation")
            return
        for key in ("34", "37"):
            r.compare(key, "recomputed_total", result[key])


def check_contradictions(r: Review) -> None:
    """Relationships that must hold whatever the numbers are."""
    for smaller, larger in NOT_MORE_THAN:
        if r.has(smaller, larger) and r.value(smaller) > r.value(larger):
            r.flag(smaller, "not_more_than", f"at most line {larger} ({r.value(larger):,})", r.value(smaller),
                   "computation")
    if r.has("11a", "11b") and r.value("11a") != r.value("11b"):
        r.flag("11b", "equals_11a", r.value("11a"), r.value("11b"), "computation")
    if r.has("34", "37") and r.value("34") and r.value("37"):
        r.flag("37", "refund_or_owed", "line 34 or line 37, not both", f"34={r.value('34')}, 37={r.value('37')}",
               "computation")


def check_document_lines(r: Review) -> None:
    """Re-sum each document line from the extracted records Eligibility put on the return."""
    mapped = r.rules.get("doc_line_map") or {}
    records = [record for doc_id, record in sorted(r.extracted.items())
               if doc_id in mapped and record.get("extractor") != "failed"]
    for key, fields in DOCUMENT_LINES.items():
        if not r.has(key):
            continue
        items = [(record["fields"].get(fields[normalize_doc_type(record["doc_type"])]) or 0, record["doc_id"])
                 for record in records if normalize_doc_type(record["doc_type"]) in fields]
        if any(not _is_dollars(amount) for amount, _ in items):
            continue  # an unreadable amount is Extraction's to report, not a recompute
        expected = r.tool("add", values=[amount for amount, _ in items], labels=[doc_id for _, doc_id in items])
        r.compare(key, "matches_documents", expected)


def check_rule_lines(r: Review) -> None:
    """Recompute 6b, 12e, 13b and 16 from the rules, and make sure 12e and 13b aren't the same deduction twice."""
    status, boxes, seniors = (r.rules.get(k) for k in ("filing_status", "box_count", "senior_qualifiers"))
    if status is None or boxes is None or seniors is None:
        r.flag(None, "rules_complete", "filing_status, box_count and senior_qualifiers",
               {"filing_status": status, "box_count": boxes, "senior_qualifiers": seniors}, "eligibility")
        return
    counted = r.rules.get("boxes")
    if isinstance(counted, dict) and sum(1 for checked in counted.values() if checked) != boxes:
        r.flag("12e", "box_count_matches_boxes", sum(1 for checked in counted.values() if checked), boxes, "eligibility")
    if r.has("12e"):
        r.compare("12e", "standard_deduction_chart", r.tool("standard_deduction", filing_status=status, box_count=boxes))
    if r.has("13b", "11b"):
        r.compare("13b", "senior_deduction", r.tool("senior_deduction", filing_status=status, magi=r.value("11b"),
                                                    qualifying_count=seniors))
    for key, tool in (("12e", "standard_deduction"), ("13b", "senior_deduction")):
        used = ((r.lines.get(key) or {}).get("provenance") or {}).get("tool")
        if r.has(key) and used != tool:
            r.flag(key, "deduction_not_double_counted", f"computed by {tool}", f"computed by {used}", "computation")
    if r.has("6a", "6b", "1z", "2a", "2b", "3b", "4b", "5b", "8", "10"):
        other = r.tool("add", values=[r.value(k) for k in ("1z", "2b", "3b", "4b", "5b", "8")],
                       labels=["1z", "2b", "3b", "4b", "5b", "8"])
        if other is not None:
            r.compare("6b", "taxable_social_security", r.tool(
                "taxable_social_security", filing_status=status, benefits=r.value("6a"), other_income=other,
                tax_exempt_interest=r.value("2a"), adjustments=r.value("10"),
                lived_with_spouse=bool(r.rules.get("lived_with_spouse", False))))
    if r.has("16", "15", "3a"):
        r.compare("16", "rate_schedule_tax", r.tool("tax_from_rate_schedule", taxable_income=r.value("15"),
                                                    filing_status=status, qualified_dividends=r.value("3a")))
        note = ((r.lines.get("16") or {}).get("provenance") or {}).get("method_note", "")
        if "rate schedule" not in note.lower():
            r.flag("16", "method_note", "names the 2025 rate schedule", note or "empty", "computation")


def check_scope(r: Review) -> None:
    """Nothing out of scope reached the return without a person being asked about it."""
    intake = r.state.get("intake") or {}
    fed: dict[str, list[str]] = {}
    for key, line in r.lines.items():
        for source in (line.get("provenance") or {}).get("sources", []):
            if isinstance(source, dict) and "doc_id" in source:
                fed.setdefault(source["doc_id"], []).append(key)
    for doc_id, record in sorted(r.extracted.items()):
        doc_type = normalize_doc_type(record.get("doc_type"))
        if doc_type not in IN_SCOPE_DOC_TYPES:
            if not has_event(r.state, "OUT_OF_SCOPE", doc_id):
                r.flag(None, "out_of_scope_document", f"{doc_id} ({doc_type}) escalated", "no OUT_OF_SCOPE event",
                       "eligibility")
            for key in fed.get(doc_id, []):
                r.flag(key, "out_of_scope_source", "no amount from an out-of-scope document", doc_id, "computation")
        elif doc_type == "1099-R" and (record.get("fields") or {}).get("box2b_taxable_not_determined") \
                and not has_event(r.state, "R1099_TAXABLE_NOT_DETERMINED", doc_id):
            r.flag(None, "box_2b_escalated", f"{doc_id} escalated", "no R1099_TAXABLE_NOT_DETERMINED event", "eligibility")
    other = (intake.get("other_income") or {}).get("value")
    if isinstance(other, dict) and other.get("has_other_income"):
        items = scope_items(other.get("description", ""), about_income=True)
        if items and not has_event(r.state, "OUT_OF_SCOPE", "other_income"):
            r.flag(None, "out_of_scope_income", f"{', '.join(items)} escalated", "no OUT_OF_SCOPE event", "eligibility")
    if r.rules.get("in_scope") is False and not has_event(r.state, "OUT_OF_SCOPE"):
        r.flag(None, "out_of_scope_return", "an OUT_OF_SCOPE event", "rules say out of scope, nobody was asked",
               "eligibility")


def check_identity(r: Review) -> None:
    """Every SSN on the return is a valid 900-series test number."""
    intake = r.state.get("intake") or {}
    numbers = [(qid, (intake.get(qid) or {}).get("value")) for qid in ("taxpayer_ssn", "spouse_ssn")]
    for index, person in enumerate((intake.get("dependents") or {}).get("value") or []):
        numbers.append((f"dependents[{index}]", (person or {}).get("ssn")))
    for where, number in numbers:
        if number is not None and not validate_ssn(str(number))[0]:
            r.flag(None, "valid_test_ssn", f"{where} is a 900-series test number", "invalid number", "intake")


def check_untrusted_documents(r: Review) -> None:
    """No line may come from a document that tried to instruct the system, unless a volunteer accepted it."""
    for key, line in sorted(r.lines.items()):
        for source in (line.get("provenance") or {}).get("sources", []):
            if not isinstance(source, dict) or "doc_id" not in source:
                continue
            record = r.extracted.get(source["doc_id"]) or {}
            if record.get("flags") and not _volunteer_accepted(r.state, source["doc_id"]):
                r.flag(key, "untrusted_document", "no amount from a document with instruction-like text",
                       f"{source['doc_id']} flagged {', '.join(record['flags'])}", "extract_aggregate")


def _volunteer_accepted(state: dict, doc_id: str) -> bool:
    """A volunteer saw this document's warning and chose to continue."""
    return any((event.get("details") or {}).get("doc_id") == doc_id and event.get("status") == "resolved"
               and (event.get("resolution") or {}).get("decision") == "continue" for event in state.get("hitl", []))


CHECKS: tuple[Callable[[Review], None], ...] = (
    check_lines_and_provenance, check_totals, check_contradictions, check_document_lines, check_rule_lines,
    check_scope, check_identity, check_untrusted_documents,
)


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------

def review_return(state: dict) -> Review:
    """Run every check on the state. Pure apart from tax_math calls; never changes `state`."""
    review = Review(state)
    for check in CHECKS:
        check(review)
    return review


def run(state: dict) -> dict:
    """The `review` node: report issues and send each to its owner, or hand the return to finalize."""
    with session(state.get("session_id")), traced_node(AGENT):
        review = review_return(state)
    iteration = state.get("review_iterations", 0) + 1
    result = {"passed": not review.issues, "issues": review.issues, "iteration": iteration,
              "tool_calls": review.tool_calls}
    return {"review": result, "review_iterations": iteration, "handoffs": _messages(review.issues, review.tool_calls)}


def _messages(issues: list[dict], tool_calls: int) -> list[dict]:
    if not issues:
        return [handoff(AGENT, "finalize", f"Every check passed ({tool_calls} independent tax_math recomputations).",
                        {"tool_calls": tool_calls})]
    by_agent: dict[str, list[dict]] = {}
    for issue in issues:
        by_agent.setdefault(issue["send_to"], []).append(issue)
    messages = []
    for agent, found in by_agent.items():
        lines = sorted({i["line"] for i in found if i["line"]}, key=lambda k: LINE_KEYS.index(k) if k in LINE_KEYS else 99)
        summary = "; ".join(_describe(i) for i in found[:5]) + ("; ..." if len(found) > 5 else "")
        messages.append(send_back(AGENT, agent, f"Review found {len(found)} problem(s): {summary}",
                                  {"lines": lines, "issues": found}))
    return messages


def _describe(issue: dict) -> str:
    where = f"line {issue['line']} " if issue["line"] else ""
    return f"{where}{issue['check'].replace('_', ' ')} (expected {issue['expected']}, found {issue['found']})"
