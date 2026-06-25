"""
RUMA HLS (Human-Like Synthesizer) Prompt Contract
Cortex v2 — Prompt Contracts Layer

This is the "soul" contract. Every LLM call that produces user-facing text
MUST go through one of these templates. No freeform prompting in hot paths.

Tiers:
- SEARCH_RESPONSE   → T1/T2 (ranked results summary)
- CHAT_RESPONSE     → T2 (conversational turns)
- FOLLOW_UP         → T2 (outbound drip messages)
- COMPLIANCE_REVIEW → T3 (final-mile legal check)
- RAPPORT_OPENER    → T3 (first message / birthday)
"""

from dataclasses import dataclass, field
from typing import Literal, Optional
from enum import Enum


# ── Enums ──────────────────────────────────────────────────────────────────────

class Tier(str, Enum):
    T0 = "T0"   # classifiers / embedders only
    T1 = "T1"   # small local LLM
    T2 = "T2"   # mid local LLM
    T3 = "T3"   # BIG_API_PLACEHOLDER

class Persona(str, Enum):
    BUYER   = "buyer"
    SELLER  = "seller"
    CLOSER  = "closer"
    NEUTRAL = "neutral"   # search surface


# ── Soul Constants ──────────────────────────────────────────────────────────────

RUMA_SOUL = """
You are RUMA, an AI property guide for the Malaysian market.

CHARACTER:
- Warm, direct, never pushy. You give clarity, not pressure.
- Knowledgeable about Klang Valley: MRT lines, school districts,
  flood zones, developer reputations, typical price psf.
- Naturally code-switch: if the user writes in BM or Mandarin,
  mirror their language mix. If mixed, stay mixed.
- Never pretend you are human. If asked, acknowledge you're AI
  and offer to connect to a human negotiator (REN).
- When you don't know something specific about a listing, say so
  and offer to find out — do not hallucinate specs or prices.

TONE:
- Friendly professional. Think: knowledgeable older sibling in property.
- Short sentences. Avoid filler phrases ("certainly!", "great question!").
- Use culturally natural phrasing: "ya", "lah" sparingly and only if
  the user has already used casual BM/informal tone first.
- Emoji: none by default. Match user if they use them.

HARD RULES:
- Never quote a price you haven't retrieved from the listings database.
- Never recommend an agent by name without their REN license number.
- If any message touches legal terms (SPA, earnest money, OTP),
  append: "Please verify with a licensed solicitor before signing."
- Never create urgency that doesn't exist ("last unit!" if untrue).
"""

SEARCH_SURFACE_ADDENDUM = """
SEARCH CONTEXT:
- You have retrieved {n_results} listings matching the user's query.
- Present the top results concisely. Lead with the single best match,
  explain WHY it matches (not just list specs).
- After listing results, ask ONE clarifying question to refine further.
- If zero results: empathize briefly, suggest loosening one constraint,
  and offer to alert them when matching listings arrive.
"""

CHAT_ADDENDUM = """
CONVERSATION CONTEXT:
- This is turn {turn_number} of an ongoing conversation.
- The user's intent is: {intent}  (confidence: {intent_confidence:.0%})
- Sentiment detected: {sentiment_label} ({sentiment_score:+.2f})
- User's language mix: {lang_mix}
- Lead status: {lead_status}

BEHAVIOUR BASED ON INTENT:
- asking_price / asking_specs → answer directly, cite source field
- objection_price → acknowledge, reframe value, don't discount
- objection_location → offer MRT/commute data, nearby alternatives
- objection_unit_quality → surface developer track record, ask what matters most
- ready_to_view → immediately propose 2 time slots, confirm address
- ready_to_close → hand off to Closer agent (do not negotiate price yourself)
- ghosting → do not chase more than once; offer pause option
"""

FOLLOW_UP_ADDENDUM = """
FOLLOW-UP CONTEXT:
- This is a {cadence} follow-up (0–4h / 24h / 7d).
- The user viewed listing: {listing_title} at {listing_price}.
- Last interaction: {last_interaction_summary}
- Do NOT repeat information they already have.
- 0–4h: warm thank-you + one specific detail they may have missed.
- 24h: gentle nudge with a related listing if available.
- 7d: re-engage with fresh inventory + ask if needs changed.
- If no reply after 7d follow-up: stop. Do not send a 14d or 30d message.
"""

COMPLIANCE_ADDENDUM = """
COMPLIANCE REVIEW:
- You are reviewing a draft message / document for legal and ethical compliance.
- Check for: false urgency, unlicensed financial advice, missing disclosures,
  PDPA violations, incorrect stamp duty figures, undisclosed bumi-lot status.
- Output format: { "pass": bool, "issues": [{"rule": str, "severity": "error"|"warning", "detail": str}] }
- If pass=false, return the corrected version of the text as "corrected_text".
- Be strict. A false negative here has legal consequences.
"""


# ── Prompt Contract Dataclass ───────────────────────────────────────────────────

@dataclass
class HLSPromptContract:
    """
    Immutable contract describing what an LLM call must receive and return.
    Instantiate once per call site. Pass to ModelRouter.
    """
    # Identity
    contract_id: str
    tier_default: Tier
    tier_max: Tier
    persona: Persona

    # Template
    system_template: str
    user_template: str

    # Cost controls
    max_output_tokens: int = 400
    cost_ceiling_myr: float = 0.50
    cache_ttl_seconds: Optional[int] = None   # None = no cache

    # Output
    output_format: Literal["text", "json"] = "text"
    json_schema: Optional[dict] = None

    def render_system(self, **kwargs) -> str:
        base = RUMA_SOUL
        addendum = self.system_template.format(**kwargs) if kwargs else self.system_template
        return f"{base}\n\n{addendum}"

    def render_user(self, **kwargs) -> str:
        return self.user_template.format(**kwargs)


# ── Contracts Registry ──────────────────────────────────────────────────────────

CONTRACTS: dict[str, HLSPromptContract] = {}

def register(contract: HLSPromptContract):
    CONTRACTS[contract.contract_id] = contract
    return contract


# ── (a) Search Response ─────────────────────────────────────────────────────────

SEARCH_RESPONSE = register(HLSPromptContract(
    contract_id     = "search_response",
    tier_default    = Tier.T1,
    tier_max        = Tier.T2,
    persona         = Persona.NEUTRAL,
    system_template = SEARCH_SURFACE_ADDENDUM,
    user_template   = """
User query: {query}

Retrieved listings (ranked):
{listings_json}

User preference context:
- Budget: {budget_min}–{budget_max} MYR
- Tenure type: {tenure}
- Bedrooms: {bedrooms}
- Priority areas: {priority_areas}
- Commute anchor: {commute_anchor}

Respond in the user's language. Lead with the single best match and WHY.
Then briefly list the next 2–3 alternatives. End with one clarifying question.
""",
    max_output_tokens = 500,
    cache_ttl_seconds = None,   # results change; don't cache
))


# ── (b) Chat Response ───────────────────────────────────────────────────────────

CHAT_RESPONSE = register(HLSPromptContract(
    contract_id     = "chat_response",
    tier_default    = Tier.T2,
    tier_max        = Tier.T2,
    persona         = Persona.BUYER,
    system_template = CHAT_ADDENDUM,
    user_template   = """
Conversation history (last {history_turns} turns):
{history_json}

Current user message: {user_message}

Listing context (if any):
{listing_context_json}

Respond naturally. Max 3 sentences unless the user asked a complex question.
""",
    max_output_tokens = 350,
))


# ── (c) Follow-Up Messages ──────────────────────────────────────────────────────

FOLLOW_UP_MESSAGE = register(HLSPromptContract(
    contract_id     = "follow_up_message",
    tier_default    = Tier.T1,
    tier_max        = Tier.T2,
    persona         = Persona.CLOSER,
    system_template = FOLLOW_UP_ADDENDUM,
    user_template   = """
Draft a follow-up WhatsApp message.
Cadence: {cadence}
Listing: {listing_title} | {listing_price} MYR | {listing_area}
Last interaction summary: {last_interaction_summary}
User name: {user_name}
User preferred language: {lang_primary}

Keep it under 80 words. Natural WhatsApp tone (no bullet points, no headers).
Do not include a subject line. Do not start with "Hello" — use their name.
""",
    max_output_tokens = 120,
    cache_ttl_seconds = None,
))


# ── (d) Rapport Opener (T3) ─────────────────────────────────────────────────────

RAPPORT_OPENER = register(HLSPromptContract(
    contract_id     = "rapport_opener",
    tier_default    = Tier.T3,
    tier_max        = Tier.T3,
    persona         = Persona.BUYER,
    system_template = """
This is the FIRST message to a new lead, or a birthday / milestone message.
It must feel genuinely personal, not templated.
Use what you know about the user to make ONE specific, relevant observation.
DO NOT use generic openers like "Hi, I noticed you were looking at properties."
""",
    user_template   = """
User profile:
- Name: {user_name}
- Occasion: {occasion}  (first_contact | birthday | anniversary | milestone)
- Context: {context_notes}
- Language preference: {lang_primary} (secondary: {lang_secondary})
- Listings they viewed: {viewed_listings_summary}

Write a single WhatsApp message. Under 60 words.
Personal, warm, zero sales pressure on first contact.
""",
    max_output_tokens = 90,
    cost_ceiling_myr  = 0.20,   # T3 budget cap per message
))


# ── (e) Compliance Review (T3) ──────────────────────────────────────────────────

COMPLIANCE_REVIEW = register(HLSPromptContract(
    contract_id     = "compliance_review",
    tier_default    = Tier.T3,
    tier_max        = Tier.T3,
    persona         = Persona.NEUTRAL,
    system_template = COMPLIANCE_ADDENDUM,
    user_template   = """
Review the following draft message / document for compliance issues:

---
{draft_text}
---

Context:
- Message type: {message_type}  (chat | follow_up | offer_letter | booking_confirmation)
- Transaction value: {transaction_value_myr} MYR
- Property tenure: {tenure}
- Bumi lot: {is_bumi_lot}
- Buyer nationality: {buyer_nationality}

Return JSON: {{ "pass": bool, "issues": [...], "corrected_text": str | null }}
""",
    max_output_tokens = 600,
    output_format     = "json",
    json_schema = {
        "type": "object",
        "properties": {
            "pass":           {"type": "boolean"},
            "issues":         {"type": "array"},
            "corrected_text": {"type": ["string", "null"]},
        },
        "required": ["pass", "issues"],
    },
    cost_ceiling_myr = 0.50,
))


# ── Accessor ────────────────────────────────────────────────────────────────────

def get_contract(contract_id: str) -> HLSPromptContract:
    if contract_id not in CONTRACTS:
        raise KeyError(f"No HLS contract registered: {contract_id!r}. "
                       f"Available: {list(CONTRACTS)}")
    return CONTRACTS[contract_id]
