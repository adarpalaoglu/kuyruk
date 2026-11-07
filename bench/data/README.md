# bench/data — measured data for Kuyruk's experiments

Raw numbers live here as CSV files (plain tables, values separated by commas).

## `placeholder_times.csv` — NOT a measurement (D82)

A stand-in, used only until Slice 10. The fake AI in demo mode (`docker compose
up`) picks each text's waiting time from a file like this one ("replay" mode).
The real file, `ollama_times.csv`, will hold 100 measured Gemma times; it
doesn't exist yet.

These ten values are rough: they span the 0.5–1.1 seconds per short review
seen once in the Slice 4 check of the real AI (7 Nov 2026). They are not
measured carefully and must never appear in a result or chart. In Slice 10,
`ollama_times.csv` replaces it, `compose.yaml` stops pointing at it, and this
file is deleted.
