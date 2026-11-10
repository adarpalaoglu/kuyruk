"""Write the web interface's story numbers and sample reviews from the data.

Slice 17 (D120). The Welcome page and The experiments page tell each
experiment's story as problem → fix → measured result, with numbers such as
"252 s → 0.007 s" or "+24%". Those numbers must never be typed into the
pages by hand: they would silently go stale after a rerun. Instead this script
computes every one of them from the CSV files in `bench/data/` (the same files
the write-up's numbers are checked against) and writes them to
`web/data/findings.json`, which the pages read. Each number states its
rounding rule below.

It also writes `web/data/sample_reviews.json`: the texts behind the New job
screen's "Use sample reviews" button, all 100 reviews of
`bench/data/reviews_tr.csv` (synthetic reviews, D87) with their line breaks
turned into spaces, so each review fits on one line of the text box. In demo
mode that job takes about 3 minutes: long enough to watch, and to press
"Simulate a crash" (19,192 tokens, so it fits the daily budget 10 times).

It needs no charting library (like `bench/cost_sums.py`), so the number check
(`bench/check_numbers.py`) can run it in CI to confirm the committed files
still match the data. `bench/charts.py` runs it too, so
`bench.run_all --charts-only` refreshes it with the charts.

    uv run python -m bench.web_data
"""

import csv
import json
import math
import re
import statistics
from pathlib import Path

from bench import cost_sums

ROOT = Path(__file__).parent.parent
DATA = Path(__file__).parent / "data"
WEB_DATA = ROOT / "web" / "data"
FINDINGS_FILE = WEB_DATA / "findings.json"
SAMPLES_FILE = WEB_DATA / "sample_reviews.json"

MB = 1_000_000  # Docker counts sizes in decimal megabytes, as the write-up does


def read(data: Path, name: str) -> list[dict]:
    """One CSV file from the data folder as a list of {column: value} rows."""
    with (data / name).open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def median_of(rows: list[dict], column: str) -> float:
    """The median of one column's filled-in cells."""
    return statistics.median(float(r[column]) for r in rows if r[column] != "")


def round_half_up(x: float, digits: int = 0) -> float:
    """Round like a person does (2.5 → 3), not Python's "to even" (2.5 → 2)."""
    factor = 10**digits
    return math.floor(x * factor + 0.5) / factor


def one_significant(x: float) -> int:
    """Round to one significant figure, e.g. 36,522 → 40,000; 31.2 → 30."""
    if x == 0:
        return 0
    power = math.floor(math.log10(abs(x)))
    return int(round_half_up(x / 10**power) * 10**power)


def e1_findings(data: Path) -> dict:
    """E1, the freeze: /health's p95 while 50 slow requests run, at a 4 s wait.

    The story compares the usual way (A, `time.sleep` in `async def`) with the
    fix (C, `await asyncio.sleep`) at the longest wait, 4 s: the median of the
    5 runs' p95 (as in the write-up's Results table).
    """
    runs = [r for r in read(data, "e1_runs.csv") if r["kind"] == "sleep"]

    def p95(setting: str) -> float:
        """Median p95 of /health, in seconds, for one setting at 4 s."""
        group = [
            r for r in runs if r["setting"] == setting and float(r["wait_seconds"]) == 4
        ]
        return median_of(group, "health_p95_ms") / 1000

    bad, good = p95("A50"), p95("C50")
    slow = {int(r["slow_requests"]) for r in runs if r["setting"] == "A50"}
    return {
        # Whole seconds: 252 s.
        "e1_bad_s": int(round_half_up(bad)),
        # Whole minutes: 4 min.
        "e1_bad_min": int(round_half_up(bad / 60)),
        # Whole milliseconds: 7 ms; and the same in seconds, 0.007 s.
        "e1_good_ms": int(round_half_up(good * 1000)),
        "e1_good_s": round_half_up(good, 3),
        # How many times faster: 252 / 0.0069 ≈ 36,300 → 36,000×
        # (`times_faster`).
        "e1_times_faster": times_faster(bad, good),
        "e1_slow_requests": slow.pop() if len(slow) == 1 else None,
    }


def times_faster(bad: float, good: float) -> int:
    """How many times faster the fix is, written as a safe round number.

    Rounded *down* to two significant figures (36,522 → 36,000), so the page
    never claims more than was measured.
    """
    ratio = bad / good
    power = math.floor(math.log10(ratio)) - 1
    return int(math.floor(ratio / 10**power) * 10**power)


def e2_findings(data: Path) -> dict:
    """E2, pulling the plug: lost texts, runs, and recovery time.

    Runs as in the write-up's lost/redone table: broken = main + sensitivity;
    fixed = main + sensitivity + the real-AI check. The recovery range is the
    lowest and highest median over the recovery-vs-wait rows (four waits,
    replayed times, real AI), in whole seconds.
    """
    runs = [r for r in read(data, "e2_runs.csv") if not r["error"]]

    def pick(**match: str) -> list[dict]:
        """The good runs whose columns have exactly the given values."""
        return [r for r in runs if all(r[k] == v for k, v in match.items())]

    broken = pick(kind="broken", part="main") + pick(kind="broken", part="sensitivity")
    ai = pick(part="ai")
    fixed = pick(kind="fixed", part="main") + pick(kind="fixed", part="sensitivity")
    waits = sorted({r["wait"] for r in pick(part="sensitivity", kind="fixed")})
    groups = [pick(part="sensitivity", kind="fixed", wait=w) for w in waits]
    groups += [pick(part="main", kind="fixed"), ai]
    medians = [median_of(g, "recovery_s") for g in groups]
    main = pick(part="main", kind="fixed")
    stale = {int(float(r["stale_seconds"])) for r in main}
    lost_fixed = {int(r["lost"]) for r in fixed + ai}
    return {
        "e2_broken_runs": len(broken),
        "e2_fixed_runs": len(fixed) + len(ai),
        "e2_ai_runs": len(ai),
        # 0 lost in every fixed run (None if the runs disagree).
        "e2_fixed_lost": lost_fixed.pop() if len(lost_fixed) == 1 else None,
        # Every broken run never finished (no recovery time): True/False.
        "e2_broken_never_finished": all(r["recovery_s"] == "" for r in broken),
        "e2_recovery_min_s": int(round_half_up(min(medians))),
        "e2_recovery_max_s": int(round_half_up(max(medians))),
        # "About 30 seconds": the main runs' median, to the nearest 10 s.
        "e2_recovery_about_s": int(round_half_up(median_of(main, "recovery_s"), -1)),
        "e2_stale_s": stale.pop() if len(stale) == 1 else None,
    }


def e3_findings(data: Path) -> dict:
    """E3, the package: image sizes and the one-line-change rebuild (Mac).

    Simple (pip-bad) against careful (uv-good), as in the write-up. The base
    image's share is the part of the pip-good → uv-good gap that changing the
    base image alone (pip-good → pip-slim) removes.
    """
    images = {
        r["image"]: float(r["image_bytes"]) / MB for r in read(data, "e3_images.csv")
    }
    builds = read(data, "e3_builds.csv")
    builds_ci = read(data, "e3_builds_ci.csv")

    def rebuild(name: str) -> float:
        """Median seconds of the one-line-change rebuild on the Mac."""
        group = [
            r
            for r in builds
            if r["dockerfile"] == name and r["step"] == "code" and r["ok"] == "yes"
        ]
        return median_of(group, "seconds")

    gap = images["pip-good"] - images["uv-good"]
    base_share = (images["pip-good"] - images["pip-slim"]) / gap
    slow, fast = rebuild("pip-bad"), rebuild("uv-good")
    versions = {r["image"] for r in read(data, "e3_images.csv")} - {
        "base-full",
        "base-slim",
    }
    return {
        "e3_bad_mb": int(round_half_up(images["pip-bad"])),
        # The same in gigabytes, one decimal: 1.3 GB.
        "e3_bad_gb": round_half_up(images["pip-bad"] / 1000, 1),
        "e3_good_mb": int(round_half_up(images["uv-good"])),
        # Whole times smaller: 1,331 / 259 = 5.1 → 5×.
        "e3_times_smaller": int(round_half_up(images["pip-bad"] / images["uv-good"])),
        "e3_rebuild_bad_s": round_half_up(slow, 1),
        "e3_rebuild_good_s": round_half_up(fast, 1),
        # One significant figure: 15.6 / 0.5 ≈ 30×.
        "e3_times_faster": one_significant(slow / fast),
        "e3_base_percent": int(round_half_up(base_share * 100)),
        "e3_rest_percent": 100 - int(round_half_up(base_share * 100)),
        "e3_versions": len(versions),
        "e3_builds": len(builds) + len(builds_ci),
    }


def e4_findings(data: Path) -> dict:
    """E4, the automatic checks: job time with uv's cache on and off.

    Good runs only (no error, a job time), as in the write-up's table. Whole
    seconds. The download time backs the page's words "under half a second".
    """
    runs = read(data, "e4_runs.csv")
    good = {
        cache: [
            r for r in runs if r["cache"] == cache and not r["error"] and r["job_s"]
        ]
        for cache in ("on", "off")
    }
    on = int(round_half_up(median_of(good["on"], "job_s")))
    off = int(round_half_up(median_of(good["off"], "job_s")))
    secrets = read(data, "e4_secrets.csv")
    skipped = [
        r
        for r in secrets
        if r["workflow"] == "release.yml" and r["note"].startswith("after a manual")
    ]
    return {
        "e4_cache_on_s": on,
        "e4_cache_off_s": off,
        "e4_saved_s": off - on,
        "e4_runs": len(good["on"]) + len(good["off"]),
        "e4_download_s": round_half_up(median_of(good["off"], "prepared_s"), 2),
        # Release runs after a manual run whose publish job was skipped.
        "e4_release_skipped": sum(
            r["secret_present"] == "step not run" for r in skipped
        ),
        "e4_release_after_manual": len(skipped),
    }


def cost_findings(data: Path) -> dict:
    """The Turkish vs English cost, with Kuyruk's own counter (o200k_base).

    The sum ratio judges it (D112); tokens per word to one decimal.
    """
    rows = cost_sums.read_rows(data / "cost_tokens.csv")
    ratio = cost_sums.sum_ratio(rows, "o200k")
    return {
        "cost_percent_more": int(round_half_up((ratio - 1) * 100)),
        "cost_reviews": len(rows),
        "cost_tr_per_word": round_half_up(
            cost_sums.tokens_per_word(rows, "o200k", "tr"), 1
        ),
        "cost_en_per_word": round_half_up(
            cost_sums.tokens_per_word(rows, "o200k", "en"), 1
        ),
    }


def findings(data: Path = DATA) -> dict:
    """Every story number, computed fresh from the CSV files."""
    return (
        e1_findings(data)
        | e2_findings(data)
        | e3_findings(data)
        | e4_findings(data)
        | cost_findings(data)
    )


def sample_reviews(data: Path = DATA) -> list[str]:
    """All the sample reviews, in file order, each on one line.

    The New job box takes one text per line, so a line break inside a review
    (with any spaces around it) becomes one space.
    """
    return [
        re.sub(r"\s*\n\s*", " ", row["text"].strip())
        for row in read(data, "reviews_tr.csv")
    ]


def as_json(value: object) -> str:
    """The exact text written to a file: readable, Turkish letters kept."""
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write(data: Path = DATA, out: Path = WEB_DATA) -> None:
    """Write findings.json and sample_reviews.json into `out`."""
    out.mkdir(parents=True, exist_ok=True)
    (out / FINDINGS_FILE.name).write_text(as_json(findings(data)), encoding="utf-8")
    (out / SAMPLES_FILE.name).write_text(
        as_json(sample_reviews(data)), encoding="utf-8"
    )


def main() -> None:
    """Write both files and say where."""
    write()
    print(f"Interface numbers written to {FINDINGS_FILE.relative_to(ROOT)}.")


if __name__ == "__main__":
    main()
