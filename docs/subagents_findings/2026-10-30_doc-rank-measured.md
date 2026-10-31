# CORTEX_DOC_RANK measured on 337 contract questions: rerank lifts correct answers, dense needs a persisted index first (2026-10-30)

Measurement only. No code changed. The harness, data and raw records live in a scratch directory outside the repo and are not committed.

## What it supports

| Stage | Quality vs flag off (paired, n=337) | Cost on a 4-core CPU | Supports |
|---|---|---|---|
| `dense` | +20 correct (134 vs 114), p=0.004 | warm 1.1 s p50; **first request per process 15 to 26 min** | **Keep off** until passage embeddings are computed at index time and persisted |
| `rerank` | +47 correct (161 vs 114), p=4e-9 | 24 s p50, 29 s p95 per request, every request | **Turn on only where ~25 s extra latency is accepted** (or with a GPU) |
| `dense,rerank` | +91 correct (205 vs 114), p=8e-19 | 25 s warm p50; first request 21 min | **Turn on after the dense cold start is fixed**; it is the best arm on every category |

No stage changes the wrong-answer count significantly (paired p between 0.14 and 0.81). Every arm, including flag off, serves wrong answers, and most of them come from one engine gap (see Finding 2) that has nothing to do with ranking.

## Setup

- **Engine.** A scratch git worktree of main at aae3cbc, with the corpus copied into `data/samples/supplier_contracts/`, because `CONTRACTS_DIR` is global and has no env override. Requests went in-process through `TestClient(create_app())` to `POST /v1/contract/ask`. The session was bound with a signed Ed25519 manifest and a Space, the same bind as the `dms_http` test fixture. `CORTEX_DOC_RANK` was set per request (it is read per request).
- **Ranking models.** The two snapshots at the revisions pinned in `CortexOS/rag/doc_rank.py`, downloaded into a scratch `HF_HUB_CACHE` before the run as measurement setup. CPU torch and sentence-transformers came from a scratch venv. Nothing was downloaded while serving.
- **Answer model.** A loopback OpenVault stand-in on 127.0.0.1 answered the four routes FreeRoute reads and forwarded every chat to **one pinned hosted chat model** for all arms and all questions. Provider retries on 429/5xx were done in the stand-in. `OPENVAULT_BASE_URL` pointed at the stand-in. Provider keys were stripped from the Cortex process environment and never logged. The model identifier is kept out of the repo per the task rule; any chat model that cites as `[n]` reproduces the setup.
  - A first attempt on a different provider hit that provider's free-tier cap of 500 requests a day after 44 questions. Its records were discarded and not scored, and the run restarted from question 0 on the pinned model.
- **Arms.** Flag off twice (`off_a`, `off_b`, the noise floor), `dense`, `rerank`, `dense,rerank`. Each question ran all 5 arms back to back in a seeded random order, in one process (two processes oversubscribed the 4 cores and distorted latency).
- **Network.** A `sys.addaudithook` recorded every `socket.connect`, and `doc_rank._load` was wrapped to tag connects made while loading weights.

## Dataset and licence

- **CUAD v1** (Zenodo record 4595826), licensed **CC BY 4.0** (README line 345). Attribution: Hendrycks, Burns, Chen, Ball, "CUAD: An Expert-Annotated NLP Dataset for Legal Contract Review", 2021.
- **Subset.** 120 contracts, seed 20261030. Each contract is at most 120k characters and has at least 2 parties. In total: 4.44 M characters, 5,689 passages. The corpus held these 120 plus the existing Acme sample.
- **341 questions** from five CUAD categories whose gold answer is a bounded fact: governing law (110, a place), agreement date (101), expiration date (59), renewal term (39, a duration), notice period to stop renewal (28, a duration). Each question names the document title and two parties, for example "Which jurisdiction's law governs the {doc} between {p1} and {p2}?", because the corpus holds 121 contracts. A question's evidence spans must appear in the whitespace-normalised contract text.
- **Gold fact match** (bounded):
  - Places are word-bounded.
  - Dates are exact calendar dates in any of m/d/yy, d/m/yy (when the first number cannot be a month), "7th day of September, 1999", "first (1st) day of January", or ISO. A date counts if it equals the CUAD label or a date the question's own evidence span states. In 3 contracts the label contradicts its own span: 0153 (span 7 September, label 7 July), 0156 (2018 vs 2008), 0266 (11 vs 18 November).
  - Durations are within ±5% in days.
- **Gold passage.** A passage from the question's contract that contains a short evidence span whole, or shares a 50-character window with a long one.

## Scoring (on the customer envelope)

- **Correct:** badge `document`, the gold fact is in the answer body, and a strictly parsed `[n]` citation points at a gold passage (or a same-contract passage that states the fact).
- **Wrong:** badge `document` and either the fact is absent or the cited passage is wrong.
- **Not served:** any other badge. Here that was always the reconfirm, since a document miss falls through to the metric chooser per the 2026-10-29 lane ruling.
- **Recall@4:** a gold passage is among the four passages the reader was shown. For ranked arms these come from the `doc_rank_trace` sha12 list; for flag off, from BM25, which matched the engine's own prompt in 645 of 646 records.
- **Excluded:** 4 of 341 questions, from every arm, because at least one arm hit a provider failure or a "metric chooser is not armed" reconfirm (FreeRoute had parked the hop). 49 questions were first excluded this way; all 5 arms of each were rerun and 45 then passed. Nothing else was dropped.
- **Paired two-sided sign test** per arm against `off_a`, on discordant pairs. The noise floor came first.

## Numbers (n = 337)

Envelope outcomes:

| Arm | Correct | Wrong | Wrong 95% CI (Wilson) | Not served | Recall@4 |
|---|---|---|---|---|---|
| off_a | 114 | 15 | 2.7% to 7.2% | 208 | 0.374 |
| off_b | 112 | 17 | 3.2% to 7.9% | 208 | 0.374 |
| dense | 134 | 8 | 1.2% to 4.6% | 195 | 0.439 |
| rerank | 161 | 13 | 2.3% to 6.5% | 163 | 0.528 |
| dense,rerank | **205** | 11 | 1.8% to 5.7% | 121 | **0.674** |

No arm reached 0 wrong, and n=337 could not certify a rate below about 1% anyway.

Paired sign tests vs `off_a` (arm-only / off-only, p):

| Comparison | Correct | Wrong | Served | Recall@4 |
|---|---|---|---|---|
| off_b (noise floor) | 7 / 9, p=0.80 | 10 / 8, p=0.81 | 17 / 17, p=1.0 | 0 / 0 |
| dense | 32 / 12, p=0.004 | 5 / 12, p=0.14 | 33 / 20, p=0.098 | 23 / 1, p=3e-6 |
| rerank | 57 / 10, p=4e-9 | 8 / 10, p=0.81 | 59 / 14, p=1e-7 | 54 / 2, p=4e-14 |
| dense,rerank | 104 / 13, p=8e-19 | 8 / 12, p=0.50 | 107 / 20, p=2e-15 | 106 / 5, p=1e-25 |

The noise floor is clean. The two flag-off runs differ on 16 questions for correct, all from reader nondeterminism, with p=0.80. Retrieval is identical between them.

Correct answers by category (off_a / dense / rerank / dense,rerank):

| Category | n | off_a | dense | rerank | dense,rerank |
|---|---|---|---|---|---|
| governing law | 110 | 12 | 18 | 32 | 48 |
| agreement date | 101 | 88 | 88 | 92 | 91 |
| expiration date | 59 | 6 | 16 | 20 | 34 |
| renewal term | 39 | 8 | 11 | 13 | 21 |
| notice period | 28 | 0 | 1 | 4 | 11 |

The gain is in clauses buried mid-contract. BM25 already finds the preamble date.

### Latency

The reader averaged about 5 s per call plus provider retries, so end-to-end times here are reader-bound. The rank time is the engine's own cost. CPU: 4 cores, no GPU.

| Arm | End-to-end p50 / p95 | Rank p50 / p95 | Rank, warm embed cache p50 / p95 | Rank, cold first request in a process |
|---|---|---|---|---|
| off_a | 17.8 s / 51.8 s | n/a | n/a | n/a |
| dense | 20.3 s / 107.4 s | 1.3 s / 92.0 s | 1.06 s / 1.43 s | 935 s, 1,182 s, 1,587 s |
| rerank | 39.7 s / 72.7 s | 24.3 s / 29.1 s | (no cache) | 22 s to 254 s |
| dense,rerank | 42.8 s / 93.0 s | 26.1 s / 74.2 s | 25.1 s / 29.5 s | 1,270 s |

- **Weight load.** Embedder 3 to 64 s; reranker 2 to 70 s (including records later replaced by the rerun). The higher numbers came right after a container restart, with a cold page cache.
- **Dense cold cost** scales with the gated pool, not the corpus shown. 146 of 337 dense requests embedded new passages, because the cache lives in process memory only. Each restart started from zero.

### Network

1,099 socket connects in the rerun process alone, all loopback (the stand-in). Across every scored record: **0 non-loopback connects and 0 connects from inside a model loader**. The process segments killed by container restarts did not print their totals, but every record carries its own connect list, and all were loopback.

### Other checks
- `ran` equalled `requested` on all 337 questions in every ranked arm; no stage was skipped.
- The ranking note never appeared in a customer answer.

## Finding 1: dense cold start blocks the first answer for 15 to 26 minutes

- **Expected:** with `CORTEX_DOC_RANK=dense`, a document question answers in a time comparable to flag off.
- **Actual:** the first dense request in each process ran 935 to 1,587 s (1,270 s with rerank), embedding 960 to 1,704 passages of the gated pool on CPU. Later questions over other contracts paid 5 to 130 s again for the passages they newly reached, so dense p95 was 92 s. The cache is an in-process dict, so every restart repeats the cost.
- **Repro:** start a fresh process with the corpus above, set `CORTEX_DOC_RANK=dense`, and ask any governing-law question. Read `ms.total` and `cache.misses` in the `doc_rank_trace` log line.
- **Root-cause class:** O(corpus) work done lazily on the request path. Embedding is index-time work. Doing it per request makes cost scale with corpus size, and losing it on restart repeats it.
- **Invariant:** "A control that blocks legitimate work is itself a failure". A 20-minute first answer is effectively a refusal.
- **Follow-up (not done):** compute passage embeddings when documents are ingested, store them next to the corpus keyed by the pinned revision and the passage sha, and load them at start.

## Finding 2: the reader's reasoning text is served as a document answer

- **Expected:** a served document answer states the fact and cites its passage. A reply that is not an answer is not served.
- **Actual:** 7.1% of successful reader replies (138 of 1,943) were the model reasoning aloud ("The user is asking about ... Looking at the passages: [1] ..."), often cut off at the 600-token cap. `documents.cited_answer` accepts any reply that contains an in-range `[n]`, so these were served under badge `document`. Per arm, 14 to 25 of the served answers were such text, and they make up 6 to 15 of each arm's 8 to 17 wrong answers. This holds with the flag off, so it is independent of DOC_RANK.
  - Example, question 0017 with dense,rerank: an expiration-date question was served 1,967 characters of deliberation ending mid-sentence ("The actual term section ("), with no date.
- **Repro:** run any reader that emits its reasoning in `content`, ask the expiration-date question for the Visp Web Site Building and Hosting Agreement, and read `answer` in the envelope.
- **Root-cause class:** an acceptance gate that checks form, not answer shape. "Contains a valid citation" is necessary, not sufficient. The reply's `finish_reason` (`length` means truncated) is never read on this path, and the length or shape of the reply is not checked.
- **Invariant:** WRONG=0 (CLAUDE.md §8/§9), and "assert the artifact the customer receives": only the envelope showed this, not the SQL or the route.
- **Follow-up (not done):** reject a reply whose `finish_reason` is not `stop`. Bound the answer body, or ask the reader for a short answer line and serve only that.

## Smaller observations
- **Citation format.** One hosted model tried during setup cited as `【1】`. `cited_answer` uses `\[(\d+)\]`, so that reader would have every correct answer turned into a misleading metric-chooser reconfirm. The pinned reader cited `[n]`. A tolerant re-parse (`[1, 2]`, `【1】`, ranges) of its replies found no correct answer the strict parser missed, in any arm.
- **Abandoned reader calls run on.** The engine stops waiting for a reader call while the hop is still working on it, and sometimes retries it. In the first 337 questions, at least 28 reader calls landed in a neighbouring request's window. The cost is provider quota spent on answers nobody reads.
- **Gold-label noise in CUAD.** 3 date labels contradict their own evidence span (above). In 2 more (0049, 0070) the span is the signature-block date while the preamble states another date. Those 2 stay scored as wrong whenever the reader gave the preamble date.

## Repro (full)
1. Download CUAD v1 from Zenodo 4595826. Build the subset with seed 20261030 and the rules above.
2. Create a worktree of aae3cbc and copy the 120 contracts into `data/samples/supplier_contracts/`. Install `.[full,dev]`, CPU torch and sentence-transformers. Download the two pinned snapshots into a scratch `HF_HUB_CACHE`.
3. Run a loopback stand-in that serves `/api/freeroute/status`, `/api/freeroute/ratelimit`, `/api/gate/check` and `/v1/chat/completions` and forwards chat to one pinned model. Set `OPENVAULT_BASE_URL` to it.
4. For each question, bind the session, set `CORTEX_DOC_RANK` per arm, and `POST /v1/contract/ask`. Capture the envelope, the `doc_rank_trace` line, the stand-in's request log and the socket audit events.
5. Score on the envelope as above. Pair arms per question. Exclude a question from all arms if any arm hit a provider failure.
