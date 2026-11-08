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
*(to be filled in after the run)*

### Results
*(to be filled in after the run)*

### Why it happens
*(to be filled in)*

### Surprises
*(to be filled in)*

### Takeaway
*(to be filled in)*

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
