"""Guardrails: classify what a taxpayer says before Intake stores it.

# STUB: owned by Guardrails and ethics. Trivially simple keyword version with the signature
# Intake calls; the owner's real version replaces it.
"""
from __future__ import annotations

_ADVICE = ("should i", "would i pay less", "can i deduct", "is it better", "convert my ira",
           "roth", "save on tax", "what do you recommend", "lower my tax")
_EXPLOITATION = ("told me not to tell", "someone told me to", "he said to send", "she said to send",
                 "they said to send", "i have to give the refund", "he takes care of my money")


def classify_utterance(text: str, question_id: str | None = None) -> str | None:
    """Return "advice", "exploitation", "third_party", "out_of_scope" or None."""
    lowered = (text or "").lower()
    if any(phrase in lowered for phrase in _ADVICE):
        return "advice"
    if any(phrase in lowered for phrase in _EXPLOITATION):
        return "exploitation"
    return None
