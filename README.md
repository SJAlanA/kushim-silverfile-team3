# Kushim SilverFile (Team 3)

SilverFile is a LangGraph multi-agent system that drafts **Form 1040-SR** for older adults served by Kushim Financial. A taxpayer answers plain-language questions one at a time, and their documents are read, including a W-2 from a photo. Eligibility is decided from a cited policy, and every dollar is computed by a deterministic tax_math tool. An independent Review agent then tries to prove the return wrong. Anything unusual (advice requests, possible exploitation, out-of-scope income, conflicting documents) stops the run and goes to a Kushim volunteer in a staff dashboard. The result is a draft 1040-SR in which every number shows where it came from. Built for Cornell ENMGT 5405, Assignment 7.

> SilverFile is a preparation aid. It never files anything and never gives tax advice. Its output is a draft return plus an audit record for a volunteer to review.

Each finished session writes to `outputs/<session_id>/`:
- `form_1040sr.html`: the draft in 1040-SR order. Hover over, tab to or click any amount to see its formula, sources and who entered it.
- `form_1040sr.pdf`: the official IRS 2025 Form 1040-SR, filled in and watermarked "CLASSROOM DRAFT - DO NOT FILE".
- `audit.json`: every line with provenance, the cited rules, volunteer decisions and review rounds.

## Team roles

| Focus area | Team member assigned | Agent owned |
| --- | --- | --- |
| Agent memory | Mohammed | Intake |
| Inter agent communication | Hamdan | Document Extraction |
| Task division and management | Siddharth | Orchestrator |
| MLOps/DevOps | Parth | Computation |
| Guardrails and ethics | Ishitha | Eligibility & Rules |
| Security | Alan (SJAlanA) | Review / Audit |

## Setup (local)

Requires Python 3.11. Everything works offline with no API key.

```bash
git clone https://github.com/SJAlanA/kushim-silverfile-team3.git
cd kushim-silverfile-team3
python3.11 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # optional: add ANTHROPIC_API_KEY to use Claude
pytest                             # all tests pass offline
```

`make install` and `make test` do the same with the Makefile. W-2 images are read with `rapidocr` (installed by pip). Tesseract is used too if it is on your machine, but isn't required.

**Offline or online.** With no key, or with `SILVERFILE_OFFLINE=1`, every agent uses deterministic rules: OCR for the W-2, scripted questions, and the eligibility table alone. With `ANTHROPIC_API_KEY` set in `.env`:
- the W-2 is read by Claude's vision;
- Intake phrases each question with the model;
- Eligibility cross-checks its table against the policy.

`run.py` loads `.env` itself. The tax numbers are identical either way, because they come from the tax_math tools.

## Run the eval cases

Run from the repository root, because case files use relative document paths.

**Eval case 1: a clean return, no human needed.** Single, born 1958, one W-2 image, Social Security, interest and dividends.

```bash
python run.py --case demo/eval_case_1/case.json        # or: make demo1
```

Expect `Status: complete`, no HITL events, and the three output paths. Key lines: 6b 6,540; 11a 36,940; 12e 17,750; 13b 6,000; 15 13,190; tax 1,338; refund 662. Details: [demo/eval_case_1.md](demo/eval_case_1.md).

**Eval case 2: a human steps in.** A widow whose husband died in August 2025, so the filing status needs a volunteer.

```bash
python run.py --case demo/eval_case_2/case.json        # or: make demo2
```

Expect exactly one `FILING_STATUS_AMBIGUITY` event. The case file resolves it as the volunteer would (continue, filing status `mfj`, with a note), and the run completes with a refund of 1,200. To resolve it yourself in the dashboard instead, follow the click-by-click steps in [demo/eval_case_2.md](demo/eval_case_2.md).

**Every HITL condition.** `evals/cases.json` has one case per escalation condition (advice, someone else answering, an undescribed refund account, a 1099-R with box 2b, out-of-scope income and documents, a mismatched W-2, and more):

```bash
pytest tests/test_evals.py
```

## Documents and W-2 extraction

The Document Extraction agent reads each document a taxpayer gives, all in parallel (one `extract_one` branch per document). It records what the paper says and nothing more: it never assigns 1040-SR lines or decides what is taxable.

| Type | Fields read |
| --- | --- |
| `W2` | employee and employer name, SSN last 4, box 1 wages, box 2 federal tax withheld |
| `1099-INT` | boxes 1, 4 and 8 |
| `1099-DIV` | boxes 1a, 1b and 4 |
| `1099-R` | boxes 1, 2a, 2b (checked or not), 4 and 7, IRA/SEP/SIMPLE, QCD amount |
| `SSA-1099` | boxes 5 and 6 |

A W-2 image is read by the first method that works:
1. **vision**: Claude reads the photo (needs `ANTHROPIC_API_KEY`);
2. **ocr**: offline, with `rapidocr` (or Tesseract if installed), taking the value under each box label;
3. **json**: the transcription file next to the image (`demo/w2_image_1.json`). The record says `"extractor": "json"` with a warning, so Review knows the picture itself wasn't read.

Every record names its extractor and a confidence. If a document is missing or failed, the agent tries it once more, then asks a volunteer. A volunteer is also asked when confidence is below 0.6, two readings disagree, or the document contains text that looks like instructions. Only the last four digits of an SSN are kept, and only 900-series test SSNs are accepted.

Read one document on its own:

```bash
SILVERFILE_OFFLINE=1 python -m src.agents.extraction demo/w2_image_1.jpg          # W-2 by OCR
python -m src.agents.extraction data/fixtures/1099int_1.json 1099-INT             # any type by name
```

The first prints `"extractor": "ocr"`, `box1_wages` 30000 and `box2_fed_withheld` 2000. `python data/generate_fixtures.py` redraws the W-2 images and rewrites the fixtures, byte for byte the same each time. Design notes: [docs/notes/inter_agent_communication.md](docs/notes/inter_agent_communication.md).

## Answer the questions yourself

```bash
python run.py --interactive
```

Use made-up details: SilverFile only accepts test SSNs that start with 9, such as `903-45-6789`. Document paths can point at the demo files, for example `demo/w2_image_3.jpg` (a W-2 for Theo Brandt, 903-45-6789) and `data/fixtures/ssa1099_1.json`.

You can stop at any time with Ctrl-C. Every answer is saved, and SilverFile prints the command to carry on later:

```bash
python run.py --session <session_id>
```

A resumed session starts with a short recap of what's been covered.

## The staff dashboard

```bash
python run.py --dashboard                              # or: make dashboard
```

Open http://localhost:8000. The dashboard and any runs must use the same checkpoint database: `CHECKPOINT_DB` in `.env`, by default `silverfile_checkpoints.sqlite` in the repository root.

- **Tax returns** (`/returns`) groups every return into *Needs a volunteer*, *In progress* and *Draft ready*. An info icon beside each heading says what it means.
- **Needs a volunteer** (`/needs-a-volunteer`) lists every open HITL event with its plain-language reason. Click **Resolve** to choose what happens next:
  - **Carry on with the return as it is**, optionally correcting one answer (pick it, type the new value; it's read the same way as the taxpayer's answer);
  - **Send it back** so SilverFile asks the taxpayer again, reads the documents again, checks eligibility again, works out the numbers again or checks the return again;
  - **Stop here** and prepare the return by hand.

  Then add a note and your name, and **Save decision**. A paused `--interactive` run carries on by itself.
- **A return's page** (`/returns/<Ref.>`) leads with the refund or amount owed and the key figures, then every form line with a plain sentence on where its amount came from (*Show the calculation* opens the full working; lines that come to 0 are folded away). It also shows the documents read and the file each came from, the review result, the answers (SSNs masked, with an eye button to show the full test number), volunteer decisions, and links to the draft form, PDF and `audit.json`.
- **Check and release.** Every finished draft waits for a volunteer, whether or not a HITL event was raised. On the return's page, tick that you've checked it, add your name and click **Release to taxpayer**. The release is recorded in state and in `audit.json`, and only then can the taxpayer see the form.
- **Policy rules** (`/policy`) is the rule book. Every `policy.md#r-…` citation on the dashboard links to its rule.

Old links (`/hitl`, `/sessions/<id>`) redirect to the new pages.

The dashboard has no login and listens only on this machine (`127.0.0.1`). See [SECURITY.md](SECURITY.md).

## The taxpayer page

```bash
python run.py --taxpayer                               # or: make taxpayer
```

Open http://localhost:8001 and click **Start my return**. Run the staff dashboard too, on the same checkpoint database, so a volunteer can resolve events and release the draft.

- **One question per screen**, in large type, with Yes/No buttons, a date picker and *I'm not sure* / *Go back* on every question. It's the same Intake agent as `--interactive`, so the same rules apply (only 900-series test SSNs, nothing echoed back).
- **Upload your forms.** At the documents question the page asks for a photo (JPG or PNG, up to 10 MB) of each form. Photos are checked to be real images and saved under random names in `UPLOAD_DIR` (default `uploads/`, git-ignored). W-2 photos are read by vision or OCR; other forms as photos can't be read yet, so they go to a volunteer.
- **A private link per return** (`/r/web-…`, 128 random bits). There's no login and no list of returns; anyone with the link can continue that return, so the page says to keep it safe.
- **The form only after release.** When the questions end, the taxpayer sees "a volunteer is checking your draft". *View my form* and *Download PDF* appear only after a volunteer clicks **Release to taxpayer** in the dashboard. The taxpayer never sees the staff-facing reason for a HITL event, only that a volunteer is taking a look.

### Grader's dashboard

http://localhost:8001/grader (or **Grader's dashboard** in the taxpayer page's header) lists eval cases 1 and 2 and the 13 negative cases from `evals/cases.json`.

1. Click **Fill** on a case. SilverFile starts a return on the taxpayer page and answers every question from the case file, the same way `run.py --case` does.
2. The replay **stops at every HITL event and never resolves it**. The case page shows the reason, what the case file's volunteer decided (as a hint), and a link straight to that decision in the staff dashboard. Make the decision there, come back and click **Continue filling**.
3. A finished draft still needs **Release to taxpayer** in the staff dashboard; then *Taxpayer view* shows the form.
4. **Expected and actual** compares the run with the case file: HITL conditions raised, how the run ended, and, for the two demo cases, every expected line.

Run both pages on the same checkpoint database (Docker does this). Set `STAFF_DASHBOARD_URL` if the staff dashboard isn't at http://localhost:8000, and `GRADER_DASHBOARD=0` to switch the grader's dashboard off.

## Run with Docker

```bash
docker compose up                                                         # dashboard :8000, taxpayer page :8001
docker compose run --rm app python run.py --case demo/eval_case_1/case.json
docker compose run --rm app python run.py --case demo/eval_case_2/case.json
docker compose run --rm app python run.py --interactive
```

**After pulling new code, rebuild the image** with `docker compose build` (or `docker compose up --build`). `docker compose run` reuses an existing image, so without a rebuild it runs the old code.

The image runs offline by default and includes Tesseract. Both services share one checkpoint database volume, so a run paused on a HITL event can be resolved in the dashboard container. Forms are written to `./outputs` and traces to `./traces` on your machine. To use Claude in Docker, put the key in `.env` and run with `SILVERFILE_OFFLINE=0 docker compose up`. Makefile shortcuts: `make docker-build`, `make docker-up`, `make docker-demo1`, `make docker-test`.

## Context-window measurement

```bash
python run.py --batch                         # 10 taxpayers in one thread, with selective recall
python run.py --batch --no-context-strategy   # the same, carrying the whole history
python -m src.batch --write-metrics           # regenerate docs/metrics.md and the chart
```

Without the strategy, input per taxpayer grows 18.3x from the first to the last; with it, 1.3x. See [docs/metrics.md](docs/metrics.md).

## Repository layout

```
kushim-silverfile-team3/
├── run.py                   # CLI: --case, --interactive, --session, --batch, --dashboard
├── src/
│   ├── state.py             # shared state and order-independent reducers
│   ├── graph.py             # LangGraph wiring (hub, Send fan-out, human_review)
│   ├── messages.py          # validated agent-to-agent messages
│   ├── llm.py               # the one place the model is called; offline fallback
│   ├── memory.py            # SQLite checkpointer, resume summary, context strategy
│   ├── hitl.py              # HITL events
│   ├── guardrails.py        # advice, exploitation and scope detection; outgoing check
│   ├── security.py          # SSN gate, PII masking, injection scan
│   ├── release.py           # a volunteer's sign-off that releases a draft to the taxpayer
│   ├── agents/              # orchestrator, intake, extraction, eligibility, computation, review
│   ├── tools/               # tax_math MCP server and client, tracing
│   ├── form/                # HTML form, IRS PDF filling, audit.json
│   ├── dashboard/           # Flask staff dashboard (port 8000)
│   └── taxpayer/            # Flask taxpayer page (port 8001)
├── data/
│   ├── policy.md            # reference corpus the Eligibility agent cites
│   ├── forms/f1040sr.pdf    # official IRS 2025 Form 1040-SR (fillable)
│   ├── fixtures/            # 1099 and SSA-1099 JSON fixtures, batch scenario
│   └── generate_fixtures.py # draws the W-2 images and writes the fixtures
├── demo/                    # eval cases 1 and 2, W-2 images
├── evals/cases.json         # one regression case per HITL condition
├── tests/
├── docs/                    # graph, metrics, implementation notes, per-area notes, AI disclosure
├── traces/                  # per-session JSONL traces (sample committed)
├── Dockerfile, docker-compose.yml, Makefile
└── SECURITY.md              # threat model and controls
```

## Orchestration diagram

Every agent returns to the orchestrator, which picks the next node from state. Dotted edges are conditional. `extract_dispatch` fans out one `extract_one` branch per document, and they all join at `extract_aggregate`. Design notes: [docs/notes/task_division_and_management.md](docs/notes/task_division_and_management.md). Rendered image: [docs/graph.png](docs/graph.png).

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	orchestrator(orchestrator)
	intake(intake)
	extract_dispatch(extract_dispatch)
	extract_one(extract_one)
	extract_aggregate(extract_aggregate)
	eligibility(eligibility)
	computation(computation)
	review(review)
	human_review(human_review)
	finalize(finalize)
	__end__([<p>__end__</p>]):::last
	__start__ --> orchestrator;
	computation --> orchestrator;
	eligibility --> orchestrator;
	extract_aggregate --> orchestrator;
	extract_dispatch -.-> extract_aggregate;
	extract_dispatch -.-> extract_one;
	extract_one --> extract_aggregate;
	human_review -.-> __end__;
	human_review -.-> orchestrator;
	intake --> orchestrator;
	orchestrator -.-> computation;
	orchestrator -.-> eligibility;
	orchestrator -.-> extract_dispatch;
	orchestrator -.-> finalize;
	orchestrator -.-> human_review;
	orchestrator -.-> intake;
	orchestrator -.-> review;
	review --> orchestrator;
	finalize --> __end__;
	classDef default fill:#f2f0ff,stroke:#7c6fd8,color:#1f1a3d,line-height:1.2
	classDef first fill:#f2f0ff,stroke:#7c6fd8,color:#1f1a3d
	classDef last fill:#bfb6fc,stroke:#7c6fd8,color:#1f1a3d
```

## AI accreditation

Code in this repository was written with Claude (Anthropic) as an AI coding assistant. Team members defined the requirements, directed the design, and reviewed, tested and validated all generated code, and take full responsibility for it. Details of how each member used AI are in [docs/AI_DISCLOSURE.md](docs/AI_DISCLOSURE.md).

## Docs

- [Implementation notes](docs/IMPLEMENTATION_NOTES.md): topology, loop guard, accessibility, volunteered-SSN handling
- Per-area design notes: [docs/notes/](docs/notes/)
- [Context-window metrics](docs/metrics.md)
- [Security and threat model](SECURITY.md)
- [Reference corpus (policy)](data/policy.md)
- [AI-use disclosure](docs/AI_DISCLOSURE.md)
