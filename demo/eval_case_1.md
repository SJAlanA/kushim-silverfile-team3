# Eval case 1: clean run

A single filer born in 1958 with one W-2 (read from an image), Social Security, a little bank interest and a few dividends. Nothing in this case needs a human, so the run should finish with a draft return and no HITL events.

Case file: [`eval_case_1/case.json`](eval_case_1/case.json)

> **Quickest way in a browser:** run `docker compose up` (or `python run.py --dashboard` and `python run.py --taxpayer`), open http://localhost:8001/grader and click **Fill eval case 1**. The page fills in the answers for you. Then release the draft in the staff dashboard to see the form in the taxpayer view. The steps below do the same from the terminal.

## The taxpayer

| Question | Answer |
| --- | --- |
| Who is answering | The taxpayer (yes) |
| Name | Mara Lindqvist |
| SSN | 901-11-1037 (900-series test number) |
| Date of birth | 21 June 1958 (65 or older, so one box on line 12d) |
| Blind | No |
| Can be claimed as a dependent | No |
| Filing status | Single |
| Dual-status alien | No |
| Dependents | None |
| Other income (self-employment, rental, crypto, foreign, capital gains) | None |
| Adjustments, Schedules 1-3 values | 0 |
| Estimated tax payments | 0 |
| Refund destination | Her own checking account at Tompkins Community Bank |

## The documents

| doc_id | Type | File | What it says |
| --- | --- | --- | --- |
| `w2_1` | W-2 | `demo/w2_image_1.jpg` | Box 1 wages 30,000; box 2 federal tax withheld 2,000 |
| `ssa_1` | SSA-1099 | `data/fixtures/ssa1099_1.json` | Box 5 net benefits 12,000 |
| `int_1` | 1099-INT | `data/fixtures/1099int_1.json` | Box 1 interest 300 |
| `div_1` | 1099-DIV | `data/fixtures/1099div_1.json` | Box 1a ordinary dividends 100, of which box 1b qualified 50 |

## Steps

1. Set up the environment (see the README), then regenerate the fixtures. This is optional: the files are committed, and the script produces identical files every time.

   ```bash
   python data/generate_fixtures.py
   ```

2. Check the W-2 image is read from the picture itself. With no API key this uses OCR. The output should show `"extractor": "ocr"`, `box1_wages` 30000 and `box2_fed_withheld` 2000.

   ```bash
   SILVERFILE_OFFLINE=1 python -m src.agents.extraction demo/w2_image_1.jpg
   ```

   With `ANTHROPIC_API_KEY` set it uses Claude vision instead (`"extractor": "vision"`). If neither OCR nor a key is available it falls back to the transcription file `demo/w2_image_1.json` and says so (`"extractor": "json"`, plus a warning). The values are the same, but the record does not pretend the image was read.

3. Run the case end to end. The answers are replayed one question at a time.

   ```bash
   python run.py --case demo/eval_case_1/case.json
   ```

4. Open `outputs/<session_id>/form_1040sr.html`. Hover over or click any number to see where it came from.

## Expected result

| Line | Value | Where it comes from |
| --- | --- | --- |
| 1z | 30,000 | W-2 box 1 |
| 2b | 300 | 1099-INT box 1 |
| 3a / 3b | 50 / 100 | 1099-DIV boxes 1b / 1a |
| 6a | 12,000 | SSA-1099 box 5 |
| 6b | 6,540 | Social Security worksheet. Provisional income is 30,400 + 6,000 = 36,400, which is 2,400 over the 34,000 second tier, so 85% × 2,400 + 4,500 = 6,540 |
| 9 / 11a | 36,940 | 30,000 + 300 + 100 + 6,540 |
| 12e | 17,750 | 15,750 single base + 1 box × 2,000 |
| 13b | 6,000 | Schedule 1-A senior deduction, no phase-out (income is below 75,000) |
| 14 | 23,750 | 17,750 + 6,000 |
| 15 | 13,190 | 36,940 − 23,750 |
| 25a | 2,000 | W-2 box 2 |

Lines 16 (tax) and 34 (refund) are left for the Computation agent to confirm. They come from the 2025 rate schedule, with the 50 of qualified dividends taxed at 0%.

No HITL events are expected (`expected.hitl_conditions` is empty).
