# syntax=docker/dockerfile:1

# uv rather than pip: the lock resolution for ortools, pvlib, shapely and numpy
# is slow enough on a cold builder to matter, and a deploy that times out the
# night before a deadline is a deploy that did not happen.
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# shapely and pvlib need a compiler on some wheels; ca-certificates is required
# for TLS to api.fortyguard.com and would otherwise fail at runtime rather than
# at build time, which is the worst place to discover it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

WORKDIR /app

# Build context is the REPOSITORY ROOT, not engine/. research/ is a runtime
# input - Workstream 3's zone drivers and the observed-validation report - and it
# lives outside engine/, so a context rooted there silently shipped an image
# without either. Locally everything worked; deployed, the Drivers tab was empty
# for every zone and /validation/observed returned 503. Deploy with:
#   fly deploy --config engine/fly.toml --dockerfile engine/Dockerfile
# from the repository root.
COPY engine/pyproject.toml ./
RUN uv pip install --system --no-cache .

COPY engine/thermcue ./thermcue
COPY engine/scripts ./scripts
COPY engine/data ./data

# Read-only to the engine, but it must be present or two of the product's
# evidence surfaces degrade to "unavailable" without saying why.
COPY research /research

# The cache is baked into the image so the judging deployment serves the demo
# even with the network removed, which is exactly what the submission checklist
# asks to be verified.
RUN mkdir -p data/cache

EXPOSE 8000

# A container that is up but cannot answer is worse than one that is down,
# because the platform will keep routing to it.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status==200 else 1)"

CMD ["uvicorn", "thermcue.api:app", "--host", "0.0.0.0", "--port", "8000"]
