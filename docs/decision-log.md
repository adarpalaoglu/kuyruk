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
- **Decision:** on Ctrl+C or a stop signal, the worker starts no new texts and waits at most `SHUTDOWN_GRACE_SECONDS` = 8 for texts already with the AI, then exits. Texts not finished by then stay waiting; the job stays running and is continued by crash recovery (Slice 8), so those texts may be done twice ("at least once", D19).
- **Why:** Docker kills a container 10 seconds after asking it to stop, so a longer wait (e.g. the AI's 120-second timeout) would never really happen there. Compose must keep Docker's stop time above this setting (Slice 9).
- **Date:** 7 Nov 2026 · **Status:** Approved (7 Nov 2026)

### D56 — A bug inside a task fails only that text
- **Decision:** if a task crashes because of a mistake in Kuyruk (not an AI error), that text is marked failed with "Internal error." and the worker carries on. The details go only to the worker's log.
- **Why:** One bad text shouldn't stop the worker or the whole job, and the reply must not show how Kuyruk works inside.
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
