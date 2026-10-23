"""Computation agent.

Responsibility: Populates every derived line (contract section 6).
Constraint: No arithmetic itself; every number comes from a tax_math MCP tool call.
Owner: MLOps/DevOps

How it works
------------
`STEPS` is the explicit computation order. Each step names the lines it produces and the lines
it reads, and the order is checked when this module is imported: every contract line is
produced exactly once, and no step reads a line that a later step produces. That is how the
Social Security ordering dependency (brief 3.2 rule 4) is enforced: line 6b reads 1z, 2b, 3b,
4b, 5b, 8 and 10, and line 9 reads 6b, so 6b can only run between them.

Every dollar value comes from `call_tool` (or, for amounts the taxpayer typed in, is recorded
as `direct_user_input` with the answer's timestamp). This module never adds, subtracts or
compares amounts itself: it only reads numbers from documents and answers and passes them to
tools. Conversions like "30000" -> 30000 are parsing, not arithmetic.

Inputs it relies on, all decided by other agents:
- `rules` from Eligibility: filing_status, box_count, senior_qualifiers, doc_line_map.
  Without the first three it raises UNCERTAIN instead of guessing.
- `extracted` records (contract section 5) and `intake` answers (contract section 4).

When Review sends work back with `{"lines": [...]}`, only the steps producing those lines and
everything downstream of them run again; the rest keep their current values.

Contract notes (additions, nothing renamed):
- Provenance `sources` may contain `{"line": "<key>"}` when a line is built from other lines.
- Provenance `inputs` for a line built from several tool calls holds the final call's inputs
  plus `"steps"`, the earlier calls in order; `formula` joins every call's formula with "; ".
- `rules["lived_with_spouse"]` (optional bool, default False) is read for MFS filers on the
  Social Security worksheet.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from src.hitl import raise_hitl
from src.messages import as_update, escalate, handoff, messages_for
from src.tools.tax_math_client import call_tool
from src.tools.tracing import session, traced_node

AGENT = "computation"

LINE_KEYS: tuple[str, ...] = ("1a", "1z", "2a", "2b", "3a", "3b", "4a", "4b", "5a", "5b", "6a", "6b", "8", "9",
                              "10", "11a", "11b", "12e", "13b", "14", "15", "16", "17", "18", "21", "22", "23",
                              "24", "25a", "25b", "25c", "25d", "26", "32", "33", "34", "37")

# Which line family a document feeds when Eligibility's doc_line_map doesn't name it.
DEFAULT_FAMILY = {"W2": "1a", "1099-INT": "2b", "1099-DIV": "3b", "SSA-1099": "6a"}
# Federal tax withheld: W-2 goes on 25a, every 1099 (including SSA-1099) on 25b.
WITHHOLDING_FIELD = {"W2": ("25a", "box2_fed_withheld"), "1099-INT": ("25b", "box4_fed_withheld"),
                     "1099-DIV": ("25b", "box4_fed_withheld"), "1099-R": ("25b", "box4_fed_withheld"),
                     "SSA-1099": ("25b", "box6_fed_withheld")}

Item = tuple[int, str, dict]  # (amount, label for the formula, provenance source)


class NeedsHuman(Exception):
    """Raised inside a step when Computation must stop and hand the case to a person."""

    def __init__(self, condition: str, reason: str, details: dict | None = None):
        super().__init__(reason)
        self.condition, self.reason, self.details = condition, reason, details or {}


# ---------------------------------------------------------------------------
# Context: everything a step may read
# ---------------------------------------------------------------------------

@dataclass
class Context:
    filing_status: str
    box_count: int
    senior_qualifiers: int
    lived_with_spouse: bool
    docs: dict[str, list[dict]]           # line family ("1a", "4a", ...) -> extracted records
    answers: dict[str, dict]              # intake answers by question_id
    lines: dict[str, dict] = field(default_factory=dict)   # current LineValues (old + new)
    calls: int = 0

    def tool(self, name: str, **kwargs: Any) -> dict:
        self.calls += 1
        return call_tool(name, **kwargs)

    def line_item(self, key: str, label: str) -> Item:
        return self.lines[key]["value"], f"line {key} {label}".strip(), {"line": key}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _amount(value: Any, where: str) -> int:
    """Parse a whole-dollar amount from a document field or answer. Missing means 0."""
    if value is None or value == "":
        return 0
    if isinstance(value, bool):
        raise NeedsHuman("UNCERTAIN", f"Expected a dollar amount for {where}, got yes/no.", {"where": where})
    if isinstance(value, int):
        parsed = value
    else:
        text = str(value).replace(",", "").replace("$", "").strip()
        if not text.isdigit():
            raise NeedsHuman("UNCERTAIN", f"Could not read a whole-dollar amount for {where}: {value!r}.",
                             {"where": where})
        parsed = int(text)
    if parsed < 0:
        raise NeedsHuman("UNCERTAIN", f"A negative amount was given for {where}: {parsed}.", {"where": where})
    return parsed


# ---------------------------------------------------------------------------
# Building LineValues
# ---------------------------------------------------------------------------

def _line(key: str, results: list[dict], sources: list[dict]) -> dict:
    """A LineValue from one or more tool results; the last result is the line's value."""
    final = results[-1]
    inputs = dict(final["inputs"])
    if len(results) > 1:
        inputs["steps"] = [{"tool": r["tool"], "inputs": r["inputs"], "value": r["value"]} for r in results[:-1]]
    return {"line": key, "value": final["value"], "provenance": {
        "sources": sources, "tool": final["tool"], "inputs": inputs,
        "formula": "; ".join(r["formula"] for r in results),
        "method_note": "; ".join(dict.fromkeys(r["method_note"] for r in results)),
        "entered_by": AGENT, "at": _now()}}


def _sum(ctx: Context, key: str, items: list[Item], before: list[dict] | None = None) -> dict:
    """Line = sum of items, via the `add` tool."""
    result = ctx.tool("add", values=[i[0] for i in items], labels=[i[1] for i in items])
    sources = [i[2] for i in items] or [{"none": f"No document or answer reports an amount for line {key}"}]
    return _line(key, [*(before or []), result], sources)


def _doc_items(ctx: Context, family: str, field_name: str, label: str) -> list[Item]:
    return [(_amount(rec["fields"].get(field_name), f"{rec['doc_id']} {field_name}"), f"{rec['doc_id']} {label}",
             {"doc_id": rec["doc_id"], "field": field_name}) for rec in ctx.docs.get(family, [])]


def _answer(ctx: Context, question_id: str, sub: str | None = None) -> tuple[dict | None, Any]:
    answer = ctx.answers.get(question_id)
    if answer is None:
        return None, None
    value = answer.get("value")
    return answer, (value or {}).get(sub) if sub else value


def _direct(ctx: Context, key: str, question_id: str, sub: str | None, label: str) -> dict:
    """A line the taxpayer typed in. Recorded as direct_user_input with the answer's timestamp.

    If the question was never answered, the line is 0 from the `add` tool with no amounts, so it
    still has a provenance record and is never blank.
    """
    source = {"intake": question_id, **({"field": sub} if sub else {})}
    answer, raw = _answer(ctx, question_id, sub)
    if answer is None:
        line = _sum(ctx, key, [])
        line["provenance"]["sources"] = [{**source, "status": "not answered"}]
        return line
    value = _amount(raw, label)
    return {"line": key, "value": value, "provenance": {
        "sources": [source], "tool": "direct_user_input", "inputs": {question_id: answer.get("value")},
        "formula": f"Entered by the taxpayer: {value:,} ({label})", "method_note": "Direct user input",
        "entered_by": "user", "at": answer.get("answered_at") or _now()}}


def _answer_item(ctx: Context, question_id: str, sub: str, label: str) -> list[Item]:
    """An answered amount as an input to a tool (empty if the question wasn't answered)."""
    answer, raw = _answer(ctx, question_id, sub)
    if answer is None:
        return []
    return [(_amount(raw, label), label, {"intake": question_id, "field": sub})]


# ---------------------------------------------------------------------------
# Steps (in order)
# ---------------------------------------------------------------------------

def _wages(ctx: Context) -> dict:
    line_1a = _sum(ctx, "1a", _doc_items(ctx, "1a", "box1_wages", "W-2 box 1"))
    ctx.lines["1a"] = line_1a
    return {"1a": line_1a, "1z": _sum(ctx, "1z", [ctx.line_item("1a", "wages")])}


def _interest(ctx: Context) -> dict:
    return {"2a": _sum(ctx, "2a", _doc_items(ctx, "2b", "box8_tax_exempt_interest", "1099-INT box 8")),
            "2b": _sum(ctx, "2b", _doc_items(ctx, "2b", "box1_interest", "1099-INT box 1"))}


def _dividends(ctx: Context) -> dict:
    return {"3a": _sum(ctx, "3a", _doc_items(ctx, "3b", "box1b_qualified", "1099-DIV box 1b")),
            "3b": _sum(ctx, "3b", _doc_items(ctx, "3b", "box1a_ordinary", "1099-DIV box 1a"))}


def _retirement(ctx: Context, total_key: str, taxable_key: str) -> dict:
    """1099-R distributions: total from box 1, taxable from box 2a (less any IRA QCD)."""
    records = ctx.docs.get(total_key, [])
    for rec in records:
        if rec["fields"].get("box2b_taxable_not_determined"):
            raise NeedsHuman("R1099_TAXABLE_NOT_DETERMINED",
                             f"1099-R {rec['doc_id']} has box 2b 'taxable amount not determined' checked. Working "
                             "out the taxable part needs the Simplified Method or General Rule, which SilverFile "
                             "does not do, so a person must take this case.", {"doc_id": rec["doc_id"]})
    taxable_items: list[Item] = []
    steps: list[dict] = []
    for rec in records:
        doc_id, fields = rec["doc_id"], rec["fields"]
        box2a = _amount(fields.get("box2a_taxable"), f"{doc_id} box2a_taxable")
        qcd = _amount(fields.get("qcd_amount"), f"{doc_id} qcd_amount")
        source = {"doc_id": doc_id, "field": "box2a_taxable"}
        if qcd and total_key == "4a":
            less_qcd = ctx.tool("subtract", a=box2a, b=qcd)
            floored = ctx.tool("max0", a=less_qcd["value"])
            steps += [less_qcd, floored]
            taxable_items.append((floored["value"], f"{doc_id} box 2a less QCD",
                                  {"doc_id": doc_id, "field": "box2a_taxable,qcd_amount"}))
        else:
            taxable_items.append((box2a, f"{doc_id} 1099-R box 2a", source))
    return {total_key: _sum(ctx, total_key, _doc_items(ctx, total_key, "box1_gross", "1099-R box 1")),
            taxable_key: _sum(ctx, taxable_key, taxable_items, before=steps)}


def _ira(ctx: Context) -> dict:
    return _retirement(ctx, "4a", "4b")


def _pensions(ctx: Context) -> dict:
    return _retirement(ctx, "5a", "5b")


def _ss_benefits(ctx: Context) -> dict:
    return {"6a": _sum(ctx, "6a", _doc_items(ctx, "6a", "box5_net_benefits", "SSA-1099 box 5"))}


def _schedule1_income(ctx: Context) -> dict:
    return {"8": _direct(ctx, "8", "schedule_values", "sch1_line10", "Schedule 1, line 10")}


def _adjustments(ctx: Context) -> dict:
    return {"10": _direct(ctx, "10", "adjustments", None, "Schedule 1, line 26")}


def _taxable_ss(ctx: Context) -> dict:
    other = ctx.tool("add", values=[ctx.lines[k]["value"] for k in ("1z", "2b", "3b", "4b", "5b", "8")],
                     labels=["line 1z", "line 2b", "line 3b", "line 4b", "line 5b", "line 8"])
    result = ctx.tool("taxable_social_security", filing_status=ctx.filing_status,
                      benefits=ctx.lines["6a"]["value"], other_income=other["value"],
                      tax_exempt_interest=ctx.lines["2a"]["value"], adjustments=ctx.lines["10"]["value"],
                      lived_with_spouse=ctx.lived_with_spouse)
    sources = [{"line": k} for k in ("6a", "1z", "2a", "2b", "3b", "4b", "5b", "8", "10")]
    return {"6b": _line("6b", [other, result], sources)}


def _total_income(ctx: Context) -> dict:
    keys = (("1z", "wages"), ("2b", "interest"), ("3b", "dividends"), ("4b", "IRA"), ("5b", "pensions"),
            ("6b", "Social Security"), ("8", "Schedule 1"))
    return {"9": _sum(ctx, "9", [ctx.line_item(k, label) for k, label in keys])}


def _agi(ctx: Context) -> dict:
    result = ctx.tool("subtract", a=ctx.lines["9"]["value"], b=ctx.lines["10"]["value"])
    line_11a = _line("11a", [result], [{"line": "9"}, {"line": "10"}])
    ctx.lines["11a"] = line_11a
    return {"11a": line_11a, "11b": _sum(ctx, "11b", [ctx.line_item("11a", "adjusted gross income")])}


def _standard_deduction(ctx: Context) -> dict:
    result = ctx.tool("standard_deduction", filing_status=ctx.filing_status, box_count=ctx.box_count)
    return {"12e": _line("12e", [result], [{"rules": "filing_status"}, {"rules": "box_count"}])}


def _senior_deduction(ctx: Context) -> dict:
    result = ctx.tool("senior_deduction", filing_status=ctx.filing_status, magi=ctx.lines["11b"]["value"],
                      qualifying_count=ctx.senior_qualifiers)
    return {"13b": _line("13b", [result], [{"line": "11b"}, {"rules": "senior_qualifiers"}])}


def _total_deductions(ctx: Context) -> dict:
    return {"14": _sum(ctx, "14", [ctx.line_item("12e", "standard deduction"),
                                   ctx.line_item("13b", "Schedule 1-A")])}


def _difference_floor0(ctx: Context, key: str, a: str, b: str) -> dict:
    difference = ctx.tool("subtract", a=ctx.lines[a]["value"], b=ctx.lines[b]["value"])
    floored = ctx.tool("max0", a=difference["value"])
    return _line(key, [difference, floored], [{"line": a}, {"line": b}])


def _taxable_income(ctx: Context) -> dict:
    return {"15": _difference_floor0(ctx, "15", "11b", "14")}


def _tax(ctx: Context) -> dict:
    result = ctx.tool("tax_from_rate_schedule", taxable_income=ctx.lines["15"]["value"],
                      filing_status=ctx.filing_status, qualified_dividends=ctx.lines["3a"]["value"])
    return {"16": _line("16", [result], [{"line": "15"}, {"line": "3a"}, {"rules": "filing_status"}])}


def _schedule2_line3(ctx: Context) -> dict:
    return {"17": _direct(ctx, "17", "schedule_values", "sch2_line3", "Schedule 2, line 3")}


def _tax_plus_schedule2(ctx: Context) -> dict:
    return {"18": _sum(ctx, "18", [ctx.line_item("16", "tax"), ctx.line_item("17", "Schedule 2")])}


def _credits(ctx: Context) -> dict:
    """Line 21 = line 19 (out of scope, 0) + line 20 (Schedule 3, line 8)."""
    return {"21": _sum(ctx, "21", _answer_item(ctx, "schedule_values", "sch3_line8",
                                               "line 20 (Schedule 3, line 8)"))}


def _tax_after_credits(ctx: Context) -> dict:
    return {"22": _difference_floor0(ctx, "22", "18", "21")}


def _other_taxes(ctx: Context) -> dict:
    return {"23": _direct(ctx, "23", "schedule_values", "sch2_line21", "Schedule 2, line 21")}


def _total_tax(ctx: Context) -> dict:
    return {"24": _sum(ctx, "24", [ctx.line_item("22", "tax after credits"), ctx.line_item("23", "other taxes")])}


def _withholding(ctx: Context) -> dict:
    by_line: dict[str, list[Item]] = {"25a": [], "25b": []}
    for records in ctx.docs.values():
        for rec in records:
            line_key, field_name = WITHHOLDING_FIELD[rec["doc_type"]]
            by_line[line_key].append((_amount(rec["fields"].get(field_name), f"{rec['doc_id']} {field_name}"),
                                      f"{rec['doc_id']} {rec['doc_type']} withheld",
                                      {"doc_id": rec["doc_id"], "field": field_name}))
    lines = {key: _sum(ctx, key, items) for key, items in by_line.items()}
    lines["25c"] = _sum(ctx, "25c", [])  # other forms: none in scope
    ctx.lines.update(lines)
    lines["25d"] = _sum(ctx, "25d", [ctx.line_item(k, "") for k in ("25a", "25b", "25c")])
    return lines


def _estimated_payments(ctx: Context) -> dict:
    return {"26": _direct(ctx, "26", "estimated_payments", None, "2025 estimated tax payments")}


def _other_payments(ctx: Context) -> dict:
    """Line 32 = lines 27-31; only line 31 (Schedule 3, line 15) is in scope."""
    return {"32": _sum(ctx, "32", _answer_item(ctx, "schedule_values", "sch3_line15",
                                               "line 31 (Schedule 3, line 15)"))}


def _total_payments(ctx: Context) -> dict:
    return {"33": _sum(ctx, "33", [ctx.line_item("25d", "withholding"), ctx.line_item("26", "estimated"),
                                   ctx.line_item("32", "other payments")])}


def _refund_or_owed(ctx: Context) -> dict:
    result = ctx.tool("overpayment_or_owed", total_tax=ctx.lines["24"]["value"],
                      total_payments=ctx.lines["33"]["value"])
    sources = [{"line": "24"}, {"line": "33"}]
    return {key: {**_line(key, [result], sources), "value": result[key]} for key in ("34", "37")}


@dataclass(frozen=True)
class Step:
    name: str
    produces: tuple[str, ...]
    reads: tuple[str, ...]
    run: Callable[[Context], dict]


STEPS: tuple[Step, ...] = (
    Step("wages", ("1a", "1z"), (), _wages),
    Step("interest", ("2a", "2b"), (), _interest),
    Step("dividends", ("3a", "3b"), (), _dividends),
    Step("ira_distributions", ("4a", "4b"), (), _ira),
    Step("pensions", ("5a", "5b"), (), _pensions),
    Step("social_security_benefits", ("6a",), (), _ss_benefits),
    Step("schedule1_income", ("8",), (), _schedule1_income),
    Step("adjustments", ("10",), (), _adjustments),
    Step("taxable_social_security", ("6b",), ("1z", "2a", "2b", "3b", "4b", "5b", "6a", "8", "10"), _taxable_ss),
    Step("total_income", ("9",), ("1z", "2b", "3b", "4b", "5b", "6b", "8"), _total_income),
    Step("adjusted_gross_income", ("11a", "11b"), ("9", "10"), _agi),
    Step("standard_deduction", ("12e",), (), _standard_deduction),
    Step("senior_deduction", ("13b",), ("11b",), _senior_deduction),
    Step("total_deductions", ("14",), ("12e", "13b"), _total_deductions),
    Step("taxable_income", ("15",), ("11b", "14"), _taxable_income),
    Step("tax", ("16",), ("15", "3a"), _tax),
    Step("schedule2_line3", ("17",), (), _schedule2_line3),
    Step("tax_plus_schedule2", ("18",), ("16", "17"), _tax_plus_schedule2),
    Step("credits", ("21",), (), _credits),
    Step("tax_after_credits", ("22",), ("18", "21"), _tax_after_credits),
    Step("other_taxes", ("23",), (), _other_taxes),
    Step("total_tax", ("24",), ("22", "23"), _total_tax),
    Step("withholding", ("25a", "25b", "25c", "25d"), (), _withholding),
    Step("estimated_payments", ("26",), (), _estimated_payments),
    Step("other_payments", ("32",), (), _other_payments),
    Step("total_payments", ("33",), ("25d", "26", "32"), _total_payments),
    Step("refund_or_owed", ("34", "37"), ("24", "33"), _refund_or_owed),
)


def _check_order(steps: tuple[Step, ...]) -> None:
    """Every contract line produced exactly once, and only read after it has been produced."""
    produced: list[str] = []
    for step in steps:
        missing = [key for key in step.reads if key not in produced]
        if missing:
            raise AssertionError(f"step {step.name} reads {missing} before they are computed")
        produced += step.produces
    if sorted(produced) != sorted(LINE_KEYS):
        raise AssertionError(f"steps produce {sorted(produced)}, contract needs {sorted(LINE_KEYS)}")


_check_order(STEPS)


# ---------------------------------------------------------------------------
# Selecting what to compute
# ---------------------------------------------------------------------------

def requested_lines(state: dict) -> list[str] | None:
    """Lines Review asked to recompute since Computation last handed off, or None for everything."""
    since_last_run: list[dict] = []
    for message in reversed(state.get("handoffs", [])):
        if message["from_agent"] == AGENT:
            break
        since_last_run.insert(0, message)
    pending = messages_for(since_last_run, AGENT, "send_back")
    asked = [key for m in pending for key in m.get("payload", {}).get("lines", []) if key in LINE_KEYS]
    return asked or None


def steps_to_run(asked: list[str] | None, existing: dict) -> list[Step]:
    """All steps, or the steps producing `asked` plus every step downstream of them.

    Falls back to all steps if a step that would be skipped has no value yet.
    """
    if asked is None:
        return list(STEPS)
    dirty, chosen = set(asked), []
    for step in STEPS:
        if dirty & set(step.produces) or dirty & set(step.reads):
            chosen.append(step)
            dirty |= set(step.produces)
    skipped = {key for step in STEPS if step not in chosen for key in step.produces}
    return chosen if skipped <= set(existing) else list(STEPS)


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------

def _context(state: dict) -> Context:
    rules = state.get("rules") or {}
    missing = [key for key in ("filing_status", "box_count", "senior_qualifiers") if rules.get(key) is None]
    if missing:
        raise NeedsHuman("UNCERTAIN", "Computation can't start: Eligibility did not provide "
                         f"{', '.join(missing)}. A person should check the eligibility result.", {"missing": missing})
    doc_line_map = rules.get("doc_line_map") or {}
    docs: dict[str, list[dict]] = {}
    for doc_id, record in sorted((state.get("extracted") or {}).items()):
        if record.get("extractor") == "failed" or record.get("doc_type") not in WITHHOLDING_FIELD:
            continue  # unreadable or out-of-scope documents are Eligibility's and Review's to flag
        docs.setdefault(_family(record, doc_line_map.get(doc_id)), []).append(record)
    return Context(filing_status=rules["filing_status"], box_count=rules["box_count"],
                   senior_qualifiers=rules["senior_qualifiers"],
                   lived_with_spouse=bool(rules.get("lived_with_spouse", False)),
                   docs=docs, answers=state.get("intake") or {}, lines=dict(state.get("lines") or {}))


def _family(record: dict, mapped: str | None) -> str:
    """The line family (1a, 2b, 3b, 4a, 5a, 6a) a document feeds."""
    families = {"1": "1a", "2": "2b", "3": "3b", "4": "4a", "5": "5a", "6": "6a"}
    if mapped and mapped[:1] in families:
        return families[mapped[:1]]
    if record["doc_type"] == "1099-R":
        return "4a" if record["fields"].get("ira_sep_simple") else "5a"
    return DEFAULT_FAMILY[record["doc_type"]]


def run(state: dict) -> dict:
    """Compute every line (or only what Review sent back) and hand off to Review."""
    with session(state.get("session_id")), traced_node(AGENT):
        try:
            ctx = _context(state)
            asked = requested_lines(state)
            steps = steps_to_run(asked, ctx.lines)
            computed: dict[str, dict] = {}
            for step in steps:
                produced = step.run(ctx)
                ctx.lines.update(produced)
                computed.update(produced)
        except NeedsHuman as stop:
            update = raise_hitl(stop.condition, stop.reason, AGENT, stop.details)
            return {**update, **as_update(escalate(AGENT, stop.reason, {"condition": stop.condition,
                                                                        **stop.details}))}

    scope = "all lines" if asked is None else f"lines {', '.join(asked)} and everything downstream"
    message = handoff(AGENT, "review", f"Computed {scope} with {ctx.calls} tax_math tool calls.",
                      {"lines": sorted(computed, key=LINE_KEYS.index), "tool_calls": ctx.calls,
                       "recomputed_for": asked or []})
    return {"lines": computed, **as_update(message)}
