from enum import Enum
from pydantic import BaseModel
from typing import Literal, Union

class NodeType(str, Enum):
    TOOL_CALL    = "TOOL_CALL"
    INFER_LOCAL  = "INFER_LOCAL"
    INFER_REMOTE = "INFER_REMOTE"
    BRANCH       = "BRANCH"
    MERGE        = "MERGE"
    EMIT         = "EMIT"

class InferenceTier(int, Enum):
    TIER0 = 0   # deterministic tool
    TIER1 = 1   # local quantized model
    TIER2 = 2   # remote frontier API

class DSLNode(BaseModel):
    id: str                          # unique node id within DAG
    type: NodeType
    tier: InferenceTier
    tool_name: str | None = None     # for TOOL_CALL nodes
    model_hint: str | None = None    # for INFER_LOCAL/REMOTE
    inputs: list[str] = []           # ids of predecessor nodes
    side_effects: list[str] = []     # ["network_write", "filesystem_write"]
    is_reversible: bool = True
    annotations: dict = {}

class AgenticDSLProgram(BaseModel):
    version: str = "1.0"
    intent_hash: str                 # SHA-256 of original intent
    nodes: list[DSLNode]
    entry_node_id: str
    output_node_id: str
    raw_dsl: str                     # original LLM output, preserved

import json
from pydantic import ValidationError
from netie.result import Ok, Err, Result, SYNTHESIS_FAILED, INVALID_DSL

def parse_dsl(raw_json: str, intent_hash: str) -> Result[AgenticDSLProgram]:
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as e:
        return Err(INVALID_DSL, f"Invalid JSON: {e}")
        
    try:
        program = AgenticDSLProgram(**data)
    except ValidationError as e:
        return Err(INVALID_DSL, f"Validation error: {e}")
        
    # Override intent_hash
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
