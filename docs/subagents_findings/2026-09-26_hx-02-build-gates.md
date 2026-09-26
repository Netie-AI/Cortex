# HX-02: hash-locked images and the model egress chokepoint invariant

- **Date:** 2026-09-26
- **Ticket:** HX-02 (EPIC-HARNESS-ENT, #280), PRD R13.1-R13.3, R2.5, R2.6
- **Base:** `claude/cortex-scale-agi-state-fkg2b6` at `03b545f`; branch `claude/cortex-hx-02`
- **Result:** both gates land green on the tree and are proven to go red.

## 1. Supply chain (R13.1-R13.3)

**Expected:** each image installs only exact, hash-checked wheels, then the project with `--no-deps`; `uv.lock` agrees with `pyproject.toml`; litellm 1.82.7 and 1.82.8 can never be installed.

**Actual at 03b545f:**
- `Dockerfile`, `Dockerfile.constructor`, `Dockerfile.core`, `Dockerfile.full` ran `pip install --upgrade pip` and a bare `pip install ".[extra]"` against floors (`litellm>=1.84.0`, no upper pin). Build isolation also fetched `poetry-core` unhashed.
- `uv.lock` recorded `netie` 0.1.0 (pyproject 2.5.0) and 30 requires-dist mismatches (`litellm>=1`, `cryptography>=42`, `fastapi>=0.115`, ...).

**Now:**
- `requirements/image-{constructor,core,full}.lock.txt`, produced only by `python scripts/lock_images.py` (`uv pip compile --generate-hashes --only-binary :all:`, CPython 3.11, x86_64 Linux). Run twice: byte-identical. `requirements/build.in` adds `poetry-core` so the project install uses `--no-build-isolation` with a hashed backend.
- Each Dockerfile: `pip install --only-binary :all: --require-hashes -r requirements/image-<variant>.lock.txt`, then `pip install --no-deps --no-build-isolation <today's extras>`. The unhashed `--upgrade pip` is gone (pip itself comes hashed from the lock).
- `uv lock` regenerated: netie 2.5.0, requires-dist equal to pyproject, `uv lock --check` exits 0. `pyproject.toml` untouched.
- `scripts/check_supply_chain.py` (offline) fails on: project version or any specifier disagreeing between `uv.lock` and pyproject; a lock line without `==` or without a sha256 hash; a declared dependency (or the build backend) missing from an image lock or locked below its floor; a denylisted version in any lock; a Dockerfile `pip install` that is neither `--require-hashes -r <its lock>` nor `--no-deps`. Wired into `ci.yml` next to `check_versions.py`.

**Repro (red on base):** `git checkout 03b545f -- uv.lock Dockerfile.core && python scripts/check_supply_chain.py` gives 34 problems, exit 1. `tests/packaging/test_supply_chain.py` on the base files: 2 failed, 12 errors.

## 2. Egress chokepoint invariant (R2.5)

**Expected:** every provider completion entry point in `CortexOS/**` and `packs/**` sits inside `with ... harness.gate.call(...)`, and provider host literals live only in the exempt-by-design files.

**Actual at 03b545f:** 9 files violate (the gate does not exist yet; HX-03 builds it). All are debt:

| Debt file | Paths | Why |
|---|---|---|
| `crew.txt` (HX-07) | `crew/llm.py` | `litellm.acompletion` at :511, :583, on a local variable named `litellm`, not an import alias |
| | `crew/config.py`, `crew/keys.py` | `integrate.api.nvidia.com` literal (the keys one is a UI hint string) |
| `workflow.txt` (HX-08) | `routing/adapters/{anthropic,openai,vllm}.py` | `litellm.acompletion` |
| `packs.txt` (HX-08) | `packs/dms/generative/brain.py`, `packs/dms/tasks/suggest.py` | Anthropic `messages.create` |
| `fabrication.txt` (HX-08) | `fabrication/hls_compiler.py` | `from litellm import acompletion` |
| `other.txt` | empty | |

`_DEBT_CEILING` is frozen to exactly those 9 paths. The test also fails when a debt line's file is clean or missing (delete the line), misfiled, or duplicated, so debt can only shrink.

**Repro (red):** delete `CortexOS/crew/llm.py` from `crew.txt`, then the invariant fails naming `llm.py:511` and `:583`. Meta-tests plant (in memory and on disk): an ungated call in a function, the alias `f = litellm.acompletion`, `from litellm import ...`, `getattr(litellm, 'acompletion')`, each SDK entry point, a non-gate `with`, a call after the gated block, host literals (including in f-strings), and a debt path outside the ceiling. All fail. Gated plants (`gate.call`, `from ...gate import call`, module alias, fully dotted, relative `from ..integrations.harness import gate`, `async with`) and exempt files pass.

## Root-cause class
- **Unpinned transitive supply chain.** A floor pin plus a bare install means whatever the index serves on build day ships, which is the March 2026 litellm compromise vector. A lock that nobody checks drifts silently (`uv.lock` still said 0.1.0 at 2.5.0).
- **Chokepoint by convention only.** Nothing stopped a new call site from bypassing the future gate.

## Invariants applied
- Prove a gate can fail before trusting it green (both gates shown red above).
- Never hand-author generated artifacts (locks come from `uv`; the debt lists come from the scanner's `__main__`).
- Don't weaken a check to force green (the debt baseline is explicit, frozen and shrink-only).

## Verified vs assumed
- **Verified:** all three images built with docker 29.3.1 from these Dockerfiles; `pip check` is clean in each; litellm 1.102.1, netie 2.5.0; the engine imports with `--network none`; core `/health` is 200. The full suite is 3342 passed, 22 skipped, 4 xfailed, exit 0. ruff, mypy, lint-imports (3 kept), `check_versions.py`, `export_openapi.py --check` and `check_supply_chain.py` all exit 0.
- **Verified caveat:** the sandbox proxy re-signs TLS, so the image builds used scratch copies of each Dockerfile with two extra lines (`COPY` the proxy CA plus `PIP_CERT`). The install lines were byte-identical. The committed Dockerfiles have no CA lines. The unmodified core build failed only on `CERTIFICATE_VERIFY_FAILED`.
- **Assumed:** Hyperlift and release builds are x86_64 (the locks are resolved for x86_64 Linux, CPython 3.11). An arm64 build, or a `PYTHON_VERSION` other than 3.11, would need its own lock.
- **Assumed:** the provider host list in the invariant covers the providers in use today. HX-01 `registry.py` should become the source of truth, and the regex should then be derived from it.

## Flags (not fixed here)
- litellm 1.102.1 fetches its model cost map from `raw.githubusercontent.com` **at import**, which is outbound egress outside any gate. Set `LITELLM_LOCAL_MODEL_COST_MAP=True` in the images (HX-06/HX-07 decision).
- The full image is 10.7 GB because `sentence-transformers` pulls CUDA torch. This is unchanged in behaviour, but a CPU torch index would shrink it.
- `ci.yml` runs on PRs to `main` only, so this draft PR (base: the scale branch) gets no CI run until it reaches main.
