# CURSOR GOVERNANCE — Vision-DMS Build ("Skill Cortex")
**How to govern Cursor for the vision warehouse capability, with Claude as a milestone gate.**

This sits ON TOP of the seven-feature governed loop (F1 ledger → F2 chat → F3 classify → F4 suggest+learn → F5 compliance gate → F6 skill capture → F7 security). The V-series below adds the warehouse vision capability. It writes to the same ledger and runs through the same compliance gate — do not build a parallel data spine.

---

## 0. Two meanings of "skill" — don't mix them up

- **Cortex skill** (your platform): a captured unit of how work is done — a reusable card (intent → action template + stats). The vision pipeline produces new Cortex skill *types*: `intake_item`, `dimension_item`, `slot_item`, `record_movement`. These are stored in `dms_skills` (F6) and feed F4 suggestions.
- **Cursor skill** (your tooling): a reusable capability definition you give the Cursor agent so a subagent can invoke it consistently. Below, each V-feature ships as a Cursor skill the subagent implements and tests.

When this doc says "skill," it means the Cursor one unless it says "Cortex skill."

---

## 1. Vision-specific `.cursorrules` additions

Append these to `cortex-dms.cursorrules` (the base rules from the prior build still apply):

```
## Vision module rules (packs/dms/vision/)
- Model placeholders only: VISION_MODEL (detection/understanding), DEPTH_SOURCE
  (lidar|reference_marker|photogrammetry), OCR_MODEL. Never hardcode a provider/model name.
- A GENERATION model (image gen/edit, e.g. "Nano Banana"-class) is NEVER used for measurement,
  detection, or recording. Generation != measurement. If a task needs a dimension or a count,
  use a detection/understanding model + a DEPTH_SOURCE. Flag any generation-model use in a
  measurement path as an error.
- Images may contain PII (faces, plates, documents). On ingest: strip EXIF GPS, and run the
  F7 redaction/blur path BEFORE any image leaves the box or enters a prompt. Images stay on-box.
- NO auto-commit below confidence. A predicted dimension, location, or movement is a SUGGESTION
  until either confidence >= threshold AND it passes the F5 compliance gate, or a human confirms.
  Wrong auto-recorded inventory is silent corruption — treat like the ledger: never write
  unverified data as fact.
- Vision inference runs local/on-box where possible (sovereignty). Heavy models behind a local
  service, not a BIG_API hot loop.
- Every vision-derived fact (dimension, slot decision, movement) writes to the F1 ledger and,
  on success, becomes a Cortex skill via F6.
```

---

## 2. Skill + subagent governance

- **One subagent per V-feature. Sequential. Gated.** V1 needs V0. V2 needs V0+V1. V3 needs all.
- Each subagent: plan in planning mode → STOP for your review → implement smallest diff → write smoke tests → run feature tests + full suite green → append to `CHANGELOG_DMS.md`.
- **Confidence + compliance are non-negotiable gates inside every V-feature.** No vision output is written as fact without clearing them.
- **Claude gate checkpoints** (you said you'll return to me at milestones — here's exactly when and with what): see §5. Stop at each ⛔ and bring me the listed artifacts before proceeding.

---

## 3. Staged feature prompts

### V0 — Location model + labels + photo-on-intake + scan-on-move (SHIP THIS FIRST)

```
CONTEXT
Cortex v2, packs/dms/. The F1–F7 loop exists (ledger, chat, classify, suggest, gate, skill
capture, security). Add the warehouse operational spine: a structured location tree, QR/barcode
labels, a photo attached to each item on intake, and movement recorded by scanning. NO vision
inference yet — this is the boring version that already beats Excel.

GOAL
A warehouse can be modeled as zones/racks/bins; items have a location; each intake captures a
photo; moving an item is a QR scan that updates location and records the movement. Everything
audited (F1) and access-controlled (F7).

BUILD EXACTLY THIS
1. Tables (migration):
   - dms_locations(id uuid pk, parent_id uuid null, kind text check in('zone','rack','bin'),
       code text unique, qr_token text unique, capacity_volume numeric null)
   - dms_items(id uuid pk, sku text, label text, current_location_id uuid fk, photo_uri text null,
       dims jsonb null, created_at)
   - dms_movements(id uuid pk, item_id uuid fk, from_location_id uuid null, to_location_id uuid,
       actor text, method text check in('scan','manual'), created_at)
2. packs/dms/vision/locations.py: build/list the location tree; generate qr_token per bin;
   render printable QR labels (a simple endpoint returning QR images/PDF for the FDE to print).
3. packs/dms/vision/intake.py:
   - POST /dms/items/intake  body{ sku, label, location_code, photo(base64) }
     -> store item, save photo on-box (strip EXIF GPS via F7), set location, ledger 'item.intake'.
4. packs/dms/vision/movement.py:
   - POST /dms/movements/scan  body{ item_qr_or_id, to_location_qr } -> update current_location,
     insert movement, ledger 'item.moved'.
5. Frontend: a Warehouse view — location tree, item list per bin, an Intake form (with photo
   capture), and a Scan-to-move flow. Reuse house style (mono for data, no radius).

ANTI-SCOPE — DO NOT:
- Run ANY vision model. No dimensioning, no detection. Photos are stored, not analyzed.
- Build slotting prediction (V2).
- Touch F1/F5/F7 internals beyond calling them.
- Break existing tests.

ACCEPTANCE
- A location tree can be created; each bin gets a unique qr_token and a printable label.
- Intake stores an item with photo (EXIF GPS stripped) at a location + ledger entry.
- A scan-move updates location and records a movement + ledger entry.
- All new tables enforce RLS (F7).

SMOKE TEST (tests/dms/test_v0_warehouse.py)
- test_location_tree_and_qr
- test_intake_stores_item_photo_and_ledger
- test_exif_gps_stripped
- test_scan_move_updates_and_records
- test_rls_on_warehouse_tables
Feature test + full suite green. ON DONE: update CHANGELOG_DMS.md.
```

⛔ **CLAUDE GATE 1** — bring me V0 before starting V1 (see §5).

---

### V1 — Vision-assisted dimensioning + free-space estimate

```
CONTEXT
Cortex v2, packs/dms/. V0 exists. Add dimension estimation for an intake item and free-space
accounting per bin. This is the first "magic" feature. Dimensions are SUGGESTIONS until gated.

GOAL
On intake, estimate item dimensions from the photo using a DEPTH_SOURCE (lidar OR a known-size
reference marker in frame), show them for human confirm, and once confirmed, update per-bin
occupied volume and report free space.

BUILD EXACTLY THIS
1. packs/dms/vision/dimension.py:
   - estimate_dims(photo, depth_source) -> { l,w,h, unit, confidence }
     - lidar path: use provided depth map. reference-marker path: detect marker of known size,
       derive scale, measure bounding box. Behind VISION_MODEL + DEPTH_SOURCE placeholders.
   - NEVER auto-commit: return as suggestion with confidence.
2. Wire into V0 intake: after photo, call estimate_dims; UI shows suggested dims with a confirm/
   edit step. On confirm -> store dims on dms_items, ledger 'item.dimensioned'. On a value that
   matters (e.g. oversize), route through F5 gate.
3. Free space: packs/dms/vision/space.py: per bin, occupied = sum(item volume); free =
   capacity_volume - occupied. Endpoint GET /dms/locations/{id}/space. Surface in Warehouse view.

ANTI-SCOPE — DO NOT:
- Use a generation model to "measure". Detection/understanding + depth only.
- Auto-write dimensions without human confirm or gate pass.
- Build slotting (V2) or vision movement (V3).
- Break existing tests.

ACCEPTANCE
- estimate_dims returns dims + confidence for lidar and reference-marker inputs.
- Suggested dims require confirm before they're stored as fact.
- Confirming updates per-bin occupied volume; free-space endpoint is correct.
- A generation-model code path in measurement fails a test by design.

SMOKE TEST (tests/dms/test_v1_dimension.py)
- test_estimate_returns_dims_and_confidence
- test_dims_require_confirm_before_fact
- test_free_space_accounting
- test_no_generation_model_in_measurement
Feature test + full suite green. ON DONE: update CHANGELOG_DMS.md.
```

⛔ **CLAUDE GATE 2** — bring me V1 before V2.

---

### V2 — Slotting prediction ("where to store best") — OUTLINE

Build only after V0+V1 are live and ideally a pilot is using them.
- `packs/dms/vision/slotting.py`: `suggest_slot(item) -> ranked bins` using velocity-based slotting (fast-movers near dispatch) + bin-packing fit (dims vs free space) + constraints (temp zone, hazmat, weight). Heuristic/OR, not an LLM.
- Output is a SUGGESTION shown to the human; chosen slot routes through F5 gate; decision becomes a Cortex skill (`slot_item`) via F6.
- Anti-scope: no auto-move of physical goods; suggestion + human + gate only.
- Acceptance: a fast-moving item near capacity-fit is ranked above a poor-fit distant bin; constraints are respected; decisions are logged.

⛔ **CLAUDE GATE 3** before V3.

---

### V3 — Vision movement capture + map reconstruction (FRONTIER) — OUTLINE

The "revolutionary" headline. Hardest. Build last, only after V0–V2 pay.
- Vision movement: detect item/event at dock or gate (VISION_MODEL) → propose a movement → human/confidence gate → record (still writes to the same dms_movements + ledger).
- Map reconstruction: photogrammetry/SLAM from multiple captures → 3D/zone map overlay on the location tree. Expensive; scope tightly; consider phone-based capture first.
- Anti-scope: never auto-record a movement as fact below confidence + gate; vision is an assist on the V0 scan spine, not a replacement for it on day one.
- Acceptance: defined per sub-task when you reach it — bring me the scope at Gate 3.

---

## 4. Sequencing (do not reorder)

```
F1–F7 governed loop (already built)
        │
        ▼
V0 location+labels+photo+scan  ──►  V1 dimensioning+free-space  ──►  V2 slotting  ──►  V3 vision movement+map
   (ship + pilot first)              (first "magic")                (optimization)     (frontier, last)
```

Ship V0, put one warehouse on it, then climb. Each rung is demoable on its own.

---

## 5. Claude gate checklist (what to bring me at each ⛔)

At each gate, paste me:
1. **What shipped** — the feature, files touched (from CHANGELOG_DMS.md).
2. **Test output** — feature smoke tests + full `pytest -q` result.
3. **The governance proof for that rung:**
   - V0 → RLS on warehouse tables passing; EXIF-GPS strip test passing; ledger entries for intake + move.
   - V1 → "dims require confirm before fact" passing; "no generation model in measurement" passing; free-space math checked.
   - V2 → slotting respects constraints; every slot decision logged + gated.
   - V3 → no movement written below confidence + gate; vision is assist-on-scan, not replacement.
4. **Anything you're unsure is right, lacking, or wrong** — so I can flag it before you build the next rung on a shaky one.

I'll verify the rung is solid, flag gaps, and hand you the next prompt. That keeps each layer standing on a verified one instead of compounding a hidden crack — same discipline as the audit ledger, applied to your build process.
