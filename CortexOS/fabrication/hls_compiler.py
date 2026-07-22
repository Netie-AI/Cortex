import hashlib
import json
import logging
from typing import Any
import tiktoken
from litellm import acompletion

from netie.result import Ok, Err, Result, SYNTHESIS_FAILED
from netie.config import get_config
from netie.fabrication.skillmesh import SkillMesh
from netie.fabrication.dsl_parser import parse_dsl, AgenticDSLProgram

logger = logging.getLogger(__name__)

class HLSCompiler:
    def __init__(self, skill_mesh: SkillMesh):
        self.skill_mesh = skill_mesh
        self.config = get_config()
        # Initialize tiktoken encoder for cl100k_base
        try:
            self.encoder = tiktoken.get_encoding("cl100k_base")
        except Exception:
            # Fallback block just in case
            self.encoder = None

    def _measure_tokens(self, text: str) -> int:
        if self.encoder:
            return len(self.encoder.encode(text))
        return len(text.split()) * 2

    def _render_and_truncate_skills(self, cards, max_tokens: int = 2000) -> str:
        while cards:
            rendered = []
            for c in cards:
                rendered.append(f"Skill: {c.name}\nID: {c.skill_id}\nDesc: {c.description}\nIntents: {', '.join(c.example_intents)}")
            
            full_text = "\n\n".join(rendered)
            if self._measure_tokens(full_text) <= max_tokens:
                return full_text
            
            # Truncate lowest-ranked card
            cards = cards[:-1]
            
        return ""

    async def synthesize(self, intent: str) -> Result[AgenticDSLProgram]:
        intent_hash = hashlib.sha256(intent.encode("utf-8")).hexdigest()
        
        mesh_result = self.skill_mesh.retrieve(intent, top_k=8)
        if not isinstance(mesh_result, Ok):
            return Err(SYNTHESIS_FAILED, "Failed to retrieve from SkillMesh")
            
        cards = mesh_result.value
        skills_text = self._render_and_truncate_skills(cards, max_tokens=2000)
        
        system_prompt = f"""You are the Netie Cortex DAG synthesizer.
You must output ONLY valid JSON matching the AgenticDSLProgram specification. No markdown wrappers.
The JSON must contain "nodes", "entry_node_id", and "output_node_id".
Remember: Ensure exactly ONE node has type "EMIT" per DAG, it must be the final output node.
Available computational skills:
{skills_text}
"""
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Intent [{intent_hash}]: {intent}"}
        ]
        
        for attempt in range(2):
            try:
                response = await acompletion(
                    model=self.config.synthesis_model,
                    messages=messages,
                    api_key=self.config.api_key or "DUMMY_KEY"
                )
            except Exception as e:
                return Err(SYNTHESIS_FAILED, f"LLM API Call failed: {str(e)}")
                
            raw_text = response.choices[0].message.content.strip()
            
            # Remove possible markdown wrappers
            if raw_text.startswith("```json"):
                raw_text = raw_text[7:]
            if raw_text.startswith("```"):
                raw_text = raw_text[3:]
            if raw_text.endswith("```"):
                raw_text = raw_text[:-3]
            raw_text = raw_text.strip()
            
            parse_result = parse_dsl(raw_text, intent_hash)
            
            if isinstance(parse_result, Ok):
                return parse_result
            else:
                # Retry logic
                if attempt == 0:
                    messages.append({"role": "assistant", "content": raw_text})
                    messages.append({
                        "role": "user", 
                        "content": f"JSON or DSL rules validation failed: {parse_result.message}. Please fix and return ONLY valid JSON."
                    })
                    continue
                else:
                    return Err(SYNTHESIS_FAILED, f"Parse failed after retry: {parse_result.message}")
                    
        return Err(SYNTHESIS_FAILED, "Unexpected unreachable hit")
