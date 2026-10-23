"""Deterministic tax_math MCP server used by the Computation and Review agents.

Owner: MLOps/DevOps.

Every tool is a pure function of its arguments and returns the same record shape:

    {"value": int, "formula": str, "inputs": dict, "tool": str, "method_note": str}

`value` is whole dollars. `formula` is the human-readable working shown in the provenance
popup on the draft form. Tools validate their inputs and raise ValueError rather than guess.

Run as an MCP server over stdio:

    python -m src.tools.tax_math_server

The client (`src/tools/tax_math_client.py`) either talks to this process over stdio or, with
TAX_MATH_INPROCESS=1, calls the functions in `TOOLS` directly. Both paths run the same code.
"""
from __future__ import annotations

import functools
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Callable

from src.tools import constants as C

try:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server
    from mcp.server.fastmcp.exceptions import ToolError
except ModuleNotFoundError:  # mcp 2.x renamed FastMCP to MCPServer
    from mcp.server.mcpserver import MCPServer as _Server
    from mcp.server.mcpserver.exceptions import ToolError

ToolResult = dict[str, Any]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _money(amount: int) -> str:
    """Format whole dollars for a formula string: 36940 -> '36,940', -500 -> '-500'."""
    return f"{amount:,}"


def _whole_dollars(amount: Decimal) -> int:
    """Round to the nearest whole dollar, halves up (the IRS rounding rule)."""
    return int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _check_amount(name: str, value: Any, allow_negative: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be a whole-dollar integer, got {value!r}")
    if value < 0 and not allow_negative:
        raise ValueError(f"{name} must not be negative, got {value}")
    return value


def _check_status(filing_status: str) -> str:
    if filing_status not in C.FILING_STATUSES:
        raise ValueError(f"filing_status must be one of {', '.join(C.FILING_STATUSES)}, got {filing_status!r}")
    return filing_status


def _result(tool: str, value: int, formula: str, inputs: dict, method_note: str, **extra: Any) -> ToolResult:
    return {"value": value, "formula": formula, "inputs": inputs, "tool": tool, "method_note": method_note,
            **extra}


# ---------------------------------------------------------------------------
# Basic arithmetic
# ---------------------------------------------------------------------------

def add(values: list[int], labels: list[str]) -> ToolResult:
    """Sum whole-dollar amounts. `labels` names each amount for the formula (same length)."""
    if len(values) != len(labels):
        raise ValueError(f"add needs one label per value ({len(values)} values, {len(labels)} labels)")
    for label, value in zip(labels, values):
        _check_amount(label, value, allow_negative=True)
    total = sum(values)
    if not values:
        formula = "No amounts reported = 0"
    else:
        formula = " + ".join(f"{_money(v)} ({label})" for v, label in zip(values, labels)) + f" = {_money(total)}"
    return _result("add", total, formula, {"values": list(values), "labels": list(labels)},
                   "Sum of the listed amounts")


def subtract(a: int, b: int) -> ToolResult:
    """a - b. The result may be negative; combine with `max0` where a line can't go below zero."""
    _check_amount("a", a, allow_negative=True)
    _check_amount("b", b, allow_negative=True)
    return _result("subtract", a - b, f"{_money(a)} - {_money(b)} = {_money(a - b)}", {"a": a, "b": b},
                   "Difference of two amounts")


def max0(a: int) -> ToolResult:
    """The larger of a and 0 (Form 1040: 'if zero or less, enter -0-')."""
    _check_amount("a", a, allow_negative=True)
    value = max(a, 0)
    return _result("max0", value, f"larger of {_money(a)} and 0 = {_money(value)}", {"a": a},
                   "Floor at zero")


# ---------------------------------------------------------------------------
# Line 6b: Social Security Benefits Worksheet (2025 Form 1040 instructions)
# ---------------------------------------------------------------------------

def taxable_social_security(filing_status: str, benefits: int, other_income: int, tax_exempt_interest: int,
                            adjustments: int, lived_with_spouse: bool = False) -> ToolResult:
    """Taxable part of Social Security benefits, following the worksheet line by line.

    other_income is Form 1040 lines 1z + 2b + 3b + 4b + 5b + 7 + 8 (worksheet line 3);
    adjustments is the Schedule 1 amount on worksheet line 6 (line 10 in this scope).
    lived_with_spouse matters only for MFS: the base amount is then 0 and 85% applies at once.
    """
    status = _check_status(filing_status)
    for name, amount in (("benefits", benefits), ("other_income", other_income),
                         ("tax_exempt_interest", tax_exempt_interest), ("adjustments", adjustments)):
        _check_amount(name, amount)
    inputs = {"filing_status": status, "benefits": benefits, "other_income": other_income,
              "tax_exempt_interest": tax_exempt_interest, "adjustments": adjustments,
              "lived_with_spouse": lived_with_spouse}
    note = "2025 Social Security Benefits Worksheet (Form 1040 instructions, lines 1-18)"

    line2 = Decimal(benefits) * C.SS_HALF
    line5 = line2 + other_income + tax_exempt_interest
    steps = [f"1. Benefits {_money(benefits)}",
             f"2. Half of benefits {_money(_whole_dollars(line2))}",
             f"5. Provisional income {_money(_whole_dollars(line2))} + {_money(other_income)} other income"
             f" + {_money(tax_exempt_interest)} tax-exempt interest = {_money(_whole_dollars(line5))}",
             f"6. Adjustments {_money(adjustments)}"]
    if line5 <= adjustments:
        steps.append("7. Adjustments are not less than provisional income, so none is taxable")
        return _result("taxable_social_security", 0, "; ".join(steps) + " = 0", inputs, note)
    line7 = line5 - adjustments
    steps.append(f"7. {_money(_whole_dollars(line5))} - {_money(adjustments)} = {_money(_whole_dollars(line7))}")
    line17 = Decimal(benefits) * C.SS_EIGHTY_FIVE

    if status == "mfs" and lived_with_spouse:
        line16 = line7 * C.SS_EIGHTY_FIVE
        steps.append(f"16. Married filing separately and lived with spouse: 85% x {_money(_whole_dollars(line7))}"
                     f" = {_money(_whole_dollars(line16))}")
    else:
        base, tier = C.SS_BASE_AMOUNT[status], C.SS_SECOND_TIER[status]
        if line7 <= base:
            steps.append(f"9. {_money(_whole_dollars(line7))} is not more than the {_money(base)} base amount,"
                         " so none is taxable")
            return _result("taxable_social_security", 0, "; ".join(steps) + " = 0", inputs, note)
        line9 = line7 - base
        line10 = Decimal(tier - base)
        line11 = max(line9 - line10, Decimal(0))
        line12 = min(line9, line10)
        line13 = line12 * C.SS_HALF
        line14 = min(line2, line13)
        line15 = line11 * C.SS_EIGHTY_FIVE
        line16 = line14 + line15
        steps += [f"9. Over base amount {_money(_whole_dollars(line7))} - {_money(base)} = {_money(_whole_dollars(line9))}",
                  f"10. Second tier minus base {_money(tier)} - {_money(base)} = {_money(int(line10))}",
                  f"11. Over second tier {_money(_whole_dollars(line11))}",
                  f"12. Smaller of line 9 and line 10 = {_money(_whole_dollars(line12))}",
                  f"13. 50% of line 12 = {_money(_whole_dollars(line13))}",
                  f"14. Smaller of line 2 and line 13 = {_money(_whole_dollars(line14))}",
                  f"15. 85% of line 11 = {_money(_whole_dollars(line15))}",
                  f"16. {_money(_whole_dollars(line14))} + {_money(_whole_dollars(line15))}"
                  f" = {_money(_whole_dollars(line16))}"]

    taxable = _whole_dollars(min(line16, line17))
    steps.append(f"17. 85% of benefits = {_money(_whole_dollars(line17))}")
    steps.append(f"18. Smaller of line 16 and line 17 = {_money(taxable)}")
    return _result("taxable_social_security", taxable, "; ".join(steps), inputs, note)


# ---------------------------------------------------------------------------
# Line 12e: Standard Deduction Chart
# ---------------------------------------------------------------------------

def standard_deduction(filing_status: str, box_count: int) -> ToolResult:
    """2025 Standard Deduction Chart for people born before 2 January 1961 or blind."""
    status = _check_status(filing_status)
    _check_amount("box_count", box_count)
    if box_count > C.MAX_BOXES:
        raise ValueError(f"box_count must be 0-{C.MAX_BOXES}, got {box_count}")
    base, per_box = C.STANDARD_DEDUCTION_BASE[status], C.STANDARD_DEDUCTION_PER_BOX[status]
    value = base + box_count * per_box
    formula = f"{_money(base)} base + {box_count} x {_money(per_box)} = {_money(value)}"
    return _result("standard_deduction", value, formula, {"filing_status": status, "box_count": box_count},
                   "2025 Standard Deduction Chart (Form 1040-SR); line 12d boxes from Eligibility")


# ---------------------------------------------------------------------------
# Line 13b: Schedule 1-A enhanced senior deduction
# ---------------------------------------------------------------------------

def senior_deduction(filing_status: str, magi: int, qualifying_count: int) -> ToolResult:
    """$6,000 per qualifying person aged 65+, less 6% of MAGI over the threshold, per person, floor 0."""
    status = _check_status(filing_status)
    _check_amount("magi", magi, allow_negative=True)
    _check_amount("qualifying_count", qualifying_count)
    if qualifying_count > 2:
        raise ValueError(f"qualifying_count must be 0, 1 or 2, got {qualifying_count}")
    inputs = {"filing_status": status, "magi": magi, "qualifying_count": qualifying_count}
    threshold = C.SENIOR_PHASEOUT_THRESHOLD[status]
    excess = max(magi - threshold, 0)
    reduction = _whole_dollars(Decimal(excess) * C.SENIOR_PHASEOUT_RATE)
    per_person = max(C.SENIOR_DEDUCTION_PER_PERSON - reduction, 0)
    value = per_person * qualifying_count
    if excess:
        formula = (f"MAGI {_money(magi)} is {_money(excess)} over {_money(threshold)}; 6% x {_money(excess)}"
                   f" = {_money(reduction)}; per person {_money(C.SENIOR_DEDUCTION_PER_PERSON)} - {_money(reduction)}"
                   f" = {_money(per_person)} (floor 0); {qualifying_count} x {_money(per_person)} = {_money(value)}")
    else:
        formula = (f"MAGI {_money(magi)} is not over {_money(threshold)}, no phase-out; "
                   f"{qualifying_count} x {_money(C.SENIOR_DEDUCTION_PER_PERSON)} = {_money(value)}")
    return _result("senior_deduction", value, formula, inputs,
                   "Schedule 1-A enhanced senior deduction (2025-2028); MAGI = AGI in this scope")


# ---------------------------------------------------------------------------
# Line 16: 2025 rate schedule with the Qualified Dividends and Capital Gain Tax Worksheet
# ---------------------------------------------------------------------------

def _schedule_tax(amount: int, status: str) -> Decimal:
    """Exact (unrounded) tax on `amount` from the 2025 rate schedule."""
    tax, lower = Decimal(0), 0
    for upper, rate in C.RATE_SCHEDULES[status]:
        if upper is None or amount <= upper:
            return tax + (amount - lower) * rate
        tax += (upper - lower) * rate
        lower = upper
    return tax


def tax_from_rate_schedule(taxable_income: int, filing_status: str, qualified_dividends: int) -> ToolResult:
    """Line 16 tax: the rate schedule on ordinary income, qualified dividends at 0/15/20%.

    Follows the 2025 Qualified Dividends and Capital Gain Tax Worksheet with no capital gains
    (Schedule D is out of scope), and keeps the smaller of that result and the plain schedule tax.
    """
    status = _check_status(filing_status)
    _check_amount("taxable_income", taxable_income)
    _check_amount("qualified_dividends", qualified_dividends)
    inputs = {"taxable_income": taxable_income, "filing_status": status, "qualified_dividends": qualified_dividends}
    note = "2025 rate schedule with qualified dividends worksheet"

    preferential = min(taxable_income, qualified_dividends)                      # worksheet line 10
    ordinary = taxable_income - preferential                                     # line 5
    zero_band = min(taxable_income, C.QD_ZERO_RATE_MAX[status])                   # line 7
    at_zero = zero_band - min(ordinary, zero_band)                                # line 9
    fifteen_band = min(taxable_income, C.QD_FIFTEEN_RATE_MAX[status])             # line 14
    at_fifteen = min(preferential - at_zero, max(fifteen_band - ordinary - at_zero, 0))  # line 17
    at_twenty = preferential - at_zero - at_fifteen                               # line 20

    ordinary_tax = _schedule_tax(ordinary, status)                                # line 22
    worksheet_tax = ordinary_tax + at_fifteen * C.QD_RATE_15 + at_twenty * C.QD_RATE_20  # line 23
    plain_tax = _schedule_tax(taxable_income, status)                             # line 24
    tax = _whole_dollars(min(worksheet_tax, plain_tax))                           # line 25

    formula = (f"Taxable income {_money(taxable_income)}, of which {_money(preferential)} qualified dividends; "
               f"rate schedule on ordinary {_money(ordinary)} = {ordinary_tax:,.2f}; "
               f"{_money(at_zero)} at 0%, {_money(at_fifteen)} at 15%, {_money(at_twenty)} at 20% "
               f"= {worksheet_tax:,.2f}; rate schedule on all {_money(taxable_income)} = {plain_tax:,.2f}; "
               f"smaller, rounded = {_money(tax)}")
    return _result("tax_from_rate_schedule", tax, formula, inputs, note)


# ---------------------------------------------------------------------------
# Lines 34 / 37
# ---------------------------------------------------------------------------

def overpayment_or_owed(total_tax: int, total_payments: int) -> ToolResult:
    """Line 34 overpayment and line 37 amount owed; exactly one of them is non-zero (or both 0).

    `value` is the signed difference payments - tax; the lines are under the keys "34" and "37".
    """
    _check_amount("total_tax", total_tax)
    _check_amount("total_payments", total_payments)
    difference = total_payments - total_tax
    overpaid, owed = max(difference, 0), max(-difference, 0)
    formula = (f"Payments {_money(total_payments)} - tax {_money(total_tax)} = {_money(difference)}; "
               f"line 34 overpaid {_money(overpaid)}, line 37 owed {_money(owed)}")
    return _result("overpayment_or_owed", difference, formula,
                   {"total_tax": total_tax, "total_payments": total_payments},
                   "Line 33 compared with line 24", **{"34": overpaid, "37": owed})


# ---------------------------------------------------------------------------
# Registry and MCP server
# ---------------------------------------------------------------------------

TOOLS: dict[str, Callable[..., ToolResult]] = {fn.__name__: fn for fn in (
    add, subtract, max0, taxable_social_security, standard_deduction, senior_deduction,
    tax_from_rate_schedule, overpayment_or_owed)}


def _as_mcp_tool(fn: Callable[..., ToolResult]) -> Callable[..., ToolResult]:
    """Report rejected inputs to the client as a plain ToolError message, not a crash."""
    @functools.wraps(fn)
    def tool(**kwargs: Any) -> ToolResult:
        try:
            return fn(**kwargs)
        except ValueError as error:
            raise ToolError(str(error)) from error
    return tool


def build_server() -> Any:
    """An MCP server exposing every function in TOOLS."""
    server = _Server("tax_math")
    for fn in TOOLS.values():
        server.tool()(_as_mcp_tool(fn))
    return server


if __name__ == "__main__":
    build_server().run("stdio")
