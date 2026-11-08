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
