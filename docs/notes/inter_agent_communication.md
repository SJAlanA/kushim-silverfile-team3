# Inter agent communication: design notes

Owner: Hamdan. Files: `src/state.py`, `src/messages.py`, `src/llm.py`, `src/agents/extraction.py`, `data/generate_fixtures.py`.

## Agents talk through state, not through each other

No agent calls another agent. Each one returns a partial state update. Routing information travels as `AgentMessage`s appended to `handoffs`, and only the orchestrator writes `next_agent`. `src/messages.py` builds every message and rejects unknown node names and kinds. Without that check, a typo like `"Computation"` would create a message that no routing rule matches, and the graph would quietly stall. With it, the sender fails straight away. The append-only `handoffs` log doubles as the audit trail, and the loop guard can count it.

## Reducers are order-independent

The extraction step fans out one `extract_one` branch per document with `Send`, and the branches write `extracted` concurrently. LangGraph folds those writes with our reducer. If the result depended on which branch finished first, the same return could come out differently on two runs, and the provenance record would not be reproducible. So every reducer is a pure function of the *set* of writes:

- `merge_extracted` ranks readings of the same `doc_id` by confidence, then extractor, then a canonical JSON tie-break. A disagreeing reading is not overwritten; it is kept under `_conflicts`, and extraction escalates it.
- `merge_answers` keeps a lossless `_versions` log and derives `history` from it. Our first version dropped repeated answers on the way in. The permutation test showed that this made the result depend on fold order, because a dropped version could matter to a later merge. Deriving `history` from a complete log fixed it.
- `merge_lines` picks the newer `provenance.at`, and `merge_hitl` lets a resolved event beat its open version, so an event can never be reopened by a late write.

`tests/test_merge_order.py` folds each reducer over every permutation of its writes, and runs a real three-branch `Send` graph in every dispatch order.

## Extraction reads paper; it does not interpret it

`extract_document` tries vision (Claude, online), then OCR (rapidocr, or Tesseract if installed), then JSON. A sidecar transcription is a last resort, and the record says `extractor: "json"` with a warning, so Review knows the image was not actually read. OCR finds each box by its label and takes the nearest line beneath it in the same column. That works because the labels are on every W-2, whatever the layout.

The agent never assigns line numbers or decides what is taxable. Blank boxes read as 0. An amount that is present but unreadable becomes `None` and drops the confidence, so a person is asked rather than the agent guessing. Only the last four digits of an SSN are stored. All document text goes through `scan_untrusted_text`, and the vision prompt tells the model that text in the image is data, never instructions. Only whitelisted fields survive.

## Trade-offs and failure modes

- A corrected file uploaded under the same `doc_id` can lose to an older, higher-confidence reading. A replacement should use a new `doc_id`.
- OCR on a rotated or blurred scan keeps the dollar amounts but can lose names, which are informational only.
- The lossless answer log grows with every re-answer. That is fine for one intake, but it is not bounded.
