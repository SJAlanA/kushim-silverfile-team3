"""Fill the official IRS fillable Form 1040-SR (2025) with pypdf.

Owner: Security.

Template: data/forms/f1040sr.pdf, downloaded from https://www.irs.gov/pub/irs-pdf/f1040s.pdf. Field
names were checked against the labels printed beside each field on that file (the form carries
no tooltips), not taken on trust.

Fixes for the four problems in the team's earlier test fill:
(a) SSNs go into the 9-character comb fields as 9 digits, so they fit;
(b) every contract line gets a value, 0 included, never blank;
(c) every page carries "Provenance: see form_1040sr.html / audit.json" in the margin, because a
    PDF can't show where numbers come from;
(d) a box is ticked only when an intake answer or Eligibility's rules say so. Nothing asks about
    digital assets, so that question is left blank.
Every page is also stamped "CLASSROOM DRAFT - DO NOT FILE". This is the one output that carries
the full test SSN, as the form requires.
"""
from __future__ import annotations

import re
from pathlib import Path

from pypdf import PageObject, PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, FloatObject, NameObject

from src.form.labels import money

TEMPLATE = Path(__file__).resolve().parents[2] / "data" / "forms" / "f1040sr.pdf"
WATERMARK = "CLASSROOM DRAFT - DO NOT FILE"
MARGIN_NOTE = "Provenance: see form_1040sr.html / audit.json"

LINE_FIELDS = {
    "1a": "f1_47", "1z": "f1_57", "2a": "f2_01", "2b": "f2_02", "3a": "f2_03", "3b": "f2_04", "4a": "f2_05",
    "4b": "f2_06", "5a": "f2_08", "5b": "f2_09", "6a": "f2_11", "6b": "f2_12", "8": "f2_15", "9": "f2_16",
    "10": "f2_17", "11a": "f2_18", "11b": "f2_19", "12e": "f2_20", "13b": "f2_22", "14": "f2_23", "15": "f2_24",
    "16": "f2_26", "17": "f2_27", "18": "f2_28", "21": "f2_31", "22": "f2_32", "23": "f2_33", "24": "f2_34",
    "25a": "f3_01", "25b": "f3_02", "25c": "f3_03", "25d": "f3_04", "26": "f3_05", "32": "f3_12", "33": "f3_13",
    "34": "f3_14", "37": "f3_19",
}
TAXPAYER_FIELDS = {"first": "f1_14", "last": "f1_15", "ssn": "f1_16"}
SPOUSE_FIELDS = {"first": "f1_17", "last": "f1_18", "ssn": "f1_19"}
MFS_SPOUSE_NAME = "f1_28"           # "Married filing separately. Enter spouse's name"
# Filing status checkboxes share the partial name c1_8, so they are addressed by full path.
FILING_STATUS_BOXES = {
    "single": ("Page1[0].Single_ReadOrder[0].c1_8[0]", "/1"),
    "mfj": ("Page1[0].Single_ReadOrder[0].c1_8[1]", "/2"),
    "mfs": ("Page1[0].Single_ReadOrder[0].c1_8[2]", "/3"),
    "hoh": ("Page1[0].c1_8[0]", "/4"),
    "qss": ("Page1[0].c1_8[1]", "/5"),
}
TWELVE_D_BOXES = {"tp_65": "c2_17", "tp_blind": "c2_18", "sp_65": "c2_19", "sp_blind": "c2_20"}
ANSWER_BOXES = {"can_be_claimed_as_dependent": "c2_13", "spouse_itemizes_separately": "c2_15",
                "dual_status_alien": "c2_16"}
# Dependents table: one column per dependent, (first name, last name, SSN, relationship).
DEPENDENT_FIELDS = [("f1_31", "f1_35", "f1_39", "f1_43"), ("f1_32", "f1_36", "f1_40", "f1_44"),
                    ("f1_33", "f1_37", "f1_41", "f1_45"), ("f1_34", "f1_38", "f1_42", "f1_46")]
CHECKED = "/1"


def _answer(state: dict, qid: str):
    return ((state.get("intake") or {}).get(qid) or {}).get("value")


def comb_ssn(ssn: str | None) -> str:
    """'901-11-1037' -> '901111037': the SSN boxes are 9-character comb fields."""
    return re.sub(r"\D", "", ssn or "")[:9]


def field_values(state: dict) -> dict[str, str]:
    """Partial field name or full path -> value, for everything this return puts on the form."""
    rules = state.get("rules") or {}
    status = rules.get("filing_status") or _answer(state, "filing_status")
    values: dict[str, str] = {
        TAXPAYER_FIELDS["first"]: str(_answer(state, "taxpayer_first_name") or ""),
        TAXPAYER_FIELDS["last"]: str(_answer(state, "taxpayer_last_name") or ""),
        TAXPAYER_FIELDS["ssn"]: comb_ssn(_answer(state, "taxpayer_ssn")),
    }
    spouse_name = f"{_answer(state, 'spouse_first_name') or ''} {_answer(state, 'spouse_last_name') or ''}".strip()
    if status == "mfj":
        values.update({SPOUSE_FIELDS["first"]: str(_answer(state, "spouse_first_name") or ""),
                       SPOUSE_FIELDS["last"]: str(_answer(state, "spouse_last_name") or ""),
                       SPOUSE_FIELDS["ssn"]: comb_ssn(_answer(state, "spouse_ssn"))})
    elif status == "mfs" and spouse_name:
        values[MFS_SPOUSE_NAME] = spouse_name
    if status in FILING_STATUS_BOXES:
        path, state_name = FILING_STATUS_BOXES[status]
        values[path] = state_name
    for key, field in TWELVE_D_BOXES.items():
        if (rules.get("boxes") or {}).get(key):
            values[field] = CHECKED
    for qid, field in ANSWER_BOXES.items():
        if _answer(state, qid) is True:
            values[field] = CHECKED
    for person, fields in zip(_answer(state, "dependents") or [], DEPENDENT_FIELDS):
        first, last, ssn, relationship = fields
        values.update({first: str(person.get("first_name", "")), last: str(person.get("last_name", "")),
                       ssn: comb_ssn(person.get("ssn")), relationship: str(person.get("relationship", ""))})
    for key, field in LINE_FIELDS.items():
        line = (state.get("lines") or {}).get(key)
        if line is not None:
            values[field] = money(line["value"])
    return values


def _qualified(names: list[str], wanted: str) -> str | None:
    """The full field name ending with `wanted` (a partial name like 'f1_14' or a path like 'Page1[0]...')."""
    target = wanted if "[" in wanted else f"{wanted}[0]"
    matches = [n for n in names if n == target or n.endswith("." + target)]
    return matches[0] if len(matches) == 1 else None


def _stamp(width: float, height: float) -> PageObject:
    """A transparent page with the diagonal watermark and the margin provenance note."""
    page = PageObject.create_blank_page(width=width, height=height)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica-Bold"),
                             NameObject("/Encoding"): NameObject("/WinAnsiEncoding")})
    faint = DictionaryObject({NameObject("/Type"): NameObject("/ExtGState"), NameObject("/ca"): FloatObject(0.16),
                              NameObject("/CA"): FloatObject(0.16)})
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/SFWm"): font}),
        NameObject("/ExtGState"): DictionaryObject({NameObject("/SFGs"): faint})})
    size, cos, sin = 36, 0.819, 0.574  # 35 degrees
    text_width = 0.66 * size * len(WATERMARK)  # Helvetica-Bold capitals average about 0.66 em
    x, y = (width - text_width * cos) / 2, (height - text_width * sin) / 2
    content = (f"q /SFGs gs 0.60 0.20 0.07 rg BT /SFWm {size} Tf {cos} {sin} {-sin} {cos} {x:.1f} {y:.1f} Tm "
               f"({WATERMARK}) Tj ET Q "
               f"q 0.60 0.20 0.07 rg BT /SFWm 7 Tf 0 1 -1 0 {width - 12:.1f} {height * 0.30:.1f} Tm "
               f"({MARGIN_NOTE}  |  {WATERMARK}) Tj ET Q")
    stream = DecodedStreamObject()
    stream.set_data(content.encode("latin-1"))
    page.replace_contents(stream)
    return page


def fill_pdf(state: dict, path: Path, template: Path = TEMPLATE) -> Path:
    """Write the filled, watermarked form to `path`. Raises FileNotFoundError if the template is missing."""
    writer = PdfWriter(clone_from=PdfReader(template))
    acroform = writer._root_object["/AcroForm"]
    if "/XFA" in acroform:  # the XFA copy of the form would hide the AcroForm values in Acrobat
        del acroform["/XFA"]
    names = list((PdfReader(template).get_fields() or {}).keys())
    values = {}
    for wanted, value in field_values(state).items():
        qualified = _qualified(names, wanted)
        if qualified is None:
            raise KeyError(f"form field {wanted!r} is not on the template")
        values[qualified] = value
    for page in writer.pages:
        # auto_regenerate=True also sets NeedAppearances, so every viewer redraws the filled values
        writer.update_page_form_field_values(page, values, auto_regenerate=True)
        box = page.mediabox
        page.merge_page(_stamp(float(box.width), float(box.height)))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        writer.write(handle)
    return path
