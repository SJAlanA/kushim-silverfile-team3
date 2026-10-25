"""The filled-form deliverable: HTML with provenance, the IRS PDF, and audit.json."""
import json
import re

import pytest
from pypdf import PdfReader

from src.agents import review
from src.agents.computation import LINE_KEYS
from src.form import render
from src.form.labels import money
from src.form.pdf_form import FILING_STATUS_BOXES, LINE_FIELDS, comb_ssn
from tests.test_review import CASE_1, INTAKE_1, ROOT, answer, build_state

FULL_SSN = re.compile(r"9\d{2}-?\d{2}-?\d{4}")
CASE_2 = json.loads((ROOT / "demo" / "eval_case_2" / "case.json").read_text())
INTAKE_2 = {**INTAKE_1, "taxpayer_first_name": "Rosa", "taxpayer_last_name": "Delgado", "taxpayer_ssn": "902-22-4518",
            "taxpayer_dob": "1957-03-14", "filing_status": "mfj", "spouse_died_in_2025": True,
            "spouse_first_name": "Luis", "spouse_last_name": "Delgado", "spouse_ssn": "902-22-4519",
            "spouse_dob": "1959-09-02", "spouse_blind": False}


def finished(state: dict) -> dict:
    """A state as `finalize` sees it: reviewed and passed."""
    update = review.run(state)
    return {**state, "review": update["review"], "review_iterations": update["review_iterations"],
            "handoffs": state.get("handoffs", []) + update["handoffs"]}


@pytest.fixture(scope="module")
def case_1(tmp_path_factory):
    state = finished(build_state(INTAKE_1, CASE_1["documents"]))
    folder = tmp_path_factory.mktemp("outputs")
    render.OUTPUT_DIR, original = folder, render.OUTPUT_DIR
    try:
        outputs = render.render_all(state)
    finally:
        render.OUTPUT_DIR = original
    return state, outputs


def pdf_values(path) -> dict:
    return {name: field.get("/V") for name, field in PdfReader(path).get_fields().items()}


def short(values: dict) -> dict:
    return {name.split(".")[-1]: value for name, value in values.items()}


# --- paths --------------------------------------------------------------------------------

def test_render_all_writes_three_files_in_the_session_folder(case_1):
    state, outputs = case_1
    assert set(outputs) == {"html", "pdf", "audit"}
    for kind, name in (("html", "form_1040sr.html"), ("pdf", "form_1040sr.pdf"), ("audit", "audit.json")):
        assert outputs[kind].endswith(f"review-test/{name}") and __import__("pathlib").Path(outputs[kind]).is_file()


def test_a_hostile_session_id_cannot_leave_the_outputs_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "OUTPUT_DIR", tmp_path)
    assert render.session_folder({"session_id": "../../etc/passwd"}).parent == tmp_path


# --- HTML -----------------------------------------------------------------------------------

def test_every_dollar_line_has_a_provenance_popover(case_1):
    state, outputs = case_1
    html = open(outputs["html"]).read()
    for key in LINE_KEYS:
        value = money(state["lines"][key]["value"])
        assert f'aria-describedby="prov-{key}"' in html and f'id="prov-{key}" role="tooltip"' in html
        popover = html.split(f'id="prov-{key}"')[1].split("</div>")[0]
        assert "Formula" in popover and "Sources" in popover and "Tool" in popover and "Entered" in popover
        assert f">{value}</button>" in html.split(f'aria-describedby="prov-{key}"')[1][:200]


def test_popovers_explain_documents_and_typed_in_answers(case_1):
    _, outputs = case_1
    html = open(outputs["html"]).read()
    line_1a = html.split('id="prov-1a"')[1].split("</div>")[0]
    line_26 = html.split('id="prov-26"')[1].split("</div>")[0]
    assert "W-2 w2_1: box 1, wages" in line_1a and "30,000" in line_1a
    assert "Direct user input" in line_26 and "Entered by the taxpayer" in line_26
    assert "2025 rate schedule" in html.split('id="prov-16"')[1].split("</div>")[0]


def test_html_is_self_contained_watermarked_and_masks_the_ssn(case_1):
    _, outputs = case_1
    html = open(outputs["html"]).read()
    assert "CLASSROOM DRAFT - DO NOT FILE" in html and "***-**-1037" in html
    assert not FULL_SSN.search(html)
    assert not re.search(r"""(src|href)=["']https?:""", html) and "@import" not in html


def test_html_lists_lines_in_form_order_by_section(case_1):
    _, outputs = case_1
    html = open(outputs["html"]).read()
    positions = [html.index(f'aria-describedby="prov-{key}"') for key in LINE_KEYS]
    assert positions == sorted(positions)
    assert [title for title in ("Filing status", "Dependents", "Income", "Tax and Credits", "Payments",
                                "Refund / Amount you owe") if title in html] == ["Filing status", "Dependents", "Income",
                                                                                "Tax and Credits", "Payments",
                                                                                "Refund / Amount you owe"]


def test_html_escapes_text_from_the_taxpayer(tmp_path, monkeypatch, case_1):
    monkeypatch.setattr(render, "OUTPUT_DIR", tmp_path)
    state = {**case_1[0], "session_id": "xss"}
    state["intake"] = {**state["intake"], "taxpayer_first_name": answer("<script>alert(1)</script>")}
    html = open(render.render_all(state)["html"]).read()
    assert "<script>alert(1)" not in html and "&lt;script&gt;" in html


# --- PDF ------------------------------------------------------------------------------------

def test_pdf_line_fields_match_the_return(case_1):
    state, outputs = case_1
    values = short(pdf_values(outputs["pdf"]))
    assert {key: values[f"{field}[0]"] for key, field in LINE_FIELDS.items()} == \
           {key: money(state["lines"][key]["value"]) for key in LINE_FIELDS}
    assert set(LINE_FIELDS) == set(LINE_KEYS)  # every in-scope line has a field, so none is left blank


def test_pdf_identity_fits_the_form_and_only_sourced_boxes_are_ticked(case_1):
    _, outputs = case_1
    values = pdf_values(outputs["pdf"])
    by_short = short(values)
    assert (by_short["f1_14[0]"], by_short["f1_15[0]"], by_short["f1_16[0]"]) == ("Mara", "Lindqvist", "901111037")
    ticked = {name for name, value in values.items() if value not in (None, "", "/Off") and ".c" in name}
    assert ticked == {"topmostSubform[0]." + FILING_STATUS_BOXES["single"][0],
                      "topmostSubform[0].Page2[0].Line12d_ReadOrder[0].c2_17[0]"}
    assert not any("c1_10" in name for name in ticked)  # digital assets: no intake answer, so no tick


def test_pdf_is_watermarked_with_a_provenance_note_on_every_page(case_1):
    _, outputs = case_1
    reader = PdfReader(outputs["pdf"])
    acroform = reader.trailer["/Root"]["/AcroForm"]
    assert "/XFA" not in acroform and bool(acroform.get("/NeedAppearances")) is True
    for page in reader.pages:
        text = page.extract_text()
        assert "CLASSROOM DRAFT - DO NOT FILE" in text and "Provenance: see form_1040sr.html / audit.json" in text


def test_pdf_for_a_joint_return_fills_the_spouse_and_both_12d_boxes(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "OUTPUT_DIR", tmp_path)
    state = finished(build_state(INTAKE_2, CASE_2["documents"]))
    values = pdf_values(render.render_all(state)["pdf"])
    by_short = short(values)
    assert (by_short["f1_17[0]"], by_short["f1_18[0]"], by_short["f1_19[0]"]) == ("Luis", "Delgado", "902224519")
    assert values["topmostSubform[0]." + FILING_STATUS_BOXES["mfj"][0]] == "/2"
    assert by_short["c2_17[0]"] == by_short["c2_19[0]"] == "/1"
    assert {key: by_short[f"{LINE_FIELDS[key]}[0]"] for key in ("12e", "13b", "34")} == \
           {"12e": "34,700", "13b": "12,000", "34": "1,200"}


def test_comb_ssn_is_nine_digits():
    assert comb_ssn("901-11-1037") == "901111037" and comb_ssn(None) == ""


# --- audit.json ---------------------------------------------------------------------------

def test_audit_has_every_line_rule_event_and_review_round(case_1):
    state, outputs = case_1
    audit = json.load(open(outputs["audit"]))
    assert audit["lines"] == json.loads(json.dumps(state["lines"]))
    assert audit["rules"]["findings"] and all(f["citation"].startswith("policy.md#") for f in audit["rules"]["findings"])
    assert audit["review"]["result"]["passed"] and audit["review"]["iterations"] == 1
    assert [m["to_agent"] for m in audit["review"]["rounds"]] == ["finalize"]
    assert audit["outputs"] == outputs and audit["notice"] == "CLASSROOM DRAFT - DO NOT FILE"


def test_audit_has_no_full_ssn(case_1):
    _, outputs = case_1
    text = open(outputs["audit"]).read()
    assert not FULL_SSN.search(text) and "***-**-1037" in text


def test_audit_keeps_hitl_resolutions(tmp_path, monkeypatch, case_1):
    monkeypatch.setattr(render, "OUTPUT_DIR", tmp_path)
    resolution = {"decision": "continue", "note": "Checked", "resolved_by": "Volunteer", "resolved_at": "t",
                  "updates": {}}
    event = {"event_id": "e1", "condition": "DOC_CONFLICT", "reason": "r", "raised_by": "eligibility",
             "status": "resolved", "resolution": resolution, "details": {}}
    audit = json.load(open(render.render_all({**case_1[0], "session_id": "hitl", "hitl": [event]})["audit"]))
    assert audit["hitl"][0]["resolution"]["resolved_by"] == "Volunteer"


def test_dependents_appear_masked_in_html_and_filled_in_the_pdf(tmp_path, monkeypatch, case_1):
    monkeypatch.setattr(render, "OUTPUT_DIR", tmp_path)
    state = {**case_1[0], "session_id": "deps"}
    kid = {"first_name": "Ana", "last_name": "Lindqvist", "ssn": "903-12-3456", "relationship": "granddaughter"}
    state["intake"] = {**state["intake"], "dependents": answer([kid])}
    outputs = render.render_all(state)
    html = open(outputs["html"]).read()
    assert "Ana" in html and "***-**-3456" in html and "903-12-3456" not in html
    by_short = short(pdf_values(outputs["pdf"]))
    assert (by_short["f1_31[0]"], by_short["f1_35[0]"], by_short["f1_39[0]"], by_short["f1_43[0]"]) == \
           ("Ana", "Lindqvist", "903123456", "granddaughter")


def test_rendering_tolerates_unexpected_shapes(tmp_path, monkeypatch):
    """finalize must never crash: a bare state and free-text review issues still render."""
    monkeypatch.setattr(render, "OUTPUT_DIR", tmp_path)
    outputs = render.render_all({"session_id": "bare", "review": {"passed": False, "issues": ["checked by hand"]}})
    assert "checked by hand" in open(outputs["html"]).read() and json.load(open(outputs["audit"]))["lines"] == {}
