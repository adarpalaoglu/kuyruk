# Progress — Kuyruk

Update at the end of every Claude Code session. Plan: `docs/spec.md` (section 12 lists the slices).

## Current slice
**Slice 3 — Jobs API** (not started)

## Slices

| # | Slice | Status | Notes |
|---|---|---|---|
| 0 | Project skeleton, `/health`, `ci.yml` | ☑ | 3 tests, 100% coverage, CI green |
| 1 | Data model + database session | ☑ | 20 tests, 99% coverage; tables checked on real Postgres 16 |
| 2 | Auth | ☑ | 60 tests, 99% coverage; real register/login checked with curl |
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
- `JWT_SECRET` is required (min 32 characters); Kuyruk refuses to start without it. A random one was added to the local `.env`. Tests use their own throwaway secret (set in `tests/conftest.py`), so CI needs none.
- Found while building: SQLModel's `regex=` option on fields is silently ignored by current Pydantic, so the username rule uses Pydantic's `StringConstraints`. Use that pattern for any later text rule too.
- No real route needs login yet; `get_current_user` is tested through a test-only `/whoami` route. Slice 3's job routes will be the first real users of it.
- Review fixes done: an empty value in `.env` (e.g. `DATABASE_URL=`) now means "use the default"; all time columns store UTC and always come back labelled as UTC (`UTCDateTime` in `app/models.py`).
- States and task are stored as plain words (e.g. `queued`) with a database CHECK rule, not Postgres's own enum type.
- Starlette warns that `httpx` for the test client is deprecated in favour of `httpx2`. Harmless (versions are pinned); Adar to decide whether to switch.

## Session log
- 6 Nov 2026 — Planning finished in Claude app. Starting documents created. Tools installed. Decision log D01–D41 approved.
- 6 Nov 2026 — Slice 0 done: uv project (Python 3.12, exact pins + `uv.lock`), folders, `app/settings.py`, `/health` + tests, `.env.example`, `.gitignore`, MIT licence, placeholder README, `ci.yml` (ruff + pytest, 85% gate).
- 6 Nov 2026 — Slice 1 done: `users`/`jobs`/`job_items` tables (SQLModel), fixed state lists, idempotency key unique per owner, `DATABASE_URL` setting, `get_session`, `create_all` on startup, `compose.yaml` with `db` only (Postgres 16.15, digest-pinned). "Code comments" rule added to CLAUDE.md and applied to Slice 0 files.
- 6 Nov 2026 — Slice 2 built (awaiting review/commit): D42 added; open items on `task` list and D42 closed; SQLite now enforces foreign keys (tests + app). D43 added. Auth: `/register` (201, 409, 422; lowercase usernames, D43), `/token` (OAuth2 password flow, JWT via PyJWT, same 401 and same work for wrong password/unknown user; passwords over the maximum refused at once, a review fix), `get_current_user`; Argon2 hashing via pwdlib (FastAPI docs' recommendation); `UserCreate`/`UserPublic`/`Token`; auth settings + `.env.example`.
