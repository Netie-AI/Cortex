# RUMA Cortex v2 — Phase 3–5 Full Plan (Revised)
**Codename:** Cortex v2 / RUMA  
**Audience:** Coding agent  
**Date:** 2026-05-04  
**Phases completed:** 0, 1, 2 (fully shipped + smoke tested)  
**This document covers:** Phase 3 (week 4), Phase 4 (week 5), Phase 5 (week 6)

---

## Architecture Overview (revised)

```
WhatsApp Business (Meta Cloud API)
      ↓  webhook
RUMA Flows (Activepieces fork, MIT license)
      ↓  POST /chat
Cortex /chat endpoint
  ├─ nlp/sentiment_intent.py  (T0 classifier)
  ├─ nlp/archetype_classifier.py  (T1)
  ├─ personality/tone.py + timing.py
  ├─ personality/memory.py  (Redis / Qdrant / Postgres)
  └─ a2a/ agents  (buyer / seller / closer / compliance)
      ↓  reply
RUMA Flows → WhatsApp Send Message
      +
Cortex /chat  ←→  Website embed widget (same lead_id)
      +
/principal  dashboard (CRM, hot leads, override)
      +
/admin/spend  (cost ledger surfaced to agency owner)
```

**Critical license decision:**  
Do NOT use n8n. n8n uses the Sustainable Use License (fair-code) which explicitly prohibits embedding in a paid SaaS product sold to external users. RUMA is exactly that use case. Use **Activepieces** (MIT, Community Edition) instead. Fork it internally as `ruma_flows/`. Each Activepieces "piece" becomes callable from a new `flow_call` DAG node kind in Cortex.

---

## Environment + Conventions

```
repo root: Cortex for RUMA/Cortex/
venv: .\myenv\Scripts\Activate.ps1
python: .\myenv\Scripts\python.exe
pytest: .\myenv\Scripts\python.exe -m pytest <target> -q
uvicorn: .\myenv\Scripts\python.exe -m uvicorn netie.api.app:create_app --factory --host 0.0.0.0 --port 8000
activate BEFORE every shell command — do not assume PATH is set
```

**Coding constraints (carry forward from Phases 0–2):**
- No `from __future__ import annotations` at module level in any file that defines FastAPI route functions or Pydantic models used as route parameters — this triggers FastAPI to misread types as query params (known bug, hit twice)
- All Pydantic models used as FastAPI body params must be defined at **module level**, not inside `register_*` functions
- Import `Request` from `starlette.requests` at module level in any file that uses it in a route signature
- `BIG_API_PLACEHOLDER` string constant stays — resolved by `model_router.py` at deploy time, never hardcoded
- `'simple'` FTS dictionary in Postgres (not `'english'`) — prevents stemming Malaysian place names
- Reranker `asyncio.Semaphore(1)` lives on the **class**, not the function call
- `node_executions` table `ceiling_myr` column exists — use it for cap analysis queries
- Windows `pytest tests/` full run has an environment-level access-violation unrelated to new code — use targeted test runs

---

## Phase 3 — Sentiment + Personality + Memory
**Week 4 | Goal: every inbound message is classified, toned, and remembered**

---

### AI3.1 — Multilingual Sentiment + Intent Classifier
**File:** `netie/nlp/sentiment_intent.py`

#### What to build
Rule-bootstrap classifier (no ML weights yet — those come in Phase 5 eval). Fast, deterministic, <5ms on CPU. Runs on every inbound message before any agent is invoked.

#### Output contract
```python
@dataclass
class SentimentIntentResult:
    sentiment: float          # -1.0 to +1.0
    intent: str               # one of INTENT_ENUM values
    language_mix: dict        # {"ms": 0.6, "en": 0.4, "zh": 0.0}
    confidence: float         # 0.0–1.0 (rule bootstrap always returns 0.7)
    archetype_hint: str | None  # "first_time_buyer" | "investor" | "expat" | "upgrader" | "bumi_eligible" | None
```

#### INTENT_ENUM (exhaustive — do not add unlisted values without updating tests)
```python
INTENT_ENUM = {
    "ready_to_view",       # nak tengok, can I view, bila boleh tengok
    "asking_price",        # berapa harga, what's the price, harga berapa
    "asking_specs",        # berapa bilik, how many rooms, ada parking tak
    "objection_price",     # mahal, too expensive, harga boleh kurang, reduce price
    "objection_location",  # jauh, too far, no LRT
    "objection_quality",   # condition macam mana, old building, renovation needed
    "ready_to_close",      # ok I want it, nak beli, proceed, let's sign
    "ghosting",            # (detected by silence — not from text, skip in classifier)
    "off_topic",           # default fallback
}
```

#### Rule implementation
```python
# Keyword maps — BM / EN / ZH mixed
INTENT_KEYWORDS = {
    "ready_to_view": ["nak tengok", "can view", "bila boleh", "viewing", "show me", "lawatan"],
    "asking_price":  ["berapa harga", "what price", "how much", "harga berapa", "price"],
    "asking_specs":  ["berapa bilik", "rooms", "parking", "sqft", "kaki persegi", "furnished"],
    "objection_price": ["mahal", "too expensive", "boleh kurang", "reduce", "lower price", "nego"],
    "objection_location": ["jauh", "too far", "no lrt", "mrt", "traffic", "jam"],
    "objection_quality": ["condition", "renovation", "lama", "old", "rosak", "repair"],
    "ready_to_close": ["nak beli", "i want", "ok deal", "proceed", "sign", "book", "deposit"],
}

NEGATION_WORDS = ["tak", "tidak", "no", "not", "bukan", "jangan"]

def classify(text: str) -> SentimentIntentResult:
    # 1. Language mix: count BM/EN/ZH tokens
    # 2. Negation check: if negation precedes intent keyword → flip polarity
    # 3. Sentiment: negative keywords list + exclamation boost
    # 4. Intent: first keyword match wins; fallback = off_topic
    # 5. Archetype hint: check for MM2H, yield, sekolah, first house signals
    ...
```

#### Archetype hint detection (add to same file)
Detects buyer archetype from first 3 messages. Used by closer agent to load matching tone profile.

```python
ARCHETYPE_SIGNALS = {
    "first_time_buyer": ["first house", "first time", "rumah pertama", "FHOA", "SRP", "afford"],
    "investor":         ["rental yield", "ROI", "investment", "passive income", "tenant", "sewakan"],
    "expat":            ["MM2H", "foreigner", "international school", "expatriate", "PR"],
    "upgrader":         ["bigger", "upgrade", "school catchment", "family", "second property"],
    "bumi_eligible":    ["bumi lot", "bumi quota", "bumiputera", "Malay reserve"],
}
```

#### Constraints
- No external imports beyond stdlib + regex. Must work with no network.
- Must return in <5ms for 99th percentile on CPU.
- `confidence` is always `0.7` for rule bootstrap — do not fake higher.
- If multiple intents match, return the first match by INTENT_KEYWORDS order above.
- Never set `archetype_hint` from a single signal — require 2+ signals from the same archetype.

#### Tests: `tests/test_nlp/test_sentiment_intent.py`

```python
# Required test cases — all must pass:

# BM objection
assert classify("harga boleh kurang tak?").intent == "objection_price"
assert classify("harga boleh kurang tak?").sentiment < 0

# Negated objection (not objecting, asking about reducing on behalf)
assert classify("dia kata tak mahal").sentiment > -0.3

# Mixed language ready_to_view
assert classify("ok boleh tengok this weekend?").intent == "ready_to_view"

# EN ready_to_close
assert classify("yes I want to proceed with the unit").intent == "ready_to_close"

# Language mix: BM-heavy
result = classify("berapa harga untuk unit ni?")
assert result.language_mix["ms"] > result.language_mix["en"]

# Archetype: investor signals
result = classify("what's the rental yield? good for investment?")
assert result.archetype_hint == "investor"

# Off-topic fallback
assert classify("hello how are you").intent == "off_topic"

# Expat signal
result = classify("do you have MM2H eligible units near international school?")
assert result.archetype_hint == "expat"
```

---

### AI3.2 — Tone Profile + Timing Engine
**Files:** `netie/personality/tone.py`, `netie/personality/timing.py`

#### tone.py — what to build

```python
@dataclass
class ToneProfile:
    formality: float          # 0.0 (casual) to 1.0 (formal)
    warmth: float             # 0.0 to 1.0
    emoji_frequency: str      # "none" | "low" | "med" | "high"
    honorifics: list[str]     # ["pak", "kak", "bro", "sis"]
    language_mix: dict        # {"primary": "en", "weight_ms": 0.25, "weight_zh": 0.0}
    identity_disclosure: str  # always "required" — AI must disclose if asked
    signature: str            # "— {agent.name}, REN {agent.ren}"

# Archetype-specific profiles (load by archetype_hint)
ARCHETYPE_TONE_PROFILES: dict[str, ToneProfile] = {
    "first_time_buyer": ToneProfile(formality=0.3, warmth=0.9, emoji_frequency="low", ...),
    "investor":         ToneProfile(formality=0.6, warmth=0.5, emoji_frequency="none", ...),
    "expat":            ToneProfile(formality=0.7, warmth=0.6, emoji_frequency="none", ...),
    "upgrader":         ToneProfile(formality=0.4, warmth=0.8, emoji_frequency="low", ...),
    "bumi_eligible":    ToneProfile(formality=0.5, warmth=0.7, emoji_frequency="low", ...),
    "default":          ToneProfile(formality=0.4, warmth=0.8, emoji_frequency="low", ...),
}

def compose_system_prompt(agent_name: str, ren: str, profile: ToneProfile, memory_facts: dict) -> str:
    """
    Compose the full system prompt for an LLM agent call.
    Inject: tone params, anti-pattern list (NEVER/ALWAYS), memory facts, identity disclosure.
    """
```

**Mandatory anti-pattern block — always injected into closer agent system prompt:**

```
NEVER:
- Use urgency manipulation ("limited time", "others are viewing", "last unit")
- Quote rental yield without explicitly stating the assumption (e.g., "assuming 95% occupancy")
- Ask "what's your budget" before demonstrating you understand their need
- Send more than 1 unsolicited follow-up message if they have not responded
- Use the word "perfect" — no property is perfect
- Promise outcomes only the principal REN can promise (loan approval, price reductions >5%)
- Refer to the user by their full NRIC name — use first name or honorific only
- Guess or state bumi eligibility unless the user has confirmed it

ALWAYS:
- Acknowledge price objections before reframing ("I hear you — at that price point, here's what I can offer...")
- Mirror the user's language mix proportionally (if they write 60% BM, reply 60% BM)
- Disclose AI assistance immediately if asked directly ("Yes, I'm an AI assistant for [Agent Name]")
- Defer to the principal agent for any negotiation above ±5% of listed price
- Pause outbound messaging during: 10pm–8am MYT, Friday 12:30–2:30pm, 2 days before CNY/Raya/Deepavali
- Store rejected prices in memory — never re-quote a rejected price in a later session
```

**Rapport decay (implement in `compose_system_prompt`):**
- After 5 exchanges: `formality -= 0.1` (capped at 0.1 floor)
- After 10 exchanges: `formality -= 0.1` again
- `emoji_frequency` may drift one level up after 10 exchanges if user uses emoji

#### timing.py — what to build

```python
import zoneinfo
MYT = zoneinfo.ZoneInfo("Asia/Kuala_Lumpur")

@dataclass
class TimingDecision:
    allowed: bool
    reason: str        # "ok" | "quiet_hours" | "friday_prayer" | "festive_pause" | "cadence_limit"
    retry_after: datetime | None  # when to retry if blocked

def is_outbound_allowed(
    lead_id: str,
    now: datetime,           # must be timezone-aware MYT
    religion: str | None,    # from semantic memory, None if not declared
    last_outbound: datetime | None,
    last_inbound: datetime | None,
    follow_up_count: int,
) -> TimingDecision:
    """
    Check all timing gates in order. Return first block found.
    Gates (in order):
      1. Quiet hours: 22:00–08:00 MYT
      2. Friday prayer: 12:30–14:30 MYT if religion == "Islam"
      3. Festive pause: 2 days before + day of CNY/Hari Raya/Deepavali
      4. 24h WhatsApp window: if last_inbound > 24h ago, block non-template outbound
      5. Cadence limit: if follow_up_count >= 1 and no response received, block
      6. 30-day archive: if last_inbound > 30 days ago, block and flag for archive
    """

FESTIVE_DATES_2026 = {
    "cny_start": date(2026, 2, 17),
    "raya_start": date(2026, 3, 20),
    "deepavali": date(2026, 11, 1),
    # update yearly — store in config, not hardcoded
}

def follow_up_cadence(last_inbound: datetime, now: datetime) -> str:
    """
    Returns the appropriate follow-up type based on elapsed time.
    0–4h:  "thank_you"    (T2, warm message)
    4–24h: "gentle_nudge" (T1, templated)
    1–7d:  "new_listing"  (T2 + RAG, suggest matching listing)
    7–30d: "revival"      (T2, re-engage with context)
    >30d:  "archive"      (stop messaging, flag lead)
    """
```

**Anniversary cron (replaces birthday cron from original plan):**
- Query Postgres for leads where `first_viewing_date` is today's date (any year)
- Generate: "Happy 1 year in your new place — how's the neighborhood treating you?"
- Use T3 (Big API) — low volume, high quality bar
- Store `anniversary_sent_year` to prevent duplicate sends

#### Tests: `tests/test_personality/test_timing.py`

```python
# Quiet hours block
decision = is_outbound_allowed(..., now=datetime(2026,5,4,23,0, tzinfo=MYT), ...)
assert decision.allowed is False
assert decision.reason == "quiet_hours"

# Friday prayer block (Muslim lead)
decision = is_outbound_allowed(..., religion="Islam",
    now=datetime(2026,5,8,13,0, tzinfo=MYT), ...)  # Friday 1pm
assert decision.allowed is False
assert decision.reason == "friday_prayer"

# Non-Muslim lead, Friday prayer window → allowed
decision = is_outbound_allowed(..., religion=None,
    now=datetime(2026,5,8,13,0, tzinfo=MYT), ...)
assert decision.allowed is True

# 24h WhatsApp window expired
decision = is_outbound_allowed(...,
    last_inbound=datetime(2026,5,3,10,0, tzinfo=MYT),
    now=datetime(2026,5,4,11,0, tzinfo=MYT), ...)  # 25h later
assert decision.allowed is False
assert decision.reason == "cadence_limit"  # or whatsapp_24h_window

# Cadence limit: 1 follow-up already sent, no response
decision = is_outbound_allowed(..., follow_up_count=1,
    last_inbound=datetime(2026,5,1,10,0, tzinfo=MYT),
    now=datetime(2026,5,4,10,0, tzinfo=MYT), ...)
assert decision.allowed is False

# Normal business hours: allowed
decision = is_outbound_allowed(...,
    now=datetime(2026,5,4,10,0, tzinfo=MYT),
    religion=None, last_inbound=datetime(2026,5,4,9,0, tzinfo=MYT),
    follow_up_count=0, last_outbound=None)
assert decision.allowed is True
```

#### Tests: `tests/test_personality/test_tone.py`

```python
# Anti-pattern injection: closer prompt always contains NEVER block
prompt = compose_system_prompt("Ali", "REN123", ARCHETYPE_TONE_PROFILES["investor"], {})
assert "NEVER" in prompt
assert "urgency manipulation" in prompt
assert "perfect" in prompt

# Archetype-specific formality
investor_profile = ARCHETYPE_TONE_PROFILES["investor"]
assert investor_profile.formality > 0.5

first_time_profile = ARCHETYPE_TONE_PROFILES["first_time_buyer"]
assert first_time_profile.warmth > 0.8

# Identity disclosure always injected
assert "AI assistant" in prompt or "disclose" in prompt.lower()

# Rapport decay
from netie.personality.tone import apply_rapport_decay
decayed = apply_rapport_decay(ARCHETYPE_TONE_PROFILES["default"], exchange_count=6)
assert decayed.formality < ARCHETYPE_TONE_PROFILES["default"].formality
```

---

### AI3.3 — Three-Layer Memory
**File:** `netie/personality/memory.py`

#### Architecture
```
Working memory   → Redis (TTL 2h)     → current session, last 10 turns
Episodic memory  → Qdrant (TTL 30d)   → last 100 messages per lead, summarized weekly
Semantic memory  → Postgres (permanent) → structured facts about lead
```

#### Semantic memory schema (Postgres table: `lead_facts`)
```sql
CREATE TABLE IF NOT EXISTS lead_facts (
    lead_id         TEXT PRIMARY KEY,
    name            TEXT,
    preferred_name  TEXT,
    language_primary TEXT DEFAULT 'en',
    language_mix    JSONB,                    -- {"ms": 0.6, "en": 0.4}
    religion        TEXT,                     -- only if explicitly opt-in
    race            TEXT,                     -- only if explicitly opt-in (bumi logic)
    budget_min_myr  NUMERIC,
    budget_max_myr  NUMERIC,
    preferred_districts TEXT[],              -- ["PJ", "KJ", "Subang"]
    school_pref     TEXT,                    -- "national" | "chinese" | "international" | null
    transport_pref  TEXT,                    -- "lrt" | "car" | null
    family_size     INT,
    archetype       TEXT,
    rejected_prices JSONB DEFAULT '[]',      -- [{"price": 850000, "date": "2026-03-12", "listing_id": "l_456"}]
    loan_eligible   BOOLEAN,
    first_viewing_date DATE,
    moving_date     DATE,
    anniversary_sent_year INT,
    last_inbound_at TIMESTAMPTZ,
    last_outbound_at TIMESTAMPTZ,
    follow_up_count INT DEFAULT 0,
    status          TEXT DEFAULT 'active',   -- "active" | "negotiating" | "closed" | "archived"
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);
```

#### Memory class interface
```python
class LeadMemory:
    def __init__(self, db_engine, qdrant_client, redis_client): ...

    # Working memory
    async def get_session(self, lead_id: str) -> list[dict]: ...          # last 10 turns
    async def append_turn(self, lead_id: str, role: str, content: str): ...
    async def clear_session(self, lead_id: str): ...

    # Semantic memory
    async def get_facts(self, lead_id: str) -> dict: ...
    async def upsert_facts(self, lead_id: str, facts: dict): ...
    async def store_rejected_price(self, lead_id: str, price: float, listing_id: str): ...
    async def get_rejected_prices(self, lead_id: str) -> list[dict]: ...

    # Episodic memory
    async def store_message(self, lead_id: str, role: str, content: str, sentiment: float): ...
    async def retrieve_relevant(self, lead_id: str, query: str, top_k: int = 5) -> list[str]: ...

    # Prompt injection helper
    async def build_context_block(self, lead_id: str) -> str:
        """
        Returns a formatted string to inject at session start:
        - Lead facts (budget, district prefs, archetype, language mix)
        - Rejected prices (CRITICAL — never re-quote these)
        - Last 10 turns from working memory
        - Top 5 episodic snippets relevant to current session
        """
```

#### Negotiation memory constraint (critical)
When `store_rejected_price` is called, the next `build_context_block` call for that lead must include:
```
REJECTED PRICES — DO NOT RE-QUOTE:
- RM 850,000 (rejected 2026-03-12, listing l_456)
```
This string must appear verbatim in the system prompt injection. Test this explicitly.

#### Weekly summarization (cron job)
**File:** `netie/personality/summarizer.py`
- Runs weekly via APScheduler or a simple cron endpoint
- Fetches episodic messages from Qdrant older than 7 days per lead
- Calls T1 (small local LLM) to condense into 3–5 bullet facts
- Upserts condensed facts into `lead_facts` Postgres record
- Deletes old Qdrant points after successful upsert

#### Tests: `tests/test_personality/test_memory.py`
```python
# Rejected price injection
await memory.store_rejected_price("u_123", 850000.0, "l_456")
context = await memory.build_context_block("u_123")
assert "850,000" in context
assert "DO NOT RE-QUOTE" in context or "REJECTED" in context

# Working memory round-trip
await memory.append_turn("u_123", "user", "berapa harga?")
session = await memory.get_session("u_123")
assert len(session) == 1
assert session[0]["content"] == "berapa harga?"

# Fact upsert + retrieval
await memory.upsert_facts("u_123", {"archetype": "investor", "budget_max_myr": 900000})
facts = await memory.get_facts("u_123")
assert facts["archetype"] == "investor"
assert facts["budget_max_myr"] == 900000
```

---

## Phase 4 — A2A Agents + WhatsApp + RUMA Flows
**Week 5 | Goal: first real WhatsApp message sent and replied to by the agent**

---

### AI4.1 — `/chat` Endpoint
**File:** `netie/api/chat.py`

#### Request/response contract
```python
class ChatRequest(BaseModel):
    lead_id: str
    message: str
    channel: str = "whatsapp"    # "whatsapp" | "web" | "api"
    listing_id: str | None = None
    metadata: dict = Field(default_factory=dict)

class ChatResponse(BaseModel):
    lead_id: str
    reply: str
    agent: str                   # "buyer" | "seller" | "closer" | "compliance"
    intent: str
    sentiment: float
    archetype: str | None
    timing_allowed: bool
    blocked_reason: str | None   # populated if timing_allowed is False
    cost_myr: float
    run_id: str
```

#### Pipeline (in order, no exceptions)
```
1. validate request
2. timing check → is_outbound_allowed() → if blocked, return 200 with timing_allowed=False + blocked_reason
3. sentiment + intent → classify(message) → SentimentIntentResult
4. update working memory → append_turn(lead_id, "user", message)
5. load facts → get_facts(lead_id) → dict
6. archetype detection → update facts if new archetype_hint confidence
7. route to agent:
     if intent in {ready_to_close, objection_price, objection_location, objection_quality}
         and (archetype confirmed or follow_up_count > 2)
         → closer_agent
     elif listing_id is set
         → seller_agent
     elif intent in {asking_price, asking_specs, ready_to_view}
         → buyer_agent
     else
         → buyer_agent (default)
8. compose system prompt → compose_system_prompt(agent, profile, facts)
9. inject rejected prices + memory context → build_context_block(lead_id)
10. compliance pre-check → if message contains money/legal terms → compliance_agent validates outbound
11. LLM call via DAG runner → llm_judged node (default T2, max T3 on judgment escalation)
12. compliance post-check on reply → flag if reply contains NEVER patterns
13. store reply in working memory → append_turn(lead_id, "assistant", reply)
14. update lead_facts → upsert sentiment, last_outbound_at, follow_up_count
15. return ChatResponse
```

#### NEVER pattern post-check (add to compliance engine)
Before returning reply, run string checks:
```python
NEVER_PATTERNS = [
    r"limited time",
    r"others are viewing",
    r"last unit",
    r"\bperfect\b",
    r"guarantee.*loan",
    r"100%.*approved",
]
# If any match → log warning, escalate to T3 for rewrite, or return generic safe reply
```

#### Tests: `tests/test_api/test_chat_endpoint.py`
```python
# Basic routing: price objection → closer agent
resp = client.post("/chat", json={
    "lead_id": "u_test_1",
    "message": "harga boleh kurang tak?",
    "channel": "whatsapp"
})
assert resp.status_code == 200
data = resp.json()
assert data["intent"] == "objection_price"
assert data["agent"] in ("closer", "buyer")  # buyer fallback ok if archetype not set
assert data["timing_allowed"] is True
assert data["reply"] != ""

# Timing block: quiet hours
with freeze_time("2026-05-04 23:00:00+08:00"):
    resp = client.post("/chat", json={"lead_id": "u_test_2", "message": "hello", "channel": "whatsapp"})
    assert resp.json()["timing_allowed"] is False
    assert resp.json()["reply"] == ""

# Compliance: reply must not contain NEVER patterns
resp = client.post("/chat", json={
    "lead_id": "u_test_3",
    "message": "is this a good deal?",
    "channel": "whatsapp"
})
reply = resp.json()["reply"].lower()
assert "perfect" not in reply
assert "limited time" not in reply
assert "last unit" not in reply

# Cost tracked
assert resp.json()["cost_myr"] >= 0.0
assert resp.json()["run_id"] != ""
```

---

### AI4.2 — Four Agent Personas
**Files:** `netie/a2a/personas/buyer.py`, `seller.py`, `closer.py`, `compliance.py`

#### Buyer agent
- Role: search, refine, schedule viewing
- Tier ceiling: T2
- Trigger: `asking_price`, `asking_specs`, `ready_to_view`, default fallback
- On `ready_to_view`: auto-call `/run` with `search.yaml` DAG to find top 3 matching listings, include in reply
- System prompt injection: include current RAG results if listing_id provided

#### Seller agent
- Role: answer questions about a specific listing
- Tier ceiling: T2
- Trigger: `listing_id` is set in ChatRequest
- Fetches listing data from Postgres/Qdrant, answers factually with evidence
- Never speculates beyond listing data — "I'll check with the owner and get back to you"

#### Closer agent
- Role: negotiation, price objection, rapport, move to booking
- Tier ceiling: T2 (T3 on judgment escalation for multi-objection turns)
- Trigger: `objection_price | objection_location | objection_quality | ready_to_close`
- **Archetype-aware**: loads matching ToneProfile, NEVER/ALWAYS block always injected
- **Negotiation memory**: rejected_prices injected into every prompt
- Judgment escalation to T3 when: 2+ objection types detected in same message, or `prior_tier_failures > 0`
- Auto-presenter trigger: if `ready_to_view` confidence > 0.8 → call `auto_presenter.generate(lead_id, listing_id)`

**Closer agent system prompt template:**
```
You are a property consultant assistant for {agent_name} (REN: {ren_number}).
You are speaking with {lead_name or "a potential buyer"} about Malaysian property.

Buyer profile:
- Archetype: {archetype}
- Budget: RM {budget_min} – RM {budget_max}
- Preferred districts: {districts}
- Language mix: {language_mix}
- Exchange count: {exchange_count}

{REJECTED_PRICES_BLOCK}

{MEMORY_CONTEXT_BLOCK}

{ANTI_PATTERN_BLOCK}  ← always inject the full NEVER/ALWAYS list

Tone: formality={formality}, warmth={warmth}, emoji={emoji_frequency}
Cultural context: Malaysia, urban Klang Valley. Match the user's language mix proportionally.
If asked directly whether you are AI: say yes immediately, then offer to connect the principal agent.

Current conversation:
{last_10_turns}
```

#### Compliance agent
- Role: pre-validate outbound, flag NEVER patterns, check 24h window
- Tier: deterministic rules + T1 for extraction + T3 for final check only on money/legal content
- Called automatically before every outbound that contains: price figures, loan terms, legal document references, percentage yields
- Returns: `{passed: bool, violations: list[str], rewrite_required: bool}`
- If `rewrite_required=True` → LLM rewrite at T2, re-check, then send

#### Tests: `tests/test_a2a/test_personas.py`
```python
# Closer: rejected price in prompt
facts = {"rejected_prices": [{"price": 850000, "date": "2026-03-12"}]}
prompt = build_closer_prompt("Ali", "REN123", facts, exchange_count=3)
assert "850,000" in prompt
assert "DO NOT RE-QUOTE" in prompt or "REJECTED" in prompt

# Compliance: flags yield without assumption
result = compliance_check("Rental yield is 6%")
assert result["passed"] is False
assert any("yield" in v.lower() for v in result["violations"])

# Compliance: clean message passes
result = compliance_check("The unit has 3 bedrooms and 2 bathrooms.")
assert result["passed"] is True

# Buyer: routes to search DAG
# (mock DAG runner — verify search.yaml DAG is invoked)
```

---

### AI4.3 — RUMA Flows (Activepieces fork) + WhatsApp Cloud API
**Directory:** `ruma_flows/`

#### Setup (coding agent: do this first before writing flows)
```bash
# Fork Activepieces (MIT license — safe to embed in RUMA SaaS)
git clone https://github.com/activepieces/activepieces ruma_flows/
cd ruma_flows
# strip enterprise packages (packages/ee) — not needed, avoid confusion
# rename branding: s/Activepieces/RUMA Flows/g in UI strings only — keep code package names

# docker-compose addition:
# ruma_flows:
#   image: activepieces/activepieces:latest
#   ports: ["8080:80"]
#   environment:
#     AP_FRONTEND_URL: http://localhost:8080
#     AP_POSTGRES_DATABASE: ruma_flows
#   depends_on: [postgres, redis]
```

#### New DAG node kind: `flow_call`
Add to `netie/fabrication/dsl_parser.py` NodeType enum:
```python
FLOW_CALL = "flow_call"
```
Add to `dag_runner.py`:
```python
async def execute_flow_call_node(node, context):
    """
    Calls a RUMA Flows (Activepieces) flow by its flow_id via internal API.
    Used to trigger WhatsApp sends, CRM updates, PDF generation from within a DAG.
    """
    flow_id = node.metadata.get("flow_id")
    payload = {k: context.get(k) for k in (node.inputs or [])}
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.RUMA_FLOWS_URL}/api/v1/webhooks/{flow_id}",
            json=payload,
            timeout=10.0
        )
    return NodeResult(node_id=node.id, output=resp.json(), tier="flow_call", cost_myr=0.0)
```

#### WhatsApp flows to build in RUMA Flows UI (document as YAML presets in `ruma_flows/presets/`)

**Flow 1: `inbound_message.yaml`** — receives WhatsApp message, routes to Cortex
```yaml
trigger: WhatsApp Trigger (on Messages)
steps:
  - Extract: lead_id from sender phone number
  - HTTP Request: POST http://cortex:8000/chat
      body: {lead_id, message: trigger.text, channel: "whatsapp"}
  - Branch: if response.timing_allowed == false → skip send
  - WhatsApp Send Message: to sender, body: response.reply
  - HTTP Request: POST http://cortex:8000/leads/{lead_id}/update
      body: {last_inbound_at: now}
```

**Flow 2: `new_lead.yaml`** — first message from unknown number
```yaml
trigger: WhatsApp Trigger (on Messages, sender not in lead_facts)
steps:
  - Create lead_facts record with lead_id = phone_number
  - Assign buyer agent
  - Send WhatsApp template: "welcome_new_lead" (pre-approved template)
  - Notify principal via WhatsApp: "New lead from {phone}"
```

**Flow 3: `viewing_followup.yaml`** — 4h after viewing logged
```yaml
trigger: Schedule (check every 15 min)
steps:
  - Query Postgres: leads where viewing_date < now - 4h AND thank_you_sent = false
  - For each lead:
      - Check timing: GET http://cortex:8000/timing/allowed?lead_id={id}
      - If allowed: send WhatsApp template "viewing_thank_you"
      - Mark thank_you_sent = true
```

**Flow 4: `hot_lead_alert.yaml`** — ready_to_close detected
```yaml
trigger: Webhook from /chat when intent=ready_to_close AND confidence > 0.8
steps:
  - WhatsApp send to principal: "🔥 Hot lead: {lead_name} is ready to close on {listing}"
  - Update lead status → "negotiating"
  - Log to CRM
```

**Flow 5: `dead_lead_revival.yaml`** — 7d no response
```yaml
trigger: Schedule (daily 10am MYT)
steps:
  - Query: leads where last_inbound_at < now - 7d AND status = "active"
  - For each:
      - GET /search with lead's preference_vec
      - Pick top 1 new listing not previously shown
      - POST /chat with "revival" intent hint
      - Send via WhatsApp
```

**Flow 6: `anniversary_cron.yaml`** — replaces birthday cron
```yaml
trigger: Schedule (daily 9am MYT)
steps:
  - Query: lead_facts where first_viewing_date month+day = today AND anniversary_sent_year != current_year
  - For each:
      - POST /chat with message: "[anniversary_trigger]" → T3 personalized reply
      - Send via WhatsApp
      - Update anniversary_sent_year
```

**Flow 7: `auto_presenter.yaml`** — triggered when ready_to_view confidence > 0.8
```yaml
trigger: Webhook from /chat
steps:
  - GET /listings/{listing_id} (photos, specs, floorplan)
  - GET /compliance/stamp_duty?price={price}
  - GET /listings/similar?listing_id={id}&top=3
  - Generate PDF via auto_presenter.generate()
  - WhatsApp Send Document: pdf file to lead
```

#### WhatsApp 24-hour window enforcement
Add compliance rule to `spa_v1.yaml` or new `whatsapp_v1.yaml`:
```yaml
rules:
  - id: whatsapp_24h_window
    when_doc_type: whatsapp_outbound
    description: "Non-template messages require last inbound within 24 hours"
    severity: error
    require_key: last_inbound_within_24h
```

In every outbound flow: call `GET /timing/allowed?lead_id={id}` before WhatsApp send. If blocked, queue message for when window reopens or use pre-approved template instead.

#### WhatsApp template directory
**Create:** `ruma_flows/templates/`

```
welcome_new_lead.{ms,en}.txt       — first contact
viewing_thank_you.{ms,en}.txt      — post-viewing follow-up
revival_new_listing.{ms,en}.txt    — 7d re-engagement
hot_lead_principal_alert.txt        — internal, to agent
appointment_confirmation.{ms,en}.txt
```

All templates must be submitted to Meta for approval before production. Document approval status in `ruma_flows/templates/STATUS.md`.

#### New endpoint: `GET /timing/allowed`
**File:** `netie/api/timing.py`
```python
# GET /timing/allowed?lead_id=u_123
# Returns: {"allowed": true, "reason": "ok"} or {"allowed": false, "reason": "quiet_hours"}
# Used by RUMA Flows before every outbound
```

#### Tests: `tests/test_flows/test_whatsapp_routing.py`
```python
# Timing gate in flow
resp = client.get("/timing/allowed?lead_id=u_123")
assert "allowed" in resp.json()
assert "reason" in resp.json()

# flow_call DAG node (mock RUMA Flows URL)
# Verify flow_call node sends correct payload to flows endpoint
dag = parse_dsl(json.dumps({
    "id": "test_flow_call",
    "entry_node_id": "call",
    "output_node_id": "out",
    "nodes": [
        {"id": "call", "kind": "flow_call",
         "metadata": {"flow_id": "viewing_followup"}, "inputs": ["lead_id"]},
        {"id": "out", "kind": "EMIT", "inputs": ["call"]}
    ]
}), intent_hash="test")
# mock httpx, verify POST to RUMA_FLOWS_URL with lead_id in body
```

---

### AI4.4 — Compliance Ruleset Expansion
**Files:** `netie/compliance/rules/tenancy_v1.yaml`, `rpgt_calc.yaml`, `whatsapp_v1.yaml`

#### tenancy_v1.yaml (new)
```yaml
doc_type: tenancy
rules:
  - id: tenancy_must_have_tenant_nric
    description: "Tenant NRIC required"
    severity: error
    require_key: tenant_nric

  - id: stamp_duty_tenancy
    description: "Tenancy stamp duty: RM1 per RM250 of annual rent above RM2,400"
    severity: error
    compare_numeric_ratio:
      # stamp = ((annual_rent - 2400) / 250) rounded up * 1
      numerator_key: stamp_duty_stated_myr
      denominator_expression: "ceil((annual_rent - 2400) / 250)"
      tolerance: 0.01

  - id: tenancy_duration_check
    description: "Tenancy duration must be between 1 month and 3 years"
    severity: warning
    require_key: duration_months
    # validate 1 <= duration_months <= 36
```

#### rpgt_calc.yaml (new)
```yaml
doc_type: rpgt
rules:
  - id: rpgt_disposal_within_3_years
    description: "RPGT 30% if disposed within 3 years (citizen)"
    severity: error
    when_matches:
      disposal_year_from_purchase: "<=3"
      citizen: true
    compare_numeric_ratio:
      numerator_key: rpgt_stated
      denominator_expression: "gain * 0.30"
      tolerance: 0.01

  - id: rpgt_citizen_after_5_years
    description: "RPGT 0% for citizens after 5 years (from 2022)"
    severity: warning
    when_matches:
      disposal_year_from_purchase: ">5"
      citizen: true
    require_key: rpgt_exemption_claimed
```

#### Tests: `tests/test_compliance/test_tenancy_rpgt.py`
```python
# Tenancy: missing NRIC
result = compliance.check({"doc_type": "tenancy", "extracted": {"stamp_duty_stated_myr": 100}})
assert any(v["id"] == "tenancy_must_have_tenant_nric" for v in result["violations"])

# RPGT: correct 30% within 3 years
result = compliance.check({"doc_type": "rpgt", "extracted": {
    "disposal_year_from_purchase": 2,
    "citizen": True,
    "gain": 100000,
    "rpgt_stated": 30000
}})
assert result["passed"] is True

# RPGT: wrong amount
result = compliance.check({"doc_type": "rpgt", "extracted": {
    "disposal_year_from_purchase": 2,
    "citizen": True,
    "gain": 100000,
    "rpgt_stated": 20000  # should be 30000
}})
assert result["passed"] is False
```

---

## Phase 5 — Website + Auto-Presenter + Eval + Principal Dashboard
**Week 6 | Goal: full product loop, agents love it, metrics visible**

---

### AI5.1 — Website Chat Widget + Routing
**File:** `netie/api/widget.py` (embed JS served from FastAPI)

#### Lead flow from listing page
```
User on listing page → clicks "Talk to Agent"
  ↓
Widget captures: listing_id (from URL), user message, optional phone
  ↓
POST /chat { lead_id: phone_or_session_id, listing_id, message, channel: "web" }
  ↓
If phone provided: send WhatsApp template "welcome_new_lead" → conversation moves to WhatsApp
If no phone: continue in widget (session-based lead_id)
  ↓
Widget shows: agent persona name + REN number + sentiment color indicator
```

#### Sentiment color indicator (CRM-facing)
```python
def sentiment_color(score: float) -> str:
    if score > 0.3:  return "green"
    if score > -0.2: return "amber"
    return "red"
# Red → push WebSocket event to /principal dashboard
```

#### Embeddable JS snippet
```html
<!-- RUMA Chat Widget -->
<script>
(function(w,d,l){
  w.RUMA = {listing_id: l, open: function(){}};
  var s = d.createElement('script');
  s.src = 'https://your-cortex-host/widget.js';
  s.async = true;
  d.head.appendChild(s);
})(window, document, '{{LISTING_ID}}');
</script>
```

#### Tests: `tests/test_api/test_widget.py`
```python
# Widget endpoint serves JS
resp = client.get("/widget.js")
assert resp.status_code == 200
assert "RUMA" in resp.text

# Web channel chat routes correctly
resp = client.post("/chat", json={
    "lead_id": "web_session_abc",
    "message": "tell me about this listing",
    "channel": "web",
    "listing_id": "l_789"
})
assert resp.status_code == 200
assert resp.json()["agent"] in ("seller", "buyer")
```

---

### AI5.2 — Auto-Presenter (PDF)
**File:** `netie/api/auto_presenter.py`

#### Trigger
Called from `/chat` pipeline when `intent == "ready_to_view"` and `confidence > 0.8`, or when closer agent explicitly decides to present.

#### Output
WhatsApp document (PDF) containing:
1. Listing cover: photo, address, price, sqft
2. Key specs: beds/baths/parking/tenure/furnishing
3. Stamp duty table (from compliance engine — already exists)
4. Loan repayment ladder: 10%, 20%, 30% down, at current OPR (deterministic T0)
5. Nearby amenities: LRT/MRT, schools, hospital (from RAG enrichment)
6. 3 similar listings at similar price (from search endpoint)
7. Agent contact: name, REN number, WhatsApp deep link

#### Implementation approach
Use `reportlab` or `weasyprint` (already pip-installable). Generate PDF bytes → save to S3-compatible storage → return pre-signed URL → RUMA Flows sends as WhatsApp document.

#### Loan repayment ladder (deterministic — T0, no LLM)
```python
OPR_RATE = 0.0325  # Bank Negara OPR — update in config
MARGIN_ABOVE_OPR = 0.015

def monthly_repayment(price: float, down_pct: float, tenure_years: int = 30) -> float:
    principal = price * (1 - down_pct)
    monthly_rate = (OPR_RATE + MARGIN_ABOVE_OPR) / 12
    n = tenure_years * 12
    return principal * (monthly_rate * (1 + monthly_rate)**n) / ((1 + monthly_rate)**n - 1)
```

#### Tests: `tests/test_api/test_auto_presenter.py`
```python
# Loan calculation
from netie.api.auto_presenter import monthly_repayment
repayment = monthly_repayment(500000, down_pct=0.10, tenure_years=30)
assert 2000 < repayment < 3000  # rough sanity for 500k at ~4.75%

# PDF generation returns bytes
from netie.api.auto_presenter import generate_pdf
pdf_bytes = await generate_pdf(listing_id="l_test", lead_id="u_test")
assert pdf_bytes[:4] == b"%PDF"
assert len(pdf_bytes) > 1000
```

---

### AI5.3 — Principal Override Dashboard (`/principal`)
**File:** `netie/api/principal.py`

#### Endpoints
```python
GET  /principal/hot_leads
     → leads where intent=ready_to_close AND confidence > 0.8, sorted by updated_at desc

GET  /principal/sentiment_alerts
     → leads where sentiment < -0.5 in last 2 hours (red flag)

GET  /principal/outbound_queue
     → messages pending compliance review (rewrite_required=True)

POST /principal/override/{lead_id}
     body: {"action": "take_over"}
     → sets lead status = "principal_active"
     → stops all agent outbound for this lead
     → sends WhatsApp to lead: "Hi, this is [Agent Name] directly — let me help you"

GET  /principal/spend
     → alias for /admin/spend, filtered to current principal's leads
```

#### WebSocket: live hot lead alerts
```python
# GET /ws/principal
# Pushes events when:
#   - intent=ready_to_close detected
#   - sentiment drops below -0.5
#   - compliance flags a message
# Event shape: {"event": "hot_lead", "lead_id": "u_123", "intent": "ready_to_close", "sentiment": 0.9}
```

#### Tests: `tests/test_api/test_principal.py`
```python
# Hot leads endpoint
resp = client.get("/principal/hot_leads")
assert resp.status_code == 200
assert isinstance(resp.json(), list)

# Override stops agent outbound
client.post("/principal/override/u_123", json={"action": "take_over"})
# Next /chat call for u_123 should return timing_allowed=False with reason="principal_active"
resp = client.post("/chat", json={"lead_id": "u_123", "message": "hello", "channel": "whatsapp"})
assert resp.json()["timing_allowed"] is False
assert "principal" in resp.json()["blocked_reason"].lower()
```

---

### AI5.4 — Eval Harness (self-feeding)
**File:** `netie/eval/harness.py`

#### Self-feeding corpus growth
Every conversation flagged by any of these conditions auto-enters eval queue:
- Sentiment drops > 0.5 in a single turn
- Principal override triggered
- Compliance flags a NEVER pattern violation
- Lead status changes to "archived" (conversation ended badly)
- User explicitly types "AI", "bot", "fake", "robot"

```python
async def flag_for_eval(lead_id: str, reason: str, conversation_snapshot: list[dict]):
    """
    Writes to eval_queue Postgres table.
    Human reviewer picks up from /eval/queue endpoint.
    After labeling: appended to eval/corpus/ as JSONL.
    """
```

#### Eval metrics (CI gate)
```python
EVAL_THRESHOLDS = {
    "sentiment_f1_macro": 0.78,
    "intent_f1_macro": 0.72,
    "response_quality_llm_judge": 3.5,   # out of 5, T3 judge
    "cost_per_conversation_myr": 0.50,
    "language_fidelity": 0.85,           # fraction where reply language mix matches input
    "never_pattern_rate": 0.00,          # zero tolerance — any NEVER pattern = fail
}

# CI gate: fail if any metric regresses > 5% absolute vs baseline
```

#### Tests: `tests/test_eval/test_harness.py`
```python
# Flag for eval
await flag_for_eval("u_123", "sentiment_crash", [{"role": "user", "content": "this is terrible"}])
queue = await get_eval_queue()
assert any(item["lead_id"] == "u_123" for item in queue)

# NEVER pattern rate = 0 on clean replies
clean_reply = "The unit has 3 bedrooms and great natural light."
assert count_never_pattern_violations(clean_reply) == 0

# NEVER pattern detected
bad_reply = "This is the perfect unit — limited time offer!"
assert count_never_pattern_violations(bad_reply) > 0
```

---

### AI5.5 — Spend Dashboard
**File:** `netie/api/admin.py`

```python
GET /admin/spend
    ?from=2026-05-01&to=2026-05-04&group_by=lead_id|tier|workflow

# Returns:
{
  "total_myr": 4.23,
  "by_tier": {"T1": 0.12, "T2": 3.80, "T3": 0.31},
  "by_lead": [{"lead_id": "u_123", "cost_myr": 1.20}, ...],
  "by_workflow": [{"run_id": "...", "cost_myr": 0.08, "node_count": 4}, ...],
  "avg_per_conversation_myr": 0.08,
  "conversations_above_ceiling": 0
}
```

Query from `node_executions` table (already exists with all needed columns).

---

## MCP Server Exposure (unfair advantage)
**File:** `netie/mcp/server.py`

Expose Cortex itself as an MCP server. This makes RUMA a platform, not just a product.

```python
# Tools to expose via MCP:
tools = [
    "search_listings",          # calls /search endpoint
    "check_compliance",         # calls /run with deterministic_rule DAG
    "get_lead_facts",           # calls memory.get_facts()
    "calculate_stamp_duty",     # deterministic T0
    "calculate_loan_repayment", # deterministic T0
    "get_spend_summary",        # calls /admin/spend
]
```

Property agents using Claude Desktop can then query their own CRM through MCP natively. Compliance rules become licensable MCP tools for other agencies.

Add `NETIE_MCP_ENABLED=true` env flag — off by default, opt-in per agency.

---

## Voice Note Support (Phase 4 extension, Malaysia-specific moat)
**File:** `netie/nlp/voice_transcriber.py`

Majority of Malaysian WhatsApp conversations include voice notes. Text-only agents miss these entirely.

```python
async def transcribe_voice_note(audio_bytes: bytes, hint_language: str = "ms") -> str:
    """
    Uses Whisper-large-v3 (already in model stack) to transcribe.
    Returns text transcript.
    hint_language: "ms" for Bahasa Malaysia primary, "en" for English primary
    """
```

RUMA Flows: add voice note handler to `inbound_message.yaml`:
```yaml
- Branch: if trigger.type == "audio"
    - Download audio from WhatsApp media URL
    - POST /transcribe {audio_bytes, hint_language: lead_facts.language_primary}
    - Set message = transcription result
    - Continue normal /chat pipeline
```

Add to `tests/test_nlp/test_voice.py`:
```python
# Transcription returns non-empty string for valid audio
# (use a short WAV fixture in tests/fixtures/)
result = await transcribe_voice_note(wav_bytes, hint_language="ms")
assert isinstance(result, str)
assert len(result) > 0
```

---

## Repository Structure (additions to existing tree)

```
netie/
├── nlp/
│   ├── sentiment_intent.py       ← AI3.1 NEW
│   ├── archetype_classifier.py   ← AI3.1 NEW (part of sentiment_intent.py)
│   ├── voice_transcriber.py      ← Phase 4 extension NEW
│   └── embedder_bge.py           ← exists
├── personality/
│   ├── tone.py                   ← AI3.2 NEW
│   ├── timing.py                 ← AI3.2 NEW
│   ├── memory.py                 ← AI3.3 NEW
│   └── summarizer.py             ← AI3.3 NEW (weekly cron)
├── a2a/
│   └── personas/
│       ├── buyer.py              ← AI4.2 NEW
│       ├── seller.py             ← AI4.2 NEW
│       ├── closer.py             ← AI4.2 NEW (archetype-aware)
│       └── compliance.py         ← AI4.2 NEW
├── compliance/
│   └── rules/
│       ├── spa_v1.yaml           ← exists + whatsapp_24h rule added
│       ├── tenancy_v1.yaml       ← AI4.4 NEW
│       ├── rpgt_calc.yaml        ← AI4.4 NEW
│       └── whatsapp_v1.yaml      ← AI4.3 NEW
├── api/
│   ├── chat.py                   ← AI4.1 NEW
│   ├── timing.py                 ← AI4.3 NEW (/timing/allowed)
│   ├── widget.py                 ← AI5.1 NEW
│   ├── auto_presenter.py         ← AI5.2 NEW
│   ├── principal.py              ← AI5.3 NEW
│   └── admin.py                  ← AI5.5 NEW
├── mcp/
│   └── server.py                 ← Phase 5 NEW (opt-in)
├── eval/
│   ├── harness.py                ← AI5.4 NEW
│   └── corpus/                   ← JSONL, append-only
├── fabrication/
│   └── dsl_parser.py             ← add FLOW_CALL node type
├── execution/
│   └── dag_runner.py             ← add execute_flow_call_node
└── db/
    └── sql/
        └── lead_facts.sql        ← AI3.3 NEW

ruma_flows/                       ← Activepieces fork (MIT)
└── presets/
    ├── inbound_message.yaml
    ├── new_lead.yaml
    ├── viewing_followup.yaml
    ├── hot_lead_alert.yaml
    ├── dead_lead_revival.yaml
    ├── anniversary_cron.yaml
    └── auto_presenter.yaml
    └── templates/
        ├── welcome_new_lead.ms.txt
        ├── welcome_new_lead.en.txt
        ├── viewing_thank_you.ms.txt
        ├── viewing_thank_you.en.txt
        ├── revival_new_listing.ms.txt
        ├── revival_new_listing.en.txt
        └── STATUS.md              ← Meta template approval status
```

---

## Full Test Matrix

| Test file | Covers | Must pass |
|---|---|---|
| `test_nlp/test_sentiment_intent.py` | Intent enum, BM/EN mixed, negation, archetype hints | All |
| `test_personality/test_timing.py` | Quiet hours, Friday prayer, 24h window, cadence, archive | All |
| `test_personality/test_tone.py` | Anti-pattern injection, archetype profiles, rapport decay | All |
| `test_personality/test_memory.py` | Rejected price injection, fact upsert, session round-trip | All |
| `test_a2a/test_personas.py` | Closer prompt structure, compliance check patterns | All |
| `test_api/test_chat_endpoint.py` | Pipeline order, timing block, NEVER pattern free reply | All |
| `test_api/test_widget.py` | Widget JS serve, web channel routing | All |
| `test_api/test_auto_presenter.py` | Loan calc, PDF bytes | All |
| `test_api/test_principal.py` | Hot leads, override stops agent, spend | All |
| `test_compliance/test_tenancy_rpgt.py` | Tenancy stamp duty, RPGT 30% rule | All |
| `test_flows/test_whatsapp_routing.py` | timing/allowed, flow_call node | All |
| `test_eval/test_harness.py` | Corpus flag, NEVER pattern counter, CI thresholds | All |

Run targeted (not full suite — Windows access-violation in full run is environment issue):
```powershell
.\myenv\Scripts\Activate.ps1
.\myenv\Scripts\python.exe -m pytest tests/test_nlp tests/test_personality tests/test_a2a tests/test_api tests/test_compliance tests/test_flows tests/test_eval -q
```

---

## Definition of Done (Phase 3–5)

- `POST /chat` with `"harga boleh kurang tak?"` returns intent=`objection_price`, timing_allowed=true, reply free of NEVER patterns
- Timing gate blocks outbound at 23:00 MYT and during Friday prayer for Muslim-flagged leads
- Rejected price stored → next session prompt contains "DO NOT RE-QUOTE RM 850,000"
- RUMA Flows (Activepieces fork, MIT) running in Docker, receives WhatsApp webhook, routes to `/chat`, sends reply — end-to-end in under 4s p95
- Auto-presenter generates valid PDF (magic bytes `%PDF`) with correct stamp duty for a 500k SPA
- `/principal/hot_leads` returns leads with `ready_to_close` confidence > 0.8
- Principal override stops agent outbound — next `/chat` returns `timing_allowed=False`
- Tenancy stamp duty and RPGT 30% rules catch seeded errors in test ruleset
- Spend dashboard returns correct tier breakdown from `node_executions`
- Eval harness corpus-flag pipeline writes to `eval_queue` table
- All targeted pytest tests pass with 0 failures

---

## Placeholders to wire at deploy time

| Placeholder | Default | Notes |
|---|---|---|
| `RUMA_FLOWS_URL` | `http://localhost:8080` | Activepieces instance URL |
| `WHATSAPP_ACCESS_TOKEN` | — | Meta Business API permanent token |
| `WHATSAPP_BUSINESS_ACCOUNT_ID` | — | From Meta developer portal |
| `WHATSAPP_PHONE_NUMBER_ID` | — | Sender number ID |
| `OPR_RATE` | `0.0325` | Bank Negara OPR — update quarterly |
| `BIG_API_PLACEHOLDER` | Anthropic Claude | Model router config |
| `NETIE_MCP_ENABLED` | `false` | Opt-in per agency |
| `FESTIVE_DATES_CONFIG` | `ruma_flows/config/festive.yaml` | Update yearly |
