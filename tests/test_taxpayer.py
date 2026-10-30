"""Taxpayer page: one question at a time, uploads checked, and the form only after a volunteer releases it."""
import io
import re

import pytest
from PIL import Image

from src.taxpayer import DOC_CHOICES, save_uploads, split_question, stage

ANSWERS = {"who_is_answering": "yes", "taxpayer_first_name": "Mara", "taxpayer_last_name": "Lindqvist",
           "taxpayer_ssn": "901-11-1037", "taxpayer_dob": "1958-06-21", "taxpayer_blind": "no",
           "can_be_claimed_as_dependent": "no", "filing_status": "single", "dual_status_alien": "no",
           "dependents": "no", "other_income": "no", "adjustments": "none", "schedule_values": "no",
           "estimated_payments": "0", "refund_destination": "My own checking account"}
_FIELDS = re.compile(r'name="question_id" value="([^"]*)"><input type="hidden" name="sub" value="([^"]*)"')


@pytest.fixture
def db(tmp_path) -> str:
    return str(tmp_path / "checkpoints.sqlite")


@pytest.fixture
def apps(monkeypatch, tmp_path, db):
    from src.dashboard import create_app as staff_app
    from src.taxpayer import create_app as taxpayer_app

    monkeypatch.setattr("src.form.render.OUTPUT_DIR", tmp_path / "outputs")
    public, staff = taxpayer_app(db, str(tmp_path / "uploads")), staff_app(db)
    public.testing = staff.testing = True
    return public.test_client(), staff.test_client()


def start(client) -> tuple[str, str]:
    response = client.post("/start")
    session_id = response.headers["Location"].rsplit("/", 1)[-1]
    return session_id, client.get(f"/r/{session_id}").get_data(as_text=True)


def answer_everything(client, session_id: str) -> str:
    """Answer each question the page shows until the questions end. Returns the last page."""
    for _ in range(60):
        page = client.get(f"/r/{session_id}").get_data(as_text=True)
        found = _FIELDS.search(page)
        if not found:
            return page
        qid, sub = found.groups()
        if qid == "documents":
            client.post(f"/r/{session_id}/documents", data={"question_id": qid, "sub": sub, "none": "yes"})
        else:
            client.post(f"/r/{session_id}/answer", data={"question_id": qid, "sub": sub, "reply": ANSWERS[qid]})
    raise AssertionError("the questions never ended")


def photo(fmt: str = "PNG") -> io.BytesIO:
    buffer = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(buffer, fmt)
    buffer.seek(0)
    return buffer


# --- asking ---------------------------------------------------------------------------------

def test_starting_a_return_gives_a_private_link_and_the_first_question(apps):
    public, _ = apps
    session_id, page = start(public)
    assert re.fullmatch(r"web-[0-9a-f]{32}", session_id)
    assert "Are you the person this tax return is for?" in page and ">Yes<" in page
    assert "Go back to the last question" not in page  # nothing to go back to yet


def test_the_link_is_kept_private(apps):
    public, _ = apps
    session_id, _ = start(public)
    headers = public.get(f"/r/{session_id}").headers
    assert headers["Referrer-Policy"] == "no-referrer" and headers["Cache-Control"] == "no-store"
    assert "noindex" in headers["X-Robots-Tag"]


@pytest.mark.parametrize("path", ["/r/web-0123", "/r/eval_case_1-3d7db7", "/r/web-" + "0" * 32])
def test_unknown_or_malformed_links_are_not_found(apps, path):
    public, _ = apps
    assert public.get(path).status_code == 404


def test_an_answer_moves_to_the_next_question(apps):
    public, _ = apps
    session_id, _ = start(public)
    public.post(f"/r/{session_id}/answer", data={"question_id": "who_is_answering", "sub": "", "reply": "yes"})
    assert "What is your first name?" in public.get(f"/r/{session_id}").get_data(as_text=True)


def test_a_stale_submit_does_not_answer_the_wrong_question(apps):
    public, _ = apps
    session_id, _ = start(public)
    public.post(f"/r/{session_id}/answer", data={"question_id": "who_is_answering", "sub": "", "reply": "yes"})
    public.post(f"/r/{session_id}/answer", data={"question_id": "who_is_answering", "sub": "", "reply": "yes"})
    assert "What is your first name?" in public.get(f"/r/{session_id}").get_data(as_text=True)


def test_a_real_ssn_is_refused_kindly_and_never_shown(apps):
    public, _ = apps
    session_id, _ = start(public)
    for qid, reply in (("who_is_answering", "yes"), ("taxpayer_first_name", "Mara"), ("taxpayer_last_name", "Lee"),
                       ("taxpayer_ssn", "123-45-6789")):
        public.post(f"/r/{session_id}/answer", data={"question_id": qid, "sub": "", "reply": reply})
    page = public.get(f"/r/{session_id}").get_data(as_text=True)
    assert 'class="note"' in page and "What is your Social Security number?" in page
    assert "6789" not in page


def test_notes_and_explanations_are_split_from_the_question():
    assert split_question("taxpayer_blind", None, "Legally blind means your eyesight is very limited even with "
                          "glasses or contacts, as confirmed by an eye doctor. Are you legally blind?") == (
        "", "Legally blind means your eyesight is very limited even with glasses or contacts, as confirmed by an "
            "eye doctor.", "Are you legally blind?")
    assert split_question("dependents", "name", "Thanks. What is the full name of your first dependent?") == (
        "Thanks.", "", "What is the full name of your first dependent?")


# --- uploads --------------------------------------------------------------------------------

def test_photos_are_saved_under_random_names(tmp_path):
    saved = save_uploads(tmp_path, ["W2"], [_upload("my w2 ../../etc.png", photo())])
    assert saved[0]["doc_type"] == "W2" and saved[0]["path"].endswith(".png")
    assert "etc" not in saved[0]["path"] and (tmp_path / saved[0]["path"].rsplit("/", 1)[-1]).is_file()


@pytest.mark.parametrize("doc_type, content, message", [
    ("W2", io.BytesIO(b"%PDF-1.7 not a photo"), "isn't a photo"),
    ("W2", photo("GIF"), "JPG or PNG"),
    ("1099-NEC", photo(), "which form"),
])
def test_anything_but_a_jpg_or_png_of_a_listed_form_is_refused(tmp_path, doc_type, content, message):
    with pytest.raises(ValueError, match=message):
        save_uploads(tmp_path, [doc_type], [_upload("w2.png", content)])
    assert not list(tmp_path.glob("*.*"))


def test_every_upload_choice_is_a_form_extraction_knows():
    from src.agents import intake_parsing

    assert {code for code, _ in DOC_CHOICES} <= set(intake_parsing.DOC_ID_PREFIX)


def _upload(name, stream):
    from werkzeug.datastructures import FileStorage

    return FileStorage(stream=stream, filename=name)


# --- the volunteer releases the draft -------------------------------------------------------

def test_the_form_appears_only_after_a_volunteer_releases_it(apps):
    public, staff = apps
    session_id, _ = start(public)
    page = answer_everything(public, session_id)
    assert "Your answers are complete" in page and "View my form" not in page
    assert public.get(f"/r/{session_id}/form.pdf").status_code == 404
    assert public.get(f"/r/{session_id}/form.html").status_code == 404

    refused = staff.post(f"/returns/{session_id}/release", data={"released_by": "Dana"}, follow_redirects=True)
    assert "Tick the box" in refused.get_data(as_text=True)
    staff.post(f"/returns/{session_id}/release", data={"checked": "yes", "released_by": "Dana"})

    assert "View my form" in public.get(f"/r/{session_id}").get_data(as_text=True)
    assert public.get(f"/r/{session_id}/form.pdf").status_code == 200
    assert "Released by Dana" in staff.get(f"/returns/{session_id}").get_data(as_text=True)
    assert staff.get(f"/returns/{session_id[-6:].upper()}").status_code == 200  # the short Ref. finds it too


def test_a_draft_can_be_released_only_once_and_only_with_a_name(apps, db):
    from src.graph import build_graph
    from src.memory import get_checkpointer
    from src.release import release_draft

    public, _ = apps
    session_id, _ = start(public)
    answer_everything(public, session_id)
    graph = build_graph(get_checkpointer(db))
    with pytest.raises(ValueError, match="name"):
        release_draft(graph, session_id, "  ")
    release_draft(graph, session_id, "Dana")
    with pytest.raises(ValueError, match="already released"):
        release_draft(graph, session_id, "Sam")


@pytest.mark.parametrize("values, payload, expected", [
    ({"status": "complete", "release": {"released_by": "Dana"}}, None, "released"),
    ({"status": "complete"}, None, "awaiting_release"),
    ({"status": "awaiting_human"}, {"type": "hitl", "event": {}}, "volunteer_checking"),
    ({"status": "escalated"}, None, "volunteer_will_contact"),
    ({"status": "computing"}, None, "working"),
])
def test_the_taxpayer_sees_a_plain_status_and_never_the_staff_reason(values, payload, expected):
    assert stage(values, payload) == expected
