# Eval case 2: a human steps in

A woman born in 1957 whose husband died in August 2025. She has one W-2 (read from an image), Social Security and a little bank interest. Her filing status could be married filing jointly, qualifying surviving spouse or single, and SilverFile does not choose. It stops, a Kushim volunteer decides, and the run then finishes with a draft return.

Case file: [`eval_case_2/case.json`](eval_case_2/case.json). Expected result: **exactly one HITL event, resolved, then a finished return.**

## The taxpayer

| Question | Answer |
| --- | --- |
| Who is answering | The taxpayer (yes) |
| Name | Rosa Delgado |
| SSN | 902-22-4518 (900-series test number, matches the W-2) |
| Date of birth | 14 March 1957 (65 or older) |
| Blind | No |
| Can be claimed as a dependent | No |
| Filing status | Married filing jointly |
| Did her spouse die in 2025 | **Yes** (this is what triggers the human) |
| Spouse | Luis Delgado, SSN 902-22-4519, born 2 September 1959 (65 or older), not blind |
| Dual-status alien | No |
| Dependents, other income, adjustments, Schedules 1-3, estimated payments | None / 0 |
| Refund destination | Her own checking account at Tompkins Community Bank |

## The documents

| doc_id | Type | File | What it says |
| --- | --- | --- | --- |
| `w2_1` | W-2 | `demo/w2_image_2.jpg` | Rosa Delgado, box 1 wages 18,500; box 2 federal tax withheld 1,200 |
| `ssa_1` | SSA-1099 | `data/fixtures/ssa1099_1.json` | Box 5 net benefits 12,000 |
| `int_1` | 1099-INT | `data/fixtures/1099int_1.json` | Box 1 interest 300 |

## Option A: resolve from the case file (fastest)

Run from the repository root, because document paths are relative.

```bash
python run.py --case demo/eval_case_2/case.json      # or: make demo2
```

You will see the questions replayed, then:

```
HITL FILING_STATUS_AMBIGUITY (raised by intake): The taxpayer's spouse died during 2025, ...
Resolved: continue by Volunteer preparer (case file)
...
Status: complete
```

The case file's `hitl_resolutions` entry is the volunteer's decision: **continue**, with the corrected answer `filing_status = mfj` and a note citing `policy.md#r-fs-02`.

## Option B: resolve it yourself in the dashboard

This is the real volunteer experience. You need two terminals, both in the repository root.

1. **Terminal 1:** `python run.py --interactive`
2. **Terminal 2:** `python run.py --dashboard`, then open http://localhost:8000.
3. In terminal 1, type these answers, one per question:

   | Question (starts with) | Type |
   | --- | --- |
   | Are you the person this tax return is for? | `yes` |
   | What is your first name? | `Rosa` |
   | What is your last name? | `Delgado` |
   | What is your Social Security number? | `902-22-4518` |
   | What is your date of birth? | `March 14, 1957` |
   | Are you legally blind? | `no` |
   | Could someone else ... list you as a dependent ...? | `no` |
   | Which of these best describes you for 2025 ...? | `married filing jointly` |
   | I'm sorry to have to ask this. Did your spouse pass away during 2025? | `yes` |

4. Terminal 1 now prints `Waiting for a Kushim volunteer.` and pauses. This is the HITL event.
5. In the dashboard, click **Needs a volunteer** in the top bar (or open http://localhost:8000/needs-a-volunteer). Find Rosa's row and click **Resolve**. The same return also appears under **Needs a volunteer** on the **Tax returns** page.
6. Read the reason on the page (the spouse died during 2025, so the filing status needs a volunteer). Then fill in the form:
   - **What should happen next?** `Carry on with the return as it is`
   - **Correct an answer:** Answer `Filing status` (already picked), Corrected value `married filing jointly`
   - **Note for the audit record:** `Spouse died August 2025, not remarried: joint return allowed for 2025 (policy.md#r-fs-02).`
   - **Your name:** your own name
7. Click **Save decision**. Terminal 1 carries on by itself and asks the rest of the questions. Type:

   | Question (starts with) | Type |
   | --- | --- |
   | What is your spouse's first name? | `Luis` |
   | What is your spouse's last name? | `Delgado` |
   | What is your spouse's Social Security number? | `902-22-4519` |
   | What is your spouse's date of birth? | `September 2, 1959` |
   | Is your spouse legally blind? | `no` |
   | Did you live in the United States ... for only part of 2025? | `no` |
   | Is there anyone ... that you want to list as a dependent? | `no` |
   | Which tax forms did you receive for 2025 ...? | `W-2, SSA-1099 and 1099-INT` |
   | Where is the file for your W-2 saved? | `demo/w2_image_2.jpg` |
   | Where is the file for your SSA-1099 saved? | `data/fixtures/ssa1099_1.json` |
   | Where is the file for your 1099-INT saved? | `data/fixtures/1099int_1.json` |
   | Did you have any other income in 2025 ...? | `no` |
   | What is the amount on line 26 of your Schedule 1? | `none` |
   | Do you have Schedules 1, 2 or 3 with amounts ...? | `no` |
   | How much did you pay the IRS in estimated tax payments ...? | `none` |
   | If you get a refund, whose bank account will it go into? | `my own checking account at Tompkins Community Bank` |

8. The run ends with `Status: complete`. Open Rosa's return in the dashboard (the **Draft ready** group) to see the refund, every line with its working, the volunteer's decision under **Volunteer decisions**, and the note. The `policy.md#r-fs-02` in the note shows as a link to the rule on the **Policy rules** page.

## What to check

- Exactly **one** HITL event appears (`FILING_STATUS_AMBIGUITY`), raised by Intake the moment the spouse question is answered yes. The Eligibility agent runs after it and does not raise a second one. Without that check the volunteer would resolve the same thing twice.
- Nothing is computed until the volunteer has decided.
- After "continue", the run completes without asking anyone anything else.

## Where the finished return is

The run prints the output location at the end (`audit: outputs/<session_id>/audit.json`). The audit record holds every line with its provenance. The HTML form (`form_1040sr.html`) is written next to it once the form renderer is merged; until then only `audit.json` is produced.

## Expected result

| Line | Value | Where it comes from |
| --- | --- | --- |
| 1z | 18,500 | W-2 box 1 |
| 2b | 300 | 1099-INT box 1 |
| 6a / 6b | 12,000 / 0 | Social Security worksheet: provisional income is 18,800 + 6,000 = 24,800, below the 32,000 joint base, so none is taxable |
| 9 / 11a | 18,800 | 18,500 + 300 |
| 12e | 34,700 | 31,500 joint base + 2 boxes × 1,600 (Rosa and Luis are both 65 or older) |
| 13b | 12,000 | Schedule 1-A: 2 × 6,000, no phase-out (income is below 150,000) |
| 14 | 46,700 | 34,700 + 12,000 |
| 15 / 16 | 0 / 0 | Deductions are larger than income |
| 25a / 33 | 1,200 | W-2 box 2 |
| 34 | 1,200 | Refund: all of the tax withheld |

`expected.hitl_conditions` in the case file is `["FILING_STATUS_AMBIGUITY"]`, and `tests/test_evals.py` fails if any other event appears.

## Why joint, and who decides

The volunteer's choice is test data. In 2025 a spouse who died during the year and has not been replaced by a new marriage can still file jointly (`policy.md#r-fs-02`), and qualifying surviving spouse is not available for 2025 without a dependent child (`policy.md#r-fs-04`). SilverFile does not choose between the options, because that is advice; it stops, explains why (`policy.md#r-fs-03`), and records who decided and when.

## Other ways to trigger a human

`evals/cases.json` has one case for each remaining condition (advice, someone else answering, an undescribed refund account, a 1099-R with box 2b checked, a 1099-NEC, rental income, a W-2 that belongs to someone else, and more). `pytest tests/test_evals.py` runs them all.
