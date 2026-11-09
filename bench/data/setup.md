# Setup notes — real AI measurements (Slice 10)

Written automatically by `bench/measure_ollama.py` when the measurement
started. Spec 9 asks every experiment to record these.

| | |
|---|---|
| Date and time | 2026-11-08T14:51:05+03:00 |
| Mac | MacBook Air (Mac16,12) |
| Chip | Apple M4 |
| Memory | 24 GB |
| macOS | 15.7.3 |
| Power | Now drawing from 'AC Power' |
| Ollama version | 0.40.15 |
| Model | gemma4:e4b |
| Model digest | `eb2177adf7646d15faead94fdad6d3e170dc8015653418a3b7d5d52a6380be5b` |
| Ollama parallel setting | 8 (`OLLAMA_NUM_PARALLEL`, D88) |
| Temperature, seed, thinking | 0.0, 42, off |
| Kuyruk commit | `d07f6ee12cfbca1a421c6d89e00dddeaf8a727b9` plus uncommitted Slice 10 work |
| Warm-up | 3 requests, not recorded (D89) |
| Other apps | Docker Desktop closed; other heavy apps closed |

The parallel setting was made with `launchctl setenv OLLAMA_NUM_PARALLEL 8`
and an Ollama restart. The Mac forgets it after a restart of the Mac.

## Run history (all on 8 Nov 2026, same Mac, model digest and settings)

1. 14:51 — first full run. The one-at-a-time run (no heat-up, cool Mac,
   empty memory: Ollama had just been restarted) is kept as
   `ollama_times_cool.csv`. The 12 "at once" runs of this attempt measured
   texts Ollama had already read (99% of input tokens remembered) and were
   thrown away; their numbers are in `docs/progress.md`.
2. 15:17 — the 12 "at once" runs redone, Ollama's memory emptied before each
   run, no heat-up. Only their first run (1 at once, round 1, 142 s) ran on a
   cooler Mac; the other 11 took 179–184 s. Files: `texts_at_once.csv`,
   `texts_at_once_items.csv`, `ollama_times_at_4.csv`.
3. 16:21 — one-at-a-time run redone after a 3-minute heat-up: 158 s, still
   faster than the steady 181–183 s, so 3 minutes was not enough. Replaced.
4. 16:30 — one-at-a-time run redone after a 10-minute heat-up (593 texts,
   not recorded), memory emptied first: 179 s, matching the steady runs (its
   first and last 25 texts took 1.00 and 0.97 times as long as the same texts
   in the steady 1-at-once runs). File: `ollama_times.csv`.

Every run: plugged in, Ollama parallel setting 8, same model digest (checked
by the script), only the shared instruction remembered (11% of input tokens).

## Findings about this setup (for the write-up)

- **One request at a time.** Ollama 0.40.15 runs gemma4:e4b with its MLX
  engine, which ignores the parallel setting and answers one request at a time
  (Ollama issue #17280, open). The same 100 texts took a median of 182, 182,
  182 and 181 s at 1, 2, 4 and 8 at once. Sending several requests at once
  doesn't help if the AI server handles them one by one (D88).
- **Ollama remembers texts it has read.** Its engine keeps a "prefix cache":
  a text it has read before is answered about 25% faster. Every measured run
  therefore starts with the model unloaded, which empties that memory (D89).
- **The Mac slows down by about a third when warm.** The same 100 texts, one
  at a time: 132 s on a cool Mac, 158 s after 3 minutes of work, 179 s after
  10 minutes (and 181–183 s in the long "at once" session). A fanless MacBook
  Air holds back its chip as it heats up, for about 5–6 minutes of steady
  work. All reported numbers except `ollama_times_cool.csv` come from a fully
  warm Mac, like a long real job.

## E1 — blocking the event loop (Slice 11)

Written automatically by `bench/e1_load.py` when the run started.

| | |
|---|---|
| Date and time | 2026-11-08T19:19:28+03:00 |
| Mac | MacBook Air (Mac16,12), Apple M4, 24 GB |
| macOS | 15.7.3 |
| Power | Now drawing from 'AC Power' |
| Python, FastAPI, uvicorn, httpx | 3.12.16, 0.162.0, 0.54.0, 0.28.1 |
| Kuyruk commit | `d809ba510bfd01cc23c64b1861489ab5d52723a0` plus uncommitted Slice 11 work |
| Server | one uvicorn process: `-m uvicorn app.main:app --host 127.0.0.1 --port 8011 --workers 1 --loop asyncio --http h11 --backlog 4096 --timeout-keep-alive 60 --no-access-log` |
| Server connection queue (backlog) | 4096; macOS cap `kern.ipc.somaxconn` = 4096 |
| Open-files limit | 10240 (hard: unlimited), for the load test and the server (inherited) |
| Client connection limit | none (`max_connections=None`) |
| Timeouts (probes and slow requests) | 900 s |
| Probes | /health and ping every 100 ms on a fixed clock; warning if one leaves > 20 ms late |
| Probe process | its own process and client, started fresh each run |
| Rest between runs | 2 s; new clients every run |
| Warm-up | 3 rounds of 5 slow requests per mode (0.05 s) and 20 of each probe, not recorded |
| Other apps | Docker Desktop closed; other heavy apps closed |
| Ollama version, model | 0.40.15, gemma4:e4b (digest `eb2177adf7646d15faead94fdad6d3e170dc8015653418a3b7d5d52a6380be5b`) |
| Ollama parallel setting | 8 (D88) |
| AI part | heat-up 600 s (D94); memory emptied and 3 warm-ups before every run (D89); 10 texts per run |

### E1 run notes (added by hand after the run)

- One full run, 19:19–20:24 on 8 Nov 2026: 80 sleep runs, then a 600 s
  heat-up (611 texts, not recorded) and 15 real-AI runs. 0 errors in all
  95 runs (46,532 probes, 5,150 slow requests).
- Clock check: 255 of the 46,532 probes (0.5%) left more than 20 ms late.
  252 of them were pings in mode A runs (251 at 4 s, 1 at 2 s), 20–26 ms
  late, late in the run when thousands of probes were open at once in the
  probe process; each of them then waited at least 60 s for its answer, so
  26 ms changes nothing. In B100: one ping 20.7 ms late (2 s, repeat 3), and
  one tick at 4 s (repeat 5, 8.1 s into the run) 62–63 ms late for both
  probes. Every other probe left within 20 ms.
- Before the full run, two trial runs (not kept) changed the load test:
  probes moved to their own process (one probe left 82 ms late at a burst
  of slow replies when everything ran in one process), and the probe
  process sends one unrecorded probe of each kind first (the first real
  probe was ~22 ms late without it). D96.

## E1 confirmation after /health became async def (Slice 11)

Written automatically by `bench/e1_load.py` when the run started.

| | |
|---|---|
| Date and time | 2026-11-08T20:38:50+03:00 |
| Mac | MacBook Air (Mac16,12), Apple M4, 24 GB |
| macOS | 15.7.3 |
| Power | Now drawing from 'AC Power' |
| Python, FastAPI, uvicorn, httpx | 3.12.16, 0.162.0, 0.54.0, 0.28.1 |
| Kuyruk commit | `d809ba510bfd01cc23c64b1861489ab5d52723a0` plus uncommitted Slice 11 work |
| Server | one uvicorn process: `-m uvicorn app.main:app --host 127.0.0.1 --port 8011 --workers 1 --loop asyncio --http h11 --backlog 4096 --timeout-keep-alive 60 --no-access-log` |
| Server connection queue (backlog) | 4096; macOS cap `kern.ipc.somaxconn` = 4096 |
| Open-files limit | 10240 (hard: unlimited), for the load test and the server (inherited) |
| Client connection limit | none (`max_connections=None`) |
| Timeouts (probes and slow requests) | 900 s |
| Probes | /health and ping every 100 ms on a fixed clock; warning if one leaves > 20 ms late |
| Probe process | its own process and client, started fresh each run |
| Rest between runs | 2 s; new clients every run |
| Warm-up | 3 rounds of 5 slow requests per mode (0.05 s) and 20 of each probe, not recorded |
| Other apps | Docker Desktop closed; other heavy apps closed |

## E2 — losing work (Slice 12)

Written automatically by `bench/e2_kill.py` when the run started.

| | |
|---|---|
| Date and time | 2026-11-09T12:07:39+03:00 |
| Mac | MacBook Air (Mac16,12), Apple M4, 24 GB |
| macOS | 15.7.3 |
| Power | Now drawing from 'AC Power' |
| Docker Engine, Compose | 29.9.2, 5.6.1 |
| Kuyruk commit | `1f924001d5eaa978c6ea89a6b4eba602b025e896` plus uncommitted Slice 12 work |
| Parts | main, stale, sensitivity, cost, ai |
| Compose | project `kuyruk-e2`, `compose.yaml` + `bench/e2_compose.yaml`; own database, emptied before every run; no `.env` values |
| Worker settings | heartbeat 5 s, stuck after 30 s (stale part: 10, 30, 60 s), 4 texts at once, looks for jobs every 1 s |
| Job | 40 texts, killed at 20 finished (real AI: 12, killed at 6) |
| Kill | `docker kill --signal KILL` (API in broken runs, worker in fixed runs); then the script starts the same container again (`docker start`). Docker's restart rule ignores a container stopped with `docker kill`, because it treats that as a manual stop, so the script restarting the worker is the correct simulation of a real crash restart. The API has no restart rule, so after a real crash it would stay down; restarting it gave the broken version its best chance. The script first waits 2 s to see whether Docker restarted it itself (it never did) |
| Watch | the database every 100 ms on a fixed clock, from this Mac; warning if a look leaves > 20 ms late |
| Broken watched after restart | 300 s (main), 60 s (sensitivity) |
| Other apps | Docker Desktop open (needed); other heavy apps closed |
| Ollama version, model | 0.40.15, gemma4:e4b (digest `eb2177adf7646d15faead94fdad6d3e170dc8015653418a3b7d5d52a6380be5b`) |
| Ollama parallel setting | 8 (D88) |
| AI part | heat-up 600 s (D94); memory emptied and 3 warm-ups before every run (D89) |

### E2 run notes (added by hand after the run)

- One full run, 12:07–14:18 on 9 Nov 2026: 65 fake-AI kill runs (main,
  stale-time check, sensitivity), 3 uninterrupted cost jobs, then a 600 s
  heat-up (500 texts, not recorded) and 5 real-AI kill runs. 0 errors in all
  70 runs; every kill restart was done by the script (2.1–2.2 s).
- Clock check: 1 database look in 70 runs left more than 20 ms late (36.1 ms,
  stale 60 s, repeat 4, in a run whose recovery took 62 s). Every other look
  left within 14 ms.
- Before the full run, one trial (1 broken + 1 fixed, not kept) gave the
  same counts: broken 20 lost, never recovered; fixed 0 lost, 4 redone,
  recovery 31.6 s.

## E3 — image size and rebuild time (Slice 13)

Mac run (`bench/e3_build.py`, D105–D107). Machine lines printed by the script;
the rest added by hand.

| | |
|---|---|
| Date and time | 9 Nov 2026, 15:59–16:10 (+03:00) |
| Mac | MacBook Air (Mac16,12), Apple M4, 24 GB; macOS 15.7.3; on AC power |
| Docker | Docker Desktop, Engine 29.9.2; its Linux VM: aarch64, 10 CPUs, 8.3 GB memory; containerd image store (sizes as `docker image inspect` reports them) |
| Kuyruk commit | `f0bef90` (Slice 13 code) |
| Base images | `python:3.12.16-trixie` (full) and `python:3.12.16-slim-trixie`, both pinned by digest; pip 25.0.1 as shipped in them; uv 0.13.7 |
| Extra package | `six==1.17.0` |
| Rounds | 5, interleaved (rotated order); build cache cleared (`docker builder prune -af`) before every cold build; base images downloaded once before the run |
| Network | home Wi-Fi; libraries downloaded from PyPI during cold builds and pip rebuilds |
| Other apps | Docker Desktop open (needed); other heavy apps closed |

### E3 run notes (added by hand after the run)

- All 60 builds worked; all 5 images started and answered `/health`.
- Before the full run, one trial round (not kept) gave the same sizes to
  within 0.01 MB, and the same uv times, but slower pip cold builds (32 and
  44 s instead of ~19 s): pip downloads every library during a cold build, so
  its time depends on the network; uv's did not change.
- A separate check before the full run: a cold uv-good build after
  `docker builder prune -af` downloaded all 35 libraries ("Prepared 35
  packages"), so a cold build really starts from nothing.
