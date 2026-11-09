"""Experiment E3's build script: image size and rebuild time (spec 9.3).

Design approved by Adar on 9 Nov 2026 (D105-D107). It builds the E3
Dockerfiles in `docker/` (recipes Docker follows to make an *image*, the
sealed box holding Python, the libraries and Kuyruk's code) and measures:

1. **Images** (once per Dockerfile): the size uncompressed (its layers), the
   size compressed (the layers gzipped, roughly what a download costs),
   whether the image actually starts and answers /health, whether `curl` is
   inside, and the Dockerfile's lines without comments or blank lines. Also
   the two base images on their own (full Python and slim), so the size gap
   can be split into "base image alone" and "uv + two stages" (D105).
   `pip-slim` is measured here only (size only).
2. **Builds** (5 rounds; each round has every main Dockerfile once, in a
   rotated order, i.e. interleaved). For each Dockerfile:
   - clear Docker's build cache, then a **cold** build (base images stay
     downloaded; the libraries are downloaded during the build);
   - change one line of Kuyruk's code, then rebuild (**code**);
   - add one small pinned package to the library list, then rebuild
     (**package**).
   A time is the wall-clock time of the `docker build` command, on this
   machine's own clock (D106).

Everything is built from a temporary copy of the project (only the files git
tracks or would track, so `.env` and other secrets are never copied), which
also gets `requirements.txt`: the exact library versions from `uv.lock`, made
by `uv export`, for the pip Dockerfiles. The real project is never changed.

Results: `bench/data/e3_builds.csv` (one row per build) and
`bench/data/e3_images.csv` (one row per image); in GitHub Actions (D107) the
same with `_ci` added. Charts: `uv run --group bench python -m bench.charts`.

Run from the project folder, plugged in, Docker Desktop open, Mac left alone
(about an hour; `caffeinate` keeps the Mac awake):

    caffeinate -dims uv run python -m bench.e3_build

Note: clearing the build cache (`docker builder prune -af`) clears it for
every project on this machine, not only Kuyruk; the next build of anything
else is then slower once. Images and containers are not touched.

`--trial OUT_DIR` is a quick check (1 round) written to OUT_DIR instead of
`bench/data/`.
"""

import argparse
import gzip
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path

from bench.csv_out import CsvOut

ROOT = Path(__file__).parent.parent
DATA = Path(__file__).parent / "data"
DOCKERFILES = ROOT / "docker"

# ---- The experiment's design (D105-D107, approved by Adar) -------------------

# The 2x2: pip with the full base vs uv with the slim base and two stages, each
# with bad and good layer order.
MAIN = ["pip-bad", "pip-good", "uv-bad", "uv-good"]
# Identical to pip-good except the base image: measured for size only.
SIZE_ONLY = ["pip-slim"]
# Which Dockerfile's base image is "full" and which is "slim".
BASES = {"base-full": "pip-good", "base-slim": "pip-slim"}
ROUNDS = 5
STEPS = ["cold", "code", "package"]

CHANGED_FILE = "app/main.py"  # the file whose first line the "code" step edits
EXTRA_PACKAGE = "six==1.17.0"  # small, pure Python, not used by Kuyruk
TAG = "kuyruk-e3:"  # image names: kuyruk-e3:pip-bad and so on

# The files that differ between the plain project and the "package" step.
LIBRARY_FILES = ["pyproject.toml", "uv.lock", "requirements.txt"]

# Starting an image to check it really runs: demo mode with its public demo
# key (the same as compose.yaml), and a throwaway database inside the
# container's own /tmp (the code folder is read-only for Kuyruk's user).
RUN_CHECK_ENV = {
    "DEMO_MODE": "true",
    "JWT_SECRET": "demo-only-jwt-secret-public-not-for-real-use",
    "DATABASE_URL": "sqlite:////tmp/kuyruk.db",
}
START_LIMIT = 60.0  # seconds to wait for /health before saying "no"
START_EVERY = 0.5  # seconds between two tries
BUILD_LIMIT = 1800.0  # give up on one build after 30 minutes

BUILD_COLUMNS = [
    "environment",
    "round",
    "position",
    "dockerfile",
    "step",
    "seconds",
    "ok",
    "image_bytes",
    "error",
]
IMAGE_COLUMNS = [
    "environment",
    "image",
    "image_bytes",
    "compressed_bytes",
    "starts",
    "has_curl",
    "lines",
]

# A function that runs one command and returns its result; tests pass a fake.
Runner = Callable[..., subprocess.CompletedProcess]


def run(command: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run one command, keep its output as text, and never raise on failure."""
    return subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=BUILD_LIMIT,
        check=False,
    )


# ---- Plain helpers (no Docker) -----------------------------------------------


def round_plan(names: list[str], rounds: int) -> list[tuple[int, int, str]]:
    """(round, position, Dockerfile) for every build round, interleaved.

    Each round has every Dockerfile once; round 2 starts one further along
    than round 1, and so on, so no Dockerfile is always first or last.
    """
    plan = []
    for r in range(rounds):
        shift = r % len(names)
        order = names[shift:] + names[:shift]
        plan += [(r + 1, p + 1, name) for p, name in enumerate(order)]
    return plan


def base_images(dockerfile_text: str) -> list[str]:
    """The images a Dockerfile starts from: the word after each FROM."""
    images = []
    for line in dockerfile_text.splitlines():
        words = line.split()
        if len(words) >= 2 and words[0].upper() == "FROM":
            images.append(words[1])
    return images


def by_digest(image: str) -> str:
    """An image name with only its fingerprint, e.g. `python@sha256:...`.

    The Dockerfiles name base images as name:version@fingerprint. Docker
    stores them under the fingerprint, and `docker history` finds them only
    by name@fingerprint, without the version.
    """
    if "@" not in image:
        return image
    name, digest = image.split("@", 1)
    # Drop a ":version" after the last "/", not a registry's ":port".
    folder, _, last = name.rpartition("/")
    last = last.split(":", 1)[0]
    return f"{folder}/{last}@{digest}" if folder else f"{last}@{digest}"


def count_lines(dockerfile_text: str) -> int:
    """A Dockerfile's lines without comments or blank lines (the fix's cost)."""
    return sum(
        1
        for line in dockerfile_text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    )


def change_one_line(path: Path, marker: str) -> None:
    """Change the first line of a file, without changing what the code does.

    The first line of `app/main.py` is the start of its description
    (docstring), so adding a few words to it is a real one-line code change
    for Docker (the file's contents differ) but not for Kuyruk.
    """
    lines = path.read_text().splitlines(keepends=True)
    lines[0] = lines[0].rstrip("\n") + f" (E3 change {marker})\n"
    path.write_text("".join(lines))


def dockerfile(name: str) -> Path:
    """The path of one E3 Dockerfile."""
    return DOCKERFILES / f"{name}.Dockerfile"


def median_and_spread(values: list[float]) -> str:
    """ "median (min-max)" of some numbers, for the printed summary."""
    if not values:
        return "-"
    return f"{statistics.median(values):.1f} s ({min(values):.1f}-{max(values):.1f})"


def last_line(text: str) -> str:
    """The last non-empty line of some output, short, for the error column."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1][:200] if lines else ""


# ---- The temporary copy of the project ---------------------------------------


class Context:
    """The temporary copy of the project that Docker builds from.

    It is prepared once: the project's files are copied, `requirements.txt`
    is made from `uv.lock`, and the library files are saved twice, as they
    are and with the extra package added. Before every build it is put back
    to the right state, so each build sees exactly the files it should.
    """

    def __init__(self, folder: Path, runner: Runner = run) -> None:
        """Remember where the copy lives; `prepare` fills it."""
        self.folder = folder
        self.runner = runner
        self.plain: dict[str, bytes] = {}
        self.with_package: dict[str, bytes] = {}

    def prepare(self, repo: Path = ROOT) -> None:
        """Copy the project, make requirements.txt, save both library states."""
        copy_project(repo, self.folder, self.runner)
        export_requirements(self.folder, self.runner)
        self.plain = self.snapshot()
        check(
            self.runner(
                ["uv", "add", "--no-sync", "--quiet", EXTRA_PACKAGE], cwd=self.folder
            ),
            "uv add",
        )
        export_requirements(self.folder, self.runner)
        self.with_package = self.snapshot()
        self.reset()

    def snapshot(self) -> dict[str, bytes]:
        """The current contents of the library files and the changed file."""
        return {
            name: (self.folder / name).read_bytes()
            for name in [*LIBRARY_FILES, CHANGED_FILE]
        }

    def reset(self) -> None:
        """Back to the plain project: no code change, no extra package."""
        self.restore(self.plain)

    def add_package(self) -> None:
        """Swap in the library files with the extra package added."""
        self.restore({name: self.with_package[name] for name in LIBRARY_FILES})

    def restore(self, files: dict[str, bytes]) -> None:
        """Write some saved files back into the copy."""
        for name, content in files.items():
            (self.folder / name).write_bytes(content)


def check(result: subprocess.CompletedProcess, what: str) -> None:
    """Stop the script with a clear message if a preparation step failed."""
    if result.returncode != 0:
        sys.exit(f"{what} failed: {last_line(result.stderr or result.stdout)}")


def copy_project(repo: Path, folder: Path, runner: Runner = run) -> None:
    """Copy the files git tracks or would track (never ignored ones like .env)."""
    result = runner(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=repo,
    )
    check(result, "git ls-files")
    for name in filter(None, result.stdout.split("\0")):
        source = repo / name
        # A file deleted but not yet committed is still listed; skip it.
        if source.is_file():
            target = folder / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def export_requirements(folder: Path, runner: Runner = run) -> None:
    """Write requirements.txt: the exact versions in uv.lock, for pip (D105).

    `--no-hashes`: only names and versions, the way a beginner's list looks;
    `--no-emit-project`: Kuyruk itself is not a library to install.
    """
    check(
        runner(
            [
                "uv",
                "export",
                "--locked",
                "--no-dev",
                "--no-hashes",
                "--no-emit-project",
                "--no-header",
                "--quiet",
                "--output-file",
                "requirements.txt",
            ],
            cwd=folder,
        ),
        "uv export",
    )


# ---- Docker ------------------------------------------------------------------


def pull_bases(runner: Runner = run) -> None:
    """Download every base image once, before any build (D106)."""
    images = {
        image
        for name in MAIN + SIZE_ONLY
        for image in base_images(dockerfile(name).read_text())
    }
    for image in sorted(images):
        print(f"Downloading base image {image.split('@')[0]} ...", flush=True)
        check(runner(["docker", "pull", "--quiet", image]), f"docker pull {image}")


def clear_cache(name: str, runner: Runner = run) -> None:
    """Remove the previous image and clear Docker's build cache (D106)."""
    runner(["docker", "image", "rm", "--force", TAG + name])
    check(runner(["docker", "builder", "prune", "--all", "--force"]), "prune")


def build(
    name: str,
    context: Context,
    runner: Runner = run,
    clock: Callable[[], float] = time.perf_counter,
) -> tuple[float, bool, str]:
    """Build one Dockerfile; return (seconds, it worked, error message)."""
    command = ["docker", "build", "--progress=plain"]
    command += ["--file", str(dockerfile(name)), "--tag", TAG + name]
    command += [str(context.folder)]
    started = clock()
    try:
        result = runner(command)
    except subprocess.TimeoutExpired:
        return clock() - started, False, f"took longer than {BUILD_LIMIT:.0f} s"
    seconds = clock() - started
    if result.returncode != 0:
        return seconds, False, last_line(result.stderr or result.stdout)
    return seconds, True, ""


def image_bytes(image: str, runner: Runner = run) -> str:
    """An image's uncompressed size: the sum of its layers, in bytes (D106).

    Each *layer* is one saved step of the image (the base image's layers
    included); `docker history` lists every layer's unpacked size. Docker's
    own image size can't be used: Docker Desktop's (with its "containerd"
    image store) also adds the compressed copy of each layer it keeps, so it
    counts every layer twice, while a standard Docker install doesn't. The
    layer sum means the same on every machine. Blank if unknown.
    """
    result = runner(
        [
            "docker",
            "history",
            "--no-trunc",
            "--human=false",
            "--format",
            "{{.Size}}",
            image,
        ]
    )
    sizes = result.stdout.split()
    if result.returncode != 0 or not sizes or not all(s.isdigit() for s in sizes):
        return ""
    return str(sum(int(s) for s in sizes))


def compressed_bytes(image: str, runner: Runner = run) -> str:
    """An image's size compressed: its layers saved and gzipped, in bytes.

    `docker save` writes the image as one stream; gzip (level 6, the usual
    default) is roughly what a registry stores and a download costs. Only the
    image's own platform is saved, since a base image may list others.
    """
    result = runner(
        ["docker", "image", "inspect", "--format", "{{.Os}}/{{.Architecture}}", image]
    )
    own_platform = result.stdout.strip() if result.returncode == 0 else ""
    for command in (
        ["docker", "save", "--platform", own_platform, image],
        ["docker", "save", image],  # older Docker without --platform
    ):
        size = gzipped_size(command)
        if size:
            return str(size)
    return ""


def gzipped_size(command: list[str]) -> int:
    """How many bytes a command's output takes once gzipped; 0 if it failed."""

    class Counter:
        """A pretend file that only counts what is written to it."""

        def __init__(self) -> None:
            self.size = 0

        def write(self, data: bytes) -> int:
            self.size += len(data)
            return len(data)

        def flush(self) -> None:
            """Nothing to flush: nothing is stored."""

    counter = Counter()
    with subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
    ) as process:
        with gzip.GzipFile(fileobj=counter, mode="wb", compresslevel=6) as packed:
            while chunk := process.stdout.read(1 << 20):
                packed.write(chunk)
    return counter.size if process.returncode == 0 else 0


def has_curl(image: str, runner: Runner = run) -> str:
    """Whether the `curl` program is inside an image: "yes" or "no"."""
    result = runner(
        ["docker", "run", "--rm", "--entrypoint", "sh", image, "-c", "command -v curl"]
    )
    return "yes" if result.returncode == 0 else "no"


def answers_health(url: str) -> bool:
    """Whether /health answers "ok" at this address right now."""
    try:
        with urllib.request.urlopen(url, timeout=2) as reply:
            return reply.status == 200
    except OSError:
        return False


def starts(
    image: str,
    runner: Runner = run,
    healthy: Callable[[str], bool] = answers_health,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> str:
    """Whether an image starts Kuyruk's API and answers /health: "yes" or "no".

    A Dockerfile that forgot to copy something still builds; this is where it
    would show (the risk in Adar's prediction).
    """
    command = ["docker", "run", "--detach", "--publish", "127.0.0.1::8000"]
    for key, value in RUN_CHECK_ENV.items():
        command += ["--env", f"{key}={value}"]
    started = runner([*command, image])
    if started.returncode != 0:
        return "no"
    container = started.stdout.strip()
    try:
        # Docker picked a free port on this machine; ask which.
        port = runner(["docker", "port", container, "8000/tcp"]).stdout
        address = port.strip().splitlines()[0] if port.strip() else ""
        url = f"http://{address}/health"
        deadline = clock() + START_LIMIT
        while clock() < deadline:
            if address and healthy(url):
                return "yes"
            sleep(START_EVERY)
        return "no"
    finally:
        runner(["docker", "rm", "--force", container])


# ---- The two parts -----------------------------------------------------------


def measure_images(
    context: Context,
    environment: str,
    write: Callable[[dict], None],
    runner: Runner = run,
    compressed: Callable[[str], str] | None = None,
    started: Callable[[str], str] | None = None,
) -> None:
    """Part 1: build each Dockerfile once and measure the image (D106)."""
    compressed = compressed or (lambda image: compressed_bytes(image, runner))
    started = started or (lambda image: starts(image, runner))
    for name in MAIN + SIZE_ONLY:
        context.reset()
        print(f"Image {name}: building ...", flush=True)
        seconds, ok, error = build(name, context, runner)
        image = TAG + name
        row = {"environment": environment, "image": name}
        row["lines"] = count_lines(dockerfile(name).read_text())
        if ok:
            row["image_bytes"] = image_bytes(image, runner)
            row["compressed_bytes"] = compressed(image)
            row["starts"] = started(image)
            row["has_curl"] = has_curl(image, runner)
        else:
            print(f"  build failed: {error}", flush=True)
            row["starts"] = "no"
        write(row)
    for base, name in BASES.items():
        # The base image is the last FROM line of that Dockerfile.
        image = by_digest(base_images(dockerfile(name).read_text())[-1])
        print(f"Image {base}: measuring ...", flush=True)
        write(
            {
                "environment": environment,
                "image": base,
                "image_bytes": image_bytes(image, runner),
                "compressed_bytes": compressed(image),
                "has_curl": has_curl(image, runner),
            }
        )


def measure_round(
    name: str,
    round_number: int,
    position: int,
    context: Context,
    environment: str,
    runner: Runner = run,
    clock: Callable[[], float] = time.perf_counter,
) -> list[dict]:
    """Part 2, one Dockerfile in one round: cold, code, package (D106).

    If a build fails, the later steps of that Dockerfile in this round are
    recorded as not run, so a failure is visible instead of hidden.
    """
    context.reset()
    clear_cache(name, runner)
    rows = []
    failed = ""
    for step in STEPS:
        row = {
            "environment": environment,
            "round": round_number,
            "position": position,
            "dockerfile": name,
            "step": step,
        }
        if failed:
            row.update(ok="no", error=f"not run: the {failed} build failed")
            rows.append(row)
            continue
        if step == "code":
            change_one_line(context.folder / CHANGED_FILE, f"round {round_number}")
        elif step == "package":
            context.add_package()
        seconds, ok, error = build(name, context, runner, clock)
        row.update(seconds=f"{seconds:.2f}", ok="yes" if ok else "no", error=error)
        if ok:
            row["image_bytes"] = image_bytes(TAG + name, runner)
        else:
            failed = step
        rows.append(row)
    return rows


# ---- Output and the command line ---------------------------------------------


def setup_lines(runner: Runner = run) -> list[str]:
    """The machine and Docker details, printed for `setup.md`."""
    version = runner(["docker", "version", "--format", "{{.Server.Version}}"])
    info = runner(
        [
            "docker",
            "info",
            "--format",
            "{{.OperatingSystem}} · {{.Architecture}} · {{.NCPU}} CPUs · "
            "{{.MemTotal}} bytes memory · storage {{.Driver}}",
        ]
    )
    return [
        f"Machine: {platform.platform()} ({platform.machine()})",
        f"Docker Engine: {version.stdout.strip()}",
        f"Docker: {info.stdout.strip()}",
    ]


def parse_arguments(arguments: list[str] | None = None) -> argparse.Namespace:
    """Read the command line: where results go, and how many rounds."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--environment",
        default="mac",
        help='where this runs: "mac" (default) or "ci" (adds _ci to the files)',
    )
    parser.add_argument(
        "--trial",
        type=Path,
        metavar="OUT_DIR",
        help="a quick check: 1 round, results written to OUT_DIR",
    )
    parser.add_argument("--rounds", type=int, default=ROUNDS, help=argparse.SUPPRESS)
    options = parser.parse_args(arguments)
    if options.trial:
        options.rounds = 1
    return options


def output_paths(options: argparse.Namespace) -> tuple[Path, Path]:
    """The builds and images CSV files for these options."""
    folder = options.trial or DATA
    suffix = "" if options.environment == "mac" else f"_{options.environment}"
    return folder / f"e3_builds{suffix}.csv", folder / f"e3_images{suffix}.csv"


def main(arguments: list[str] | None = None) -> None:
    """Prepare the copy, measure the images, then run the build rounds."""
    options = parse_arguments(arguments)
    builds_path, images_path = output_paths(options)
    for line in setup_lines():
        print(line)
    with tempfile.TemporaryDirectory(prefix="kuyruk-e3-") as folder:
        context = Context(Path(folder))
        context.prepare()
        pull_bases()
        images = CsvOut(images_path, IMAGE_COLUMNS)
        measure_images(context, options.environment, images.write)
        images.close()
        builds = CsvOut(builds_path, BUILD_COLUMNS)
        times: dict[tuple[str, str], list[float]] = {}
        for round_number, position, name in round_plan(MAIN, options.rounds):
            print(f"Round {round_number}, {name}:", flush=True)
            for row in measure_round(
                name, round_number, position, context, options.environment
            ):
                builds.write(row)
                print(f"  {row['step']}: {row.get('seconds', '-')} s, ok {row['ok']}")
                if row["ok"] == "yes":
                    times.setdefault((name, row["step"]), []).append(
                        float(row["seconds"])
                    )
        builds.close()
    for name in MAIN:
        shown = [f"{s} {median_and_spread(times.get((name, s), []))}" for s in STEPS]
        print(f"{name}: " + " · ".join(shown))
    print(f"Results written to {builds_path} and {images_path}.")


if __name__ == "__main__":
    main()
