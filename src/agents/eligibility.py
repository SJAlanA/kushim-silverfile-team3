"""Eligibility & Rules agent.

Responsibility: 1040-SR eligibility, filing status validation, box counts, schedules, scope.
Constraint: Grounded in data/policy.md; must cite.
Owner: Guardrails and ethics

# STUB: owned by Guardrails and ethics. Minimal version with the contract signature so the
# graph runs end to end; the owner's real version replaces it.
"""
from __future__ import annotations


def run(state: dict) -> dict:
    return {"rules": {"in_scope": True, "citations": []}}
