"""Generates synthetic tax documents and the clean eval case.

Owner: Inter agent communication.

    python data/generate_fixtures.py

Writes:
- demo/w2_image_1.jpg, demo/w2_image_2.jpg, demo/w2_image_3.jpg: filled-in W-2s drawn with Pillow
  on the official IRS box layout (a-f, 1-20), each with a sidecar JSON (same name, .json) holding
  its ground truth. The sidecar never holds a full SSN. Image 3 has every box filled.
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
FILL_BLUE = (25, 90, 200)        # typed entries, like a W-2 filled in by payroll software
SHADE = (200, 200, 200)
WATERMARK = (176, 32, 40)
PAPER = (255, 255, 255)
SIZE = (1700, 1110)


@dataclass(frozen=True)
class W2:
    """What is printed on one synthetic W-2. Amounts are in whole dollars; 0 or "" leaves a box blank."""
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
    box7_ss_tips: int = 0
    box8_allocated_tips: int = 0
    box10_dependent_care: int = 0
    box11_nonqualified: int = 0
    box12: tuple[tuple[str, int], ...] = ()          # up to four (code, amount) pairs, 12a-12d
    box13_statutory: bool = False
    box13_retirement_plan: bool = False
    box13_third_party_sick: bool = False
    box14_other: str = ""
    state: str = ""                                  # boxes 15-20, one state/locality row
    state_id: str = ""
    box16_state_wages: int = 0
    box17_state_tax: int = 0
    box18_local_wages: int = 0
    box19_local_tax: int = 0
    box20_locality: str = ""

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

# Every box filled, after the layout of a payroll-printed W-2: 401(k) deferrals (12a D) make box 1
# lower than boxes 3 and 5, employer health cost is reported in 12b DD, and the retirement plan box
# is checked. Not used by eval_case_1; it is here for demos and for other eval cases.
THEO_W2 = W2(
    employee_first="Theo R", employee_last="Brandt", employee_ssn="903-45-6789",
    employer_name="Ithaca Valley Hardware", employer_address="215 Meadow Street\nIthaca NY 14850",
    employer_ein="91-2233445", employee_address="48 Elm Street\nDryden NY 13053",
    box1_wages=48500, box2_fed_withheld=6835, box3_ss_wages=50000, box4_ss_withheld=3100,
    box5_medicare_wages=50000, box6_medicare_withheld=725, control_number="IVH-A1B2",
    box12=(("D", 1500), ("DD", 1000)), box13_retirement_plan=True,
    state="NY", state_id="91-0457812", box16_state_wages=50000, box17_state_tax=1535,
)


# Layout of the official Form W-2, in points measured from the IRS form (form is 1172 x 650),
# scaled onto the image. Columns: the left block, box 1 column, box 2 column.
_SCALE, _ORIGIN = 1.4, (30, 30)
_LEFT, _MID, _HALF, _RIGHT = 0, 640, 907, 1172


def _xy(x: float, y: float) -> tuple[int, int]:
    return round(_ORIGIN[0] + x * _SCALE), round(_ORIGIN[1] + y * _SCALE)


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.load_default(size=size)  # bundled with Pillow, so every machine draws the same image


def _money(amount: int) -> str:
    return f"{amount:,.2f}" if amount else ""


def _rect(draw: ImageDraw.ImageDraw, x0: float, y0: float, x1: float, y1: float, fill=None) -> None:
    draw.rectangle((*_xy(x0, y0), *_xy(x1, y1)), outline=INK, width=2, fill=fill)


def _label(draw: ImageDraw.ImageDraw, x: float, y: float, text: str, size: int = 19) -> None:
    draw.text(_xy(x, y), text, font=_font(size), fill=INK)


def _entry(draw: ImageDraw.ImageDraw, x: float, y: float, text: str, size: int = 30) -> None:
    """Typed text, one line per newline."""
    for row, line in enumerate(text.split("\n")):
        draw.text(_xy(x, y + row * 23), line, font=_font(size), fill=FILL_BLUE)


def _amount(draw: ImageDraw.ImageDraw, right: float, y: float, amount: int) -> None:
    """A dollar amount right-aligned against a box edge, as payroll software prints it."""
    text = _money(amount)
    width = draw.textlength(text, font=_font(30)) / _SCALE
    _entry(draw, right - 12 - width, y, text)


def _money_box(draw: ImageDraw.ImageDraw, x0: float, y0: float, x1: float, y1: float,
               label: str, amount: int) -> None:
    _rect(draw, x0, y0, x1, y1)
    _label(draw, x0 + 9, y0 + 5, label)
    _amount(draw, x1, y0 + 24, amount)


def _checkbox(draw: ImageDraw.ImageDraw, x: float, y: float, label: str, checked: bool) -> None:
    for row, word in enumerate(label.split("\n")):
        draw.text(_xy(x, y + row * 9), word, font=_font(14), fill=INK)
    _rect(draw, x + 4, y + 22, x + 24, y + 42)
    if checked:
        _entry(draw, x + 8, y + 20, "X", size=28)


def _draw_header(draw: ImageDraw.ImageDraw, w2: W2) -> None:
    _rect(draw, _LEFT, 0, 255, 52)
    _rect(draw, 255, 0, 530, 52)
    _label(draw, 265, 5, "a Employee's social security number")
    _entry(draw, 300, 22, w2.employee_ssn)
    _label(draw, 545, 32, "OMB No. 1545-0008", size=18)
    draw.text(_xy(8, 12), "SYNTHETIC TEST DOCUMENT\nCLASSROOM USE ONLY", font=_font(17), fill=WATERMARK)
    for row, text in enumerate(("This information is being furnished to the Internal Revenue Service.",
                                "If you are required to file a tax return, a negligence penalty or other",
                                "sanction may be imposed on you if this income is taxable and you fail",
                                "to report it.")):
        draw.text(_xy(700, 4 + row * 11), text, font=_font(14), fill=INK)


def _draw_left_block(draw: ImageDraw.ImageDraw, w2: W2) -> None:
    _rect(draw, _LEFT, 52, _MID, 104)
    _label(draw, 9, 57, "b Employer identification number (EIN)")
    _entry(draw, 340, 66, w2.employer_ein)
    _rect(draw, _LEFT, 104, _MID, 260)
    _label(draw, 9, 109, "c Employer's name, address, and ZIP code")
    _entry(draw, 95, 135, f"{w2.employer_name}\n{w2.employer_address}")
    _rect(draw, _LEFT, 260, _MID, 312)
    _label(draw, 9, 265, "d Control number")
    _entry(draw, 180, 276, w2.control_number)
    _rect(draw, _LEFT, 312, _MID, 547)
    _label(draw, 9, 317, "e Employee's first name and initial")
    _label(draw, 300, 317, "Last name")
    _label(draw, 600, 317, "Suff.")
    _entry(draw, 95, 340, w2.employee_first)
    _entry(draw, 305, 340, w2.employee_last)
    _entry(draw, 95, 363, w2.employee_address)
    _label(draw, 9, 528, "f Employee's address and ZIP code")


def _draw_right_block(draw: ImageDraw.ImageDraw, w2: W2) -> None:
    rows = [("1 Wages, tips, other compensation", w2.box1_wages, "2 Federal income tax withheld", w2.box2_fed_withheld),
            ("3 Social security wages", w2.box3_ss_wages, "4 Social security tax withheld", w2.box4_ss_withheld),
            ("5 Medicare wages and tips", w2.box5_medicare_wages, "6 Medicare tax withheld", w2.box6_medicare_withheld),
            ("7 Social security tips", w2.box7_ss_tips, "8 Allocated tips", w2.box8_allocated_tips)]
    for index, (left_label, left_amount, right_label, right_amount) in enumerate(rows):
        y0 = 52 + index * 52
        _money_box(draw, _MID, y0, _HALF, y0 + 52, left_label, left_amount)
        _money_box(draw, _HALF, y0, _RIGHT, y0 + 52, right_label, right_amount)
    _rect(draw, _MID, 260, _HALF, 312)
    _rect(draw, _MID + 28, 260, _HALF, 312, fill=SHADE)
    _label(draw, _MID + 8, 265, "9")
    _money_box(draw, _HALF, 260, _RIGHT, 312, "10 Dependent care benefits", w2.box10_dependent_care)
    _money_box(draw, _MID, 312, _HALF, 364, "11 Nonqualified plans", w2.box11_nonqualified)

    _rect(draw, _MID, 364, _HALF, 416)
    _label(draw, _MID + 8, 369, "13")
    for offset, label, checked in ((28, "Statutory\nemployee", w2.box13_statutory),
                                   (110, "Retirement\nplan", w2.box13_retirement_plan),
                                   (185, "Third-party\nsick pay", w2.box13_third_party_sick)):
        _checkbox(draw, _MID + offset, 366, label, checked)
    _rect(draw, _MID, 416, _HALF, 547)
    _label(draw, _MID + 8, 421, "14 Other")
    _entry(draw, _MID + 12, 442, w2.box14_other, size=24)

    codes = list(w2.box12) + [("", 0)] * (4 - len(w2.box12))
    for index, (code, amount) in enumerate(codes[:4]):
        y0 = 312 + index * 52
        _rect(draw, _HALF, y0, _RIGHT, y0 + 52)
        _label(draw, _HALF + 8, y0 + 5, f"12{'abcd'[index]}" + (" See instructions for box 12" if index == 0 else ""))
        for row, letter in enumerate("Code"):
            draw.text(_xy(_HALF + 10, y0 + 18 + row * 8), letter, font=_font(11), fill=INK)
        draw.line((*_xy(_HALF + 75, y0 + 24), *_xy(_HALF + 75, y0 + 50)), fill=INK, width=2)
        _entry(draw, _HALF + 30, y0 + 22, code)
        _amount(draw, _RIGHT, y0 + 22, amount)
    _rect(draw, _HALF, 520, _RIGHT, 547, fill=SHADE)


def _draw_state_rows(draw: ImageDraw.ImageDraw, w2: W2) -> None:
    columns = [(0, 63, "15 State"), (63, 345, "Employer's state ID number"),
               (345, 532, "16 State wages, tips, etc."), (532, 703, "17 State income tax"),
               (703, 890, "18 Local wages, tips, etc."), (890, 1063, "19 Local income tax"),
               (1063, 1172, "20 Locality name")]
    values = [w2.state, w2.state_id, w2.box16_state_wages, w2.box17_state_tax, w2.box18_local_wages,
              w2.box19_local_tax, w2.box20_locality]
    for (x0, x1, label), value in zip(columns, values):
        _rect(draw, x0, 547, x1, 650)
        _label(draw, x0 + 6, 552, label, size=17 if x1 - x0 < 120 else 19)
        if isinstance(value, int):
            _amount(draw, x1, 568, value)
        else:
            _entry(draw, x0 + 12, 568, value)
    for x in range(0, 1172, 12):  # dashed line between the two state rows
        draw.line((*_xy(x, 604), *_xy(x + 6, 604)), fill=INK, width=1)


def _draw_footer(draw: ImageDraw.ImageDraw) -> None:
    draw.text(_xy(0, 668), "Form", font=_font(18), fill=INK)
    draw.text(_xy(30, 652), "W-2", font=_font(58), fill=INK)
    draw.text(_xy(118, 660), "Wage and Tax\nStatement", font=_font(26), fill=INK)
    draw.text(_xy(430, 656), "2025", font=_font(52), fill=INK)
    draw.text(_xy(780, 664), "Department of the Treasury - Internal Revenue Service", font=_font(19), fill=INK)
    draw.text(_xy(0, 710), "Copy B - To Be Filed With Employee's FEDERAL Tax Return.", font=_font(20), fill=INK)
    draw.text(_xy(0, 726), "This information is being furnished to the Internal Revenue Service.",
              font=_font(19), fill=INK)


def make_w2_image(w2: W2, path: Path) -> Path:
    """Draw a filled-in W-2 on the official box layout and write its sidecar JSON next to it.

    Labels are black and entries blue, so OCR can tell them apart by position under each label.
    """
    if not 900 <= int(w2.employee_ssn[:3]) <= 999:
        raise ValueError("synthetic W-2s may only carry 900-series test SSNs")
    image = Image.new("RGB", SIZE, PAPER)
    draw = ImageDraw.Draw(image)
    _draw_header(draw, w2)
    _draw_left_block(draw, w2)
    _draw_right_block(draw, w2)
    _draw_state_rows(draw, w2)
    _draw_footer(draw)

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
    for name, w2 in (("w2_image_1.jpg", MARA_W2), ("w2_image_2.jpg", ROSA_W2), ("w2_image_3.jpg", THEO_W2)):
        print("wrote", make_w2_image(w2, DEMO / name).relative_to(ROOT))
    for name, data in JSON_FIXTURES.items():
        _write_json(FIXTURES / name, data)
        print("wrote", (FIXTURES / name).relative_to(ROOT))
    _write_json(DEMO / "eval_case_1" / "case.json", eval_case_1())
    print("wrote demo/eval_case_1/case.json")


if __name__ == "__main__":
    main()
