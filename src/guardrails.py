"""Guardrails: classify what a taxpayer says, and check what agents say back.

# STUB: owned by Guardrails and ethics. Trivially simple keyword versions with the return
# shapes from prompts/05; the owner's real version replaces it.
"""
from __future__ import annotations

import re

_ADVICE = ("should i", "would i pay less", "can i deduct", "is it better", "convert my ira",
           "roth", "save on tax", "what do you recommend", "lower my tax")
_EXPLOITATION = ("told me not to tell", "someone told me to", "he said to send", "she said to send",
                 "they said to send", "i have to give the refund", "he takes care of my money")
_THIRD_PARTY = ("doing this for me", "she just signs", "he just signs", "answering for her", "answering for him")
_OUT_OF_SCOPE = ("rental", "self-employ", "1099-nec", "crypto", "bitcoin", "foreign", "amended", "capital gain")
_ADVICE_OUT = re.compile(r"\byou should\b|\bi recommend\b|\bi suggest\b|\bbetter for you\b|\bto save (money|on tax)")
_SSN = re.compile(r"\b\d{3}[- ]?\d{2}[- ]?\d{4}\b")
_DOLLARS = re.compile(r"\$\s?(\d[\d,]*)")


def classify_utterance(text: str) -> dict:
    lowered = (text or "").lower()
    return {
        "advice_request": any(phrase in lowered for phrase in _ADVICE),
        "third_party": any(phrase in lowered for phrase in _THIRD_PARTY),
        "exploitation_signals": [phrase for phrase in _EXPLOITATION if phrase in lowered],
        "out_of_scope_items": [item for item in _OUT_OF_SCOPE if item in lowered],
        "volunteered_ssn": bool(_SSN.search(text or "")),
    }


def check_outgoing(text: str, lines: dict | None = None) -> tuple[bool, str]:
    """(allowed, reason): blocks advice phrasing or a dollar amount that isn't on the return."""
    if _ADVICE_OUT.search((text or "").lower()):
        return False, "advice phrasing"
    sourced = {line["value"] for line in (lines or {}).values()}
    for amount in _DOLLARS.findall(text or ""):
        if int(amount.replace(",", "")) not in sourced:
            return False, "unsourced dollar amount"
    return True, ""
