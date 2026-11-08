"""Draw the Slice 10 charts from the measured CSV files, and print key numbers.

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

matplotlib (the charting library) is in the separate `bench` dependency group,
so Kuyruk itself, its tests and its Docker image don't need it. Run:

    uv run --group bench python -m bench.charts
"""

import csv
import statistics
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


def main() -> None:
    """Draw all three charts and print the key numbers."""
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
    print(f"Charts written to {CHARTS}.")


if __name__ == "__main__":
    main()
