"""Fixed 2025 tax figures used by the tax_math tools. One place, one source per number.

Owner: MLOps/DevOps.

Sources:
- Rate schedules and capital gain rate amounts: Rev. Proc. 2024-40, sections .01 (Tables 1-4)
  and .03 (maximum zero-rate and 15-percent-rate amounts). These are the figures printed in the
  2025 Form 1040 instructions (Tax Rate Schedules; Qualified Dividends and Capital Gain Tax
  Worksheet).
- Standard deduction, Schedule 1-A senior deduction and Social Security worksheet amounts: the
  assignment brief section 3.2 and shared contract section 15 ("don't change").

Every table is keyed by the contract's filing status codes: single, mfj, mfs, hoh, qss.
"""
from __future__ import annotations

from decimal import Decimal

FILING_STATUSES: tuple[str, ...] = ("single", "mfj", "mfs", "hoh", "qss")
MARRIED_JOINT = ("mfj", "qss")  # statuses that share the joint-return figures

# --- Line 16: 2025 tax rate schedules ------------------------------------------------------------
# Each schedule lists (upper bound of the bracket, rate). The last bracket has no upper bound.
RATES: tuple[Decimal, ...] = tuple(Decimal(r) for r in ("0.10", "0.12", "0.22", "0.24", "0.32", "0.35", "0.37"))
_BOUNDS: dict[str, tuple[int, ...]] = {
    "single": (11_925, 48_475, 103_350, 197_300, 250_525, 626_350),   # Schedule X, Table 3
    "mfj":    (23_850, 96_950, 206_700, 394_600, 501_050, 751_600),   # Schedule Y-1, Table 1
    "qss":    (23_850, 96_950, 206_700, 394_600, 501_050, 751_600),   # Schedule Y-1, Table 1
    "mfs":    (11_925, 48_475, 103_350, 197_300, 250_525, 375_800),   # Schedule Y-2, Table 4
    "hoh":    (17_000, 64_850, 103_350, 197_300, 250_500, 626_350),   # Schedule Z, Table 2
}
RATE_SCHEDULES: dict[str, tuple[tuple[int | None, Decimal], ...]] = {
    status: tuple(zip((*bounds, None), RATES)) for status, bounds in _BOUNDS.items()
}

# --- Qualified Dividends and Capital Gain Tax Worksheet (Rev. Proc. 2024-40 section .03) ---------
QD_ZERO_RATE_MAX: dict[str, int] = {"single": 48_350, "mfs": 48_350, "mfj": 96_700, "qss": 96_700, "hoh": 64_750}
QD_FIFTEEN_RATE_MAX: dict[str, int] = {"single": 533_400, "mfs": 300_000, "mfj": 600_050, "qss": 600_050,
                                       "hoh": 566_700}
QD_RATE_15 = Decimal("0.15")
QD_RATE_20 = Decimal("0.20")

# --- Line 12e: Standard Deduction Chart (brief 3.2 rule 2, contract section 15) ------------------
STANDARD_DEDUCTION_BASE: dict[str, int] = {"single": 15_750, "mfs": 15_750, "mfj": 31_500, "qss": 31_500,
                                           "hoh": 23_625}
STANDARD_DEDUCTION_PER_BOX: dict[str, int] = {"single": 2_000, "hoh": 2_000, "mfj": 1_600, "mfs": 1_600,
                                              "qss": 1_600}
MAX_BOXES = 4  # line 12d: taxpayer 65+, taxpayer blind, spouse 65+, spouse blind

# --- Line 13b: Schedule 1-A enhanced senior deduction (brief 3.2 rule 3) -------------------------
SENIOR_DEDUCTION_PER_PERSON = 6_000
SENIOR_PHASEOUT_THRESHOLD: dict[str, int] = {"single": 75_000, "mfs": 75_000, "hoh": 75_000, "qss": 75_000,
                                             "mfj": 150_000}
SENIOR_PHASEOUT_RATE = Decimal("0.06")

# --- Line 6b: Social Security Benefits Worksheet (brief 3.2 rule 4) ------------------------------
SS_BASE_AMOUNT: dict[str, int] = {"single": 25_000, "mfs": 25_000, "hoh": 25_000, "qss": 25_000, "mfj": 32_000}
SS_SECOND_TIER: dict[str, int] = {"single": 34_000, "mfs": 34_000, "hoh": 34_000, "qss": 34_000, "mfj": 44_000}
SS_HALF = Decimal("0.50")
SS_EIGHTY_FIVE = Decimal("0.85")
