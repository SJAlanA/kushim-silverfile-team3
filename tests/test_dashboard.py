"""The dashboard lists sessions and resolving a HITL event lets the graph carry on."""
import pytest

from src.hitl import raise_hitl
from src.memory import get_checkpointer
from tests.orchestration_helpers import answer, config, pending, stub_agents


@pytest.fixture
def paused_session(monkeypatch, tmp_path):
    """A session in the SQLite store, paused on an UNCERTAIN event raised by Intake."""
    monkeypatch.chdir(tmp_path)
    db = str(tmp_path / "checkpoints.sqlite")

    def intake(state):
        if not state.get("hitl"):
            return {"intake": {"taxpayer_first_name": answer("Mara"), "taxpayer_ssn": answer("901-11-1037")},
                    **raise_hitl("UNCERTAIN", "The taxpayer wasn't sure about her date of birth.", "intake")}
        return {"intake_complete": True}

    stub_agents(monkeypatch, intake=intake)
    from src.graph import build_graph

    graph = build_graph(get_checkpointer(db))
    graph.invoke({"session_id": "dash-1"}, config("dash-1"))
    assert pending(graph, config("dash-1"))["type"] == "hitl"
    return db, graph


@pytest.fixture
def client(paused_session):
    from src.dashboard import create_app

    app = create_app(paused_session[0])
    app.testing = True
    return app.test_client()


def test_sessions_page_groups_the_paused_session(client):
    page = client.get("/").get_data(as_text=True)
    needs_human = page.split('id="h-needs_human"')[1].split("</section>")[0]
    assert "Mara" in needs_human and "Needs a volunteer" in needs_human and "unclear" in needs_human


def test_session_page_masks_the_ssn(client):
    page = client.get("/sessions/dash-1").get_data(as_text=True)
    assert "901-11-1037" not in page and "1037" in page


def test_resolving_an_event_continues_the_graph(client, paused_session):
    _, graph = paused_session
    event_id = graph.get_state(config("dash-1")).values["hitl"][0]["event_id"]
    assert "date of birth" in client.get("/hitl").get_data(as_text=True)

    response = client.post(f"/sessions/dash-1/hitl/{event_id}",
                           data={"decision": "continue", "note": "Confirmed by phone", "resolved_by": "Dana",
                                 "updates": "taxpayer_dob = 1958-06-21"})
    assert response.status_code == 302

    values = graph.get_state(config("dash-1")).values
    assert values["hitl"][0]["status"] == "resolved"
    assert values["hitl"][0]["resolution"]["resolved_by"] == "Dana"
    assert values["intake"]["taxpayer_dob"]["value"] == "1958-06-21"
    assert values["status"] == "complete"
    completed = client.get("/").get_data(as_text=True).split('id="h-completed"')[1]
    assert "Mara" in completed


def test_resolve_needs_a_name(client, paused_session):
    _, graph = paused_session
    event_id = graph.get_state(config("dash-1")).values["hitl"][0]["event_id"]
    response = client.post(f"/sessions/dash-1/hitl/{event_id}", data={"decision": "continue", "resolved_by": ""})
    assert response.status_code == 400
    assert graph.get_state(config("dash-1")).values["hitl"][0]["status"] == "open"


# --- how the session page lays out calculations and answers -------------------------------

def test_an_addition_becomes_a_tape_of_terms_and_a_total():
    from src.dashboard.views import calculation

    steps = calculation("30,000 (line 1z wages) + 300 (line 2b interest) + 0 (line 20 (Schedule 3, line 8)) = 30,300")
    assert steps == [{"kind": "sum", "number": "", "result": "30,300",
                      "terms": [("30,000", "line 1z wages"), ("300", "line 2b interest"),
                                ("0", "line 20 (Schedule 3, line 8)")]}]


def test_a_worksheet_splits_into_numbered_steps_with_results_on_the_right():
    from src.dashboard.views import calculation

    steps = calculation("1. Benefits 12,000; 7. 36,400 - 0 = 36,400; MAGI 36,940 is not over 75,000, no phase-out")
    assert [(s["number"], s["text"], s["result"]) for s in steps] == [
        ("1", "Benefits", "12,000"), ("7", "36,400 - 0", "36,400"),
        ("", "MAGI 36,940 is not over 75,000, no phase-out", "")]


def test_line_sources_are_named_and_grouped():
    from src.dashboard.views import line_row

    line = {"value": 5, "provenance": {"tool": "add", "formula": "", "method_note": "Sum of the listed amounts",
                                       "sources": [{"line": "1z"}, {"line": "2b"}, {"rules": "box_count"}]}}
    row = line_row("9", "Total income", line, {})
    assert row["sources"] == ["Lines 1z, 2b", "Eligibility rules: line 12d box count"] and row["method"] == ""


def test_structured_answers_read_as_short_lines_not_json():
    from src.dashboard.views import readable

    assert readable({"is_taxpayer": True, "relationship": None, "has_authority": None}) == ["Answering for themselves: Yes"]
    assert readable({"sch1_line10": 0}) == ["Schedule 1, line 10: 0"]
    assert readable([{"doc_id": "w2_1", "doc_type": "W2", "path": "demo/w2_image_2.jpg"}]) == ["W-2 (w2_1)"]
    assert readable("1958-06-21") == "June 21, 1958"
    assert readable("mfj", "filing_status") == "Married filing jointly" and readable([]) == "None"


def test_session_page_shows_no_raw_json_or_placeholder_sources(client):
    page = client.get("/sessions/dash-1").get_data(as_text=True)
    assert '{"' not in page and "answer: " not in page


def test_lines_that_come_to_zero_are_folded_apart():
    from src.dashboard.views import line_sections

    def line(value):
        return {"value": value, "provenance": {"tool": "add", "formula": "", "sources": []}}

    sections = line_sections({"lines": {"1z": line(30000), "4a": line(0), "34": line(662), "37": line(0)}})
    assert [(s["title"], [r["key"] for r in s["rows"]], [r["key"] for r in s["zero_rows"]]) for s in sections] == [
        ("Income", ["1z"], ["4a"]), ("Refund / Amount you owe", ["34"], ["37"])]


def test_outcome_is_a_refund_an_amount_owed_or_neither():
    from src.dashboard.views import outcome

    def lines(refund, owed):
        return {"lines": {"34": {"value": refund}, "37": {"value": owed}}}

    assert outcome(lines(662, 0))["label"] == "Refund" and outcome(lines(0, 90))["value"] == 90
    assert outcome(lines(0, 0))["kind"] == "even" and outcome({"lines": {}}) is None


def test_documents_read_in_plain_words():
    from src.dashboard.views import documents

    rows = documents({"extracted": {"w2_1": {"doc_type": "W2", "extractor": "ocr", "confidence": 0.96,
                                             "fields": {"box1_wages": 30000, "employer_name": "Willowbrook"}}}})
    assert rows[0]["type"] == "W-2" and rows[0]["how"] == "Read from the image (OCR)"
    assert rows[0]["fields"] == [("Box 1, wages", "30,000"), ("Employer name", "Willowbrook")]
