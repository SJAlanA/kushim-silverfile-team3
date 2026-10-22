"""Intake dialogue: one question at a time, drift, PII, honest uncertainty and escalations (brief 4.3, 4.4)."""
import json

from src.agents import intake
from src.agents.intake_questions import GLOSSARY, ORDINALS, QUESTIONS, SUB_TEXT


def _open_conditions(state: dict) -> list[str]:
    return [event["condition"] for event in state.get("hitl", []) if event["status"] == "open"]


def _assistant_text(state: dict) -> str:
    return " ".join(message.content for message in state["messages"] if message.type == "ai")


def test_every_question_and_follow_up_is_a_single_question():
    texts = [question.text for question in QUESTIONS]
    texts += [text.format(ordinal=ORDINALS[0], name="Lucia", doc_type="W-2") for text in SUB_TEXT.values()]
    texts += [f"{GLOSSARY['dependent']} {QUESTIONS[0].text}"]
    assert all(intake.is_single_question(text) for text in texts)


def test_compound_questions_are_detected():
    assert not intake.is_single_question("What is your name? What is your date of birth?")
    assert not intake.is_single_question("What is your name and what is your date of birth?")


def test_full_clean_intake_asks_one_question_per_turn(dialog, case_single):
    asked = []
    while dialog.payload:
        assert dialog.payload["type"] == "intake_question"
        assert intake.is_single_question(dialog.payload["text"])
        asked.append(dialog.payload["question_id"])
        dialog.say(intake.case_reply(case_single, dialog.payload))
    state = dialog.state
    assert state["intake_complete"] is True
    assert len(asked) == len(set(asked)) == len(state["intake"])
    assert not any(qid.startswith("spouse_") for qid in asked)  # single filer
    assert [doc["doc_id"] for doc in state["documents"]] == ["int_1", "ssa_1", "w2_1"]
    assert not _open_conditions(state)


def test_changed_answer_is_kept_in_history_and_confirmed(dialog, case_single):
    dialog.answer_until(case_single, "taxpayer_blind")
    assert dialog.say("Actually, I need to change my date of birth")["question_id"] == "taxpayer_dob"
    assert dialog.say("April 2, 1954")["question_id"] == "taxpayer_blind"  # picks up where we were
    dob = dialog.state["intake"]["taxpayer_dob"]
    assert dob["value"] == "1954-04-02"
    assert [entry["value"] for entry in dob["history"]] == ["1955-03-14"]
    assert "Thanks, I've updated your date of birth." in _assistant_text(dialog.state)


def test_go_back_reasks_previous_question_without_losing_answers(dialog, case_single):
    dialog.answer_until(case_single, "taxpayer_dob")
    answered_before = set(dialog.state["intake"])
    assert dialog.say("go back")["question_id"] == "taxpayer_ssn"
    assert set(dialog.state["intake"]) == answered_before
    assert dialog.say("901-11-1037")["question_id"] == "taxpayer_dob"


def test_off_topic_reply_gets_gentle_redirect_and_same_question(dialog, case_single):
    dialog.answer_until(case_single, "taxpayer_dob")
    payload = dialog.say("The weather has been lovely")
    assert payload["question_id"] == "taxpayer_dob"
    assert payload["text"].startswith("Sorry, I didn't quite catch that.")
    assert intake.is_single_question(payload["text"])


def test_volunteered_ssn_is_redacted_never_stored_and_explained(dialog):
    dialog.say("Yes, it's me. My social is 123-45-6789")
    state = dialog.state
    assert "123-45-6789" not in json.dumps(state["intake"]) + json.dumps([m.content for m in state["messages"]])
    assert state["intake"]["who_is_answering"]["value"]["is_taxpayer"] is True
    assert intake.VOLUNTEERED_SSN_NOTICE in _assistant_text(state)


def test_non_test_ssn_is_refused_and_not_stored(dialog, case_single):
    dialog.answer_until(case_single, "taxpayer_ssn")
    payload = dialog.say("123-45-6789")
    assert payload["question_id"] == "taxpayer_ssn"
    assert "start with 9" in payload["text"]
    assert "123-45-6789" not in json.dumps([m.content for m in dialog.state["messages"]])
    assert "taxpayer_ssn" not in dialog.state["intake"]
    dialog.say("901-11-1037")
    assert dialog.state["intake"]["taxpayer_ssn"]["value"] == "901-11-1037"
    assert dialog.state["intake"]["taxpayer_ssn"]["raw_text"] == "[SSN redacted]"


def test_not_sure_is_flagged_for_a_volunteer(dialog, case_single):
    dialog.answer_until(case_single, "filing_status")
    assert dialog.say("I'm not sure, to be honest") is None
    state = dialog.state
    assert _open_conditions(state) == ["UNCERTAIN"]
    assert intake.UNCERTAIN_REPLY in _assistant_text(state)


def test_two_unreadable_replies_are_flagged_for_a_volunteer(dialog, case_single):
    dialog.answer_until(case_single, "taxpayer_dob")
    dialog.say("purple")
    assert dialog.say("still purple") is None
    assert _open_conditions(dialog.state) == ["UNCERTAIN"]


def test_advice_request_escalates_and_is_noted(dialog, case_single):
    dialog.answer_until(case_single, "estimated_payments")
    assert dialog.say("Should I convert my IRA to a Roth first?") is None
    state = dialog.state
    assert _open_conditions(state) == ["TAX_ADVICE"]
    assert "Roth" in state["hitl"][0]["details"]["taxpayer_said"]
    assert intake.ADVICE_REPLY in _assistant_text(state)


def test_third_party_without_authority_escalates(dialog):
    assert dialog.say("No")["text"].startswith("What is your relationship")
    assert dialog.say("I'm her neighbour")["text"].startswith("Do you have legal authority")
    assert dialog.say("no") is None
    assert _open_conditions(dialog.state) == ["EXPLOITATION"]


def test_helper_with_power_of_attorney_continues(dialog):
    dialog.say("No, I'm her daughter and I have power of attorney")
    assert dialog.payload["question_id"] == "taxpayer_first_name"
    assert dialog.state["intake"]["who_is_answering"]["value"]["has_authority"] is True
    assert intake.HELPER_REPLY in _assistant_text(dialog.state)


def test_refund_to_an_account_the_taxpayer_cannot_describe_escalates(dialog, case_single):
    dialog.answer_until(case_single, "refund_destination")
    assert dialog.say("I don't know, my nephew set that part up") is None
    assert _open_conditions(dialog.state) == ["EXPLOITATION"]
    assert dialog.state["intake"]["refund_destination"]["value"]["described"] is False


def test_spouse_who_died_in_2025_escalates_filing_status(dialog, case_single):
    dialog.answer_until(case_single, "filing_status")
    assert dialog.say("I'm a widow")["question_id"] == "spouse_died_in_2025"
    assert dialog.say("Yes, in June") is None
    assert _open_conditions(dialog.state) == ["FILING_STATUS_AMBIGUITY"]


def test_each_term_is_explained_only_once(dialog, case_single):
    while dialog.payload:
        dialog.say(intake.case_reply(case_single, dialog.payload))
    assert _assistant_text(dialog.state).count(GLOSSARY["dependent"]) == 1


def test_documents_and_dependents_can_be_given_conversationally(dialog, case_single):
    dialog.answer_until(case_single, "dependents")
    assert dialog.say("Yes, my granddaughter")["text"].startswith("What is the full name of your first")
    assert "related to you" in dialog.say("Lucia Delgado")["text"]
    assert "Social Security number" in dialog.say("She's my granddaughter")["text"]
    assert dialog.say("912-45-6603")["question_id"] == "dependents"
    assert dialog.say("No, that's all")["question_id"] == "documents"
    assert "W-2" in dialog.say("I have a W-2 and an SSA-1099")["text"]
    assert "SSA-1099" in dialog.say("demo/w2_image_1.jpg")["text"]
    dialog.say("data/fixtures/ssa_1.json")
    state = dialog.state
    assert state["intake"]["dependents"]["value"] == [
        {"first_name": "Lucia", "last_name": "Delgado", "relationship": "She's my granddaughter", "ssn": "912-45-6603"}]
    assert [(doc["doc_id"], doc["path"]) for doc in state["documents"]] == [
        ("ssa_1", "data/fixtures/ssa_1.json"), ("w2_1", "demo/w2_image_1.jpg")]
