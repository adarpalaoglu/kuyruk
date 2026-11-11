# Where FastAPI's built-in pieces fail on long AI jobs

*Adar Palaoğlu*

<!--
This is a skeleton (Slice 18a). Places marked "18b" are for Adar's own words
and are empty on purpose. Every number is labelled and checked against the
data by `uv run python -m bench.check_numbers` (D131). No em dashes, en
dashes or semicolons (a test checks this, D132).
-->

## The question

For long AI jobs, where exactly do FastAPI's built-in pieces fail, and what is the smallest fix for each?

<!-- 18b: Adar writes this in his own words (why the question matters). -->

## How I measured it

- **The test bench.** Kuyruk, a job service that sends piles of up to 1,000<!--s:max_texts_per_job--> Turkish texts to an AI. A separate worker does the work, 4<!--s:texts_at_once--> texts at once, and writes a heartbeat every 5<!--s:heartbeat_seconds--> seconds.
- **The AI.** Gemma 4 E4B on Ollama, on a MacBook Air. I measured it first, on 100<!--n:samples_count--> synthetic Turkish reviews. Most runs use a pretend AI that replays those measured waiting times, and each experiment ends with a check on the real AI.
- **The protocol.** A prediction written down before every experiment. The usual way and the fixed way, each run several times, in a shuffled order. The median and the spread are reported. Raw data is in `bench/data/`, the full results are in `docs/experiments.md`.

<!-- 18b: Adar writes this in his own words (anything to add about the method). -->

## The four findings

### E1. Blocking the event loop

While 50<!--n:e1_slow_requests--> slow requests each waited 4<!--n:e1_wait_s--> seconds, the "are you alive?" check (`/health`) took 252<!--n:e1_bad_s--> seconds at the slow end with `time.sleep` inside `async def`, and 7<!--n:e1_good_ms--> milliseconds with `await asyncio.sleep`.

<!-- 18b: Adar writes this in his own words (what it shows). -->

### E2. Losing work

A job of 40<!--n:e2_texts--> texts was killed after 20<!--n:e2_kill_at--> were finished. With `BackgroundTasks` inside the API, 20<!--n:e2_broken_lost--> texts were lost in all 25<!--n:e2_broken_runs--> runs and the job never finished. With the separate worker and its heartbeat, 0<!--n:e2_fixed_lost--> were lost in all 30<!--n:e2_fixed_runs--> runs, 4<!--n:e2_redone--> texts were done twice each time, and work carried on after 26<!--n:e2_recovery_min_s--> to 34<!--n:e2_recovery_max_s--> seconds.

<!-- 18b: Adar writes this in his own words (what it shows). -->

### E3. Image size and rebuild time

The simple image was 1,331<!--n:e3_bad_mb--> MB, the careful one 259<!--n:e3_good_mb--> MB. Of the difference, 98<!--n:e3_base_percent-->% came from the base image alone. After a one-line code change, a rebuild took 15.6<!--n:e3_rebuild_bad_s--> seconds the simple way and 0.5<!--n:e3_rebuild_good_s--> seconds the careful way.

<!-- 18b: Adar writes this in his own words (what it shows). -->

### E4. CI

Across 20<!--n:e4_runs--> runs on GitHub, the job took 90<!--n:e4_cache_on_s--> seconds with uv's download cache and 90<!--n:e4_cache_off_s--> seconds without it. Downloading every library took 0.42<!--n:e4_download_s--> seconds. A pull request from a fork was not tested live (one GitHub account), so that part relies on GitHub's documentation.

<!-- 18b: Adar writes this in his own words (what it shows). -->

### Two bonus findings

- The same 50<!--n:cost_reviews--> reviews needed 24<!--n:cost_percent_more-->% more tokens in Turkish than in English with Kuyruk's counter, and 13<!--n:cost_gemma_percent_more-->% more with Gemma's own.
- Ollama answered one request at a time on this setup. The same texts took 182<!--n:at_once_fewest_s--> seconds sent 1<!--n:at_once_fewest--> at a time and 181<!--n:at_once_most_s--> seconds sent 8<!--n:at_once_most--> at a time.

<!-- 18b: Adar writes this in his own words (what they show). -->

## What it means for real AI services

<!-- 18b: Adar writes this in his own words. -->

## Limits

- All AI numbers come from one MacBook Air, one model and one version of Ollama.
- After a crash, up to 4<!--s:texts_at_once--> texts are done twice ("at least once").
- The budget's token count is an estimate of Gemma's, and leaves out the instruction and the answers.
- One worker does one job at a time.

<!-- 18b: Adar writes this in his own words (the limits that matter most, and what he would do next). -->
