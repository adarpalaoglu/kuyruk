# E3 comparison Dockerfile "uv-good" (D105): the careful way. Slim base, uv,
# two stages, the libraries installed before the code is copied (good layer
# order). The same recipe as the real Dockerfile at the repo root, without its
# labels (descriptions that don't change the image's size or build time).
#
# Built only by bench/e3_build.py from a temporary copy of the project. Never
# used to run Kuyruk for real; the real recipe is the Dockerfile at the repo
# root. Two *stages* (multi-stage build): the first installs the libraries
# with uv, the second copies only the finished result into a fresh slim image,
# so uv and its download cache never ship.

# uv, the tool that installs the libraries; only its program file is used.
FROM ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 AS uv

# ---- Stage 1: builder — install the libraries into /app/.venv --------------
FROM python:3.12.16-slim-trixie@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54 AS builder

COPY --from=uv /uv /usr/local/bin/uv

# Compile the libraries now (faster start), copy rather than link files from
# uv's cache, and use the image's own Python.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Good layer order: the library list first, so a change to Kuyruk's code
# reuses this saved install step instead of redoing it. The cache mount keeps
# uv's downloads between builds without putting them in the image.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

# ---- Stage 2: runtime — the image that actually runs -----------------------
FROM python:3.12.16-slim-trixie@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54

# A normal (non-root) user, as in the real Dockerfile.
RUN groupadd --system --gid 10001 kuyruk \
    && useradd --system --uid 10001 --gid kuyruk --no-create-home kuyruk

WORKDIR /app

# Find the libraries' programs first; print log lines at once; don't write
# Python cache files at run time.
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Only what Kuyruk needs to run: the libraries from stage 1, Kuyruk's code,
# and the waiting times the fake AI replays. Forgetting one of these lines
# still builds, but the container breaks when it runs.
COPY --from=builder /app/.venv /app/.venv
COPY app ./app
COPY bench/data ./bench/data

USER kuyruk

EXPOSE 8000

# Start the API, as the real Dockerfile does.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
