from pydantic import BaseModel
from .dsl_parser import DSLNode, AgenticDSLProgram

class DAGEdge(BaseModel):
    from_node: str
    to_node: str
    data_type: str = "any"

class DAGNode(BaseModel):
    id: str
    dsl_node: DSLNode
    depth: int                       # topological depth
    is_reachable: bool = True        # set to False by dead-code eliminator
    parallel_group: int | None = None

class CompiledDAG(BaseModel):
    dag_hash: str                    # SHA-256 of canonical DAG IR
    nodes: dict[str, DAGNode]        # id -> node
    edges: list[DAGEdge]
    execution_order: list[list[str]] # topological layers (each layer = parallel)
    total_nodes: int
    pruned_nodes: int

import json
import hashlib
from collections import defaultdict, deque
from netie.result import Ok, Err, Result, CYCLIC_DAG

class DAGCompiler:
    def __init__(self, dead_code_eliminator: bool = True):
        self.dead_code_eliminator = dead_code_eliminator
        
    def compile(self, program: AgenticDSLProgram) -> Result[CompiledDAG]:
        adjacency = defaultdict(list)
        in_degree = defaultdict(int)
        
        # Initialize in-degrees for all nodes
        for node in program.nodes:
            in_degree[node.id] = 0
            
        edges = []
        for node in program.nodes:
            for inp in node.inputs:
                adjacency[inp].append(node.id)
                in_degree[node.id] += 1
                edges.append(DAGEdge(from_node=inp, to_node=node.id))
                
        # Batch-layer Kahn's
        queue = [nid for nid, deg in in_degree.items() if deg == 0]
        execution_order = []
        processed_count = 0
        depth_map = {}
        
        current_depth = 0
        while queue:
            layer = sorted(queue)
            execution_order.append(layer)
            next_queue = []
            
            for node_id in layer:
                processed_count += 1
                depth_map[node_id] = current_depth
                for successor in adjacency[node_id]:
                    in_degree[successor] -= 1
                    if in_degree[successor] == 0:
                        next_queue.append(successor)
                        
            queue = next_queue
            current_depth += 1
            
        if processed_count < len(program.nodes):
            return Err(CYCLIC_DAG, "Cycle detected in DAG")
            
        # Reverse BFS to find reachable nodes from EMIT node
        reachable = set()
        queue_bfs = deque([program.output_node_id])
        reachable.add(program.output_node_id)
        
        # Build reverse adjacency (we actually have normal adjacency mapping parent->child, 
        # but inputs map child->parent directly on the node)
        parent_map = {n.id: n.inputs for n in program.nodes}
        
        while queue_bfs:
            curr = queue_bfs.popleft()
            for parent in parent_map.get(curr, []):
                if parent not in reachable:
                    reachable.add(parent)
                    queue_bfs.append(parent)
        
        compiled_nodes = {}
        sorted_node_dicts = []
        pruned_count = 0
        
        # Filter nodes and edges if eliminator is enabled
        keep_nodes = []
        for n in program.nodes:
            if n.id not in reachable:
                pruned_count += 1
                if self.dead_code_eliminator:
                    continue
            keep_nodes.append(n)
            
        filtered_edges = []
        for e in edges:
            if e.from_node in reachable and e.to_node in reachable:
                filtered_edges.append(e)
            elif not self.dead_code_eliminator:
                filtered_edges.append(e)
        
        # We need a stable ordering to produce the dict list
        # Sorted by id for canonical hash
        for node in sorted(keep_nodes, key=lambda n: n.id):
            depth = depth_map[node.id]
            
            # Find parallel group ID (which is the layer index)
            parallel_group = None
            for idx, layer in enumerate(execution_order):
                if node.id in layer:
                    parallel_group = idx
                    break
                    
            cnode = DAGNode(
                id=node.id, 
                dsl_node=node, 
                depth=depth, 
                is_reachable=(node.id in reachable),
                parallel_group=parallel_group
            )
            compiled_nodes[node.id] = cnode
            sorted_node_dicts.append(cnode.model_dump())
            
        # Canonical hash
        canonical_json = json.dumps(sorted_node_dicts, sort_keys=True, separators=(',', ':'))
        dag_hash = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
        
        return Ok(CompiledDAG(
            dag_hash=dag_hash,
            nodes=compiled_nodes,
            edges=filtered_edges,
            execution_order=execution_order,
            total_nodes=len(compiled_nodes),
            pruned_nodes=pruned_count
        ))
