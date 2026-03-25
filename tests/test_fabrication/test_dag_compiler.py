import json
from netie.fabrication.dsl_parser import AgenticDSLProgram, DSLNode, NodeType, InferenceTier
from netie.fabrication.dag_compiler import DAGCompiler, CompiledDAG
from netie.result import Ok, Err, CYCLIC_DAG

def make_node(id, inputs=None, ntype=NodeType.TOOL_CALL):
    return DSLNode(
        id=id,
        type=ntype,
        tier=InferenceTier.TIER0,
        inputs=inputs or [],
        side_effects=[],
        is_reversible=True,
        annotations={}
    )

def test_dag_compiler_valid():
    prog = AgenticDSLProgram(
        intent_hash="abc",
        entry_node_id="A",
        output_node_id="C",
        raw_dsl="...",
        nodes=[
            make_node("A"),
            make_node("B1", inputs=["A"]),
            make_node("B2", inputs=["A"]),
            make_node("C", inputs=["B1", "B2"], ntype=NodeType.EMIT)
        ]
    )
    
    compiler = DAGCompiler()
    res = compiler.compile(prog)
    assert isinstance(res, Ok)
    
    dag = res.value
    # parallel grouping check
    assert len(dag.execution_order) == 3
    assert "A" in dag.execution_order[0]
    assert "B1" in dag.execution_order[1]
    assert "B2" in dag.execution_order[1]
    assert "C" in dag.execution_order[2]
    
    # Check stable hash
    res2 = compiler.compile(prog)
    assert dag.dag_hash == res2.value.dag_hash

def test_dag_compiler_cyclic():
    prog = AgenticDSLProgram(
        intent_hash="abc",
        entry_node_id="A",
        output_node_id="C",
        raw_dsl="...",
        nodes=[
            make_node("A", inputs=["B"]),
            make_node("B", inputs=["A"]),
            make_node("C", inputs=["B"], ntype=NodeType.EMIT)
        ]
    )
    
    compiler = DAGCompiler()
    res = compiler.compile(prog)
    assert getattr(res, "code", None) == CYCLIC_DAG

def test_dead_code_elimination_true():
    prog = AgenticDSLProgram(
        intent_hash="abc",
        entry_node_id="A",
        output_node_id="C",
        raw_dsl="...",
        nodes=[
            make_node("A"),
            make_node("B", inputs=["A"]),
            make_node("C", inputs=["B"], ntype=NodeType.EMIT),
            make_node("D", inputs=["A"]) # dead code, not connected to C
        ]
    )
    
    compiler = DAGCompiler(dead_code_eliminator=True)
    res = compiler.compile(prog)
    assert isinstance(res, Ok)
    dag: CompiledDAG = res.value
    
    assert "D" not in dag.nodes
    assert dag.pruned_nodes == 1
    assert dag.total_nodes == 3

def test_dead_code_elimination_false():
    prog = AgenticDSLProgram(
        intent_hash="abc",
        entry_node_id="A",
        output_node_id="C",
        raw_dsl="...",
        nodes=[
            make_node("A"),
            make_node("B", inputs=["A"]),
            make_node("C", inputs=["B"], ntype=NodeType.EMIT),
            make_node("DEAD", inputs=["A"]) # dead code
        ]
    )
    
    compiler = DAGCompiler(dead_code_eliminator=False)
    res = compiler.compile(prog)
    assert isinstance(res, Ok)
    dag: CompiledDAG = res.value
    
    assert "DEAD" in dag.nodes
    assert not dag.nodes["DEAD"].is_reachable
    assert dag.pruned_nodes == 1
    assert dag.total_nodes == 4
