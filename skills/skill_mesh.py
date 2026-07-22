"""
RUMA SkillMesh — Skill Cards
Cortex v2 | personality/skill_mesh.py

SkillMesh quality = output quality. Each skill card defines:
- Input schema (what the DAG node must supply)
- Output schema (what downstream nodes expect)
- Contract reference (which HLS prompt contract to use)
- Quality gates (minimum scores before output is emitted)
- Fallback behaviour (what to do if quality gate fails)

Four skills for RUMA MVP:
  1. PropertySearch   — AI-ranked search with RAG
  2. ConversationChat — multi-turn buyer/seller dialogue
  3. PhotoRetrieval   — direct photo links with metadata
  4. ComplianceCheck  — rule + LLM compliance gate

Usage:
  from personality.skill_mesh import SKILLS, invoke_skill
  result = await invoke_skill("PropertySearch", payload, cortex_runtime)
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from enum import Enum


# ── Quality thresholds ──────────────────────────────────────────────────────────

QUALITY_FLOOR = {
    "PropertySearch":   0.72,   # NDCG@10 proxy via reranker score
    "ConversationChat": 0.65,   # LLM self-eval fluency score
    "PhotoRetrieval":   0.90,   # deterministic — must resolve URL
    "ComplianceCheck":  1.00,   # zero tolerance; must pass or return corrected
}


# ── Base Skill Card ─────────────────────────────────────────────────────────────

@dataclass
class SkillCard:
    name: str
    description: str
    contract_id: str
    dag_node_kind: str          # llm_judged | deterministic_rule | llm_stream | a2a_call
    input_schema:  dict         # JSON Schema
    output_schema: dict         # JSON Schema
    quality_floor: float
    fallback: str               # "retry_t3" | "return_empty" | "raise"
    max_latency_ms: int = 4000
    cost_ceiling_myr: float = 0.50
    cacheable: bool = False
    cache_ttl_s: int = 0

    # Runtime — injected by Cortex on registration
    _invoke_fn: Optional[Callable] = field(default=None, repr=False)

    def register_invoke(self, fn: Callable):
        self._invoke_fn = fn
        return self

    async def invoke(self, payload: dict, runtime=None) -> dict:
        if self._invoke_fn is None:
            raise RuntimeError(f"Skill '{self.name}' has no invoke function registered. "
                               "Call skill.register_invoke(fn) before use.")
        t0 = time.monotonic()
        result = await self._invoke_fn(payload, runtime)
        elapsed_ms = (time.monotonic() - t0) * 1000

        # Latency gate
        if elapsed_ms > self.max_latency_ms:
            result["_latency_warning"] = f"{elapsed_ms:.0f}ms exceeded {self.max_latency_ms}ms target"

        # Quality gate
        score = result.get("_quality_score", 1.0)
        if score < self.quality_floor:
            if self.fallback == "retry_t3":
                result["_fallback_triggered"] = True
                # Signal to DAG: retry at T3
                result["_escalate_tier"] = "T3"
            elif self.fallback == "return_empty":
                return {"result": None, "_quality_failed": True, "_skill": self.name}
            elif self.fallback == "raise":
                raise ValueError(f"Skill '{self.name}' quality gate failed: "
                                 f"score={score:.2f} < floor={self.quality_floor}")

        result["_skill"] = self.name
        result["_elapsed_ms"] = round(elapsed_ms)
        return result


# ── Skill 1: PropertySearch ─────────────────────────────────────────────────────

SKILL_PROPERTY_SEARCH = SkillCard(
    name         = "PropertySearch",
    description  = (
        "AI-ranked property search. Fuses BM25 sparse + BGE-M3 dense retrieval via RRF, "
        "cross-encoder reranks, personalizes by user preference vector. "
        "Returns top-N listings with match_reason and photo_urls."
    ),
    contract_id  = "search_response",
    dag_node_kind = "llm_judged",
    quality_floor = QUALITY_FLOOR["PropertySearch"],
    fallback      = "retry_t3",
    max_latency_ms = 3000,
    cost_ceiling_myr = 0.10,
    cacheable     = False,      # results are fresh; never cache
    input_schema  = {
        "type": "object",
        "required": ["query", "user_id"],
        "properties": {
            "query":          {"type": "string", "maxLength": 500},
            "user_id":        {"type": "string"},
            "budget_min":     {"type": "number"},
            "budget_max":     {"type": "number"},
            "tenure":         {"type": "string", "enum": ["freehold", "leasehold", "bumi_lot", "any"]},
            "bedrooms":       {"type": "integer", "minimum": 0, "maximum": 10},
            "property_type":  {"type": "string", "enum": ["condo", "terraced", "semi_d", "bungalow", "studio", "any"]},
            "areas":          {"type": "array", "items": {"type": "string"}},
            "commute_anchor": {"type": "string"},  # e.g. "KLCC", "Bukit Jalil"
            "top_k":          {"type": "integer", "default": 5},
            "page":           {"type": "integer", "default": 1},
        }
    },
    output_schema = {
        "type": "object",
        "properties": {
            "listings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "listing_id":    {"type": "string"},
                        "title":         {"type": "string"},
                        "price_myr":     {"type": "number"},
                        "area":          {"type": "string"},
                        "bedrooms":      {"type": "integer"},
                        "bathrooms":     {"type": "integer"},
                        "sqft":          {"type": "number"},
                        "tenure":        {"type": "string"},
                        "photo_urls":    {"type": "array", "items": {"type": "string"}},
                        "primary_photo": {"type": "string"},  # direct CDN URL
                        "match_reason":  {"type": "string"},  # AI-generated 1-sentence why
                        "reranker_score":{"type": "number"},
                        "source":        {"type": "string"},  # "mudah" | "propertyguru" | "developer"
                        "listing_url":   {"type": "string"},
                        "agent_ren":     {"type": "string"},
                        "whatsapp_link": {"type": "string"},  # wa.me deeplink
                    }
                }
            },
            "synthesis":   {"type": "string"},   # AI prose summary (from HLS contract)
            "total_found": {"type": "integer"},
            "refine_question": {"type": "string"},  # one clarifying Q
        }
    }
)


# ── Skill 2: ConversationChat ───────────────────────────────────────────────────

SKILL_CONVERSATION_CHAT = SkillCard(
    name         = "ConversationChat",
    description  = (
        "Multi-turn AI chat for buyer/seller dialogue. Detects intent and sentiment "
        "on every inbound turn (T0). Routes response generation to T2 (default) or T3 "
        "(judgment escalation on complex objections or ready_to_close). "
        "Persona: warm, direct, multilingual (BM/EN/中文)."
    ),
    contract_id  = "chat_response",
    dag_node_kind = "llm_judged",
    quality_floor = QUALITY_FLOOR["ConversationChat"],
    fallback      = "retry_t3",
    max_latency_ms = 2500,
    cost_ceiling_myr = 0.30,
    cacheable     = False,
    input_schema  = {
        "type": "object",
        "required": ["session_id", "user_id", "user_message"],
        "properties": {
            "session_id":      {"type": "string"},
            "user_id":         {"type": "string"},
            "user_message":    {"type": "string", "maxLength": 2000},
            "listing_id":      {"type": ["string", "null"]},   # if in listing context
            "history_turns":   {"type": "integer", "default": 10},
            # Injected by sentiment pipeline (T0) before this skill runs
            "detected_intent":      {"type": "string"},
            "intent_confidence":    {"type": "number"},
            "sentiment_score":      {"type": "number"},
            "sentiment_label":      {"type": "string"},
            "lang_mix":             {"type": "object"},
            "lead_status":          {"type": "string"},
        }
    },
    output_schema = {
        "type": "object",
        "properties": {
            "reply":           {"type": "string"},
            "reply_lang":      {"type": "string"},           # primary lang used in reply
            "escalate_to_human": {"type": "boolean"},
            "schedule_viewing":  {"type": "boolean"},        # triggers viewing DAG if true
            "closer_trigger":    {"type": "boolean"},        # hands off to Closer agent
            "suggested_listings": {
                "type": "array",
                "items": {"type": "string"},
                "description": "listing_ids to show inline if relevant"
            },
        }
    }
)


# ── Skill 3: PhotoRetrieval ─────────────────────────────────────────────────────

SKILL_PHOTO_RETRIEVAL = SkillCard(
    name         = "PhotoRetrieval",
    description  = (
        "Resolves photo URLs for a listing. Priority: Supabase Storage CDN → "
        "original source URL (PropertyGuru / Mudah CDN) → placeholder. "
        "Returns signed URLs if bucket is private, public CDN URLs if public. "
        "No LLM involved — fully deterministic. Quality = URL resolution rate."
    ),
    contract_id  = "",           # no LLM contract — deterministic
    dag_node_kind = "deterministic_rule",
    quality_floor = QUALITY_FLOOR["PhotoRetrieval"],
    fallback      = "return_empty",
    max_latency_ms = 500,
    cost_ceiling_myr = 0.00,    # zero cost
    cacheable     = True,
    cache_ttl_s   = 3600,       # 1h — photos don't change often
    input_schema  = {
        "type": "object",
        "required": ["listing_id"],
        "properties": {
            "listing_id":  {"type": "string"},
            "max_photos":  {"type": "integer", "default": 8},
            "prefer_cdn":  {"type": "boolean", "default": True},
            "min_width_px": {"type": "integer", "default": 400},
        }
    },
    output_schema = {
        "type": "object",
        "properties": {
            "listing_id":    {"type": "string"},
            "primary_photo": {"type": "string"},   # first/hero image
            "photos": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "url":       {"type": "string"},
                        "source":    {"type": "string"},   # "supabase" | "pg" | "mudah"
                        "width_px":  {"type": ["integer", "null"]},
                        "height_px": {"type": ["integer", "null"]},
                        "is_valid":  {"type": "boolean"},
                    }
                }
            },
            "total_photos":  {"type": "integer"},
            "fallback_used": {"type": "boolean"},
        }
    }
)


# ── Skill 4: ComplianceCheck ────────────────────────────────────────────────────

SKILL_COMPLIANCE_CHECK = SkillCard(
    name         = "ComplianceCheck",
    description  = (
        "Two-stage compliance gate for any outbound message or document. "
        "Stage 1: deterministic YAML ruleset (stamp duty, RPGT, bumi-lot, NRIC). "
        "Stage 2: T3 LLM final-mile review ONLY if the message contains money/legal terms "
        "(detected by T0 classifier — 95% of messages skip T3). "
        "Returns pass/fail + issues + corrected_text."
    ),
    contract_id  = "compliance_review",    # T3 stage
    dag_node_kind = "deterministic_rule",   # stage 1 node kind
    quality_floor = QUALITY_FLOOR["ComplianceCheck"],
    fallback      = "raise",               # never silently pass a compliance failure
    max_latency_ms = 2000,
    cost_ceiling_myr = 0.50,
    cacheable     = False,
    input_schema  = {
        "type": "object",
        "required": ["draft_text", "message_type"],
        "properties": {
            "draft_text":           {"type": "string"},
            "message_type":         {"type": "string",
                                     "enum": ["chat", "follow_up", "offer_letter",
                                              "booking_confirmation", "spa_draft"]},
            "transaction_value_myr":{"type": ["number", "null"]},
            "tenure":               {"type": "string"},
            "is_bumi_lot":          {"type": "boolean", "default": False},
            "buyer_nationality":    {"type": "string"},
        }
    },
    output_schema = {
        "type": "object",
        "properties": {
            "pass":           {"type": "boolean"},
            "stage1_issues":  {"type": "array"},   # deterministic rule failures
            "stage2_issues":  {"type": "array"},   # LLM-detected issues (if stage 2 ran)
            "stage2_ran":     {"type": "boolean"},
            "corrected_text": {"type": ["string", "null"]},
            "severity":       {"type": "string", "enum": ["clean", "warning", "error"]},
        }
    }
)


# ── Registry ────────────────────────────────────────────────────────────────────

SKILLS: dict[str, SkillCard] = {
    "PropertySearch":   SKILL_PROPERTY_SEARCH,
    "ConversationChat": SKILL_CONVERSATION_CHAT,
    "PhotoRetrieval":   SKILL_PHOTO_RETRIEVAL,
    "ComplianceCheck":  SKILL_COMPLIANCE_CHECK,
}


async def invoke_skill(name: str, payload: dict, runtime=None) -> dict:
    """
    Entrypoint for DAG nodes. Looks up skill, validates input, runs invoke.
    
    Usage:
        result = await invoke_skill("PropertySearch", {
            "query": "condo near MRT Pudu max 2000 rent",
            "user_id": "u_123",
            "budget_max": 2000,
        })
        listings = result["listings"]
        synthesis = result["synthesis"]
    """
    if name not in SKILLS:
        raise KeyError(f"Unknown skill: {name!r}. Available: {list(SKILLS)}")
    skill = SKILLS[name]
    return await skill.invoke(payload, runtime)


# ── Quick sanity check ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("SkillMesh loaded. Skills registered:")
    for name, card in SKILLS.items():
        print(f"  {name:22s} | floor={card.quality_floor:.2f} | "
              f"max_lat={card.max_latency_ms}ms | T3={card.contract_id=='compliance_review' or card.contract_id=='rapport_opener'}")
