# S2 — Stream broker shortlist (NATS JetStream vs Redpanda)

**Research date:** 2026-07-22  
**Task:** A2 (CURSOR_EXEC_PACKET) — research only; no Python/JSX/tests modified.  
**Prereq shipped:** S0 webhook → bronze (`packs/dms/streams/*`, `CortexOS/api/stream_routes.py`).  
**Stress baseline:** ~379 ev/s @ 8 threads, 0 errors (`CHANGELOG_DMS.md`, buffer concurrency fix).

---

## Verdict + recommended V1

**Recommend V1: NATS JetStream (embedded / sidecar on Windows).**

| Criterion | NATS JetStream | Redpanda |
|---|---|---|
| Windows-first DMS box | Native `nats-server.exe` (amd64/arm64 zip); JetStream in same binary (`-js`) | No native Windows broker; Docker Desktop + WSL2 (or Linux VM) |
| Ops weight | Single binary, scoop/choco optional, file store dir | Container runtime + volume + Kafka client stack |
| Python client | `nats-py` (async-friendly) | Kafka-protocol clients (`aiokafka` / `confluent-kafka`) + Quix later |
| Bronze landing | Same S0 contract via consumer → `buffer.append_events` / `_write` | Same S0 contract |
| Client Kafka/Flink estates | Not Kafka wire; bridge later if needed | Kafka API + Quix Streams (BUILD_PLAN V2) |
| Fit for wave-3 S2 | **V1 embedded option** | **V2 interop tier** when a client brings Kafka |

**V1 shape:** keep `POST /dms/streams/{id}/events` as the public ingest path; optionally publish durable copies onto a JetStream subject/stream. A thin consumer (or DBOS workflow per BUILD_PLAN S2) drains JetStream → existing bronze writer. S0 in-memory buffer remains the local fast path and the single bronze writer.

**V2 shape (defer):** Redpanda in Docker/WSL2 + Quix Streams when a customer needs Kafka-compatible producers/consumers. Do not make Redpanda the default on a Windows-first sovereign box.

---

## Landing contract mapping to S0 bronze (must not break)

S0 already defines the durable landing surface. S2 brokers are **upstream durability / fan-in**, not a second lake schema.

### Immutable bronze row shape (S0)

Table: `lake.bronze.stream_<sanitized_stream_id>`

| Column | Source |
|---|---|
| `event_id` | Explicit `event_id` or SHA-256 of sorted JSON payload (first 32 hex) |
| `ts` | Event `ts` or ingest UTC ISO |
| `payload` | Full event JSON string |
| `_stream_id` | Registry / route stream id |
| `_received_at` | Writer wall clock |

Writer: `packs/dms/streams/buffer.py` → `_write_batch` (batched multi-row INSERT under `_WRITE_LOCK`).

### Semantics S2 must preserve

| Guarantee | S0 today | S2 requirement |
|---|---|---|
| Delivery | At-least-once (unflushed in-memory batch lost on crash) | At-least-once into bronze; broker may redeliver → **dedup by `event_id`** (per-batch today; extend to lake-side / idempotent consumer if broker acks after write) |
| Dedup | Content-hash / `event_id` within drained batch | Same identity function; do not invent a second id scheme |
| Backpressure | `BackpressureError` → HTTP **429** past `DMS_STREAM_HARD_CAP` | HTTP path stays 429; broker path uses JetStream/Redpanda publish limits / consumer lag (map to 429 or 503 at the edge if API still accepts) |
| Registry | `dms_streams` ops table + steward CRUD | Unchanged; broker subjects map 1:1 to `stream_id` |
| Audit | `stream.created`, `stream.flushed` {count} | Keep flushed ledger on bronze write; optional `stream.broker_*` later — do not drop flushed |
| RBAC / caps | Steward write, viewer list/preview; max 1000 events/request | Unchanged on HTTP; broker auth separate (NATS creds / Redpanda ACLs) |

### Correct architecture (do not fork writers)

```
Producers ──► POST /dms/streams/.../events ──► buffer.append_events ──► bronze.stream_*
                 │                                    ▲
                 │ (optional S2)                      │
                 └──► NATS JetStream ── consumer ─────┘
                          (V2: Redpanda topic) ────────┘
```

**Rule:** one bronze writer module (`buffer._write` / `_write_batch`). Broker consumers call into that path (or a thin wrapper that reuses normalization + `_event_id`). Never invent parallel `bronze.kafka_*` tables for the same logical stream.

---

## Windows install story

### NATS JetStream (V1)

1. Download `nats-server-*-windows-amd64.zip` from [nats-io/nats-server releases](https://github.com/nats-io/nats-server/releases) (or `scoop install main/nats-server` / `choco install nats-server`).
2. Run with JetStream enabled, e.g.  
   `nats-server.exe -js -sd D:\Cortex\data\nats`  
   (store under DMS data root; document path in ops notes).
3. Python: pin `nats-py` in an optional `[streams]` / S2 extra when implementation starts.
4. Optional: Windows Service wrapper later; V1 can be process supervised by `run_demo.ps1` / existing orchestrator loop — no Docker required.

**Why this wins on Windows-first DMS:** matches the product’s “sovereign on-box” story; no WSL tax for the default install; single binary aligns with AirGPT/CortexOS Windows packaging.

### Redpanda (V2 only)

1. Install Docker Desktop with WSL2 backend (or run Linux host).
2. `docker run` Redpanda image with ports 9092 / admin; persist volumes on a shared drive.
3. `rpk` on Windows is limited; full CLI expects WSL/Linux. Quix Streams for Kafka-API interop per BUILD_PLAN.
4. Accept extra failure surface: Docker daemon down, WSL networking quirks, volume permissions.

**Do not** document Redpanda as the V1 “double-click Windows” path.

---

## Failure modes / backpressure

| Mode | S0 behavior | With JetStream V1 | With Redpanda V2 |
|---|---|---|---|
| Producer faster than bronze | Buffer fills → **429** | Persist in JetStream; consumer slows; API can still 429 if edge buffer used, or reject publish when stream max bytes/msgs hit | Same idea via topic retention / producer blocking |
| API process kill mid-batch | Lose at most one unflushed in-memory batch (documented S0) | Messages still in JetStream if published before crash; consumer resumes → at-least-once into bronze | Same if produce acked |
| Duplicate redelivery | Dedup within batch only | Must keep `event_id` dedup; consider durable seen-set or INSERT-then-ack discipline | Same |
| Broker down | N/A | HTTP path should still work (S0 path independent); flag broker optional | If HTTP only forwards to Kafka and broker down → outage unless fallback to S0 buffer |
| Bronze / DuckLake write stall | `_WRITE_LOCK` serializes; producers hit hard cap | Consumer ack only **after** successful `_write`; pending acks = natural backpressure | Same |
| Stress context | ~379 ev/s webhook→buffer→bronze | Broker adds hop; V1 success = bronze lag + zero loss under redelivery, not “beat 379” | Kafka clients heavier; bench separately in B1 |

**Backpressure policy for S2:**

1. Keep HTTP **429** for edge buffer / hard cap (do not replace with silent drop).
2. JetStream: configure stream limits + consumer `max_ack_pending`; map publish failures to 429/503 at the API if dual-write is enabled.
3. Never claim exactly-once; stay on at-least-once + `event_id` dedup (BUILD_PLAN S0 anti-scope language).

---

## Links to truth-ground repo files

| Role | Path |
|---|---|
| Plan S0 + S2 | `docs/dms/BUILD_PLAN_V2_LAKEHOUSE.md` (§ FEATURE S0; § S2 — Broker tier) |
| Exec packet A2 | `docs/dms/CURSOR_EXEC_PACKET_2026-07-22.md` |
| Buffer / bronze writer / 429 | `packs/dms/streams/buffer.py` |
| Stream registry (ops DB) | `packs/dms/streams/registry.py` |
| Package docstring (S0 → S2) | `packs/dms/streams/__init__.py` |
| HTTP routes | `CortexOS/api/stream_routes.py` |
| Simulator | `scripts/stream_simulate.py` |
| Smoke | `tests/dms/test_s0_streams.py` |
| Stress note ~379 ev/s | `CHANGELOG_DMS.md`, `STATUS.md` |
| Architecture diagram (NATS/Kafka → lake) | `docs/dms/BUILD_PLAN_V2_LAKEHOUSE.md` §1 |

Referenced but **not present in repo** at research time: `docs/research/findings/STREAMING_ORCH_2026.md` (cited from `packs/dms/streams/__init__.py`).

---

## Anti-scope

- Do **not** replace S0 HTTP intake with broker-only ingest in V1.
- Do **not** require Docker/WSL2 for default DMS Windows install.
- Do **not** add Kafka/Redpanda/Quix to the default dependency set in S2 V1.
- Do **not** change bronze schema or invent parallel lake tables for broker traffic.
- Do **not** claim exactly-once or EOS transactions across broker + DuckLake.
- Do **not** implement S1 agent consumers as part of the broker shortlist (consumers = DBOS workflows when S2 builds; agents already have S1).
- Do **not** rewrite buffer concurrency / stress path “for broker” without a B1 bench plan.
- Do **not** treat this findings doc as license to edit Python/JSX/tests in the research turn.

---

## Cursor implementation slice when S2 turn comes

Ordered, smallest shippable slice (wave 3):

1. **Ops flag** `DMS_STREAM_BROKER=off|nats` (default `off`) — S0 path unchanged when off.
2. **Pin + smoke:** optional extra with `nats-py`; script or `run_demo` helper that starts `nats-server -js -sd <data>/nats` on Windows.
3. **Subject map:** `dms.stream.<stream_id>` (or JetStream stream `DMS` with subjects `dms.stream.*`); registry `stream_id` remains source of truth.
4. **Dual-write (optional, steward):** after normalize in `append_events`, publish `{event_id, ts, payload}` to JetStream; bronze write remains authoritative for lake reads.
5. **Consumer worker:** drain JetStream → call existing `buffer` write path (or shared `_normalize` + `_write`); ack only after successful bronze insert; ledger `stream.flushed` unchanged.
6. **Tests:** unit with mock NATS or testcontainers-if-available; keep `tests/dms/test_s0_streams.py` green without broker; add `test_s2_nats_roundtrip` gated/skip if no binary.
7. **Docs:** Windows install one-pager + failure modes; point V2 Redpanda/Quix at client Kafka estates only.
8. **Defer:** Redpanda compose file, Quix, Kafka ACL UI, multi-node JetStream cluster, replacing in-memory buffer entirely.

**Acceptance sketch (align with BUILD_PLAN):** broker optional; same bronze columns; restart mid-stream loses no **acked** JetStream messages; HTTP still 429 under hard cap; S0 simulator works with broker off.

---

## Comparison one-liner

**NATS JetStream = Windows-native durability sidecar for S2 V1; Redpanda/WSL2 = Kafka-interop V2 when the customer’s estate demands it — both must land through the existing S0 bronze writer.**
