# GET HELP -- what we actually used

Official page: https://allthingsagentichackathon.devpost.com/resources
FAQ: https://allthingsagentichackathon.devpost.com/details/faqs

## Discord + Discussion

- Discord: ask peers; faster for "does Resume re-run tools?"
- Discussion forum: Devpost-monitored; use for credits / eligibility / submission form
- Do not wait on Discord to ship. The FAQ already answers: new project only, one track, proof of GCP in the video, private repo must add testing@devpost.com and cloudhackathons@google.com

## Credits (do this today -- reviews take up to 72 business hours, form closes 28 Aug 12:00 PT)

Form: https://forms.gle/riGhgDSHkHeMx8Ca6
Track name on the form: Fortified Enterprise Fleet
Description: Night Shift is an SME factory agent fleet that finishes a purchase order once, even after a crash.

Also: Google Cloud free trial is a separate bucket from the $150 hackathon credits.

Cost rules we follow (Resources tab): Gemini Flash first, Cloud Run min instances 0, max instances 2, record proof then turn services off, protect public URLs.

## Webinar 1 -- 11 Aug -- Three orchestration patterns of ADK 2

From a single agent to a multi-agent system, and knowing which pattern to reach for.

In this repo:

- Sequential: Scout extracts vendor/SKU/qty/week (`pipeline.extract_po`, ADK `scout`)
- Parallel fan-out: Stock + VendorMemory + Armor at once (`pipeline.start` parallel dict, ADK tuple after START)
- Loop: Critic until pass (`pipeline.critic_until_pass`)

ADK 2.6 deprecates SequentialAgent/ParallelAgent/LoopAgent in favor of `Workflow` graphs. We use `Workflow`.

## Webinar 2 -- 13 Aug -- Long-running agent

Crash recovery, human approval, and the idempotency trap -- why a resumable agent might order two laptops.

ADK Resume docs say tools may run more than once on resume. Purchases must check for duplicates.

In this repo:

- Human approval: `POST /api/runs/{id}/approve` (placer identity required)
- Crash: `POST /api/runs/{id}/crash` writes intent, does not commit
- Resume: `POST /api/runs/{id}/resume` -- second `place()` returns `idempotent_replay`
- Test: `tests/test_idempotency.py::test_resume_does_not_order_twice`

## Webinar 3 -- 20 Aug -- Self-evolving agent

Watch it rewrite its own instructions and climb the score, then catch it gaming the metric.

In this repo: `night_shift/evolve.py`

- Clerk rejects aggressive tone -> scribe prompt is rewritten
- `claimed_done` without a placed PO -> gaming, score -2

## Webinar 4 -- 27 Aug -- Agent memory

Persistence is not memory. Climb session state, vector search, managed cloud memory.

In this repo: `night_shift/memory.py`

1. Session: current draft, on-hand qty (forgotten when the run ends unless promoted)
2. Vector search: similar vendor/SKU notes
3. Long-term bank: vendor habits, clerk preferences, standing POs

On Cloud Run this maps to Firestore collections (`session`, `vectors`, `bank`). Local demo is in-process so pytest stays offline.

## GEAR vs GEAP

- GEAR = free learning program (Introduction to Agents). Optional skill-up, not the product.
- GEAP = Gemini Enterprise Agent Platform (Registry, Runtime, Memory Bank, Identity, Gateway, Model Armor, Observability). Fleet judging is built around these. We implement the same surfaces in-app and can point them at Vertex Agent Engine later without changing the demo story.

## Bonus points (do after the demo works)

- Public blog or video: must say it was created for this hackathon (+0.2)
- Social post with `#AllThingsAgenticHackathon` (+0.2)
- Extra Google AI models (Gemma / Veo / Lyria), +0.2 each, max +0.6
