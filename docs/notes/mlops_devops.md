# MLOps/DevOps: Computation, tax_math and reproducibility

Owner: Parth Chandak.

## Why every number goes through MCP

We treat the no-arithmetic rule as an audit requirement. All tax math lives in one MCP server (`src/tools/tax_math_server.py`) of eight pure functions, each returning the value with its working (`value`, `formula`, `inputs`, `tool`, `method_note`). The agent only passes document fields and answers to tools, so each line's provenance is the tool's own record. Review calls the same tools, so a disagreement points at an input, never at two implementations of one rule.

The client keeps one stdio session open per process, so 34 tool calls pay the start-up cost once. `TAX_MATH_INPROCESS=1` calls the same functions directly; a test checks both paths agree.

## Ordering is data, not code flow

`STEPS` in `src/agents/computation.py` lists every step with the lines it produces and the lines it reads. It is checked when the module loads: every contract line is produced exactly once, and nothing is read before it is computed. That makes the Social Security dependency (6b needs the rest of income, and 9 needs 6b) impossible to break silently. The same table drives send-backs: when Review asks for line 16 again, only 16 and its downstream lines (18, 22, 24, 34, 37) are recomputed.

## How we test "no arithmetic"

A test swaps `call_tool` for a fake returning a unique sentinel per call. Every tool-derived line must carry a sentinel, so a value added up in Python fails. Another check confirms `computation.py` never calls `sum`, `min`, `max` or `round`.

## Reproducibility

- Tax figures sit in one module (`src/tools/constants.py`), checked against Rev. Proc. 2024-40 and the brief.
- Versions: `requirements.txt` gives bounded ranges and `make lock` writes exact versions to `requirements.lock`, which Docker and CI prefer. The bounds matter: `mcp` 2.0 renamed `FastMCP`, and the server supports both.
- Docker runs offline by default. The dashboard and CLI services share the checkpoint database volume, so a run paused on a HITL event can be resolved in the dashboard container.
- Every session writes `traces/<session_id>.jsonl` (node start/end with timing, every tool call with inputs and output).

## Failure modes and trade-offs

- **Missing rules.** If Eligibility gives no filing status, box count or senior count, Computation raises `UNCERTAIN` instead of guessing. The cost is that the pipeline stops rather than drafting.
- **1099-R box 2b checked.** Computation refuses and raises `R1099_TAXABLE_NOT_DETERMINED`, even if Eligibility missed it.
- **Rounding.** Tools round half up at the end of each worksheet, so hand calculations rounding each line can differ by a dollar.
- **Line 16 uses the rate schedule**, not the Tax Table the IRS requires below $100,000. The contract fixes this choice and the `method_note` says so; results can differ from the Tax Table by a few dollars.
- **Docker runs as root** so bind-mounted `outputs/` stays writable on any host: "just works" over least privilege, for a classroom demo.

## How to run

```bash
make install && make test      # offline
make demo1                     # eval case 1
make dashboard                 # http://localhost:8000
docker compose up              # dashboard in Docker
docker compose run --rm app python run.py --case demo/eval_case_1/case.json
```
