# Context-window metrics

Batch scenario (brief 4.5): 10 taxpayer intakes from `data/fixtures/batch/` run one after another in
one LangGraph thread. Each row is the total input tokens the Intake agent's model calls use for that
taxpayer (about 20 question turns each).

- **No strategy** (`python run.py --batch --no-context-strategy`): every prompt carries the whole
  thread history, so cost grows with every taxpayer already processed.
- **Strategy** (`python run.py --batch`): selective recall. Each prompt carries only the current
  taxpayer's messages plus a rolling decisions summary capped at the last five taxpayers.

Offline run: counts are estimated as `len(prompt) // 4` over the exact prompt Intake would send. Regenerate with `python -m src.batch --write-metrics`.

| # | Item | Input tokens (no strategy) | Input tokens (strategy) |
| --- | --- | --- | --- |
| 1 | batch_01 | 5,532 | 5,532 |
| 2 | batch_02 | 22,082 | 9,187 |
| 3 | batch_03 | 26,856 | 6,354 |
| 4 | batch_04 | 36,507 | 6,527 |
| 5 | batch_05 | 64,293 | 10,535 |
| 6 | batch_06 | 84,093 | 11,886 |
| 7 | batch_07 | 74,078 | 8,203 |
| 8 | batch_08 | 79,819 | 7,338 |
| 9 | batch_09 | 123,999 | 11,359 |
| 10 | batch_10 | 101,023 | 7,317 |

| | No strategy | Strategy |
| --- | --- | --- |
| First item | 5,532 | 5,532 |
| Last item | 101,023 | 7,317 |
| Growth, last / first | 18.3x | 1.3x |
| Total | 618,282 | 84,238 |

![Per-item input tokens](context_strategy.svg)
