# Respond.io competitive analysis — DMS Brain positioning
**Research date:** 2026-06-26 | **Purpose:** Beat respond.io on governed warehouse SME wedge

## What respond.io sells
- Omnichannel inbox (WhatsApp, email, IG, etc.)
- Broadcast campaigns + chatbot flows
- CRM-lite contact tags
- AI "magic" replies (generic, cloud-hosted)
- Per-seat SaaS pricing

## Gaps we exploit (Netie DMS Brain)
| Their weakness | Our strength |
|---|---|
| Generic AI, no warehouse context | Intent classify on logistics intents (F3) |
| Cloud data residency concerns | Sovereign on-box (NAS/VPS) |
| No audit trail for AI decisions | F1 hash-chained ledger on every message |
| Prompt injection / scam exposure | `prompt_harness` bank-grade gate |
| No compliance gate on actions | F5 deterministic rules (LLM extracts, rules decide) |
| One-size-fits-all tone | Psychological state routing (frustrated vs ready_to_buy) |

## Persona routing (Closer wedge — PARKING_LOT P4)
| State | Tackle | Agent tone |
|---|---|---|
| `frustrated` | Acknowledge + de-escalate, no upsell | Empathetic, short |
| `ready_to_buy` | Quote + next step CTA | Confident, specific |
| `suspicious` | Human handoff, no auto-reply | Minimal, flag steward |
| `casual` | Warm, brief | Friendly |
| `neutral` | Factual warehouse answer | Professional |

## Demo story (sell better than respond.io)
"We don't replace your WMS — we govern the conversations around it. Every inbound message is classified, scam-scored, PII-redacted, ledgered, and only then suggested to your team. Data never leaves your box."

## Implementation status
- F3 classify + psychological_state: **shipped**
- Warmed delayed draft (2–5s): **P4 deferred**
- WhatsApp BSP integration: **placeholder WHATSAPP_BSP**

## Next to beat them in pitch
1. Live demo: scam message → blocked + steward alert
2. Live demo: stock query → DuckDB answer + ledger proof
3. Case study: FDE setup fee vs respond.io seat creep
