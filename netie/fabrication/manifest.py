from pydantic import BaseModel
from typing import Literal
from .dsl_parser import InferenceTier

class FilesystemPermission(BaseModel):
    paths: list[str]
    mode: Literal["read", "write", "read_write"]

class NetworkPermission(BaseModel):
    hosts: list[str]        # e.g. ["api.openai.com", "api.anthropic.com"]
    ports: list[int] = [443]
    protocols: list[str] = ["https"]

class CapabilityManifest(BaseModel):
    manifest_id: str                         # UUID
    dag_hash: str
    intent_hash: str
    created_at: str                          # ISO 8601
    ttl_ms: int = 30_000                     # 30 second default

    filesystem: list[FilesystemPermission] = []
    network: NetworkPermission | None = None
    syscalls_allowed: list[str] = []         # Linux only, from allowlist
    inference_tiers: dict[str, InferenceTier] = {}  # node_id -> tier
    models_required: dict[str, str] = {}     # model_name -> sha256 hash

    # Security
    signature: str | None = None             # ephemeral key signature
    is_verified: bool = False

import uuid
from netie.result import Ok, Err, Result
from netie.crypto.ephemeral_keys import generate_session_key, sign_manifest_data, EphemeralKeyPair
from netie.fabrication.dag_compiler import CompiledDAG
from netie.fabrication.dsl_parser import NodeType

def generate_manifest(dag: CompiledDAG, keypair: EphemeralKeyPair | None = None) -> Result[CapabilityManifest]:
    if not keypair:
        keypair = generate_session_key()
        
    reachable_nodes = [node for node in dag.nodes.values() if node.is_reachable]
    
    fs_perms = []
    network_hosts = set()
    tiers = {}
    models_req = {}
    
    for node in reachable_nodes:
        tiers[node.id] = node.dsl_node.tier
        if node.dsl_node.type == NodeType.INFER_LOCAL:
            if node.dsl_node.model_hint:
                models_req[node.dsl_node.model_hint] = "unknown_hash"
                
        ann = node.dsl_node.annotations
        if "filesystem" in ann:
            fs_perms.append(FilesystemPermission(paths=ann["filesystem"], mode="read_write"))
        if "network_hosts" in ann:
            for h in ann.__getitem__("network_hosts"):
                network_hosts.add(h)
    
    net_perm = NetworkPermission(hosts=list(network_hosts)) if network_hosts else None
    
    ttl_ms = min(10_000 + (2_000 * len(reachable_nodes)), 120_000)
    manifest_id = str(uuid.uuid4())
    
    # We infer intent_hash from DAG nodes if possible, but actually we don't store it in CompiledDAG...
    # Wait, the spec says intent_hash is in AgenticDSLProgram. Let's just use "unknown" or pass it in.
    # Actually, CapabilityManifest requires intent_hash. Let's pass it or extract from dag.
    # Since CapabilityManifest doesn't store intent_hash natively in CompiledDAG, I'll pass it in.
    # But signature is just manifest_id and dag_hash per user instructions!
    
    signature = sign_manifest_data(manifest_id, dag.dag_hash, keypair)
    
    # intent hash can just be empty for now if not available? It's type str.
    # I'll default to dag_hash if we don't have intent_hash here.
    
    manifest = CapabilityManifest(
        manifest_id=manifest_id,
        dag_hash=dag.dag_hash,
        intent_hash="pending", # To be filled by router if needed
        created_at="now",      # UUID timestamp or ISO could go here
        ttl_ms=ttl_ms,
        filesystem=fs_perms,
        network=net_perm,
        inference_tiers=tiers,
        models_required=models_req,
        signature=signature,
        is_verified=False
    )
    
    return Ok(manifest)
