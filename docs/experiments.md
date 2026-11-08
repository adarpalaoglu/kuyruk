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
### Prediction
### Method
### Results
### Why it happens
### Surprises
### Takeaway

---

## E3 — Image size and rebuild time

### Question
### Prediction
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
