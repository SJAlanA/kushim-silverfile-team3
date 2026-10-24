"""Guardrails: classify what a taxpayer says, and check what agents say back.

Owner: Guardrails and ethics.

Two directions, both deterministic (no model call, so they work offline and cannot be talked
around):

- `classify_utterance(text)` runs on every taxpayer reply. It only *notices*; Intake decides
  what to do with the flags. For exploitation SilverFile notices and stops, it never judges
  (policy.md R-EXP-01), so these patterns lean toward catching too much. Each false positive
  costs a volunteer a short look; each false negative can cost a person their refund.
- `check_outgoing(text, lines)` runs on every agent reply before it reaches the taxpayer.

Patterns are plain regular expressions grouped by name so a volunteer or a teammate can read
and extend them.
"""
from __future__ import annotations

import re

ADVICE_REPLY = ("I can't give tax advice, but I've noted your question for the volunteer "
                "who reviews your return.")

# ---------------------------------------------------------------------------
# Incoming: advice requests (R-ADV-01)
# ---------------------------------------------------------------------------

_ADVICE_PATTERNS = [re.compile(p) for p in (
    r"\bwould (i|we) (pay|owe) (less|more)\b",
    r"\bpay (less|fewer|lower) (tax|taxes)\b",
    r"\bcan (i|we) (deduct|write off|claim|get a deduction)\b",
    r"\btax[- ]deductible\b",
    r"\b(is|are|was|were) [^.?!]{1,40}\bdeductible\b",
    r"\bwrite[- ]?offs?\b",
    r"\bis it (better|smarter|wiser|worth)\b",
    r"\b(better|best|smarter) (for me )?to (file|convert|take|claim|wait)\b",
    r"\bwhich (is|would be) (better|best)\b",
    r"\broth\b",
    r"\bconver(t|ting|sion)\b[^.?!]{0,30}\b(ira|401\(?k\)?|retirement|account)\b",
    r"\bsave (me )?(money|on (my )?tax(es)?|tax(es)?)\b",
    r"\b(lower|reduce|cut|minimi[sz]e) (my|our|the) (tax|taxes|bill|tax bill)\b",
    r"\bavoid (paying )?(tax|taxes)\b",
    r"\bloophole",
    r"\btax (planning|strategy|strategies)\b",
    r"\b(what|how much) (do|would) you (recommend|suggest|advise)\b",
    r"\b(do|would) you (recommend|suggest|advise)\b",
    r"\bwhat would you do\b",
    r"\bwhat (is|'s) the best (way|thing|option|filing status)\b",
    r"\bhow (can|do|could) (i|we) (pay less|save|lower|reduce)\b",
)]
# "Should I" is advice unless it is about answering the question in front of the person.
_SHOULD = re.compile(r"\bshould (?:i|we)\b([^.?!]*)|"
                     r"\b(?:do you think|wonder(?:ing)? (?:if|whether)|not sure (?:if|whether)|tell me (?:if|whether)|"
                     r"know (?:if|whether)|ask(?:ing)? (?:if|whether)) (?:i|we) should\b([^.?!]*)")
_PROCEDURAL = re.compile(r"\b(answer|say|type|enter|write|spell|use|put|include|upload|attach|bring|call|"
                         r"click|press|start|continue|repeat|go back|read|send|scan|photograph|sign in|log in)\b")


def _asks_for_advice(lowered: str) -> bool:
    if any(pattern.search(lowered) for pattern in _ADVICE_PATTERNS):
        return True
    return any(not _PROCEDURAL.search(first + second) for first, second in _SHOULD.findall(lowered))


# ---------------------------------------------------------------------------
# Incoming: someone else answering (R-EXP-01)
# ---------------------------------------------------------------------------

_RELATIVE = (r"(son|daughter|nephew|niece|grandson|granddaughter|grandchild|brother|sister|husband|wife|"
             r"caregiver|care ?giver|guardian|neighbou?r|friend|accountant|lawyer|attorney|aide|helper|pastor|"
             r"financial advisor|advisor|agent)")
_THIRD_PARTY_PATTERNS = [re.compile(p) for p in (
    r"\bdo(ing|es)? (this|it|that|these|all (of )?this|the (form|taxes|paperwork)) for (me|her|him|them)\b",
    r"\bfill(ing|ed|s)? (this|it|the form|these) (out|in) for (me|her|him|them)\b",
    rf"\banswer(ing|s|ed)? (this |these |the questions )?for (her|him|them|my {_RELATIVE})\b",
    r"\b(he|she|they) (just |only )?signs?\b",
    r"\bsigns? (it|these|everything) (for|after)\b",
    rf"\bon (her|his|their|my {_RELATIVE}'s) behalf\b",
    rf"\bi(?: am|'m) (her|his|their) {_RELATIVE}\b",
    rf"\bi(?: am|'m) (her|his|their) (power of attorney|poa|representative|proxy)\b",
    r"\bi(?: am|'m) (helping|assisting) (her|him|them|my (mother|mom|father|dad|aunt|uncle|grandmother|"
    r"grandfather|grandma|grandpa|parent|parents))\b",
    r"\bi (do|handle|manage|take care of) (all )?(of )?(her|his|their|my (mother|mom|father|dad|aunt|uncle|"
    r"grandmother|grandfather|grandma|grandpa)'?s?) (taxes|paperwork|money|finances|banking|bills)\b",
    rf"\bmy {_RELATIVE} (is|are|was) (doing|filling|typing|answering|handling|helping me (fill|type|answer))\b",
    rf"\bmy {_RELATIVE} (told|asked|wants|said) (me )?(to )?(let|have) (him|her|them) (do|handle|answer|sign)\b",
    r"\b(this is|that is|it is) (her|his|their) (return|tax return|taxes|account)\b",
)]

# ---------------------------------------------------------------------------
# Incoming: exploitation and pressure signals (R-EXP-01)
# ---------------------------------------------------------------------------

_EXPLOITATION_PATTERNS: dict[str, list[re.Pattern]] = {
    "secrecy": [re.compile(p) for p in (
        r"\b(told|asked|said) (me )?(not to|to not) (tell|say|mention|talk)\b",
        r"\bdon'?t tell (anyone|anybody|my|the)\b",
        r"\bkeep (it|this|that) (a )?secret\b",
        r"\b(just )?between (us|you and me)\b",
        r"\bno ?one (can|should|needs to|must) know\b",
    )],
    "pressure": [re.compile(p) for p in (
        r"\b(rush|rushed|rushing|hurry|hurried|pressure[sd]?|pressuring|threaten\w*|forc(e|ed|es|ing)) me\b",
        r"\b(made|make|makes|making) me (sign|do this|file|send|give)\b",
        r"\bwon'?t let me (leave|stop|ask|call|talk|wait)\b",
        r"\b(has|have|need|needs) to be (done|signed|sent|filed) (today|tonight|right now|immediately|right away)\b",
        r"\bbefore i (can )?(think|ask anyone|call anyone|talk to anyone)\b",
        r"\bi (don'?t|do not) have (time|a choice) to\b",
        r"\bi'?m afraid (of|to)\b",
    )],
    "refund_redirect": [re.compile(p) for p in (
        r"\b(send|put|deposit|route|wire|move|transfer|direct|mail)\b[^.?!]{0,25}\b(refund|check|money|return)\b"
        r"[^.?!]{0,25}\b(to|into)\b[^.?!]{0,20}\b(him|her|them|his|their|a friend|someone|somebody|a new|another)\b",
        r"\b(change|switch|update|replace)\b[^.?!]{0,25}\b(refund|direct deposit)\b[^.?!]{0,30}"
        r"\b(account|bank|destination)\b",
        r"\b(change|switch|update|replace)\b[^.?!]{0,20}\b(bank|account)\b[^.?!]{0,25}\brefund\b",
        r"\b(he|she|they) (said|says|told me|wants|want|asked) (me )?(to )?(send|put|have|get) (the )?(refund|"
        r"money|check)\b",
        r"\brefund (goes|is going|should go|must go|needs to go) (to|into) (his|her|their|a friend)",
    )],
    "financial_control": [re.compile(p) for p in (
        rf"\b(he|she|they|my {_RELATIVE}) (takes|take|handles|handle|manages|manage|controls|control) "
        r"(care of )?(all )?(of )?my (money|finances|bank|accounts?|checks?|mail|bills)\b",
        rf"\b(he|she|they|my {_RELATIVE}) (has|have|keeps|keep) my (password|pin|bank card|debit card|"
        r"checkbook|social security card|mail)\b",
        r"\b(i|we) (don'?t|do not|can'?t|cannot) (know|remember|say|tell|see) (whose|which|what|the) "
        r"(bank )?(account|routing)\b",
        r"\bnot my (own )?(account|bank account)\b",
    )],
    "refund_owed_to_other": [re.compile(p) for p in (
        r"\bi (have|had|need|must) to (give|send|hand|pay) (him|her|them|it to (him|her|them))\b[^.?!]{0,20}"
        r"\b(refund|money|check)\b",
        r"\bi (owe|promised) (him|her|them) (the |my )?(refund|money|check)\b",
        rf"\b(refund|money|check) (is|are|goes|belongs) (to|for) (him|her|them|my {_RELATIVE})\b",
    )],
}

# ---------------------------------------------------------------------------
# Incoming: out-of-scope items (policy.md R-SCOPE-*)
# ---------------------------------------------------------------------------

# label -> (rule ID, strict pattern, extra patterns used only on text that is already about income)
SCOPE_ITEMS: dict[str, tuple[str, re.Pattern, re.Pattern | None]] = {
    "itemized deductions": ("R-SCOPE-03", re.compile(
        r"\bitemiz\w*|\bschedule a\b|\bmortgage interest\b|\bcharitable (donations?|contributions?|giving)\b|"
        r"\bmedical expenses\b"), None),
    "self-employment": ("R-SCOPE-04", re.compile(
        r"\bself[- ]?employ\w*|\bmy own business\b|\bfreelanc\w*|\bgig (work|income|economy)\b|"
        r"\bside (business|hustle|gig)\b|\bindependent contractor\b|\bschedule c\b|\b1099[- ]?(nec|k|misc)\b|"
        r"\bnon-?employee compensation\b"),
     re.compile(r"\bbusiness\b|\bconsult\w*|\bcontract work\b|\bcommission\b|\bdriving for\b")),
    "rental income": ("R-SCOPE-05", re.compile(
        r"\brental\b|\brent(ed|ing)? (out|my|the)\b|\blandlord\b|\btenants?\b|\bschedule e\b|\broyalt(y|ies)\b"),
     re.compile(r"\brents?\b|\bproperty\b|\bairbnb\b")),
    "capital gains": ("R-SCOPE-06", re.compile(
        r"\bcapital gains?\b|\bschedule d\b|\b1099[- ]?b\b|\bsold (my |some |the |a )?(stocks?|shares?|house|home|"
        r"property|investments?|bonds?|mutual funds?|land|business)\b|\bselling (my |some |the )?(stocks?|"
        r"shares?|investments?|house|home|property)\b"),
     re.compile(r"\bstocks?\b|\bshares\b|\binvestments?\b|\bsale of\b|\bsold\b")),
    "digital assets": ("R-SCOPE-07", re.compile(
        r"\bcrypto\w*|\bbitcoin\b|\bethereum\b|\bdigital (assets?|currenc(y|ies)|coins?|tokens?)\b|\bnfts?\b|"
        r"\b1099[- ]?da\b"), None),
    "foreign income or accounts": ("R-SCOPE-08", re.compile(
        r"\bforeign (income|accounts?|bank|pension|tax|assets?|currency|employer|earnings|wages|company)\b|"
        r"\boverseas (income|accounts?|bank|pension|job|employer|wages)\b|"
        r"\bincome from (abroad|overseas|another country|outside the (u\.?s\.?|united states|country))\b|"
        r"\bfbar\b|\baccounts? (abroad|overseas|in another country)\b"),
     re.compile(r"\bforeign\b|\boverseas\b|\babroad\b|\banother country\b|\boutside the (u\.?s\.?|united states)\b")),
    "amended or prior-year return": ("R-SCOPE-09", re.compile(
        r"\bamend(ed|ing|ment)?\b[^.?!]{0,25}\b(return|taxes|1040|filing)\b|\b1040[- ]?x\b|\bprior[- ]year\b|"
        r"\bprevious (tax )?years?'?s? (return|taxes)\b|\bback taxes\b|\b(2023|2024) (tax )?return\b"), None),
    "state return": ("R-SCOPE-10", re.compile(
        r"\bstate (tax )?(return|taxes|refund|income tax|filing)\b|\bit-201\b"), None),
}
OUT_OF_SCOPE_RULES = {label: rule for label, (rule, _, _) in SCOPE_ITEMS.items()}


def scope_items(text: str, about_income: bool = False) -> list[str]:
    """Labels of out-of-scope items named in `text`, in a fixed order.

    `about_income=True` is for text that is already a description of income (the "other income"
    answer), where single words like "foreign" or "stocks" are safe to act on. In general speech
    they are not ("I was born in a foreign country"), so the strict patterns are used.
    """
    lowered = (text or "").lower()
    found = []
    for label, (_, strict, loose) in SCOPE_ITEMS.items():
        if strict.search(lowered) or (about_income and loose is not None and loose.search(lowered)):
            found.append(label)
    return found


# ---------------------------------------------------------------------------
# Incoming: volunteered identity numbers (R-SSN-01)
# ---------------------------------------------------------------------------

_SSN = re.compile(r"\b\d{3}[- ]?\d{2}[- ]?\d{4}\b")


def classify_utterance(text: str) -> dict:
    """Flags for one taxpayer reply. Intake decides what each flag means for the dialogue.

    - advice_request: asks for advice, planning or optimisation (R-ADV-01)
    - third_party: someone other than the taxpayer appears to be answering (R-EXP-01)
    - exploitation_signals: names of the pressure signals found (R-EXP-01); [] if none
    - out_of_scope_items: labels of out-of-scope items mentioned (R-SCOPE-*)
    - volunteered_ssn: the reply contains a nine-digit number shaped like an SSN (R-SSN-01)
    """
    lowered = " ".join((text or "").lower().replace("’", "'").split())
    return {
        "advice_request": _asks_for_advice(lowered),
        "third_party": any(pattern.search(lowered) for pattern in _THIRD_PARTY_PATTERNS),
        "exploitation_signals": [name for name, patterns in _EXPLOITATION_PATTERNS.items()
                                 if any(pattern.search(lowered) for pattern in patterns)],
        "out_of_scope_items": scope_items(lowered),
        "volunteered_ssn": bool(_SSN.search(text or "")),
    }


# ---------------------------------------------------------------------------
# Outgoing: nothing SilverFile says may be advice or an unsourced amount (R-ADV-01, R-SAFE-01)
# ---------------------------------------------------------------------------

_ADVICE_OUT = re.compile("|".join((
    r"\byou should\b", r"\byou ought to\b", r"\bi (would )?(recommend|suggest|advise)\b",
    r"\bwe (would )?(recommend|suggest|advise)\b", r"\bmy advice\b", r"\bi'?d (advise|suggest|recommend)\b",
    r"\bbetter for you\b", r"\bit'?s (best|better|wise|smart) (to|if you)\b", r"\bit would be (best|better|wise) to\b",
    r"\bto save (you )?(money|on tax|on taxes|tax|taxes)\b", r"\byou('d| would| could| might) (pay|owe) (less|more)\b",
    r"\byou (can|could|may|might) (deduct|write off|claim)\b", r"\bconsider (filing|converting|switching|taking|"
    r"claiming)\b", r"\b(lower|reduce|minimi[sz]e) your (tax|taxes|bill|tax bill)\b", r"\btax[- ]saving\b",
    r"\bworth (converting|filing|switching)\b", r"\byour best (option|choice|bet)\b",
)))
_DOLLARS = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)|\b(\d[\d,]*(?:\.\d+)?)\s+dollars?\b", re.IGNORECASE)


def _amounts(text: str) -> list[float]:
    return [float((dollar or word).replace(",", "")) for dollar, word in _DOLLARS.findall(text or "")]


def check_outgoing(text: str, lines: dict | None = None) -> tuple[bool, str]:
    """(allowed, reason): blocks advice phrasing, or a dollar amount that isn't on the return.

    `lines` is the state's `lines` (line -> LineValue). A whole-dollar amount is allowed only if
    some line holds exactly that value; amounts with cents are never allowed.
    """
    normalized = (text or "").lower().replace("’", "'")
    if _ADVICE_OUT.search(normalized):
        return False, "advice phrasing"
    sourced = {line["value"] for line in (lines or {}).values()}
    for amount in _amounts(text):
        if amount != int(amount) or int(amount) not in sourced:
            return False, "unsourced dollar amount"
    return True, ""
