# E3 comparison Dockerfile "pip-good" or "pip-slim" (D105): pip, one stage,
# the libraries installed before the code is copied (good layer order).
# pip-good.Dockerfile and pip-slim.Dockerfile are identical except for the
# FROM line: pip-good uses the full Python base image, pip-slim the slim one,
# so the size difference between them is the base image's effect alone (a test
# checks that nothing else differs). pip-slim is measured for size only.
#
# Built only by bench/e3_build.py, from a temporary copy of the project that
# also holds requirements.txt: the exact library versions from uv.lock, made by
# `uv export`, so pip installs the same versions as uv (D105). Never used to
# run Kuyruk for real; the real recipe is the Dockerfile at the repo root.

# The base image, pinned to an exact version and fingerprint (digest).
FROM python:3.12.16-slim-trixie@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54

# A normal (non-root) user, as in the real Dockerfile, so the comparison only
# differs in what E3 measures.
RUN groupadd --system --gid 10001 kuyruk \
    && useradd --system --uid 10001 --gid kuyruk --no-create-home kuyruk

WORKDIR /app

# Print log lines at once; don't write Python cache files at run time.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Good layer order: the library list first, so a change to Kuyruk's code
# reuses the saved install step below instead of redoing it.
COPY requirements.txt ./

# Install the libraries the way a beginner would: plain pip, and pip's download
# cache is left inside the image (no --no-cache-dir).
RUN pip install -r requirements.txt

# Then everything else, including Kuyruk's code.
COPY . .

USER kuyruk

EXPOSE 8000

# Start the API, as the real Dockerfile does.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
