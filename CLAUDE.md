# CLAUDE.md — Kuyruk

Read this first in every session. Then read `docs/progress.md` (where we are) and only the section of `docs/spec.md` for the current slice.

## What Kuyruk is
A small, well-tested FastAPI job service for long Turkish text jobs, plus four experiments that measure where FastAPI's built-in pieces fail and the smallest fix for each. Full plan: `docs/spec.md`. Decisions and reasons: `docs/decision-log.md`.

## Who decides
- Adar is the director: he makes every decision. Do not change a decision in `docs/decision-log.md` or `docs/spec.md` on your own. If something in the spec seems wrong, stop and explain the problem and the options in plain English.
- Adar is new to Claude Code and the terminal. Whenever you need him to do something (approve a plan, answer a permission question, run a command, check a web page), tell him exactly what to press or type and what he should see.
- Adar is not comfortable reading code. When you explain, use plain English first, short concrete examples, and at most 2–3 lines of code with a plain-English note.

## How we work (one slice per session)
1. Start in plan mode. Read progress.md and the current slice in spec.md. Propose a short plan in plain English: what you will build, which files change, which tests prove it.
2. Wait for approval. Then implement only that slice. No extra features.
3. Show evidence: run the tests and show the result; for endpoints, show one real request and reply.
4. Explain what was built in 5–10 plain sentences, and flag anything Adar must be able to explain himself (marked HAND-DESIGNED in the spec).
5. Update `docs/progress.md` (tick the slice, note anything left over). Commit with a clear message.

## Rules
- Python 3.12, uv for everything (`uv add`, `uv run`). Never pip directly.
- Pin all versions. The finished project must need no maintenance.
- Style: ruff. Run `uv run ruff check . && uv run ruff format .` before committing.
- Tests: pytest. Every new behaviour gets a test, including error cases. Coverage must stay ≥ 85%.
- Fast tests use SQLite and the fake AI; never call real Ollama in tests.
- All adjustable numbers live in `app/settings.py` (read from environment). Never hard-code limits.
- Secrets only in `.env` (git-ignored). Keep `.env.example` up to date with names only. Never print or log a secret.
- Errors always use one shape: `{"error": {"code": "...", "message": "..."}}` plus the request ID header.
- Never return `hashed_password` or any internal field in a reply.
- Another user's job is always 404, never 403.
- The words matter: a **job** is one submission; a **text** is one item inside it; the **worker** is only Kuyruk's separate worker process. Don't reuse these words for other things.
- Don't add Celery, Redis, cloud hosting, or anything not in the spec.

## Commands
- Install: `uv sync`
- Tests: `uv run pytest`
- Run everything: `docker compose up`
- Ollama runs on the Mac itself, not in Docker.
