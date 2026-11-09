# Experiments — Kuyruk

The four experiments of spec 9: where FastAPI's built-in pieces fail, and the
smallest fix for each. Every prediction is written here **before** the
experiment runs (spec 9, general protocol) and is never edited afterwards.
Raw data: `bench/data/`. Charts: `docs/charts/`. Setup notes:
`bench/data/setup.md`.

---

## E1 — Blocking the event loop

### Question
The same slow step written three ways: A `async def` + `time.sleep`, B plain
`def` (FastAPI's helper threads), C `async def` + `await asyncio.sleep`. While
50 slow requests run, how fast does `/health` still answer (p50 and p95), and
when do the slow requests finish?

### Prediction
E1 prediction (Adar, 8 Nov 2026, before running): A (async def + time.sleep): The API can't switch to other requests while it waits, so /health gets stuck behind the slow requests. Instead of a few milliseconds it will take seconds, and the unlucky (p95) answers could take tens of seconds, because the waits pile up one after another. B (plain def): The waiting happens on the helper threads, so /health stays fast (milliseconds). But only about 40 threads exist, so with 50 slow requests, 10 must wait for a free thread and take about twice as long to finish. C (async def + await asyncio.sleep): The API switches while waiting, so /health stays in milliseconds and all 50 slow requests finish in about one wait time. Best: C. It stays fast with no limit on how many requests are waiting. B is fine until the threads run out. A freezes everything.

### Method
- **The slow step**, in experiment-only addresses that exist only with
  `EXPERIMENTS_ENABLED=true` (`app/experiments.py`, D95): A
  `async def` + `time.sleep`, B plain `def` + `time.sleep`, C `async def` +
  `await asyncio.sleep`. For the real-AI check, the same three ways around a
  real call to Gemma on Ollama: A a blocking web client (`httpx.Client`)
  inside `async def`, B the same client in plain `def`, C Kuyruk's own async
  client (`OllamaAI`). The blocking client sends exactly Kuyruk's request.
- **The load test** (`bench/e1_load.py`, HAND-DESIGNED, D96): one Kuyruk
  server (one uvicorn process) on the Mac. All slow requests are sent at the
  same moment (time zero). From a separate process, every 100 ms on a fixed
  clock, one request to the real `/health` (unchanged, a plain `def`) and
  one to an `async def` ping, never waiting for earlier ones to answer.
  Probing stops when the last slow request has answered. Recorded: every
  probe's time (sending to full answer) and every slow request's finish time.
- **Runs** (D97): A with 50 slow requests, B with 50, C with 50, and B with
  100 (more than FastAPI's ~40 helper threads), at waits of 0.5 / 1 / 2 /
  4 s; 5 repeats, each repeat in its own shuffled order (80 runs). Real-AI
  check (D98): 10 requests per run, 3 ways × 5 repeats, interleaved, after a
  10-minute heat-up, with Ollama's memory emptied and 3 warm-ups before every
  run (15 runs).
- **Per run**, p50 and p95 of the probe times; below, the median of the 5
  runs, and for p95 the spread (min–max) of the 5 runs.
- **Safeguards** (measure Kuyruk, not the load test): no client connection
  limit, 900 s timeouts, server backlog 4096 with macOS's cap raised to
  4096, open-files limit 10,240, probes from their own process. Clock
  check: 99.5% of the 46,532 probes left within 20 ms of plan; the others
  are described in `bench/data/setup.md`. 0 errors in 95 runs.
- **After the fix** (D99): the same setup with `/health` changed to
  `async def`; B50 and B100 at a 1 s wait, 5 repeats each, interleaved
  (`bench/e1_load.py --after-fix`, 10 runs, 0 errors).
- Mac, versions and the full setup: `bench/data/setup.md` (8 Nov 2026,
  MacBook Air M4, plugged in, Docker closed). Data: `bench/data/e1_*.csv`.
  Charts: `docs/charts/e1_health_p95.png`, `docs/charts/e1_slow_finish.png`.

### Results
`/health` while the slow requests run (median of 5 runs; p95 spread in
brackets). "Last done" = when the last slow request had its answer, from
time zero.

| Wait | Setting | /health p50 | /health p95 | ping p95 | Last done |
|---|---|---|---|---|---|
| 0.5 s | A50 | 15.8 s | 25.8 s (25.7–25.8) | 24.3 s | 25.6 s |
| | B50 | 7.4 ms | 415 ms (406–428) | 5.5 ms | 1.1 s |
| | B100 | 204 ms | 922 ms (913–926) | 6.3 ms | 1.6 s |
| | C50 | 5.5 ms | 6.4 ms (6.1–7.9) | 5.1 ms | 0.6 s |
| 1 s | A50 | 38.7 s | 61.7 s (61.3–62.0) | 48.1 s | 50.6 s |
| | B50 | 8.1 ms | 867 ms (862–875) | 5.8 ms | 2.1 s |
| | B100 | 455 ms | 1,846 ms (1,845–1,868) | 6.5 ms | 3.1 s |
| | C50 | 5.8 ms | 6.4 ms (5.9–7.5) | 5.2 ms | 1.1 s |
| 2 s | A50 | 86.2 s | 132.0 s (126.9–132.3) | 103.0 s | 100.6 s |
| | B50 | 9.2 ms | 1,767 ms (1,760–1,775) | 5.9 ms | 4.1 s |
| | B100 | 978 ms | 3,709 ms (3,691–3,715) | 8.6 ms | 6.1 s |
| | C50 | 5.8 ms | 6.4 ms (6.2–6.8) | 4.9 ms | 2.1 s |
| 4 s | A50 | 161.7 s | 251.9 s (251.6–252.0) | 229.7 s | 200.7 s |
| | B50 | 10.7 ms | 3,578 ms (3,576–3,580) | 8.4 ms | 8.1 s |
| | B100 | 2,013 ms | 7,407 ms (7,385–7,426) | 15.2 ms | 12.1 s |
| | C50 | 6.0 ms | 6.9 ms (6.7–7.7) | 5.1 ms | 4.1 s |

Real-AI check (10 requests to Gemma; one answer alone took a median of
1.2 s, measured in the A runs):

| Way | /health p50 | /health p95 | ping p95 | Last done |
|---|---|---|---|---|
| A (blocking client in `async def`) | 7.6 s | 13.5 s (13.2–13.7) | 13.1 s | 13.9 s |
| B (blocking client in plain `def`) | 5.4 ms | 6.0 ms (6.0–6.2) | 4.5 ms | 13.5 s |
| C (Kuyruk's async client) | 5.4 ms | 6.1 ms (6.0–6.2) | 4.5 ms | 13.7 s |

**After the fix (D99).** E1 led to one change in Kuyruk: `/health` became
`async def`, so it no longer needs a helper thread. Confirmation run, same
setup, B50 and B100 at a 1 s wait, 5 repeats each, interleaved (rows marked
`after_fix` in the data; stars on the chart):

| Setting (1 s wait) | /health p50 | /health p95 | ping p95 | Last done |
|---|---|---|---|---|
| B50, before (plain-def /health) | 8.1 ms | 867 ms (862–875) | 5.8 ms | 2.1 s |
| B50, after (async /health) | 4.8 ms | 5.6 ms (5.3–5.7) | 4.9 ms | 2.1 s |
| B100, before (plain-def /health) | 455 ms | 1,846 ms (1,845–1,868) | 6.5 ms | 3.1 s |
| B100, after (async /health) | 4.6 ms | 6.0 ms (5.7–6.3) | 5.3 ms | 3.1 s |

/health's p95 dropped about 150× (B50) and 300× (B100) and now matches the
async ping. The slow requests themselves finish exactly as before: the fix
helps /health, not the slow step.

The 5 repeats agree closely: every p95 spread is within a few percent of
its median, apart from C's few-millisecond values.

**My prediction, part by part:**

| Prediction | Result | Verdict |
|---|---|---|
| A: /health gets stuck; seconds instead of milliseconds | p50 15.8–162 s | Right |
| A: p95 could take tens of seconds; the waits pile up | p95 26 s at 0.5 s, 62 s at 1 s, 132 s at 2 s, 252 s at 4 s: tens to hundreds of seconds | Right, and worse than predicted |
| B: /health stays fast (milliseconds) | p50 stays fast (7–11 ms), but p95 is 0.4–3.6 s, about 0.9 × the wait. The async ping stays 5–8 ms | Partly wrong |
| B: ~40 threads; with 50 slow requests, 10 wait and take about twice as long | at 1 s: 40 done at 1.1 s, the other 10 at 2.1 s (same pattern at every wait) | Right |
| C: /health stays in milliseconds | p95 6.4–6.9 ms at every wait | Right |
| C: all 50 finish in about one wait time | last done = wait + 0.1 s at every wait | Right |
| Best: C; B fine until the threads run out; A freezes everything | C best at every wait. A froze everything, including the slow requests' own answers (see Surprises). B100 (threads run out): 3 waits, /health p95 ≈ 1.85 × the wait. But B already hurt /health's p95 at 50 requests | Mostly right |
| (real AI, not predicted separately) | A froze /health for the whole job (p95 13.5 s); B and C kept it at 6 ms. All three took the same ~13.5–13.9 s for the 10 texts | — |

### Why it happens
*[Adar writes this in his own words.]*

### Surprises
- **In A, even finished requests can't answer.** 49 of the 50 slow requests
  got their answer only at the very end of the run (at 2 s: 100.6 s, not 2,
  4, 6 … s); one came back after 8–9 waits. With the real AI, all 10 A
  answers arrived together at 13.9 s; in C they arrived one by one from
  0.8 s on.
- **In A, /health's p95 is longer than the whole run** (252 s while the slow
  requests ended at 200.7 s). After the last slow request, the server still
  had about 4,000 waiting probes to answer, which took another 5 s (0.5 s
  wait) to 61 s (4 s wait).
- **In B, /health is slow although the event loop is free.** The async ping
  stays at 5–15 ms in every B run, so the loop is free; /health, a plain
  `def` like the slow step, waits for a helper thread.
- **In B100, nothing finishes after the first wait.** At 1 s, 80 slow
  requests answered at 2.1–2.2 s and 20 at 3.1 s, none at 1 s, although
  only 40 can wait at a time. FastAPI's code shows that a plain `def` route
  uses a helper thread twice: once to run it and once more to check its
  reply before sending it (`serialize_response` in `fastapi/routing.py`).
- **With the real AI, the fix doesn't make the job faster.** All three ways
  took about the same time for the 10 texts (13.5–13.9 s), because Ollama
  answers one at a time on this Mac (D88). The difference is only whether
  the rest of the API stays usable meanwhile.
- **The load test can measure itself.** In a first trial, with the probes
  and the slow requests in one process, a burst of 40 slow replies made a
  probe leave 82 ms late, and C's /health p95 read 14–23 ms instead of 7 ms.
  Hence the separate probe process (D96).

### Takeaway
*[Adar writes this in his own words.]*

---

## E2 — Losing work

### Question
A job is killed halfway. Broken: the job runs with FastAPI `BackgroundTasks`
inside the API, and the API is killed. Fixed: the job runs in Kuyruk's
separate worker with crash recovery, and the worker is killed. How many texts
are lost, how many are done twice, how long until work continues, does the job
ever finish, and what does the fix cost?

### Prediction
E2 prediction (Adar, 8 Nov 2026, before running): Broken (BackgroundTasks inside the API): When the API is killed halfway through a job, the job stays "running" forever, even after the API restarts, because nothing remembers that it was in progress. Every text not finished at the kill is lost (about half the job), and the user waits forever. Fixed (separate worker + recovery): When the worker is killed halfway, no text is lost. Another worker (or the restarted one) notices the job is stuck once the heartbeat has been silent for the stale time, and continues from the first waiting text. Up to 4 texts are done twice: the ones that were with the AI at the moment of the kill. Recovery takes about the stale time (30 s) plus a second or two. Cost of the fix: a separate program and some extra database writes (a heartbeat every 5 s), which is small compared with losing work.

### Method
- **The broken version** (D100): an experiment-only address,
  `POST /experiments/e2/jobs/{ticket}/run-in-api`, which exists only with
  `EXPERIMENTS_ENABLED=true` and never in demo mode (same rules as D95). It
  marks one of your queued jobs as running and hands it to FastAPI
  `BackgroundTasks`, which runs it inside the API process after the reply is
  sent. The texts are done by the worker's own code (`Worker.process_job`),
  so they are processed exactly as in the fixed version: 4 at once, same
  retries, same "text started" log line. In broken runs no worker runs, so
  nothing ever looks for stuck jobs. One more difference: the worker's
  database calls are short blocking calls, which inside the API run on its
  event loop and briefly hold up other requests (the E1 mistake). It changes
  nothing that E2 counts (lost, redone, recovery), and `/health` answered
  after every restart. Because the broken version reuses the worker's code,
  it also writes a heartbeat every 5 s, which nothing ever reads.
- **The fixed version:** Kuyruk's separate worker with crash recovery as
  built in Slice 8 (heartbeat every 5 s, a job is stuck once its heartbeat
  has been silent for 30 s, takeover by compare-and-swap; D68–D76).
- **The kill script** (`bench/e2_kill.py`, D101): everything runs in Docker
  Compose under its own project name (`kuyruk-e2`, own database), with
  production timings. One run: empty the tables, sign up, send a 40-text job
  (the fake AI replays the measured 4-at-once times of Slice 10, median
  7.2 s per text), and read the job straight from the database every 100 ms.
  When 20 of the 40 texts are finished, the program doing the job (the API in
  broken runs, the worker in fixed runs) is killed with a real SIGKILL
  (`docker kill`). Then the script starts the same container again. The
  worker has a restart rule in `compose.yaml`, but Docker's restart rule
  ignores a container stopped with `docker kill`, because it treats that as a
  manual stop, so the script restarting the worker is the correct simulation
  of a real crash restart. The API has no restart rule, so after a real crash
  it would stay down; restarting it gave the broken version its best chance,
  and the job still never continued. (The script first waits 2 s to see
  whether Docker restarted the container itself; it never did. That is why
  "restart" takes about 2.1 s.)
- **Per run:** **lost** = texts not finished at the end, whatever their
  state; **redone** = texts started twice (same job and position in two
  "text started" log lines, D75); **recovery time** = kill → first text
  finished after the kill, also split into kill → takeover and takeover →
  first text; whether the job reaches done; crash count; whether `/health`
  answers after the restart. All times on the Mac's one clock.
- **Runs** (D102), each repeat in its own shuffled order: main, broken vs
  fixed, replayed times, 5 repeats each, broken watched 5 minutes after the
  restart; sensitivity, broken vs fixed at fixed waits of 0.5 / 1 / 2 / 4 s
  per text, 5 repeats each, broken watched 60 s; stale-time check, fixed
  only, "stuck after" 10 / 30 / 60 s, 5 repeats each (65 runs). Real-AI
  check (D103): fixed only, real Gemma on Ollama, a 12-text job killed at 6
  of 12, 5 repeats, after a 10-minute heat-up with Ollama's memory emptied
  and 3 warm-ups before every run.
- **Cost of the fix** (D104): 3 uninterrupted 40-text jobs; database writes
  from Postgres's own counters (`pg_stat_user_tables`: `n_tup_ins` +
  `n_tup_upd` + `n_tup_del`), read before and after each job; heartbeat
  writes = `jobs` updates − one per finished text − 2 (claim and done).
  Lines of code counted by the script from the repo, without comments,
  blank lines or docstrings, and without the demo-only lines in `beat` (the
  demo crash button's check, D74), which belong to demo mode, not to the
  fix.
- **Safeguards:** 0 errors in all 70 runs. Clock check: 1 database look in
  70 runs left more than 20 ms late (36 ms, in a 60 s stale-time run, where
  recovery took 62 s). The same 40 texts in the same order every run.
- Mac, versions and the full setup: `bench/data/setup.md` (9 Nov 2026,
  MacBook Air M4, plugged in, Docker Desktop open). Data:
  `bench/data/e2_runs.csv`, `e2_cost.csv`, `e2_code.csv`. Charts:
  `docs/charts/e2_lost_redone.png`, `docs/charts/e2_recovery.png`.

### Results
Every group of 5 repeats gave the same counts in every run, and times
within 0.2 s of each other (spread in brackets; real AI within 1 s).

**What is lost, and does the job finish?** (killed at 20 of 40 finished)

| Version | Runs | Lost | Redone | Job at the end | Recovery time |
|---|---|---|---|---|---|
| Broken (BackgroundTasks in the API) | 25 | 20 of 40 in every run | 0 | still "running" in every run (watched 5 min in 5 runs, 60 s in 20) | never |
| Fixed (worker + recovery), replayed times | 25 | 0 | 4 in every run | done in every run | 31.8 s (31.7–31.8) |
| Fixed, real AI (12 texts, killed at 6) | 5 | 0 | 4 in every run | done in every run | 26.2 s (26.2–27.2) |

In the broken runs, 24 texts were started: the 20 finished ones and the 4
that were with the AI at the kill. Those 4 were never started again. After
the restart `/health` answered normally in every run, so the API looked
healthy while the job stood still. Crash count stayed 0: nothing noticed the
crash.

**Recovery time vs wait per text** (fixed, stuck after 30 s; median of 5)

| Wait per text | Kill → takeover | Takeover → first text | Recovery | Job done after the kill |
|---|---|---|---|---|
| 0.5 s | 27.8 s | 0.5 s | 28.4 s (28.3–28.4) | 30.5 s |
| 1 s | 29.9 s | 1.0 s | 30.9 s (30.9–30.9) | 35.0 s |
| 2 s | 29.9 s | 2.0 s | 31.9 s (31.8–32.0) | 40.0 s |
| 4 s | 29.9 s | 4.0 s | 33.9 s (33.9–34.0) | 50.0 s |
| replayed (median 7.2 s) | 25.9 s | 5.9 s | 31.8 s (31.7–31.8) | 65.0 s |
| real AI | 25.9 s | 0.3 s | 26.2 s (26.2–27.2) | 33.6 s |

**Recovery time vs stale time** (fixed, replayed times; median of 5)

| Stuck after | Kill → takeover | Recovery | Redone |
|---|---|---|---|
| 10 s | 6.6 s | 12.5 s (12.4–12.5) | 4 |
| 30 s | 25.9 s | 31.7 s (31.7–31.8) | 4 |
| 60 s | 56.2 s | 62.0 s (61.9–62.1) | 4 |

**Cost of the fix**

| | |
|---|---|
| Database writes for one uninterrupted 40-text job (75.8 s) | 139 (same in all 3 jobs) |
| of which heartbeat | 15 (10.8%), exactly one per 5 s of running |
| Worker (`app/worker.py`), lines of code | 462 |
| of which crash recovery (takeover, heartbeat, hand back; demo-only lines left out) | 105 |

The heartbeat's writes are the cost compared with a plain BackgroundTasks
job, which has no heartbeat. (E2's broken version also writes one, because
it reuses the worker's code, D100; there nothing ever reads it.) Plus one
more program to run: the worker's container.

**My prediction, part by part:**

| Prediction | Result | Verdict |
|---|---|---|
| Broken: the job stays "running" forever, even after the API restarts | "running" in all 25 runs, still after 5 minutes; crash count 0 | Right |
| Broken: every text not finished at the kill is lost (about half) | exactly 20 of 40 lost in every run, including the 4 that were with the AI | Right |
| Broken: the user waits forever | the job never moves again; `/health` says the API is fine | Right |
| Fixed: no text is lost | 0 lost in all 30 fixed runs (25 fake AI, 5 real AI) | Right |
| Fixed: the restarted worker notices the job once the heartbeat has been silent for the stale time, and continues from the first waiting text | takeover in every run; crash count 1; the job finished with all texts done | Right |
| Fixed: up to 4 texts done twice, the ones with the AI at the kill | exactly 4 in every run | Right (always the full 4) |
| Recovery: about the stale time (30 s) plus a second or two | 26.2–34.0 s. Takeover came 25.9–29.9 s after the kill, sometimes before 30 s; then the first text took one AI wait (0.3–5.9 s) | Partly right |
| Cost: a separate program and some extra database writes (heartbeat every 5 s), small compared with losing work | one more container; 15 of 139 writes (10.8%) per job compared with a job with no heartbeat; 105 lines of recovery code | Right |

### Why it happens
<!-- draft: Adar to rewrite in his own words -->
- **Broken: the job's progress lives only in the API process.**
  BackgroundTasks runs the job inside the API, after the reply is sent. A
  SIGKILL ends that process with no clean-up, so the database still says
  "running". The restarted API is a new process with no memory of the job,
  and nothing is responsible for checking for stuck jobs. So the job never
  continues: 20 of 40 texts lost in all 25 broken runs.
- **Why `/health` said fine:** it only checks that the API answers, not that
  work is moving. A green health check is not proof that jobs are making
  progress.
- **Fixed: progress lives in the database.** Each finished text is saved
  straight away, and the worker writes a heartbeat every 5 s. Once a job's
  heartbeat is older than the stuck-after time, a worker takes it over and
  continues from the saved texts: 0 lost in all 30 fixed runs.
- **Why exactly 4 texts are redone:** 4 texts run at once, and a text in
  progress is not saved. So all 4 texts that were with the AI are lost at
  the kill and done again.
- **Why recovery took 26–34 s:** the countdown starts at the last heartbeat,
  which was 0–5 s before the kill. Then up to 1 s until the worker's next
  stuck-job check, then one text's AI wait. The stale-time check confirms
  it: stuck after 10 / 30 / 60 s gave recovery of 12.5 / 31.7 / 62.0 s, about
  the stuck-after time plus 2 s.

### Surprises
- **Recovery can be shorter than the stale time.** "Stuck" is counted from
  the last heartbeat, not from the kill. The heartbeat comes every 5 s, so at
  the kill the last one was already 0–5 s old. With the replayed times it was
  about 4 s old, so the takeover came 25.9 s after the kill; at waits of
  1–4 s it was just fresh (29.9 s). The worker also looks for jobs only once
  a second, which adds up to 1 s. So kill → takeover is between the stale
  time minus 5 s and the stale time plus 1 s, and then one text's AI wait
  comes on top.
- **The spread is tiny.** In each group of 5 the kill → takeover times agree
  within 0.2 s, because the kill always comes at the same point of the same
  job with the same timings, so it meets the heartbeat at the same moment. A
  kill at a random moment would spread over that 6-second window.
- **Real AI recovered fastest** (26.2 s): its takeover came early (25.9 s),
  and the first texts done after it happened to be two very short reviews
  (26 and 59 characters, 0.3–0.5 s each).
- **The redone texts are always 4, never fewer.** With 4 texts at once and a
  job far from its end, 4 texts are always with the AI when the kill comes.
  Fewer only near the end of a job.
- **The stale time sets the recovery time almost one to one** (10 → 12.5 s,
  30 → 31.7 s, 60 → 62.0 s), and changes nothing else: still 0 lost and 4
  redone. A shorter stale time means a faster recovery, but leaves less
  room for a living worker whose heartbeat is late (for example a busy
  database or a paused container) before it looks stuck. Kuyruk uses 30 s,
  six heartbeats (D68).

### Takeaway
<!-- draft: Adar to rewrite in his own words -->
- **The rule:** don't run long jobs inside the web server. Use a separate
  worker, save progress after each item, and add a heartbeat so another
  worker can take over a stuck job.
- **What the fix costs:** one extra container; 15 of 139 database writes
  per job (10.8%), compared with a plain BackgroundTasks job with no
  heartbeat; 105 of the worker's 462 lines of code.
- **Work can run twice:** after a crash, up to as many items as run at once
  are redone (4 here, in every run). With a paid AI service that would mean
  up to 4 extra paid calls per crash (not tested in E2). Every item must be
  safe to redo.
- **Choosing the stuck-after time:** shorter recovers faster (recovery ≈
  stuck-after + about 2 s). But keep it several heartbeats long, so a worker
  that is alive but briefly slow doesn't lose its job to another worker (not
  tested in E2). 30 s with a 5 s heartbeat is a reasonable default.
- **Monitoring:** watch for jobs stuck in "running", not just `/health`.
  In E2 the broken job stood still for 5 minutes while `/health` stayed
  fine.

**Note (not built or measured): 4 uvicorn workers with BackgroundTasks.**
Running the API as 4 uvicorn worker processes (`--workers 4`) doesn't fix
this. Each process runs its own background tasks, so a job lives in
whichever process took the request. If one process dies, only its jobs are
lost and the other 3 keep answering. uvicorn starts a new process in its
place, but the new process doesn't know those jobs existed, so they stay
"running" exactly as in E2. A restart of the whole API (for example a new
release) stops all 4 at once, and a polite stop can only wait so long for
background tasks before Docker kills the process. More processes make the problem smaller per
crash and harder to notice, but nothing would ever finish the lost texts.
That needs something outside the request that remembers the job in the
database and checks it is still alive: the worker and its heartbeat.

---

## E3 — Image size and rebuild time

### Question
How much smaller is Kuyruk's Docker image, and how much faster does it rebuild after a small change, when it is built the careful way instead of the simple way, and what does that care cost?

### Prediction
E3 prediction (Adar, 9 Nov 2026, before running):

Image size. The simple image (full Python base image, everything installed in one stage) will be around 1–1.2 GB, mostly because the full Python base ships compilers and tools Kuyruk never uses. The optimised image (slim base, multi-stage, only the installed packages copied into the final stage) will be around 200–300 MB, so roughly 4–5 times smaller.

Rebuild after changing one line of code. The simple version will take 30–60 s, because it copies all the code before installing packages, so any code change throws away the cached install and redoes it. The optimised version will take under 5 s, because packages are installed from the lockfile in an earlier layer that stays cached.

Rebuild after adding one package. Both versions have to reinstall, so the gap will be much smaller: maybe 30–60 s for the simple one and 15–30 s for the optimised one, if uv’s download cache is reused.

Cost of the fix. The Dockerfile will be about 3 times longer and harder to read for someone new. The main risk is forgetting to copy something into the final stage, which builds fine but breaks only when the container runs. The slim image also lacks tools like curl for debugging inside the container.

### Method
### Results
### Why it happens
### Surprises
### Takeaway

---

## E4 — CI

### Question
### Prediction
### Method
### Results
### Why it happens
### Surprises
### Takeaway
