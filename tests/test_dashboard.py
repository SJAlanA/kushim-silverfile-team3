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
    page = client.get("/returns").get_data(as_text=True)
    needs_human = page.split('id="h-needs_human"')[1].split("</section>")[0]
    assert "Mara" in needs_human and "Needs a volunteer" in needs_human and "unclear" in needs_human


def test_session_page_masks_the_ssn(client):
    page = client.get("/returns/dash-1").get_data(as_text=True)
    assert "901-11-1037" not in page and "1037" in page


def test_resolving_an_event_continues_the_graph(client, paused_session):
    _, graph = paused_session
    event_id = graph.get_state(config("dash-1")).values["hitl"][0]["event_id"]
    assert "date of birth" in client.get("/needs-a-volunteer").get_data(as_text=True)

    response = client.post(f"/returns/dash-1/decisions/{event_id}",
                           data={"decision": "continue", "note": "Confirmed by phone", "resolved_by": "Dana",
                                 "updates": "taxpayer_dob = 1958-06-21"})
    assert response.status_code == 302

    values = graph.get_state(config("dash-1")).values
    assert values["hitl"][0]["status"] == "resolved"
    assert values["hitl"][0]["resolution"]["resolved_by"] == "Dana"
    assert values["intake"]["taxpayer_dob"]["value"] == "1958-06-21"
    assert values["status"] == "complete"
    completed = client.get("/returns").get_data(as_text=True).split('id="h-completed"')[1]
    assert "Mara" in completed


def test_resolve_needs_a_name(client, paused_session):
    _, graph = paused_session
    event_id = graph.get_state(config("dash-1")).values["hitl"][0]["event_id"]
    response = client.post(f"/returns/dash-1/decisions/{event_id}", data={"decision": "continue", "resolved_by": ""})
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


def test_each_line_says_in_one_sentence_where_its_amount_came_from():
    from src.dashboard.views import line_row

    line = {"value": 5, "provenance": {"tool": "add", "formula": "", "method_note": "Sum of the listed amounts",
                                       "sources": [{"line": "1z"}, {"line": "2b"}, {"rules": "box_count"}]}}
    row = line_row("9", "Total income", line, {})
    assert row["how"] == "Added up line 1z, line 2b and the number of boxes checked on line 12d."
    assert row["method"] == ""


def test_structured_answers_read_as_short_lines_not_json():
    from src.dashboard.views import readable

    assert readable({"is_taxpayer": True, "relationship": None, "has_authority": None}) == ["Answering for themselves: Yes"]
    assert readable({"sch1_line10": 0}) == ["Schedule 1, line 10: 0"]
    assert readable([{"doc_id": "w2_1", "doc_type": "W2", "path": "demo/w2_image_2.jpg"}]) == ["W-2 (w2_1)"]
    assert readable("1958-06-21") == "June 21, 1958"
    assert readable("mfj", "filing_status") == "Married filing jointly" and readable([]) == "None"


def test_session_page_shows_no_raw_json_or_placeholder_sources(client):
    page = client.get("/returns/dash-1").get_data(as_text=True)
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
                                             "source_path": "demo/w2_image_1.jpg",
                                             "fields": {"box1_wages": 30000, "employer_name": "Willowbrook"}}}})
    assert rows[0]["type"] == "W-2" and rows[0]["how"] == "Read from the image with text recognition (OCR)"
    assert rows[0]["fields"] == [("Box 1: Wages, tips, other compensation", "30,000"), ("Employer's name", "Willowbrook")]
    assert rows[0]["file"] == "w2_image_1.jpg"


@pytest.mark.parametrize("source_path, status", [("demo/w2_image_1.jpg", 200), ("../../../../etc/hosts", 404),
                                                 ("/etc/hosts", 404), ("", 404)])
def test_a_document_link_serves_only_the_recorded_file_inside_the_repository(client, paused_session, source_path, status):
    _, graph = paused_session
    record = {"doc_id": "w2_1", "doc_type": "W2", "fields": {}, "extractor": "ocr", "confidence": 1.0,
              "source_path": source_path}
    graph.update_state(config("dash-1"), {"extracted": {"w2_1": record}})
    assert client.get("/returns/dash-1/documents/w2_1").status_code == status


def test_a_correction_picked_in_the_form_is_read_like_the_taxpayers_answer(client, paused_session):
    _, graph = paused_session
    event_id = graph.get_state(config("dash-1")).values["hitl"][0]["event_id"]
    response = client.post(f"/returns/dash-1/decisions/{event_id}",
                           data={"decision": "continue", "resolved_by": "Dana",
                                 "correct_qid": "taxpayer_dob", "correct_value": "June 21, 1958"})
    assert response.status_code == 302
    assert graph.get_state(config("dash-1")).values["intake"]["taxpayer_dob"]["value"] == "1958-06-21"


def test_an_unreadable_correction_is_refused_in_plain_words(client, paused_session):
    _, graph = paused_session
    event_id = graph.get_state(config("dash-1")).values["hitl"][0]["event_id"]
    response = client.post(f"/returns/dash-1/decisions/{event_id}",
                           data={"decision": "continue", "resolved_by": "Dana",
                                 "correct_qid": "taxpayer_dob", "correct_value": "sometime last spring"})
    assert response.status_code == 400 and "doesn&#39;t read as a date" in response.get_data(as_text=True)
    assert graph.get_state(config("dash-1")).values["hitl"][0]["status"] == "open"


def test_resolve_page_uses_plain_words_not_condition_codes(client, paused_session):
    _, graph = paused_session
    event_id = graph.get_state(config("dash-1")).values["hitl"][0]["event_id"]
    page = client.get(f"/returns/dash-1/decisions/{event_id}").get_data(as_text=True)
    assert "An answer or document is unclear" in page and "Uncertain" not in page
    assert "extract dispatch" not in page and "Read the documents again" in page


def test_where_an_amount_came_from_names_the_document_box():
    from src.dashboard.views import how, term_label

    extracted = {"w2_1": {"doc_type": "W2"}}
    assert how("add", [{"doc_id": "w2_1", "field": "box1_wages"}], extracted) == \
        "Copied from W-2 (w2_1), box 1: Wages, tips, other compensation."
    assert how("add", [{"line": "1z"}, {"line": "2b"}, {"line": "3b"}], {}) == "Added up lines 1z, 2b and 3b."
    assert how("max0", [{"line": "11b"}, {"line": "14"}], {}) == "Line 11b minus line 14, and never less than 0."
    assert how("add", [{"none": "nothing"}], {}) == "Nothing was reported for this line, so it is 0."
    assert term_label("div_1 1099-DIV withheld") == "1099-DIV (div_1), box 4: Federal income tax withheld"


def test_review_says_what_it_checked_without_tool_jargon():
    from src.dashboard.views import review_summary

    summary = review_summary({"review": {"passed": True, "iteration": 1}, "handoffs": [
        {"from_agent": "review", "to_agent": "finalize", "kind": "handoff",
         "reason": "Every check passed (26 independent tax_math recomputations)."}]})
    assert summary["text"].startswith("Passed on the first check.") and "tax_math" not in summary["text"]
    assert summary["sent_back"] == []


def test_a_failed_review_lists_its_issues_in_words_not_raw_data():
    from src.dashboard.views import review_summary

    issue = {"line": "9", "check": "recomputed_total", "expected": 36940, "found": 36000, "send_to": "computation"}
    summary = review_summary({"review": {"passed": False, "issues": [issue, "a plain note"], "iteration": 1}})
    assert summary["issues"] == ["Line 9: recomputed total. Expected 36,940, found 36,000.", "a plain note"]


def test_policy_citations_become_named_links():
    from src.dashboard.policy import link_citations

    html = str(link_citations("Joint return allowed (policy.md#r-fs-02). <b>"))
    assert 'href="/policy#r-fs-02"' in html and "policy rule R-FS-02: Married at the end of the year" in html
    assert html.endswith("</a>). &lt;b&gt;")


def test_urls_name_the_page_and_old_links_redirect(client):
    assert client.get("/").headers["Location"].endswith("/returns")
    assert client.get("/sessions/dash-1").headers["Location"].endswith("/returns/1")
    assert client.get("/returns/1").status_code == 200 and client.get("/returns/NOPE").status_code == 404
    assert client.get("/policy").status_code == 200


def test_full_ssn_is_shown_only_on_request_and_never_cached(client):
    page = client.get("/returns/dash-1").get_data(as_text=True)
    assert "901-11-1037" not in page and "/returns/1/ssn/taxpayer_ssn" in page
    response = client.post("/returns/dash-1/ssn/taxpayer_ssn")
    assert response.get_json() == {"value": "901-11-1037"} and response.headers["Cache-Control"] == "no-store"
    assert client.post("/returns/dash-1/ssn/taxpayer_first_name").status_code == 404
