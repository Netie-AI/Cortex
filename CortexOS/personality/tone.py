"""Tone profile → system preamble for routed LLM nodes (§5.1 architecture)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass(slots=True)
class ToneProfile:
    formality: float = 0.3
    warmth: float = 0.8
    emoji_frequency: str = "low"
    honorifics: list[str] = field(default_factory=lambda: ["pak", "kak", "bro", "sis"])
    primary_language: str = "en"
    language_mix: dict[str, float] = field(default_factory=lambda: {"ms": 0.25, "zh": 0.15})
    signature: str = "— sent on behalf of {{agent.name}}, REN {{agent.ren}}"
    identity_disclosure: str = "required"


@dataclass(slots=True)
class LoadedToneAgent:
    agent_id: str
    tone: ToneProfile


def load_tone_agent_yaml(path: str | Path) -> LoadedToneAgent:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or "agent_id" not in raw:
        raise ValueError("tone YAML requires top-level agent_id")
    tone_block = raw.get("tone") or {}
    lm_block = tone_block.get("language_mix")
    primary_language = "en"
    language_mix_weights: dict[str, float] = {}
    if isinstance(lm_block, dict):
        lm_copy = dict(lm_block)
        primary_language = str(lm_copy.pop("primary", primary_language))
        for k, v in lm_copy.items():
            try:
                language_mix_weights[str(k)] = float(v)
            except (TypeError, ValueError):
                continue
    if not language_mix_weights:
        language_mix_weights = {"ms": 0.25, "zh": 0.15}

    tone = ToneProfile(
        formality=float(tone_block.get("formality", 0.3)),
        warmth=float(tone_block.get("warmth", 0.8)),
        emoji_frequency=str(tone_block.get("emoji_frequency", "low")),
        honorifics=list(tone_block.get("honorifics", ["pak", "kak", "bro", "sis"])),
        primary_language=primary_language,
        language_mix=language_mix_weights,
        signature=str(tone_block.get("signature", ToneProfile.signature)),
        identity_disclosure=str(tone_block.get("identity_disclosure", ToneProfile.identity_disclosure)),
    )
    return LoadedToneAgent(agent_id=str(raw["agent_id"]), tone=tone)


def compose_system_prompt(agent_id: str, tone_profile: ToneProfile) -> str:
    lm = tone_profile.language_mix or {}
    mix_desc = ", ".join(f"{k}={v:.2f}" for k, v in sorted(lm.items()) if isinstance(v, (int, float)))
    honors = ", ".join(tone_profile.honorifics)
    return (
        f"You are agent `{agent_id}`, speaking on behalf of the CRM user-facing assistant. "
        f"Tone: formality={tone_profile.formality}, warmth={tone_profile.warmth}, "
        f"emoji_frequency={tone_profile.emoji_frequency}, honorifics may include [{honors}]. "
        f"Cultural setting: Malaysia, urban Klang Valley. Language mix weights: "
        f"primary `{tone_profile.primary_language}` — channel targets: [{mix_desc}]. "
        f"Never lie about being human. Identity disclosure rule: `{tone_profile.identity_disclosure}` — "
        "if asked directly, disclose AI assistance. "
        f"Suggested sign-off template: `{tone_profile.signature}`"
    )
