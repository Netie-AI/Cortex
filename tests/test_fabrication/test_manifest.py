import pytest
from netie.fabrication.manifest import generate_manifest, CapabilityManifest
from netie.fabrication.dag_compiler import CompiledDAG, DAGNode, DAGEdge
from netie.fabrication.dsl_parser import DSLNode, NodeType, InferenceTier
from netie.crypto.ephemeral_keys import generate_session_key, verify_manifest_data

def make_node(id, is_reachable=True):
    return DAGNode(
        id=id,
        dsl_node=DSLNode(id=id, type=NodeType.TOOL_CALL, tier=InferenceTier.TIER0),
        depth=0,
        is_reachable=is_reachable
    )

def test_manifest_generation():
    keypair = generate_session_key()
    
    # 3-node DAG
    nodes = {
        "n1": make_node("n1"),
        "n2": make_node("n2"),
        "n3": make_node("n3"),
        "n4_dead": make_node("n4_dead", False)
    }
    
    dag = CompiledDAG(
        dag_hash="hash123",
        nodes=nodes,
        edges=[],
        execution_order=[],
        total_nodes=4,
        pruned_nodes=1
    )
    
    res = generate_manifest(dag, keypair)
    manifest = res.value
    
    assert len(manifest.inference_tiers) == 3
    assert manifest.ttl_ms == 10000 + (2000 * 3) # 16000
    assert manifest.signature is not None
    assert len(manifest.signature) > 0
    assert verify_manifest_data(manifest.manifest_id, manifest.dag_hash, manifest.signature, keypair)

def test_manifest_ttl_cap():
    nodes = {f"n{i}": make_node(f"n{i}") for i in range(60)}
    dag = CompiledDAG(
        dag_hash="hash_big",
        nodes=nodes,
        edges=[],
        execution_order=[],
        total_nodes=60,
        pruned_nodes=0
    )
    
    res = generate_manifest(dag)
    assert res.value.ttl_ms == 120000 

def test_manifest_signature_forgery():
    dag = CompiledDAG(
        dag_hash="hash123",
        nodes={"n1": make_node("n1")},
        edges=[],
        execution_order=[],
        total_nodes=1,
        pruned_nodes=0
    )
    keypair = generate_session_key()
    res = generate_manifest(dag, keypair)
    manifest = res.value
    
    # verify normal
    assert verify_manifest_data(manifest.manifest_id, manifest.dag_hash, manifest.signature, keypair)
    
    # forgery by changing dag_hash (which is what signature signs)
    assert not verify_manifest_data(manifest.manifest_id, "forged_hash", manifest.signature, keypair)
