"""Frozen image-vs-uv skew baseline (LITELLM-SKEW-01).

The lines below are the mismatch set printed by
``python scripts/check_supply_chain.py --mismatches`` against main 0faede64,
with the three litellm rows removed: constructor 44, core 44, full 67.
``requirements/lock_skew/`` may only shrink. This file is the ceiling,
because that directory and ``scripts/check_supply_chain.py`` are not
protected paths: a commit can add a mismatch and the matching skew line
together and the checker still passes.

Current non-comment lines in each ``requirements/lock_skew/image-<image>.txt``
must be a subset of the literal set for that image. Removing a line passes.
A line that is not in the frozen set fails, and the message names the image
and the line.

This file is under ``tests/invariants/``. A commit that touches it needs
``INVARIANT-CHANGE:`` in the commit body. Lead approved this one-time
addition on #360.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IMAGES = ("constructor", "core", "full")

_CONSTRUCTOR = """\
image-constructor.lock.txt: aiohttp==3.14.4 disagrees with uv.lock aiohttp==3.14.3
image-constructor.lock.txt: annotated-doc==0.0.5 disagrees with uv.lock annotated-doc==0.0.4
image-constructor.lock.txt: annotated-types==0.8.0 disagrees with uv.lock annotated-types==0.7.0
image-constructor.lock.txt: anyio==4.15.1 disagrees with uv.lock anyio==4.14.2
image-constructor.lock.txt: cffi==2.1.1 disagrees with uv.lock cffi==2.1.0
image-constructor.lock.txt: charset-normalizer==3.5.2 disagrees with uv.lock charset-normalizer==3.4.9
image-constructor.lock.txt: click==8.5.0 disagrees with uv.lock click==8.4.2
image-constructor.lock.txt: duckdb==1.5.6 disagrees with uv.lock duckdb==1.5.5
image-constructor.lock.txt: filelock==3.32.7 disagrees with uv.lock filelock==3.32.0
image-constructor.lock.txt: fsspec==2026.9.0 disagrees with uv.lock fsspec==2026.6.0
image-constructor.lock.txt: h2==4.4.1 disagrees with uv.lock h2==4.3.0
image-constructor.lock.txt: hf-xet==1.6.0 disagrees with uv.lock hf-xet==1.5.2
image-constructor.lock.txt: huggingface-hub==1.33.0 disagrees with uv.lock huggingface-hub==1.24.0
image-constructor.lock.txt: idna==3.20 disagrees with uv.lock idna==3.18
image-constructor.lock.txt: jiter==0.17.0 disagrees with uv.lock jiter==0.16.0
image-constructor.lock.txt: markupsafe==3.0.4 disagrees with uv.lock markupsafe==3.0.3
image-constructor.lock.txt: multidict==6.9.1 disagrees with uv.lock multidict==6.7.1
image-constructor.lock.txt: openai==2.54.0 disagrees with uv.lock openai==2.47.0
image-constructor.lock.txt: packaging==26.3 disagrees with uv.lock packaging==26.2
image-constructor.lock.txt: pip==26.2.1 disagrees with uv.lock pip==26.1.2
image-constructor.lock.txt: propcache==0.5.4 disagrees with uv.lock propcache==0.5.2
image-constructor.lock.txt: pydantic==2.13.5 disagrees with uv.lock pydantic==2.13.4
image-constructor.lock.txt: pydantic-core==2.46.5 disagrees with uv.lock pydantic-core==2.46.4
image-constructor.lock.txt: pygments==2.21.0 disagrees with uv.lock pygments==2.20.0
image-constructor.lock.txt: python-dotenv==1.2.4 disagrees with uv.lock python-dotenv==1.2.2
image-constructor.lock.txt: pytz==2026.5 disagrees with uv.lock pytz==2026.2
image-constructor.lock.txt: regex==2026.9.29 disagrees with uv.lock regex==2026.7.19
image-constructor.lock.txt: rpds-py==2026.9.1 disagrees with uv.lock rpds-py==2026.6.3
image-constructor.lock.txt: sqlglot==30.21.0 disagrees with uv.lock sqlglot==30.13.0
image-constructor.lock.txt: sqlite-utils==4.2.1 disagrees with uv.lock sqlite-utils==4.1.1
image-constructor.lock.txt: starlette==1.7.0 disagrees with uv.lock starlette==1.3.1
image-constructor.lock.txt: tiktoken==0.14.0 disagrees with uv.lock tiktoken==0.13.0
image-constructor.lock.txt: tokenizers==0.23.2 disagrees with uv.lock tokenizers==0.22.2
image-constructor.lock.txt: tqdm==4.70.1 disagrees with uv.lock tqdm==4.69.0
image-constructor.lock.txt: typer==0.27.2 disagrees with uv.lock typer==0.27.0
image-constructor.lock.txt: typing-inspection==0.4.4 disagrees with uv.lock typing-inspection==0.4.2
image-constructor.lock.txt: urllib3==2.8.0 disagrees with uv.lock urllib3==2.7.0
image-constructor.lock.txt: uvicorn==0.54.0 disagrees with uv.lock uvicorn==0.51.0
image-constructor.lock.txt: uvloop==0.23.0 disagrees with uv.lock uvloop==0.22.1
image-constructor.lock.txt: wasmtime==49.0.0 disagrees with uv.lock wasmtime==47.0.1
image-constructor.lock.txt: watchfiles==1.3.0 disagrees with uv.lock watchfiles==1.2.0
image-constructor.lock.txt: websockets==17.2 disagrees with uv.lock websockets==16.1.1
image-constructor.lock.txt: yarl==1.25.1 disagrees with uv.lock yarl==1.24.5
image-constructor.lock.txt: zipp==4.1.1 disagrees with uv.lock zipp==4.1.0
"""

_CORE = """\
image-core.lock.txt: aiohttp==3.14.4 disagrees with uv.lock aiohttp==3.14.3
image-core.lock.txt: annotated-doc==0.0.5 disagrees with uv.lock annotated-doc==0.0.4
image-core.lock.txt: annotated-types==0.8.0 disagrees with uv.lock annotated-types==0.7.0
image-core.lock.txt: anyio==4.15.1 disagrees with uv.lock anyio==4.14.2
image-core.lock.txt: cffi==2.1.1 disagrees with uv.lock cffi==2.1.0
image-core.lock.txt: charset-normalizer==3.5.2 disagrees with uv.lock charset-normalizer==3.4.9
image-core.lock.txt: click==8.5.0 disagrees with uv.lock click==8.4.2
image-core.lock.txt: duckdb==1.5.6 disagrees with uv.lock duckdb==1.5.5
image-core.lock.txt: filelock==3.32.7 disagrees with uv.lock filelock==3.32.0
image-core.lock.txt: fsspec==2026.9.0 disagrees with uv.lock fsspec==2026.6.0
image-core.lock.txt: h2==4.4.1 disagrees with uv.lock h2==4.3.0
image-core.lock.txt: hf-xet==1.6.0 disagrees with uv.lock hf-xet==1.5.2
image-core.lock.txt: huggingface-hub==1.33.0 disagrees with uv.lock huggingface-hub==1.24.0
image-core.lock.txt: idna==3.20 disagrees with uv.lock idna==3.18
image-core.lock.txt: jiter==0.17.0 disagrees with uv.lock jiter==0.16.0
image-core.lock.txt: markupsafe==3.0.4 disagrees with uv.lock markupsafe==3.0.3
image-core.lock.txt: multidict==6.9.1 disagrees with uv.lock multidict==6.7.1
image-core.lock.txt: openai==2.54.0 disagrees with uv.lock openai==2.47.0
image-core.lock.txt: packaging==26.3 disagrees with uv.lock packaging==26.2
image-core.lock.txt: pip==26.2.1 disagrees with uv.lock pip==26.1.2
image-core.lock.txt: propcache==0.5.4 disagrees with uv.lock propcache==0.5.2
image-core.lock.txt: pydantic==2.13.5 disagrees with uv.lock pydantic==2.13.4
image-core.lock.txt: pydantic-core==2.46.5 disagrees with uv.lock pydantic-core==2.46.4
image-core.lock.txt: pygments==2.21.0 disagrees with uv.lock pygments==2.20.0
image-core.lock.txt: python-dotenv==1.2.4 disagrees with uv.lock python-dotenv==1.2.2
image-core.lock.txt: pytz==2026.5 disagrees with uv.lock pytz==2026.2
image-core.lock.txt: regex==2026.9.29 disagrees with uv.lock regex==2026.7.19
image-core.lock.txt: rpds-py==2026.9.1 disagrees with uv.lock rpds-py==2026.6.3
image-core.lock.txt: sqlglot==30.21.0 disagrees with uv.lock sqlglot==30.13.0
image-core.lock.txt: sqlite-utils==4.2.1 disagrees with uv.lock sqlite-utils==4.1.1
image-core.lock.txt: starlette==1.7.0 disagrees with uv.lock starlette==1.3.1
image-core.lock.txt: tiktoken==0.14.0 disagrees with uv.lock tiktoken==0.13.0
image-core.lock.txt: tokenizers==0.23.2 disagrees with uv.lock tokenizers==0.22.2
image-core.lock.txt: tqdm==4.70.1 disagrees with uv.lock tqdm==4.69.0
image-core.lock.txt: typer==0.27.2 disagrees with uv.lock typer==0.27.0
image-core.lock.txt: typing-inspection==0.4.4 disagrees with uv.lock typing-inspection==0.4.2
image-core.lock.txt: urllib3==2.8.0 disagrees with uv.lock urllib3==2.7.0
image-core.lock.txt: uvicorn==0.54.0 disagrees with uv.lock uvicorn==0.51.0
image-core.lock.txt: uvloop==0.23.0 disagrees with uv.lock uvloop==0.22.1
image-core.lock.txt: wasmtime==49.0.0 disagrees with uv.lock wasmtime==47.0.1
image-core.lock.txt: watchfiles==1.3.0 disagrees with uv.lock watchfiles==1.2.0
image-core.lock.txt: websockets==17.2 disagrees with uv.lock websockets==16.1.1
image-core.lock.txt: yarl==1.25.1 disagrees with uv.lock yarl==1.24.5
image-core.lock.txt: zipp==4.1.1 disagrees with uv.lock zipp==4.1.0
"""

_FULL = """\
image-full.lock.txt: aiohttp==3.14.4 disagrees with uv.lock aiohttp==3.14.3
image-full.lock.txt: annotated-doc==0.0.5 disagrees with uv.lock annotated-doc==0.0.4
image-full.lock.txt: annotated-types==0.8.0 disagrees with uv.lock annotated-types==0.7.0
image-full.lock.txt: anyio==4.15.1 disagrees with uv.lock anyio==4.14.2
image-full.lock.txt: cffi==2.1.1 disagrees with uv.lock cffi==2.1.0
image-full.lock.txt: charset-normalizer==3.5.2 disagrees with uv.lock charset-normalizer==3.4.9
image-full.lock.txt: click==8.5.0 disagrees with uv.lock click==8.4.2
image-full.lock.txt: cuda-bindings==13.4.3 disagrees with uv.lock cuda-bindings==13.3.1
image-full.lock.txt: cuda-pathfinder==1.8.3 disagrees with uv.lock cuda-pathfinder==1.6.0
image-full.lock.txt: dbos==2.31.1 disagrees with uv.lock dbos==2.28.0
image-full.lock.txt: duckdb==1.5.6 disagrees with uv.lock duckdb==1.5.5
image-full.lock.txt: filelock==3.32.7 disagrees with uv.lock filelock==3.32.0
image-full.lock.txt: fsspec==2026.9.0 disagrees with uv.lock fsspec==2026.6.0
image-full.lock.txt: greenlet==3.5.6 disagrees with uv.lock greenlet==3.5.4
image-full.lock.txt: grpcio==1.84.0 disagrees with uv.lock grpcio==1.82.1
image-full.lock.txt: h2==4.4.1 disagrees with uv.lock h2==4.3.0
image-full.lock.txt: hf-xet==1.6.0 disagrees with uv.lock hf-xet==1.5.2
image-full.lock.txt: huggingface-hub==1.33.0 disagrees with uv.lock huggingface-hub==1.24.0
image-full.lock.txt: idna==3.20 disagrees with uv.lock idna==3.18
image-full.lock.txt: jiter==0.17.0 disagrees with uv.lock jiter==0.16.0
image-full.lock.txt: joblib==1.6.0 disagrees with uv.lock joblib==1.5.3
image-full.lock.txt: markupsafe==3.0.4 disagrees with uv.lock markupsafe==3.0.3
image-full.lock.txt: multidict==6.9.1 disagrees with uv.lock multidict==6.7.1
image-full.lock.txt: narwhals==2.26.0 disagrees with uv.lock narwhals==2.24.0
image-full.lock.txt: nvidia-cudnn-cu13==9.24.0.43 disagrees with uv.lock nvidia-cudnn-cu13==9.20.0.48
image-full.lock.txt: nvidia-nccl-cu13==2.30.7 disagrees with uv.lock nvidia-nccl-cu13==2.29.7
image-full.lock.txt: nvidia-nvjitlink==13.4.92 disagrees with uv.lock nvidia-nvjitlink==13.3.33
image-full.lock.txt: openai==2.54.0 disagrees with uv.lock openai==2.47.0
image-full.lock.txt: packaging==26.3 disagrees with uv.lock packaging==26.2
image-full.lock.txt: pip==26.2.1 disagrees with uv.lock pip==26.1.2
image-full.lock.txt: propcache==0.5.4 disagrees with uv.lock propcache==0.5.2
image-full.lock.txt: protobuf==7.36.2 disagrees with uv.lock protobuf==7.35.1
image-full.lock.txt: psycopg==3.3.6 disagrees with uv.lock psycopg==3.3.4
image-full.lock.txt: psycopg-binary==3.3.6 disagrees with uv.lock psycopg-binary==3.3.4
image-full.lock.txt: pydantic==2.13.5 disagrees with uv.lock pydantic==2.13.4
image-full.lock.txt: pydantic-core==2.46.5 disagrees with uv.lock pydantic-core==2.46.4
image-full.lock.txt: pygments==2.21.0 disagrees with uv.lock pygments==2.20.0
image-full.lock.txt: python-dotenv==1.2.4 disagrees with uv.lock python-dotenv==1.2.2
image-full.lock.txt: pytz==2026.5 disagrees with uv.lock pytz==2026.2
image-full.lock.txt: qdrant-client==1.19.1 disagrees with uv.lock qdrant-client==1.18.0
image-full.lock.txt: regex==2026.9.29 disagrees with uv.lock regex==2026.7.19
image-full.lock.txt: rpds-py==2026.9.1 disagrees with uv.lock rpds-py==2026.6.3
image-full.lock.txt: scikit-learn==1.9.1 disagrees with uv.lock scikit-learn==1.9.0
image-full.lock.txt: sentence-transformers==6.1.0 disagrees with uv.lock sentence-transformers==5.6.0
image-full.lock.txt: setuptools==84.0.0 disagrees with uv.lock setuptools==83.0.0
image-full.lock.txt: sqlalchemy==2.1.3 disagrees with uv.lock sqlalchemy==2.0.51
image-full.lock.txt: sqlglot==30.21.0 disagrees with uv.lock sqlglot==30.13.0
image-full.lock.txt: sqlite-utils==4.2.1 disagrees with uv.lock sqlite-utils==4.1.1
image-full.lock.txt: starlette==1.7.0 disagrees with uv.lock starlette==1.3.1
image-full.lock.txt: tantivy==0.26.2 disagrees with uv.lock tantivy==0.26.0
image-full.lock.txt: threadpoolctl==3.7.0 disagrees with uv.lock threadpoolctl==3.6.0
image-full.lock.txt: tiktoken==0.14.0 disagrees with uv.lock tiktoken==0.13.0
image-full.lock.txt: tokenizers==0.23.2 disagrees with uv.lock tokenizers==0.22.2
image-full.lock.txt: torch==2.14.1 disagrees with uv.lock torch==2.13.0
image-full.lock.txt: tqdm==4.70.1 disagrees with uv.lock tqdm==4.69.0
image-full.lock.txt: transformers==5.18.0 disagrees with uv.lock transformers==5.14.1
image-full.lock.txt: triton==3.8.0 disagrees with uv.lock triton==3.7.1
image-full.lock.txt: typer==0.27.2 disagrees with uv.lock typer==0.27.0
image-full.lock.txt: typing-inspection==0.4.4 disagrees with uv.lock typing-inspection==0.4.2
image-full.lock.txt: urllib3==2.8.0 disagrees with uv.lock urllib3==2.7.0
image-full.lock.txt: uvicorn==0.54.0 disagrees with uv.lock uvicorn==0.51.0
image-full.lock.txt: uvloop==0.23.0 disagrees with uv.lock uvloop==0.22.1
image-full.lock.txt: wasmtime==49.0.0 disagrees with uv.lock wasmtime==47.0.1
image-full.lock.txt: watchfiles==1.3.0 disagrees with uv.lock watchfiles==1.2.0
image-full.lock.txt: websockets==17.2 disagrees with uv.lock websockets==16.1.1
image-full.lock.txt: yarl==1.25.1 disagrees with uv.lock yarl==1.24.5
image-full.lock.txt: zipp==4.1.1 disagrees with uv.lock zipp==4.1.0
"""

def _frozen(image: str) -> frozenset[str]:
    text = {"constructor": _CONSTRUCTOR, "core": _CORE, "full": _FULL}[image]
    return frozenset(line for line in text.splitlines() if line.strip())


def skew_lines(root: Path) -> dict[str, list[str]]:
    """Non-comment lines in each image's lock_skew file, in file order."""
    found: dict[str, list[str]] = {}
    for image in IMAGES:
        path = root / "requirements" / "lock_skew" / f"image-{image}.txt"
        rows: list[str] = []
        for raw in path.read_text(encoding="utf-8").splitlines():
            if not raw.strip() or raw.lstrip().startswith("#"):
                continue
            rows.append(raw.strip())
        found[image] = rows
    return found


def skew_lines_outside_baseline(root: Path) -> list[str]:
    """Named failures for lock_skew lines that are not in the frozen set."""
    problems: list[str] = []
    for image, rows in skew_lines(root).items():
        allowed = _frozen(image)
        for line in rows:
            if line not in allowed:
                problems.append(
                    f"new skew line not in frozen baseline: image-{image}: {line}"
                )
    return problems


def test_lock_skew_is_subset_of_frozen_baseline() -> None:
    """Removals stay inside the ceiling. An added line is named and fails."""
    problems = skew_lines_outside_baseline(ROOT)
    assert problems == [], "\n".join(problems)


def test_removing_a_frozen_line_stays_inside_baseline(tmp_path: Path) -> None:
    skew = tmp_path / "requirements" / "lock_skew"
    skew.mkdir(parents=True)
    for image in IMAGES:
        rows = list(_frozen(image))
        if image == "core":
            rows = rows[1:]
        (skew / f"image-{image}.txt").write_text("\n".join(rows) + "\n", encoding="utf-8")
    assert skew_lines_outside_baseline(tmp_path) == []
