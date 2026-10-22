"""Generates synthetic tax documents and the clean eval case.

Owner: Inter agent communication.

    python data/generate_fixtures.py

Writes:
- demo/w2_image_1.jpg, demo/w2_image_2.jpg: W-2 images drawn with Pillow, each with a sidecar
  JSON (same name, .json) holding its ground truth. The sidecar never holds a full SSN.
- data/fixtures/*.json: 1099-INT, 1099-DIV, 1099-R (normal, box 2b checked, IRA with a QCD),
  SSA-1099 and an out-of-scope 1099-NEC.
- demo/eval_case_1/case.json: the clean run (contract section 13).

Every person, employer and number is invented. SSNs use the 900-999 test range only, and
the output is identical on every run so the committed files never drift.

Guardrails and ethics reuse `make_w2_image` for eval_case_2:

    from data.generate_fixtures import W2, make_w2_image
    make_w2_image(W2(employee_first="Ada", ...), Path("demo/eval_case_2/w2.jpg"))
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "data" / "fixtures"
DEMO = ROOT / "demo"

INK = (20, 20, 20)
FORM_RED = (176, 32, 40)
PAPER = (255, 255, 255)
SIZE = (1700, 1000)


@dataclass(frozen=True)
class W2:
    """What is printed on one synthetic W-2. Amounts are in whole dollars."""
    employee_first: str
    employee_last: str
    employee_ssn: str            # must be 900-999 area
    employer_name: str
    employer_address: str
    employer_ein: str
    employee_address: str
    box1_wages: int
    box2_fed_withheld: int
    box3_ss_wages: int
    box4_ss_withheld: int
    box5_medicare_wages: int
    box6_medicare_withheld: int
    control_number: str = ""

    def ground_truth(self) -> dict:
        """The sidecar record: contract section 5 W-2 fields, last four SSN digits only."""
        return {
            "doc_type": "W2",
            "note": "Ground truth for the generated image. Used only if the image cannot be read.",
            "fields": {
                "employee_name": f"{self.employee_first} {self.employee_last}",
                "employee_ssn_last4": self.employee_ssn[-4:],
                "employer_name": self.employer_name,
                "box1_wages": self.box1_wages,
                "box2_fed_withheld": self.box2_fed_withheld,
            },
        }


MARA_W2 = W2(
    employee_first="Mara", employee_last="Lindqvist", employee_ssn="901-11-1037",
    employer_name="Willowbrook Garden Center", employer_address="410 Orchard Lane, Ithaca NY 14850",
    employer_ein="91-4820117", employee_address="22 Cayuga Heights Rd, Ithaca NY 14850",
    box1_wages=30000, box2_fed_withheld=2000, box3_ss_wages=30000, box4_ss_withheld=1860,
    box5_medicare_wages=30000, box6_medicare_withheld=435, control_number="WGC-0042",
)

ROSA_W2 = W2(
    employee_first="Rosa", employee_last="Delgado", employee_ssn="902-22-4518",
    employer_name="Finger Lakes Public Library", employer_address="18 Tioga Street, Ithaca NY 14850",
    employer_ein="91-7730254", employee_address="7 Lake Street, Trumansburg NY 14886",
    box1_wages=18500, box2_fed_withheld=1200, box3_ss_wages=18500, box4_ss_withheld=1147,
    box5_medicare_wages=18500, box6_medicare_withheld=268, control_number="FLPL-1187",
)


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.load_default(size=size)  # bundled with Pillow, so every machine draws the same image


def _money(amount: int) -> str:
    return f"{amount:,.2f}"


def _cell(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], label: str, value: str) -> None:
    """One W-2 box: a ruled rectangle, a small label at the top, the value underneath."""
    x0, y0, x1, y1 = box
    draw.rectangle(box, outline=FORM_RED, width=2)
    draw.text((x0 + 10, y0 + 8), label, font=_font(20), fill=FORM_RED)
    for row, text in enumerate(value.split("\n")):
        draw.text((x0 + 16, y0 + 44 + row * 36), text, font=_font(30), fill=INK)


def make_w2_image(w2: W2, path: Path) -> Path:
    """Draw a W-2 with readable box labels (so OCR works) and write its sidecar JSON next to it."""
    if not 900 <= int(w2.employee_ssn[:3]) <= 999:
        raise ValueError("synthetic W-2s may only carry 900-series test SSNs")
    image = Image.new("RGB", SIZE, PAPER)
    draw = ImageDraw.Draw(image)
    draw.text((40, 24), "Form W-2  Wage and Tax Statement  2025", font=_font(34), fill=INK)
    draw.text((40, 70), "SYNTHETIC TEST DOCUMENT - CLASSROOM USE ONLY", font=_font(20), fill=FORM_RED)

    left, mid, right = 40, 860, 1660
    half = (mid + right) // 2
    _cell(draw, (left, 110, mid, 210), "a Employee's social security number", w2.employee_ssn)
    _cell(draw, (left, 210, mid, 310), "b Employer identification number (EIN)", w2.employer_ein)
    _cell(draw, (left, 310, mid, 470), "c Employer's name, address, and ZIP code",
          f"{w2.employer_name}\n{w2.employer_address}")
    _cell(draw, (left, 470, mid, 570), "d Control number", w2.control_number)
    _cell(draw, (left, 570, 450, 670), "e Employee's first name and initial", w2.employee_first)
    _cell(draw, (450, 570, mid, 670), "Last name", w2.employee_last)
    _cell(draw, (left, 670, mid, 770), "f Employee's address and ZIP code", w2.employee_address)

    pairs = [("1 Wages, tips, other compensation", w2.box1_wages),
             ("2 Federal income tax withheld", w2.box2_fed_withheld),
             ("3 Social security wages", w2.box3_ss_wages),
             ("4 Social security tax withheld", w2.box4_ss_withheld),
             ("5 Medicare wages and tips", w2.box5_medicare_wages),
             ("6 Medicare tax withheld", w2.box6_medicare_withheld)]
    for index, (label, amount) in enumerate(pairs):
        x0 = mid if index % 2 == 0 else half
        x1 = half if index % 2 == 0 else right
        y0 = 110 + (index // 2) * 100
        _cell(draw, (x0, y0, x1, y0 + 100), label, _money(amount))
    _cell(draw, (mid, 410, right, 770), "12a-14 Other", "")
    draw.text((40, 800), "Copy B - To Be Filed With Employee's FEDERAL Tax Return. Department of the Treasury - "
                         "Internal Revenue Service", font=_font(18), fill=INK)

    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "JPEG", quality=95)
    _write_json(path.with_suffix(".json"), w2.ground_truth())
    return path


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def _form(doc_type: str, payer: str, fields: dict, **extra) -> dict:
    return {"doc_type": doc_type, "tax_year": 2025, "payer_name": payer, **extra, "fields": fields}


JSON_FIXTURES: dict[str, dict] = {
    "1099int_1.json": _form("1099-INT", "Tompkins Community Bank",
                            {"box1_interest": 300, "box4_fed_withheld": 0, "box8_tax_exempt_interest": 0}),
    "1099div_1.json": _form("1099-DIV", "Seneca Index Fund",
                            {"box1a_ordinary": 100, "box1b_qualified": 50, "box4_fed_withheld": 0}),
    "ssa1099_1.json": _form("SSA-1099", "Social Security Administration",
                            {"box5_net_benefits": 12000, "box6_fed_withheld": 0}),
    "1099r_pension_1.json": _form("1099-R", "Cayuga County Teachers Pension Fund",
                                  {"box1_gross": 20000, "box2a_taxable": 20000, "box2b_taxable_not_determined": False,
                                   "box4_fed_withheld": 2000, "box7_code": "7", "ira_sep_simple": False,
                                   "qcd_amount": 0}),
    "1099r_ira_qcd_1.json": _form("1099-R", "Ithaca Savings IRA Custodian",
                                  {"box1_gross": 8000, "box2a_taxable": 8000, "box2b_taxable_not_determined": False,
                                   "box4_fed_withheld": 800, "box7_code": "7", "ira_sep_simple": True,
                                   "qcd_amount": 3000}),
    "1099r_2b_checked.json": _form("1099-R", "Old Mill Annuity Company",
                                   {"box1_gross": 15000, "box2a_taxable": 0, "box2b_taxable_not_determined": True,
                                    "box4_fed_withheld": 1500, "box7_code": "7", "ira_sep_simple": False,
                                    "qcd_amount": 0}),
    "1099nec_1.json": _form("1099-NEC", "Lakeside Events LLC",
                            {"box1_nonemployee_compensation": 4200, "box4_fed_withheld": 0}),
}


def eval_case_1() -> dict:
    """Clean run: Single, born 1958, one W-2 image plus SSA-1099, 1099-INT and 1099-DIV."""
    return {
        "case_id": "eval_case_1",
        "description": "Clean minimal run: single filer born 1958 with wages, interest, dividends and Social "
                       "Security. No human review needed.",
        "answers": {
            "who_is_answering": "yes",
            "taxpayer_first_name": "Mara",
            "taxpayer_last_name": "Lindqvist",
            "taxpayer_ssn": MARA_W2.employee_ssn,
            "taxpayer_dob": "1958-06-21",
            "taxpayer_blind": False,
            "can_be_claimed_as_dependent": False,
            "filing_status": "single",
            "dual_status_alien": False,
            "dependents": [],
            "other_income": {"has_other_income": False, "description": ""},
            "adjustments": 0,
            "schedule_values": {"sch1_line10": 0, "sch2_line3": 0, "sch2_line21": 0, "sch3_line8": 0,
                                "sch3_line15": 0},
            "estimated_payments": 0,
            "refund_destination": "My own checking account at Tompkins Community Bank",
        },
        "documents": [
            {"doc_id": "w2_1", "doc_type": "W2", "path": "demo/w2_image_1.jpg"},
            {"doc_id": "ssa_1", "doc_type": "SSA-1099", "path": "data/fixtures/ssa1099_1.json"},
            {"doc_id": "int_1", "doc_type": "1099-INT", "path": "data/fixtures/1099int_1.json"},
            {"doc_id": "div_1", "doc_type": "1099-DIV", "path": "data/fixtures/1099div_1.json"},
        ],
        "hitl_resolutions": [],
        "expected": {
            "lines": {"1z": 30000, "2b": 300, "3a": 50, "3b": 100, "6a": 12000, "6b": 6540, "9": 36940,
                      "11a": 36940, "12e": 17750, "13b": 6000, "14": 23750, "15": 13190, "25a": 2000},
            "hitl_conditions": [],
        },
    }


def main() -> None:
    for name, w2 in (("w2_image_1.jpg", MARA_W2), ("w2_image_2.jpg", ROSA_W2)):
        print("wrote", make_w2_image(w2, DEMO / name).relative_to(ROOT))
    for name, data in JSON_FIXTURES.items():
        _write_json(FIXTURES / name, data)
        print("wrote", (FIXTURES / name).relative_to(ROOT))
    _write_json(DEMO / "eval_case_1" / "case.json", eval_case_1())
    print("wrote demo/eval_case_1/case.json")


if __name__ == "__main__":
    main()
