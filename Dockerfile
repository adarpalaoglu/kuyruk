# Kuyruk's production Dockerfile: the recipe Docker follows to build Kuyruk's
# *image* (a sealed box holding Python, the libraries and Kuyruk's code). The
# same image runs as two containers in compose.yaml: the API and the worker.
# It lives at the repo root because it is the one real Dockerfile; docker/ is
# for experiment E3's four comparison Dockerfiles (D77).
#
# It has two *stages* (multi-stage build): the first installs the libraries
# with uv, the second copies only the finished result into a fresh, small
# image, so uv and its download cache never ship. Every base image is pinned to
# an exact version and its fingerprint (digest), so the build is the same on
# every machine, forever (D41).

# uv, the tool that installs Kuyruk's libraries (the same version as on the Mac
# and in CI). Only its program file is used, in the builder stage.
FROM ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 AS uv

# ---- Stage 1: builder — install the libraries into /app/.venv --------------
# "slim" is a cut-down Debian Linux (here Debian 13, "trixie") with Python.
FROM python:3.12.16-slim-trixie@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54 AS builder

COPY --from=uv /uv /usr/local/bin/uv

# UV_COMPILE_BYTECODE: turn the libraries into Python's ready-to-run form now,
# so containers start faster. UV_LINK_MODE=copy: copy files rather than link
# them to uv's cache, which isn't kept. UV_PYTHON_DOWNLOADS=never: use the
# image's own Python, never download another.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Layer order (D34): Docker saves each step as a *layer* and reuses it while
# its inputs are unchanged. The library list changes rarely, so it comes
# first; a change to Kuyruk's code then reuses this slow step instead of
# installing everything again. --locked: exactly the versions in uv.lock, or
# fail. --no-dev: leave out test and style tools. The cache mount keeps uv's
# downloads between builds on this machine without putting them in the image.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

# ---- Stage 2: runtime — the image that actually runs -----------------------
FROM python:3.12.16-slim-trixie@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54

# A normal (non-root) user with a fixed number, so a bug in Kuyruk can't act
# as the all-powerful "root" user inside the container.
RUN groupadd --system --gid 10001 kuyruk \
    && useradd --system --uid 10001 --gid kuyruk --no-create-home kuyruk

WORKDIR /app

# PATH: find the libraries' programs (uvicorn, and the venv's python) first.
# PYTHONUNBUFFERED: print log lines at once, so `docker compose logs` shows
# them live. PYTHONDONTWRITEBYTECODE: don't try to write cache files into the
# read-only code folder.
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# The libraries from stage 1, then Kuyruk's code. app/ includes
# app/data/o200k_base.tiktoken, the token-counting file (D45); bench/data/
# holds the waiting times the fake AI replays. Files stay owned by root, so the
# kuyruk user can read but not change them.
COPY --from=builder /app/.venv /app/.venv
COPY app ./app
COPY bench/data ./bench/data

USER kuyruk

EXPOSE 8000

# Standard labels (OCI = Open Container Initiative) describing the image.
# GitHub's registry uses "source" to link the published image to the repo,
# and "revision" says exactly which commit it was built from. REVISION is
# filled in by release.yml; local builds say "unknown". Labels come last
# because they change on every commit and nothing comes after them.
ARG REVISION=unknown
LABEL org.opencontainers.image.title="Kuyruk" \
      org.opencontainers.image.description="A small, well-tested FastAPI job service for long Turkish text jobs." \
      org.opencontainers.image.source="https://github.com/adarpalaoglu/kuyruk" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.revision="${REVISION}"

# Start the API. Written as a list (the "exec form"), so uvicorn itself is the
# container's first process and receives Docker's stop signal directly. The
# worker container replaces this with `python -m app.worker` (compose.yaml).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
