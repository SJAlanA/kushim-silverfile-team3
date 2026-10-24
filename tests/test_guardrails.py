"""Tests for src/guardrails.py: what taxpayers say (classify_utterance) and what agents say back."""
import pytest

from src.guardrails import ADVICE_REPLY, check_outgoing, classify_utterance, scope_items


def flags(text: str) -> dict:
    return classify_utterance(text)


# The three advice examples from the brief, plus other ways people ask the same thing.
@pytest.mark.parametrize("text", [
    "Should I convert my IRA to a Roth?",
    "Would I pay less if we filed separately?",
    "Can I deduct my medications?",
    "Is it better to take the standard deduction?",
    "What do you recommend?",
    "how can I save on taxes this year",
    "I want to lower my tax bill, any tricks?",
    "Is my hearing aid tax deductible?",
    "Should we wait to claim Social Security?",
    "Do you think I should file jointly or separately",
])
def test_advice_requests_are_caught(text):
    assert flags(text)["advice_request"] is True


@pytest.mark.parametrize("text", [
    "Should I type the dashes in my number?",
    "Should I read the numbers out loud?",
    "My son drove me here",
    "I was a teacher for thirty years",
    "yes", "no", "I'm not sure", "March 3, 1955",
    "My own checking account at Tompkins Community Bank",
])
def test_ordinary_answers_do_not_trigger_advice(text):
    assert flags(text)["advice_request"] is False


@pytest.mark.parametrize("text", [
    "My nephew is doing this for me",
    "I'm her son, she just signs",
    "I'm answering for her, she can't see well",
    "My daughter is filling this out for me",
    "I do all of my mother's taxes",
    "I'm helping my father with his paperwork",
])
def test_someone_else_answering_is_noticed(text):
    assert flags(text)["third_party"] is True


@pytest.mark.parametrize("text, signal", [
    ("They said to send the refund to his account", "refund_redirect"),
    ("I want to change the account the refund goes to", "refund_redirect"),
    ("He told me not to tell anyone about this", "secrecy"),
    ("Please keep this a secret", "secrecy"),
    ("He is rushing me and says it has to be done today", "pressure"),
    ("She makes me sign things", "pressure"),
    ("My nephew takes care of all my money", "financial_control"),
    ("I don't know whose account it is", "financial_control"),
    ("I have to give him the refund", "refund_owed_to_other"),
])
def test_exploitation_signals_are_noticed_and_named(text, signal):
    assert signal in flags(text)["exploitation_signals"]


@pytest.mark.parametrize("text", [
    "My son drove me here",
    "My daughter helped me find my W-2 and then I did the rest myself",
    "My neighbor told me about this program",
    "The bank said to bring my statement",
    "My bank handles my direct deposit",
    "I need to hurry, my bus is coming",
    "My grandson set up my computer",
    "I'm a retired nurse",
])
def test_harmless_mentions_of_other_people_do_not_trigger_exploitation(text):
    result = flags(text)
    assert result["third_party"] is False and result["exploitation_signals"] == []


@pytest.mark.parametrize("text, items", [
    ("I rent out my basement to a tenant", ["rental income"]),
    ("I sold some stock last year", ["capital gains"]),
    ("I have some bitcoin", ["digital assets"]),
    ("I'm self-employed", ["self-employment"]),
    ("I got a 1099-NEC", ["self-employment"]),
    ("I need to amend my 2023 return", ["amended or prior-year return"]),
    ("I want to itemize", ["itemized deductions"]),
    ("I also have to do my state return", ["state return"]),
    ("I was born abroad", []),                      # strict: only specific phrases in ordinary speech
    ("I have a bank account abroad", ["foreign income or accounts"]),
    ("I have a foreign bank account", ["foreign income or accounts"]),
])
def test_out_of_scope_items_are_labelled(text, items):
    assert flags(text)["out_of_scope_items"] == items


def test_words_like_foreign_only_count_in_a_description_of_income():
    assert scope_items("I was born in a foreign country") == []
    assert scope_items("foreign", about_income=True) == ["foreign income or accounts"]
    assert scope_items("some stocks", about_income=True) == ["capital gains"]


@pytest.mark.parametrize("text, volunteered", [
    ("My number is 901-11-1037", True), ("901 11 1037", True), ("901111037", True),
    ("My phone is 555-1234", False), ("I was born in 1957", False), ("", False),
])
def test_volunteered_ssn(text, volunteered):
    assert flags(text)["volunteered_ssn"] is volunteered


def test_classifier_handles_empty_and_unusual_input():
    assert not any(flags("").values()) and not any(flags(None).values())
    assert flags("SHOULD I CONVERT MY IRA?")["advice_request"] is True       # case does not matter
    assert flags("I’m her son, she just signs")["third_party"] is True   # curly apostrophe


# ---------------------------------------------------------------------------
# Outgoing replies
# ---------------------------------------------------------------------------

LINES = {"1z": {"value": 30000}, "9": {"value": 36940}}


@pytest.mark.parametrize("text", [
    "You should file separately.",
    "I recommend taking the standard deduction.",
    "It would be better to wait.",
    "You could deduct your medical costs.",
    "To save money, convert your IRA.",
    "Consider filing jointly.",
    "You'd pay less that way.",
])
def test_outgoing_advice_is_blocked(text):
    allowed, reason = check_outgoing(text, LINES)
    assert (allowed, reason) == (False, "advice phrasing")


@pytest.mark.parametrize("text", [
    "Your wages come to $29,000.",
    "That is $30,000.50 in wages.",
    "About 31,000 dollars.",
    "You will owe $1,234 in tax.",
])
def test_outgoing_unsourced_amounts_are_blocked(text):
    assert check_outgoing(text, LINES) == (False, "unsourced dollar amount")
    assert check_outgoing(text, None) == (False, "unsourced dollar amount")


@pytest.mark.parametrize("text", [
    "Your wages are $30,000.",
    "Your total income is 36,940 dollars.",
    "What is your first name?",
    "Please give one total in whole dollars, for example 1,200, or say none.",
    "Thanks, I've updated your date of birth.",
    ADVICE_REPLY,
    "I'm not sure about this one. Let me flag it for a volunteer, and we'll keep going.",
])
def test_outgoing_clean_replies_are_allowed(text):
    assert check_outgoing(text, LINES) == (True, "")


def test_scripted_advice_reply_is_itself_clean_and_in_plain_words():
    assert check_outgoing(ADVICE_REPLY) == (True, "")
    assert "can't give tax advice" in ADVICE_REPLY and "volunteer" in ADVICE_REPLY


def test_every_intake_question_and_gloss_passes_the_outgoing_check():
    from src.agents.intake_questions import GLOSSARY, QUESTIONS, SUB_TEXT

    texts = [q.text for q in QUESTIONS] + list(GLOSSARY.values()) + list(SUB_TEXT.values())
    blocked = [t for t in texts if not check_outgoing(t.format_map(_Blank()))[0]]
    assert blocked == []


class _Blank(dict):
    def __missing__(self, key):
        return key
