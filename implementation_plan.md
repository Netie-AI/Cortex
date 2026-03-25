# Netie Cortex — Implementation Plan
# Open Source Edition (GitHub)
# AI Coding Agent Reference Document

---

## DOCUMENT MAP — Which Sections of the v2 Architecture Doc to Build

The v2 Strategic Architecture document contains ENTERPRISE and OPEN SOURCE content mixed
together. This plan tells you exactly which sections to implement now vs defer.

```
v2 Doc Section                         Open Source?   When
──────────────────────────────────────────────────────────────
Cover / Executive Summary              READ ONLY      n/a
TOC                                    READ ONLY      n/a

PART I  (Sections 1–3)                 SKIP           Competitive analysis only
  Anthropic thought process            SKIP
  Opus Runtime architecture            SKIP
  Opus Runtime weaknesses              SKIP

PART II (Sections 4–16)               BUILD          See phases below

  §4  Design Strategy                  READ ONLY      Guiding principles
  §5  9-Layer Architecture             PARTIAL        Layers 1,2,3 only (Phase 1-2)
  §6  Adversarially Hardened           PARTIAL        §6.1 SkillMesh v2 YES
      Fabrication                                     §6.2 Causal Scanner SIMPLIFIED
                                                      §6.3 Formal Policy DEFER (Phase 3)
  §7  Confidential Computing           DEFER          Phase 4+ / Enterprise only
      (SEV-SNP, TDX, Homomorphic)
  §8  ZK Attestation                   DEFER          Phase 4+ / Enterprise only
  §9  Sovereign Distillation           PHASE 2        Months 4-6
  §10 Adversarial Flywheel             PHASE 2        §10.1 GAN loop simplified
                                                      §10.2 Open Atlas DEFER
  §11 DICE Trust Architecture          DEFER          Enterprise only
  §12 Head-to-Head comparison          READ ONLY      Benchmarking reference
  §13 Updated Roadmap                  ADAPT          See Phase plan below
  §14 Threat Model v2                  REFERENCE      T1-T6 implement, T7-T12 defer
  §15 Known Limitations                REFERENCE      Document in README
  §16 Conclusion                       READ ONLY
  Appendix A                           REFERENCE      Benchmark targets
```

### Page Reference Guide (Approximate)
```
Pages 1–3    Cover, Executive Summary, TOC        — READ
Pages 4–13   Part I (Anthropic analysis)          — SKIP for build
Pages 14–15  §4–§5 Design strategy + arch         — READ
Pages 16–18  §6 Fabrication pipeline              — BUILD (simplified CIS)
Pages 19–21  §7 Confidential Computing            — DEFER
Pages 22–23  §8 ZK Attestation                    — DEFER
Pages 24–25  §9 Sovereign Distillation            — PHASE 2
Pages 26     §10 Adversarial Flywheel             — PHASE 2 (partial)
Pages 27     §11 DICE Trust                       — DEFER
Pages 28–29  §12 Head-to-head matrix              — BENCHMARK REFERENCE
Pages 30–31  §13 Roadmap                          — ADAPT to this plan
Pages 32–33  §14 Threat Model                     — T1-T6 only
Pages 34–35  §15–§16 Limits + Conclusion          — README reference
Page  36     Appendix A                           — BENCHMARK REFERENCE
```

---

## PROJECT OVERVIEW

### What This Is
Netie Cortex is an open source agentic AI runtime. Users install it locally,
bring their own API key (OpenAI, Anthropic, Mistral, etc.), and the system:

1. Takes a natural language task
2. Synthesizes a minimal execution DAG (one LLM call)
3. Executes the DAG locally using their own compute
4. Applies Wasm sandboxing + platform security on execution
5. Learns which skill patterns work best over time

### What You Do NOT Host
- No servers
- No databases
- No model inference infrastructure
- No user data
- Zero recurring cost to the project maintainer

### User's Responsibility
- Their machine runs the runtime
- Their API key pays for synthesis (one call per task)
- Their disk stores local GGUF models (optional, Tier 1)
- Their OS provides kernel-level isolation (Linux Landlock, auto-detected)

---

## TECH STACK

### Core Language
**Python 3.11+** — primary implementation language.
Rationale: fastest iteration, best ML/AI library ecosystem, lowest barrier
for open source contributors.

Optional Rust extensions via PyO3 for performance-critical paths
(DAG compiler, Wasm runtime wrapper) in Phase 3+.

### Key Dependencies
```toml
[tool.poetry.dependencies]
python = "^3.11"

# CLI
typer = "^0.12"
rich = "^13"

# SkillMesh — BM25
rank-bm25 = "^0.2"

# SkillMesh — Dense retrieval
sentence-transformers = "^3"        # downloads 80MB model once
torch = {version="^2", optional=true}  # optional, CPU-only by default
numpy = "^1.26"

# LLM API (multi-provider)
litellm = "^1"                      # wraps OpenAI, Anthropic, Mistral etc.

# Local inference (Tier 1)
llama-cpp-python = {version="^0.2", optional=true}

# Wasm runtime
wasmtime = "^24"                    # official Python bindings

# Config / validation
pydantic = "^2"
pyyaml = "^6"
toml = "^0.10"

# Crypto
cryptography = "^42"                # AES-256-GCM, X25519
pqc-kyber = "^0.1"                  # ML-KEM / Kyber (if available)
                                    # fallback: X25519 only for Phase 1

# Storage
diskcache = "^5"                    # FlowMesh CAS
sqlite-utils = "^3"                 # Sovereign Skill Library

# Testing
pytest = "^8"
pytest-asyncio = "^0.23"
respx = "^0.21"                     # mock HTTP
```

### What NOT to Use
- ❌ LangChain / LlamaIndex — defeats the purpose (replaces the loop we're eliminating)
- ❌ FastAPI server — this is a CLI/library, not a server
- ❌ Docker — user's OS runs it natively
- ❌ Any cloud SDK as a hard dependency

---

## REPOSITORY STRUCTURE

```
netie-cortex/
│
├── README.md
├── ARCHITECTURE.md               # High-level design (from v2 doc §4-§6)
├── CONTRIBUTING.md
├── LICENSE                       # Apache 2.0
├── pyproject.toml
├── .github/
│   └── workflows/
│       ├── test.yml              # pytest on push
│       └── release.yml           # PyPI publish on tag
│
├── netie/                        # Main package
│   ├── __init__.py               # version, public API
│   ├── cli.py                    # typer CLI entrypoint
│   ├── config.py                 # NettieConfig pydantic model
│   │
│   ├── fabrication/              # Layer 1 — Fabrication Pipeline
│   │   ├── __init__.py
│   │   ├── intent_router.py      # Entry point for user intent
│   │   ├── skill_registry.py     # Load/index skill cards from YAML
│   │   ├── skillmesh.py          # BM25 + dense retrieval
│   │   ├── hls_compiler.py       # LLM synthesis call (one shot)
│   │   ├── dsl_parser.py         # Parse Agentic DSL → AST
│   │   ├── dag_compiler.py       # AST → typed DAG IR
│   │   ├── dead_code.py          # Backward reachability pruning
│   │   ├── manifest.py           # CapabilityManifest generation
│   │   └── causal_scanner.py     # Simplified injection detection
│   │
│   ├── execution/                # Layer 2 — Execution
│   │   ├── __init__.py
│   │   ├── executor.py           # DAG executor (main loop)
│   │   ├── wasm_isolate.py       # Wasmtime wrapper per-agent
│   │   ├── tier_router.py        # Route nodes to Tier 0/1/2
│   │   ├── tier0_tools.py        # Deterministic tools (HTTP, regex, etc.)
│   │   ├── tier1_inference.py    # Local llama-cpp-python
│   │   └── tier2_api.py          # LiteLLM remote frontier call
│   │
│   ├── security/                 # Layer 3 — Platform Security Adapter
│   │   ├── __init__.py
│   │   ├── platform.py           # Detect OS, dispatch adapter
│   │   ├── linux_adapter.py      # Landlock + seccomp-bpf
│   │   ├── macos_adapter.py      # sandbox-exec profiles
│   │   ├── windows_adapter.py    # Wasm-only (Hyperlight later)
│   │   └── manifest_enforcer.py  # Apply manifest constraints
│   │
│   ├── crypto/                   # Layer 4 (Phase 1 subset)
│   │   ├── __init__.py
│   │   ├── transport.py          # X25519 + AES-256-GCM
│   │   └── ephemeral_keys.py     # Per-session key derivation
│   │
│   ├── skills/                   # Layer 5 — Sovereign Skill Library
│   │   ├── __init__.py
│   │   ├── library.py            # SQLite-backed skill store
│   │   ├── telemetry.py          # Latency, cost, acceptance tracking
│   │   └── rlhf.py               # Edge weight update algorithm
│   │
│   └── cas/                      # FlowMesh Content-Addressable Store
│       ├── __init__.py
│       └── store.py              # diskcache-backed CAS
│
├── skills/                       # Default skill cards (YAML)
│   ├── _schema.yaml              # Skill card schema definition
│   ├── web_search.yaml
│   ├── code_execution.yaml
│   ├── file_operations.yaml
│   ├── summarization.yaml
│   ├── data_extraction.yaml
│   └── api_call.yaml
│
├── wasm_modules/                 # Pre-compiled Wasm agent modules
│   ├── base_agent.wat            # WAT source
│   └── base_agent.wasm           # Compiled
│
└── tests/
    ├── conftest.py
    ├── test_fabrication/
    │   ├── test_skillmesh.py
    │   ├── test_dag_compiler.py
    │   ├── test_dead_code.py
    │   └── test_manifest.py
    ├── test_execution/
    │   ├── test_executor.py
    │   ├── test_tier_routing.py
    │   └── test_wasm_isolate.py
    ├── test_security/
    │   └── test_platform_adapter.py
    └── fixtures/
        ├── sample_intents.txt
        ├── sample_dags.json
        └── sample_skills.yaml
```

---

## DATA MODELS (Source of Truth)

All models use Pydantic v2. Define these first — everything else builds on them.

### Agentic DSL Node Types
```python
# netie/fabrication/dsl_parser.py

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
```

### DAG Intermediate Representation
```python
# netie/fabrication/dag_compiler.py

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
```

### Capability Manifest
```python
# netie/fabrication/manifest.py

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
```

### Skill Card
```python
# netie/fabrication/skill_registry.py

class SkillCard(BaseModel):
    skill_id: str                    # slug e.g. "web_search_v1"
    name: str
    description: str
    category: str
    example_intents: list[str]       # used for dense retrieval training
    required_tools: list[str]
    required_network: list[str] = []
    max_tier: InferenceTier = InferenceTier.TIER2
    version: str = "1.0.0"
    author: str = "community"

    # Performance telemetry (updated by Sovereign Skill Library)
    mean_latency_ms: float = 0.0
    p99_latency_ms: float = 0.0
    mean_token_cost: float = 0.0
    acceptance_rate: float = 1.0
    weight: float = 1.0             # RLHF-updated retrieval weight
```

---

## PHASE 1 — CORE PIPELINE (Months 1–3)

### Goal
Working CLI that takes a natural language task, synthesizes a DAG, executes it,
and proves token savings on benchmarks. No Wasm yet — that's Phase 1b.

### Success Criteria
- [ ] `netie run "summarize this webpage https://..."` works end-to-end
- [ ] Token usage is logged per run
- [ ] SkillMesh reduces synthesis context to ≤ 2,000 tokens
- [ ] DAG execution is deterministic (same input → same output 100% of time)
- [ ] All tests pass on Ubuntu 22.04, macOS 14, Windows 11

### Components to Build

#### 1a. SkillMesh Retriever
File: `netie/fabrication/skillmesh.py`

```
Input:  user intent string
Output: list[SkillCard] top-K (default K=8)

Algorithm:
  Stage 1 (BM25):
    - Index all skill cards using rank_bm25.BM25Okapi
    - Query with tokenized intent
    - Return top 50 candidates

  Stage 2 (Dense re-ranking):
    - Load sentence-transformers model: "all-MiniLM-L6-v2" (80MB)
    - Encode intent and all 50 candidate descriptions
    - Cosine similarity re-ranking
    - Return top K=8

  Constraint check:
    - Adversarial embedding detector (Phase 1: simple heuristic — check
      cosine sim variance across K results. If stddev > 0.4, flag as
      near decision boundary and reduce K to 3)
```

#### 1b. HLS Compiler (Synthesis)
File: `netie/fabrication/hls_compiler.py`

```
Input:  user intent + list[SkillCard] top-K
Output: AgenticDSLProgram

Steps:
  1. Build synthesis prompt:
     - System: strict DSL-only output instructions
     - Inject top-K skill cards as JSON metadata (target: <2K tokens total)
     - Include DSL grammar specification
     - Require JSON output matching AgenticDSLProgram schema

  2. Call LiteLLM with user's configured model
     litellm.completion(model=config.synthesis_model, messages=[...])
     Temperature: 0.1 (near-deterministic)
     Max tokens: 1000

  3. Parse response:
     - Validate JSON schema
     - On parse failure: retry once with error feedback
     - On second failure: raise SynthesisError

  4. Log token usage to telemetry
```

**Synthesis prompt template (critical — put this in `prompts/synthesis.txt`):**
```
You are a High-Level Synthesis compiler for an agentic runtime.
Your ONLY output is a valid JSON object matching the AgenticDSLProgram schema.
No prose. No explanation. No markdown. Only JSON.

Available skill cards:
{skill_cards_json}

DSL Node Types: TOOL_CALL, INFER_LOCAL, INFER_REMOTE, BRANCH, MERGE, EMIT
Inference Tiers: 0=deterministic_tool, 1=local_model, 2=remote_api

Rules:
- Prefer Tier 0 nodes wherever deterministic tools exist
- Use Tier 1 for summarization, extraction, classification
- Use Tier 2 only for novel synthesis requiring full reasoning
- Every DAG must have exactly one EMIT node
- Declare all network hosts in node annotations
- Declare all filesystem paths in node annotations

User intent: {intent}

Output the DAG JSON now:
```

#### 1c. DSL Parser + DAG Compiler
File: `netie/fabrication/dsl_parser.py`, `dag_compiler.py`

```
Input:  AgenticDSLProgram
Output: CompiledDAG

Steps:
  1. Validate all node references (inputs must reference existing node ids)
  2. Build adjacency list
  3. Topological sort (Kahn's algorithm)
  4. Detect cycles — raise CyclicDAGError if any
  5. Assign depth levels (parallel execution groups)
  6. Compute dag_hash = SHA-256(canonical JSON of sorted nodes)
```

#### 1d. Dead-Code Eliminator
File: `netie/fabrication/dead_code.py`

```
Input:  CompiledDAG
Output: CompiledDAG (with unreachable nodes marked is_reachable=False)

Algorithm:
  1. Start from EMIT node
  2. Walk backwards through edges (reverse topological BFS)
  3. Mark every visited node as reachable
  4. Any unvisited node: set is_reachable=False
  5. Update pruned_nodes count
  6. Remove edges to/from pruned nodes
```

#### 1e. Capability Manifest Generator
File: `netie/fabrication/manifest.py`

```
Input:  CompiledDAG
Output: CapabilityManifest

Steps:
  1. Walk all is_reachable=True nodes
  2. Collect filesystem annotations → FilesystemPermission list
  3. Collect network annotations → NetworkPermission
  4. Collect inference_tier per node_id
  5. Collect model_name → hash from INFER_LOCAL nodes
  6. Set ttl_ms based on node count (base 10s + 2s per node, max 120s)
  7. Sign manifest with ephemeral session key (netie/crypto/ephemeral_keys.py)
```

#### 1f. Simple Causal Injection Scanner
File: `netie/fabrication/causal_scanner.py`

**Phase 1 version** (simplified — full causal mediation is Phase 2):
```
Input:  AgenticDSLProgram + original intent string
Output: ScanResult(passed: bool, flags: list[str], risk_score: float)

Checks (heuristic, no model required):
  1. Privilege escalation check:
     Any TOOL_CALL node with filesystem.write AND also network access?
     → Flag as potential exfiltration pattern

  2. Injection echo check:
     Do any DSL node tool_names contain substrings from common injection
     patterns? ("[INST]", "ignore previous", "system:", "###")
     → Flag as injection parroting

  3. Blast radius check:
     Count INFER_REMOTE nodes. If > 3 in single DAG → Flag as excessive
     remote inference (possible runaway)

  4. Anomalous node count:
     If total nodes > 20 → Flag as over-complex for intent length

  5. Tier violation check:
     Any EMIT node at Tier 2? → Flag (EMIT should be deterministic)

Risk score = weighted sum of flags (0.0 = clean, 1.0 = block)
Threshold: risk_score > 0.6 → refuse synthesis, return error to user
```

#### 1g. DAG Executor (Phase 1 — No Wasm Yet)
File: `netie/execution/executor.py`

```
Phase 1: Execute DAG nodes as plain Python coroutines.
Wasm isolation added in Phase 1b.

Input:  CompiledDAG + CapabilityManifest + user context
Output: ExecutionResult(output: str, token_usage: dict, duration_ms: int)

Algorithm:
  for layer in dag.execution_order:          # each layer is parallel
    results = await asyncio.gather(*[
      execute_node(node, manifest, context)
      for node in layer
      if dag.nodes[node].is_reachable
    ])
    store results in context for downstream nodes

execute_node dispatch:
  Tier 0 TOOL_CALL   → tier0_tools.dispatch(node.tool_name, inputs)
  Tier 1 INFER_LOCAL → tier1_inference.run(model, prompt)
  Tier 2 INFER_REMOTE → tier2_api.call(node.model_hint, prompt)
  BRANCH             → evaluate condition, select path
  MERGE              → concatenate/aggregate inputs
  EMIT               → return final output
```

#### 1h. CLI
File: `netie/cli.py`

```python
# Commands to implement:
netie run "<intent>"              # main execution command
netie run "<intent>" --dry-run    # show DAG without executing
netie run "<intent>" --show-dag   # pretty-print DAG after synthesis
netie config set api_key <key>    # store API key in ~/.netie/config.toml
netie config set model <model>    # e.g. gpt-4o, claude-3-5-sonnet
netie skills list                 # show all indexed skill cards
netie skills add <path.yaml>      # add custom skill card
netie cache clear                 # clear FlowMesh CAS
netie benchmark                   # run standard benchmark suite
```

---

## PHASE 1b — WASM EXECUTION LAYER (Months 2–3, overlaps Phase 1)

### Goal
All agent execution runs inside Wasmtime isolates. Platform security adapter
detects OS and applies maximum available isolation automatically.

### Components

#### Wasm Module Design
File: `wasm_modules/base_agent.wat`

```
The base_agent Wasm module:
- Receives: node instructions + inputs as WASI stdin (JSON)
- Executes: tool call, inference call, or passthrough
- Writes: result to WASI stdout (JSON)
- Has NO filesystem access unless manifest grants it
- Has NO network access unless manifest grants it
- All WASI imports are capability-filtered by the host

One Wasm instance per DAG node (or per parallel group for efficiency).
Instantiation time target: <1ms (AOT-compiled via wasmtime.Engine with
cache=True and strategy="cranelift")
```

#### Wasmtime Python Wrapper
File: `netie/execution/wasm_isolate.py`

```python
# Key interface:
class WasmIsolate:
    def __init__(self, manifest: CapabilityManifest, node_id: str):
        # Configure engine with AOT compilation
        # Apply WASI capability grants from manifest
        # Set memory limits (max 64MB per isolate by default)

    async def execute(self, instructions: dict) -> dict:
        # Load wasm module
        # Write instructions to stdin pipe
        # Run module
        # Read result from stdout pipe
        # Drop linear memory (deterministic deallocation)
        # Return result dict

    def __del__(self):
        # Ensure linear memory is dropped
        # Release WASI handles
```

#### Platform Security Adapter
File: `netie/security/platform.py`

```python
def get_adapter() -> BasePlatformAdapter:
    system = platform.system()
    if system == "Linux":
        if _landlock_available():    # kernel >= 5.13
            return LandlockAdapter()
        else:
            return LinuxBasicAdapter()  # seccomp-bpf only
    elif system == "Darwin":
        return MacOSAdapter()           # sandbox-exec profiles
    elif system == "Windows":
        return WindowsAdapter()         # Wasm-only for now
    else:
        return WasmOnlyAdapter()        # fallback

# LandlockAdapter applies manifest.filesystem as Landlock rules
# BEFORE handing control to WasmIsolate.execute()
# Uses ctypes syscall interface to kernel Landlock API
```

---

## PHASE 2 — LOCAL INFERENCE + SOVEREIGN DISTILLATION (Months 4–6)

### Goal
Tier 1 inference runs locally. Users can distill their own micro-models.
API token cost approaches zero for execution-phase tasks.

### Components

#### Tier 1 Local Inference
File: `netie/execution/tier1_inference.py`

```
Uses llama-cpp-python (llama.cpp Python bindings).
Model management:
  - Models stored in ~/.netie/models/
  - Downloaded on first use from Hugging Face (user prompted to confirm)
  - Default model: microsoft/Phi-3-mini-4k-instruct-gguf (2.2GB INT4)
  - Alternative: Qwen2.5-1.5B-Instruct-GGUF (1.0GB INT4)

Model selection per node:
  - Check manifest.models_required for node_id
  - If not specified, use config.default_local_model
  - If model not downloaded, fallback to Tier 2 with warning

Performance targets:
  - 1.5B model: ~20 tokens/sec on CPU (M1 Mac / Ryzen 5)
  - 3B model:   ~10 tokens/sec on CPU
  - GPU (if available via llama.cpp CUDA): ~100+ tokens/sec
```

#### Distillation Pipeline (§9 of v2 doc, simplified)
File: `netie/skills/distillation.py`

```
Phase 2 implements steps D1-D5 from §9.1:

D1: Sample synthetic pairs from teacher model
    - User specifies: skill_category + teacher_model + num_samples
    - Generate (intent, expected_dsl) pairs via teacher API
    - Filter through CIS (causal scanner)
    - Save to ~/.netie/distillation/{skill_category}/

D2: Fine-tuning via LoRA (Low-Rank Adaptation)
    - Use mlx-lm (Apple Silicon) or trl + PEFT (NVIDIA/CPU)
    - Base model: Phi-3-mini or Qwen2.5-1.5B
    - LoRA rank: 16, alpha: 32
    - Train on filtered pairs

D3: RLHF alignment (simplified — DPO on accept/reject pairs)
    - Collect user accept/reject signals from production runs
    - DPO fine-tune on preference pairs

D4: Benchmark against held-out test set
    - Report quality delta vs teacher

D5: GGUF quantization + signing
    - Convert to GGUF INT4 via llama.cpp convert scripts
    - Sign with user's local key (SHA-256 + Ed25519)
    - Store in ~/.netie/models/distilled/
```

---

## PHASE 3 — FORMAL POLICY + HARDENING (Months 7–9)

### Goal
Formally verified safety policies. Production-grade platform hardening.
Begin PQC transport.

### Components

#### Formal Policy Compiler (§6.3 of v2 doc, simplified)
File: `netie/fabrication/policy_compiler.py`

```
Phase 3 version: implement policy DSL in Python (not full TLA+).
Full TLA+/Alloy integration is Phase 4.

Phase 3 Policy DSL (Python-native):
  - Policies expressed as Python dataclasses with constraint methods
  - Policy checker walks the CompiledDAG and evaluates each constraint
  - Returns: PolicyCheckResult(passed: bool, violations: list[str])

Built-in policies (always enforced, not configurable):
  1. NoExfiltration: data read from user-private paths cannot flow to
     network-write nodes (DAG edge lineage analysis)
  2. BoundedNetworkAccess: max 5 TOOL_CALL network_write nodes per DAG
  3. ReversibilityGuard: irreversible side-effect nodes must have a
     preceding HUMAN_CHECKPOINT if ttl > 10 seconds

User-configurable policies (in ~/.netie/policies.yaml):
  - max_tier2_nodes: int
  - allowed_network_hosts: list[str]
  - require_human_checkpoint: bool
```

#### Hybrid PQC Transport (§6.2 of v2 doc)
File: `netie/crypto/transport.py`

```
Phase 3 — X25519 + AES-256-GCM (classical, but forward-ready)
Phase 4 — Add ML-KEM wrapper when pqc-kyber Python bindings stabilize

Key derivation:
  1. X25519 ephemeral key pair per session
  2. HKDF-SHA256 to derive AES-256-GCM session key
  3. Encrypt webhook payloads and inter-node messages
  4. Keys dropped after session TTL expires

Platform key storage:
  Linux:   libsecret keyring
  macOS:   Keychain Services (via keyring library)
  Windows: Windows Credential Manager (via keyring library)
  Fallback: encrypted file in ~/.netie/keys/ (AES-256 with passphrase)
```

---

## PHASE 4 — ENTERPRISE LAYER (Months 10+, Not Open Source Default)

### These features from the v2 doc are DEFERRED to Phase 4:
- §7: SEV-SNP / TDX Confidential Computing
- §8: ZK-SNARK Execution Receipts
- §11: DICE Multi-Vendor Trust Anchors
- Full TLA+/Alloy formal verification (replaces Phase 3 Python policy)
- Homomorphic inference (OpenFHE/SEAL)

### Delivery model for Phase 4:
- Enterprise-only branch (`netie-enterprise`)
- Requires cloud/server hardware with SEV-SNP/TDX
- Operator self-hosts — still no infrastructure cost to Netie project

---

## BENCHMARKS & SUCCESS METRICS

### Token Cost Benchmark
Compare against baseline (naive GPT-4o call per task step):

| Scenario                     | Baseline tokens | Target (Netie) | Target reduction |
|------------------------------|-----------------|----------------|------------------|
| Web summarization task       | ~8,000          | ~800           | 10×              |
| Code generation task         | ~12,000         | ~600           | 20×              |
| Data extraction pipeline     | ~15,000         | ~300           | 50×              |

### Execution Speed Benchmark
| Component                    | Target          |
|------------------------------|-----------------|
| SkillMesh retrieval latency  | < 50ms          |
| HLS synthesis (LLM call)     | < 3s            |
| DAG compilation              | < 10ms          |
| Wasm instantiation (cold)    | < 5ms           |
| Tier 1 inference (1.5B)      | < 500ms / token |
| Full pipeline (Tier 0 only)  | < 500ms         |

### Security Benchmark
| Control                      | Test                                        |
|------------------------------|---------------------------------------------|
| Wasm filesystem isolation    | Attempt to read /etc/passwd from Wasm module |
| Manifest enforcement         | Attempt network call to undeclared host      |
| Landlock (Linux)             | Attempt path traversal outside declared dirs |
| CIS injection detection      | Run known AdvInjection benchmark corpus      |

---

## CONFIGURATION

### User config: `~/.netie/config.toml`
```toml
[api]
provider = "openai"          # openai | anthropic | mistral | ollama
api_key = ""                 # or set NETIE_API_KEY env var
synthesis_model = "gpt-4o-mini"
tier2_model = "gpt-4o"

[inference]
default_local_model = "phi-3-mini"   # or path to .gguf file
max_context_tokens = 2000            # SkillMesh top-K target
n_gpu_layers = 0                     # 0 = CPU only; -1 = all layers GPU

[security]
enable_landlock = true       # Linux only, auto-disabled if kernel < 5.13
enable_seccomp = true        # Linux only
manifest_ttl_ms = 30000
max_dag_nodes = 20

[skills]
library_path = "~/.netie/skills.db"
user_skills_dir = "~/.netie/skills/"
enable_telemetry = true      # local only, never transmitted

[cache]
cas_path = "~/.netie/cas/"
max_cache_size_mb = 500
cache_ttl_hours = 24
```

---

## ERROR HANDLING CONTRACT

Every public function must follow this contract:

```python
# Use Result type pattern (not exceptions for expected failures)
from dataclasses import dataclass
from typing import TypeVar, Generic

T = TypeVar('T')

@dataclass
class Ok(Generic[T]):
    value: T

@dataclass
class Err:
    code: str          # machine-readable error code
    message: str       # human-readable explanation
    detail: dict = field(default_factory=dict)

Result = Ok[T] | Err

# Error codes
SYNTHESIS_FAILED       = "E001"
INJECTION_DETECTED     = "E002"
MANIFEST_VIOLATION     = "E003"
WASM_ESCAPE_ATTEMPT    = "E004"
MODEL_NOT_FOUND        = "E005"
POLICY_VIOLATION       = "E006"
API_KEY_MISSING        = "E007"
CYCLIC_DAG             = "E008"
TOOL_NOT_FOUND         = "E009"
TIER1_UNAVAILABLE      = "E010"  # fallback to Tier 2 with warning
```

---

## TESTING STRATEGY

### Unit tests — every module has a test file
### Integration tests — full pipeline tests in tests/integration/
### Adversarial tests — injection corpus tests in tests/adversarial/

```python
# Minimum test coverage per module:
# fabrication/skillmesh.py        → 90% coverage
# fabrication/dag_compiler.py     → 95% coverage (correctness critical)
# fabrication/dead_code.py        → 95% coverage
# fabrication/causal_scanner.py   → 80% coverage + adversarial corpus
# execution/executor.py           → 85% coverage
# execution/wasm_isolate.py       → 80% coverage
# security/platform.py            → 70% coverage (OS-specific, mock heavy)
```

### CI matrix
```yaml
os: [ubuntu-22.04, ubuntu-24.04, macos-14, windows-2022]
python: [3.11, 3.12]
# Linux tests additionally run Landlock tests (requires kernel 5.13+)
# macOS tests additionally run sandbox-exec adapter tests
```

---

## OPEN SOURCE LOGISTICS

### License
Apache 2.0 — allows commercial use, modification, distribution.
No copyleft burden on users.

### PyPI Package Name
`netie-cortex` — check availability before publishing.
Install: `pip install netie-cortex`

### Initial GitHub Release Checklist
- [ ] README with 30-second quickstart
- [ ] Working `netie run "summarize https://..."` demo
- [ ] Benchmark results vs plain GPT-4o loop
- [ ] Contributing guide
- [ ] GitHub Actions CI passing on all 3 OS
- [ ] `pip install netie-cortex` works cleanly
- [ ] ARCHITECTURE.md (summarized from v2 doc §4-§6)
- [ ] ROADMAP.md (public version of this plan)
