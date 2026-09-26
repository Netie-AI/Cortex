# syntax=docker/dockerfile:1
# Public Constructor on /cortex. Auth stays on, as in every shipped image: no
# Dockerfile sets DMS_AUTH_DISABLED (T2-FAILCLOSED, #263). DMS_REFUSE_DEMO_KEYS
# below is kept for older deploy configs and no longer changes anything.
# Hyperlift default path is Dockerfile (identical copy of this file).
ARG PYTHON_VERSION=3.11
FROM python:${PYTHON_VERSION}-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CORTEX_PROFILE=core \
    PACK=dms \
    DMS_REFUSE_DEMO_KEYS=1 \
    PORT=8080
# Hyperlift: set PORT=8080 in the manager if you override it. Pair OpenVault via
# OPENVAULT_BASE_URL, or set DMS_API_KEYS there. Never bake keys into this image.

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY netie ./netie
COPY CortexOS ./CortexOS
COPY packs ./packs
COPY packages ./packages
COPY contract ./contract
COPY data/samples ./data/samples

# Supply chain (R13.1, HX-02): every third-party wheel is exact-pinned and
# hash-checked from requirements/image-constructor.lock.txt (scripts/lock_images.py,
# never hand-edited), then the project goes in with --no-deps. No build
# isolation, so the build backend is the hashed poetry-core from the lock.
COPY requirements/image-constructor.lock.txt ./requirements/
RUN pip install --no-cache-dir --only-binary :all: --require-hashes \
        -r requirements/image-constructor.lock.txt \
    && pip install --no-cache-dir --no-deps --no-build-isolation ".[dms]" \
    && python -c "from CortexOS.dms.warehouse_db import load_inventory_csv; load_inventory_csv()"

# Hyperlift default app port is 8080 (set PORT in Hyperlift Manager, not EXPOSE).
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD curl -fsS http://127.0.0.1:${PORT}/cortex/login || exit 1

CMD ["sh", "-c", "python -m uvicorn CortexOS.api.main:app --host 0.0.0.0 --port ${PORT}"]
