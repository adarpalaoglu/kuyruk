# Progress — Kuyruk

Update at the end of every Claude Code session. Plan: `docs/spec.md` (section 12 lists the slices).

## Current slice
**Slice 1 — Data model + database session** (not started)

## Slices

| # | Slice | Status | Notes |
|---|---|---|---|
| 0 | Project skeleton, `/health`, `ci.yml` | ☑ | 3 tests, 100% coverage, CI green |
| 1 | Data model + database session | ☐ | |
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
- Starlette warns that `httpx` for the test client is deprecated in favour of `httpx2`. Harmless (versions are pinned); Adar to decide whether to switch.

## Session log
- 6 Nov 2026 — Planning finished in Claude app. Starting documents created. Tools installed. Decision log D01–D41 approved.
- 6 Nov 2026 — Slice 0 done: uv project (Python 3.12, exact pins + `uv.lock`), folders, `app/settings.py`, `/health` + tests, `.env.example`, `.gitignore`, MIT licence, placeholder README, `ci.yml` (ruff + pytest, 85% gate).
