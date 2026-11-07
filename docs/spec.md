# Kuyruk — specification

Owner and decision-maker: Adar Palaoğlu. Code: Claude Code. Last updated: 7 November 2026.
Reasons for every decision: `docs/decision-log.md`. Status: `docs/progress.md`.

---

## 1. Goal and question

**Question:** For long AI jobs, where exactly do FastAPI's built-in pieces fail, and what is the smallest fix for each?

Kuyruk is a test bench. The app shows the foundation of AI systems (a tested API, a database, a background worker, CI); the four experiments show where that foundation breaks. It works like the Anthropic/OpenAI batch APIs: a **job** (batch) contains many **texts** (requests); the user gets a **ticket** (batch id) immediately and collects results later.

README line: *"The stack is standard; the experiments and decisions are mine."*

Out of scope (stated in README): Celery/Redis, cloud hosting or an online demo, model training or fine-tuning.

The finished project is **frozen**: all versions pinned, nothing online, nothing that needs upkeep.

---

## 2. Tasks (what Kuyruk does to each text)

One task per job; every text in the job gets the same task. Together the three tasks form a realistic AI pipeline: clean → count → AI.

### 2.1 `normalise` — HAND-DESIGNED (Adar's rules, in this order)
1. Unicode NFC (each letter stored one way). Runs first so an email containing a Turkish letter stored in two parts is still recognised (D49).
2. Replace emails and Turkish phone numbers with `[EMAIL]` and `[TELEFON]` (simple patterns; imperfection noted in limits). Runs before the remaining steps so they can't alter them; NFC never changes the placeholders.
3. Trim start and end; collapse runs of spaces and line breaks into one space.
4. Turkish lowercasing: `I → ı`, `İ → i`, then lowercase the rest (e.g. `ISPARTA İÇİN → ısparta için`). The placeholders `[EMAIL]` and `[TELEFON]` stay in capitals (D44).
5. Keep Turkish letters ç ğ ı ö ş ü (never strip them: `kır` ≠ `kir`). Texts typed without them stay as they are.
6. Keep emoji.

Tests must cover each rule with Turkish examples (ç/ğ/ı/İ/ö/ş/ü, no-diacritic text, emoji, an email, a phone number).

### 2.2 `count_tokens`
Count tokens with tiktoken, one fixed encoding (pin it in settings). Result: the integer count.

### 2.3 `llm`
Send each text to the AI with one fixed instruction: *"Summarise this in one Turkish sentence. Reply with only that sentence."* Users cannot change the instruction. Result: the summary text.

### 2.4 The AI behind `llm`
One setting chooses the AI: `AI_BACKEND = ollama | fake`.
- **ollama:** local Ollama on the Mac (not in Docker), model **Gemma 4 E4B**, exact version pinned in settings and in the experiment setup notes. **Thinking mode must be switched off** on every call (Ollama `think: false`): with it on, the model writes a long hidden reasoning before answering, which adds large, uneven delay. The instruction also says to reply with only the one sentence and nothing else (in testing, the model otherwise added alternative versions).
- **fake:** waits, then returns a fixed answer. Wait mode is either a fixed number of seconds, or **replay**: each call draws a wait from the measured real times (`bench/data/ollama_times.csv`) using a fixed random seed. Can fail on purpose: every Nth text, or every text (for the "AI switched off" test). Failures can be marked temporary (retryable) or permanent.

The rest of Kuyruk must not know which backend it is talking to.

---

## 3. Limits and budget (all numbers in settings)

| Setting | Default |
|---|---|
| Texts per job | 1–1,000 |
| Characters per text | 1–2,000 (empty rejected) |
| Daily AI token budget per user | 200,000 (only `llm` jobs count) |
| Texts at once (per job) | 4 |
| Tries per text | 3 (waits 1 s, then 2 s) |
| Stop job after this many failures in a row | 10 |
| Heartbeat interval / stale after | 5 s / 30 s |
| Max crash count before job fails | 3 |
| Login pass lifetime | 30 min |

Breaking a limit rejects the whole job immediately, with a clear message. Nothing is partly accepted.

### 3.1 Daily budget — HAND-DESIGNED
- Before accepting an `llm` job, count its tokens (same tokenizer as `count_tokens`).
- Tokens used today = sum of tokens of the user's `llm` jobs created today (UTC), excluding failed jobs that never started. Computed each time, never stored as a running total.
- If used + this job > budget → reject with `budget_exceeded`: *"This job needs 12,000 tokens; you have 3,000 left today."*
- **Check and reserve are one locked step per user** (Postgres row lock on the user, or an advisory lock), so two jobs sent at the same moment cannot both pass. Test: send two jobs at once that together exceed the budget; exactly one is accepted.

---

## 4. Job states — HAND-DESIGNED

Job states: `queued → running → done | failed | cancelled`.
Text states: `waiting → ok | failed`.

- **done:** every text has been tried. The job shows counts: total, succeeded, failed (e.g. "497 succeeded, 3 failed"). Partial failure is still `done`.
- **failed:** the job cannot continue: crash count exceeded, or 10 texts failed in a row (message: "AI unavailable").
- **cancelled:** the user cancelled. The worker stops before the next text; finished texts keep their results.
- **Retries:** up to 3 tries per text, only for temporary errors (timeout, AI busy). Permanent errors (a problem with the text itself) fail at once.
- **At least once:** after a crash, the text that was in progress may be redone. Accepted, measured in E2, noted in limits.

Allowed transitions only; anything else is a bug and must be rejected by code and covered by tests.

---

## 5. Data model (SQLModel)

**User:** id, username (unique), hashed_password, created_at.

**Job:** id (ticket, UUID), owner_id, task, state, total, succeeded, failed, tokens_needed, idempotency_key (nullable, unique per owner), created_at, started_at, finished_at, last_active_at, crash_count, claimed_by (worker id, nullable), error_message.

**JobItem (a "text"):** id, job_id, position (1…N), original_text, result, state, tries, error_message.

Results live in the same row as their text. `last_active_at` is updated every heartbeat while the job runs; if it is older than "stale after", the job is stuck, not still working.

Public reply models (`UserPublic`, `JobPublic`, `JobItemPublic`) expose only safe fields. A test proves `hashed_password` never appears in any reply.

---

## 6. API

| Method | Path | Login | What it does |
|---|---|---|---|
| POST | `/register` | no | Create account |
| POST | `/token` | no | Log in (OAuth2 password flow) → JWT pass |
| POST | `/jobs` | yes | Send a job → **202** + ticket. Optional `Idempotency-Key` header: same key twice → same ticket, no new job |
| GET | `/jobs/{ticket}` | yes | State + counts + progress |
| GET | `/jobs?status=` | yes | List own jobs, optional state filter |
| GET | `/jobs/{ticket}/results` | yes | One row per text: position, original, result, state, error |
| GET | `/jobs/{ticket}/events` | yes | Live progress (SSE) |
| POST | `/jobs/{ticket}/cancel` | yes | Cancel |
| GET | `/me/budget` | yes | Tokens used / left today |
| GET | `/health` | no | Instant "alive" reply (E1 and Docker use it) |
| POST | `/demo/crash-worker` | yes | **Demo mode only:** tells the worker to exit abruptly so recovery can be watched. Disabled (404) outside demo mode |

Rules:
- Another user's ticket → **404** (never 403).
- Dependencies: `get_session` (yield) → `get_current_user` → `enforce_daily_budget` (on POST /jobs for `llm`).
- Error shape everywhere: `{"error": {"code": "...", "message": "..."}}`. Custom handler for `budget_exceeded`. Validation errors → 422 in the same shape.
- Middleware: request ID (in/out header `X-Request-ID`) and timing (logged; used in E1).
- CORS: only the interface's origin is allowed. Tests: allowed origin works, another origin is blocked.

### 6.1 SSE event format — HAND-DESIGNED
Events, each as JSON:
- `progress`: `{"done": 212, "total": 500, "succeeded": 210, "failed": 2}` (after each finished text)
- `state`: `{"state": "running"}` (on every state change)
- `recovered`: `{"from_position": 212}` (when a job resumes after a crash)
- `end`: final state; then the stream closes.
The stream keeps going even if the login pass expires during it.

---

## 7. Worker

A separate process (its own container): `python -m app.worker`.
- Polls for `queued` jobs (and stale `running` jobs); **claims** one with a locked update so two workers never take the same job (`SELECT … FOR UPDATE SKIP LOCKED` on Postgres).
- One job at a time per worker; inside a job, up to 4 texts at once (setting).
- One text per AI call.
- Heartbeat: updates `last_active_at` every 5 s.
- Checks for cancel before each text.
- Applies the retry and "10 in a row" rules.

### 7.1 Crash recovery (re-queue logic) — HAND-DESIGNED
On worker start and on each poll:
- A `running` job whose `last_active_at` is stale is **stuck**: increase `crash_count`; if it exceeds 3 → `failed`; otherwise take it over and **continue** from the texts still `waiting` (never restart from text 1). Send a `recovered` event.
- Texts already `ok` or `failed` are never redone; the one in progress at crash time may be redone (at least once).

Test: two workers running against Postgres never process the same job; a killed worker's job is recovered by another and finishes with no lost texts.

---

## 8. Infrastructure

- **Docker Compose:** `api`, `worker`, `db` (Postgres 16), `web` (interface, separate origin), each with a healthcheck; `restart: unless-stopped` on worker (needed for the crash demo). Ollama stays on the Mac; containers reach it at `host.docker.internal:11434`.
- **Dockerfile (production):** multi-stage, uv, slim base, good layer order (dependencies before code).
- **Settings:** `app/settings.py` (pydantic-settings). Secrets (`JWT_SECRET`, `POSTGRES_PASSWORD`) in `.env`; `.env.example` lists names only.
- **Demo mode:** `DEMO_MODE=true` → fake AI in replay mode, crash endpoint enabled, sample Turkish reviews available. `docker compose up` starts in demo mode by default so anyone can try it without Ollama.

### 8.1 Tests
- Fast suite: SQLite + fake AI + `dependency_overrides`; runs on every change.
- Postgres suite (marked `@pytest.mark.postgres`): locking, claiming, budget race, two workers; runs in CI against a Postgres service.
- Streaming test for SSE. Coverage gate 85% (CI fails below).

### 8.2 GitHub Actions
- `ci.yml` (every push and pull request): ruff, fast tests, Postgres tests, coverage gate. uv cache configurable (for E4).
- `release.yml` (push to main only, never pull requests): build image, push to GHCR with `GITHUB_TOKEN`, run the secret check (prints only `secret present: yes/no` for `DEMO_SECRET`).
- Build all four E3 Dockerfiles in CI (for E3's second environment).

---

## 9. Experiments

General protocol (E1, E2): prediction written in `docs/experiments.md` **before** running; broken vs fixed; 5 repeats, interleaved; report median and spread; raw CSVs in `bench/data/`; chart scripts in `bench/`; setup notes (machine, model version, plugged in, nothing else running, warm-up done).

### 9.0 Measuring the real AI (before E1/E2)
- 100 Turkish texts of varied length (own data, no personal data) → per-text time with real Ollama, after warm-up requests that are not counted. Output: `bench/data/ollama_times.csv` + distribution chart.
- Same job at 1 / 2 / 4 / 8 texts at once → total time chart ("where the laptop stops getting faster").

### 9.1 E1 — Blocking the event loop
Same slow AI step written three ways in an experiment-only endpoint/flag: `async def` + `time.sleep` (bad), plain `def` (thread pool), `async def` + `await asyncio.sleep` (good). While 50 jobs run, measure p50/p95 latency of `/health`. Sensitivity: waits 0.5 / 1 / 2 / 4 s. Real-AI check point on the same chart. Include the thread-pool exhaustion case (more slow requests than ~40 threads). E1 load-test script is HAND-DESIGNED (Adar decides what it sends and measures).

### 9.2 E2 — Losing work
Broken: job run with FastAPI `BackgroundTasks` inside the API; kill the API container mid-job → job stuck `running` forever. Fixed: the worker + recovery (section 7.1); kill the worker mid-job. Measure: lost texts, redone texts, recovery time, cost of the fix (extra code, extra database writes). Sensitivity + real-AI check as in E1.

### 9.3 E3 — Image size and rebuild time
2×2: naive pip vs multi-stage uv slim × bad vs good layer order (4 Dockerfiles in `docker/`, same code). Measure size, cold build (cache cleared), rebuild after a one-line code change. 5 repeats, interleaved, median. On the Mac and in GitHub Actions.

### 9.4 E4 — CI
(a) uv cache off vs on, ~10 runs each, job duration. (b) Secret availability: push to main / PR from own branch → recorded; PR from fork → not tested live (no second account), behaviour taken from GitHub's documentation and cited; the check stays in the workflow and records automatically if a fork PR ever arrives. `pull_request_target` explained in the write-up only; never used.

### 9.5 Extras
- Turkish vs English token cost: 50 own Turkish reviews + English translations (made with AI, disclosed) → table and % difference.
- `make experiments` (or one uv command) reruns every experiment and redraws every chart.

---

## 10. Web interface

Two equal ways to use Kuyruk: `/docs` for developers; the web interface for everyone else. **Everything possible on `/docs` must be possible in the interface** (a parity checklist in `docs/progress.md`, checked at the end).

- Look: professional, clean, minimal; one font, one main colour, lots of white space. Responsive (works on phone). Basic accessibility (labels, contrast, keyboard).
- Turkish / English switch.
- Screens: welcome / "How it works" (diagram + three plain sentences) · sign up / log in · new job (paste or upload a file, "Try with sample Turkish reviews", choose task) · my jobs (state filter) · job page (live progress, texts waiting → working → done, plain-language status like "Summarising review 212 of 500…", cancel) · results (original beside result, download CSV/JSON) · budget meter · system status · "Behind the scenes" (the four experiment charts in plain language, "simulate a crash" button showing live recovery) · short hints on each screen.
- Served from its own container/origin (so the CORS test is real).
- Process: Claude (app) designs a mockup of every screen → Adar approves → Claude Code builds it after the backend is finished. Tech choice made in that slice; all versions pinned.

---

## 11. Deliverables

`app/`, `tests/`, `bench/`, `docker/`, `web/`, `compose.yaml`, `.github/workflows/ci.yml` and `release.yml`, `docs/decision-log.md`, `docs/experiments.md`, `docs/progress.md`, README, 1–2 page write-up, MIT licence.

README order: one-sentence summary → GIF → walkthrough video (2–3 min: full flow, Behind the scenes, crash button, real AI) → diagram → run it yourself (`docker compose up`) → API overview → four experiments, one chart and one-line finding each → bonus findings (Turkish cost, texts at once) → key decisions → limits and what I'd do next → "The stack is standard; the experiments and decisions are mine." → credits.

---

## 12. Slices (build in this order; one per Claude Code session)

| # | Slice | Proves |
|---|---|---|
| 0 | Project skeleton: uv, folders, ruff, pytest, settings, `.env.example`, licence, `/health` + test, `ci.yml` (ruff + tests) | Setup works, CI green |
| 1 | Data model + database session (SQLite in tests, Postgres via compose `db`) | Tables exist, sessions work |
| 2 | Auth: `/register`, `/token`, `get_current_user`, public models | Login works; password never returned |
| 3 | Jobs API: `POST /jobs` (validation, limits, 202, idempotency key), `GET` job/list/results, 404 rule, error shape, request-ID + timing middleware | Every route + error case tested |
| 4 | Tasks: `normalise` (Adar's rules), `count_tokens`, fake AI (fixed, replay, failures), Ollama client, backend switch | Each rule and backend tested |
| 5 | Worker: claim, process texts (4 at once), per-text state, retries, 10-in-a-row stop, heartbeat, cancel endpoint | Job runs end to end with fake AI |
| 6 | Budget: `enforce_daily_budget`, `/me/budget`, per-user lock | Budget race test passes on Postgres |
| 7 | SSE `/events` + streaming test | Live progress works |
| 8 | Crash recovery (section 7.1), crash count, two-worker test, demo crash endpoint | No lost texts after a kill |
| 9 | Docker: production Dockerfile, compose (api, worker, db, healthchecks), demo mode default, Postgres tests in CI, `release.yml` to GHCR + secret check | `docker compose up` works on a clean machine |
| 10 | Measure the real AI: 100 texts, warm-up, setup notes, 1/2/4/8 at once, charts | Replay data exists |
| 11 | E1 (write prediction first) | Chart + finding |
| 12 | E2 (write prediction first) | Chart + finding |
| 13 | E3: four Dockerfiles, build script, Mac + CI (prediction first) | Table/chart + finding |
| 14 | E4: cache runs, secret check results (prediction first) | Table + finding |
| 15 | Extras: Turkish vs English cost table; one-command rerun of all experiments | Reproducible |
| 16 | Interface mockup (in Claude app) → Adar's approval | Approved design |
| 17 | Interface build: all screens, TR/EN, CORS, demo mode, crash button, parity check | Everything on /docs works in the interface |
| 18 | Finish: README, diagram, GIF, walkthrough video, write-up, final "done" checklist, freeze versions | Project done |

---

## 13. "Done" checklist

**A. Works:** `docker compose up` starts everything on a clean machine · full user flow (sign up, log in, send, watch, cancel, results) · all three tasks with real and fake AI · limits and budget with clear errors · unfinished jobs carry on after a crash · interface works end to end and matches /docs · demo mode works without Ollama · crash simulation works · walkthrough video and GIF in README.

**B. Tested:** every endpoint incl. error cases · every risk (budget race, double-click, AI off, crash, two workers) · password never returned · coverage ≥ 85% enforced · all tests pass on both databases.

**C. Experiments:** real AI measured (100 texts, setup notes, warm-up) · E1–E4 each with a prediction written first, results and one main chart · E1/E2 sensitivity + real-AI check · one command reruns everything · Turkish vs English table.

**D. Explained:** decision log complete and approved by Adar · README in the order above · write-up done · Adar can answer every hard question in his own words without notes.

**E. Published:** CI publishes the image from main · repo public, MIT licence, no secrets · versions pinned, nothing needs upkeep.

---

## 14. Hard questions Adar must answer unaided
- Why does `async def` + `time.sleep` freeze everyone, while plain `def` doesn't (until the thread pool runs out)?
- With 4 uvicorn workers, what happens to `BackgroundTasks` jobs, and why doesn't that fix E2?
- What is the key in `dependency_overrides`, and why does overriding `get_session` affect every route?
- Which secrets are available to a fork's pull request, and why is `pull_request_target` dangerous?
- What does `response_model` do to an object that has `hashed_password`?
- Why 404 not 403; 202 not 201; SSE not WebSocket; JWT not API keys; SQLite in tests vs Postgres in production?
- How does Kuyruk tell a stuck job from a slow one, and why can one text be done twice?
- How do two simultaneous jobs break a correct budget check, and how does the lock fix it?
