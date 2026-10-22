# Agent memory and Intake: design notes

## State and persistence

Every session is a LangGraph thread (`thread_id = session_id`) checkpointed to a SQLite file, so a session abandoned on Tuesday resumes on Friday from a fresh process (tested by rebuilding the graph on the same file). `list_sessions()` reads that file for the dashboard and shows only the taxpayer's first name.

Intake runs in two phases: first it phrases a question and stores it in `intake_meta["pending"]`, then it calls `interrupt()` with the stored question. LangGraph re-runs a node from the top on resume, so phrasing inside the interrupting step could make an LLM produce a different question from the one the person saw.

## Never losing an answer

Answers are keyed by contract question ID. A changed answer moves the old value into `history`, and Intake confirms the change in one sentence ("Thanks, I've updated your date of birth"). "Go back" and "change my …" set `intake_meta["force"]`, which re-asks an earlier question. After that, the walk resumes at the first unanswered question, so nothing is skipped or asked twice. Answers to questions that stop applying (for example spouse details after switching to single) are kept, not deleted. Eligibility decides whether they matter.

## Accessibility and dignity

- **One question at a time:** a test fails on more than one `?` or a joined "and what".
- **Plain language:** each term is explained once per session, and `intake_meta["glossed"]` tracks which terms have been explained.
- **Repetition and drift:** off-topic replies get a gentle redirect and the same question.
- **No unnecessary PII:** SSNs are asked only for people on the return. A volunteered SSN is redacted before storage and never echoed, and the user is told why. A non-900 number is refused and not saved.
- **Honest uncertainty:** "I'm not sure", or two unreadable replies, flags the question for a volunteer (`UNCERTAIN`) instead of guessing.

Intake does no arithmetic. It asks for one total in whole dollars rather than adding up instalments or rounding cents.

## Context strategy

Selective recall: Intake's prompt holds only the current item's messages plus `decisions_summary`, a rolling summary capped at the last five items plus a count of earlier ones. Over the 10-item batch, input grows 18.3x without the strategy and 1.3x with it (`docs/metrics.md`). Offline counts are estimated as `len(prompt) // 4` over the exact prompt. With an API key they are the model's reported counts.

## Trade-offs and failure modes

- The offline parsers are keyword based. Unusual phrasing ends in a volunteer flag rather than a wrong value, which is the safer failure.
- "Change" and "go back" are only recognised in short replies, so a long correction request is treated as an answer.
- The spouse-death question is asked for joint, separate and surviving-spouse filers, or when a reply mentions being widowed. A recently widowed person who answers "single" is not asked.
- Volunteered-SSN detection only catches nine-digit patterns, not numbers spelled out in words.
