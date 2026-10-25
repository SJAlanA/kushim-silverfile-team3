# Security, Review / Audit and the provenance form: design notes

Owner: Security (Alan). Files: `src/security.py`, `src/agents/review.py`, `src/form/`, `SECURITY.md`.

## The SSN gate

`validate_ssn` is the only way an SSN enters state, so one function enforces the 900-999 rule. It takes dashed, plain or spaced digits, also inside a sentence, but rejects a reply holding two numbers rather than guess. It also rejects impossible test numbers (group `00`, serial `0000`). The rejection is fixed text that never quotes the number, so a real SSN can't leak through a reply, log or retry prompt.

## The volunteered-SSN choice

People volunteer numbers unasked ("Lindqvist, and my SSN is ..."). Intake drops the number, keeps the rest of the answer and says so. `redact_pii` backs this up for all free text. Our first version kept a last-four hint for every SSN-like number. Agent memory's tests caught that this leaves four digits of a possibly real SSN in state. Now any number outside the 900 range is masked completely (`***-**-****`). Only test numbers keep `***-**-1037`, so a volunteer can tell answers apart. Masking too much costs readability; masking too little costs a person's identity.

## Injection defence

Document text is data, never instructions. `scan_untrusted_text` flags ignore-instructions phrasing, role changes, fake system turns, "set box 1 to ...", refund redirects, hidden notes to the reviewer and invisible characters. It is plain regular expressions, tuned so clean W-2 text never triggers it. Extraction attaches the flags and asks a volunteer. Review fails any line fed by a flagged document unless that volunteer chose to continue. The deeper defence is architectural: all arithmetic is done by deterministic tax_math tools, so an injected line can't change an amount.

## Review's falsification strategy

Review assumes the return is wrong and tries to prove it:

- It recomputes every total from its parts with `call_tool`.
- It re-sums each document line from the extracted records.
- It recomputes 6b, 12e, 13b and 16 from Eligibility's rules.
- It checks that provenance sources point at something real.
- It checks the relationships that must always hold (6b ≤ 6a, 3a ≤ 3b, never both 34 and 37), scope and every SSN.

A correct return costs about 26 tool calls. Review never edits state: it sends one `send_back` per responsible agent, and the orchestrator reruns them and stops at `MAX_REVIEW_ITERATIONS` with `REVIEW_LIMIT`. Known limit: Review shares the tax_math tools with Computation, so its independence comes from recomputing from different inputs (documents and rules, not a line's own formula).

## The form

In the HTML draft, every amount is a button whose popover shows the formula, sources, tool, method and who entered it when, on hover, keyboard focus or click. The PDF fills the official 2025 IRS 1040-SR. Its field names were checked against the labels printed on the form, it ticks only boxes an answer supports, it gives every line a value, and it points to the HTML and `audit.json` for provenance.
