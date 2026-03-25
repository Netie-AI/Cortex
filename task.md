# Netie Cortex — Task List
# For AI Coding Agents
# Each task is self-contained with clear inputs, outputs, and acceptance criteria.
# Execute tasks in order within each phase. Tasks within the same phase marked
# [PARALLEL] can be executed concurrently.

---

## HOW TO READ THIS FILE

Each task follows this format:
```
TASK-XXX: Title
Phase:     Which phase this belongs to
Depends:   Other task IDs that must complete first (empty = no deps)
Files:     Files to create or modify
Input:     What you need before starting
Output:    What exists when this task is done
AC:        Acceptance criteria — ALL must pass
```

---

## SETUP TASKS

---

### TASK-001: Initialize Repository Structure
```
Phase:    Setup
Depends:  none
Files:    CREATE all directories and placeholder files listed in
          implementation_plan.md "REPOSITORY STRUCTURE" section

Input:    Empty directory

Output:
  - pyproject.toml with all dependencies listed in implementation_plan.md
  - All directories created
  - All __init__.py files created (empty)
  - .gitignore (Python standard + add: *.gguf, ~/.netie/, .env)
  - LICENSE (Apache 2.0 full text)

AC:
  [ ] `pip install -e ".[dev]"` completes without errors
  [ ] `python -c "import netie"` succeeds
  [ ] `pytest --collect-only` finds test files without import errors
  [ ] pyproject.toml defines extras: [dev] [local-inference] [gpu]
```

---

### TASK-002: Define All Pydantic Data Models
```
Phase:    Setup
Depends:  TASK-001
Files:
  CREATE netie/fabrication/dsl_parser.py  (models only, no logic yet)
  CREATE netie/fabrication/manifest.py    (models only)
  CREATE netie/fabrication/skill_registry.py (SkillCard model only)

Input:    Data model definitions in implementation_plan.md
          "DATA MODELS (Source of Truth)" section

Output:
  - NodeType enum
  - InferenceTier enum
  - DSLNode model
  - AgenticDSLProgram model
  - DAGEdge, DAGNode, CompiledDAG models
  - FilesystemPermission, NetworkPermission, CapabilityManifest models
  - SkillCard model

AC:
  [ ] `from netie.fabrication.dsl_parser import DSLNode, NodeType` succeeds
  [ ] `from netie.fabrication.manifest import CapabilityManifest` succeeds
  [ ] `from netie.fabrication.skill_registry import SkillCard` succeeds
  [ ] All models are Pydantic v2 BaseModel subclasses
  [ ] All fields have type annotations
  [ ] Round-trip test: model.model_dump() then Model(**d) equals original
```

---

### TASK-003: Define Result Type and Error Codes
```
Phase:    Setup
Depends:  TASK-001
Files:
  CREATE netie/result.py

Input:    Error handling contract in implementation_plan.md

Output:
  - Ok[T] dataclass
  - Err dataclass with code, message, detail fields
  - Result type alias
  - All error code constants (E001-E010)

AC:
  [ ] `from netie.result import Ok, Err, Result` succeeds
  [ ] Ok and Err are generic-compatible
  [ ] All 10 error codes defined as module-level string constants
  [ ] Test: isinstance(Ok(42), Ok) is True
  [ ] Test: isinstance(Err("E001", "fail"), Err) is True
```

---

### TASK-004: Create Config System
```
Phase:    Setup
Depends:  TASK-001
Files:
  CREATE netie/config.py

Input:
  - Config schema in implementation_plan.md "CONFIGURATION" section

Output:
  - NettieConfig pydantic model mirroring config.toml structure
  - load_config() function: reads ~/.netie/config.toml, falls back to
    env vars (NETIE_API_KEY, NETIE_MODEL, etc.), falls back to defaults
  - save_config(config: NettieConfig) function
  - get_config() singleton (cached after first load)

AC:
  [ ] load_config() returns NettieConfig with defaults when no file exists
  [ ] NETIE_API_KEY env var overrides config file value
  [ ] save_config() creates ~/.netie/ directory if it does not exist
  [ ] save_config() writes valid TOML
  [ ] load_config(save_config(c)) round-trips without data loss
```

---

## PHASE 1 TASKS — CORE PIPELINE

---

### TASK-010: Create Default Skill Cards
```
Phase:    1
Depends:  TASK-002
Files:
  CREATE skills/_schema.yaml
  CREATE skills/web_search.yaml
  CREATE skills/summarization.yaml
  CREATE skills/code_execution.yaml
  CREATE skills/file_operations.yaml
  CREATE skills/data_extraction.yaml
  CREATE skills/api_call.yaml

Input:    SkillCard Pydantic model from TASK-002

Output:
  - 6 valid YAML skill cards, each loadable as SkillCard model
  - Each card has at minimum: skill_id, name, description, category,
    3+ example_intents, required_tools list

AC:
  [ ] Each YAML file validates against SkillCard model
  [ ] Each card has at least 3 example_intents
  [ ] No two cards have the same skill_id
  [ ] `pyyaml.safe_load(card_yaml)` succeeds for all cards
```

---

### TASK-011: Implement Skill Registry Loader [PARALLEL]
```
Phase:    1
Depends:  TASK-002, TASK-010
Files:
  MODIFY netie/fabrication/skill_registry.py (add functions)

Input:    SkillCard model, YAML skill cards in skills/ directory

Output:
  - load_skill_cards(directory: Path) -> list[SkillCard]
    Loads all .yaml files in directory, validates each as SkillCard
  - SkillRegistry class:
      __init__(cards: list[SkillCard])
      get(skill_id: str) -> SkillCard | None
      all() -> list[SkillCard]
      count() -> int

AC:
  [ ] load_skill_cards(skills/) returns exactly 6 SkillCard objects
  [ ] Invalid YAML files are logged as warnings, not raised as exceptions
  [ ] SkillRegistry.get("web_search_v1") returns the correct card
  [ ] SkillRegistry.count() == 6
  [ ] tests/test_fabrication/test_skill_registry.py: all tests pass
```

---

### TASK-012: Implement BM25 Stage of SkillMesh [PARALLEL]
```
Phase:    1
Depends:  TASK-011
Files:
  CREATE netie/fabrication/skillmesh.py

Input:
  - rank_bm25 library
  - SkillRegistry

Output:
  - BM25Index class:
      __init__(registry: SkillRegistry)
        Tokenizes each skill card: title + description + example_intents
        Builds BM25Okapi index
      query(intent: str, top_n: int = 50) -> list[tuple[SkillCard, float]]
        Returns (card, score) tuples sorted by score descending

AC:
  [ ] BM25Index builds without error on 6 default skill cards
  [ ] query("summarize this article") returns summarization card in top 3
  [ ] query("search the web for news") returns web_search card in top 3
  [ ] query returns at most top_n results
  [ ] Empty query returns empty list (no exception)
  [ ] tests/test_fabrication/test_skillmesh.py: all BM25 tests pass
```

---

### TASK-013: Implement Dense Re-ranking Stage of SkillMesh [PARALLEL]
```
Phase:    1
Depends:  TASK-012
Files:
  MODIFY netie/fabrication/skillmesh.py (add DenseReranker class)

Input:
  - sentence-transformers library
  - BM25Index from TASK-012

Output:
  - DenseReranker class:
      __init__(model_name: str = "all-MiniLM-L6-v2")
        Downloads model on first init (cached by sentence-transformers)
      rerank(intent: str, candidates: list[tuple[SkillCard, float]],
             top_k: int = 8) -> list[SkillCard]
        Encodes intent + candidate descriptions
        Cosine similarity scoring
        Returns top_k cards

  - SkillMesh class (combines BM25 + Dense):
      __init__(registry: SkillRegistry)
      retrieve(intent: str, top_k: int = 8) -> Result[list[SkillCard]]
        Stage 1: BM25 top 50
        Stage 2: Dense rerank to top_k
        Adversarial check: if stddev of top-k cosine scores > 0.4,
          set top_k = min(top_k, 3) and add "near_boundary" warning

AC:
  [ ] DenseReranker downloads model without error
  [ ] rerank() returns correct type: list[SkillCard]
  [ ] SkillMesh.retrieve("summarize this article") returns ≤ 8 cards
  [ ] Retrieved cards are sorted by relevance (most relevant first)
  [ ] Total token count of injected skill card JSON ≤ 2000 tokens
    (measure with tiktoken cl100k_base encoding)
  [ ] Near-boundary detection: artificially craft a nonsense intent and
    verify top_k is reduced to 3 (unit test with mock cosine scores)
  [ ] tests/test_fabrication/test_skillmesh.py: all dense tests pass
```

---

### TASK-014: Implement HLS Compiler (Synthesis)
```
Phase:    1
Depends:  TASK-013, TASK-004
Files:
  CREATE netie/fabrication/hls_compiler.py
  CREATE netie/prompts/synthesis.txt  (the synthesis prompt template)

Input:
  - litellm library
  - SkillMesh (TASK-013)
  - NettieConfig (TASK-004)
  - Synthesis prompt template from implementation_plan.md

Output:
  - HLSCompiler class:
      __init__(config: NettieConfig)
      async compile(intent: str, skill_cards: list[SkillCard])
          -> Result[AgenticDSLProgram]
        Build synthesis prompt from template
        Call litellm.acompletion()
        Parse JSON response into AgenticDSLProgram
        On JSON parse failure: retry once with error feedback appended
        On second failure: return Err(SYNTHESIS_FAILED, ...)
        Log token_usage to config.skills telemetry

AC:
  [ ] compile() makes exactly 1 LLM call per synthesis (2 if retry)
  [ ] Response parsing validates against AgenticDSLProgram schema
  [ ] Invalid JSON response triggers exactly one retry
  [ ] Token usage is captured and returned in metadata
  [ ] Missing API key returns Err(API_KEY_MISSING) before any API call
  [ ] compile() works with openai AND anthropic providers (test both)
  [ ] Synthesis prompt injects ≤ 2000 tokens of skill metadata (verified
    with tiktoken before sending)
  [ ] tests/test_fabrication/test_hls_compiler.py: all tests pass
    (use respx to mock LiteLLM HTTP calls)
```

---

### TASK-015: Implement DSL Parser
```
Phase:    1
Depends:  TASK-002
Files:
  MODIFY netie/fabrication/dsl_parser.py (add parsing functions)

Input:
  - DSLNode, AgenticDSLProgram models

Output:
  - parse_dsl(raw_json: str) -> Result[AgenticDSLProgram]
    Parses and validates raw LLM JSON output
    Validates: all input references exist as node ids
    Validates: exactly one EMIT node exists
    Validates: entry_node_id and output_node_id exist in nodes list
    Validates: no self-referencing nodes (node.id not in node.inputs)

AC:
  [ ] Valid DSL JSON parses successfully
  [ ] Missing EMIT node returns Err(SYNTHESIS_FAILED, "no EMIT node")
  [ ] Duplicate node ids returns Err
  [ ] Self-referencing node returns Err
  [ ] Unknown input reference returns Err
  [ ] tests/test_fabrication/test_dsl_parser.py: ≥ 10 test cases including
    all error paths
```

---

### TASK-016: Implement DAG Compiler
```
Phase:    1
Depends:  TASK-015
Files:
  CREATE netie/fabrication/dag_compiler.py

Input:    AgenticDSLProgram from TASK-015

Output:
  - compile_dag(program: AgenticDSLProgram) -> Result[CompiledDAG]
    Build adjacency list from node.inputs declarations
    Kahn's algorithm topological sort
    Detect cycles → return Err(CYCLIC_DAG)
    Assign depth to each node (BFS from entry node)
    Group nodes by depth into execution_order layers
    Compute dag_hash = SHA-256(json.dumps(sorted node dicts))
    Count total_nodes

AC:
  [ ] Linear DAG (A→B→C→EMIT) sorts correctly: [[A],[B],[C],[EMIT]]
  [ ] Parallel DAG (A→C, B→C, C→EMIT) groups correctly: [[A,B],[C],[EMIT]]
  [ ] Cyclic DAG (A→B→A) returns Err(CYCLIC_DAG)
  [ ] dag_hash is deterministic: same program → same hash always
  [ ] total_nodes count matches len(program.nodes)
  [ ] tests/test_fabrication/test_dag_compiler.py: ≥ 8 test cases
```

---

### TASK-017: Implement Dead-Code Eliminator
```
Phase:    1
Depends:  TASK-016
Files:
  CREATE netie/fabrication/dead_code.py

Input:    CompiledDAG from TASK-016

Output:
  - eliminate_dead_code(dag: CompiledDAG) -> CompiledDAG
    Reverse BFS from EMIT node
    Mark unreachable nodes: is_reachable = False
    Remove edges to/from unreachable nodes
    Update pruned_nodes count
    Recompute dag_hash after pruning

AC:
  [ ] DAG with no dead code: pruned_nodes == 0, all nodes reachable
  [ ] DAG with one orphaned node: pruned_nodes == 1, orphan is_reachable=False
  [ ] DAG with entire orphaned branch: all branch nodes marked unreachable
  [ ] dag_hash changes after pruning (different from input DAG hash)
  [ ] Function is pure — does not mutate input, returns new CompiledDAG
  [ ] tests/test_fabrication/test_dead_code.py: ≥ 6 test cases
```

---

### TASK-018: Implement Capability Manifest Generator
```
Phase:    1
Depends:  TASK-017, TASK-003
Files:
  MODIFY netie/fabrication/manifest.py (add generator function)
  CREATE netie/crypto/ephemeral_keys.py

Input:
  - CompiledDAG from TASK-017
  - ephemeral key signing from crypto module

Output:
  - generate_manifest(dag: CompiledDAG) -> Result[CapabilityManifest]
    Walk all is_reachable=True nodes
    Collect filesystem annotations (node.annotations.get("filesystem", []))
    Collect network annotations (node.annotations.get("network_hosts", []))
    Build inference_tiers dict: {node_id: node.dsl_node.tier}
    Set ttl_ms = min(10000 + (2000 * reachable_node_count), 120000)
    Generate manifest_id as UUID4
    Sign with ephemeral key

  - In netie/crypto/ephemeral_keys.py:
    generate_session_key() -> bytes  (32 bytes, os.urandom)
    sign_manifest(manifest: CapabilityManifest, key: bytes) -> str
      HMAC-SHA256(key, manifest canonical JSON) → hex string
    verify_manifest(manifest: CapabilityManifest, key: bytes) -> bool

AC:
  [ ] Manifest generated from 3-node DAG has correct inference_tiers (3 entries)
  [ ] ttl_ms for 5-node DAG = 10000 + 10000 = 20000
  [ ] ttl_ms is capped at 120000 regardless of node count
  [ ] Signature is non-empty string
  [ ] verify_manifest returns True for correctly signed manifest
  [ ] verify_manifest returns False if manifest fields are mutated after signing
  [ ] tests/test_fabrication/test_manifest.py: all tests pass
```

---

### TASK-019: Implement Causal Injection Scanner
```
Phase:    1
Depends:  TASK-015, TASK-016
Files:
  CREATE netie/fabrication/causal_scanner.py

Input:
  - AgenticDSLProgram
  - Original intent string

Output:
  - ScanResult dataclass:
      passed: bool
      flags: list[str]
      risk_score: float  (0.0 = clean, 1.0 = block)
      detail: dict

  - scan(program: AgenticDSLProgram, intent: str) -> ScanResult
    Implement all 5 checks from implementation_plan.md:
    1. Privilege escalation check (weight: 0.4)
    2. Injection echo check (weight: 0.3)
    3. Blast radius check >3 INFER_REMOTE nodes (weight: 0.15)
    4. Anomalous node count >20 (weight: 0.1)
    5. Tier violation: EMIT at Tier 2 (weight: 0.05)
    risk_score = sum(weight for each triggered check)
    passed = risk_score < 0.6

  INJECTION_PATTERNS constant: list of known injection strings to check
  Include at minimum:
    "[INST]", "ignore previous", "system:", "###", "IGNORE ALL",
    "jailbreak", "DAN", "you are now", "pretend you are",
    "disregard", "override", "forget your instructions"

AC:
  [ ] Clean program (no flags): risk_score == 0.0, passed == True
  [ ] Program with filesystem.write + network_write on same node:
    risk_score >= 0.4, "privilege_escalation" in flags
  [ ] Program with "[INST]" in tool_name: "injection_echo" in flags
  [ ] Program with 5 INFER_REMOTE nodes: "excessive_remote" in flags
  [ ] Program with 25 nodes: "anomalous_complexity" in flags
  [ ] risk_score >= 0.6 → passed == False
  [ ] tests/test_fabrication/test_causal_scanner.py: ≥ 10 tests
    including all 5 check types
```

---

### TASK-020: Implement Intent Router (Fabrication Orchestrator)
```
Phase:    1
Depends:  TASK-013, TASK-014, TASK-015, TASK-016, TASK-017,
          TASK-018, TASK-019
Files:
  CREATE netie/fabrication/intent_router.py

Input:    All fabrication components

Output:
  - FabricationResult dataclass:
      dag: CompiledDAG
      manifest: CapabilityManifest
      scan_result: ScanResult
      skill_cards_used: list[SkillCard]
      synthesis_tokens: int
      duration_ms: int

  - IntentRouter class:
      __init__(config: NettieConfig, registry: SkillRegistry)
      async route(intent: str) -> Result[FabricationResult]
        1. SkillMesh.retrieve(intent) → top-K cards
        2. HLSCompiler.compile(intent, cards) → DSL program
        3. parse_dsl(program) → validated program
        4. CausalScanner.scan(program, intent) → scan result
           If scan.passed == False: return Err(INJECTION_DETECTED)
        5. compile_dag(program) → CompiledDAG
        6. eliminate_dead_code(dag) → pruned DAG
        7. generate_manifest(dag) → CapabilityManifest
        8. Return FabricationResult

AC:
  [ ] route() calls each stage exactly once in correct order
  [ ] Failed CIS scan returns Err(INJECTION_DETECTED) without calling DAG stages
  [ ] FabricationResult.synthesis_tokens > 0 after successful run
  [ ] FabricationResult.duration_ms > 0
  [ ] End-to-end test with mocked LLM: "summarize this text: ..."
    completes and returns a valid FabricationResult
  [ ] tests/test_fabrication/test_intent_router.py: integration test passes
```

---

### TASK-021: Implement Tier 0 Tools
```
Phase:    1
Depends:  TASK-002
Files:
  CREATE netie/execution/tier0_tools.py

Input:    Tool definitions referenced in default skill cards

Output:
  - ToolRegistry class with these built-in tools:
    http_get(url: str, headers: dict = {}) -> Result[str]
      Fetches URL, returns response body text, respects manifest allowlist
    http_post(url: str, body: dict, headers: dict = {}) -> Result[str]
    regex_extract(pattern: str, text: str) -> Result[list[str]]
    json_parse(text: str, path: str = "$") -> Result[Any]
      JSONPath extraction using jsonpath-ng
    text_concat(parts: list[str], separator: str = "\n") -> Result[str]
    file_read(path: str) -> Result[str]
      Reads file, checks path against manifest before opening
    file_write(path: str, content: str) -> Result[bool]
      Writes file, checks path against manifest before opening

  - dispatch(tool_name: str, inputs: dict,
             manifest: CapabilityManifest) -> Result[Any]
    Routes to correct tool function
    Enforces manifest.network and manifest.filesystem before execution

AC:
  [ ] http_get to declared host succeeds (mock with respx)
  [ ] http_get to undeclared host returns Err(MANIFEST_VIOLATION)
  [ ] file_read from declared path succeeds
  [ ] file_read from undeclared path returns Err(MANIFEST_VIOLATION)
  [ ] regex_extract with valid pattern returns list of matches
  [ ] regex_extract with invalid pattern returns Err, not exception
  [ ] tests/test_execution/test_tier0_tools.py: all tests pass
```

---

### TASK-022: Implement Tier 2 Remote API Call
```
Phase:    1
Depends:  TASK-004
Files:
  CREATE netie/execution/tier2_api.py

Input:    LiteLLM, NettieConfig

Output:
  - Tier2Client class:
      __init__(config: NettieConfig)
      async call(prompt: str, model_hint: str | None = None,
                 max_tokens: int = 500) -> Result[str]
        Uses config.tier2_model unless model_hint overrides
        Returns text content of first choice

AC:
  [ ] call() uses config.tier2_model by default
  [ ] call() uses model_hint when provided
  [ ] call() returns Err(API_KEY_MISSING) when no key configured
  [ ] Token usage is logged
  [ ] Timeout of 30s: returns Err on timeout, not exception
  [ ] tests/test_execution/test_tier2_api.py: all tests pass (mock LiteLLM)
```

---

### TASK-023: Implement DAG Executor
```
Phase:    1
Depends:  TASK-020, TASK-021, TASK-022
Files:
  CREATE netie/execution/executor.py

Input:
  - CompiledDAG + CapabilityManifest
  - Tier0 ToolRegistry, Tier2Client

Output:
  - ExecutionResult dataclass:
      output: str
      node_results: dict[str, Any]  # node_id -> result
      token_usage: dict             # {tier0: 0, tier1: 0, tier2: int}
      duration_ms: int
      error: str | None

  - DAGExecutor class:
      __init__(tier0: ToolRegistry, tier2: Tier2Client,
               manifest: CapabilityManifest)
      async execute(dag: CompiledDAG, context: dict = {})
          -> Result[ExecutionResult]
        For each layer in dag.execution_order (layers run sequentially):
          Execute all nodes in layer concurrently (asyncio.gather)
          Store results in node_results
        EMIT node result becomes final output
        Track duration_ms
        On node failure: propagate Err up, stop execution

  - execute_node(node: DAGNode, node_results: dict,
                 manifest: CapabilityManifest) -> Result[Any]
    Dispatches to Tier 0 or Tier 2 based on node.dsl_node.tier
    Tier 1 → log warning "Tier 1 not available, routing to Tier 2"
              then call Tier 2 (Tier 1 is Phase 2)
    Resolves node inputs from node_results dict

AC:
  [ ] Linear 3-node DAG executes nodes in correct order
  [ ] Parallel 2-branch DAG executes both branches concurrently
    (verify with asyncio timing: concurrent should be ~1× slower than
    slowest branch, not 2× slower than sum of branches)
  [ ] Node failure propagates Err and stops execution
  [ ] EMIT node output becomes ExecutionResult.output
  [ ] Token usage accumulates across all Tier 2 nodes
  [ ] Tier 1 nodes fall back to Tier 2 with logged warning
  [ ] tests/test_execution/test_executor.py: all tests pass
```

---

### TASK-024: Implement CLI
```
Phase:    1
Depends:  TASK-020, TASK-023, TASK-004
Files:
  MODIFY netie/cli.py

Input:    All components from Phase 1

Output:
  Implement these CLI commands using typer:

  netie run "<intent>"
    Runs full pipeline: fabrication → execution → print output
    Shows rich progress bar during synthesis and execution
    On success: prints output, shows token usage summary
    On error: prints error code + message in red

  netie run "<intent>" --dry-run
    Runs fabrication only (no execution)
    Pretty-prints the compiled DAG as a tree using rich.Tree

  netie run "<intent>" --show-dag
    Runs full pipeline
    After completion, pretty-prints the DAG

  netie config set <key> <value>
    Sets key in ~/.netie/config.toml
    Valid keys: api_key, provider, synthesis_model, tier2_model

  netie config show
    Prints current config (masks api_key to last 4 chars)

  netie skills list
    Lists all indexed skill cards in a rich.Table

  netie skills add <path>
    Validates YAML as SkillCard, adds to user skills dir

  netie benchmark
    Runs 3 benchmark tasks from tests/fixtures/sample_intents.txt
    Reports: token_usage, duration_ms, skill_cards_used for each

AC:
  [ ] `netie --help` shows all commands
  [ ] `netie run "hello world" --dry-run` works without API key (dry run
    still needs API for synthesis — print informative error if key missing)
  [ ] `netie config set api_key sk-test` writes to config file
  [ ] `netie config show` masks API key: "sk-...test"
  [ ] `netie skills list` shows 6 default skills in a table
  [ ] CLI exits with code 0 on success, 1 on user error, 2 on internal error
  [ ] All rich output uses consistent color scheme
```

---

### TASK-025: Write Comprehensive Tests for Phase 1
```
Phase:    1
Depends:  TASK-010 through TASK-024
Files:
  CREATE tests/test_fabrication/test_skillmesh.py
  CREATE tests/test_fabrication/test_hls_compiler.py
  CREATE tests/test_fabrication/test_dsl_parser.py
  CREATE tests/test_fabrication/test_dag_compiler.py
  CREATE tests/test_fabrication/test_dead_code.py
  CREATE tests/test_fabrication/test_manifest.py
  CREATE tests/test_fabrication/test_causal_scanner.py
  CREATE tests/test_fabrication/test_intent_router.py
  CREATE tests/test_execution/test_tier0_tools.py
  CREATE tests/test_execution/test_tier2_api.py
  CREATE tests/test_execution/test_executor.py
  CREATE tests/conftest.py
  CREATE tests/fixtures/sample_intents.txt  (10 diverse intents)
  CREATE tests/fixtures/sample_dags.json    (5 pre-compiled test DAGs)

Input:    All Phase 1 components

AC:
  [ ] `pytest tests/` passes with 0 failures on Ubuntu 22.04
  [ ] `pytest tests/` passes with 0 failures on macOS 14
  [ ] `pytest tests/` passes with 0 failures on Windows 11
  [ ] Total test count ≥ 80
  [ ] Coverage ≥ 85% for fabrication/ modules
  [ ] Coverage ≥ 80% for execution/ modules
  [ ] No test uses real API calls (all LLM calls mocked with respx/pytest-mock)
  [ ] `pytest --tb=short -q` completes in under 30 seconds
```

---

## PHASE 1b TASKS — WASM EXECUTION

---

### TASK-030: Create Base Wasm Agent Module
```
Phase:    1b
Depends:  TASK-001
Files:
  CREATE wasm_modules/base_agent.wat
  CREATE wasm_modules/compile.sh  (script to produce base_agent.wasm)

Input:    Wasmtime documentation, WASI preview1 spec

Output:
  base_agent.wat that:
  - Imports: wasi_snapshot_preview1 standard imports only
  - Exports: _start function
  - Reads JSON instruction object from stdin (wasi fd_read)
  - Writes JSON result object to stdout (wasi fd_write)
  - Has no other capabilities (no fs, no network in the WAT itself —
    capabilities granted externally by host)

  base_agent.wasm: compiled from .wat using wat2wasm or wasmtime compile

AC:
  [ ] base_agent.wasm compiles without error
  [ ] wasm_module_test: wasmtime run with {"op":"echo","data":"hello"}
    as stdin produces {"result":"hello"} on stdout
  [ ] wasm binary size < 50KB
  [ ] wasmtime instantiation time < 5ms (measure 100 runs, report P99)
```

---

### TASK-031: Implement Wasm Isolate Wrapper
```
Phase:    1b
Depends:  TASK-030, TASK-018
Files:
  CREATE netie/execution/wasm_isolate.py

Input:
  - wasmtime Python library
  - CapabilityManifest
  - base_agent.wasm

Output:
  - WasmIsolate class:
      __init__(manifest: CapabilityManifest, node_id: str,
               wasm_path: Path)
        Configure wasmtime.Engine with:
          - cranelift backend
          - cache enabled
          - max_wasm_stack = 1MB
        Configure wasmtime.Linker with WASI
        Apply capability grants from manifest (filesystem and network)

      async execute(instructions: dict) -> Result[dict]
        Serialize instructions to JSON
        Write to stdin pipe
        Instantiate module (fresh instance per call)
        Call _start
        Read stdout pipe
        Parse JSON result
        Drop instance (deterministic deallocation)
        Return result dict

      _apply_capabilities(wasi_config, manifest, node_id)
        Grant filesystem preopen dirs from manifest.filesystem
        If manifest.network is None: do not grant any network WASI caps
        Note: true network isolation requires OS-level (TASK-040)

AC:
  [ ] execute({"op":"echo","data":"test"}) returns {"result":"test"}
  [ ] Fresh Wasm instance per execute() call (verified by instance counter)
  [ ] execute() duration < 10ms for echo operation
  [ ] Wasm module crashes (trap) → returns Err, not Python exception
  [ ] Memory is reclaimed after execute() (no linear memory leak over
    1000 consecutive calls — verify with psutil memory tracking)
  [ ] tests/test_execution/test_wasm_isolate.py: all tests pass
```

---

### TASK-032: Integrate Wasm Isolates into DAG Executor
```
Phase:    1b
Depends:  TASK-031, TASK-023
Files:
  MODIFY netie/execution/executor.py

Input:    WasmIsolate, existing DAGExecutor

Output:
  - Update execute_node() to run each node inside a WasmIsolate when
    wasm_available is True
  - WasmIsolate wraps the tool call or inference call
  - Fallback to plain Python execution if wasmtime not installed
    (log warning: "wasmtime not available, running without Wasm isolation")
  - WasmIsolate receives serialized inputs and manifest
  - WasmIsolate returns serialized output

AC:
  [ ] `netie run "echo test" --dry-run` still works (no Wasm in dry-run)
  [ ] With wasmtime installed: executor uses WasmIsolate for each node
  [ ] Without wasmtime: executor falls back to plain Python with warning
  [ ] Tier 0 tools still enforce manifest restrictions inside Wasm
  [ ] tests/test_execution/test_executor.py: all existing tests still pass
  [ ] New test: executor uses WasmIsolate when wasmtime available
```

---

## PHASE 1b TASKS — PLATFORM SECURITY

---

### TASK-040: Implement Platform Security Adapter (Linux)
```
Phase:    1b
Depends:  TASK-018
Files:
  CREATE netie/security/platform.py
  CREATE netie/security/linux_adapter.py

Input:
  - CapabilityManifest
  - ctypes for syscall interface
  - Landlock documentation (kernel.org/doc/html/latest/userspace-api/landlock.html)

Output:
  - BasePlatformAdapter abstract class:
      apply(manifest: CapabilityManifest) -> Result[None]
      release() -> None

  - LandlockAdapter(BasePlatformAdapter):
      apply():
        Check kernel version ≥ 5.13 (via platform.release())
        If not: fall back to WasmOnlyAdapter
        Create Landlock ruleset with allowed_access flags derived from manifest
        Add path rules for each manifest.filesystem entry
        Restrict self (prctl Landlock)
        Return Ok(None)

  - LinuxBasicAdapter(BasePlatformAdapter):
      apply():
        Apply seccomp-bpf allowlist filter (safe minimal syscall set)
        Use python-seccomp or ctypes-based BPF program
        Allowlist: read, write, open, close, mmap, mprotect, brk,
          exit, exit_group, rt_sigreturn, futex, clock_gettime,
          and any additional syscalls required by wasmtime

  - get_adapter() function:
      import platform
      if system == "Linux":
          if landlock_available(): return LandlockAdapter()
          else: return LinuxBasicAdapter()
      elif system == "Darwin": return MacOSAdapter()
      elif system == "Windows": return WindowsAdapter()
      else: return WasmOnlyAdapter()

AC:
  [ ] get_adapter() returns LandlockAdapter on Linux 5.13+ (mock kernel ver)
  [ ] get_adapter() returns LinuxBasicAdapter on Linux < 5.13
  [ ] LandlockAdapter.apply() with manifest allowing /tmp → restricts to /tmp
  [ ] Attempt to open /etc/passwd after LandlockAdapter.apply() → EACCES
    (test in subprocess to avoid polluting test process)
  [ ] get_adapter() returns WasmOnlyAdapter on unsupported OS (mock)
  [ ] tests/test_security/test_platform_adapter.py: all tests pass
    (Linux-specific tests skip on non-Linux with pytest.mark.skipif)
```

---

### TASK-041: Implement Platform Security Adapter (macOS + Windows)
```
Phase:    1b
Depends:  TASK-040
Files:
  CREATE netie/security/macos_adapter.py
  CREATE netie/security/windows_adapter.py

Output:
  - MacOSAdapter(BasePlatformAdapter):
      apply():
        Generate sandbox-exec profile from manifest
        Profile denies all by default, allows:
          - network-outbound to manifest.network.hosts
          - file-read-data for manifest.filesystem read paths
          - file-write-data for manifest.filesystem write paths
        Write profile to temp file
        Apply via subprocess sandbox-exec (for child processes)
        Note: sandbox-exec only restricts child processes, not the
          current Python process. Document this limitation clearly.

  - WindowsAdapter(BasePlatformAdapter):
      apply():
        Phase 1b: Wasm-only isolation (no Windows-specific hardening)
        Log info: "Windows: Wasm capability sandbox active.
          Hyperlight sdTEE available in enterprise tier."
        Return Ok(None)

  - WasmOnlyAdapter(BasePlatformAdapter):
      apply(): return Ok(None)  # Wasm itself is the only sandbox
      Log info message about isolation level

AC:
  [ ] MacOSAdapter generates valid sandbox-exec profile (test on macOS)
  [ ] MacOSAdapter.apply() does not raise exception on macOS 14
  [ ] WindowsAdapter.apply() returns Ok(None) on Windows
  [ ] WasmOnlyAdapter.apply() returns Ok(None) on all platforms
  [ ] All adapters implement BasePlatformAdapter interface
  [ ] macOS tests skip on non-macOS with pytest.mark.skipif
```

---

## PHASE 2 TASKS — LOCAL INFERENCE + SOVEREIGN SKILL LIBRARY

---

### TASK-050: Implement Sovereign Skill Library
```
Phase:    2
Depends:  TASK-011
Files:
  CREATE netie/skills/library.py
  CREATE netie/skills/telemetry.py

Input:    sqlite-utils library

Output:
  - SkillLibrary class (SQLite-backed):
      __init__(db_path: Path)
      upsert(card: SkillCard) -> None
      get(skill_id: str) -> SkillCard | None
      all() -> list[SkillCard]
      update_telemetry(skill_id: str, latency_ms: int,
                       token_cost: float, accepted: bool) -> None
        Updates mean_latency_ms, p99_latency_ms, mean_token_cost,
        acceptance_rate using exponential moving average (α=0.1)
        Updates skill weight using RLHF rule from implementation_plan.md:
        new_weight = old_weight + 0.01 * (reward - baseline)
        reward = +1 (accepted) | -1 (rejected) | 0 (no signal)
        baseline = EMA of recent rewards (α=0.1)

  Schema:
    TABLE skills: all SkillCard fields as columns + baseline (float)

AC:
  [ ] upsert + get round-trips SkillCard without data loss
  [ ] update_telemetry with accepted=True increases weight
  [ ] update_telemetry with accepted=False decreases weight
  [ ] weight never goes below 0.01 (floor)
  [ ] weight never goes above 10.0 (ceiling)
  [ ] all() returns skills sorted by weight descending
  [ ] Library persists across process restarts (SQLite file)
  [ ] tests/test_skills/test_library.py: all tests pass
```

---

### TASK-051: Implement FlowMesh CAS
```
Phase:    2
Depends:  TASK-023
Files:
  CREATE netie/cas/store.py

Input:    diskcache library

Output:
  - CASStore class:
      __init__(cache_path: Path, max_size_mb: int = 500)
      get(dag_hash: str, input_hash: str) -> Result[ExecutionResult] | None
      put(dag_hash: str, input_hash: str,
          result: ExecutionResult) -> None
      invalidate(dag_hash: str) -> None  # evict all entries for this DAG
      stats() -> dict  # hit_count, miss_count, size_bytes

  - cache_key(dag_hash: str, input_hash: str) -> str
    SHA-256(dag_hash + ":" + input_hash)

  - Integrate into DAGExecutor:
    Before execution: check CAS with dag.dag_hash + hash(context)
    If hit: return cached result (log "cache hit")
    After execution: store result in CAS

AC:
  [ ] Second identical run returns cached result (verified by checking
    that LLM mock is called 0 times on second run)
  [ ] Cache hit is faster than full execution (duration < 5ms)
  [ ] invalidate() removes all entries for a DAG hash
  [ ] Cache respects max_size_mb (LRU eviction via diskcache)
  [ ] Cache persists across process restarts
  [ ] tests/test_cas/test_store.py: all tests pass
```

---

### TASK-052: Implement Tier 1 Local Inference
```
Phase:    2
Depends:  TASK-023
Files:
  CREATE netie/execution/tier1_inference.py

Input:    llama-cpp-python library (optional extra)

Output:
  - ModelManager class:
      __init__(models_dir: Path)
      is_available(model_name: str) -> bool
      load(model_name: str) -> Result[Llama]
        Load from models_dir/<model_name>.gguf
        Cache loaded model in memory
      download_prompt(model_name: str) -> str
        Return Hugging Face URL + sha256 for known models:
          "phi-3-mini": "microsoft/Phi-3-mini-4k-instruct-gguf"
          "qwen2-1.5b": "Qwen/Qwen2.5-1.5B-Instruct-GGUF"

  - Tier1Client class:
      __init__(manager: ModelManager, config: NettieConfig)
      async infer(prompt: str, model_name: str = None,
                  max_tokens: int = 200) -> Result[str]
        Load model via manager
        Run llama.cpp inference
        Return generated text

  - Update DAGExecutor.execute_node():
    INFER_LOCAL nodes → Tier1Client.infer() instead of Tier2Client.call()
    If Tier1Client fails (model not found): fallback to Tier2 with warning

AC:
  [ ] Tier1Client.infer() works with phi-3-mini model when .gguf present
  [ ] When model not present: falls back to Tier 2, logs warning
  [ ] When llama-cpp-python not installed: falls back to Tier 2, logs warning
  [ ] INFER_LOCAL nodes in DAG now use Tier 1 (verified by checking
    Tier 1 token counter increments, Tier 2 does not)
  [ ] tests/test_execution/test_tier1_inference.py:
    Mock llama.cpp — do not require real model in CI
```

---

### TASK-053: Implement Edge RLHF Accept/Reject Signals
```
Phase:    2
Depends:  TASK-050
Files:
  MODIFY netie/cli.py (add feedback prompt)
  CREATE netie/skills/rlhf.py

Input:    SkillLibrary, ExecutionResult

Output:
  - After `netie run` completes and output is shown:
    Display: "Was this helpful? [y/n/skip]"
    If y: call library.update_telemetry(skill_id, ..., accepted=True)
      for all skill_cards_used in FabricationResult
    If n: call with accepted=False
    If skip/timeout: call with accepted (no signal, reward=0)

  - rlhf.py: RLHFUpdater class:
      batch_update(library: SkillLibrary,
                   skills_used: list[str],
                   accepted: bool | None,
                   latency_ms: int,
                   token_cost: float)
        Calls library.update_telemetry for each skill

AC:
  [ ] User accepts (y) → acceptance_rate increases for used skills
  [ ] User rejects (n) → acceptance_rate decreases for used skills
  [ ] Skipped → acceptance_rate unchanged
  [ ] Feedback prompt only appears when config.enable_telemetry == True
  [ ] tests/test_skills/test_rlhf.py: all tests pass
```

---

## PHASE 3 TASKS — POLICY + PQC TRANSPORT

---

### TASK-060: Implement Python Policy Compiler (Simplified)
```
Phase:    3
Depends:  TASK-017, TASK-018
Files:
  CREATE netie/fabrication/policy_compiler.py

Input:    CompiledDAG, CapabilityManifest, user policies from config

Output:
  - PolicyViolation dataclass: policy_name, node_id, detail
  - PolicyCheckResult dataclass: passed, violations: list[PolicyViolation]

  - PolicyCompiler class:
      __init__(config: NettieConfig)
      check(dag: CompiledDAG,
            manifest: CapabilityManifest) -> PolicyCheckResult
        Run these built-in policies (always enforced):
          1. no_exfiltration_policy(dag, manifest)
          2. bounded_network_policy(dag, manifest,
               max_nodes=config.security.max_dag_nodes)
          3. reversibility_policy(dag, manifest)
        Run user-configured policies from config policies section

  Implement each policy as a standalone function returning
  list[PolicyViolation].

  Integrate into IntentRouter.route() after dead_code elimination,
  before generate_manifest. Abort if PolicyCheckResult.passed == False.

AC:
  [ ] DAG with data flowing from read-path to network-write violates
    NoExfiltration and returns PolicyViolation
  [ ] DAG with 10 TOOL_CALL network_write nodes violates BoundedNetwork
  [ ] Clean DAG passes all policies: PolicyCheckResult.passed == True
  [ ] Policy violations appear in FabricationResult metadata
  [ ] tests/test_fabrication/test_policy_compiler.py: all tests pass
```

---

### TASK-061: Implement PQC-Ready Transport (X25519 Phase)
```
Phase:    3
Depends:  TASK-018
Files:
  MODIFY netie/crypto/transport.py (full implementation)

Input:    cryptography library

Output:
  - TransportSession class:
      __init__()
        Generate ephemeral X25519 private key
      public_key() -> bytes
      derive_session_key(peer_public: bytes) -> bytes
        X25519 ECDH key agreement
        HKDF-SHA256(shared_secret, salt=b"netie-v1", length=32)
      encrypt(plaintext: bytes) -> bytes
        AES-256-GCM with random 12-byte nonce
        Returns nonce + ciphertext + tag (concatenated)
      decrypt(ciphertext: bytes) -> Result[bytes]
        Split nonce + ciphertext + tag
        AES-256-GCM decrypt
        Return Err on authentication failure
      close()
        Zero out private key from memory (memset equivalent)

  - Apply to webhook payloads: encrypt all outbound messages,
    decrypt all inbound messages before processing

AC:
  [ ] encrypt() + decrypt() round-trip is lossless
  [ ] decrypt() with tampered ciphertext returns Err (not exception)
  [ ] close() zeros the private key (verify key bytes are 0x00 after close)
  [ ] Generated keys are unique per session (run 100 times, all unique)
  [ ] tests/test_crypto/test_transport.py: all tests pass
```

---

## FINAL INTEGRATION TASK

---

### TASK-070: End-to-End Integration + README
```
Phase:    All
Depends:  All previous tasks
Files:
  MODIFY README.md (full rewrite with quickstart)
  CREATE ARCHITECTURE.md (simplified §4-§6 from v2 doc)
  CREATE ROADMAP.md
  CREATE .github/workflows/test.yml
  CREATE .github/workflows/release.yml

Output:
  README.md must include:
  - One-liner description
  - Quickstart (5 commands from install to first run)
  - How it works (diagram in ASCII or Mermaid)
  - Token cost benchmark results (from TASK-025 benchmark)
  - Installation: pip install netie-cortex
  - Configuration: NETIE_API_KEY env var
  - All CLI commands with examples

  test.yml CI matrix:
    ubuntu-22.04 + python 3.11, 3.12
    macos-14 + python 3.11, 3.12
    windows-2022 + python 3.11, 3.12
    Runs: pytest tests/ -x --tb=short

  release.yml:
    Triggers on git tag v*.*.*
    Runs tests, then publishes to PyPI via twine

AC:
  [ ] `pip install netie-cortex` installs cleanly in a fresh venv
  [ ] `NETIE_API_KEY=sk-xxx netie run "what is 2+2"` produces output
  [ ] `netie run "summarize https://example.com" --dry-run` shows DAG
  [ ] GitHub Actions CI passes on all 6 matrix targets
  [ ] README quickstart copy-pasteable without modification
  [ ] ARCHITECTURE.md explains the 5 phases clearly in < 500 words
```

---

## TASK EXECUTION ORDER SUMMARY

```
SETUP:    001 → 002 → 003 → 004
PHASE 1:  010 → 011
          012 [PARALLEL with 013]
          013 → 014 → 015 → 016 → 017 → 018
          019 [PARALLEL with 016-018, depends on 015]
          020 (depends on all above)
          021 [PARALLEL with 020]
          022 [PARALLEL with 020]
          023 (depends on 020, 021, 022)
          024 (depends on 020, 023)
          025 (depends on all Phase 1)
PHASE 1b: 030 → 031 → 032
          040 → 041
PHASE 2:  050 → 051
          052 [PARALLEL with 050-051]
          053 (depends on 050)
PHASE 3:  060 (depends on Phase 1 complete)
          061 (depends on Phase 1 complete)
FINAL:    070 (depends on all)
```

---

## NOTES FOR AI CODING AGENTS

1. ALWAYS check if a task's Depends are complete before starting
2. NEVER import from phases not yet implemented — use Optional or stub
3. Use `# TODO(TASK-XXX)` comments for known gaps to fill later
4. Every file must have a module docstring explaining its role
5. Use async/await consistently — synchronous LLM calls are not acceptable
6. NEVER store API keys in code — always from config or env var
7. NEVER log user intents or outputs — privacy by default
8. When in doubt about a data model, refer to TASK-002 as the source of truth
9. Tier 1 inference is OPTIONAL — the system must work without it
10. Wasm isolation is PREFERRED but the system must work without wasmtime
    (graceful degradation, not hard failure)
