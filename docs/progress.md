# Progress — Kuyruk

Update at the end of every Claude Code session. Plan: `docs/spec.md` (section 12 lists the slices).

## Current slice
**Slice 2 — Auth** (not started)

## Slices

| # | Slice | Status | Notes |
|---|---|---|---|
| 0 | Project skeleton, `/health`, `ci.yml` | ☑ | 3 tests, 100% coverage, CI green |
| 1 | Data model + database session | ☑ | 20 tests, 99% coverage; tables checked on real Postgres 16 |
| 2 | Auth | ☐ | |
| 3 | Jobs API | ☐ | |
| 4 | Tasks + fake AI + Ollama client | ☐ | |
| 5 | Worker | ☐ | |
| 6 | Budget + lock | ☐ | |
| 7 | SSE progress | ☐ | |
| 8 | Crash recovery + two workers | ☐ | |
| 9 | Docker, compose, release workflow | ☐ | |
| 10 | Measure the real AI | ☐ | |
| 11 | E1 | ☐ | Prediction written? ☐ |
| 12 | E2 | ☐ | Prediction written? ☐ |
| 13 | E3 | ☐ | Prediction written? ☐ |
| 14 | E4 | ☐ | Prediction written? ☐ |
| 15 | Extras (TR vs EN, one-command rerun) | ☐ | |
| 16 | Interface mockup approved | ☐ | |
| 17 | Interface build | ☐ | |
| 18 | README, video, write-up, freeze | ☐ | |

## Decision log
- Entries D01–D41 drafted 5–6 Nov 2026 · Approved by Adar: ☑ (6 Nov 2026)

## Interface parity (fill in at slice 17)
Everything on `/docs` can be done in the interface:
☐ register ☐ log in ☐ send job ☐ idempotency (double-click safe) ☐ job status ☐ list + filter ☐ results + download ☐ live progress ☐ cancel ☐ budget ☐ health/status ☐ crash demo

## Open items / leftovers
- **Decision-log entry needed:** tables are created with SQLModel's `create_all` on startup; no migration tool, because the project is frozen. Note: `create_all` only creates *missing* tables and never changes existing ones, so if a table changes before the freeze, the local Postgres volume must be wiped (`docker compose down -v`).
- **Adar's choice (made default in Slice 1):** `task` is also a fixed list (`normalise`, `count_tokens`, `llm`), like the states. Veto if you want it free text.
- **Review leftover (Adar chose to defer):** SQLite does not check that a job's owner / a text's job actually exists (`PRAGMA foreign_keys` is off), while Postgres does. Tests could pass on code that fails on Postgres. Fix later if needed: switch the check on for SQLite connections in `app/db.py`.
- Review fixes done: an empty value in `.env` (e.g. `DATABASE_URL=`) now means "use the default"; all time columns store UTC and always come back labelled as UTC (`UTCDateTime` in `app/models.py`).
- States and task are stored as plain words (e.g. `queued`) with a database CHECK rule, not Postgres's own enum type.
- Starlette warns that `httpx` for the test client is deprecated in favour of `httpx2`. Harmless (versions are pinned); Adar to decide whether to switch.

## Session log
- 6 Nov 2026 — Planning finished in Claude app. Starting documents created. Tools installed. Decision log D01–D41 approved.
- 6 Nov 2026 — Slice 0 done: uv project (Python 3.12, exact pins + `uv.lock`), folders, `app/settings.py`, `/health` + tests, `.env.example`, `.gitignore`, MIT licence, placeholder README, `ci.yml` (ruff + pytest, 85% gate).
- 6 Nov 2026 — Slice 1 done: `users`/`jobs`/`job_items` tables (SQLModel), fixed state lists, idempotency key unique per owner, `DATABASE_URL` setting, `get_session`, `create_all` on startup, `compose.yaml` with `db` only (Postgres 16.15, digest-pinned). "Code comments" rule added to CLAUDE.md and applied to Slice 0 files.
