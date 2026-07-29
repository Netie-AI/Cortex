# Cursor → Claude handoff — G2.3 open-set recognizer

**Date:** 2026-07-27  
**From:** Cursor (Seek UI now teaches ranking via Accept/Dismiss)  
**To:** Claude (judgment — open-set ingress)  
**Depends on:** G2.0–G2.2 **SHIPPED** (goal · seek · V shrinkage · F1 audit)

**STATUS: READY FOR CLAUDE BUILD — G2.3 (owner-recommended)**

---

## 0. Why this slice next

G2.2 taught ranking from **real user decisions**. G2.4 could auto-close that loop with inferred outcomes — weaker signal. The sharper gap: **`/fire` and other ingress still treat every payload as if the engine has seen its shape before.** G2.3 asks that question first.

---

## 1. Do not touch

| Path | Why |
|------|-----|
| `dag_runner.py` / `hooks.py` / `demo/dms-ui/**` | Parallel dirty |
| `BASELINE_CONSTRAINTS` / auto-approve | Ethical floor |
| Seek UI / AirGPT proxies | Cursor lane |

Additive only in `app.py` if you add routes.

---

## 2. Build G2.3 only

### A) `CortexOS/execution/osr.py`

Classify incoming work:

| Band | Signal | Route |
|------|--------|--------|
| **known** | family cosine ≥ τ + prior wins | stored scoreboard winner / preset |
| **near** | mid band | top-3 race (existing `race_router`) |
| **open** | below τ or new schema | gen-cFSM generate (horizon escalate 3→5→7) |

Emit:

```text
{ band, family_id?, novelty_score, proposed_horizon, assumptions[] }
```

Assumptions in **words** (same law as routine drafts / seek).

### B) Wire ingress

1. `POST /api/routines/{id}/fire` — run OSR on `external_text` **after** untrusted-payload wrap (wrap stays mandatory; OSR must not see raw as trusted).
2. Optional thin `POST /api/engine/osr` for classify-only (UI/debug).
3. Open band may call gen-cFSM; known must **not** silently invent a family.

### C) Tests (DB_PATH only, no chdir)

1. Each band routes correctly.  
2. Unknown-shaped payload never becomes `known`.  
3. Untrusted text never reaches a tool unwrapped.  
4. Silence litmus (seek) still green — OSR must not starve seeker / tick.  
5. Assumptions present on OSR responses.

### Out of scope

G2.4 telemetry compress · G2.5 pattern-armed · G2.6 update/OAuth · weakening autonomy.

---

## 3. Verify

```powershell
cd D:\Cortex
$env:DMS_AUTH_DISABLED="1"; $env:PACK="dms"
python -m pytest tests/dms/test_osr.py tests/dms/test_engine_seek.py tests/dms/test_action_value.py -q
python -m scripts.secrets_scan
```

Live optional: fire an unfamiliar string at a routine → expect `band=open` + generated IR, not a forced known family.

---

## 4. Hand-back checklist

- [ ] `osr.py` + band routing  
- [ ] `/fire` through OSR with wrap intact  
- [ ] Tests for known/near/open + no silent known  
- [ ] Silence litmus still green  
- [ ] STATUS.md G2.3 block + refresh `NEXT_LANES.md`  
- [ ] Recommend next: G2.4 telemetry **or** G2.5 pattern-armed  

---

## Cursor already did (G2.2 UI)

Seek page now: `value_why` sentence, “learned from past outcomes” chip, Accept → `/outcome` + routine draft preview, Dismiss → `/outcome`, seek history, `audit.ok=false` → **Not audited**.

---

## Bottom line

Make novelty honest before execution. Known reuses; open generates under gen-cFSM; never pretend a stranger is a friend.
