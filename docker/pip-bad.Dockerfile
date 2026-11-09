# E3 comparison Dockerfile "pip-bad" (D105): the simple way. Full Python
# base image, pip, one stage, and the code copied before the libraries are
# installed (bad layer order).
#
# Built only by bench/e3_build.py, from a temporary copy of the project that
# also holds requirements.txt: the exact library versions from uv.lock, made by
# `uv export`, so pip installs the same versions as uv (D105). Never used to
# run Kuyruk for real; the real recipe is the Dockerfile at the repo root.

# The base image, pinned to an exact version and fingerprint (digest).
FROM python:3.12.16-trixie@sha256:271e78638e5d4e8aeeeb4c1e72cb40c56be3add83a92eaa99b7aab83955121c0

# A normal (non-root) user, as in the real Dockerfile, so the comparison only
# differs in what E3 measures.
RUN groupadd --system --gid 10001 kuyruk \
    && useradd --system --uid 10001 --gid kuyruk --no-create-home kuyruk

WORKDIR /app

# Print log lines at once; don't write Python cache files at run time.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Bad layer order: copy everything first. Docker reuses a saved step (a
# *layer*) only while everything it was built from is unchanged, so any change
# to any file, even one line of code, makes Docker redo the install below.
COPY . .

# Install the libraries the way a beginner would: plain pip, and pip's download
# cache is left inside the image (no --no-cache-dir).
RUN pip install -r requirements.txt

USER kuyruk

EXPOSE 8000

# Start the API, as the real Dockerfile does.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
