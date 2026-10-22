# Context-window metrics

Batch scenario (brief 4.5): 10 taxpayer intakes from `data/fixtures/batch/` run one after another in
one LangGraph thread. Each row is the total input tokens the Intake agent's model calls use for that
taxpayer (about 20 question turns each).

- **No strategy** (`python run.py --batch --no-context-strategy`): every prompt carries the whole
  thread history, so cost grows with every taxpayer already processed.
- **Strategy** (`python run.py --batch`): selective recall. Each prompt carries only the current
  taxpayer's messages plus a rolling decisions summary capped at the last five taxpayers.

Offline run: counts are estimated as `len(prompt) // 4` over the exact prompt Intake would send. Regenerate with `python -m src.batch --write-metrics`.

Taxpayers need different numbers of questions, so the last two columns divide by the number of
Intake model calls for that taxpayer. That per-question view shows the shape most clearly: it climbs
steadily with no strategy and stays flat with selective recall.

| # | Item | Questions | Input tokens (no strategy) | Input tokens (strategy) | Per question (no strategy) | Per question (strategy) |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | batch_01 | 16 | 5,532 | 5,532 | 345 | 345 |
| 2 | batch_02 | 22 | 22,082 | 9,187 | 1,003 | 417 |
| 3 | batch_03 | 16 | 26,856 | 6,354 | 1,678 | 397 |
| 4 | batch_04 | 16 | 36,507 | 6,527 | 2,281 | 407 |
| 5 | batch_05 | 22 | 64,293 | 10,535 | 2,922 | 478 |
| 6 | batch_06 | 23 | 84,093 | 11,886 | 3,656 | 516 |
| 7 | batch_07 | 17 | 74,078 | 8,203 | 4,357 | 482 |
| 8 | batch_08 | 16 | 79,819 | 7,338 | 4,988 | 458 |
| 9 | batch_09 | 22 | 123,999 | 11,359 | 5,636 | 516 |
| 10 | batch_10 | 16 | 101,023 | 7,317 | 6,313 | 457 |

| | No strategy | Strategy |
| --- | --- | --- |
| First item | 5,532 | 5,532 |
| Last item | 101,023 | 7,317 |
| Growth, last / first | 18.3x | 1.3x |
| Total | 618,282 | 84,238 |

![Per-item input tokens](context_strategy.svg)
