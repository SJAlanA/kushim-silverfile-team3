# Task division and management: design notes

Files: `src/agents/orchestrator.py`, `src/graph.py`, `run.py`, `src/dashboard/`.

## Topology: a hub with one parallel spoke

Every agent hands control back to the orchestrator, whose single conditional edge picks the next node. A fixed pipeline wouldn't work, because the order depends on the case: a HITL resolution can change an answer midway, Review can send a line back to Computation, and Extraction can ask to re-read a document. Keeping all routing in one function puts every path in one place, testable with a table of hand-built states (`tests/test_routing.py`).

Extraction is the exception. `extract_dispatch` sends one `extract_one` branch per unread document, and they join at `extract_aggregate`. The documents are independent, and the order-independent `merge_extracted` reducer gives the same result whichever branch finishes first.

## How the next hop is chosen

The orchestrator checks ten rules in priority order: open HITL event, loop guard, send-backs, unfinished intake, unread documents, Eligibility, scope, Computation, Review, finalize. When it dispatches an agent, it hashes exactly the inputs that agent reads (`routing.inputs_seen`): Eligibility reads answers, documents and readings; Computation adds the rules; Review adds the line values. If a volunteer corrects the filing status, only Eligibility, Computation and Review run again, in that order, so Computation never runs before extraction and Eligibility finish. Timestamps are left out of the hashes, so a recomputation that lands on the same numbers isn't a change.

## Loop guard

Each hop appends `[agent, progress hash]` to `routing.trail`. The progress hash covers everything an agent can produce: answers, intake bookkeeping, documents, readings, rules, line values, the review verdict and HITL statuses. Handoffs, route history and token logs are left out. The guard trips when:

1. The last 2- or 3-agent pattern repeats three times in a row and the progress hash never changes: agents are passing work around without producing anything.
2. More than `MAX_HOPS` (default 60) hops go to work agents. Intake hops don't count, because each one handles a single question and Intake limits its own retries.

On a trip, the orchestrator raises `LOOP_GUARD` and routes only to `human_review`. Any human resolution clears the trail, so the volunteer's decision starts a fresh count.

## Review bound

A wrapper around the Review node increments `review_iterations` on every pass. Below `MAX_REVIEW_ITERATIONS` (default 3) a failed review runs again. At the bound, the orchestrator raises `REVIEW_LIMIT` once for those exact line values. "Continue" finalizes the return. "Send back" or corrected answers reset the count, because a person has changed something.

## Trade-offs and failure modes

- The loop guard and the review bound overlap. With the defaults, `REVIEW_LIMIT` fires first. If `MAX_REVIEW_ITERATIONS` is set above 3, a Computation/Review cycle that makes no progress trips `LOOP_GUARD` instead. The run still stops.
- An agent that writes nothing isn't rerun on the same inputs; missing rules raise `UNCERTAIN` instead.
- The dashboard resumes the graph inside the web request, so a slow model call holds the page. A team would need a background worker.
- `routing` is an addition to the contract. Only the orchestrator and `human_review` write it.
