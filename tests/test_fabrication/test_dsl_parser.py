import json
from netie.fabrication.dsl_parser import parse_dsl, AgenticDSLProgram
from netie.result import Ok

def make_json(data):
    return json.dumps(data)

def test_dsl_parser():
    # Helper to generate basic envelope
    def build_envelope(nodes, entry_id, output_id):
        return make_json({
            "version": "1.0",
            "intent_hash": "dummy",
            "entry_node_id": entry_id,
            "output_node_id": output_id,
            "raw_dsl": "...",
            "nodes": nodes
        })
        
    def base_node(id, ntype="TOOL_CALL", tier=0, inputs=None):
        return {
            "id": id,
            "type": ntype,
            "tier": tier,
            "inputs": inputs or [],
            "side_effects": [],
            "is_reversible": True,
            "annotations": {}
        }

    # Case 1: Valid 3-node linear DAG
    c1 = build_envelope([
        base_node("n1"),
        base_node("n2", inputs=["n1"]),
        base_node("n3", ntype="EMIT", inputs=["n2"])
    ], "n1", "n3")
    assert isinstance(parse_dsl(c1, "abc"), Ok)

    # Case 2: Missing EMIT node
    c2 = build_envelope([
        base_node("n1"),
        base_node("n2", inputs=["n1"])
    ], "n1", "n2")
    assert not isinstance(parse_dsl(c2, "abc"), Ok)
    assert parse_dsl(c2, "abc").message == "no EMIT node"

    # Case 3: Two EMIT nodes -> Err
    c3 = build_envelope([
        base_node("n1", ntype="EMIT"),
        base_node("n2", ntype="EMIT", inputs=["n1"])
    ], "n1", "n2")
    assert not isinstance(parse_dsl(c3, "abc"), Ok)
    assert "exactly one EMIT" in parse_dsl(c3, "abc").message

    # Case 4: Duplicate node ids -> Err
    c4 = build_envelope([
        base_node("n1"),
        base_node("n1", ntype="EMIT")
    ], "n1", "n1")
    assert not isinstance(parse_dsl(c4, "abc"), Ok)
    assert "Duplicate node id" in parse_dsl(c4, "abc").message

    # Case 5: Self-referencing node -> Err
    c5 = build_envelope([
        base_node("n1", inputs=["n1"]),
        base_node("n2", ntype="EMIT", inputs=["n1"])
    ], "n1", "n2")
    assert not isinstance(parse_dsl(c5, "abc"), Ok)
    assert "Self-referencing" in parse_dsl(c5, "abc").message

    # Case 6: Input references nonexistent node id -> Err
    c6 = build_envelope([
        base_node("n1"),
        base_node("n2", ntype="EMIT", inputs=["n99"])
    ], "n1", "n2")
    assert not isinstance(parse_dsl(c6, "abc"), Ok)
    assert "nonexistent node" in parse_dsl(c6, "abc").message

    # Case 7: entry_node_id not in nodes -> Err
    c7 = build_envelope([
        base_node("n1"),
        base_node("n2", ntype="EMIT", inputs=["n1"])
    ], "n99", "n2")
    assert not isinstance(parse_dsl(c7, "abc"), Ok)
    assert "entry_node_id n99 not in nodes" in parse_dsl(c7, "abc").message

    # Case 8: output_node_id not in nodes -> Err
    c8 = build_envelope([
        base_node("n1"),
        base_node("n2", ntype="EMIT", inputs=["n1"])
    ], "n1", "n99")
    assert not isinstance(parse_dsl(c8, "abc"), Ok)
    assert "output_node_id n99 not in nodes" in parse_dsl(c8, "abc").message

    # Case 9: Valid parallel DAG -> Ok
    c9 = build_envelope([
        base_node("start"),
        base_node("p1", inputs=["start"]),
        base_node("p2", inputs=["start"]),
        base_node("end", ntype="EMIT", inputs=["p1", "p2"])
    ], "start", "end")
    assert isinstance(parse_dsl(c9, "abc"), Ok)

    # Case 10: Completely empty nodes list -> Err
    c10 = build_envelope([], "n1", "n2")
    assert not isinstance(parse_dsl(c10, "abc"), Ok)
    assert "empty" in parse_dsl(c10, "abc").message

    # Case 11: raw_json is not JSON at all -> Err
    c11 = "THIS IS NOT JSON"
    res11 = parse_dsl(c11, "abc")
    assert not isinstance(res11, Ok)
    assert "Invalid JSON" in res11.message
