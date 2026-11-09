# Decision log — Kuyruk

Every decision below is mine (Adar Palaoğlu). Claude helped me lay out the options; I chose, and these are my reasons. Each entry was written **before** any results existed. Experiment predictions are in `docs/experiments.md`, also written before running.

Status key: **Draft** = written from my answers, waiting for my approval · **Approved** = I've read it and agree it states my reasons.

---

## Project shape

### D01 — Kuyruk is about the system around the AI, not the AI call
- **Options:** an app that calls an AI · a job service that handles big piles of AI work reliably.
- **Decision:** a job service.
- **Why:** Calling an AI is easy. What breaks in real projects is everything around it: long waits, crashes, cost, many users at once. That is what I want to understand and show.
- **Date:** 5 Nov 2026 · **Status:** Approved (6 Nov 2026)

### D02 — Three tasks forming a real pipeline: clean → count → AI
- **Options:** AI only · count + AI · clean + count + AI.
- **Decision:** all three.
- **Why:** Real AI work starts by cleaning messy data and estimating cost before spending money. The fast tasks also make tests quick, and the cleanup step is a Turkish-specific part I designed myself.
- **Date:** 5 Nov 2026 · **Status:** Approved (6 Nov 2026)

### D03 — One task per job
- **Options:** one task for the whole job · a different task per text.
- **Decision:** one task per job.
- **Why:** Progress, cost and time estimates stay simple when every text in a job is similar. Mixing tasks adds complexity and proves nothing extra.
- **Date:** 5 Nov 2026 · **Status:** Approved (6 Nov 2026)

### D04 — One fixed AI instruction
- **Options:** users write their own instruction · one fixed instruction.
- **Decision:** fixed: "Summarise this in one Turkish sentence. Reply with only that sentence."
- **Why:** Runs stay comparable across experiments, and I avoid prompt-injection problems, which are not what this project is about. The second sentence was added (7 Nov 2026) because in testing the model otherwise added alternative versions.
- **Date:** 5 Nov 2026, wording updated 7 Nov 2026 · **Status:** Approved (6 Nov 2026; update approved 7 Nov 2026)

---

## AI and measurement

### D05 — Real AI for use; a controllable fake AI for tests and repeated experiments
- **Options:** real AI everywhere · fake AI everywhere · both, each where it fits.
- **Decision:** both.
- **Why:** My experiments test how Kuyruk behaves *while waiting* for the AI, not what the AI says. The real AI's timing jumps around (laptop heat, other programs), it becomes the bottleneck itself, it won't fail on command, and GitHub's machines can't run it. A fake AI that copies the real waiting times lets me see causes clearly. The real AI is still used for real work, for measuring, for the final check of each experiment and for the demo video.
- **Date:** 5 Nov 2026 · **Status:** Approved (6 Nov 2026)

### D06 — Measure the real AI first; the fake replays those real times
- **Options:** pick a waiting time (e.g. 2 s) · measure the real AI and copy it.
- **Decision:** measure 100 real Turkish texts first; the fake draws its waits from those measurements.
- **Why:** I don't want made-up numbers. The fake's timing comes from my own measurements, and I can say exactly where it came from.
- **Date:** 5 Nov 2026 · **Status:** Approved (6 Nov 2026)

### D07 — Sensitivity check and real-AI check for E1 and E2
- **Decision:** rerun each at 0.5 / 1 / 2 / 4 s waits, then once with the real AI, shown on the same chart.
- **Why:** If my conclusion holds at every waiting time and matches the real AI, it is solid. If it doesn't match, that is a finding I explain.
- **Date:** 5 Nov 2026 · **Status:** Approved (6 Nov 2026)

### D08 — Gemma 4 E4B on Ollama, running on the Mac (not in Docker)
- **Options:** Gemma 4 E4B (newest small Gemma, chosen at install over Gemma 3 4B) · Qwen 3 4B · a Turkish-specialised 7B model.
- **Decision:** Gemma 4 E4B, exact version pinned; Ollama runs directly on the Mac.
- **Why:** It is small enough for my MacBook Air (24 GB), trained on many languages including Turkish, and easy for others to reproduce. Like Qwen, Gemma 4 "thinks out loud" before answering by default (I saw this in my first test), which adds long, uneven delay, so I switch thinking off on every call and ask for one sentence only. Docker on a Mac can't use the chip's graphics power, so the AI inside Docker would be much slower.
- **Date:** 6 Nov 2026 · **Status:** Approved (6 Nov 2026)

### D45 — Tokens counted with tiktoken `o200k_base`, its file kept in the repo
- **Options:** tiktoken with a fixed encoding · Gemma's own tokenizer (needs Ollama running, or a much bigger library).
- **Decision:** tiktoken with `o200k_base`, set in settings. The encoding file is kept in the repo and checked by checksum before use.
- **Why:** It is fast, works with no internet, and can't silently change. A frozen project can't depend on a download link.
- **Cost:** The count is an estimate of Gemma's real count, not exact. Stated in the limits.
- **Date:** 7 Nov 2026 · **Status:** Approved (7 Nov 2026)

### D46 — Temperature 0 and a fixed seed, so runs are comparable
- **Decision:** every Ollama call uses temperature 0 (no randomness in word choice) and a fixed seed from settings.
- **Why:** Experiment runs should get the same answers for the same texts, so differences come from Kuyruk, not the AI.
- **Cost:** The same text gives the same summary almost always, not always: in the Slice 4 manual check, one review's first answer differed from the next six (which all matched).
- **Date:** 7 Nov 2026 · **Status:** Approved (7 Nov 2026)

### D47 — Which AI errors are temporary
- **Decision:** temporary (retried) = timeout, Ollama unreachable, a 5xx reply, a 429 ("too many requests") reply, or a reply cut off halfway. Anything else is permanent and fails at once.
- **Why:** Temporary errors mean "the AI couldn't answer right now", so trying again a moment later may work. Permanent errors (e.g. wrong model name) will never work, and retrying only wastes time (D16).
- **Date:** 7 Nov 2026 · **Status:** Approved (7 Nov 2026)

### D48 — The fake AI is the default
- **Decision:** `AI_BACKEND` is `fake` unless set to `ollama`.
- **Why:** A fresh copy of Kuyruk works with nothing installed. I set `ollama` in my own `.env` for real work.
- **Date:** 7 Nov 2026 · **Status:** Approved (7 Nov 2026)

### D50 — The AI never sees emails or phone numbers
- **Options:** send `llm` texts as written · run all six cleanup rules first · hide only emails and phone numbers first.
- **Decision:** before an `llm` text goes to the AI, Kuyruk joins split letters (NFC, which the email pattern needs, D49) and hides emails and phone numbers with `[EMAIL]` and `[TELEFON]`. Nothing else changes: capitals and spacing stay as the user wrote them. The stored original text is not changed.
- **Why:** My reason for hiding personal data (D31) is that it must not reach the AI. Sending texts as written would break that. Running all six rules would also change what the AI reads (lowercase, spacing) for no benefit.
- **Date:** 7 Nov 2026 · **Status:** Approved (7 Nov 2026)

### D09 — Warm-up requests before measuring
- **Decision:** send a few uncounted requests before every measurement.
- **Why:** The first request loads the model into memory and is much slower. Counting it would make the AI look slower than it is.
- **Date:** 6 Nov 2026 · **Status:** Approved (6 Nov 2026)

---

## Limits and budget

### D10 — Job limits: 1–1,000 texts, up to 2,000 characters each
- **Why:** 1,000 texts at about 2 seconds each is about 30 minutes, long enough for my experiments and realistic on a laptop. Very long texts make the AI slow and uneven, which makes experiments messy.
- **Date:** 5 Nov 2026 · **Status:** Approved (6 Nov 2026)

### D11 — Daily budget counted in AI tokens, only for AI jobs
- **Options:** number of texts per day · AI tokens per day.
- **Decision:** 200,000 tokens per user per day; cleaning and counting are free.
- **Why:** This is how real AI providers charge. It also ties my tasks together: the token counter protects the AI's cost.
- **Date:** 5 Nov 2026 · **Status:** Approved (6 Nov 2026)

### D12 — Reject the whole job if it breaks a limit
- **Options:** reject the whole job · accept part of it.
- **Decision:** reject the whole job immediately with a clear message.
- **Why:** Accepting half a job would confuse the user ("why did only half run?").
- **Date:** 5 Nov 2026 · **Status:** Approved (6 Nov 2026)

### D13 — Budget used today is calculated each time, not stored as a running total
- **Why:** One source of truth. A stored total can drift from reality if something crashes at the wrong moment.
- **Date:** 5 Nov 2026 · **Status:** Approved (6 Nov 2026)

### D14 — Budget check and reservation happen as one locked step
- **Why:** Two jobs sent at the same second could both see "5,000 left" and both pass, even though each check is correct on its own. Locking per user makes the second one wait until the first is counted. A test proves it.
- **Date:** 5 Nov 2026 · **Status:** Approved (6 Nov 2026)

### D57 — What the budget counts
- **Decision:** a job's `tokens_needed` is the tokens of its texts exactly as the AI receives them: letters joined (NFC) and emails and phone numbers hidden (D50), counted with the same counter as `count_tokens` (`o200k_base`, D45). Only `llm` jobs get a number; cleaning and counting jobs leave it empty.
- **Why:** The budget protects what the AI actually reads. A long email address the AI never sees shouldn't cost the user anything.
- **Cost:** The fixed instruction and the AI's answers are not counted, so the real use is higher than the number shown. Stated in the limits.
- **Date:** 7 Nov 2026 · **Status:** Approved (7 Nov 2026)

### D58 — Which jobs count towards today
- **Decision:** the user's `llm` jobs created since today's midnight UTC, except jobs that ended without any AI work: cancelled while still queued, or failed before starting. A job cancelled while running counts in full.
- **Why:** A job the worker never took never used the AI, so counting it would be unfair. Once a job has started, some of its texts have already gone to the AI.
- **Cost:** There is no refund for the texts a job cancelled halfway never did.
- **Date:** 7 Nov 2026 · **Status:** Approved (7 Nov 2026)

### D59 — How an over-budget job is refused
- **Decision:** 429 with code `budget_exceeded`, a message such as "This job needs 12,000 tokens; you have 3,000 left today.", and a `Retry-After` header with the seconds until the next midnight UTC. Nothing is saved. A job that uses exactly what is left is accepted.
- **Why:** 429 ("too many requests, try later") is what real AI providers send when a limit is reached, and Retry-After tells a program exactly when trying again can work. The limit is "up to", so reaching it exactly is allowed.
- **Date:** 7 Nov 2026 · **Status:** Approved (7 Nov 2026)

### D60 — A repeated send is checked before the budget
- **Decision:** a resend with the same Idempotency-Key and the same content returns the saved ticket without counting or checking the budget again. The key is looked up once before the lock and once more after it.
- **Why:** A double-click must never cost twice (D20), and must not be refused just because the first click used up the budget. The second lookup catches a twin request that was saved while this one waited for the lock.
- **Date:** 7 Nov 2026 · **Status:** Approved (7 Nov 2026)

### D61 — How the per-user lock works
- **Decision:** on Postgres, the user's row is locked with `SELECT … FOR UPDATE` before the budget is added up; the lock lasts until the job is saved or refused. SQLite has no row locks, so there a write that changes nothing to the user's row takes SQLite's single whole-database write lock instead. Tokens are counted before taking the lock.
- **Why:** Locking only one user's row means other users never wait. Counting first keeps the lock short. A test proves that without the lock two jobs sent at once both pass, and with it only one does.
- **Date:** 7 Nov 2026 · **Status:** Approved (7 Nov 2026)

### D62 — A job bigger than the whole daily limit gets 422, not 429
- **Decision:** if a job alone needs more tokens than the daily limit, it is refused before the daily check (no lock needed) with 422, code `job_exceeds_daily_budget`, a message such as "This job needs 340,000 tokens; the daily limit is 200,000. Split it into smaller jobs.", and no Retry-After.
- **Why:** Such a job can never succeed, not even tomorrow. A 429 with Retry-After would wrongly suggest that waiting helps.
- **Date:** 7 Nov 2026 · **Status:** Approved (7 Nov 2026)

---

## Job life

### D15 — A job with some failed texts is still "done", with counts
- **Options:** any failure = failed · done with counts · a percentage cut-off.
- **Decision:** done once every text has been tried, showing "497 succeeded, 3 failed". Failed only when it can't carry on at all.
- **Why:** Calling 497 good results a failure is wrong. This is also how Anthropic's and OpenAI's batch services work. A cut-off percentage would be arbitrary.
- **Date:** 5 Nov 2026 · **Status:** Approved (6 Nov 2026)

### D16 — Up to 3 tries per text, with growing waits, for temporary errors only
- **Why:** Brief AI errors are common, and waiting a bit longer each time is standard practice. Retrying forever could block a job, and retrying a broken text is pointless.
- **Date:** 5 Nov 2026 · **Status:** Approved (6 Nov 2026)

### D17 — Users can cancel
- **Why:** Real batch services allow it, it costs little, and it gives a useful test: cancel halfway and check nothing is lost.
- **Date:** 5 Nov 2026 · **Status:** Approved (6 Nov 2026)

### D18 — Stop a job after 10 failures in a row
- **Why:** If the AI is switched off, every text would fail three times and waste minutes. Ten in a row means the AI is down, not that the texts are bad.
- **Date:** 5 Nov 2026 · **Status:** Approved (6 Nov 2026)

### D51 — State changes only happen "if the state is still what we expect"
- **Decision:** every state change goes through one place that checks it against my table of allowed moves and refuses anything else as a bug. The change is written as "set it to done only if it is still running", which the database checks and does in one step.
- **Why:** The API and the worker are separate programs changing the same job. If the user cancels at the moment the worker writes "done", a blind write would silently undo the cancel. With the condition, whoever writes first wins, so a cancel is never overwritten.
- **Date:** 7 Nov 2026 · **Status:** Approved (7 Nov 2026)

### D52 — How cancel replies
- **Decision:** a cancel replies 200 with the job. A job that has already finished (done, failed or cancelled, so cancelling twice too) gives 409 `job_finished`. The finish time is the moment of the cancel, even if a few texts already with the AI finish just after.
- **Why:** The user sees at once that the cancel worked. A finished job can't be cancelled, and saying so is clearer than pretending it worked.
- **Date:** 7 Nov 2026 · **Status:** Approved (7 Nov 2026)

### D54 — How "10 failures in a row" is counted
- **Decision:** texts count in the order they *finish* (with 4 at once this can differ slightly from their position order). A text counts once, after its last try. Texts already with the AI when the limit is reached still finish and keep their results.
- **Why:** The rule is there to notice that the AI is down. Finishing order is when Kuyruk actually learns about each failure, and throwing away answers that are already on their way would waste work.
- **Date:** 7 Nov 2026 · **Status:** Approved (7 Nov 2026)

### D19 — Accept that one text may be done twice after a crash ("at least once")
- **Why:** Preventing it completely is very hard. I measure how often it happens in E2 and state it in the limits.
- **Date:** 5 Nov 2026 · **Status:** Approved (6 Nov 2026)

### D20 — Double-click protection with a request key
- **Why:** A user who clicks "send" twice shouldn't pay twice. The same key returns the same ticket, as payment companies like Stripe do.
- **Date:** 5 Nov 2026 · **Status:** Approved (6 Nov 2026)

---

## Data and API

### D21 — Three tables; each result lives next to its text
- **Why:** Each text has exactly one result, so a separate results table adds work for nothing. "Last active time" and "crash count" on the job are what let me tell a stuck job from a slow one (E2).
- **Date:** 5 Nov 2026 · **Status:** Approved (6 Nov 2026)

### D22 — 202 Accepted, not 201 Created
- **Why:** When the reply is sent, the work hasn't happened yet. Kuyruk has only accepted the job and given a ticket. 202 says exactly that.
- **Date:** 5 Nov 2026 · **Status:** Approved (6 Nov 2026)

### D23 — Another user's job returns 404, not 403
- **Why:** 403 would tell Mehmet that Ayşe's ticket exists. 404 tells him nothing.
- **Date:** 5 Nov 2026 · **Status:** Approved (6 Nov 2026)

### D24 — Live progress with SSE, not WebSocket
- **Why:** Progress only flows one way, from Kuyruk to the user. SSE does exactly that over normal web requests and is simpler. WebSocket is for two-way conversation, which I don't need.
- **Date:** 5 Nov 2026 · **Status:** Approved (6 Nov 2026)

### D25 — Login with username/password and JWT passes, not API keys
- **Why:** People log in through the interface, and a short-lived pass (30 minutes) limits damage if it leaks. API keys suit programs and would need their own management screens.
- **Date:** 5 Nov 2026 · **Status:** Approved (6 Nov 2026)

### D26 — Open sign-up
- **Why:** Anyone who runs Kuyruk can try it in two minutes. It runs on their own machine, so there's no risk.
- **Date:** 5 Nov 2026 · **Status:** Approved (6 Nov 2026)

### D43 — Usernames are case-insensitive; English letters, digits and underscore only
- **Decision:** Kuyruk turns usernames into lowercase at sign-up and at log-in, and allows only a–z, 0–9 and `_`.
- **Why:** Otherwise ADAR and adar could be two different accounts, and someone could pretend to be another user. Turkish letters are left out because Turkish I/İ turn into lowercase differently from English, which makes look-alike names hard to rule out.
- **Cost:** Turkish users can't use ç, ğ, ı, ö, ş, ü in usernames (ayşe must sign up as ayse). The texts inside jobs aren't affected; they keep every Turkish letter.
- **Date:** 6 Nov 2026 · **Status:** Approved (6 Nov 2026)

### D63 — Live progress by looking at the database twice a second
- **Decision:** an open progress stream looks at its job in the database every `EVENTS_POLL_SECONDS` = 0.5 and sends only what changed.
- **Why:** It works the same on SQLite and Postgres and needs very little code; half a second feels live.
- **Cost:** one tiny query per open stream per look; quick changes can be merged into one event or skipped (a tiny job may go from queued to done with no `running` in between). Postgres LISTEN/NOTIFY (the database tells you when something changes) would avoid the looking, but only works on Postgres.
- **Date:** 7 Nov 2026 · **Status:** Approved (7 Nov 2026)

### D64 — Event details (HAND-DESIGNED, completes spec 6.1)
- **Decision:** on connect, a snapshot: one `state` and one `progress` with the current counts. After that, only changes. If both change in one look, `progress` comes before `state`. `end` carries `{"state": "<final state>"}` and comes after the final `state`. A job already finished when you connect gets the snapshot and `end` at once. No event numbers: a client that reconnects simply gets a new snapshot.
- **Why:** Someone joining halfway sees where the job is at once; the texts counted in one look finished before the state changed; a new snapshot makes reconnecting simple and always correct.
- **Date:** 7 Nov 2026 · **Status:** Approved (7 Nov 2026)

### D65 — Login is checked once, when the stream opens
- **Decision:** the login pass and the 404 rule are checked only when a progress stream opens. Once open, the stream keeps going even if the pass expires.
- **Why:** A long job shouldn't cut off its viewer at minute 30.
- **Cost:** someone with a stolen pass can keep watching that one job until it ends, even after the pass expires; they can't open anything new with it.
- **Date:** 7 Nov 2026 · **Status:** Approved (7 Nov 2026)

### D66 — A keep-alive comment every 15 seconds
- **Decision:** a stream that has sent nothing for `EVENTS_KEEPALIVE_SECONDS` = 15 sends a comment line (`: keep-alive`), which clients ignore.
- **Why:** Networks and proxies often close connections that are silent for 30–60 seconds.
- **Date:** 7 Nov 2026 · **Status:** Approved (7 Nov 2026)

### D67 — The login pass never goes in a URL
- **Decision:** the interface reads the progress stream with `fetch` and sends the pass in the Authorization header. Browsers' built-in `EventSource` can't send that header, so it is not used; putting the pass in the address instead is never allowed.
- **Why:** Addresses end up in server logs, proxy logs and browser history, where a pass could be copied.
- **Date:** 7 Nov 2026 · **Status:** Approved (7 Nov 2026)

---

## Worker

### D27 — One text per AI call
- **Why:** Each text gets its own clean result, state and retries. Grouping texts risks mixed-up or skipped answers, and one bad text spoils the group.
- **Date:** 5 Nov 2026 · **Status:** Approved (6 Nov 2026)

### D28 — 4 texts at once by default, and measure where more stops helping
- **Why:** One at a time is too slow; too many overloads the laptop's AI. Measuring 1/2/4/8 answers a real consulting question: how much hardware do we need?
- **Date:** 5 Nov 2026 · **Status:** Approved (6 Nov 2026)

### D29 — One job at a time per worker; a separate worker process, not BackgroundTasks
- **Why:** If the job runs inside the API and the API restarts, the work is lost (that is E2). A separate worker that keeps everything in the database can recover. A big job can delay small ones; the fix is more workers, which I state as a limit.
- **Date:** 5 Nov 2026 · **Status:** Approved (6 Nov 2026)

### D30 — No Celery or Redis
- **Why:** E2 is meant to show *when* you would need them. Adding them first would hide the problem I'm studying.
- **Date:** 5 Nov 2026 · **Status:** Approved (6 Nov 2026)

### D53 — The worker looks for new jobs every second
- **Decision:** `WORKER_POLL_SECONDS` = 1.
- **Why:** A job starts within a second, which feels instant to a user, and the check is one tiny database query.
- **Date:** 7 Nov 2026 · **Status:** Approved (7 Nov 2026)

### D55 — A stopped worker waits at most 8 seconds for texts in progress
- **Decision:** on Ctrl+C or a stop signal, the worker starts no new texts and waits at most `SHUTDOWN_GRACE_SECONDS` = 8 for texts already with the AI, then exits. Texts not finished by then stay waiting; the job is handed back (D76) and the next worker continues it, so those texts may be done twice ("at least once", D19). *(Wording updated 7 Nov 2026: before D76 the job was continued by crash recovery.)*
- **Why:** Docker kills a container 10 seconds after asking it to stop, so a longer wait (e.g. the AI's 120-second timeout) would never really happen there. compose.yaml therefore gives the worker a stop time of 12 seconds (`stop_grace_period`), above this setting. *(Updated 7 Nov 2026 in Slice 9: was "Compose must keep Docker's stop time above this setting (Slice 9)".)*
- **Date:** 7 Nov 2026, wording updated 7 Nov 2026 (twice) · **Status:** Approved (7 Nov 2026; updates approved 7 Nov 2026)

### D56 — A bug inside a task fails only that text
- **Decision:** if a task crashes because of a mistake in Kuyruk (not an AI error), that text is marked failed with "Internal error." and the worker carries on. The details go only to the worker's log.
- **Why:** One bad text shouldn't stop the worker or the whole job, and the reply must not show how Kuyruk works inside.
- **Date:** 7 Nov 2026 · **Status:** Approved (7 Nov 2026)

---

## Crash recovery

### D68 — A job is stuck when its heartbeat is 30 seconds old, by the database's clock
- **Decision:** a `running` job whose `last_active_at` is older than `STALE_AFTER_SECONDS` = 30 is stuck. The heartbeat (every 5 s) runs beside the texts, not between them, so a text that waits a long time for the AI never stops it. Every heartbeat write and the "is it stuck?" check use the database's own current time, never a worker's clock. Kuyruk refuses to start if the stale time is not longer than the heartbeat time.
- **Why:** This is how a slow job is told from a stuck one: a slow job keeps beating; only a dead or frozen worker stops. With one clock (the database's), a worker whose own clock is wrong can neither make a healthy job look stuck nor hide a stuck one. A stale time no longer than the heartbeat would make every healthy job look stuck between two beats.
- **Date:** 7 Nov 2026 · **Status:** Approved (7 Nov 2026)

### D69 — Stuck jobs first; taking over is one compare-and-swap
- **Decision:** on start and on every poll, a worker first looks for a stuck job, and only if there is none claims a queued job. It takes a stuck job over with one change that the database makes only if the job is still running with the same `last_active_at` and `claimed_by` the worker just read ("compare-and-swap"). On Postgres the look also skips rows another worker has locked (`FOR UPDATE SKIP LOCKED`).
- **Why:** A stuck job has already waited at least 30 seconds and someone may be watching it. With the compare-and-swap, if two workers try at the same moment, the first change goes through and the second matches nothing, so two workers can never both take the same job.
- **Date:** 7 Nov 2026 · **Status:** Approved (7 Nov 2026)

### D70 — At most 3 takeovers, then the job fails
- **Decision:** every takeover adds 1 to `crash_count`. A job may be taken over at most `MAX_CRASH_COUNT` = 3 times. The 4th time it is found stuck, it is not taken over: it becomes `failed` with "Crashed too many times" (its crash count shows 4). Texts already finished keep their results. A job handed back after a polite stop (D76) is not a crash and doesn't count.
- **Why:** A job that keeps killing its worker (for example a text that makes the worker run out of memory) would otherwise crash every worker forever. Three chances is enough for ordinary crashes.
- **Date:** 7 Nov 2026, last sentence added 7 Nov 2026 · **Status:** Approved (7 Nov 2026; update approved 7 Nov 2026)

### D71 — A try counts when it finishes, and is saved at once
- **Decision:** each finished attempt at a text (an answer or an error) adds 1 to that text's `tries` in the database straight away. An attempt cut off by a crash never finished, so it doesn't count. A text taken over after a crash gets only the tries it has left (3 in total).
- **Why:** A crash is not the text's fault, so it must not use up a try. Saving each finished try means a text can't get more than 3 real tries just because its worker crashed in between.
- **Date:** 7 Nov 2026 · **Status:** Approved (7 Nov 2026)

### D72 — A worker that lost its job stops; the first saved answer wins
- **Decision:** before each text, a worker checks that the job is still running and still has its name in `claimed_by`. If another worker has taken it over (for example after this worker lost the database for over 30 seconds), it starts no more texts and never marks the job done or failed. A text's answer is saved only if the text is still waiting, so if two workers finish the same text, only the first answer is kept and counted.
- **Why:** A worker can look dead without being dead. These checks make sure the two never both work through the job, and that counts are never doubled.
- **Date:** 7 Nov 2026 · **Status:** Approved (7 Nov 2026)

### D73 — Details of the `recovered` event (completes spec 6.1)
- **Decision:** at takeover, the worker saves the lowest waiting position on the job (`resumed_from_position`). The live stream sends `recovered` `{"from_position": N}` when it sees the crash count go up. In one look the order is `recovered`, then `progress`, then `state`. It is not sent when the job failed for crashing too often. If nothing was waiting (the crash came after the last text but before "done"), `from_position` is `null`. Someone who connects after a recovery gets only the usual snapshot.
- **Why:** The takeover happens before the new worker's texts finish, and those finish before the job's state changes. A job that failed did not resume. Saving the position at takeover gives the exact text where work restarted, not a guess made later.
- **Date:** 7 Nov 2026 · **Status:** Approved (7 Nov 2026)

### D74 — How the demo crash works
- **Decision:** `POST /demo/crash-worker` (demo mode only, login required) puts a "crash requested" mark on the caller's oldest running job in the database and replies 202 with its ticket. The worker running that job checks the mark at every heartbeat and before starting each text and, if it is in demo mode too, exits at once with no clean-up (`os._exit`, like a power cut). The worker that takes over clears the mark. Outside demo mode the address answers 404, before the login check. With no running job: 409 `no_running_job`.
- **Why:** The API and the worker are separate programs (in Docker, separate containers) and can't send each other signals, but both share the database. Only the caller's own job, and only its worker, is affected. A real crash runs no clean-up, so the demo must not either. 404 outside demo mode means nobody learns the address exists. A crash with nothing running would show nothing. Checking before each text too (added 7 Nov 2026, after the manual check): with the heartbeat alone, a job that finished within one heartbeat (an 8-text job took 4 s) never crashed.
- **Date:** 7 Nov 2026, check before each text added 7 Nov 2026 · **Status:** Approved (7 Nov 2026; update approved 7 Nov 2026)

### D75 — One log line per text started, for E2
- **Decision:** each time a worker starts a text, it writes one line: `text started job=<ticket> position=<n> worker=<worker id>`. A retry is not a new start.
- **Why:** E2 must count redone texts. A text started twice (by two workers) is a redone text, so counting repeated job + position pairs in the logs gives the number exactly, without extra database writes.
- **Date:** 7 Nov 2026 · **Status:** Approved (7 Nov 2026)

### D76 — A polite stop hands the job back; it is not a crash
- **Options:** treat a polite stop like a crash (wait 30 s, crash count +1) · hand the job back.
- **Decision:** when a worker is stopped politely (Ctrl+C, or Docker stopping its container) and texts are still left after its 8-second grace (D55), it removes its name from the job (`claimed_by` empty); the job stays `running`. The next worker picks up a running job with no name at its next poll, without waiting for the stale time, before any queued job. The crash count stays the same and no `recovered` event is sent. Texts cut off after the grace may still be done twice (D19).
- **Why:** The code review found that otherwise restarting the worker during a long job (e.g. `docker compose restart`) used up one of its 3 allowed takeovers each time, so a job that never crashed could fail with "Crashed too many times". A polite stop is not a crash, and handing the job back also lets work continue within a second instead of 30.
- **Date:** 7 Nov 2026 · **Status:** Approved (7 Nov 2026)

---

## Cleanup rules (normalise)

### D31 — My six cleanup rules
1. Hide emails and phone numbers first, because Turkey's data-protection law (KVKK) cares about personal data, and companies worry about sending customer details to AI.
2. Store each letter one way, because identical-looking letters can otherwise be treated as different.
3. Tidy spaces, because it's harmless and obvious.
4. Lowercase the Turkish way (I → ı, İ → i), because standard software gets Turkish wrong (ISPARTA → "isparta" instead of "ısparta").
5. Keep Turkish letters, because removing them changes meaning (kır = countryside, kir = dirt).
6. Keep emoji, because they carry meaning (🙄 turns praise into sarcasm).
- **Order:** hiding personal details runs first so lowercasing can't change an email before it is hidden. *(Changed 7 Nov 2026: NFC now runs first, see D49; placeholders stay in capitals, see D44.)*
- **Date:** 5 Nov 2026 · **Status:** Approved (6 Nov 2026)

### D44 — Placeholders stay in capitals
- **Decision:** `[EMAIL]` and `[TELEFON]` are not lowercased by rule 4.
- **Why:** In capitals it is obvious that something was hidden there, and they can't be confused with real words.
- **Cost:** A user who types `[EMAIL]` or `[TELEFON]` themselves also keeps it in capitals.
- **Date:** 7 Nov 2026 · **Status:** Approved (7 Nov 2026)

### D49 — NFC runs first, before hiding emails and phones
- **Decision:** new order: 1 NFC, 2 hide emails and phones, 3 tidy spaces, 4 Turkish lowercase (placeholders stay capital), 5 keep Turkish letters, 6 keep emoji.
- **Why:** The code review found that an email with a Turkish letter stored in two parts (e.g. "ş" as "s" plus a separate mark, common in text pasted from a Mac or a PDF) was partly or not at all hidden, so personal data could reach the AI. Joining letters first fixes that. NFC never changes the placeholders, so the reason for hiding early still holds.
- **Date:** 7 Nov 2026 · **Status:** Approved (7 Nov 2026)

---

## Infrastructure and testing

### D32 — SQLite for fast tests, Postgres for production and for some tests
- **Why:** SQLite makes tests run in seconds. But the two databases behave differently for locking and claiming jobs, so the tests that depend on that run on real Postgres in CI.
- **Date:** 6 Nov 2026 · **Status:** Approved (6 Nov 2026)

### D33 — Secrets in a private file, never on GitHub
- **Why:** The repo shows only the names of secrets, so others know what to fill in. Nothing secret is ever uploaded or printed.
- **Date:** 6 Nov 2026 · **Status:** Approved (6 Nov 2026)

### D34 — Dockerfile layer order: dependencies before code
- **Why:** Libraries change rarely and code changes often. Putting libraries first means a one-line code change doesn't re-download everything. E3 measures how much this saves.
- **Date:** 5 Nov 2026 · **Status:** Approved (6 Nov 2026)

### D35 — Registry publishing only from main, never from pull requests
- **Why:** Pull requests can contain anyone's code. Only code already accepted into main may publish, and only main has access to secrets.
- **Date:** 5 Nov 2026 · **Status:** Approved (6 Nov 2026)

### D36 — Fork test not done live; relying on GitHub's documentation
- **Why:** I have no second person with a GitHub account, and GitHub's rules allow only one free personal account per person. I say this openly, cite the documentation, and keep the check in place so it records automatically if a fork pull request ever arrives.
- **Date:** 5 Nov 2026 · **Status:** Approved (6 Nov 2026)

### D37 — Explain `pull_request_target`, never use it
- **Why:** It gives secrets to strangers' pull requests. A deliberately unsafe example in a public project could be copied by someone who doesn't understand it.
- **Date:** 5 Nov 2026 · **Status:** Approved (6 Nov 2026)

### D42 — Tables are created on startup; no migration tool
- **Decision:** When Kuyruk starts, it creates any tables that are missing (SQLModel `create_all`). I don't use a migration tool such as Alembic.
- **Why:** The project will be finished and frozen, so the tables won't change after release. A migration tool would add code and steps that never get used.
- **Cost:** `create_all` never changes a table that already exists. During development, changing a table means wiping the local database (`docker compose down -v`). In a product that keeps changing after release, I would use Alembic.
- **Date:** 6 Nov 2026 · **Status:** Approved (6 Nov 2026)

### D77 — The production Dockerfile sits at the repo root; `docker/` is for E3
- **Options:** `docker/Dockerfile` · `Dockerfile` at the root.
- **Decision:** the one real Dockerfile is `Dockerfile` at the root. `docker/` holds only E3's four comparison Dockerfiles (Slice 13).
- **Why:** `docker compose up` and anyone reading the repo find the real recipe where they expect it, and it can never be mixed up with the deliberately bad experiment versions.
- **Date:** 7 Nov 2026 · **Status:** Approved (7 Nov 2026)

### D78 — Only the API creates the tables; the worker starts after the API is healthy
- **Options:** both create tables on start · only the API does.
- **Decision:** only the API runs `create_all` when it starts. The worker never creates tables; in compose it starts only once the API is healthy, and the API only answers `/health` after its start-up step has finished.
- **Why:** Two programs creating the same tables at the same moment can collide on an empty database. With one owner, the order is simple and certain: database, then API (tables), then worker.
- **Date:** 7 Nov 2026 · **Status:** Approved (7 Nov 2026)

### D79 — The worker's health check is an "I'm alive" file
- **Options:** no health check · "is the process running" · "can it reach the database" · a file the worker keeps re-dating.
- **Decision:** the worker re-dates a small file on every pass of its loop (about once a second when idle) and at every heartbeat (during a job). `python -m app.worker --check-alive` says healthy only while the file is younger than 30 seconds (`WORKER_ALIVE_MAX_AGE_SECONDS`).
- **Why:** The worker is the container's first process, so if it dies the container stops anyway; "is it running" would prove nothing. Reaching the database says nothing about the worker itself. A fresh file proves the worker's loop is really turning, and a frozen worker shows up as unhealthy. Docker only reports unhealthy; restarting after a crash is done by `restart: unless-stopped`.
- **Date:** 7 Nov 2026 · **Status:** Approved (7 Nov 2026)

### D80 — `docker compose up` is a demo with public demo-only secrets, refused outside demo mode
- **Options:** require a `.env` before anything starts · demo values built into compose.
- **Decision:** with no `.env`, compose fills in clearly labelled demo-only values: `DEMO_MODE=true`, the fake AI replaying waiting times, a demo database password and a demo `JWT_SECRET`. Kuyruk refuses to start with the demo `JWT_SECRET` unless `DEMO_MODE` is true. The database and API ports open only to this computer (127.0.0.1). For real use, a `.env` with real secrets and `DEMO_MODE=false` (documented in `.env.example`) wins over every demo value. Settings errors never repeat the values they were given, so a wrong setting can't print part of a secret.
- **Why:** Anyone should be able to try Kuyruk with one command and nothing to fill in (spec 8). But the demo key is public, so anyone could forge a login pass with it; refusing it outside demo mode means it can never quietly end up in real use. Opening the ports only to this computer keeps other machines on the same network out of a database with a public password.
- **Date:** 7 Nov 2026 · **Status:** Approved (7 Nov 2026)

### D81 — Demo timings: heartbeat 1 s, stuck after 5 s
- **Decision:** in the Docker demo, `HEARTBEAT_SECONDS=1` and `STALE_AFTER_SECONDS=5`. Real use goes back to 5 s and 30 s (the defaults in `app/settings.py`), set in `.env`.
- **Why:** With the real defaults, a crash is recovered about 30 seconds later, which is a long, empty wait in a demo or video. With 1 s / 5 s the recovery shows within seconds (the clean-machine check measured 5.1 s). The cost is one small database write per second per running job, fine for a demo.
- **Date:** 7 Nov 2026 · **Status:** Approved (7 Nov 2026)

### D82 — Placeholder waiting times until Slice 10
- **Decision:** until Slice 10 measures the real AI, the demo's fake AI replays `bench/data/placeholder_times.csv`: ten rough values between 0.5 and 1.1 seconds, the range seen once in the Slice 4 check. The file name and `bench/data/README.md` say it is not a measurement. Slice 10 replaces it with `ollama_times.csv` and deletes it. *(Done in Slice 10, 8 Nov 2026: replaced by `ollama_times_at_4.csv`, the times at 4 at once, D93.)*
- **Why:** Replay mode needs a file to start at all, and the demo should feel like the real AI. Naming it "placeholder" means it can never be mistaken for measured data.
- **Date:** 7 Nov 2026 · **Status:** Approved (7 Nov 2026)

### D83 — CI: Postgres tests in their own step; the uv cache can be switched off for E4
- **Decision:** `ci.yml` runs a Postgres 16 service (same image as compose). The fast tests (with the 85% gate) and the Postgres tests (listed one by one) are two separate steps. A manual run (`gh workflow run ci.yml -f uv_cache=false`) can switch uv's download cache off; pushes and pull requests always use it.
- **Why:** As separate steps, the log shows plainly that the Postgres tests ran and passed rather than being skipped. E4 needs about 10 runs with the cache off and 10 with it on; a manual switch gives those without dummy commits and without changing the workflow between runs.
- **Date:** 7 Nov 2026 · **Status:** Approved (7 Nov 2026)

### D84 — The image is published only after CI passes for a push to main
- **Options:** release on every push to main, alongside CI · run the tests again inside the release · start the release when CI finishes (`workflow_run`).
- **Decision:** `release.yml` starts when the CI workflow completes, and publishes only if that CI run succeeded, was started by a push, on main, in this repository. It checks out and builds exactly the commit that CI tested (`head_sha`), never the newest commit on main.
- **Why:** The tests run once, and a commit whose tests fail never gets an image. It is safe only because of those checks: like `pull_request_target` (D37), a `workflow_run` workflow runs with this repository's secrets and, here, permission to publish, even when the CI run it follows came from a stranger's pull request. So it must never act on a pull request run, and never build pull request code.
- **Date:** 7 Nov 2026 · **Status:** Approved (7 Nov 2026)

### D85 — Image tags: the commit id and `latest`
- **Decision:** each published image gets two tags: the full commit id, and `latest`. The image also carries standard labels: source (the repo), licence (MIT), revision (the commit id), title and description.
- **Why:** The commit tag is exact and never moves: that image is that commit, forever. `latest` is convenient: always the newest image from main. The labels let GitHub link the image to the repo and let anyone trace any image back to its commit. `latest` is only moved if the commit is still the newest on main when its release runs, and releases run one at a time, so two quick pushes can never leave `latest` on the older commit. *(Added 7 Nov 2026 after the Slice 9 code review.)*
- **Date:** 7 Nov 2026, updated 7 Nov 2026 · **Status:** Approved (7 Nov 2026; update approved 7 Nov 2026)

### D86 — The clean-machine check runs on a copy without `.env`, under its own name
- **Decision:** `scripts/clean_machine_check.py` copies the project's files (git's list, so no `.env`) into a temporary folder and runs everything under the Docker project name `kuyruk-check`. Images have no fixed name, so each project's images are its own. It removes that project's containers, volume and the images it built (not the shared Postgres image), builds with no cache, starts with `docker compose up`, uses Kuyruk like a visitor (sign up, log in, job, live progress, demo crash, recovery) and prints pass/fail per step, then removes everything again.
- **Why:** My own `.env` and database would hide exactly the problems a first-time visitor would meet. A copy without `.env` behaves like a fresh download, and a separate project name means the check never touches my local data.
- **Date:** 7 Nov 2026 · **Status:** Approved (7 Nov 2026)

---

## Measuring the real AI (Slice 10)

### D87 — The 100 test texts are synthetic reviews written by an AI
- **Decision:** `bench/data/reviews_tr.csv` holds 100 made-up Turkish product reviews written by Claude (an AI), and says so in `bench/data/README.md`. 34 short (up to 200 characters), 33 medium (201–800), 33 long (801–2,000). Some typed without Turkish letters, some with emoji; 12 contain made-up emails (`@example.com`) or phone numbers (`555 000 …`). No real people or brands.
- **Why:** Real customer reviews would bring personal data and copyright questions. Synthetic ones, clearly labelled, are safe to publish, and I checked a random 20 sound natural. The length spread covers everything Kuyruk accepts, and the emails and phones exercise the hiding step on the measured path.
- **Date:** 8 Nov 2026 · **Status:** Approved (8 Nov 2026)

### D88 — Ollama's parallel setting is 8; with this model it has no effect
- **Decision:** all Slice 10 measurements run with Ollama's parallel setting at 8 (`OLLAMA_NUM_PARALLEL=8`), also the one-at-a-time run, so every number comes from one server setup. **Finding:** Ollama 0.40.15 runs this model with its MLX engine, which ignores that setting and answers one request at a time (8 short texts: 3.0 s one after another, 2.9 s sent together; Ollama issue #17280 describes the same, still open). I keep the model (D08) and measure this setup as it is.
- **Why:** Changing the model to get parallel answers would break the pinned version and the Slice 4 check. The finding is useful in itself: sending several texts at once doesn't help if the AI server handles them one by one. Kuyruk's worker still sends 4 at once, which costs nothing and pays off on a server that does answer in parallel.
- **Date:** 8 Nov 2026 · **Status:** Approved (8 Nov 2026)

### D89 — A fresh start before every run: memory emptied, three warm-ups never recorded
- **Decision:** every measured run (the one-at-a-time run and each of the 12 "at once" runs) starts by unloading the model from Ollama, which empties its memory of earlier texts, then sends 3 requests using 3 extra texts that are not among the 100; their times are not recorded.
- **Why:** Loading the model takes about 5 s, a one-off cost and not a text's real time, so the warm-up pays it. Ollama's engine also remembers texts it has already read (a "prefix cache") and answers a repeat faster, because it skips reading it again. Real jobs bring new texts, so each run must start with an empty memory. *(Updated 8 Nov 2026: the first attempt assumed Ollama remembers only the last text and emptied nothing; the 12 "at once" runs then found 99% of each text already remembered and ran about 25% too fast. They were redone.)*
- **Date:** 8 Nov 2026, updated 8 Nov 2026 · **Status:** Approved (8 Nov 2026; update approved 8 Nov 2026)

### D90 — What "seconds" means
- **Decision:** a text's time is the clock time around Kuyruk's own Ollama client call (`OllamaAI`), after `prepare_for_ai`: exactly what a worker waits. Token counts come from Ollama's reply; the measuring script reads them by listening in on the reply, without changing Kuyruk's code.
- **Why:** The fake AI replays these times in place of the real call, so they must measure the same thing the worker waits for, including the trip to Ollama and back.
- **Date:** 8 Nov 2026 · **Status:** Approved (8 Nov 2026)

### D91 — Shuffled texts, interleaved runs
- **Decision:** the 100 texts are sent in a shuffled order (fixed seed). The 12 runs of "texts at once" are interleaved: round 1 = 1, 2, 4, 8; round 2 = 8, 4, 2, 1; round 3 = 2, 8, 1, 4. Each run starts with Ollama's memory emptied (D89). Each run's start time is recorded.
- **Why:** A fanless MacBook Air gets warmer and slower over a long run, and background tasks come and go. Mixing the order spreads that drift over every setting and every text length, so it can't pass for an effect of one of them.
- **Date:** 8 Nov 2026 · **Status:** Approved (8 Nov 2026)

### D92 — The model is pinned by its digest
- **Decision:** settings keep the model name `gemma4:e4b` (Ollama looks models up by name). The exact digest (`eb2177adf764…`, a fingerprint of the model's files) is in `bench/data/setup.md`, and the measuring script refuses to run if the installed model's digest is different.
- **Why:** A name can later point at an updated model; the digest can't. All numbers then provably come from one model.
- **Date:** 8 Nov 2026 · **Status:** Approved (8 Nov 2026)

### D93 — "Stops getting faster", and which times the fake AI replays
- **Decision:** "stops getting faster" is the first step where doubling the texts at once cuts the median total time by less than 10%. The fake AI's default replay file is the per-text times measured at 4 at once (`bench/data/ollama_times_at_4.csv`, the worker's `TEXTS_AT_ONCE`), so the demo, E1 and E2 behave like the real AI under real load. The one-at-a-time times (`bench/data/ollama_times.csv`) are kept for the charts and comparison. If `TEXTS_AT_ONCE` is changed, the replayed times no longer match that load exactly.
- **Why:** A worker sending 4 texts at once waits for each text as long as it takes at 4 at once (on this setup, including the wait in Ollama's queue), not as long as a text alone. Replaying those times keeps the fake honest about real use.
- **Date:** 8 Nov 2026 · **Status:** Approved (8 Nov 2026)

### D94 — Measure on a warm Mac: a 10-minute heat-up first
- **Decision:** every measurement starts by keeping the AI busy for 10 minutes (texts sent, nothing recorded). The one-at-a-time run was redone this way; the first one, on a cool Mac, is kept as `bench/data/ollama_times_cool.csv` as evidence only.
- **Why:** My fanless MacBook Air slows down by about a third as it heats up: the same 100 texts took 132 s cool, 158 s after 3 minutes of work and 179 s after 10. A long real job runs on a warm Mac, so all numbers must come from one condition, the warm one. 3 minutes was tried and was not enough; the Mac kept slowing for about 5–6 minutes.
- **Date:** 8 Nov 2026 · **Status:** Approved (8 Nov 2026)

---

## Experiment E1 (Slice 11)

### D95 — Experiment addresses are off by default and never in the demo
- **Decision:** the E1 addresses (`/experiments/e1/…`) exist only with `EXPERIMENTS_ENABLED=true`; otherwise they answer 404 and are hidden from /docs. Kuyruk refuses to start with it and demo mode both on.
- **Why:** the bad modes freeze the API on purpose; nobody should reach them by accident.
- **Date:** 8 Nov 2026 · **Status:** Approved (8 Nov 2026)

### D96 — What the E1 load test measures (HAND-DESIGNED)
- **Decision:** probes every 100 ms on a fixed clock, without waiting for the last answer: the real `/health` (unchanged, plain `def`) and an `async def` ping. A probe's time runs from sending to the full answer. A slow request's finish time is counted from the moment all were sent. Errors are counted, never dropped. Safeguards so the test measures Kuyruk, not itself: no client connection limit, 900 s timeouts (longer than the longest run), server backlog 4096 with macOS's cap (`kern.ipc.somaxconn`) checked, open-files limit 10,240; every probe records when it was meant to leave and when it left, and lateness over 20 ms is reported. The probes are sent from their own process, started fresh for each run, after one unrecorded warm-up probe of each kind, so a burst of slow replies in the load test can't delay them (found in the trial: 82 ms late in one process). All limits are written to `bench/data/setup.md`.
- **Why:** waiting for answers would hide the worst moments; two probes separate "loop stuck" from "threads used up".
- **Date:** 8 Nov 2026 · **Status:** Approved (8 Nov 2026)

### D97 — E1 runs: one server, 4 settings × 4 waits × 5 repeats, interleaved
- **Decision:** A50, B50, C50, B100 at waits of 0.5 / 1 / 2 / 4 s; one uvicorn process; warm-up not recorded; a fixed shuffled order per repeat; each run's numbers are the p50/p95 of that run, and results report the median and spread (min–max) of the 5 runs.
- **Why:** same protocol as spec 9; interleaving spreads drift (as in D91).
- **Date:** 8 Nov 2026 · **Status:** Approved (8 Nov 2026)

### D98 — E1 real-AI check: 10 texts, 5 repeats, warm Mac
- **Decision:** 10-minute heat-up (D94), memory emptied and 3 warm-ups before every run (D89), same 10 reviews each run. The blocking client is a plain `httpx.Client` sending exactly Kuyruk's request. On the chart, the points sit at the median AI answer time measured in that check's mode-A runs (where answers run one after another, so each is one clean AI time).
- **Why:** fewer requests because Ollama answers one at a time (D88), so 50 would take minutes per run without showing anything new.
- **Date:** 8 Nov 2026 · **Status:** Approved (8 Nov 2026)

### D99 — /health is async def
- **Decision:** /health is `async def`.
- **Why:** E1 showed that when the ~40 helper threads are full, a plain-def /health waits for a thread too (B50 p95 ≈ 0.9 × the wait), so Docker's health check could mark a busy but working API as unhealthy. The async ping stayed at 5–8 ms. Cost: none; /health does no blocking work.
- **Date:** 8 Nov 2026 · **Status:** Approved (8 Nov 2026)

---

## Experiment E2 (Slice 12)

### D100 — The broken version is the worker's own code, run by BackgroundTasks inside the API
- **Decision:** an experiment-only address (`POST /experiments/e2/jobs/{ticket}/run-in-api`, same rules as D95) claims one of your queued jobs for the API and hands it to FastAPI `BackgroundTasks`. The texts are done by the worker's own code (`Worker.process_job`), so they are processed exactly as in the fixed version (4 at once, same retries, same "text started" line). In broken runs no worker runs, so nothing ever looks for stuck jobs.
- **Why:** the two things that matter for E2 differ between broken and fixed: where the job runs (inside the API, so it dies with the API) and whether anything recovers it. Any other difference (e.g. a simpler loop) could be blamed for the result instead.
- **Side effects, written down:** the worker's database calls are short blocking calls; inside the API they run on its event loop and briefly hold up other requests (the E1 mistake). This changes nothing E2 counts (lost, redone, recovery), and `/health` answered after every restart. The broken version also writes the worker's heartbeat every 5 s, which nothing reads.
- **Date:** 9 Nov 2026 · **Status:** Approved by Adar (9 Nov 2026)

### D101 — What E2 measures and how
- **Decision:** a 40-text `llm` job; when 20 texts are finished, the program doing it (the API in broken runs, the worker in fixed runs) is killed with a real SIGKILL (`docker kill`), and the same container is then started by the script (`docker start`). The worker has a restart rule in `compose.yaml`, but Docker's restart rule ignores a container stopped with `docker kill`, because it treats that as a manual stop, so the script restarting the worker is the correct simulation of a real crash restart. The API has no restart rule, so after a real crash it would stay down; restarting it gives the broken version its best chance. A 2 s check first records it if Docker restarted the container anyway. The script reads the job straight from the database every 100 ms on a fixed clock, from the Mac, and uses only the Mac's clock for times. **Lost** = texts not finished (not ok or failed) at the end, whatever their state. **Redone** = extra starts of the same job and position in the "text started" log lines (D75). **Recovery time** = kill → first text finished after it (also split into kill → takeover and takeover → first text). Also recorded: restart time, final state, crash count, /health after the restart, and every look more than 20 ms late.
- **Why:** reading the database (not the API) keeps watching while the API is dead; one clock avoids mixing the Mac's and the database's times; a SIGKILL can't be caught, so no clean-up runs, like a power cut.
- **Date:** 9 Nov 2026 · **Status:** Approved by Adar (9 Nov 2026)

### D102 — E2 runs
- **Decision:** own Compose project (`kuyruk-e2`, `bench/e2_compose.yaml`), tables emptied and the worker stopped before every run. Main: broken vs fixed, fake AI replaying the measured 4-at-once times, heartbeat 5 s, stale 30 s, 5 repeats each, interleaved; broken watched 5 minutes after the restart. Sensitivity: fixed waits of 0.5 / 1 / 2 / 4 s per text, broken vs fixed, 5 repeats each, interleaved; broken watched 60 s after the restart. Stale-time check: fixed only, "stuck after" 10 / 30 / 60 s (heartbeat 5 s), replayed times, 5 repeats each, interleaved; recovery time and redone texts.
- **Why:** same protocol as spec 9. The 5-minute watch in the main runs shows the broken job never finishes; repeating it 20 more times in the sensitivity runs would add ~80 minutes and show nothing new.
- **Date:** 9 Nov 2026 · **Status:** Approved by Adar (9 Nov 2026)

### D103 — E2 real-AI check: 12 texts, killed at 6, warm Mac
- **Decision:** fixed version only, real Ollama, a 12-text job killed when 6 of 12 texts are finished, 5 repeats; a 10-minute heat-up first (D94), Ollama's memory emptied and 3 warm-ups before every run (D89); the same 12 texts every run.
- **Why:** Ollama answers one at a time (D88), so a 40-text job would take minutes per run without showing anything new. Shown as marked points on the charts.
- **Date:** 9 Nov 2026 · **Status:** Approved by Adar (9 Nov 2026)

### D104 — How the cost of the fix is counted
- **Decision:** database writes: Postgres's own counters in `pg_stat_user_tables`, `n_tup_upd` (rows updated) plus `n_tup_ins` (rows inserted) and `n_tup_del` (rows deleted), for every table, read before and after each of 3 uninterrupted 40-text jobs (12 s after the writes, as Postgres saves the counters shortly after). Heartbeat writes = `jobs` updates − one per finished text − 2 (claim and done), checked against job time ÷ 5 s; they are the cost compared with a plain BackgroundTasks job, which has no heartbeat (E2's broken version writes one too only because it reuses the worker's code, D100). Code: lines of code without comments, blank lines or docstrings, counted by the script from `app/worker.py` (all of it, and its recovery parts `take_over_stuck_job`, `heartbeat`, `beat`, `hand_back`, without the demo-only lines in `beat`, the demo crash button's check, D74). The broken version's own file is not counted: it runs the worker's code, so its few lines would understate it.
- **Why:** the database's own counters count every write, including ones a guess would miss; counting from the repo makes the number reproducible.
- **Date:** 9 Nov 2026 · **Status:** Approved by Adar (9 Nov 2026)

### D105 — What E3 compares
- **Decision:** the spec's 2×2 (naive pip vs multi-stage uv slim × bad vs good layer order) as four Dockerfiles in `docker/`: `pip-bad`, `pip-good`, `uv-bad`, `uv-good`. The pip versions use the full `python:3.12.16-trixie` base image and one stage; the uv versions use the slim base and two stages, like the real `Dockerfile` (`uv-good` matches it). Bad order = all the code copied before the libraries are installed; good order = the library list first, the code after. pip installs from a list exported from `uv.lock` (exact versions, no hashes), so both tools install the same versions and only the tool changes. The pip versions are written the way a beginner would: no clean-up of pip's download cache, which stays in the image. One extra, size only: `pip-slim`, identical to `pip-good` in every line except the `FROM` line (checked by a test), so pip-bad/pip-good → pip-slim is the base image's effect alone, and pip-slim → uv-good is the effect of uv plus two stages. Beyond the spec, also timed: a rebuild after adding one package.
- **Why:** the simple versions change three things at once (base, tool, stages); pip-slim splits out the base image. The added-package rebuild checks the third part of Adar's prediction. Exporting from the lock keeps every version pinned.
- **Date:** 9 Nov 2026 · **Status:** Approved by Adar (9 Nov 2026)

### D106 — How E3 measures
- **Decision:** `bench/e3_build.py` builds from a temporary copy of the project (the repo is never changed). Per Dockerfile, one round: clear Docker's build cache (`docker builder prune -af`; base images stay downloaded) → **cold build** → change one line of Kuyruk's code → **rebuild** → add one small pinned package → **rebuild**. Times are the wall-clock time of the `docker build` command on the machine's own clock. 5 rounds, each with the Dockerfiles in a rotated order (interleaved); median and spread reported. Recorded per image: uncompressed size (the sum of its layers' sizes in `docker history`) and compressed size (the image's layers gzipped, roughly what a download costs); the two base images' own sizes; whether the image starts and loads Kuyruk; whether `curl` is inside; and each Dockerfile's lines without comments or blank lines. `pip-slim` is built once per environment, size only.
- **Update (9 Nov 2026, Adar's decision):** the uncompressed size was first read from `docker image inspect`. On the Mac, Docker Desktop (containerd image store) counts there each layer twice, unpacked plus its compressed copy (e.g. uv-good: 259 + 71 = 331 MB), while a standard Docker install counts it once. So the uncompressed size is now the sum of the layers in `docker history`, the same everywhere, and the Mac run was repeated with it.
- **Why:** clearing the cache before each cold build makes every cold build start alike; keeping the base images downloaded keeps their download time (network noise) out, while library downloads, a real part of a build, stay in. The run check tests the "builds fine but breaks when run" risk.
- **Date:** 9 Nov 2026 · **Status:** Approved by Adar (9 Nov 2026)

### D107 — E3's second environment: a manual GitHub workflow
- **Decision:** `.github/workflows/e3.yml` runs the same script on GitHub's `ubuntu-24.04` machine, only when started by hand (`workflow_dispatch`), and offers the CSV as a download; it is then committed as `bench/data/e3_builds_ci.csv`. It never runs on push or pull request.
- **Why:** 5 rounds of 4 Dockerfiles × 3 builds would make every CI run much longer, and would skew E4, which times CI runs. GitHub's machine is x86 (Intel/AMD), not Apple's chip, so sizes may differ slightly; the write-up says so.
- **Date:** 9 Nov 2026 · **Status:** Approved by Adar (9 Nov 2026)

### D108 — How E4 measures the uv cache
- **Decision:** 1 unrecorded warm-up run (cache on), then 20 manual CI runs (`gh workflow run ci.yml -f uv_cache=…`) on one commit: 10 on, 10 off, interleaved on-off-off-on, started one at a time (each starts when the previous one has finished). Job time = job start → end from GitHub's records; waiting for a machine recorded apart. Step times from the timestamps GitHub stamps on each log line (fractions of a second); GitHub's whole-second step times kept as a cross-check. Median and spread reported.
- **Why:** one at a time and interleaved, so runs don't compete for machines or drift with time of day; the warm-up makes sure the cache exists; whole seconds are too coarse for a step that may take under a second.
- **Date:** 9 Nov 2026 · **Status:** Approved by Adar (9 Nov 2026)

### D109 — E4's secret evidence
- **Decision:** the "secret present" lines from runs that already happened (push to main, push to own branch, pull request #1 from own branch, Release after a push) and from the 20 manual runs, each with its run link. Fork pull request: not tested live (D36); GitHub's documentation cited with its link and at most one short quoted line. Release runs that follow the manual runs are counted: each must show its publish job skipped.
- **Why:** every claim points to a run anyone can open; the fork case can't be produced with one account.
- **Date:** 9 Nov 2026 · **Status:** Approved by Adar (9 Nov 2026)

---

## Presentation

### D38 — A full, professional web interface, equal to /docs
- **Why:** The docs page suits developers, but someone non-technical can't follow it. As a consultant I will demo to people who aren't engineers, so anyone should be able to open the interface, use everything, and understand what Kuyruk does.
- **Date:** 6 Nov 2026 · **Status:** Approved (6 Nov 2026)

### D39 — Turkish/English switch
- **Why:** My future clients are Turkish; my professors and the wider audience read English.
- **Date:** 6 Nov 2026 · **Status:** Approved (6 Nov 2026)

### D40 — No online demo; a walkthrough video instead
- **Why:** I want to finish the project and not maintain it. An online demo needs ongoing care, and a broken link looks worse than none. A video shows everything, and anyone technical can run it locally in demo mode.
- **Date:** 6 Nov 2026 · **Status:** Approved (6 Nov 2026)

### D41 — Finished means frozen
- **Why:** All versions are pinned and nothing runs online, so the project stays exactly as I left it.
- **Date:** 6 Nov 2026 · **Status:** Approved (6 Nov 2026)
