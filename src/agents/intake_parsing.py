"""Deterministic parsers for Intake replies.

Owner: Agent memory.
Each parser returns the parsed value or None when the reply can't be read with confidence.
They only read what the person said; none of them does arithmetic or applies a tax rule.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime

_UNCERTAIN = re.compile(r"\b(i\s*don'?t know|dunno|not sure|unsure|no idea|no clue|can'?t remember|"
                        r"don'?t remember|i'?m confused)\b")
_YES = re.compile(r"^(yes|yeah|yep|yup|y|correct|right|i am|i do|i did|it is|that'?s right|that'?s me|true|sure)\b")
_NO = re.compile(r"^(no|nope|nah|n|not|never|i'?m not|i am not|i don'?t|i do not|i didn'?t|false|none)\b")
_NAME = re.compile(r"^[A-Za-z][A-Za-z' .-]{0,49}$")
_NAME_PREFIX = re.compile(r"^(my (first |last |full )?name is|it'?s|it is|i'?m|i am|call me)\s+", re.I)
_AMOUNT = re.compile(r"\$?\s*(\d[\d,]*)(?:\.(\d{1,2}))?")
_NONE_AMOUNT = re.compile(r"^(none|no|nothing|zero|n/?a|nil|\$?0+(\.0+)?)\b")
_PER_UNIT = re.compile(r"\b(each|per|times|payments of|every)\b")
_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%B %d, %Y", "%B %d %Y", "%b %d, %Y", "%b %d %Y",
                 "%d %B %Y", "%d %b %Y")

_STATUS_PATTERNS = (
    ("qss", re.compile(r"qualifying surviving spouse|surviving spouse|widow|widower|\bqss\b")),
    ("mfj", re.compile(r"jointly|joint|together|\bmfj\b")),
    ("mfs", re.compile(r"separately|separate|\bmfs\b")),
    ("hoh", re.compile(r"head of (the )?household|\bhoh\b")),
    ("single", re.compile(r"\bsingle\b|not married|unmarried")),
)
_WIDOW = re.compile(r"widow|passed away|died|surviving")

_DOC_TYPES = (
    ("SSA-1099", re.compile(r"ssa[- ]?1099|social security (benefit )?statement")),
    ("1099-INT", re.compile(r"1099[- ]?int")),
    ("1099-DIV", re.compile(r"1099[- ]?div")),
    ("1099-R", re.compile(r"1099[- ]?r\b")),
    ("1099-NEC", re.compile(r"1099[- ]?nec")),
    ("1099-B", re.compile(r"1099[- ]?b\b")),
    ("1099-MISC", re.compile(r"1099[- ]?misc")),
    ("W2", re.compile(r"\bw[- ]?2\b")),
)
DOC_ID_PREFIX = {"W2": "w2", "SSA-1099": "ssa", "1099-INT": "int", "1099-DIV": "div", "1099-R": "r"}

_BACK = re.compile(r"^(go back|back|previous question|last question|undo)\b")
_CHANGE = re.compile(r"\b(change|update|correct|fix) (my|that|the)\b|made a mistake|that'?s wrong|i meant\b")
_CHANGE_TOPICS = (
    ("spouse_dob", re.compile(r"spouse.*(birth|born)|(birth|born).*spouse")),
    ("spouse_first_name", re.compile(r"spouse.*name")),
    ("spouse_ssn", re.compile(r"spouse.*(social security|ssn)")),
    ("taxpayer_last_name", re.compile(r"last name|surname")),
    ("taxpayer_first_name", re.compile(r"\bname\b")),
    ("taxpayer_dob", re.compile(r"birth|born|\bdob\b|birthday")),
    ("taxpayer_ssn", re.compile(r"social security|\bssn\b")),
    ("filing_status", re.compile(r"filing status|\bstatus\b|married|single")),
    ("taxpayer_blind", re.compile(r"blind|eyesight")),
    ("dependents", re.compile(r"dependent")),
    ("documents", re.compile(r"document|form|w-?2|1099")),
    ("estimated_payments", re.compile(r"estimated")),
    ("refund_destination", re.compile(r"refund|bank")),
    ("other_income", re.compile(r"income")),
)

# A refund the taxpayer can't place in their own (or joint) account is an exploitation signal.
_REFUND_UNKNOWN = re.compile(r"don'?t know|not sure|no idea|someone else|somebody|set it up|"
                             r"\b(nephew|niece|son|daughter|grandson|granddaughter|friend|neighbou?r|"
                             r"caregiver|helper|boyfriend|girlfriend)'?s\b|\b(his|her|their) (account|bank)\b")
_REFUND_OWN = re.compile(r"\b(my|our|mine|ours)\b|joint|paper check|a check|mail|own account|checking|savings")
_AUTHORITY_WORDS = re.compile(r"power of attorney|\bpoa\b|guardian|conservator|legal authority|authori[sz]ed")


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def is_uncertain(text: str) -> bool:
    return bool(_UNCERTAIN.search(normalize(text)))


def parse_json(text: str):
    stripped = (text or "").strip()
    if not stripped or stripped[0] not in "[{":
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


def parse_yes_no(text: str) -> bool | None:
    lowered = normalize(text)
    if _NO.search(lowered):  # checked first so "i am not" isn't read as "i am"
        return False
    if _YES.search(lowered):
        return True
    has_yes, has_no = bool(re.search(r"\byes\b", lowered)), bool(re.search(r"\bno\b", lowered))
    return has_yes if has_yes != has_no else None


def parse_name(text: str) -> str | None:
    candidate = _NAME_PREFIX.sub("", (text or "").strip()).strip(" .")
    return candidate if candidate and _NAME.match(candidate) else None


def parse_date(text: str) -> str | None:
    candidate = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", (text or "").strip().rstrip("."))
    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(candidate, fmt).date()
        except ValueError:
            continue
        return parsed.isoformat() if date(1900, 1, 1) <= parsed <= date(2025, 12, 31) else None
    return None


def parse_amount(text: str) -> int | None:
    """Whole dollars. Amounts with cents, or a per-unit figure instead of a total, are re-asked."""
    lowered = normalize(text)
    if _NONE_AMOUNT.match(lowered):
        return 0
    matches = _AMOUNT.findall(lowered)
    if len(matches) != 1 or _PER_UNIT.search(lowered):
        return None
    dollars, cents = matches[0]
    if cents and int(cents) != 0:
        return None
    return int(dollars.replace(",", ""))


def parse_filing_status(text: str) -> tuple[str | None, bool]:
    """Return (status, widow_mentioned)."""
    lowered = normalize(text)
    widow = bool(_WIDOW.search(lowered))
    for status, pattern in _STATUS_PATTERNS:
        if pattern.search(lowered):
            return status, widow
    return None, widow


def find_doc_types(text: str) -> list[str]:
    """Document types in the order the person mentioned them."""
    lowered = normalize(text)
    found = [(match.start(), doc_type) for doc_type, pattern in _DOC_TYPES for match in pattern.finditer(lowered)]
    return [doc_type for _, doc_type in sorted(found)]


def parse_navigation(text: str) -> tuple[str, str | None] | None:
    """("back", None), ("change", question_id or None), or None."""
    lowered = normalize(text)
    if _BACK.search(lowered):
        return "back", None
    if _CHANGE.search(lowered):
        topic = next((qid for qid, pattern in _CHANGE_TOPICS if pattern.search(lowered)), None)
        return "change", topic
    return None


def refund_is_described(text: str) -> bool | None:
    lowered = normalize(text)
    if _REFUND_UNKNOWN.search(lowered):
        return False
    return True if _REFUND_OWN.search(lowered) else None


def states_authority(text: str) -> bool:
    return bool(_AUTHORITY_WORDS.search(normalize(text)))
