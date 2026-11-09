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

E2 (Slice 12, spec 9.2), from the files made by `bench/e2_kill.py`, if they
exist:

- `e2_lost_redone.png`: texts lost and texts done twice per run, broken vs
  fixed (and the real-AI check);
- `e2_recovery.png`: recovery time (kill -> first text finished after it)
  against the wait per text and against the stale time; broken runs shown
  as "never".

And the E2 key numbers per setting, and the cost of the fix (D104).

E3 (Slice 13, spec 9.3), from the files made by `bench/e3_build.py`, if they
exist (the Mac, and GitHub Actions with `_ci` in the name):

- `e3_image_size.png`: each image's size, split into its base image and what
  the Dockerfile adds on top;
- `e3_build_time.png`: cold build, rebuild after a one-line code change and
  rebuild after adding one package, per Dockerfile.

And the E3 key numbers: sizes, how many times smaller, how much of the size
gap is the base image alone (D105), and the median (min-max) build times.

E4 (Slice 14, spec 9.4), from the files made by `bench/e4_ci.py`, if they
exist:

- `e4_cache.png`: CI job time with uv's cache on vs off (every run a dot,
  the median a line), and where the time goes (median per part of the job).

And the E4 key numbers: median (min-max) of the job and of each part, with
the cache on and off, uv's own download times, and the secret checks (D109).

matplotlib (the charting library) is in the separate `bench` dependency group,
so Kuyruk itself, its tests and its Docker image don't need it. Run:

    uv run --group bench python -m bench.charts
"""

import csv
import statistics
from collections import Counter
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


# ---- E2 ---------------------------------------------------------------------

# Broken and fixed: one fixed colour and marker shape each (the shape also
# tells them apart without colour). The real-AI check gets its own marker.
E2_STYLE = {
    "broken": ("#eb6834", "o", "Broken: BackgroundTasks inside the API"),
    "fixed": ("#2a78d6", "s", "Fixed: separate worker + recovery"),
}
E2_AI_STYLE = ("#1baf7a", "D", "Fixed, real AI (12 texts)")


def numbers(rows: list[dict], column: str) -> list[float]:
    """The values of one column that were measured (blank = never happened)."""
    return [float(r[column]) for r in rows if r.get(column, "") != ""]


def kill_runs(runs: list[dict], **match: str) -> list[dict]:
    """The kill runs that measured something (no error) and match `match`."""
    return [
        r
        for r in runs
        if not r.get("error") and all(r.get(k) == v for k, v in match.items())
    ]


def e2_lost_redone_chart(runs: list[dict]) -> None:
    """Lost and redone texts per run, broken vs fixed (main runs, real AI).

    Every run is one dot, spread a little sideways so equal values don't
    hide each other; the short line is the median of the runs.
    """
    figure, panels = plt.subplots(1, 2, figsize=(10, 5), sharey=False)
    groups = [
        ("Broken", kill_runs(runs, part="main", kind="broken"), E2_STYLE["broken"]),
        ("Fixed", kill_runs(runs, part="main", kind="fixed"), E2_STYLE["fixed"]),
        ("Fixed, real AI", kill_runs(runs, part="ai", kind="fixed"), E2_AI_STYLE),
    ]
    for axes, column, title in zip(
        panels,
        ["lost", "redone"],
        ["Texts lost (never finished)", "Texts done twice"],
        strict=True,
    ):
        for place, (_, rows, (colour, marker, _)) in enumerate(groups):
            values = numbers(rows, column)
            if not values:
                continue
            offsets = [(i - (len(values) - 1) / 2) * 0.06 for i in range(len(values))]
            axes.scatter(
                [place + o for o in offsets],
                values,
                color=colour,
                marker=marker,
                s=45,
                edgecolor=SURFACE,
                zorder=3,
            )
            middle = statistics.median(values)
            axes.plot([place - 0.25, place + 0.25], [middle, middle], color=TEXT)
            axes.annotate(
                f"{middle:g}",
                xy=(place + 0.27, middle),
                va="center",
                color=TEXT,
                fontsize=9,
            )
        axes.set_xticks(range(len(groups)), [g[0] for g in groups])
        axes.set_xlim(-0.6, len(groups) - 0.4)
        axes.set_ylim(bottom=-0.5)
        style(axes, title, "", "Texts")
    figure.suptitle(
        "Killed halfway: what is lost and what is redone (each dot is one run)",
        x=0.01,
        ha="left",
        color=TEXT,
    )
    save(figure, "e2_lost_redone.png")


def e2_recovery_chart(runs: list[dict], replay_median: float) -> None:
    """Recovery time (kill -> first text finished after it), fixed runs.

    Left: against the wait per text (sensitivity runs at fixed waits, the
    main runs at the replayed times' median, real-AI points marked); broken
    runs never recover, drawn as a band at the top. Right: against the
    stale time ("stuck after"). Dot = median of the runs, bar = min-max.
    """
    figure, (left, right) = plt.subplots(1, 2, figsize=(11, 5))
    colour, marker, name = E2_STYLE["fixed"]
    points = []
    for wait in ["0.5", "1", "2", "4"]:
        values = numbers(
            kill_runs(runs, part="sensitivity", kind="fixed", wait=wait), "recovery_s"
        )
        if values:
            points.append((float(wait), values))
    main_values = numbers(kill_runs(runs, part="main", kind="fixed"), "recovery_s")
    if main_values:
        points.append((replay_median, main_values))
    points.sort()

    def draw(axes, xs_values, colour, marker, label):
        """Median dots joined by a line, with min-max bars."""
        if not xs_values:
            return
        xs = [x for x, _ in xs_values]
        middles = [statistics.median(v) for _, v in xs_values]
        lows = [m - min(v) for m, (_, v) in zip(middles, xs_values, strict=True)]
        highs = [max(v) - m for m, (_, v) in zip(middles, xs_values, strict=True)]
        axes.errorbar(
            xs,
            middles,
            yerr=[lows, highs],
            color=colour,
            marker=marker,
            markersize=7,
            markeredgecolor=SURFACE,
            capsize=4,
            linewidth=2,
            label=label,
        )

    draw(left, points, colour, marker, name + " (fake AI)")
    ai_values = numbers(kill_runs(runs, part="ai", kind="fixed"), "recovery_s")
    if ai_values:
        # Drawn at the replayed times' median: those times were measured on
        # the same Ollama, 4 texts at once (Slice 10), as the real-AI runs.
        ai_colour, ai_marker, ai_name = E2_AI_STYLE
        draw(
            left,
            [(replay_median, ai_values)],
            ai_colour,
            ai_marker,
            ai_name + "; x = replayed time",
        )
    # The stale time these runs used, read from the data, not typed in.
    stale_values = numbers(
        [r for r in kill_runs(runs) if r.get("part") in ("main", "sensitivity")],
        "stale_seconds",
    )
    stale = statistics.median(stale_values) if stale_values else None
    if stale is not None:
        left.axhline(stale, color=MUTED, linestyle="--", linewidth=1)
        left.annotate(
            f"stale time ({stale:g} s)",
            xy=(0.02, stale),
            xycoords=("axes fraction", "data"),
            xytext=(0, 4),
            textcoords="offset points",
            color=MUTED,
            fontsize=8,
        )
    broken = kill_runs(runs, kind="broken")
    if broken:
        never = [r for r in broken if r.get("recovery_s", "") == ""]
        highest = [v for _, vs in points for v in vs] + ai_values + [stale or 0]
        top = max(highest) * 1.25
        left.axhspan(top, top * 1.12, color=E2_STYLE["broken"][0], alpha=0.15)
        left.annotate(
            f"Broken: never recovers ({len(never)} of {len(broken)} runs)",
            xy=(0.02, top * 1.06),
            xycoords=("axes fraction", "data"),
            va="center",
            color=TEXT,
            fontsize=9,
        )
        left.set_ylim(0, top * 1.12)
    left.set_xscale("log", base=2)
    left.set_xticks(
        [0.5, 1, 2, 4, replay_median],
        ["0.5", "1", "2", "4", f"{replay_median:.1f}\n(replay)"],
    )
    style(
        left,
        "Recovery time vs wait per text",
        "Seconds the AI takes per text (log scale)",
        "Seconds from kill to first text finished",
    )
    left.legend(frameon=False, labelcolor=TEXT, loc="center right", fontsize=8)

    stale_points = []
    for value in ["10", "30", "60"]:
        values = numbers(
            kill_runs(runs, part="stale", stale_seconds=value), "recovery_s"
        )
        if values:
            stale_points.append((float(value), values))
    draw(right, stale_points, colour, marker, name)
    if stale_points:
        xs = [x for x, _ in stale_points]
        right.plot(
            xs,
            xs,
            color=MUTED,
            linestyle="--",
            linewidth=1,
            label="recovery = stale time",
        )
        right.set_xticks(xs)
        right.legend(frameon=False, labelcolor=TEXT, loc="upper left", fontsize=8)
    right.set_ylim(bottom=0)
    style(
        right,
        "Recovery time vs stale time",
        'Stale time ("stuck after", s)',
        "Seconds from kill to first text finished",
    )
    save(figure, "e2_recovery.png")


def e2_numbers(runs: list[dict], cost: list[dict], code: list[dict]) -> None:
    """Print the E2 key numbers: median (min-max) of the runs, per setting."""
    columns = ["lost", "redone", "takeover_s", "recovery_s", "finish_s", "restart_s"]
    keys = sorted(
        {
            (r["part"], r["kind"], r["wait"], r["stale_seconds"])
            for r in runs
            if r.get("part")
        }
    )
    for part, kind, wait, stale in keys:
        group = [
            r
            for r in runs
            if (r["part"], r["kind"], r["wait"], r["stale_seconds"])
            == (part, kind, wait, stale)
        ]
        good = [r for r in group if not r.get("error")]
        parts = []
        for column in columns:
            values = numbers(good, column)
            parts.append(
                f"{column} {median_and_spread(values) if values else 'never/n.a.'}"
            )
        states = Counter(r["final_state"] for r in good)
        late = sum(int(r["late_looks"] or 0) for r in good)
        print(
            f"E2 {part} {kind} wait {wait} stale {stale}: {len(good)} runs "
            f"({len(group) - len(good)} errors); "
            + "; ".join(parts)
            + f"; final {dict(states)}; late looks {late}"
        )
    if cost:
        for column in [
            "job_s",
            "all_writes",
            "heartbeat_writes",
            "heartbeats_expected",
            "heartbeat_share",
        ]:
            values = numbers(cost, column)
            if not values:
                continue
            if column == "heartbeat_share":
                shown = f"{statistics.median(values):.1%} (median)"
            else:
                shown = median_and_spread(values)
            print(f"E2 cost {column}: {shown}")
    for row in code:
        print(f"E2 code {row['what']}: {row['lines']} lines")


def e2_charts() -> None:
    """Draw the E2 charts and print its numbers, if E2 has been measured."""
    if not (DATA / "e2_runs.csv").exists():
        return
    runs = read("e2_runs.csv")
    replay_median = statistics.median(
        float(r["seconds"]) for r in read("ollama_times_at_4.csv")
    )
    e2_lost_redone_chart(runs)
    e2_recovery_chart(runs, replay_median)
    cost = read("e2_cost.csv") if (DATA / "e2_cost.csv").exists() else []
    code = read("e2_code.csv") if (DATA / "e2_code.csv").exists() else []
    e2_numbers(runs, cost, code)


# ---- E3 ----------------------------------------------------------------------

# The E3 Dockerfiles in the order shown, and which base image each starts from.
E3_IMAGES = ["pip-bad", "pip-good", "pip-slim", "uv-bad", "uv-good"]
E3_MAIN = ["pip-bad", "pip-good", "uv-bad", "uv-good"]
E3_BASE = {
    "pip-bad": "base-full",
    "pip-good": "base-full",
    "pip-slim": "base-slim",
    "uv-bad": "base-slim",
    "uv-good": "base-slim",
}
E3_STEPS = {
    "cold": "Cold build (cache cleared)",
    "code": "After a one-line code change",
    "package": "After adding one package",
}
# The two environments, in the order shown, with their file suffix (D107).
E3_ENVIRONMENTS = [("Mac", ""), ("GitHub Actions", "_ci")]
MB = 1_000_000  # Docker counts sizes in decimal megabytes


def e3_sizes(images: list[dict]) -> dict[str, float]:
    """{image: uncompressed size in MB} for the images that were measured."""
    return {
        r["image"]: float(r["image_bytes"]) / MB for r in images if r["image_bytes"]
    }


def e3_size_chart(environments: list[tuple[str, list[dict]]]) -> None:
    """Image size per Dockerfile, split into "base image" and "added on top".

    One panel per environment. The darker part of each bar is the base image
    the Dockerfile starts from; the lighter part is what the Dockerfile adds.
    """
    figure, panels = plt.subplots(
        1, len(environments), figsize=(6 * len(environments), 4.5), squeeze=False
    )
    for axes, (name, images) in zip(panels[0], environments, strict=True):
        sizes = e3_sizes(images)
        shown = [i for i in E3_IMAGES if i in sizes]
        places = range(len(shown))
        base = [min(sizes.get(E3_BASE[i], 0.0), sizes[i]) for i in shown]
        added = [sizes[i] - b for i, b in zip(shown, base, strict=True)]
        axes.barh(places, base, color=SERIES, label="base image")
        axes.barh(places, added, left=base, color=SERIES_2, label="added on top")
        for place, image in zip(places, shown, strict=True):
            axes.annotate(
                f"{sizes[image]:,.0f} MB",
                xy=(sizes[image], place),
                xytext=(4, 0),
                textcoords="offset points",
                va="center",
                color=TEXT,
                fontsize=9,
            )
        axes.set_yticks(list(places), shown)
        axes.invert_yaxis()  # the simple version on top
        axes.set_xlim(0, max(sizes.values(), default=1) * 1.25)
        axes.legend(frameon=False, loc="lower right")
        style(axes, name, "Size uncompressed (MB, sum of the layers)", "")
    figure.suptitle("Image size per Dockerfile", x=0.01, ha="left", color=TEXT)
    save(figure, "e3_image_size.png")


def e3_times(builds: list[dict]) -> dict[tuple[str, str], list[float]]:
    """{(Dockerfile, step): [seconds of each build that worked]}."""
    times: dict[tuple[str, str], list[float]] = {}
    for row in builds:
        if row["ok"] == "yes":
            times.setdefault((row["dockerfile"], row["step"]), []).append(
                float(row["seconds"])
            )
    return times


def e3_time_chart(environments: list[tuple[str, list[dict]]]) -> None:
    """Build times: one panel per step, one row per environment.

    Each build is a small dot; the bar is the median of the rounds.
    """
    figure, panels = plt.subplots(
        len(environments),
        len(E3_STEPS),
        figsize=(13, 4 * len(environments)),
        squeeze=False,
    )
    for row_panels, (name, builds) in zip(panels, environments, strict=True):
        times = e3_times(builds)
        for axes, (step, title) in zip(row_panels, E3_STEPS.items(), strict=True):
            for place, image in enumerate(E3_MAIN):
                values = times.get((image, step), [])
                if not values:
                    continue
                middle = statistics.median(values)
                axes.bar(place, middle, color=SERIES, width=0.6)
                axes.scatter([place] * len(values), values, color=TEXT, s=10, zorder=3)
                # The median written just right of the bar's top, so a slow
                # round's dot far above doesn't pull the label away from it.
                axes.annotate(
                    f"{middle:.1f} s",
                    xy=(place + 0.31, middle),
                    va="center",
                    color=TEXT,
                    fontsize=9,
                )
            axes.set_xticks(range(len(E3_MAIN)), E3_MAIN)
            # Room right of the last bar for its label.
            axes.set_xlim(-0.5, len(E3_MAIN) - 0.1)
            style(axes, f"{name}: {title}", "", "Seconds")
    figure.suptitle(
        "Build time (bar: median of the rounds; dots: each build)",
        x=0.01,
        ha="left",
        color=TEXT,
    )
    save(figure, "e3_build_time.png")


def e3_numbers(name: str, images: list[dict], builds: list[dict]) -> None:
    """Print the E3 key numbers for one environment.

    Sizes, how many times smaller, and how the size gap splits (D105):
    pip-good → pip-slim is the base image alone (only the FROM line differs);
    pip-slim → uv-good is uv plus two stages.
    """
    sizes = e3_sizes(images)
    for row in images:
        size = sizes.get(row["image"])
        packed = row.get("compressed_bytes") or ""
        print(
            f"E3 {name} image {row['image']}: "
            + (f"{size:,.0f} MB" if size is not None else "not measured")
            + (f", {float(packed) / MB:,.0f} MB compressed" if packed else "")
            + f"; starts {row.get('starts') or '-'}; curl {row.get('has_curl') or '-'}"
            + (f"; {row['lines']} lines" if row.get("lines") else "")
        )
    needed = {"pip-bad", "pip-good", "pip-slim", "uv-good"}
    if needed <= sizes.keys():
        print(
            f"E3 {name}: pip-bad is {sizes['pip-bad'] / sizes['uv-good']:.1f}x "
            f"the size of uv-good"
        )
        gap = sizes["pip-good"] - sizes["uv-good"]
        base_part = sizes["pip-good"] - sizes["pip-slim"]
        rest = sizes["pip-slim"] - sizes["uv-good"]
        if gap > 0:
            print(
                f"E3 {name}: of the {gap:,.0f} MB gap pip-good -> uv-good, "
                f"{base_part:,.0f} MB ({base_part / gap:.0%}) is the base image "
                f"alone and {rest:,.0f} MB ({rest / gap:.0%}) is uv + two stages"
            )
    times = e3_times(builds)
    for image in E3_MAIN:
        # "-" for a step with no build that worked.
        parts = [
            f"{step} {median_and_spread(times[image, step])} s"
            if (image, step) in times
            else f"{step} -"
            for step in E3_STEPS
        ]
        failed = sum(1 for r in builds if r["dockerfile"] == image and r["ok"] != "yes")
        print(f"E3 {name} {image}: " + "; ".join(parts) + f"; failed {failed}")


def e3_charts() -> None:
    """Draw the E3 charts and print its numbers, for each measured environment."""
    measured = []
    for name, suffix in E3_ENVIRONMENTS:
        images_file = DATA / f"e3_images{suffix}.csv"
        builds_file = DATA / f"e3_builds{suffix}.csv"
        if images_file.exists() and builds_file.exists():
            measured.append((name, read(images_file.name), read(builds_file.name)))
    if not measured:
        return
    e3_size_chart([(name, images) for name, images, _ in measured])
    e3_time_chart([(name, builds) for name, _, builds in measured])
    for name, images, builds in measured:
        e3_numbers(name, images, builds)


# ---- E4 ----------------------------------------------------------------------

E4_STYLE = {"on": (SERIES, "o", "cache on"), "off": (SERIES_2, "s", "cache off")}

# The parts of a CI job, and which steps (by the start of their name) belong
# to each. Steps not listed here are "the rest" (set up, checkout, clean-up).
E4_PARTS = {
    "Postgres start-up": ["Initialize containers"],
    "uv setup + cache": ["Run astral-sh/setup-uv", "Post Run astral-sh/setup-uv"],
    "Install (uv sync)": ["Install dependencies"],
    "Tests (ruff, fast, Postgres)": ["Ruff", "Fast tests", "Postgres tests"],
}
E4_REST = "The rest (set up, checkout, clean-up)"
# One colour per part of the job, in the order of E4_PARTS, then "the rest".
E4_PART_COLOURS = ["#52514e", "#eb6834", "#eda100", "#2a78d6", "#a8a6a0"]


def e4_part_of(step: str) -> str:
    """Which part of the job a step belongs to."""
    for part, starts in E4_PARTS.items():
        if any(step.startswith(start) for start in starts):
            return part
    return E4_REST


def e4_good_runs(runs: list[dict], cache: str) -> list[dict]:
    """The recorded runs of one kind that worked and have a job time."""
    return [
        r
        for r in runs
        if r["cache"] == cache and not r.get("error") and r.get("job_s", "") != ""
    ]


def e4_parts(steps: list[dict], run_ids: set[str]) -> dict[str, list[float]]:
    """{part: [seconds in each run]}, from the steps' log times."""
    per_run: dict[str, dict[str, float]] = {}
    for step in steps:
        if step["run_id"] in run_ids and step["log_s"] != "":
            part = e4_part_of(step["step"])
            totals = per_run.setdefault(step["run_id"], {})
            totals[part] = totals.get(part, 0.0) + float(step["log_s"])
    parts: dict[str, list[float]] = {}
    for totals in per_run.values():
        for part, seconds in totals.items():
            parts.setdefault(part, []).append(seconds)
    return parts


def e4_chart(runs: list[dict], steps: list[dict]) -> None:
    """Job time cache on vs off, and where the time goes (median per part)."""
    figure, (left, right) = plt.subplots(
        1, 2, figsize=(12, 4.8), gridspec_kw={"width_ratios": [1, 1.6]}
    )
    for place, cache in enumerate(["on", "off"]):
        colour, marker, name = E4_STYLE[cache]
        values = [float(r["job_s"]) for r in e4_good_runs(runs, cache)]
        if not values:
            continue
        offsets = [(i - (len(values) - 1) / 2) * 0.03 for i in range(len(values))]
        left.scatter(
            [place + o for o in offsets],
            values,
            color=colour,
            marker=marker,
            s=40,
            edgecolor=SURFACE,
            zorder=3,
        )
        middle = statistics.median(values)
        left.plot([place - 0.25, place + 0.25], [middle, middle], color=TEXT)
        left.annotate(
            f"{middle:.0f} s",
            xy=(place + 0.27, middle),
            va="center",
            color=TEXT,
            fontsize=9,
        )
    left.set_xticks([0, 1], ["cache on", "cache off"])
    left.set_xlim(-0.6, 1.6)
    left.set_ylim(bottom=0)
    style(left, "Job time (each dot is one run)", "", "Seconds")

    names = [*E4_PARTS, E4_REST]
    for place, cache in enumerate(["on", "off"]):
        ids = {r["run_id"] for r in e4_good_runs(runs, cache)}
        parts = e4_parts(steps, ids)
        start = 0.0
        for index, part in enumerate(names):
            values = parts.get(part, [])
            width = statistics.median(values) if values else 0.0
            right.barh(
                place,
                width,
                left=start,
                color=E4_PART_COLOURS[index],
                label=part if place == 0 else None,
                height=0.55,
            )
            if width >= 3:
                right.annotate(
                    f"{width:.1f}",
                    xy=(start + width / 2, place),
                    ha="center",
                    va="center",
                    color=SURFACE,
                    fontsize=8,
                )
            start += width
    right.set_yticks([0, 1], ["cache on", "cache off"])
    right.invert_yaxis()
    right.legend(frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.18), ncol=2)
    # Each part's own median is stacked, so the bar's length is close to,
    # but not exactly, the median job time on the left.
    style(
        right,
        "Where the time goes (median of each part)",
        "Seconds (each part's median; together close to, not equal to, the job's)",
        "",
    )
    figure.suptitle(
        "CI with uv's download cache on and off", x=0.01, ha="left", color=TEXT
    )
    save(figure, "e4_cache.png")


def e4_spread(values: list[float]) -> str:
    """ "median (min-max)" with 2 decimals, for E4's sub-second steps."""
    return f"{statistics.median(values):.2f} ({min(values):.2f}-{max(values):.2f})"


def e4_numbers(runs: list[dict], steps: list[dict], secrets: list[dict]) -> None:
    """Print the E4 key numbers: the job and its parts, on and off; the secrets."""
    for cache in ["on", "off"]:
        good = e4_good_runs(runs, cache)
        failed = sum(1 for r in runs if r["cache"] == cache) - len(good)
        print(
            f"E4 cache {cache}: {len(good)} runs ({failed} not counted); job "
            f"{median_and_spread([float(r['job_s']) for r in good]) if good else '-'} s"
            f"; waiting for a machine "
            f"{median_and_spread(numbers(good, 'queue_s')) if good else '-'} s"
        )
        parts = e4_parts(steps, {r["run_id"] for r in good})
        for part in [*E4_PARTS, E4_REST]:
            if parts.get(part):
                print(f"E4 cache {cache}: {part} {e4_spread(parts[part])} s")
        for column in ["prepared_s", "installed_s"]:
            values = numbers(good, column)
            if values:
                print(f"E4 cache {cache}: uv {column} {e4_spread(values)} s")
        results = Counter(r.get("cache_result", "") for r in good)
        print(f"E4 cache {cache}: cache result {dict(results)}")
    kinds = Counter((r["workflow"], r["event"], r["secret_present"]) for r in secrets)
    for (workflow, event, present), count in sorted(kinds.items()):
        print(f"E4 secret: {workflow} {event}: {present} ({count} runs)")


def e4_charts() -> None:
    """Draw the E4 chart and print its numbers, if E4 has been measured."""
    if not (DATA / "e4_runs.csv").exists():
        return
    runs = read("e4_runs.csv")
    steps = read("e4_steps.csv") if (DATA / "e4_steps.csv").exists() else []
    secrets = read("e4_secrets.csv") if (DATA / "e4_secrets.csv").exists() else []
    e4_chart(runs, steps)
    e4_numbers(runs, steps, secrets)


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
    e2_charts()
    e3_charts()
    e4_charts()
    print(f"Charts written to {CHARTS}.")


if __name__ == "__main__":
    main()
