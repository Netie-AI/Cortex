import json
from enum import Enum

from netie.result import INVALID_DSL, SYNTHESIS_FAILED, Err, Ok, Result
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class NodeType(str, Enum):
    TOOL_CALL    = "TOOL_CALL"
    INFER_LOCAL  = "INFER_LOCAL"
    INFER_REMOTE = "INFER_REMOTE"
    BRANCH       = "BRANCH"
    MERGE        = "MERGE"
    EMIT         = "EMIT"
    LLM_STREAM = "llm_stream"
    SPECULATIVE_LLM = "speculative_llm"
    LLM_CACHED = "llm_cached"
    DETERMINISTIC_RULE = "deterministic_rule"
    LLM_JUDGED = "llm_judged"
    A2A_CALL = "a2a_call"
    DOCUMENT_REF = "document_ref"
    RAG_RETRIEVE = "RAG_RETRIEVE"
    RAG_RERANK = "RAG_RERANK"
    RAG_ANSWER = "RAG_ANSWER"
    AGENT_TASK = "agent_task"   # one workflow subagent: preset prompt + tool loop

class InferenceTier(int, Enum):
    TIER0 = 0   # deterministic tool
    TIER1 = 1   # local quantized model
    TIER2 = 2   # remote frontier API
    TIER3 = 3   # big-api escalation


class TierName(str, Enum):
    T0 = "T0"
    T1 = "T1"
    T2 = "T2"
    T3 = "T3"

class DSLNode(BaseModel):
    id: str                          # unique node id within DAG
    type: NodeType = Field(alias="kind")
    tier: InferenceTier | TierName | None = None
    default_tier: TierName | None = None
    max_tier: TierName | None = None
    cost_ceiling_myr: float | None = None
    tool_name: str | None = None     # for TOOL_CALL nodes
    model_hint: str | None = None    # for INFER_LOCAL/REMOTE
    provider: str | None = None
    request_type: str | None = None
    prompt: str | None = None
    system: str | None = None
    max_tokens: int | None = None
    ruleset: str | None = None
    context_key: str | None = None
    capability: str | None = None
    target_did: str | None = None
    cache_ttl: str | None = None
    inputs: list[str] = []           # ids of predecessor nodes
    side_effects: list[str] = []     # ["network_write", "filesystem_write"]
    is_reversible: bool = True
    outbound: bool = False
    annotations: dict = {}

    @model_validator(mode="before")
    @classmethod
    def normalize_kind(cls, data):
        if isinstance(data, dict):
            if "kind" not in data and "type" in data:
                data["kind"] = data["type"]
        return data

class AgenticDSLProgram(BaseModel):
    version: str = "1.0"
    intent_hash: str = ""            # SHA-256 of original intent (injected by parse_dsl)
    nodes: list[DSLNode]
    entry_node_id: str
    output_node_id: str
    raw_dsl: str = ""                # original LLM output, preserved (injected by parse_dsl)
    model_config = ConfigDict(populate_by_name=True)




def parse_dsl(raw_json: str, intent_hash: str) -> Result[AgenticDSLProgram]:
    try:
        data = json.loads(raw_json)
        if not isinstance(data, dict):
            return Err(INVALID_DSL, "DSL envelope must be a JSON object")
    except json.JSONDecodeError as e:
        return Err(INVALID_DSL, f"Invalid JSON: {e}")

    enriched = dict(data)
    enriched["intent_hash"] = intent_hash
    enriched["raw_dsl"] = raw_json

    try:
        program = AgenticDSLProgram(**enriched)
    except ValidationError as e:
        return Err(INVALID_DSL, f"Validation error: {e}")

    program.intent_hash = intent_hash
    program.raw_dsl = raw_json
    
    if not program.nodes:
        return Err(INVALID_DSL, "Nodes list is completely empty")
        
    node_ids = set()
    emit_nodes = 0
    
    for node in program.nodes:
        if node.id in node_ids:
            return Err(INVALID_DSL, f"Duplicate node id: {node.id}")
        node_ids.add(node.id)
        
        if node.type == NodeType.EMIT:
            emit_nodes += 1
            
        for inp in node.inputs:
            if inp == node.id:
                return Err(INVALID_DSL, f"Self-referencing node: {node.id}")
                
    if emit_nodes == 0:
        return Err(SYNTHESIS_FAILED, "no EMIT node")
    elif emit_nodes > 1:
        return Err(INVALID_DSL, f"exactly one EMIT is required, found {emit_nodes}")
        
    for node in program.nodes:
        for inp in node.inputs:
            if inp not in node_ids:
                return Err(INVALID_DSL, f"Input {inp} references nonexistent node id")
                
    if program.entry_node_id not in node_ids:
        return Err(INVALID_DSL, f"entry_node_id {program.entry_node_id} not in nodes")
        
    if program.output_node_id not in node_ids:
        return Err(INVALID_DSL, f"output_node_id {program.output_node_id} not in nodes")
        
    return Ok(program)
