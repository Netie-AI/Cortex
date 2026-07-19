# G1 + G2 — Docker/WSL2 GPU + Ollama/llama.cpp native

**Date:** 2026-07-18  
**Gate:** Unblocks real backends (E1 install managers)  
**Hardware context:** RTX 4070 12 GB, Win11, Docker Desktop + WSL2  
**Status:** RESEARCH COMPLETE — host + Docker GPU smokes passed 2026-07-18 (see Local confirm log). Remaining: WSL-native `nvidia-smi`, `ollama run`, and 8B-AWQ load VRAM.

---

## G1 — vLLM / SGLang via Docker + WSL2 GPU

### Prereq checklist

| Step | Detail | Confirm |
|---|---|---|
| 1 | Windows 11 + WSL2 updated (`wsl --update`) | NEEDS_LOCAL_CONFIRM (WSL version only) |
| 2 | NVIDIA Game Ready / Studio driver with **WSL GPU-PV** support | **CONFIRMED host** — `nvidia-smi` → RTX 4070, 12282 MiB, driver **595.79**; WSL-inside still NEEDS_LOCAL_CONFIRM |
| 3 | Docker Desktop **WSL2 backend** on | **CONFIRMED** — `docker version` → `linux/28.0.4` |
| 4 | Docker Desktop **GPU support** enabled | **CONFIRMED** — `docker run --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi` → `NVIDIA GeForce RTX 4070` |
| 5 | Optional: Docker Desktop **≥ 4.54** if using Docker Model Runner vLLM path | [Docker blog](https://www.docker.com/blog/docker-model-runner-vllm-windows/) |

### Smoke tests

```powershell
# Host
nvidia-smi

# Docker sees GPU
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

**Local confirm log (2026-07-18):** both commands succeeded. Image was pulled fresh (`sha256:0f6bfcbf…`); container reported `NVIDIA GeForce RTX 4070`.

Alternate sample from Docker docs:

```powershell
docker run --rm -it --gpus=all nvcr.io/nvidia/k8s/cuda-sample:nbody nbody -gpu -benchmark
```

### vLLM (official image pattern)

```powershell
docker run --rm -it --gpus all -p 8000:8000 `
  -v ${env:USERPROFILE}\.cache\huggingface:/root/.cache/huggingface `
  vllm/vllm-openai:latest `
  --model Qwen/Qwen2.5-7B-Instruct-AWQ `
  --quantization awq `
  --gpu-memory-utilization 0.90 `
  --max-model-len 8192
```

OpenAI base: `http://127.0.0.1:8000/v1`

**8B AWQ @ 12 GB (guidance):** weights ~4–6 GB; leave headroom for KV. Start `--max-model-len 8192` (or 16384 with `--kv-cache-dtype fp8`). If OOM at load → lower utilization to 0.85 or shorten context. **NEEDS_LOCAL_CONFIRM** exact free VRAM after load.

### SGLang (official image pattern)

```powershell
docker run --rm -it --gpus all -p 30000:30000 `
  -v ${env:USERPROFILE}\.cache\huggingface:/root/.cache/huggingface `
  lmsysorg/sglang:latest `
  python -m sglang.launch_server `
  --model-path Qwen/Qwen2.5-7B-Instruct-AWQ `
  --host 0.0.0.0 --port 30000 `
  --mem-fraction-static 0.85
```

Image tag/`lmsysorg/sglang` naming: **NEEDS_LOCAL_CONFIRM** against current Hub tags (alternate: build from SGLang Dockerfile).

### Docker Model Runner shortcut (optional)

```powershell
docker desktop enable model-runner
docker model install-runner --backend vllm --gpu cuda
docker run --rm --gpus all nvidia/cuda:12.0-base nvidia-smi   # still valid GPU check
```

Useful for demos; Cortex E1 should still own explicit `vllm/vllm-openai` for flag control (B4 matrix).

### Known Win11 + WSL2 failure modes

| Symptom | Likely cause |
|---|---|
| `nvidia-container-cli: initialization error` | Host driver / WSL kernel mismatch — update driver + `wsl --update` |
| Docker has no `--gpus` | WSL2 backend off or GPU feature disabled in Desktop |
| OOM inside container at load | `--gpu-memory-utilization` too high or context too long |
| Slow first token | HF weights download / cold NVMe — mount cache volume |

**Sources:** [CUDA on WSL](https://docs.nvidia.com/cuda/wsl-user-guide/index.html), [Docker Desktop GPU](https://docs.docker.com/desktop/features/gpu/), [Docker Model Runner engines](https://docs.docker.com/ai/model-runner/inference-engines/).

---

## G2 — Ollama / llama.cpp native Windows GPU

### Ollama (personal zero-Docker path)

| Item | Finding |
|---|---|
| Native Win CUDA | Supported on NVIDIA; install from ollama.com Windows build — uses CUDA when `nvidia-smi` works |
| KV cache env | `OLLAMA_KV_CACHE_TYPE=f16\|q8_0\|q4_0` ([FAQ](https://docs.ollama.com/faq)) |
| Flash Attention | `OLLAMA_FLASH_ATTENTION=1` — **required** for quantized KV to actually apply; if FA unsupported → warn + fall back to f16 |
| Set on Windows | System/user env vars → quit tray app → relaunch |
| Layer offload | Automatic; no documented public `OLLAMA_N_GPU_LAYERS` — runner passes `--n-gpu-layers` internally |
| TurboQuant | **Not** in Ollama KV types yet |

**Recommended personal env (4070 12 GB):**

```text
OLLAMA_FLASH_ATTENTION=1
OLLAMA_KV_CACHE_TYPE=q8_0
OLLAMA_MAX_LOADED_MODELS=1
```

### llama.cpp Windows CUDA

| Item | Finding |
|---|---|
| Build | CUDA toolkit + VS Build Tools; or use prebuilt `llama-server` releases with CUDA |
| Partial offload | `-ngl N` / `--n-gpu-layers` |
| mmap | `--mmap` default-ish for weights; `--no-mmap` if working-set fights page cache |
| TurboQuant flags | Forks: `--cache-type-k turbo3 --cache-type-v turbo3` (see C1); mainline may lag |

AirGPT already documents the intended serve line (`F:\AirGPT\docs\local-inference.md`):

```text
llama-server -m model.gguf -fa on -c 16384 \
  --cache-type-k turbo3 --cache-type-v turbo3
```

### Personal vs power path (E1)

| Profile | Path | Backend |
|---|---|---|
| Personal / zero-Docker | Native | Ollama (default) → llama.cpp if TurboQuant needed |
| Business / throughput | Docker WSL2 GPU | vLLM → SGLang |

---

## Back to builder

| Deliverable | Status |
|---|---|
| Prereq checklist | Written + host/Docker GPU confirmed |
| Working docker commands | Written; CUDA 12.4.1 GPU smoke **passed** on box |
| Native personal path + KV flags | Written (`q8_0` + FA) |
| Gate | **YELLOW→GREEN after `ollama run` smoke** (Docker GPU path unblocked) |

**E1 can scaffold managers now** against these commands. Docker/WSL2 GPU path is verified; mark native Ollama "verified" after `ollama run` + optional WSL-inside `nvidia-smi`.
