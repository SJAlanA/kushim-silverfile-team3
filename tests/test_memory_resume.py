"""Resumability: a session abandoned on Tuesday continues on Friday where it left off (brief 4.3)."""
import re

from conftest import Dialog, build_intake_graph
from src.agents import intake
from src.memory import get_checkpointer, list_sessions, resume_session, resume_summary


def test_session_resumes_after_restart_with_answers_and_summary(tmp_path, case_single):
    db = str(tmp_path / "checkpoints.sqlite")
    tuesday = build_intake_graph(get_checkpointer(db))
    session = Dialog(tuesday, "mara-2025")
    session.start()
    session.answer_until(case_single, "taxpayer_blind")
    answers_before = session.state["intake"]
    tuesday.checkpointer.conn.close()  # the process ends

    friday = build_intake_graph(get_checkpointer(db))  # a new process, same database file
    resumed = resume_session(friday, "mara-2025")
    assert resumed["summary"] == ("Welcome back. Last time we covered your name, your Social Security number "
                                  "and your date of birth. Next, I'd like to ask about your eyesight.")
    assert resumed["pending"]["question_id"] == "taxpayer_blind"
    assert not re.search(r"\d", resumed["summary"])  # no SSN or dollar amounts

    session = Dialog(friday, "mara-2025")
    session.payload = resumed["pending"]
    while session.payload:
        session.say(intake.case_reply(case_single, session.payload))
    final = session.state["intake"]
    assert all(final[qid] == answer for qid, answer in answers_before.items())
    assert session.state["intake_complete"] is True


def test_resume_summary_mentions_filing_status_and_open_flags():
    state = {"intake": {"filing_status": {"value": "mfj", "raw_text": "together", "answered_at": "", "history": []}},
             "hitl": [{"event_id": "e1", "condition": "UNCERTAIN", "status": "open"}]}
    summary = resume_summary(state)
    assert "your filing status (married filing jointly)" in summary
    assert summary.endswith("One question is waiting for a volunteer to look at.")


def test_resume_summary_for_a_session_that_had_not_started():
    assert resume_summary({}) == "Welcome back. We hadn't started the questions yet, so let's begin."


def test_list_sessions_reads_the_checkpoint_database(tmp_path, case_single):
    db = str(tmp_path / "checkpoints.sqlite")
    graph = build_intake_graph(get_checkpointer(db))
    clean = Dialog(graph, "clean")
    clean.start()
    clean.answer_until(case_single, "taxpayer_ssn")
    flagged = Dialog(graph, "flagged")
    flagged.start()
    flagged.say("I'm not sure")
    graph.checkpointer.conn.close()

    rows = {row["session_id"]: row for row in list_sessions(db)}
    assert rows["clean"]["taxpayer"] == "Mara" and rows["clean"]["open_hitl"] == 0
    assert rows["flagged"]["status"] == "awaiting_human" and rows["flagged"]["open_hitl"] == 1
    assert list_sessions(str(tmp_path / "missing.sqlite")) == []
