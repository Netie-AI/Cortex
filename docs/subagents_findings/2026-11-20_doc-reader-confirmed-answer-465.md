# #465: a document answer was served on the form of the reply (2026-11-20)

Gate: `CortexOS/rag/documents.py::read`, reached through
`query_service.rag_answer` by `answer_engine._space_doc_rag`, the legacy
route `rag` and the lane router (`lane_ask._try_document_lane`).

## Expected vs actual (main `304bf84`)

- Expected: only a finished answer that states the fact asked, from the
  passages it cites, is served under badge `document`. Anything else falls
  through as a document miss.
- Actual: any reader reply with an in-range `[n]` was served. The issue's
  measurement: 138 of 1,943 successful reader replies (7.1%) were the model
  reasoning aloud, often cut at the token cap, served as answers. On the
  branch's test file, main's code fails 141 of 145.

## Repro

`python -m pytest tests/dms/test_doc_reader_answer_465.py -q` with
`documents.py`, `query_service.py` and `integrations/freeroute.py` taken from
`origin/main`: 141 failed. Shortest: reader reply
`The question asks for the payment terms ... so the answer is probably Net 60 [1].`
is served by `POST /v1/contract/ask` with badge `document`.

## Root-cause class

**A form check standing in for a meaning check.** A valid citation is
necessary, not sufficient. The first fix (structured `{"answer","citations"}`
reply plus `finish_reason`) narrowed the form, and three Opus attackers showed
it only moved the trust boundary into the `answer` field: reasoning, a hedge
("does not state ... [1]") or a contradiction (`Net 60 [1]` against a Net 30
passage) inside a well-formed object was served on every path. The lane
router's check (#460) never sees the passages, and after a router fall-through
the ladder read the documents again and served with no check at all.

## Fix

- Form: one JSON object, no repeated key (`json.loads` keeps the last one), an
  answer with text besides its citations, no `Sources:` line written by the
  model, no miss sentinel in any spelling, ASCII citation digits, and a
  `finish_reason` of `stop` or none. A leading `<think>` block and an upper-case
  fence tag are accepted.
- Meaning: a second call (`doc-answer-check`) sees the question, the answer and
  the numbered passages and must reply a strict `{"answers": true}`. It runs in
  `documents.read`, so every caller of `rag_answer` is covered, the router's
  re-read included. No check configured, nothing served.
- `Completion.finish_reason` carries the provider's value (FreeRoute).

Cost: one more FreeRoute call per served document answer: reader plus check
on the ladder path; router, reader, check and the router's own check (four)
on the lane path.

## Invariants that applied

- Adversary is not the verifier: the writer's 83 tests passed on the first
  version; attackers A and C found served non-answers within 10 minutes.
- Assert the artifact the customer receives: every must-fail runs through
  `POST /dms/query`, `POST /v1/contract/ask` or the lane router envelope.
- Prove a gate can fail: main's code 141 red; check ignored 38; repeated keys
  7; content-free or own Sources line 9; finish_reason ignored 9; old citation
  regex 6; the check inside `rag_answer` always yes 9; sentinel exact in the
  parser 3. The sentinel check in `read` on the raw reply is a redundant layer:
  a bare sentinel is not JSON, so that mutation alone stays green.
- Lane ownership: a first version edited `tests/freeroute_fake.py` (#215 lane);
  `test_branch_does_not_dual_write_freeroute_layer` went red; reverted.

## Open, by name

- Re-measure on the 337 set (issue acceptance): needs a live OpenVault, not
  reachable from this session. Not run.
- DOC-ANSWER-SOURCES-ALL: `DocAnswer.sources` lists every retrieved passage,
  not only the cited ones (pre-existing).
- Over-refusal by form: `[1, 2]` inline style, extra keys, citations as
  strings, a preamble before the object, provider-native finish reasons
  (`end_turn`, `STOP`). Each falls through as a miss. The recorded OpenVault
  response uses `stop`.
- `freeroute.complete` raises on `choices` as a dict or `message` as a string
  (pre-existing); `_space_doc_rag` does not catch it.

## Subagent shape

Three Opus attackers in parallel, isolated, read-only, function level (no
pytest, no TestClient): A reply form and parser, B finish_reason plumbing and
coverage, C served content end to end with main-vs-branch replay. Then one
Opus verifier on the reworked head. Build and fixes on the session model.
