# Progress — Kuyruk

Update at the end of every Claude Code session. Plan: `docs/spec.md` (section 12 lists the slices).

## Current slice
**Slice 7 — SSE progress** (not started)

## Slices

| # | Slice | Status | Notes |
|---|---|---|---|
| 0 | Project skeleton, `/health`, `ci.yml` | ☑ | 3 tests, 100% coverage, CI green |
| 1 | Data model + database session | ☑ | 20 tests, 99% coverage; tables checked on real Postgres 16 |
| 2 | Auth | ☑ | 60 tests, 99% coverage; real register/login checked with curl |
| 3 | Jobs API | ☑ | 147 tests, 99% coverage; real curl run on Postgres 16 |
| 4 | Tasks + fake AI + Ollama client | ☑ | 245 tests, 99% coverage; real Gemma checked on 2 reviews |
| 5 | Worker | ☑ | 330 tests, 99.8% coverage; real Gemma 5-text job and a live cancel checked on Postgres 16 |
| 6 | Budget + lock | ☑ | 368 tests, 99% coverage; Postgres race test passes (and fails with the lock off); real curl run on Postgres 16 |
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
- D44–D49 and the D04 wording update · Approved by Adar: ☑ (7 Nov 2026)
- D50–D56 (worker and cancel) · Approved by Adar: ☑ (7 Nov 2026)
- D57–D62 (daily budget) and the spec 3.1 wording update · Approved by Adar: ☑ (7 Nov 2026)

## Interface parity (fill in at slice 17)
Everything on `/docs` can be done in the interface:
☐ register ☐ log in ☐ send job ☐ idempotency (double-click safe) ☐ job status ☐ list + filter ☐ results + download ☐ live progress ☐ cancel ☐ budget ☐ health/status ☐ crash demo

## Open items / leftovers
- `JWT_SECRET` is required (min 32 characters); Kuyruk refuses to start without it. A random one was added to the local `.env`. Tests use their own throwaway secret (set in `tests/conftest.py`), so CI needs none.
- Found while building: SQLModel's `regex=` option on fields is silently ignored by current Pydantic, so the username rule uses Pydantic's `StringConstraints`. Use that pattern for any later text rule too.
- Review fixes done: an empty value in `.env` (e.g. `DATABASE_URL=`) now means "use the default"; all time columns store UTC and always come back labelled as UTC (`UTCDateTime` in `app/models.py`).
- States and task are stored as plain words (e.g. `queued`) with a database CHECK rule, not Postgres's own enum type.
- Starlette warns that `httpx` for the test client is deprecated in favour of `httpx2`. Harmless (versions are pinned); Adar to decide whether to switch.

- "Character" means one Unicode character as Python counts it. Some emoji (👍🏽, family emoji) count as 2 or more of the 2,000.
- When a job breaks a rule, only the first broken text is named, so 1,000 bad texts still give one short message.
- The timing log measures until the reply *starts*. For SSE (Slice 7) that is not the full stream time.
- `tokens_needed` is now empty (null) until Slice 6 fills it; the local Postgres was wiped once for this (`docker compose down -v`, D42).
- Size limit is 25 MB (Adar's choice after review): it fits even the worst valid job written with `\uXXXX` escapes (1,000 × 2,000 emoji ≈ 24 MB), so no job that follows the text rules is refused for its size.
- Code review: changing `tokens_needed` to nullable breaks a Postgres database made before Slice 3 (no migrations). Accepted as the D42 cost, no code change: wipe with `docker compose down -v`.
- The two-requests-at-once idempotency test on SQLite simulates the race (a lookup that misses once). A real two-at-once test belongs with the Postgres tests in Slice 9.

Slice 4 (limits for the README, and leftovers):
- Code review found emails with Turkish letters stored in two parts were not hidden. Fixed by Adar's new rule order, NFC first (D49); spec 2.1 updated, tests added.
- Phone pattern catches only +90 / 0 followed by 3-3-2-2 digits with spaces, dashes or nothing between. Missed: no leading 0 (532 123 45 67), brackets `(0312)`, dots, a line break inside the number, numbers in words. Email pattern is simple; unusual valid emails may be missed. A user who types `[EMAIL]` or `[TELEFON]` themselves keeps it in capitals.
- Whitespace rule also turns tabs and other Unicode spaces into one space.
- Token counts use tiktoken `o200k_base` (OpenAI's rulebook), an approximation of Gemma's own tokenizer, not the same. Its file is in `app/data/` with a checksum check; the pattern is copied from tiktoken 0.14.0 and a test checks it still matches. In 4 small samples Turkish was not always more tokens than English (10 vs 11, 12 vs 11), so no test assumes it; Slice 15 measures it.
- Real Gemma is *mostly* repeatable, not always: with temperature 0 and seed 42 the same review gave one summary on its first try and a different one on the next 6 tries (which all matched). Experiments must not assume identical summaries. Times: 5.5 s warm-up, then 0.5–1.1 s per short review.
- `ollama_times.csv` (Slice 10) must have one column named `seconds`.
- Fake AI "fail every Nth" counts calls, not texts: a retry is a new call.
- Ollama errors after review (D47): 429 and a reply that breaks off halfway are temporary, like timeouts, unreachable and 5xx.
- `httpx` moved from test-only to a main library (the Ollama client uses it); `tiktoken==0.14.0` added.

Slice 5 (leftovers and notes):
- **For Slice 8:** a stopped worker (Ctrl+C / SIGTERM) leaves its job `running`, with unfinished texts `waiting`. Nothing takes it over until Slice 8's recovery. Texts cut off after the 8 s grace may be done twice (D55, at least once).
- **For Slice 9:** compose must give the worker a stop grace (`stop_grace_period`) above `SHUTDOWN_GRACE_SECONDS` (8 s); Docker's default is 10 s.
- With 4 at once, a cancel can let up to 3 texts beyond the "current" one finish: the worker may have started them a moment before the cancel arrived. They keep their results (seen in the manual check: 8 done at cancel, 12 ok at the end, 8 waiting).
- `count_tokens` results are stored as the number written as text (e.g. "12"), because every result shares one text column.
- The worker's database calls are short and run directly on its loop; only the AI calls are awaited. Fine for one job at a time; E1 measures the API, not the worker.
- Local Postgres had old queued jobs from earlier manual checks; the worker picked them up and finished them on first start.
- Code review fixes: a failed heartbeat is now logged and retried (before, one database blip stopped the job from ever being marked done); if an error ends a job early, texts still with the AI are stopped too (before, they kept running and changed the next job's failures-in-a-row count).
- Manual check (7 Nov 2026): real gemma4:e4b, 5 Turkish reviews, queued → running → done in 9.2 s (≈5 s of it the model warming up), all 5 ok; the email and phone in review 5 were hidden before the AI saw them.

Slice 6 (limits for the README, and leftovers):
- **Limit:** the budget counts only the texts as the AI receives them; the fixed instruction and the AI's answers are not counted, so real use is higher (D57). A job cancelled while running counts in full (D58).
- **For Slice 9:** the Postgres race test (`tests/test_budget_postgres.py`, marked `postgres`) is skipped unless `POSTGRES_TEST_URL` is set. CI needs a Postgres service and that variable. Locally it uses a separate database, `kuyruk_test` (created once with `docker compose exec db createdb -U kuyruk kuyruk_test`), which the test wipes each run. Run it with `POSTGRES_TEST_URL=postgresql+psycopg://kuyruk:<password>@localhost:5432/kuyruk_test uv run pytest -m postgres`.
- Lock proof (7 Nov 2026): with `lock_user` switched off by a throwaway plugin, the race test fails with `[202, 202]` (both accepted); with it on, `[202, 429]`.
- Code review fix: a job checked just before midnight UTC but saved just after it counted on the new day, which never checked it (one job over budget possible). The job is now saved with the moment of the check as its `created_at`; test added.
- `prepare_for_ai` moved from `app/worker.py` to `app/normalise.py`, so the worker and the budget use the same step.
- On POST /jobs the body is read by both the route and `enforce_daily_budget`, so FastAPI reported a broken body twice; the 422 message now drops repeated sentences.
- Manual check (7 Nov 2026, Postgres, `DAILY_TOKEN_BUDGET=100`): 4 jobs of 25 tokens accepted (exactly 100), the 5th refused with 429 and `Retry-After: 45009`; `/me/budget` went from 0 used to 100 used, 0 left; a 150-token job got 422 `job_exceeds_daily_budget`. Those 4 jobs are still queued in the local database; the worker will run them on its next start.

## Session log
- 6 Nov 2026 — Planning finished in Claude app. Starting documents created. Tools installed. Decision log D01–D41 approved.
- 6 Nov 2026 — Slice 0 done: uv project (Python 3.12, exact pins + `uv.lock`), folders, `app/settings.py`, `/health` + tests, `.env.example`, `.gitignore`, MIT licence, placeholder README, `ci.yml` (ruff + pytest, 85% gate).
- 6 Nov 2026 — Slice 1 done: `users`/`jobs`/`job_items` tables (SQLModel), fixed state lists, idempotency key unique per owner, `DATABASE_URL` setting, `get_session`, `create_all` on startup, `compose.yaml` with `db` only (Postgres 16.15, digest-pinned). "Code comments" rule added to CLAUDE.md and applied to Slice 0 files.
- 6 Nov 2026 — Slice 2 built (awaiting review/commit): D42 added; open items on `task` list and D42 closed; SQLite now enforces foreign keys (tests + app). D43 added. Auth: `/register` (201, 409, 422; lowercase usernames, D43), `/token` (OAuth2 password flow, JWT via PyJWT, same 401 and same work for wrong password/unknown user; passwords over the maximum refused at once, a review fix), `get_current_user`; Argon2 hashing via pwdlib (FastAPI docs' recommendation); `UserCreate`/`UserPublic`/`Token`; auth settings + `.env.example`.
- 6 Nov 2026 — Slice 3 done: `POST /jobs` (limits from settings, first broken text named by position, control characters except \n \t \r refused, lone surrogates refused, one transaction, 202 + Location, Idempotency-Key with 409 on different content and race handled by the unique rule), `GET /jobs/{ticket}`, `GET /jobs?status=&limit=&offset=`, `GET /jobs/{ticket}/results`; same 404 for other/missing/malformed tickets; `JobPublic`/`JobItemPublic`; one error shape for every error incl. 500 (`app/errors.py`); request ID + timing + safe 500 + 25 MB body limit with 413 (`app/middleware.py`); `tokens_needed` nullable. Login errors now use the shared shape.
- 7 Nov 2026 — Slice 4 done: `normalise` (Adar's 6 rules, placeholders stay capital), `count_tokens` (tiktoken o200k_base, offline, checksum), AI interface with temporary/permanent errors, fake AI (fixed/replay, fail every Nth, temporary/permanent), Ollama client (think off, temperature 0, seed), `AI_BACKEND` switch (default fake). Manual check with real gemma4:e4b done. Review fixes: NFC first (D49); 429 and cut-off replies temporary (D47). D44–D49 approved.
- 7 Nov 2026 — Slice 5 done: state rules in one place (`app/states.py`, conditional changes, D51), worker (`python -m app.worker`: claim with FOR UPDATE SKIP LOCKED, 4 texts at once, retries 1 s/2 s, 10-in-a-row stop, heartbeat, clean stop with 8 s grace), `POST /jobs/{ticket}/cancel` (200 / 409 `job_finished` / 404), emails and phones hidden before the AI (D50). D50–D56 approved; spec 2.3 updated.
- 7 Nov 2026 — Slice 6 done: 10-in-a-row reading confirmed and Slice 5 review finding 3 accepted (both open items removed). Daily budget: `enforce_daily_budget` on POST /jobs (llm only; replay first; tokens on hidden text; 422 `job_exceeds_daily_budget` if bigger than the whole limit; per-user lock, FOR UPDATE on Postgres; 429 `budget_exceeded` + Retry-After), `GET /me/budget`, `DAILY_TOKEN_BUDGET` setting. Review fix: job saved with the check time (midnight edge). D57–D62 approved; spec 3.1 updated.
