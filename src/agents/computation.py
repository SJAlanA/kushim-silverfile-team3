"""Computation agent.

Responsibility: Populates every derived line.
Constraint: No arithmetic itself; every number comes from a tax_math MCP tool call.
Owner: MLOps/DevOps

# STUB: owned by MLOps/DevOps. Minimal version with the contract signature so the graph runs
# end to end. It writes no lines, because a dollar amount without a tax_math provenance record
# is never allowed; the owner's real version replaces it.
"""
from __future__ import annotations


def run(state: dict) -> dict:
    return {"lines": {}}
