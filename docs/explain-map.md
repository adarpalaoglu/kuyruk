# Explain map

Where to look, for Adar's learning phase. For each hard question (spec 14) and each HAND-DESIGNED part of the spec: the files and functions where it lives, the tests that prove it, and the decision-log entries that give the reasons. **Pointers only.** The answers are for Adar to give in his own words.

How to read a pointer: `app/budget.py` `lock_user` means "the function called `lock_user` in the file `app/budget.py`". Decision entries (D14 and so on) are in `docs/decision-log.md`.

## The hard questions (spec 14)

### Why does `async def` with `time.sleep` freeze everyone, while plain `def` doesn't (until the thread pool runs out)?
- Code: `app/experiments.py` `sleep_a`, `sleep_b`, `sleep_c` (the same wait written three ways), and `ai_a`, `ai_b`, `ai_c` (the same with the real AI). `app/main.py` `health`.
- Inside FastAPI: `fastapi/routing.py` `serialize_response` (why a plain `def` route uses a helper thread twice).
- Tests: `tests/test_experiments.py` `test_health_stays_fast_while_all_helper_threads_are_busy`.
- Evidence: `docs/experiments.md` E1 (Results and Surprises, especially B100). Load test: `bench/e1_load.py`.
- Decisions: D95, D96, D97, D98, D99.

### With 4 uvicorn workers, what happens to `BackgroundTasks` jobs, and why doesn't that fix E2?
- Code: `app/experiments_e2.py` `run_in_api` (the broken version). `app/worker.py` `Worker.process_job` (the code both versions run).
- Evidence: `docs/experiments.md` E2, the note "4 uvicorn workers with BackgroundTasks" at the end. Kill script: `bench/e2_kill.py`.
- Decisions: D29, D100, D101.

### What is the key in `dependency_overrides`, and why does overriding `get_session` affect every route?
- Code: `app/db.py` `get_session`, `get_engine`. Routes that ask for it: `SessionDep` in `app/jobs.py` and `app/auth.py`.
- Tests: `tests/conftest.py` (the fixture that sets `app.dependency_overrides[get_session]` and clears it afterwards).
- Decisions: D32.

### Which secrets are available to a fork's pull request, and why is `pull_request_target` dangerous?
- Code: `.github/workflows/ci.yml` (the step that prints `secret present: yes/no`). `.github/workflows/release.yml` (the `if:` conditions on the publish job, and the `workflow_run` trigger).
- Evidence: `docs/experiments.md` E4 part (b), "A pull request from a fork" and "About `pull_request_target`". Data: `bench/data/e4_secrets.csv`.
- Decisions: D35, D36, D37, D84, D109.

### What does `response_model` do to an object that has `hashed_password`?
- Code: `app/auth.py` `register` (its `response_model=UserPublic`). `app/models.py` `User` and `UserPublic`.
- Tests: `tests/test_auth.py` `test_hash_never_appears_in_any_reply`, `test_password_is_stored_only_as_a_hash`.
- Decisions: D21 (the tables), spec 5 (public reply models).

### Why 404 not 403, 202 not 201, SSE not WebSocket, JWT not API keys, SQLite in tests vs Postgres in production?
- 404: `app/jobs.py` `get_own_job`, `job_not_found`. Test: `tests/test_jobs.py` `test_other_users_missing_and_malformed_tickets_get_the_same_404`. Decision D23.
- 202: `app/jobs.py` `create_job` (`status_code=status.HTTP_202_ACCEPTED`). Decision D22.
- SSE: `app/events.py` `job_events`, `watch_job`. Tests: `tests/test_events.py`. Decisions D24, D63 to D67.
- JWT: `app/security.py` `create_access_token`, `read_access_token`. `app/auth.py` `login`, `get_current_user`. Decisions D25, D116.
- SQLite and Postgres: `tests/conftest.py`, the Postgres tests (`tests/test_budget_postgres.py`, `tests/test_recovery_postgres.py`, `tests/test_jobs_postgres.py`), `.github/workflows/ci.yml` (the Postgres service). Decisions D32, D83.

### How does Kuyruk tell a stuck job from a slow one, and why can one text be done twice?
- Code: `app/worker.py` `Worker.heartbeat`, `Worker.beat`, `take_over_stuck_job`, `Worker.hand_back`.
- Tests: `tests/test_recovery.py` `test_a_slow_worker_that_keeps_beating_is_never_taken_over`, `test_a_stale_heartbeat_is_taken_over`, `test_a_wrong_worker_clock_changes_nothing`, `test_redone_texts_are_logged_again_after_a_takeover`. `tests/test_recovery_postgres.py` `test_killed_worker_is_replaced_and_no_text_is_lost`.
- Evidence: `docs/experiments.md` E2 (redone texts, recovery time, stale-time check).
- Decisions: D19, D68, D69, D70, D71, D72, D75, D76.

### How do two simultaneous jobs break a correct budget check, and how does the lock fix it?
- Code: `app/budget.py` `lock_user`, `check_daily_budget`, `tokens_used_today`. `app/jobs.py` `enforce_daily_budget`.
- Tests: `tests/test_budget_postgres.py` `test_two_jobs_at_once_over_budget_only_one_is_accepted` (it fails with the lock switched off, see progress.md Slice 6). `tests/test_budget.py`.
- Decisions: D13, D14, D60, D61.

## The HAND-DESIGNED parts

### The cleanup rules, `normalise` (spec 2.1)
- Code: `app/normalise.py` `normalise`, `unicode_nfc`, `hide_emails_and_phones`, `tidy_spaces`, `turkish_lowercase`, and `prepare_for_ai` (what the AI receives).
- Tests: `tests/test_normalise.py` (one or more tests per rule, for example `test_isparta_icin`, `test_kir_and_kir_stay_different`, `test_email_with_split_turkish_letters_is_fully_hidden`).
- Decisions: D31, D44, D49, D50.

### The daily budget (spec 3.1)
- Code: `app/budget.py` (all of it), `app/jobs.py` `enforce_daily_budget`, `estimate_job`.
- Tests: `tests/test_budget.py`, `tests/test_budget_postgres.py`, `tests/test_estimate.py`.
- Decisions: D11, D13, D14, D57 to D62, D119.

### Job states (spec 4)
- Code: `app/states.py` `check_allowed`, `change_job_state`, `change_item_state`.
- Tests: `tests/test_states.py` `test_tables_match_adars_rules`, `test_forbidden_job_move_is_refused`, `test_allowed_job_move_from_wrong_state_changes_nothing`.
- Decisions: D15, D16, D18, D51, D52, D54.

### The live progress events (spec 6.1)
- Code: `app/events.py` `progress_event`, `state_event`, `recovered_event`, `end_event`, `watch_job`.
- Tests: `tests/test_events.py` `test_job_that_runs_to_done_gives_every_event_in_order`, `test_joining_halfway_starts_with_a_snapshot`, `test_stream_keeps_going_after_the_pass_expires`.
- Decisions: D63, D64, D65, D66, D67, D73.

### Crash recovery (spec 7.1)
- Code: `app/worker.py` `take_over_stuck_job`, `claim_next_job`, `Worker.heartbeat`, `Worker.beat`, `Worker.hand_back`.
- Tests: `tests/test_recovery.py` (all), `tests/test_recovery_postgres.py` `test_two_workers_never_take_the_same_job_or_text`, `test_two_takeovers_at_the_same_instant_only_one_wins`.
- Decisions: D68 to D76.

### The E1 load test (spec 9.1)
- Code: `bench/e1_load.py` `probe_process`, `probe_until`, `send_all_slow`, `run_split`, `summarise`.
- Setup and safeguards: `bench/data/setup.md` (E1 part).
- Decisions: D96, D97, D98.
