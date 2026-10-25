"""Security helpers: the SSN gate, PII masking and the prompt-injection scan.

Owner: Security.

Three functions from contract section 10, plus `mask_ssn` for display:

- `validate_ssn(text)` is the only way an SSN gets into state. It accepts 900-999 test numbers
  and nothing else, so a real Social Security number can never be stored. A rejected value is
  never echoed back, logged or returned: the reason is the only thing that leaves the function.
- `redact_pii(text)` masks identity and bank numbers in free text before it is stored, leaving
  a last-four hint ("***-**-1037") so a volunteer can still tell answers apart.
- `scan_untrusted_text(text)` flags text in a document that tries to instruct the system.
  Document text is data, never instructions; Extraction attaches the flags and Review fails a
  return when a flagged document fed a line.

Everything here is deterministic regular expressions: no model call, so it works offline and
cannot be argued with.
"""
from __future__ import annotations

import re

# --- SSN gate (brief 3.2, contract section 15) ------------------------------------------------

_SSN_CANDIDATE = re.compile(r"(?<![\d-])(\d{3})[- ]?(\d{2})[- ]?(\d{4})(?![\d-])")
REAL_SSN_REASON = ("For this classroom version I can only accept test numbers starting with 9. "
                   "Please don't enter your real Social Security number.")
INVALID_TEST_SSN_REASON = ("That isn't a valid test number: the middle two digits can't be 00 and the "
                           "last four can't be 0000. Please check it and try again.")
NOT_AN_SSN_REASON = "That doesn't look like a nine-digit Social Security number. Please type all nine digits."


def validate_ssn(text: str) -> tuple[bool, str]:
    """(True, "9XX-XX-XXXX") for a valid 900-series test number, else (False, a plain-language reason).

    Accepts "9XX-XX-XXXX", "9XXXXXXXX" or spaced digits, alone or inside a sentence. Only area
    900-999 is accepted, and the structurally invalid group 00 and serial 0000 are rejected too.
    """
    matches = _SSN_CANDIDATE.findall(text or "")
    if len(matches) != 1:
        return False, NOT_AN_SSN_REASON
    area, group, serial = matches[0]
    if not 900 <= int(area) <= 999:
        return False, REAL_SSN_REASON
    if group == "00" or serial == "0000":
        return False, INVALID_TEST_SSN_REASON
    return True, f"{area}-{group}-{serial}"


def mask_ssn(ssn: str | None) -> str:
    """'901-11-1037' -> '***-**-1037'. Anything without four trailing digits becomes '***-**-****'."""
    digits = re.sub(r"\D", "", ssn or "")
    return f"***-**-{digits[-4:]}" if len(digits) >= 4 else "***-**-****"


# --- PII masking (brief 4.3) --------------------------------------------------------------------

_EMAIL = re.compile(r"\b([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
_PHONE = re.compile(r"(?<![\d-])(?:\+?1[-. ]?)?(?:\(\d{3}\)\s?|\d{3}[-. ])\d{3}[-. ](\d{4})(?![\d-])")
_ROUTING = re.compile(r"(?i)\b(routing|aba|rtn)\b(\D{0,25}?)(\d{5})(\d{4})(?!\d)")
_SSN_LIKE = re.compile(r"(?<![\d-])(\d{3})[- ]?\d{2}[- ]?(\d{4})(?![\d-])")
_LONG_NUMBER = re.compile(r"(?<![\d,.])(\d{4,13})(\d{4})(?![\d,.])")  # 8 to 17 digits: account numbers


def _mask_ssn_like(match: re.Match) -> str:
    """A 900-series test number keeps its last four as a hint; any other area could be a real
    SSN, so none of its digits survive."""
    return f"***-**-{match.group(2)}" if match.group(1).startswith("9") else "***-**-****"


def redact_pii(text: str) -> str:
    """Mask SSN-like numbers (any area, so real ones are caught), routing and account numbers,
    phone numbers and email addresses, keeping a short hint where that is safe.

    '901-11-1037' -> '***-**-1037' (test number), '123-45-6789' -> '***-**-****' (could be real),
    'routing 021000021' -> 'routing *****0021',
    '000123456789' -> '********6789', '(607) 555-0142' -> '(***) ***-0142', 'ann@mail.com' -> 'a***@mail.com'.
    Dates, dollar amounts, ZIP codes, EINs and file names are left alone.
    """
    text = _EMAIL.sub(lambda m: f"{m.group(1)}***@{m.group(2)}", text or "")
    text = _ROUTING.sub(lambda m: f"{m.group(1)}{m.group(2)}*****{m.group(4)}", text)
    text = _PHONE.sub(lambda m: f"(***) ***-{m.group(1)}", text)
    text = _SSN_LIKE.sub(_mask_ssn_like, text)
    return _LONG_NUMBER.sub(lambda m: "*" * len(m.group(1)) + m.group(2), text)


# --- Prompt injection in documents (brief 4, Review / Audit) --------------------------------------

INJECTION_PATTERNS: dict[str, re.Pattern] = {
    "ignore-instructions": re.compile(
        r"\b(ignore|disregard|forget|override|bypass)\b[^.\n]{0,40}\b(previous|prior|above|earlier|all|any|your|the|"
        r"these|those|system)\b[^.\n]{0,25}\b(instructions?|rules|prompts?|directions|guidelines|checks)\b"),
    "role-change": re.compile(
        r"\byou are now\b|\bact as (an?|the)\b|\bpretend (to be|you are)\b|\bfrom now on,? you\b|"
        r"\bnew (system )?instructions?\b|\byour (new )?(role|task) is\b"),
    "system-prompt": re.compile(
        r"(^|[\s\[(<])(system|assistant|developer)\s*:|<\s*/?\s*(system|instructions?|prompt)\s*>|"
        r"\[\s*/?\s*(system|inst)\s*\]|\bsystem prompt\b"),
    "amount-change": re.compile(
        r"\b(change|set|report|enter|record|use|make|increase|decrease|lower|raise|treat)\b[^.\n]{0,40}"
        r"\b(wages?|amount|box ?\d+[a-z]?|withholding|withheld|income|refund|line ?\d+[a-z]?|total)\b"
        r"[^.\n]{0,20}\b(to|as|=|be)\s*\$?\s*\d"),
    "refund-redirect": re.compile(
        r"\b(deposit|send|route|direct|wire|transfer|pay)\b[^.\n]{0,40}\brefund\b[^.\n]{0,40}\b(account|routing|"
        r"card|address)\b|\brefund\b[^.\n]{0,30}\b(should|must|will) go to\b"),
    "hidden-instruction": re.compile(
        r"\b(do not|don't|never) (tell|mention|inform|show|alert|flag)\b[^.\n]{0,40}\b(user|taxpayer|human|"
        r"reviewer|volunteer|preparer|anyone)\b|\b(approve|pass|skip) (the |this )?(review|check|audit)\b"),
}
_INVISIBLE = re.compile("[​‌‍⁠﻿‪-‮]")


def scan_untrusted_text(text: str) -> list[str]:
    """Names of the injection patterns found in untrusted document text, in a fixed order; [] if none.

    Also flags invisible formatting characters ("hidden-text"), a common way to hide instructions
    in a document a person reads as normal.
    """
    lowered = " ".join((text or "").lower().replace("’", "'").split())
    flags = [name for name, pattern in INJECTION_PATTERNS.items() if pattern.search(lowered)]
    if _INVISIBLE.search(text or ""):
        flags.append("hidden-text")
    return flags
