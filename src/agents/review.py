"""Review / Audit agent.

Responsibility: Adversarially checks the assembled return before a human sees it.
Constraint: Can send work back; must not fix things itself.
Owner: Security

# STUB: owned by Security. Minimal version with the contract signature so the graph runs end
# to end; the owner's real version replaces it.
"""
from __future__ import annotations


def run(state: dict) -> dict:
    iteration = state.get("review_iterations", 0) + 1
    return {"review": {"passed": True, "issues": [], "iteration": iteration}, "review_iterations": iteration}
