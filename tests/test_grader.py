"""Grader's dashboard: a case fills the taxpayer page, stops for every HITL event, and continues after a decision."""
import pytest

from src.taxpayer import grader


@pytest.fixture
def apps(monkeypatch, tmp_path):
    from src.dashboard import create_app as staff_app
    from src.taxpayer import create_app as taxpayer_app

    monkeypatch.setattr("src.form.render.OUTPUT_DIR", tmp_path / "outputs")
    db = str(tmp_path / "checkpoints.sqlite")
    public, staff = taxpayer_app(db, str(tmp_path / "uploads")), staff_app(db)
    public.testing = staff.testing = True
    return public.test_client(), staff.test_client()


def fill(public, case_id: str) -> str:
    """Start a case from the grader's dashboard. Returns the new return's session id."""
    response = public.post(f"/grader/cases/{case_id}")
    assert response.status_code == 302
    return response.headers["Location"].rsplit("/", 1)[-1]


def run_page(public, case_id: str, session_id: str) -> str:
    return public.get(f"/grader/cases/{case_id}/{session_id}").get_data(as_text=True)


def test_the_catalog_has_both_demo_cases_and_every_negative_case():
    cases = grader.catalog()
    assert [c["id"] for c in cases.values() if c["group"] == "demo"] == ["eval_case_1", "eval_case_2"]
    assert len([c for c in cases.values() if c["group"] == "negative"]) == 13
    assert all(c["case"]["answers"] and c["case"]["documents"] for c in cases.values())


def test_the_grader_page_lists_every_case(apps):
    public, _ = apps
    page = public.get("/grader").get_data(as_text=True)
    assert "Fill eval case 1" in page and "Fill eval case 2" in page and "Advice request" in page


def test_eval_case_1_fills_to_a_draft_that_still_needs_a_release(apps):
    public, _ = apps
    session_id = fill(public, "eval_case_1")
    page = run_page(public, "eval_case_1", session_id)
    assert "Draft ready to release" in page and "✗" not in page
    assert public.get(f"/r/{session_id}/form.pdf").status_code == 404  # not until a volunteer releases it


def test_eval_case_2_stops_for_the_volunteer_then_finishes_after_the_decision(apps):
    public, staff = apps
    session_id = fill(public, "eval_case_2")
    page = run_page(public, "eval_case_2", session_id)
    assert "A volunteer is needed" in page and "Filing status" in page and "Married filing jointly" in page

    event_id = _open_event(page)
    staff.post(f"/returns/{session_id}/decisions/{event_id}",
               data={"decision": "continue", "resolved_by": "Grader", "correct_qid": "filing_status",
                     "correct_value": "married filing jointly"})
    assert "Ready to carry on" in run_page(public, "eval_case_2", session_id)

    public.post(f"/grader/cases/eval_case_2/{session_id}/continue")
    page = run_page(public, "eval_case_2", session_id)
    assert "Draft ready to release" in page and "✗" not in page and page.count("✓") == 10

    staff.post(f"/returns/{session_id}/release", data={"checked": "yes", "released_by": "Grader"})
    assert "Released to the taxpayer" in run_page(public, "eval_case_2", session_id)
    assert public.get(f"/r/{session_id}/form.pdf").status_code == 200


def test_a_negative_case_ends_as_the_case_file_expects_when_the_volunteer_stops(apps):
    public, staff = apps
    session_id = fill(public, "advice_request")
    page = run_page(public, "advice_request", session_id)
    assert "Taxpayer asked for advice" in page and "Stop here and prepare this return by hand" in page

    staff.post(f"/returns/{session_id}/decisions/{_open_event(page)}", data={"decision": "stop", "resolved_by": "Grader"})
    page = run_page(public, "advice_request", session_id)
    assert "Stopped by a volunteer" in page and "✗" not in page


def test_unknown_cases_and_a_switched_off_dashboard_are_not_found(apps, monkeypatch):
    public, _ = apps
    assert public.post("/grader/cases/no_such_case").status_code == 404
    monkeypatch.setenv("GRADER_DASHBOARD", "0")
    assert public.get("/grader").status_code == 404
    assert public.post("/grader/cases/eval_case_1").status_code == 404


def _open_event(page: str) -> str:
    """The event id in the run page's link to the staff dashboard decision."""
    import re

    return re.search(r"/decisions/([^\"]+)\"", page).group(1)
