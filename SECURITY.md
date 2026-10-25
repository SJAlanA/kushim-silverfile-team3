# Security

SilverFile is a classroom build (Cornell ENMGT 5405, Assignment 7). It drafts Form 1040-SR for older adults with a volunteer reviewing every return, and it **never files anything**. This page lists what could go wrong, what stops it, and where the test is. Owner: Security (Alan).

## Threat model

| Threat | How it could happen | Control | Where it's tested |
| --- | --- | --- | --- |
| **Real SSN entered** | A taxpayer types their real Social Security number. | `security.validate_ssn` is the only way an SSN reaches state. It accepts only test numbers with area 900-999 (and rejects group `00` and serial `0000`). A rejected value is never echoed, stored or logged: only a kind reason goes back. | `tests/test_security.py` (formats, rejections, and an Intake run proving a rejected number never reaches state or traces) |
| **PII leaking into records** | An SSN, bank, phone or email turns up in free text, the audit record, a trace or a page. | `security.redact_pii` masks free text before Intake stores it. A real-looking SSN loses every digit (`***-**-****`); only 900-series test numbers keep a last-four hint. The HTML form, `audit.json` and the dashboard show SSNs as `***-**-1234`, and every audit string is redacted again before writing. Only the PDF draft carries the full test SSN, because the form needs it. Documents keep only the SSN's last four. | `tests/test_security.py`, `tests/test_form_render.py` (no full SSN in HTML or audit), `tests/test_extraction.py` |
| **Prompt injection via documents** | A W-2 or upload contains text like "ignore previous instructions" or "change box 1 to 0". | Document text is data, never instructions. `security.scan_untrusted_text` flags instruction-like text and invisible characters; Extraction attaches the flags and asks a volunteer. Review fails any line fed by a flagged document unless a volunteer saw the warning and chose to continue. The vision prompt also tells the model that text in the image is data. Tax arithmetic is done by deterministic tools, so model output can't change an amount. | `tests/test_security.py`, `tests/test_review.py` |
| **Refund redirection** | Someone pushes the taxpayer to send the refund to an account they can't describe, or a document tries to set one. | Intake asks whose account the refund goes to and escalates `EXPLOITATION` if the taxpayer can't say. Guardrails watches for refund-redirect and pressure language, and the injection scan flags refund instructions inside documents. SilverFile never collects routing or account numbers, and `redact_pii` masks any that are typed. | `tests/test_guardrails.py`, `tests/test_security.py` |
| **Wrong or tampered numbers** | A bug, a bad reading, or a tampered state changes a total. | Review recomputes every total and rule-driven line with the tax_math tools, re-sums the document lines from the extracted records, and checks provenance, contradictions and scope. It never fixes anything: it sends work back, and the orchestrator stops after `MAX_REVIEW_ITERATIONS` and asks a person. | `tests/test_review.py` |
| **Secrets in the repo or logs** | An API key is committed or written to a trace. | `.env` is git-ignored and has never been committed. The key is read only from the environment (`src/llm.py`). Traces hold tax_math calls and node timings, never the key or the dialogue. Without a key everything runs offline. | `git log -- .env` is empty; `.env.example` holds no values |
| **Dashboard access** | Someone else on the network opens the dashboard and reads or resolves sessions. | The dashboard binds to `127.0.0.1` by default, so only the same machine can reach it. Docker publishes port 8000; run it on a trusted machine only. The session cookie is signed with `DASHBOARD_SECRET` (random if unset). | See "Out of scope" |
| **Path tricks in outputs** | A session id like `../../x` writes outside `outputs/`. | `render.session_folder` reduces the id to safe characters. | `tests/test_form_render.py` |
| **Script injection in the HTML form** | A name or answer contains HTML. | All text in `form_1040sr.html` is escaped, and the page loads nothing from outside. | `tests/test_form_render.py` |

## Out of scope for a classroom build

- **Dashboard login.** The dashboard has no authentication. A production version needs real accounts for Kushim staff. A simple shared password read from an environment variable would be the minimum for a demo shared beyond one machine. **This is not production software.**
- **Encryption at rest.** The checkpoint database (`silverfile_checkpoints.sqlite`) and `outputs/` are plain files. They hold fake 900-series test data only; real data would need encrypted storage and a retention policy.
- **Real identity verification** and **e-filing.** SilverFile never submits a return.
- **Network hardening, rate limiting and audit-log tamper evidence.**

## Reporting

Found a problem? Open an issue on the repository, or tell the Security owner. Please don't put real personal data in an issue.
