"""Table-driven tests for the Eligibility decision table (src/agents/eligibility_rules.py)."""
import itertools
import re
from pathlib import Path

import pytest

from src.agents import eligibility
from src.agents.eligibility_rules import FILING_STATUSES, decide, name_matches

ROOT = Path(__file__).resolve().parent.parent
SENIOR, YOUNG = "1958-06-21", "1962-03-02"   # born before / after 2 January 1961


def answers(**overrides) -> dict:
    """A complete, clean set of answers for a single senior; tests override what they vary."""
    base = {"filing_status": "single", "taxpayer_dob": SENIOR, "taxpayer_blind": False,
            "taxpayer_first_name": "Mara", "taxpayer_last_name": "Lindqvist", "taxpayer_ssn": "901-11-1037",
            "can_be_claimed_as_dependent": False, "dual_status_alien": False, "dependents": [],
            "other_income": {"has_other_income": False, "description": ""}}
    return {**base, **overrides}


def joint(**overrides) -> dict:
    spouse = {"filing_status": "mfj", "spouse_dob": SENIOR, "spouse_blind": False, "spouse_first_name": "Luis",
              "spouse_last_name": "Lindqvist", "spouse_ssn": "901-11-2222", "spouse_died_in_2025": False}
    return answers(**{**spouse, **overrides})


def conditions(decision) -> list[str]:
    return [e.condition for e in decision.escalations]


def record(doc_type, extractor="json", **fields) -> dict:
    return {"doc_type": doc_type, "extractor": extractor, "fields": fields}


# ---------------------------------------------------------------------------
# 1040-SR eligibility (R-ELIG-01)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status, taxpayer_dob, spouse_dob, eligible", [
    ("single", "1961-01-01", None, True),     # the last eligible birthday
    ("single", "1961-01-02", None, False),    # the first ineligible birthday
    ("single", "1940-12-31", None, True),
    ("hoh", "1970-05-05", None, False),
    ("qss", "1959-05-05", None, True),
    ("mfs", YOUNG, SENIOR, False),            # separate returns: the spouse's age doesn't help
    ("mfj", YOUNG, SENIOR, True),             # joint: one spouse is enough
    ("mfj", SENIOR, YOUNG, True),
    ("mfj", YOUNG, "1970-01-01", False),
])
def test_eligibility_by_birth_date(status, taxpayer_dob, spouse_dob, eligible):
    decision = decide(answers(filing_status=status, taxpayer_dob=taxpayer_dob, spouse_dob=spouse_dob,
                              spouse_blind=False, spouse_died_in_2025=False), [], {})
    assert decision.rules["eligible_1040sr"] is eligible
    assert ("OUT_OF_SCOPE" in conditions(decision)) is (not eligible)
    assert decision.rules["in_scope"] is eligible


def test_missing_birth_date_is_not_decided_and_does_not_escalate():
    decision = decide(answers(taxpayer_dob=None), [], {})
    assert decision.rules["eligible_1040sr"] is None
    assert decision.rules["box_count"] is None
    assert decision.escalations == []


# ---------------------------------------------------------------------------
# Line 12d boxes and senior qualifiers (R-SD-02, R-SR-01): every status x box combination
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status, tp_senior, tp_blind, sp_senior, sp_blind",
                         [(s, *combo) for s in FILING_STATUSES
                          for combo in itertools.product([True, False], repeat=4)])
def test_box_count_and_qualifiers_for_every_combination(status, tp_senior, tp_blind, sp_senior, sp_blind):
    spouse_on_return = status in ("mfj", "mfs")
    given = answers(filing_status=status, taxpayer_dob=SENIOR if tp_senior else YOUNG, taxpayer_blind=tp_blind,
                    spouse_dob=SENIOR if sp_senior else YOUNG, spouse_blind=sp_blind, spouse_died_in_2025=False,
                    dependents=[{"first_name": "Sam"}])
    rules = decide(given, [], {}).rules
    expected_boxes = [tp_senior, tp_blind] + ([sp_senior, sp_blind] if spouse_on_return else [])
    assert rules["box_count"] == expected_boxes.count(True)
    expected_qualifiers = 0 if status == "mfs" else int(tp_senior) + (int(sp_senior) if status == "mfj" else 0)
    assert rules["senior_qualifiers"] == expected_qualifiers
    assert rules["filing_status"] == status


def test_boxes_are_named_so_a_volunteer_can_read_them():
    rules = decide(joint(taxpayer_blind=True), [], {}).rules
    assert rules["boxes"] == {"taxpayer_65_or_older": True, "taxpayer_blind": True,
                              "spouse_65_or_older": True, "spouse_blind": False}
    assert rules["box_count"] == 3 and rules["senior_qualifiers"] == 2


def test_unanswered_blindness_means_no_count_rather_than_a_guess():
    rules = decide(answers(taxpayer_blind=None), [], {}).rules
    assert rules["box_count"] is None and rules["boxes"] is None


def test_separate_filer_spouse_boxes_are_counted_with_a_note_for_the_volunteer():
    rules = decide(answers(filing_status="mfs", spouse_dob=SENIOR, spouse_blind=False,
                           spouse_itemizes_separately=False, spouse_died_in_2025=False), [], {}).rules
    assert rules["box_count"] == 2 and rules["senior_qualifiers"] == 0
    assert any("confirm the spouse" in f["text"] for f in rules["findings"])


# ---------------------------------------------------------------------------
# Escalation triggers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("overrides, subject", [
    ({"can_be_claimed_as_dependent": True}, "can_be_claimed_as_dependent"),
    ({"dual_status_alien": True}, "dual_status_alien"),
    ({"filing_status": "mfs", "spouse_dob": SENIOR, "spouse_blind": False, "spouse_itemizes_separately": True,
      "spouse_died_in_2025": False}, "spouse_itemizes_separately"),
])
def test_standard_deduction_chart_exceptions_escalate_out_of_scope(overrides, subject):
    decision = decide(answers(**overrides), [], {})
    assert [(e.condition, e.subject, e.rule) for e in decision.escalations] == [("OUT_OF_SCOPE", subject, "R-SD-04")]
    assert decision.rules["in_scope"] is False


def test_spouse_itemizing_only_matters_on_a_separate_return():
    decision = decide(joint(spouse_itemizes_separately=True), [], {})
    assert decision.escalations == []


@pytest.mark.parametrize("doc_type, rule", [
    ("1099-NEC", "R-SCOPE-04"), ("1099-B", "R-SCOPE-06"), ("1099-DA", "R-SCOPE-07"),
    ("SCHEDULE E", "R-SCOPE-05"), ("1099-MISC", "R-SCOPE-11"), ("K-1", "R-SCOPE-11"),
])
def test_out_of_scope_documents_escalate_and_stay_out_of_the_map(doc_type, rule):
    documents = [{"doc_id": "x_1", "doc_type": doc_type, "path": "x"}]
    decision = decide(answers(), documents, {"x_1": record(doc_type, nonemployee=4200)})
    assert [(e.condition, e.subject, e.rule) for e in decision.escalations] == [("OUT_OF_SCOPE", "x_1", rule)]
    assert decision.escalations[0].details()["doc_id"] == "x_1"
    assert "x_1" not in decision.rules["doc_line_map"]


@pytest.mark.parametrize("description, label", [
    ("I rent out a room", "rental income"), ("some bitcoin I sold", "digital assets"),
    ("a foreign pension", "foreign income or accounts"), ("sold my stock", "capital gains"),
    ("my own business", "self-employment"),
])
def test_out_of_scope_other_income_is_decided_here_not_in_intake(description, label):
    decision = decide(answers(other_income={"has_other_income": True, "description": description}), [], {})
    assert conditions(decision) == ["OUT_OF_SCOPE"]
    assert label in decision.escalations[0].extra["items"]


@pytest.mark.parametrize("description", ["a raffle prize", "a pension from Canada", ""])
def test_other_income_that_matches_nothing_is_uncertain_not_assumed_in_scope(description):
    decision = decide(answers(other_income={"has_other_income": True, "description": description}), [], {})
    assert [(e.condition, e.rule) for e in decision.escalations] == [("UNCERTAIN", "R-SCOPE-12")]


def test_box_2b_escalates_even_when_everything_else_is_present_and_in_scope():
    documents = [{"doc_id": "w2_1", "doc_type": "W2", "path": "a"}, {"doc_id": "r_1", "doc_type": "1099-R", "path": "b"}]
    extracted = {"w2_1": record("W2", employee_name="Mara Lindqvist", employee_ssn_last4="1037", box1_wages=30000),
                 "r_1": record("1099-R", box1_gross=15000, box2a_taxable=0, box2b_taxable_not_determined=True)}
    decision = decide(answers(), documents, extracted)
    assert [(e.condition, e.subject, e.rule) for e in decision.escalations] == [
        ("R1099_TAXABLE_NOT_DETERMINED", "r_1", "R-1099R-01")]
    assert decision.rules["filing_status"] == "single" and decision.rules["box_count"] == 1


def test_a_failed_reading_is_left_to_extraction():
    documents = [{"doc_id": "r_1", "doc_type": "1099-R", "path": "b"}]
    decision = decide(answers(), documents, {"r_1": record("1099-R", extractor="failed")})
    assert decision.escalations == [] and "r_1" not in decision.rules["doc_line_map"]


def test_spouse_who_died_in_2025_escalates_filing_status_ambiguity():
    decision = decide(joint(spouse_died_in_2025=True), [], {})
    assert [(e.condition, e.subject, e.rule) for e in decision.escalations] == [
        ("FILING_STATUS_AMBIGUITY", "spouse_died_in_2025", "R-FS-03")]
    assert decision.rules["filing_status"] == "mfj" and decision.rules["box_count"] == 2


@pytest.mark.parametrize("status, rule", [("qss", "R-FS-04"), ("hoh", "R-FS-05")])
def test_status_that_needs_a_dependent_escalates_without_one(status, rule):
    decision = decide(answers(filing_status=status), [], {})
    assert [(e.condition, e.rule) for e in decision.escalations] == [("FILING_STATUS_AMBIGUITY", rule)]
    assert decide(answers(filing_status=status, dependents=[{"first_name": "Sam"}]), [], {}).escalations == []


def test_unknown_filing_status_value_escalates():
    decision = decide(answers(filing_status="widowed"), [], {})
    assert conditions(decision) == ["FILING_STATUS_AMBIGUITY"] and decision.rules["filing_status"] is None


# ---------------------------------------------------------------------------
# Documents against what the taxpayer said (R-DOC-01)
# ---------------------------------------------------------------------------

DOCS = [{"doc_id": "w2_1", "doc_type": "W2", "path": "a"}]


@pytest.mark.parametrize("given, document_name, document_last4, conflict", [
    (answers(), "Mara Lindqvist", "1037", False),
    (answers(), "MARA R LINDQVIST", "1037", False),            # case and a middle initial are ignored
    (answers(), "Lindqvist, Mara R.", "1037", False),
    (answers(), "Pat Jones", "1037", True),                    # a different person
    (answers(), "Mara Lindqvist", "9999", True),               # right name, wrong number
    (answers(), "Pat Jones", "9999", True),
    (joint(), "Luis Lindqvist", "2222", False),                # a joint return: the spouse's W-2 is fine
    (joint(), "Luis Lindqvist", "9999", True),
    (answers(filing_status="mfs", spouse_dob=SENIOR, spouse_blind=False, spouse_itemizes_separately=False,
             spouse_died_in_2025=False, spouse_first_name="Luis", spouse_last_name="Lindqvist",
             spouse_ssn="901-11-2222"), "Luis Lindqvist", "2222", True),   # separate: the spouse's W-2 is not theirs
])
def test_w2_must_belong_to_the_taxpayer_or_a_joint_spouse(given, document_name, document_last4, conflict):
    extracted = {"w2_1": record("W2", employee_name=document_name, employee_ssn_last4=document_last4)}
    decision = decide(given, DOCS, extracted)
    assert (conditions(decision) == ["DOC_CONFLICT"]) is conflict
    if conflict:
        assert decision.escalations[0].subject == "w2_1" and decision.escalations[0].rule == "R-DOC-01"


def test_conflict_reason_never_contains_a_social_security_number():
    extracted = {"w2_1": record("W2", employee_name="Mara Lindqvist", employee_ssn_last4="9999")}
    reason = decide(answers(), DOCS, extracted).escalations[0].reason
    assert "1037" not in reason and "9999" not in reason and "Social Security number" in reason


def test_nothing_to_compare_means_no_conflict():
    extracted = {"w2_1": record("W2", box1_wages=1000)}
    assert decide(answers(), DOCS, extracted).escalations == []


@pytest.mark.parametrize("full_name, first, last, expected", [
    ("Theo R Brandt", "Theo", "Brandt", True), ("theo brandt", "THEO", "BRANDT", True),
    ("Brandt, Theo", "Theo", "Brandt", True), ("Theo Brandt", "Theo", "Brant", False),
    ("Theo Brandt", "Theodore", "Brandt", False), ("", "Theo", "Brandt", None), ("Theo Brandt", "", "", None),
])
def test_name_matching(full_name, first, last, expected):
    assert name_matches(full_name, first, last) is expected


# ---------------------------------------------------------------------------
# Document-to-line map and schedules
# ---------------------------------------------------------------------------

def test_doc_line_map_families():
    documents = [{"doc_id": d, "doc_type": t, "path": "x"} for d, t in [
        ("w2_1", "W2"), ("int_1", "1099-INT"), ("div_1", "1099-DIV"), ("ssa_1", "SSA-1099"),
        ("ira_1", "1099-R"), ("pen_1", "1099-R")]]
    extracted = {"ira_1": record("1099-R", ira_sep_simple=True, box2b_taxable_not_determined=False),
                 "pen_1": record("1099-R", ira_sep_simple=False, box2b_taxable_not_determined=False)}
    assert decide(answers(), documents, extracted).rules["doc_line_map"] == {
        "w2_1": "1a", "int_1": "2b", "div_1": "3b", "ssa_1": "6a", "ira_1": "4a", "pen_1": "5a"}


def test_w2_spelled_with_a_dash_is_still_a_w2():
    documents = [{"doc_id": "w2_1", "doc_type": "W-2", "path": "x"}]
    assert decide(answers(), documents, {}).rules["doc_line_map"] == {"w2_1": "1a"}


@pytest.mark.parametrize("given, schedules", [
    ({}, ["1-A"]),
    ({"taxpayer_dob": YOUNG, "filing_status": "single"}, []),
    ({"adjustments": 500}, ["1-A", "1"]),
    ({"schedule_values": {"sch2_line3": 100, "sch3_line8": 50}}, ["1-A", "2", "3"]),
    ({"schedule_values": {"sch1_line10": 0, "sch2_line3": 0}}, ["1-A"]),
])
def test_schedules_that_apply(given, schedules):
    rules = decide(answers(**given), [], {}).rules
    assert rules["schedules"] == schedules


# ---------------------------------------------------------------------------
# Grounding: every finding cites a rule that exists in policy.md
# ---------------------------------------------------------------------------

def test_every_finding_cites_a_real_rule_with_a_matching_anchor():
    policy = eligibility.load_policy()
    scenarios = [decide(answers(), DOCS, {"w2_1": record("W2", employee_name="Pat Jones")}),
                 decide(joint(spouse_died_in_2025=True, can_be_claimed_as_dependent=True), [], {}),
                 decide(answers(filing_status="qss", other_income={"has_other_income": True, "description": "crypto"}),
                        [{"doc_id": "n", "doc_type": "1099-NEC", "path": "x"}], {})]
    for decision in scenarios:
        assert decision.rules["findings"]
        assert eligibility.ungrounded(decision.rules["findings"], policy) == []
        for finding in decision.rules["findings"]:
            assert finding["citation"] == f"policy.md#{finding['rule'].lower()}"


def test_every_rule_id_used_in_the_code_exists_in_the_policy():
    policy = eligibility.load_policy()
    used = set()
    for path in (ROOT / "src").rglob("*.py"):
        used |= set(re.findall(r"\bR-[A-Z0-9]+-\d\d\b", path.read_text()))
    assert used and used <= set(policy), sorted(used - set(policy))


def test_policy_has_the_rules_the_brief_requires():
    required = {"R-ELIG-01", "R-FS-01", "R-FS-02", "R-FS-03", "R-FS-04", "R-FS-05", "R-SD-01", "R-SD-02", "R-SD-03",
                "R-SD-04", "R-SR-01", "R-SS-01", "R-SCOPE-01", "R-1099R-01", "R-ADV-01", "R-EXP-01", "R-SSN-01"}
    assert required <= set(eligibility.load_policy())
