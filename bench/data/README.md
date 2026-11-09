# bench/data — measured data for Kuyruk's experiments

Raw numbers live here as CSV files (plain tables, values separated by commas).
Charts are drawn from these files only (`bench/charts.py` → `docs/charts/`).

## `reviews_tr.csv` — the 100 test texts (SYNTHETIC, D87)

**These are not real reviews.** All 100 are made-up Turkish product reviews,
written by Claude (an AI) for Kuyruk's measurements, and checked by Adar for
sounding natural. No real customers, people, brands or shops; the emails
(`@example.com`) and phone numbers (`555 000 …`) are invented.

Columns: `id`, `text`. 34 short (up to 200 characters), 33 medium (201–800),
33 long (801–2,000; the longest is 1,840), in mixed order. Some are typed
without Turkish letters, some contain emoji, and 12 contain an email or phone
number, which Kuyruk hides before the AI sees the text.

## Real AI times (Slice 10, spec 9.0)

Made by `bench/measure_ollama.py` with Gemma 4 E4B on Ollama on a MacBook Air.
The exact setup (Mac, Ollama version, model digest, date) is in `setup.md`.
Each run starts with Ollama's memory of earlier texts emptied and 3 warm-up
requests, not recorded (D89). The script now also starts with a 10-minute
heat-up, so the Mac is warm as in a long real job; `ollama_times.csv` was
measured that way. The "at once" runs were measured before the heat-up was
added: only their very first run (1 at once, round 1, 142 s) ran on a
cooler Mac; all the others took 179–184 s. A text's
`seconds` is the clock time of Kuyruk's own Ollama call, i.e. what a worker
waits (D90).

- `ollama_times.csv` — each text sent alone, one after another, in shuffled
  order. Columns: `order` (when it ran), `id`, `characters`, `input_tokens`,
  `output_tokens` (both counted by Ollama), `seconds`.
- `ollama_times_cool.csv` — the first one-at-a-time run, on a cool Mac (no
  heat-up), same columns. Kept as evidence that the fanless Mac slows down
  once warm; not used by Kuyruk.
- `texts_at_once.csv` — the same 100 texts sent 1, 2, 4 and 8 at once, 3 times
  each, in interleaved order (D91). One row per run: `round`, `texts_at_once`,
  `total_seconds`, `started_at`.
- `texts_at_once_items.csv` — the same runs, one row per text: `round`,
  `texts_at_once`, then the same columns as `ollama_times.csv` (without
  `order`).
- `ollama_times_at_4.csv` — the per-text times of the three runs at 4 at once
  (300 times; columns `round`, `id`, `seconds`). **The fake AI replays these by
  default** (`FAKE_AI_REPLAY_FILE`, D93), because the worker sends 4 texts at
  once, so a text takes as long as it does at 4 at once.

On this setup Ollama answers one request at a time, whatever its parallel
setting (D88), so at 4 at once a text's time includes waiting in Ollama's
queue.

The earlier `placeholder_times.csv` (ten rough guesses, D82) was deleted in
Slice 10.

## E1 — blocking the event loop (Slice 11)

Made by `bench/e1_load.py` (D96–D98); setup in `setup.md`. Every row has
`kind` (`sleep`: the timed waits; `ai`: the real-AI check), `setting` (mode
and number of slow requests, e.g. `B100`), `mode`, `slow_requests`,
`wait_seconds` (empty for `ai`) and `repeat`. All times are counted from the
run's time zero, when all slow requests were sent.

- `e1_runs.csv` — one row per run (95): `order_in_repeat`, `started_at`; for
  `health` and `ping`: number of probes, p50, p95 and max in milliseconds;
  the first, median and last slow-request finish in seconds;
  `server_step_median_s` (how long the slow step itself took, as the server
  measured it); `errors`; `max_probe_late_ms` (the clock check).
- `e1_probes.csv` — every probe (46,532): `target` (`health` or `ping`),
  `planned` (when it was meant to leave), `sent` (when it left), `seconds`
  (time to its full answer), `ok`, `error`.
- `e1_slow.csv` — every slow request (5,150): `index`, `finished`,
  `server_seconds`, `ok`, `error`.

## E3 — image size and rebuild time (Slice 13)

Made by `bench/e3_build.py` (D105–D107); setup in `setup.md`. Files without a
suffix were measured on the Mac; files ending in `_ci` on GitHub's
`ubuntu-24.04` machine (`.github/workflows/e3.yml`). Sizes are in bytes,
times in seconds.

- `e3_images.csv` / `e3_images_ci.csv` — one row per image (5 Dockerfiles
  and the 2 base images on their own, `base-full` and `base-slim`):
  `image_bytes` (uncompressed: the sum of the image's layers in
  `docker history`, D106), `compressed_bytes` (the image
  saved with `docker save` and gzipped), `starts` (the image started Kuyruk's
  API and `/health` answered), `has_curl`, `lines` (the Dockerfile's lines
  without comments or blank lines; empty for base images).
- `e3_builds.csv` / `e3_builds_ci.csv` — one row per build (5 rounds × 4
  Dockerfiles × 3 steps): `round`, `position` (place in the round's rotated
  order), `dockerfile`, `step` (`cold`: build cache cleared first; `code`:
  after a one-line change to `app/main.py`; `package`: after adding
  `six==1.17.0`), `seconds`, `ok`, `image_bytes`, `error`.
