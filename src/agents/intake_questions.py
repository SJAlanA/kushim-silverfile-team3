"""Intake question catalog, plain-language glossary and which questions apply.

Owner: Agent memory.
Pure data and lookups: no I/O, no tax rules. Skipping a question (e.g. no spouse questions
for a single filer) is dialogue logic only; Eligibility decides what the answers mean.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Question:
    qid: str
    text: str                    # exactly one plain-language question
    kind: str                    # parser to use, see intake_parsing
    topic: str                   # how the resume summary and confirmations refer to it
    glossary: tuple[str, ...] = ()
    sensitive: bool = False      # value is never echoed back


QUESTIONS: tuple[Question, ...] = (
    Question("who_is_answering", "Are you the person this tax return is for?", "who", "who is answering"),
    Question("taxpayer_first_name", "What is your first name?", "name", "your name"),
    Question("taxpayer_last_name", "What is your last name?", "name", "your name"),
    Question("taxpayer_ssn", "What is your Social Security number? This practice system only accepts "
             "test numbers that start with 9.", "ssn", "your Social Security number", sensitive=True),
    Question("taxpayer_dob", "What is your date of birth?", "date", "your date of birth"),
    Question("taxpayer_blind", "Are you legally blind?", "yes_no", "your eyesight", ("legally blind",)),
    Question("can_be_claimed_as_dependent", "Could someone else, such as a family member, list you as a "
             "dependent on their own tax return?", "yes_no", "whether someone else can claim you", ("dependent",)),
    Question("filing_status", "Which of these best describes you for 2025: single, married filing jointly, "
             "married filing separately, head of household, or qualifying surviving spouse?",
             "filing_status", "your filing status", ("filing status",)),
    Question("spouse_died_in_2025", "I'm sorry to have to ask this. Did your spouse pass away during 2025?",
             "yes_no", "your spouse"),
    Question("spouse_first_name", "What is your spouse's first name?", "name", "your spouse's name"),
    Question("spouse_last_name", "What is your spouse's last name?", "name", "your spouse's name"),
    Question("spouse_ssn", "What is your spouse's Social Security number? This practice system only accepts "
             "test numbers that start with 9.", "ssn", "your spouse's Social Security number", sensitive=True),
    Question("spouse_dob", "What is your spouse's date of birth?", "date", "your spouse's date of birth"),
    Question("spouse_blind", "Is your spouse legally blind?", "yes_no", "your spouse's eyesight", ("legally blind",)),
    Question("spouse_itemizes_separately", "On their own return, is your spouse listing deductions one by one "
             "instead of taking the standard amount?", "yes_no", "your spouse's return", ("itemize",)),
    Question("dual_status_alien", "Did you live in the United States as a citizen or resident for only part "
             "of 2025?", "yes_no", "where you lived in 2025"),
    Question("dependents", "Is there anyone, such as a child or relative you support, that you want to list "
             "as a dependent?", "dependents", "your dependents", ("dependent",)),
    Question("documents", "Which tax forms did you receive for 2025, such as a W-2, SSA-1099, 1099-R, "
             "1099-INT or 1099-DIV?", "documents", "your documents"),
    Question("other_income", "Did you have any other income in 2025, for example from your own business, "
             "rental property, selling investments, cryptocurrency, or another country?",
             "other_income", "other income"),
    Question("adjustments", "What is the amount on line 26 of your Schedule 1? You can say none if you "
             "don't have one.", "amount", "adjustments to income", ("adjustments to income",)),
    Question("schedule_values", "Do you have Schedules 1, 2 or 3 with amounts already filled in by a preparer?",
             "schedule_values", "amounts from Schedules 1, 2 and 3"),
    Question("estimated_payments", "How much did you pay the IRS in estimated tax payments for 2025? "
             "You can say none.", "amount", "estimated tax payments", ("estimated tax payments",)),
    Question("refund_destination", "If you get a refund, whose bank account will it go into?", "refund",
             "where a refund would go"),
)

BY_ID: dict[str, Question] = {question.qid: question for question in QUESTIONS}

# Follow-up questions inside one question_id, keyed by (question_id, sub).
SUB_TEXT: dict[tuple[str, str], str] = {
    ("who_is_answering", "relationship"): "What is your relationship to the person this return is for?",
    ("who_is_answering", "authority"): "Do you have legal authority to act for them, such as a power of attorney?",
    ("dependents", "name"): "What is the full name of your {ordinal} dependent?",
    ("dependents", "relationship"): "How is {name} related to you?",
    ("dependents", "ssn"): "What is {name}'s Social Security number? Only test numbers that start with 9 "
                           "are accepted.",
    ("dependents", "more"): "Is there another dependent you want to add?",
    ("documents", "path"): "Where is the file for your {doc_type} saved?",
    ("other_income", "describe"): "Could you tell me briefly what kind of income that was?",
    ("schedule_values", "sch1_line10"): "What is the amount on Schedule 1, line 10?",
    ("schedule_values", "sch2_line3"): "What is the amount on Schedule 2, line 3?",
    ("schedule_values", "sch2_line21"): "What is the amount on Schedule 2, line 21?",
    ("schedule_values", "sch3_line8"): "What is the amount on Schedule 3, line 8?",
    ("schedule_values", "sch3_line15"): "What is the amount on Schedule 3, line 15?",
}

SCHEDULE_FIELDS = ("sch1_line10", "sch2_line3", "sch2_line21", "sch3_line8", "sch3_line15")
SUB_KINDS = {"relationship": "text", "authority": "yes_no", "name": "full_name", "ssn": "ssn",
             "more": "yes_no", "path": "path", "describe": "text", **{field: "amount" for field in SCHEDULE_FIELDS}}

# Said once, the first time a question uses the term. Statements, never questions.
GLOSSARY: dict[str, str] = {
    "legally blind": "Legally blind means your eyesight is very limited even with glasses or contacts, "
                     "as confirmed by an eye doctor.",
    "dependent": "A dependent is someone you support, such as a child or a relative who relies on you.",
    "filing status": "Your filing status is the category the IRS uses for your household.",
    "itemize": "Most people take a standard amount off their income; a few list each deduction instead.",
    "adjustments to income": "Adjustments to income are a few specific items, such as some retirement "
                             "contributions, that lower the income you are taxed on.",
    "estimated tax payments": "Estimated tax payments are payments you send the IRS yourself during the year, "
                              "separate from tax taken out of a paycheck or pension.",
}

ORDINALS = ("first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth")
SPOUSE_DETAIL_QUESTIONS = {"spouse_first_name", "spouse_last_name", "spouse_ssn", "spouse_dob", "spouse_blind"}


def _filing_status(intake: dict) -> str | None:
    answer = intake.get("filing_status")
    return answer["value"] if answer else None


def applies(qid: str, intake: dict, meta: dict) -> bool:
    """Whether a question should be asked, given the answers so far."""
    status = _filing_status(intake)
    if qid in SPOUSE_DETAIL_QUESTIONS:
        return status in ("mfj", "mfs")
    if qid == "spouse_died_in_2025":
        return status in ("mfj", "mfs", "qss") or bool(meta.get("widow_mentioned"))
    if qid == "spouse_itemizes_separately":
        return status == "mfs"
    return True


def next_question_id(intake: dict, meta: dict) -> str | None:
    """The question to ask next: a requested re-ask first, then the first unanswered one."""
    forced = meta.get("force")
    if forced:
        return forced
    flagged = set(meta.get("flagged", []))
    for question in QUESTIONS:
        if question.qid not in intake and question.qid not in flagged and applies(question.qid, intake, meta):
            return question.qid
    return None
