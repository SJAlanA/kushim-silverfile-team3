# Guardrails and ethics: design notes

## What this area owns

The reference corpus (`data/policy.md`), the Eligibility & Rules agent, the HITL helpers (`src/hitl.py`) and two guardrails (`src/guardrails.py`): one reads what the taxpayer says, one checks what SilverFile says back.

## Decision table first, model second

Eligibility is a pure decision table (`eligibility_rules.decide`). Every finding names a rule ID from `policy.md`, and the agent checks at run time that the ID exists, so a citation can't point at nothing. Online, a model gets the policy and only yes/no facts (no names, SSNs or exact birth dates) and answers the same questions. Disagreements are recorded in `rules["llm_check"]`; the table always wins. A wrong box count silently changes dollars, and a table can be tested exhaustively (every status × box combination is in `test_decision_table.py`).

## How each escalation is detected

- **Out of scope:** unknown document types, scope phrases in the "other income" answer, the three Standard Deduction Chart exceptions, nobody born before 2 January 1961.
- **Box 2b:** checked on any 1099-R, even when everything else is fine.
- **Advice:** pattern lists in `guardrails.py`, run on every reply by Intake.
- **Filing status:** spouse died in 2025; qualifying surviving spouse or head of household with no dependent listed.
- **Document conflict:** the W-2 name and SSN last four against the taxpayer (either spouse on a joint return).
- **Exploitation:** see below.
- **Unclassifiable:** other income that matches nothing raises UNCERTAIN, never assumed in scope.

## Notice and stop

For exploitation we chose to notice and stop, never to judge. SilverFile can't know whether a nephew helping with a form is kind or coercive, and an accusation from a tax tool would be wrong, hurtful and unprovable. So the patterns lean toward catching too much, and both the event reason and the reply to the taxpayer stay neutral and kind. A missed signal can cost someone a refund; a false alarm costs a volunteer a short look.

## Not raising an event twice

The orchestrator reruns Eligibility whenever an answer or document changes. Before raising, the agent skips any event with the same condition and subject (document or question), open or resolved, so Intake's events aren't repeated and a volunteer decides each thing once.

## Known false positives and negatives

- "Should I …" counts as advice unless it's clearly about answering (type, enter, upload). A harmless "should I use my maiden name?" will escalate.
- Third-party and pressure patterns are phrase based. Indirect wording ("Dave said it would be easier if he took care of it") is missed.
- "Foreign" and "stocks" only count in the other-income answer, so a mention elsewhere is missed. A pension "from Canada" becomes UNCERTAIN, not OUT_OF_SCOPE, which still reaches a person.
- "No wages" versus an existing W-2 isn't detected, because Intake has no such question; only name and SSN mismatches are.
- Death date isn't collected, so a spouse who turned 65 during 2025 is counted by the end-of-year rule.
