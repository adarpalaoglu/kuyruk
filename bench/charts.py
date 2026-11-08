"""Draw the experiment charts from the measured CSV files, and print key numbers.

Slice 10 (spec 9.0). Reads only the CSV files in `bench/data/` made by
`bench/measure_ollama.py`, so anyone can redraw the charts without Ollama.
Writes three pictures (PNG files) to `docs/charts/`:

- `ollama_times_distribution.png`: how the per-text times are spread, one at a
  time and at 4 at once (the worker's setting);
- `ollama_time_vs_length.png`: per-text time against text length, on a warm
  and on a cool Mac;
- `texts_at_once.png`: total time for the 100 texts at 1, 2, 4 and 8 at once.

It also prints the key numbers: median, 5th and 95th percentile per text, the
median total time at each setting, and where it stops getting faster (D93).

E1 (Slice 11, spec 9.1), from the files made by `bench/e1_load.py`, if they
exist:

- `e1_health_p95.png`: how slow /health's unlucky answers (p95) get while the
  slow requests run, against the wait time, one line per setting; real-AI
  points marked;
- `e1_slow_finish.png`: when each slow request finished, per setting.

And the E1 key numbers: per setting and wait, the median (and min-max) of the
5 runs (D97).

matplotlib (the charting library) is in the separate `bench` dependency group,
so Kuyruk itself, its tests and its Docker image don't need it. Run:

    uv run --group bench python -m bench.charts
"""

import csv
import statistics
from itertools import zip_longest
from pathlib import Path

import matplotlib

# Draw straight to files; no window is opened.
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402 (must come after the line above)

DATA = Path(__file__).parent / "data"
CHARTS = Path(__file__).parent.parent / "docs" / "charts"

# "Stops getting faster" (D93): the first doubling of texts at once that cuts
# the median total time by less than this share.
SPEED_UP_THRESHOLD = 0.10

# Colours and lines: one calm colour for the data, dark text, a faint grid.
SERIES = "#2a78d6"
SERIES_2 = "#eb6834"
TEXT = "#0b0b0b"
MUTED = "#52514e"
GRID = "#e4e3df"
SURFACE = "#fcfcfb"

# E1: one fixed colour and marker shape per setting (the shape also tells the
# settings apart without colour). Checked with the dataviz palette validator.
E1_STYLE = {
    "A50": ("#eb6834", "o", "A: async def + time.sleep (50)"),
    "B50": ("#2a78d6", "s", "B: plain def (50)"),
    "B100": ("#eda100", "D", "B: plain def (100, more than the threads)"),
    "C50": ("#1baf7a", "^", "C: async def + await asyncio.sleep (50)"),
}
# The wait shown on the finish-time chart.
E1_FINISH_WAIT = 2.0


def read(name: str) -> list[dict]:
    """One CSV file from bench/data as a list of {column: value} rows."""
    with (DATA / name).open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def percentile(values: list[float], share: float) -> float:
    """The value below which `share` of the values lie (e.g. 0.95 → 95th).

    Uses the "inclusive" method: the smallest value is the 0th percentile and
    the largest the 100th, with straight-line steps in between.
    """
    cuts = statistics.quantiles(values, n=100, method="inclusive")
    return cuts[round(share * 100) - 1]


def style(axes: plt.Axes, title: str, x_label: str, y_label: str) -> None:
    """Shared look: title, axis names, a faint grid behind the data."""
    axes.set_title(title, loc="left", color=TEXT, fontsize=12, pad=12)
    axes.set_xlabel(x_label, color=MUTED)
    axes.set_ylabel(y_label, color=MUTED)
    axes.set_facecolor(SURFACE)
    axes.grid(True, color=GRID, linewidth=0.8)
    axes.set_axisbelow(True)
    axes.tick_params(colors=MUTED)
    for side in ("top", "right"):
        axes.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        axes.spines[side].set_color(GRID)


def total(rows: list[dict]) -> float:
    """The sum of the per-text times in some rows, in seconds."""
    return sum(float(r["seconds"]) for r in rows)


def save(figure: plt.Figure, name: str) -> None:
    """Write one chart to docs/charts/."""
    CHARTS.mkdir(parents=True, exist_ok=True)
    figure.patch.set_facecolor(SURFACE)
    figure.tight_layout()
    figure.savefig(CHARTS / name, dpi=150)
    plt.close(figure)


def distribution_chart(alone: list[float], at_4: list[float]) -> None:
    """Two histograms on one time scale: one at a time, and 4 at once.

    A histogram groups the times into narrow ranges ("bins") and shows how
    many texts fall in each. The dashed line marks the median.
    """
    figure, (top, bottom) = plt.subplots(2, 1, figsize=(8, 6), sharex=True)
    largest = max(alone + at_4)
    bins = [largest * step / 40 for step in range(41)]
    for axes, times, colour, title in (
        (top, alone, SERIES, f"One at a time ({len(alone)} texts, warm Mac)"),
        (bottom, at_4, SERIES_2, f"4 at once ({len(at_4)} text runs)"),
    ):
        axes.hist(times, bins=bins, color=colour, edgecolor=SURFACE, linewidth=1)
        median = statistics.median(times)
        axes.axvline(median, color=TEXT, linestyle="--", linewidth=1)
        axes.annotate(
            f"median {median:.2f} s",
            xy=(median, 1),
            xycoords=("data", "axes fraction"),
            xytext=(4, -14),
            textcoords="offset points",
            color=TEXT,
            fontsize=9,
        )
        style(axes, title, "", "Texts")
    bottom.set_xlabel("Seconds per text (Gemma 4 E4B on Ollama)", color=MUTED)
    save(figure, "ollama_times_distribution.png")


def length_chart(warm: list[dict], cool: list[dict]) -> None:
    """Each text as a dot: its length across, its time up (one at a time).

    Two runs of the same 100 texts: on a warm Mac (the measurement) and on a
    cool one (the first attempt), to show how much the Mac slows when warm.
    """
    figure, axes = plt.subplots(figsize=(8, 5))
    for rows, colour, label in (
        (warm, SERIES, f"Warm Mac (all 100: {total(warm):.0f} s)"),
        (cool, SERIES_2, f"Cool Mac (all 100: {total(cool):.0f} s)"),
    ):
        axes.scatter(
            [int(r["characters"]) for r in rows],
            [float(r["seconds"]) for r in rows],
            s=24,
            color=colour,
            edgecolors=SURFACE,
            linewidths=1,
            label=label,
        )
    style(
        axes,
        "Per-text time against text length (one at a time, 100 texts)",
        "Characters in the text",
        "Seconds",
    )
    axes.legend(frameon=False, labelcolor=TEXT, loc="upper left")
    axes.set_xlim(left=0)
    axes.set_ylim(bottom=0)
    save(figure, "ollama_time_vs_length.png")


def at_once_chart(runs: list[dict]) -> dict[int, float]:
    """Total time per setting: each run as a small dot, the median as a bar.

    Returns {texts at once: median total seconds}.
    """
    settings = sorted({int(r["texts_at_once"]) for r in runs})
    totals = {
        n: [float(r["total_seconds"]) for r in runs if int(r["texts_at_once"]) == n]
        for n in settings
    }
    medians = {n: statistics.median(totals[n]) for n in settings}
    figure, axes = plt.subplots(figsize=(8, 5))
    positions = range(len(settings))
    axes.bar(positions, [medians[n] for n in settings], width=0.5, color=SERIES)
    for position, n in zip(positions, settings, strict=True):
        # The single runs, so the spread between repeats is visible.
        axes.scatter(
            [position] * len(totals[n]),
            totals[n],
            s=18,
            color=TEXT,
            zorder=3,
        )
        axes.annotate(
            f"{medians[n]:.0f} s",
            xy=(position, medians[n]),
            xytext=(0, 6),
            textcoords="offset points",
            ha="center",
            color=TEXT,
            fontsize=9,
        )
    axes.set_xticks(list(positions), [str(n) for n in settings])
    style(
        axes,
        "Total time for the same 100 texts (bar: median; dots: each run)",
        "Texts sent at once",
        "Seconds for all 100 texts",
    )
    axes.set_ylim(bottom=0, top=max(max(t) for t in totals.values()) * 1.15)
    save(figure, "texts_at_once.png")
    return medians


def stops_getting_faster(medians: dict[int, float]) -> str:
    """Where doubling the texts at once first saves less than 10% (D93)."""
    settings = sorted(medians)
    for smaller, bigger in zip(settings, settings[1:], strict=False):
        saving = 1 - medians[bigger] / medians[smaller]
        if saving < SPEED_UP_THRESHOLD:
            return (
                f"stops getting faster after {smaller} at once "
                f"({smaller} → {bigger} saves {saving:.1%})"
            )
    return f"still more than {SPEED_UP_THRESHOLD:.0%} faster at {settings[-1]}"


def summary(name: str, times: list[float]) -> str:
    """One line: median, 5th and 95th percentile of some per-text times."""
    return (
        f"{name}: median {statistics.median(times):.2f} s, "
        f"5th percentile {percentile(times, 0.05):.2f} s, "
        f"95th percentile {percentile(times, 0.95):.2f} s"
    )


# ---- E1 ---------------------------------------------------------------------


def median_and_spread(values: list[float]) -> str:
    """ "median (min-max)" of some numbers, for the printed key numbers."""
    return f"{statistics.median(values):.1f} ({min(values):.1f}-{max(values):.1f})"


def by_setting(rows: list[dict], column: str) -> dict[tuple[str, float], list[float]]:
    """{(setting, wait): [one value per run]} for one column of e1_runs.csv."""
    grouped: dict[tuple[str, float], list[float]] = {}
    for row in rows:
        if row[column] != "":
            key = (row["setting"], float(row["wait_seconds"] or 0))
            grouped.setdefault(key, []).append(float(row[column]))
    return grouped


def e1_health_chart(
    sleep: list[dict], ai: list[dict], after_fix: list[dict] | None = None
) -> None:
    """/health p95 against the wait, one line per setting, log scales.

    Dots: the 5 runs; line: their median (D97). Faint dashed line: the async
    ping's p95 median, which needs no helper thread. Real-AI points: hollow
    marks at the AI's typical answer time (median of the mode-A runs, D98).
    """
    health = by_setting(sleep, "health_p95_ms")
    ping = by_setting(sleep, "ping_p95_ms")
    figure, axes = plt.subplots(figsize=(9, 7))
    waits = sorted({wait for _, wait in health})
    for setting, (colour, marker, name) in E1_STYLE.items():
        # Only the waits this setting has data for: a stopped run, or a run
        # where every probe failed, leaves gaps (drawn as gaps, not zeros).
        have = [w for w in waits if health.get((setting, w))]
        if not have:
            continue
        for wait in have:
            values = health[(setting, wait)]
            axes.scatter([wait] * len(values), values, s=14, color=colour, alpha=0.35)
        medians = [statistics.median(health[(setting, w)]) for w in have]
        axes.plot(
            have,
            medians,
            color=colour,
            marker=marker,
            markersize=8,
            linewidth=2,
            markeredgecolor=SURFACE,
            label=name,
        )
        ping_waits = [w for w in waits if ping.get((setting, w))]
        axes.plot(
            ping_waits,
            [statistics.median(ping[(setting, w)]) for w in ping_waits],
            color=colour,
            linewidth=1,
            linestyle="--",
            alpha=0.6,
        )
        # A direct label at the line's end, in text colour.
        axes.annotate(
            setting,
            xy=(have[-1], medians[-1]),
            xytext=(8, 0),
            textcoords="offset points",
            va="center",
            color=TEXT,
            fontsize=9,
        )
    if ai:
        ai_health = by_setting(ai, "health_p95_ms")
        steps = [
            float(r["server_step_median_s"])
            for r in ai
            if r["mode"] == "A" and r["server_step_median_s"]
        ]
        x = statistics.median(steps)
        for setting, values in ai_health.items():
            colour, marker, _ = E1_STYLE[f"{setting[0][0]}50"]
            axes.scatter(
                [x],
                [statistics.median(values)],
                s=120,
                marker=marker,
                facecolors="none",
                edgecolors=colour,
                linewidths=2,
                zorder=4,
            )
        # B and C sit on top of each other, so they share one label, placed
        # above the low lines; A gets its own.
        modes = sorted(ai_health)
        for group, y_offset in (
            ([k for k in modes if k[0][0] == "A"], 0),
            ([k for k in modes if k[0][0] != "A"], 26),
        ):
            if not group:
                continue
            top = max(statistics.median(ai_health[k]) for k in group)
            axes.annotate(
                "real AI, " + " and ".join(k[0][0] for k in group),
                xy=(x, top),
                xytext=(-12, y_offset),
                textcoords="offset points",
                ha="right",
                va="center",
                color=TEXT,
                fontsize=8,
                arrowprops={"arrowstyle": "-", "color": MUTED, "linewidth": 0.8}
                if y_offset
                else None,
            )
        # One legend entry for every hollow mark.
        axes.scatter(
            [],
            [],
            s=120,
            marker="o",
            facecolors="none",
            edgecolors=MUTED,
            linewidths=2,
            label=f"real AI (10 requests), at {x:.1f} s per answer",
        )
    if after_fix:
        # The confirmation run after /health became async def (D99): stars
        # in the setting's colour at the wait it was run at.
        fixed = by_setting(after_fix, "health_p95_ms")
        for (setting, wait), values in sorted(fixed.items()):
            colour = E1_STYLE[setting][0]
            axes.scatter(
                [wait],
                [statistics.median(values)],
                s=160,
                marker="*",
                color=colour,
                edgecolors=TEXT,
                linewidths=0.6,
                zorder=5,
            )
        axes.annotate(
            "B50 and B100 after\n/health became async def",
            xy=(1.0, max(statistics.median(v) for v in fixed.values())),
            xytext=(-40, 55),
            textcoords="offset points",
            ha="right",
            color=TEXT,
            fontsize=8,
            arrowprops={"arrowstyle": "-", "color": MUTED, "linewidth": 0.8},
        )
        axes.scatter(
            [],
            [],
            s=160,
            marker="*",
            color=MUTED,
            edgecolors=TEXT,
            linewidths=0.6,
            label="after /health became async def (D99)",
        )
    axes.plot([], [], color=MUTED, linewidth=1, linestyle="--", label="async ping p95")
    axes.set_xscale("log", base=2)
    axes.set_yscale("log")
    axes.set_xticks(waits, [f"{w:g} s" for w in waits])
    style(
        axes,
        "/health p95 while 50 (or 100) slow requests run (line: median of 5 runs)",
        "Wait of each slow request",
        "/health p95, milliseconds (log scale)",
    )
    # The legend sits under the chart, so it covers no line.
    axes.legend(
        frameon=False,
        labelcolor=TEXT,
        loc="upper left",
        bbox_to_anchor=(0, -0.12),
        ncol=2,
        fontsize=8,
    )
    save(figure, "e1_health_p95.png")


def e1_finish_chart(slow: list[dict]) -> None:
    """When each slow request finished, at one wait, per setting.

    Requests sorted by finish time; for each place in that order, the median
    over the 5 runs. "One after another" is a rising line, "waves" are
    steps, "all at once" is flat.
    """
    figure, axes = plt.subplots(figsize=(9, 5.5))
    for setting, (colour, marker, name) in E1_STYLE.items():
        runs: dict[str, list[float]] = {}
        for row in slow:
            # Only requests that really got their answer: a failed one's
            # time is when it failed, not when it finished.
            if (
                row["setting"] == setting
                and float(row["wait_seconds"]) == E1_FINISH_WAIT
                and row["ok"] == "True"
            ):
                runs.setdefault(row["repeat"], []).append(float(row["finished"]))
        if not runs:
            continue
        ordered = [sorted(times) for times in runs.values()]
        # For each place in the finishing order, the median over the runs
        # that have that place (no run is cut short to match another).
        medians = [
            statistics.median([t for t in place if t is not None])
            for place in zip_longest(*ordered)
        ]
        places = range(1, len(medians) + 1)
        # A marker on every 10th request tells the lines apart without colour.
        axes.plot(
            places,
            medians,
            color=colour,
            linewidth=2,
            marker=marker,
            markevery=10,
            markersize=7,
            markeredgecolor=SURFACE,
            label=name,
        )
        # B50 ends right on the B100 line, so its label goes just below.
        axes.annotate(
            f"{setting}: last at {medians[-1]:.1f} s",
            xy=(len(medians), medians[-1]),
            xytext=(6, -12 if setting == "B50" else 0),
            textcoords="offset points",
            va="center",
            color=TEXT,
            fontsize=9,
        )
    axes.set_yscale("log")
    style(
        axes,
        f"When each slow request finished (wait {E1_FINISH_WAIT:g} s, "
        "median of 5 runs)",
        "Slow requests, in the order they finished",
        "Seconds after all were sent (log scale)",
    )
    axes.legend(frameon=False, labelcolor=TEXT, loc="lower right", fontsize=8)
    axes.set_xlim(right=125)
    save(figure, "e1_slow_finish.png")


def e1_numbers(rows: list[dict]) -> None:
    """Print the E1 key numbers: median (min-max) of the 5 runs (D97)."""
    columns = [
        ("health_p50_ms", "/health p50 ms"),
        ("health_p95_ms", "/health p95 ms"),
        ("ping_p95_ms", "ping p95 ms"),
        ("slow_last_finish_s", "last slow done s"),
        ("errors", "errors"),
        ("max_probe_late_ms", "latest probe ms"),
    ]
    grouped = {column: by_setting(rows, column) for column, _ in columns}
    keys = sorted(grouped["health_p95_ms"], key=lambda k: (k[1], k[0]))
    for key in keys:
        # A column can be empty for a setting (e.g. every ping failed).
        parts = [
            f"{title} {median_and_spread(grouped[c][key])}"
            if grouped[c].get(key)
            else f"{title} n/a"
            for c, title in columns
        ]
        print(f"E1 {rows[0]['kind']} {key[0]:>5} wait {key[1]:g}: " + "; ".join(parts))


def e1_charts() -> None:
    """Draw the E1 charts and print its numbers, if E1 has been measured."""
    if not (DATA / "e1_runs.csv").exists():
        return
    runs = read("e1_runs.csv")
    sleep = [r for r in runs if r["kind"] == "sleep"]
    ai = [r for r in runs if r["kind"] == "ai"]
    after_fix = [r for r in runs if r["kind"] == "after_fix"]
    if sleep:
        e1_health_chart(sleep, ai, after_fix)
        e1_finish_chart([r for r in read("e1_slow.csv") if r["kind"] == "sleep"])
        e1_numbers(sleep)
    if ai:
        e1_numbers(ai)
    if after_fix:
        e1_numbers(after_fix)


def main() -> None:
    """Draw all the charts and print the key numbers."""
    alone_rows = read("ollama_times.csv")
    cool_rows = read("ollama_times_cool.csv")
    alone = [float(r["seconds"]) for r in alone_rows]
    at_4 = [float(r["seconds"]) for r in read("ollama_times_at_4.csv")]
    distribution_chart(alone, at_4)
    length_chart(alone_rows, cool_rows)
    medians = at_once_chart(read("texts_at_once.csv"))

    print(summary("Per text, one at a time (warm Mac)", alone))
    print(
        summary(
            "Per text, one at a time (cool Mac)",
            [float(r["seconds"]) for r in cool_rows],
        )
    )
    print(
        f"All 100 one at a time: {total(alone_rows):.0f} s warm, "
        f"{total(cool_rows):.0f} s cool"
    )
    print(summary("Per text, 4 at once", at_4))
    for n, seconds in medians.items():
        print(f"{n} at once: median {seconds:.1f} s for all 100 texts")
    print("It", stops_getting_faster(medians) + ".")
    e1_charts()
    print(f"Charts written to {CHARTS}.")


if __name__ == "__main__":
    main()
