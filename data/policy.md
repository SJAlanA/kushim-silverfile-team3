# SilverFile Reference Corpus

The rules the Eligibility & Rules agent decides by, and the only text it may cite. Each rule has a stable ID, which is also its anchor: `policy.md#r-elig-01` is rule `R-ELIG-01`. Every finding Eligibility returns names one of these IDs, and the agent checks at run time that the ID exists in this file.

Sources: **Brief** is the Assignment 7 brief (section numbers in brackets). **1040-SR** is the 2025 Form 1040-SR and its instructions. SilverFile is a preparation aid: it never files, never gives tax advice, and never states a dollar amount it cannot source.

Rule format: the rule, its source, then what SilverFile does (`Action`).

## Eligibility for Form 1040-SR

### R-ELIG-01
**Who may use Form 1040-SR.** For tax year 2025 the taxpayer must have been born before 2 January 1961. On a joint return only one spouse needs to meet this. There is no income restriction.
Source: Brief [3.2 rule 1]; 1040-SR, "Who can use".
Action: eligible when the taxpayer, or on a joint return either spouse, was born before 1961-01-02. If no one qualifies, the return is out of scope: escalate `OUT_OF_SCOPE`.

## Filing status

### R-FS-01
**Five statuses.** Single, married filing jointly (MFJ), married filing separately (MFS), head of household (HOH) and qualifying surviving spouse (QSS) are all in scope.
Source: Brief [3.3].
Action: any other value is not a filing status; escalate `FILING_STATUS_AMBIGUITY`.

### R-FS-02
**Married at the end of the year, or spouse died during the year.** A person who is married on 31 December, or whose spouse died during 2025 and who has not remarried, may file jointly for 2025.
Source: 1040-SR instructions, "Filing status".
Action: MFJ is allowed in the year of death. SilverFile does not choose between MFJ, QSS and Single; it asks a person (R-FS-03).

### R-FS-03
**Spouse who died during 2025.** The right status depends on facts SilverFile does not collect (remarriage, dependent child, who is filing for the deceased). This is the most common filing-status mistake for widowed filers.
Source: Brief [4.4 condition 5].
Action: always escalate `FILING_STATUS_AMBIGUITY`. After a volunteer decides, the chosen status is used and the event is not raised again.

### R-FS-04
**Qualifying surviving spouse (QSS).** Available for the two years after the year of death, and only with a dependent child. A spouse who died in 2025 does not make QSS available for 2025 (see R-FS-02).
Source: 1040-SR instructions, "Qualifying surviving spouse".
Action: QSS with no dependent listed is unsupported by the answers; escalate `FILING_STATUS_AMBIGUITY`.

### R-FS-05
**Head of household (HOH).** For an unmarried (or considered unmarried) person who paid more than half the cost of keeping up a home for a qualifying person, who is normally a listed dependent.
Source: 1040-SR instructions, "Head of household".
Action: HOH with no dependent listed is unsupported by the answers; escalate `FILING_STATUS_AMBIGUITY`.

## Standard deduction

### R-SD-01
**Base amounts for 2025.** Single and MFS: $15,750. MFJ and QSS: $31,500. HOH: $23,625. These are applied by the `standard_deduction` tool, never by Eligibility.
Source: Brief [3.2 rule 2]; 1040-SR, Standard Deduction Chart for Seniors.
Action: Eligibility passes the filing status and box count; Computation gets the dollars from the tool.

### R-SD-02
**Box count (line 12d).** One box each for: taxpayer 65 or older, taxpayer blind, spouse 65 or older, spouse blind. Spouse boxes apply only when a spouse is on the return (MFJ or MFS). A person is 65 for 2025 if born before 2 January 1961.
Source: Brief [3.2 rule 2]; 1040-SR, line 12d.
Action: `box_count` is a count of the checked boxes, from 0 to 4. On MFS the spouse boxes are counted as the brief says, with a note for the volunteer to confirm them.

### R-SD-03
**Amount per box.** Each checked box adds $2,000 if the person is unmarried and not a surviving spouse, otherwise $1,600. The `standard_deduction` tool applies this from the filing status.
Source: Brief [3.2 rule 2].
Action: none in Eligibility.

### R-SD-04
**When the chart does not apply.** The Standard Deduction Chart does not apply if the taxpayer can be claimed as a dependent, if a separately-filing spouse itemizes, or if the taxpayer was a dual-status alien.
Source: Brief [3.2 rule 2]; 1040-SR, Standard Deduction Chart for Seniors.
Action: any of the three escalates `OUT_OF_SCOPE`. The spouse-itemizes test applies only to MFS.

## Schedule 1-A senior deduction

### R-SR-01
**Enhanced senior deduction (new for 2025).** An additional $6,000 for each qualifying person aged 65 or older ($12,000 if both spouses on a joint return qualify), claimed on Schedule 1-A and carried to line 13b. It is separate from, and in addition to, the additional standard deduction in R-SD-03. It phases out above $75,000 of income ($150,000 joint) at 6% of the excess. Applies to tax years 2025 to 2028. MFS filers cannot take it.
Source: Brief [3.2 rule 3].
Action: `senior_qualifiers` is the number of people on the return aged 65 or older (taxpayer, plus spouse on MFJ), and 0 for MFS. The dollars and the phase-out are the `senior_deduction` tool's job.

## Social Security

### R-SS-01
**Taxable Social Security ordering.** Line 6b comes from a worksheet over provisional income, which needs the rest of income first (lines 1z, 2b, 3b, 4b, 5b, 8 and 10), and line 9 needs 6b.
Source: Brief [3.2 rule 4].
Action: Eligibility maps the SSA-1099 to the 6a family and leaves the ordering to Computation.

## Scope

### R-SCOPE-01
**In-scope documents.** Amounts from W-2, SSA-1099, 1099-R, 1099-INT and 1099-DIV.
Source: Brief [3.3].
Action: these documents are mapped to lines in `doc_line_map`.

### R-SCOPE-02
**Standard deduction only; Schedules 1, 2 and 3 as typed.** The return uses the standard deduction and the Schedule 1-A senior deduction. Amounts for Schedules 1, 2 and 3 are typed in by the taxpayer and recorded as direct user input.
Source: Brief [3.3].
Action: `schedules` lists the schedules that apply.

### R-SCOPE-03
**Itemized deductions (Schedule A) are out of scope.**
Source: Brief [3.3].
Action: escalate `OUT_OF_SCOPE`.

### R-SCOPE-04
**Self-employment or business income (Schedule C), including 1099-NEC non-employee compensation, is out of scope.**
Source: Brief [3.3].
Action: escalate `OUT_OF_SCOPE`.

### R-SCOPE-05
**Rental income (Schedule E) is out of scope.**
Source: Brief [3.3].
Action: escalate `OUT_OF_SCOPE`.

### R-SCOPE-06
**Capital gains that need Schedule D are out of scope.**
Source: Brief [3.3].
Action: escalate `OUT_OF_SCOPE`.

### R-SCOPE-07
**Digital assets are out of scope.**
Source: Brief [3.3].
Action: escalate `OUT_OF_SCOPE`.

### R-SCOPE-08
**Foreign income or accounts are out of scope.**
Source: Brief [3.3].
Action: escalate `OUT_OF_SCOPE`.

### R-SCOPE-09
**Amended returns and prior-year returns are out of scope.**
Source: Brief [3.3].
Action: escalate `OUT_OF_SCOPE`.

### R-SCOPE-10
**State returns are out of scope.** SilverFile drafts the federal Form 1040-SR only.
Source: Brief [3.3].
Action: escalate `OUT_OF_SCOPE`.

### R-SCOPE-11
**Any other document type is out of scope.** If a document is not one of the five in R-SCOPE-01, SilverFile cannot say what it means for the return.
Source: Brief [3.3].
Action: escalate `OUT_OF_SCOPE` and leave the document out of `doc_line_map`.

### R-SCOPE-12
**Other income SilverFile cannot classify.** If the taxpayer says there was other income but the description matches no rule above, SilverFile cannot tell whether it belongs on Schedule 1 or is out of scope.
Source: Brief [4.3 honest uncertainty], [4.4].
Action: escalate `UNCERTAIN` so a volunteer decides.

## Pensions and annuities

### R-1099R-01
**Taxable amount not determined.** A 1099-R with box 2b "taxable amount not determined" checked needs the Simplified Method or the General Rule, which SilverFile does not do. This escalates regardless of what else is known.
Source: Brief [3.3 note], [4.4 condition 3].
Action: escalate `R1099_TAXABLE_NOT_DETERMINED` for that document.

## Documents versus what the taxpayer said

### R-DOC-01
**A document must match the person.** A W-2 must belong to the taxpayer or, on a joint return, to the spouse. The employee name (first and last, ignoring case and middle initials) and the last four digits of the Social Security number must agree with what the taxpayer gave. SilverFile does not decide which one is right.
Source: Brief [4.4 condition 2].
Action: a mismatch escalates `DOC_CONFLICT` for that document.

## Advice, exploitation and identity data

### R-ADV-01
**No advice, planning or optimisation.** "Should I convert my IRA to a Roth?", "Would I pay less if we filed separately?" and "Can I deduct my medications?" are out of scope. Transcription and arithmetic are not advice; optimisation and planning are.
Source: Brief [2], [4.4 condition 4].
Action: Intake detects the request, says in one sentence that SilverFile cannot advise, offers to note the question for the volunteer preparer, and escalates `TAX_ADVICE`. No agent reply may contain advice phrasing.

### R-EXP-01
**Notice and stop on possible exploitation.** Signals include someone else answering for the taxpayer without stated authority, a refund routed to an account the taxpayer cannot describe, urgency or pressure, and requests to change where a refund goes. SilverFile does not decide whether abuse is happening.
Source: Brief [4.4 condition 6].
Action: Intake escalates `EXPLOITATION` with a neutral reason. A volunteer follows up.

### R-SSN-01
**Test Social Security numbers only.** This is a practice system. Only numbers starting with 900 to 999 are accepted, so a real number is never stored. A number volunteered where none was asked for is not saved.
Source: Brief [3.2 final paragraph], [4.3 no unnecessary PII].
Action: enforced at entry by Intake and Security.

## System limits

### R-SAFE-01
**Never file; never assert an unsourced amount.** The output is a draft plus an audit record for a human. Every dollar amount carries a provenance record, and an agent reply to the taxpayer may not contain a dollar amount that is not on the draft return.
Source: Brief [2], [4.2].
Action: `check_outgoing` blocks any reply that breaks this.
