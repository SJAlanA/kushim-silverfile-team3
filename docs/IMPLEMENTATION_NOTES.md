# Implementation Notes

How SilverFile is put together, and why. Each area's full notes are in [`docs/notes/`](notes/); this page pulls the decisions that matter across the whole system.

## Topology and justification

SilverFile is a **hub with one parallel spoke** ([diagram](../README.md#orchestration-diagram)). Every agent returns control to the orchestrator, whose single conditional edge picks the next node from state. A fixed pipeline wouldn't work, because the order depends on the case:

- a volunteer can change an answer halfway through;
- Review can send a line back to Computation;
- Extraction can ask for a document to be read again.

Keeping every routing rule in one function makes every path testable from a table of hand-built states (`tests/test_routing.py`).

The orchestrator checks ten rules in priority order: open HITL event, loop guard, send-backs, unfinished intake, unread documents, Eligibility, scope, Computation, Review, finalize. When it dispatches an agent, it hashes exactly the inputs that agent reads. If a volunteer corrects the filing status, only Eligibility, Computation and Review run again, in that order, so Computation can never run ahead of Extraction or Eligibility.

**The parallel spoke is extraction.** `extract_dispatch` sends one `extract_one` branch per unread document (LangGraph `Send`), and they join at `extract_aggregate`. Documents are independent, so this is the one place parallelism is safe and useful. It depends on the reducers in `src/state.py` being order-independent: each is a pure function of the set of writes, so a return comes out the same whichever branch finishes first. `tests/test_merge_order.py` checks every permutation and a real three-branch fan-out.

**Agents never call each other.** They return partial state updates and talk through `AgentMessage`s in `handoffs`, built by `src/messages.py`, which rejects unknown node names so a typo fails at the sender instead of stalling the graph. Only the orchestrator writes `next_agent`.

## Loop guard and review bound

Each hop appends `[agent, progress hash]` to `routing.trail`. The progress hash covers everything an agent can produce: answers, readings, rules, line values, the review verdict and HITL statuses. Messages, route history and token logs are left out. The guard trips, raises `LOOP_GUARD` and routes only to a human when:

1. the last 2- or 3-agent pattern repeats three times with no change to the progress hash (work passed around, nothing produced); or
2. more than `MAX_HOPS` (default 60) hops go to work agents. Intake hops don't count, since Intake limits its own retries.

Review is bounded separately. Every pass increments `review_iterations`, and at `MAX_REVIEW_ITERATIONS` (default 3) the orchestrator raises `REVIEW_LIMIT`. A human decision clears the loop guard's trail; a correction or a send-back also resets the review count.

## Every number is computed by a tool and explains itself

- **No arithmetic in agents.** All tax math lives in eight pure functions on the tax_math MCP server. Each returns its value with its working (`formula`, `inputs`, `tool`, `method_note`), and that record becomes the line's provenance. Computation's step order is a data table checked when the module loads: every line is produced once, and nothing is read before it is computed. A test fails if any value is added up in Python.
- **Review tries to prove the return wrong.** It recomputes every total from its parts with the same tools, re-adds each document line from the extracted records, and recomputes 6b, 12e, 13b and 16 from the rules. It also checks provenance, scope and SSNs. It never edits anything: problems go back to the responsible agent, and the orchestrator reruns it.
- **Eligibility is a decision table.** Every finding cites a rule in `data/policy.md`, and the agent checks the rule exists. Online, a model gets the policy and yes/no facts only, and answers the same questions; disagreements are recorded and the table wins.
- **The form shows its working.** In `form_1040sr.html`, every amount opens a popover with its formula, sources, tool, method and who entered it when. `audit.json` holds the same record. The filled IRS PDF can't show provenance, so it points to both.

## Accessibility and dignity choices in the intake dialog

Intake is written for older adults, some answering with a family member beside them:

- **One question at a time.** A test fails on any question with two question marks or a joined "and what".
- **Plain language, explained once.** Terms such as "dependent", "filing status" or "legally blind" get a one-sentence explanation the first time, then not again.
- **Kind repetition.** An off-topic or unclear reply gets a gentle redirect and the same question. "I'm not sure", or two unreadable replies, flags the question for a volunteer instead of guessing.
- **Nothing lost.** A changed answer is confirmed in one sentence and the old value is kept in `history`. "Go back" re-asks an earlier question without skipping or repeating others.
- **Stop any time.** Every answer is checkpointed. Quitting says it's saved and prints the `--session` command; resuming starts with a plain recap of what's been covered.
- **Respect around loss.** The spouse-death question is asked gently, and once it's answered yes, the questions about the spouse switch to the past tense.
- **No advice, no judgement.** Requests for advice get one plain sentence and are noted for the volunteer. For possible exploitation (someone else answering without authority, pressure, a refund account the taxpayer can't describe) SilverFile notices and stops, with a neutral, kind reason. It never accuses: a false alarm costs a volunteer a short look, a missed signal can cost someone their refund.

## Volunteered SSN handling

`security.validate_ssn` is the only way an SSN enters state, and it accepts only 900-999 test numbers. The rejection message never quotes the number.

People also volunteer numbers unasked ("Lindqvist, and my SSN is ..."). Intake drops the number, keeps the rest of the answer and tells the person it didn't save it. `security.redact_pii` backs this up for all free text:

- a number that could be a real SSN is masked completely (`***-**-****`);
- only 900-series test numbers keep a last-four hint, so a volunteer can tell answers apart.

Everywhere an SSN is displayed (dashboard, HTML form, `audit.json`) it appears as `***-**-1234`. Only the PDF draft carries the full test SSN, because the form needs it. Documents keep only the last four digits.

## Untrusted documents

Document text is data, never instructions. `security.scan_untrusted_text` flags instruction-like text and invisible characters. Extraction attaches the flags and asks a volunteer, and Review fails any line fed by a flagged document unless that volunteer chose to continue. Because all arithmetic is in deterministic tools, an injected sentence can't change an amount.

## Offline and online

With no API key, or `SILVERFILE_OFFLINE=1`, every agent uses deterministic rules: OCR for the W-2, scripted questions, the decision table alone. That's how all tests and graders run. With `ANTHROPIC_API_KEY` in `.env`:
- the W-2 is read by Claude's vision (and labelled `vision`);
- Intake phrases its questions with the model;
- Eligibility runs its model cross-check.

Every model reply is checked before use, and any failure falls back to the offline path with the fallback recorded.

## Known limits

- The dashboard has no login and binds to `127.0.0.1` by default; it isn't production software (see [`SECURITY.md`](../SECURITY.md)).
- Line 16 uses the 2025 rate schedule, as the brief requires, not the IRS Tax Table, so results can differ from the Table by a few dollars.
- Exploitation and advice detection is phrase based: indirect wording can be missed, and some harmless phrasing escalates.
- The dashboard resumes the graph inside the web request, so a slow model call holds the page.
