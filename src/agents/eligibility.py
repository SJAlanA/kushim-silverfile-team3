"""Eligibility & Rules agent.

Responsibility: 1040-SR eligibility, filing status validation, box counts, schedules, scope.
Constraint: Grounded in data/policy.md; must cite.
Owner: Guardrails and ethics

The decision table in `eligibility_rules.decide` is the source of truth. It is deterministic and
every finding it returns cites a rule ID from data/policy.md; this module checks that each cited
ID really exists in that file. Online, a model is also given the policy (and nothing else) and
asked the same questions; its answers are compared with the table and any disagreement is
recorded in `rules["llm_check"]`. The table's answer is used either way.

Escalation: anything the table says needs a person becomes a HITL event, unless the state already
holds an event for the same condition and subject (open or resolved). The orchestrator reruns this
agent whenever an answer or a document reading changes, including after a volunteer's edit, so
without that check the volunteer would be asked the same thing twice.

Intake raises some events itself (advice, exploitation, a spouse who died, out-of-scope text in
most answers); the subject check is what stops those being raised again here.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from src import llm
from src.agents.eligibility_rules import Decision, Escalation, citation, decide
from src.hitl import has_event, merge_updates, raise_hitl
from src.messages import as_update, escalate, handoff
from src.tools.tracing import session, traced_node

AGENT = "eligibility"
POLICY_PATH = Path(__file__).resolve().parents[2] / "data" / "policy.md"
RULE_HEADING = re.compile(r"^### (R-[A-Z0-9]+(?:-[A-Z0-9]+)*)\s*$", re.MULTILINE)
# What the model is asked, and the rule that governs each answer.
LLM_FIELDS = {"eligible_1040sr": "R-ELIG-01", "box_count": "R-SD-02", "senior_qualifiers": "R-SR-01"}


@lru_cache(maxsize=1)
def load_policy() -> dict[str, str]:
    """rule ID -> the rule's full text, read from data/policy.md."""
    text = POLICY_PATH.read_text()
    matches = list(RULE_HEADING.finditer(text))
    return {m.group(1): text[m.end():(matches[i + 1].start() if i + 1 < len(matches) else len(text))].strip()
            for i, m in enumerate(matches)}


def ungrounded(findings: list[dict], policy: dict[str, str]) -> list[str]:
    """Rule IDs cited by findings that are not in the policy (should always be empty)."""
    return sorted({f["rule"] for f in findings if f["rule"] not in policy or f["citation"] != citation(f["rule"])})


def run(state: dict) -> dict:
    """Decide the return's rules, escalate what needs a person, and hand off to Computation."""
    with session(state.get("session_id")), traced_node(AGENT):
        answers = {qid: answer.get("value") for qid, answer in state.get("intake", {}).items()}
        decision = decide(answers, state.get("documents", []), state.get("extracted", {}))
        policy = load_policy()
        missing = ungrounded(decision.rules["findings"], policy)
        if missing:  # a bug in the table, never user input: fail loudly rather than cite a rule that isn't there
            raise RuntimeError(f"findings cite rules that are not in policy.md: {', '.join(missing)}")
        check = crosscheck(answers, decision, policy)
        if check is not None:
            decision.rules["llm_check"] = check
        new = [e for e in decision.escalations if not has_event(state, e.condition, e.subject)]
        return {"rules": decision.rules, **_escalation_update(new), **_message(decision, new)}


def _escalation_update(escalations: list[Escalation]) -> dict:
    return merge_updates(*(raise_hitl(e.condition, e.reason, AGENT, e.details()) for e in escalations))


def _message(decision: Decision, new: list[Escalation]) -> dict:
    """The handoff to Computation, or an escalation notice when a person is needed."""
    if new:
        reason = f"{len(new)} item(s) need a volunteer: " + "; ".join(e.reason for e in new)
        return as_update(escalate(AGENT, reason, {"conditions": [e.condition for e in new]}))
    rules = decision.rules
    return as_update(handoff(AGENT, "computation", "Rules decided; see rules.findings for the cited rules.",
                             {"filing_status": rules["filing_status"], "box_count": rules["box_count"],
                              "senior_qualifiers": rules["senior_qualifiers"]}))


# ---------------------------------------------------------------------------
# Optional model cross-check (online only)
# ---------------------------------------------------------------------------

PROMPT = ("You check a tax-preparation rules table. Use ONLY the policy below; do not use outside knowledge. "
          "Given the facts, answer with a single JSON object and nothing else: "
          '{"eligible_1040sr": true|false|null, "box_count": integer|null, "senior_qualifiers": integer|null, '
          '"citations": ["R-..."]}. Use null if the facts are not enough. Cite the rule IDs you relied on.\n\n'
          "POLICY\n{policy}")


def facts_for_model(answers: dict, decision: Decision) -> dict:
    """What the model sees: statuses and yes/no facts only, no names, numbers or exact birth dates."""
    rules = decision.rules
    return {"filing_status": rules["filing_status"], "boxes": rules["boxes"],
            "spouse_died_in_2025": answers.get("spouse_died_in_2025"),
            "taxpayer_born_before_1961_01_02": (rules["boxes"] or {}).get("taxpayer_65_or_older"),
            "spouse_born_before_1961_01_02": (rules["boxes"] or {}).get("spouse_65_or_older")}


def crosscheck(answers: dict, decision: Decision, policy: dict[str, str]) -> dict | None:
    """Ask the model the same questions with only the policy as context. None offline or on any failure.

    Returns {"agreed": bool, "differences": [{"field", "table", "model", "rule"}], "citations": [...]}.
    A model that cites a rule that does not exist is treated as disagreeing: its answer is not grounded.
    """
    if llm.get_llm() is None or decision.rules["boxes"] is None:
        return None
    policy_text = "\n\n".join(f"{rule_id}\n{text}" for rule_id, text in policy.items())
    messages = [SystemMessage(content=PROMPT.replace("{policy}", policy_text)),
                HumanMessage(content=json.dumps(facts_for_model(answers, decision)))]
    try:
        text, _ = llm.invoke_with_usage(messages)
        reply = json.loads(text[text.index("{"):text.rindex("}") + 1])
    except Exception:  # noqa: BLE001 - any model or parsing failure means "no cross-check", never a wrong answer
        return None
    citations = [c for c in reply.get("citations", []) if isinstance(c, str)]
    differences = [{"field": name, "table": decision.rules[name], "model": reply.get(name), "rule": rule}
                   for name, rule in LLM_FIELDS.items() if reply.get(name) != decision.rules[name]]
    if any(c not in policy for c in citations) or not citations:
        differences.append({"field": "citations", "table": None, "model": citations, "rule": "R-ELIG-01"})
    return {"agreed": not differences, "differences": differences, "citations": citations}
