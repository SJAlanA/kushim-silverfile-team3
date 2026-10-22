# Contributing

Graders use the GitHub commit history to see who did what, so everyone commits their own work from their own GitHub account.

## Workflow

1. Pull the latest `main`: `git checkout main && git pull`
2. Create a branch for your area: `git checkout -b <area>/<short-description>`
   (for example `security/ssn-gate`, `memory/checkpointer`, `extraction/w2-reader`)
3. Commit small, focused changes with clear messages.
4. Push and open a pull request into `main`.
5. Get one teammate to review before merging.

## Ownership

| Area | Files |
| --- | --- |
| Agent memory | `src/memory.py`, `src/agents/intake.py`, `docs/metrics.md` |
| Inter agent communication | `src/state.py`, `src/agents/extraction.py`, `README.md` |
| Task division and management | `src/graph.py`, `src/agents/orchestrator.py`, `src/dashboard/` |
| MLOps/DevOps | `src/agents/computation.py`, `src/tools/`, `requirements.txt`, CI, Docker |
| Guardrails and ethics | `src/agents/eligibility.py`, `data/policy.md`, report |
| Security | `src/agents/review.py`, SSN validation, PII handling |

`src/state.py` and `src/graph.py` are shared contracts. Changes to them need a PR approved by their owner.

## Rules

- Never commit `.env` or any real API key.
- Never commit a real Social Security number. Test SSNs must start with 900-999.
- Run `pytest` before opening a PR.
