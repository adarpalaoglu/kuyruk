# Where the usual way breaks

*Adar Palaoğlu*

<!--
Every number is labelled and checked against the data by
`uv run python -m bench.check_numbers` (D131). No em dashes, en dashes or
semicolons (a test checks this, D132). The opinions are Adar's own words.
-->

## The question

When you build an AI job service the usual way with FastAPI, where does it break, and what is the smallest fix for each?

Kuyruk processes long AI requests the standard way (ticket, tracking, nothing lost on a crash). What's different is the experiments.

## How I measured it

Every experiment asks four questions. What can go wrong? What is lost? What is the smallest fix? What does it save and cost?

- **The test bench.** Kuyruk, a job service that sends piles of up to 1,000<!--s:max_texts_per_job--> Turkish texts to an AI. A separate worker does the work, 4<!--s:texts_at_once--> texts at once, and writes a heartbeat every 5<!--s:heartbeat_seconds--> seconds.
- **The AI.** Gemma 4 E4B on Ollama, on a MacBook Air. I measured it first, on 100<!--n:samples_count--> synthetic Turkish reviews. Most runs use a pretend AI that replays those measured waiting times, and each experiment ends with a check on the real AI.
- **The protocol.** A prediction written down before every experiment. The usual way and the fixed way, each run several times, in a shuffled order. The median and the spread are reported. Raw data is in `bench/data/`, the full results are in `docs/experiments.md`.

## The four findings

### E1. Blocking the event loop

While 50<!--n:e1_slow_requests--> slow requests each waited 4<!--n:e1_wait_s--> seconds, I asked a simple "are you alive?" question (`/health`) every tenth of a second. With `time.sleep` inside `async def`, in a typical run, the slowest one in twenty answers took 252<!--n:e1_bad_s--> seconds or more. With `await asyncio.sleep`, the same measure was 7<!--n:e1_good_ms--> milliseconds.

The way a wait is written changes how long everyone else waits. It changes the job's own time only if the AI can answer several requests at once.

### E2. Losing work

A job of 40<!--n:e2_texts--> texts was killed after 20<!--n:e2_kill_at--> were finished. With `BackgroundTasks` inside the API, 20<!--n:e2_broken_lost--> texts were lost in all 25<!--n:e2_broken_runs--> runs and the job never finished. With the separate worker and its heartbeat, 0<!--n:e2_fixed_lost--> were lost in all 30<!--n:e2_fixed_runs--> runs, 4<!--n:e2_redone--> texts were done twice each time, and work carried on after 26<!--n:e2_recovery_min_s--> to 34<!--n:e2_recovery_max_s--> seconds.

The heartbeat shows whether anyone is still working on a running job.

The job's memory lived in the program. When the program crashed, the database still said "running", which was misleading. The fix keeps the progress in the database. After a crash it repeats at most the texts that were with the AI at that moment (4<!--s:texts_at_once--> with my setting), and only after a crash. This is called "at least once".

### E3. Image size and rebuild time

The simple image was 1,331<!--n:e3_bad_mb--> MB, the careful one 259<!--n:e3_good_mb--> MB. After a one-line code change, a rebuild took 15.6<!--n:e3_rebuild_bad_s--> seconds the simple way and 0.5<!--n:e3_rebuild_good_s--> seconds the careful way.

I told Docker to start from the slim version of Python, because people who run the app don't need the developer tools. That one choice gave 98<!--n:e3_base_percent-->% of the size saving. Putting the build steps in a sensible order made rebuilds fast.

### E4. CI

Across 20<!--n:e4_runs--> runs on GitHub, the job took 90<!--n:e4_cache_on_s--> seconds with uv's download cache and 90<!--n:e4_cache_off_s--> seconds without it. Downloading every library took 0.42<!--n:e4_download_s--> seconds. A pull request from a fork was not tested live (one GitHub account), so that part relies on GitHub's documentation.

The cache saved nothing, so measure before you optimise. For secrets, keep them in GitHub secrets and also control which runs can use them.

### Two bonus findings

- I counted the tokens of the same 50<!--n:cost_reviews--> reviews in Turkish and in English, with Kuyruk's counter and with Gemma's own.
- Ollama answered one request at a time on this setup. The same texts took 182<!--n:at_once_fewest_s--> seconds sent 1<!--n:at_once_fewest--> at a time and 181<!--n:at_once_most_s--> seconds sent 8<!--n:at_once_most--> at a time.

Turkish needs more tokens than English (24<!--n:cost_percent_more-->% more with the counter Kuyruk uses, 13<!--n:cost_gemma_percent_more-->% more with Gemma's). With a paid AI that means a bigger bill. Either way, it uses up the daily budget faster.

## Limits

- All AI numbers come from one MacBook Air, one model and one version of Ollama.
- After a crash, up to 4<!--s:texts_at_once--> texts are done twice ("at least once").
- The budget's token count is an estimate of Gemma's, and leaves out the instruction and the answers.
- One worker does one job at a time.

## What I'd do next

My next focus is reliability. First, I would test more kinds of failure than one worker stopping in the middle of a job. Second, I would rerun the experiments on a cloud AI that can answer several requests at once, because Ollama on my Mac answers one at a time. That would show whether the experiment results change when the AI is no longer the bottleneck.
