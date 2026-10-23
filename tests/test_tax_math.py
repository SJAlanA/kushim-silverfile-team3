"""Table-driven checks for every tax_math tool (src/tools/tax_math_server.py).

Expected values are worked by hand from the 2025 Form 1040 instructions and the brief, not
recomputed with the code under test.
"""
import pytest

from src.tools import constants
from src.tools.tax_math_client import TaxMathError, call_tool
from src.tools.tax_math_server import TOOLS

RESULT_KEYS = {"value", "formula", "inputs", "tool", "method_note"}


@pytest.fixture(autouse=True)
def _inprocess(monkeypatch, tmp_path):
    monkeypatch.setenv("TAX_MATH_INPROCESS", "1")
    monkeypatch.setenv("SILVERFILE_TRACE_DIR", str(tmp_path))


# --- eval_case_1 (demo/eval_case_1.md) --------------------------------------------------------

@pytest.mark.parametrize("tool, kwargs, expected", [
    ("taxable_social_security", dict(filing_status="single", benefits=12_000, other_income=30_400,
                                     tax_exempt_interest=0, adjustments=0), 6_540),
    ("add", dict(values=[30_000, 300, 100, 6_540], labels=["1z", "2b", "3b", "6b"]), 36_940),
    ("standard_deduction", dict(filing_status="single", box_count=1), 17_750),
    ("senior_deduction", dict(filing_status="single", magi=36_940, qualifying_count=1), 6_000),
    ("add", dict(values=[17_750, 6_000], labels=["12e", "13b"]), 23_750),
    ("subtract", dict(a=36_940, b=23_750), 13_190),
    ("tax_from_rate_schedule", dict(taxable_income=13_190, filing_status="single", qualified_dividends=50), 1_338),
])
def test_eval_case_1(tool, kwargs, expected):
    result = call_tool(tool, **kwargs)
    assert result["value"] == expected
    assert set(result) >= RESULT_KEYS and result["tool"] == tool and result["inputs"]


# --- Standard Deduction Chart: every status, base plus 1-4 boxes -------------------------------

CHART = {
    "single": (15_750, 17_750, 19_750, 21_750, 23_750),
    "hoh":    (23_625, 25_625, 27_625, 29_625, 31_625),
    "mfj":    (31_500, 33_100, 34_700, 36_300, 37_900),
    "qss":    (31_500, 33_100, 34_700, 36_300, 37_900),
    "mfs":    (15_750, 17_350, 18_950, 20_550, 22_150),
}


@pytest.mark.parametrize("status, boxes, expected",
                         [(s, b, amounts[b]) for s, amounts in CHART.items() for b in range(5)])
def test_standard_deduction_chart(status, boxes, expected):
    assert call_tool("standard_deduction", filing_status=status, box_count=boxes)["value"] == expected


# --- Taxable Social Security: 0%, 50% and 85% tiers --------------------------------------------

@pytest.mark.parametrize("status, benefits, other, exempt, adjustments, lived_with_spouse, expected", [
    ("single", 12_000, 10_000, 0, 0, False, 0),          # provisional 16,000 under 25,000 base: 0%
    ("single", 20_000, 20_000, 0, 0, False, 2_500),      # 30,000: 50% of 5,000 over base
    ("single", 12_000, 30_400, 0, 0, False, 6_540),      # 36,400: into the 85% tier (eval_case_1)
    ("single", 12_000, 30_000, 400, 0, False, 6_540),    # tax-exempt interest counts toward provisional
    ("single", 20_000, 100_000, 0, 0, False, 17_000),    # capped at 85% of benefits
    ("single", 20_000, 30_000, 0, 50_000, False, 0),     # adjustments wipe out provisional income
    ("hoh", 20_000, 20_000, 0, 0, False, 2_500),         # HOH uses the 25,000 base
    ("mfj", 30_000, 10_000, 0, 0, False, 0),             # 25,000 under the 32,000 joint base
    ("mfj", 30_000, 40_000, 0, 0, False, 15_350),        # 55,000: both joint tiers
    ("mfs", 10_000, 5_000, 0, 0, True, 8_500),           # MFS living with spouse: base 0, straight to 85%
    ("mfs", 10_000, 5_000, 0, 0, False, 0),              # MFS living apart: normal 25,000 base
])
def test_taxable_social_security(status, benefits, other, exempt, adjustments, lived_with_spouse, expected):
    result = call_tool("taxable_social_security", filing_status=status, benefits=benefits, other_income=other,
                       tax_exempt_interest=exempt, adjustments=adjustments, lived_with_spouse=lived_with_spouse)
    assert result["value"] == expected


def test_social_security_formula_shows_worksheet_steps():
    formula = call_tool("taxable_social_security", filing_status="single", benefits=12_000, other_income=30_400,
                        tax_exempt_interest=0, adjustments=0)["formula"]
    for step in ("1. Benefits", "5. Provisional income", "13. 50%", "15. 85%", "18. Smaller"):
        assert step in formula


# --- Schedule 1-A senior deduction: phase-out per person, floor 0 ------------------------------

@pytest.mark.parametrize("status, magi, count, expected", [
    ("single", 36_940, 1, 6_000),      # under 75,000: no phase-out
    ("single", 75_000, 1, 6_000),      # exactly at the threshold
    ("single", 85_000, 1, 5_400),      # 6% of 10,000 = 600 off
    ("single", 175_000, 1, 0),         # 6% of 100,000 = 6,000: fully phased out
    ("single", 250_000, 1, 0),         # floor at 0, never negative
    ("hoh", 85_000, 1, 5_400),
    ("mfj", 140_000, 2, 12_000),       # under 150,000 joint threshold
    ("mfj", 170_000, 2, 9_600),        # 1,200 off each of two people
    ("mfj", 160_000, 1, 5_400),        # one qualifying spouse
    ("mfj", 260_000, 2, 0),
    ("mfj", 100_000, 0, 0),            # nobody qualifies
])
def test_senior_deduction(status, magi, count, expected):
    assert call_tool("senior_deduction", filing_status=status, magi=magi, qualifying_count=count)["value"] == expected


# --- Line 16: rate schedule and qualified dividends worksheet ----------------------------------

@pytest.mark.parametrize("taxable, status, qualified, expected", [
    (0, "single", 0, 0),
    (13_190, "single", 50, 1_338),         # eval_case_1: 50 of qualified dividends at 0%
    (40_000, "single", 10_000, 3_362),     # all 10,000 qualified dividends at 0%
    (5_000, "single", 8_000, 0),           # qualified dividends larger than taxable income
    (50_000, "single", 0, 5_914),          # plain rate schedule, into 22%
    (100_000, "mfj", 0, 11_828),
    (100_000, "single", 20_000, 15_514),   # qualified dividends at 15%
    (700_000, "single", 100_000, 199_547),  # qualified dividends at 20%
])
def test_tax_from_rate_schedule(taxable, status, qualified, expected):
    result = call_tool("tax_from_rate_schedule", taxable_income=taxable, filing_status=status,
                       qualified_dividends=qualified)
    assert result["value"] == expected
    assert result["method_note"] == "2025 rate schedule with qualified dividends worksheet"


def test_rate_schedules_match_rev_proc_2024_40():
    bounds = {status: tuple(upper for upper, _ in schedule[:-1])
              for status, schedule in constants.RATE_SCHEDULES.items()}
    assert bounds["single"] == (11_925, 48_475, 103_350, 197_300, 250_525, 626_350)
    assert bounds["mfj"] == bounds["qss"] == (23_850, 96_950, 206_700, 394_600, 501_050, 751_600)
    assert bounds["hoh"] == (17_000, 64_850, 103_350, 197_300, 250_500, 626_350)
    assert bounds["mfs"] == (11_925, 48_475, 103_350, 197_300, 250_525, 375_800)
    assert constants.QD_ZERO_RATE_MAX == {"single": 48_350, "mfs": 48_350, "mfj": 96_700, "qss": 96_700,
                                          "hoh": 64_750}


# --- Lines 34 / 37 -----------------------------------------------------------------------------

@pytest.mark.parametrize("tax, payments, line34, line37", [
    (1_338, 2_000, 662, 0), (5_000, 2_000, 0, 3_000), (2_000, 2_000, 0, 0)])
def test_overpayment_or_owed(tax, payments, line34, line37):
    result = call_tool("overpayment_or_owed", total_tax=tax, total_payments=payments)
    assert (result["34"], result["37"]) == (line34, line37)


# --- Basic tools and input validation ----------------------------------------------------------

@pytest.mark.parametrize("tool, kwargs, expected", [
    ("add", dict(values=[], labels=[]), 0),
    ("subtract", dict(a=100, b=250), -150),
    ("max0", dict(a=-150), 0),
    ("max0", dict(a=42), 42),
])
def test_basic_tools(tool, kwargs, expected):
    assert call_tool(tool, **kwargs)["value"] == expected


@pytest.mark.parametrize("tool, kwargs", [
    ("add", dict(values=[1, 2], labels=["only one"])),
    ("add", dict(values=[True], labels=["a bool"])),
    ("standard_deduction", dict(filing_status="married", box_count=1)),
    ("standard_deduction", dict(filing_status="single", box_count=5)),
    ("taxable_social_security", dict(filing_status="single", benefits=-1, other_income=0,
                                     tax_exempt_interest=0, adjustments=0)),
    ("senior_deduction", dict(filing_status="mfj", magi=50_000, qualifying_count=3)),
    ("no_such_tool", dict()),
])
def test_bad_inputs_are_refused_not_guessed(tool, kwargs):
    with pytest.raises(TaxMathError):
        call_tool(tool, **kwargs)


def test_every_tool_is_deterministic():
    kwargs = dict(taxable_income=88_888, filing_status="hoh", qualified_dividends=4_321)
    assert call_tool("tax_from_rate_schedule", **kwargs) == call_tool("tax_from_rate_schedule", **kwargs)
    assert set(TOOLS) == {"add", "subtract", "max0", "taxable_social_security", "standard_deduction",
                          "senior_deduction", "tax_from_rate_schedule", "overpayment_or_owed"}
