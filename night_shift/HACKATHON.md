# Devpost paste (Fortified Enterprise Fleet)

Do not use an AI-generic product name. This project is **Night Shift**.

## Tagline

The factory agent fleet that finishes the purchase order once -- even after the laptop dies.

## Text description (features, tech, data, learnings)

Night Shift is a multi-agent purchasing fleet for a small factory. The unlikely hero is Mei Ling, the clerk who goes home at 6pm with a WhatsApp pile from Ah Seng ("send 200 pcs M8 tonight, same as last week") and a fear that last week's crash already sent the PO twice.

It is not a chatbot. The operator pastes the messy inbox once. A Google ADK 2 Workflow then:

1. Sequential Scout extracts vendor, SKU, qty, week with Gemini 3.5 Flash.
2. Parallel fan-out checks stock, recalls vendor memory, and runs Model-Armor-style injection/PII/tool-poison scans.
3. Critic loops until the draft is complete.
4. A human must approve. Only the Placer identity may call place-order (Agent Identity + Gateway).
5. Place-order is idempotent. ADK Resume can re-run a tool after a crash; we key the PO on (vendor, sku, qty, week) so the second run returns `idempotent_replay` and `placed_count` stays 1. That is the official "two laptops" trap, built as the demo rather than a footnote.

Technologies: Gemini 3.5 Flash (Gemini API / Vertex AI), Google ADK 2 Workflow + App ResumabilityConfig, Cloud Run (scale to zero), optional Firestore for the Memory Bank. Agent Registry, Gateway, Armor, and a three-layer memory hierarchy (session, vector, long-term) are first-class HTTP surfaces for judges.

Data sources: synthetic shop-floor chat (no real customer PII). Vendor habits are seeded, then written by real runs.

Learnings: persistence is not memory; a crash dump does not know Ah Seng's last price. Resume without an idempotency key is how you double-order. A self-evolving prompt that marks "done" without a PO is gaming, not improvement. Cortex (our pre-existing governed engine) stays a disclosed optional backend -- this submission is new work from the contest window and does not import CortexOS.

## Built with

Google ADK 2, Gemini 3.5 Flash, Cloud Run, FastAPI, (optional) Firestore, Python 3.12

## Pre-existing disclosure

Cortex engine at D:\Cortex (ontology, OSR, routines, ledger) existed before 3 Aug 2026. Night Shift does not import it. Ideas from a local Cortex crew worktree (Scout/Critic roles) were re-implemented here on ADK during the Submission Period.

## Video beat sheet (keep under 4:00, English, live)

0:00 Problem: Mei Ling, WhatsApp pile, last crash maybe double-ordered M8.
0:25 Architecture: ADK Workflow, Gemini 3.5, Cloud Run, Registry/Gateway/Armor/Memory.
0:50 Live UI: paste inbox, sequential + parallel + critic events on screen.
1:20 Approve. Crash before commit. Resume. Ledger shows one PO, status idempotent_replay.
2:10 Google Cloud Console: Cloud Run service `night-shift`, `*.run.app` in the address bar, Vertex/Gemini logs.
2:50 Memory + evolve: vendor habit retrieved; gaming detector on a fake "done".
3:20 What we learned. End.

Narrate. Do not play music over a silent screencast.

## After the demo works

1. Public GitHub repo (or private + testing@devpost.com and cloudhackathons@google.com)
2. This README spin-up + architecture diagram screenshot
3. YouTube/Vimeo public video
4. Optional: blog + LinkedIn `#AllThingsAgenticHackathon` + Gemma classifier node
