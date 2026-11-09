"""Tests for experiment E3's build script (`bench/e3_build.py`) and Dockerfiles.

No Docker is needed: every command the script would run goes to a pretend
runner (`FakeRunner`) that records it and answers like Docker would. The
Dockerfile tests read the real files in `docker/` and check that each one
differs from its partner only in what E3 measures (D105).
"""

import subprocess
from pathlib import Path

import pytest

from bench import e3_build
from bench.e3_build import (
    BUILD_COLUMNS,
    IMAGE_COLUMNS,
    MAIN,
    SIZE_ONLY,
    Context,
    base_images,
    build,
    change_one_line,
    count_lines,
    dockerfile,
    measure_images,
    measure_round,
    round_plan,
    starts,
)


def done(stdout="", returncode=0, stderr=""):
    """A finished command's result, as subprocess.run would return it."""
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class FakeRunner:
    """Pretend commands: records each one and gives a canned answer.

    `fail` lists command prefixes (joined with spaces) that should fail;
    `answers` maps a prefix to the output it prints.
    """

    def __init__(self, fail=(), answers=None):
        self.commands = []
        self.fail = list(fail)
        self.answers = answers or {}

    def __call__(self, command, cwd=None):
        self.commands.append((command, cwd))
        text = " ".join(command)
        if any(text.startswith(prefix) for prefix in self.fail):
            return done(returncode=1, stderr="step 3 went wrong\nERROR: no space")
        for prefix, output in self.answers.items():
            if text.startswith(prefix):
                return done(output)
        return done()

    def ran(self, prefix):
        """How many recorded commands start with `prefix`."""
        return sum(1 for c, _ in self.commands if " ".join(c).startswith(prefix))


def ticking_clock(step=10.0):
    """A pretend clock that moves `step` seconds every time it is read."""
    now = [0.0]

    def clock():
        now[0] += step
        return now[0]

    return clock


# ---- The Dockerfiles ---------------------------------------------------------


def instructions(name):
    """A Dockerfile's lines without comments or blank lines."""
    return [
        line
        for line in dockerfile(name).read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def test_every_dockerfile_exists():
    for name in MAIN + SIZE_ONLY:
        assert dockerfile(name).is_file()


def test_pip_slim_differs_from_pip_good_only_in_the_from_line():
    # D105: the size difference must be the base image's effect alone, so
    # every line, comments included, must match except FROM.
    good = dockerfile("pip-good").read_text().splitlines()
    slim = dockerfile("pip-slim").read_text().splitlines()
    assert len(good) == len(slim)
    different = [(g, s) for g, s in zip(good, slim, strict=True) if g != s]
    assert len(different) == 1
    good_line, slim_line = different[0]
    assert good_line.startswith("FROM python:3.12.16-trixie@sha256:")
    assert slim_line.startswith("FROM python:3.12.16-slim-trixie@sha256:")


def index_of(lines, start):
    """The position of the first line starting with `start`."""
    return next(i for i, line in enumerate(lines) if line.startswith(start))


@pytest.mark.parametrize("name", ["pip-bad", "uv-bad"])
def test_bad_order_copies_the_code_before_installing(name):
    lines = instructions(name)
    install = next(
        i for i, line in enumerate(lines) if "install" in line or "sync" in line
    )
    assert index_of(lines, "COPY . .") < install


def test_pip_good_installs_from_the_list_before_copying_the_code():
    lines = instructions("pip-good")
    listed = index_of(lines, "COPY requirements.txt")
    install = index_of(lines, "RUN pip install")
    assert listed < install < index_of(lines, "COPY . .")


def test_uv_good_installs_from_the_lock_before_copying_the_code():
    lines = instructions("uv-good")
    assert "COPY . ." not in lines
    assert index_of(lines, "COPY pyproject.toml uv.lock") < index_of(lines, "COPY app")


def test_bad_and_good_partners_differ_only_in_layer_order():
    # Only the order changes: the same instructions, rearranged.
    for bad, good in [("pip-bad", "pip-good"), ("uv-bad", "uv-good")]:
        bad_lines, good_lines = instructions(bad), instructions(good)
        copy_lines = {
            "COPY . .",
            "COPY requirements.txt ./",
            "COPY pyproject.toml uv.lock ./",
        }
        assert [x for x in bad_lines if x not in copy_lines] == [
            x for x in good_lines if x not in copy_lines
        ]


def test_pip_files_install_the_same_way_without_cleaning_the_cache():
    for name in ["pip-bad", "pip-good", "pip-slim"]:
        installs = [line for line in instructions(name) if "pip install" in line]
        assert installs == ["RUN pip install -r requirements.txt"]


def test_uv_good_matches_the_real_dockerfile_apart_from_labels():
    real = [
        line
        for line in (e3_build.ROOT / "Dockerfile").read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    label_start = index_of(real, "ARG REVISION")
    label_end = index_of(real, "CMD")
    without_labels = real[:label_start] + real[label_end:]
    assert instructions("uv-good") == without_labels


def test_every_base_image_is_pinned_by_digest():
    for name in MAIN + SIZE_ONLY:
        for image in base_images(dockerfile(name).read_text()):
            assert "@sha256:" in image


# ---- Plain helpers -----------------------------------------------------------


def test_round_plan_has_every_dockerfile_in_every_round_and_rotates():
    plan = round_plan(["a", "b", "c", "d"], 5)
    assert len(plan) == 20
    for r in range(1, 6):
        assert sorted(n for rr, _, n in plan if rr == r) == ["a", "b", "c", "d"]
    firsts = [n for _, p, n in plan if p == 1]
    assert firsts == ["a", "b", "c", "d", "a"]
    assert [p for rr, p, _ in plan if rr == 2] == [1, 2, 3, 4]


def test_base_images_reads_every_from_line():
    text = "# FROM not this\nFROM uv:1@sha256:x AS uv\nRUN x\nfrom slim:2\n"
    assert base_images(text) == ["uv:1@sha256:x", "slim:2"]


def test_count_lines_skips_comments_and_blank_lines():
    text = "# a comment\n\nFROM x\n  # indented comment\nRUN a \\\n    && b\n"
    assert count_lines(text) == 3


def test_change_one_line_changes_only_the_first_line(tmp_path):
    path = tmp_path / "main.py"
    path.write_text('"""Title.\n\nMore.\n"""\nx = 1\n')
    change_one_line(path, "round 2")
    lines = path.read_text().splitlines()
    assert lines[0] == '"""Title. (E3 change round 2)'
    assert lines[1:] == ["", "More.", '"""', "x = 1"]


def test_median_and_spread():
    assert e3_build.median_and_spread([3.0, 1.0, 2.0]) == "2.0 s (1.0-3.0)"
    assert e3_build.median_and_spread([]) == "-"


def test_last_line_is_short_and_skips_blank_lines():
    assert e3_build.last_line("a\nb\n\n") == "b"
    assert e3_build.last_line("") == ""
    assert len(e3_build.last_line("x" * 500)) == 200


# ---- The temporary copy ------------------------------------------------------


def make_repo(folder: Path) -> None:
    """A tiny pretend project with the files the context needs."""
    (folder / "app").mkdir(parents=True)
    (folder / "app" / "main.py").write_text('"""Main."""\n')
    (folder / "pyproject.toml").write_text("deps = []\n")
    (folder / "uv.lock").write_text("lock 1\n")
    (folder / ".env").write_text("SECRET=1\n")


class ProjectRunner(FakeRunner):
    """A pretend git and uv that act on the files, like the real ones."""

    def __init__(self, repo):
        super().__init__()
        self.repo = repo

    def __call__(self, command, cwd=None):
        result = super().__call__(command, cwd)
        if command[:2] == ["git", "ls-files"]:
            # git never lists ignored files such as .env; "gone.py" was
            # deleted but is still listed.
            return done("app/main.py\0pyproject.toml\0uv.lock\0gone.py\0")
        if command[:2] == ["uv", "add"]:
            (cwd / "pyproject.toml").write_text("deps = ['six==1.17.0']\n")
            (cwd / "uv.lock").write_text("lock 2\n")
        if command[:2] == ["uv", "export"]:
            lock = (cwd / "uv.lock").read_text()
            (cwd / "requirements.txt").write_text(
                "six==1.17.0\n" if "2" in lock else ""
            )
        return result


def test_context_copies_the_project_but_never_the_secrets(tmp_path):
    repo, copy = tmp_path / "repo", tmp_path / "copy"
    make_repo(repo)
    copy.mkdir()
    context = Context(copy, ProjectRunner(repo))
    context.prepare(repo)
    assert (copy / "app" / "main.py").read_text() == '"""Main."""\n'
    assert not (copy / ".env").exists()
    assert not (copy / "gone.py").exists()


def test_context_switches_between_plain_and_extra_package(tmp_path):
    repo, copy = tmp_path / "repo", tmp_path / "copy"
    make_repo(repo)
    copy.mkdir()
    context = Context(copy, ProjectRunner(repo))
    context.prepare(repo)
    # After preparing, the copy is back to the plain project.
    assert (copy / "uv.lock").read_text() == "lock 1\n"
    assert (copy / "requirements.txt").read_text() == ""
    change_one_line(copy / "app" / "main.py", "x")
    context.add_package()
    assert (copy / "uv.lock").read_text() == "lock 2\n"
    assert (copy / "requirements.txt").read_text() == "six==1.17.0\n"
    # The code change stays when the package is added ...
    assert "E3 change" in (copy / "app" / "main.py").read_text()
    # ... and reset undoes both.
    context.reset()
    assert (copy / "uv.lock").read_text() == "lock 1\n"
    assert (copy / "app" / "main.py").read_text() == '"""Main."""\n'
    # The real project was never touched.
    assert (repo / "uv.lock").read_text() == "lock 1\n"
    assert (repo / "app" / "main.py").read_text() == '"""Main."""\n'


def test_requirements_are_exported_with_exact_versions_from_the_lock(tmp_path):
    runner = FakeRunner()
    e3_build.export_requirements(tmp_path, runner)
    command, cwd = runner.commands[0]
    assert command[:3] == ["uv", "export", "--locked"]
    assert "--no-dev" in command and "--no-hashes" in command
    assert cwd == tmp_path


def test_real_lock_exports_only_exact_versions(tmp_path):
    # The real uv.lock, exported the way the script does it: every line
    # pins one exact version (name==version), nothing looser.
    import shutil

    for name in ["pyproject.toml", "uv.lock"]:
        shutil.copy(e3_build.ROOT / name, tmp_path / name)
    e3_build.export_requirements(tmp_path)
    lines = [
        line.split(";")[0].strip()
        for line in (tmp_path / "requirements.txt").read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert lines
    for line in lines:
        assert "==" in line and not any(c in line for c in "<>~*")


def test_a_failed_preparation_step_stops_the_script(tmp_path):
    with pytest.raises(SystemExit, match="git ls-files failed: ERROR: no space"):
        e3_build.copy_project(tmp_path, tmp_path, FakeRunner(fail=["git"]))


# ---- Builds ------------------------------------------------------------------


class FakeContext:
    """A context that only records what was asked of it."""

    def __init__(self, folder):
        self.folder = folder
        self.calls = []

    def reset(self):
        self.calls.append("reset")

    def add_package(self):
        self.calls.append("add_package")


def code_folder(tmp_path):
    """A folder holding app/main.py, for the "code" step."""
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text('"""Main."""\n')
    return tmp_path


def test_build_times_the_docker_command():
    runner = FakeRunner()
    seconds, ok, error = build(
        "uv-good", FakeContext(Path("/ctx")), runner, ticking_clock(7)
    )
    assert (seconds, ok, error) == (7.0, True, "")
    command = runner.commands[0][0]
    assert command[:2] == ["docker", "build"]
    assert command[-1] == "/ctx"
    assert "kuyruk-e3:uv-good" in command


def test_a_failed_build_gives_its_last_error_line():
    runner = FakeRunner(fail=["docker build "])
    seconds, ok, error = build(
        "pip-bad", FakeContext(Path("/c")), runner, ticking_clock()
    )
    assert not ok and error == "ERROR: no space"


def test_a_build_that_takes_too_long_is_a_failure():
    def slow(command, cwd=None):
        raise subprocess.TimeoutExpired(command, 1)

    _, ok, error = build("pip-bad", FakeContext(Path("/c")), slow, ticking_clock())
    assert not ok and "longer than" in error


def test_one_round_clears_the_cache_then_cold_code_package(tmp_path):
    folder = code_folder(tmp_path)
    context = FakeContext(folder)
    runner = FakeRunner(answers={"docker image inspect": "123456\n"})
    rows = measure_round("pip-good", 2, 3, context, "mac", runner, ticking_clock(5))
    assert [r["step"] for r in rows] == ["cold", "code", "package"]
    assert all(r["ok"] == "yes" and r["seconds"] == "5.00" for r in rows)
    assert all(r["image_bytes"] == "123456" for r in rows)
    assert rows[0]["round"] == 2 and rows[0]["position"] == 3
    assert set(rows[0]) <= set(BUILD_COLUMNS)
    # The cache was cleared once, before the cold build.
    texts = [" ".join(c) for c, _ in runner.commands]
    assert texts.index("docker builder prune --all --force") < next(
        i for i, t in enumerate(texts) if t.startswith("docker build ")
    )
    assert runner.ran("docker builder prune") == 1
    assert context.calls == ["reset", "add_package"]
    assert "E3 change round 2" in (folder / "app" / "main.py").read_text()


def test_a_failed_cold_build_is_recorded_and_the_rest_not_run(tmp_path):
    runner = FakeRunner(fail=["docker build "])
    rows = measure_round(
        "uv-bad", 1, 1, FakeContext(code_folder(tmp_path)), "ci", runner
    )
    assert [r["ok"] for r in rows] == ["no", "no", "no"]
    assert rows[0]["error"] == "ERROR: no space"
    assert rows[1]["error"] == "not run: the cold build failed"
    assert runner.ran("docker build ") == 1
    assert rows[0]["environment"] == "ci"


# ---- Images ------------------------------------------------------------------


def test_starts_says_yes_when_health_answers_and_removes_the_container():
    runner = FakeRunner(
        answers={"docker run": "abc123\n", "docker port": "127.0.0.1:55001\n"}
    )
    tries = iter([False, False, True])
    asked = []

    def healthy(url):
        asked.append(url)
        return next(tries)

    result = starts(
        "img", runner, healthy, sleep=lambda s: None, clock=ticking_clock(1)
    )
    assert result == "yes"
    assert asked[0] == "http://127.0.0.1:55001/health"
    run_command = runner.commands[0][0]
    assert "DEMO_MODE=true" in run_command and run_command[-1] == "img"
    assert runner.ran("docker rm --force abc123") == 1


def test_starts_says_no_when_health_never_answers():
    runner = FakeRunner(answers={"docker run": "abc\n", "docker port": "127.0.0.1:1\n"})
    result = starts(
        "img", runner, lambda url: False, sleep=lambda s: None, clock=ticking_clock(10)
    )
    assert result == "no"
    assert runner.ran("docker rm --force abc") == 1


def test_starts_says_no_when_the_container_cannot_start():
    runner = FakeRunner(fail=["docker run"])
    assert starts("img", runner, lambda url: True, sleep=lambda s: None) == "no"


def test_has_curl():
    assert e3_build.has_curl("img", FakeRunner()) == "yes"
    assert e3_build.has_curl("img", FakeRunner(fail=["docker run"])) == "no"


def test_image_bytes_is_blank_when_unknown():
    runner = FakeRunner(answers={"docker image inspect": "42\n"})
    assert e3_build.image_bytes("img", runner) == "42"
    assert e3_build.image_bytes("img", FakeRunner(fail=["docker"])) == ""


def test_gzipped_size_counts_the_packed_output():
    # A real command (this Python) writing 100,000 identical bytes: gzip packs
    # them into far fewer.
    import sys

    command = [sys.executable, "-c", "import sys; sys.stdout.write('a' * 100000)"]
    size = e3_build.gzipped_size(command)
    assert 0 < size < 1000
    failing = [sys.executable, "-c", "import sys; sys.exit(1)"]
    assert e3_build.gzipped_size(failing) == 0


def test_measure_images_covers_every_dockerfile_and_both_bases():
    rows = []
    runner = FakeRunner(answers={"docker image inspect": "1000\n"})
    measure_images(
        FakeContext(Path("/c")),
        "mac",
        rows.append,
        runner,
        compressed=lambda image: "500",
        started=lambda image: "yes",
    )
    assert [r["image"] for r in rows] == MAIN + SIZE_ONLY + ["base-full", "base-slim"]
    for row in rows:
        assert set(row) <= set(IMAGE_COLUMNS)
        assert row["image_bytes"] == "1000" and row["compressed_bytes"] == "500"
    assert all(r["starts"] == "yes" and r["lines"] > 0 for r in rows[:5])
    assert "starts" not in rows[5] and "lines" not in rows[5]


def test_measure_images_records_a_failed_build():
    rows = []
    measure_images(
        FakeContext(Path("/c")),
        "mac",
        rows.append,
        FakeRunner(fail=["docker build "]),
        compressed=lambda image: "",
        started=lambda image: "yes",
    )
    assert rows[0]["starts"] == "no" and "image_bytes" not in rows[0]


# ---- Command line and output -------------------------------------------------


def test_parse_arguments_and_output_paths(tmp_path):
    options = e3_build.parse_arguments([])
    assert options.rounds == 5 and options.environment == "mac"
    builds, images = e3_build.output_paths(options)
    assert builds == e3_build.DATA / "e3_builds.csv"
    assert images == e3_build.DATA / "e3_images.csv"
    options = e3_build.parse_arguments(["--environment", "ci"])
    assert e3_build.output_paths(options)[0].name == "e3_builds_ci.csv"
    options = e3_build.parse_arguments(["--trial", str(tmp_path)])
    assert options.rounds == 1
    assert e3_build.output_paths(options)[1] == tmp_path / "e3_images.csv"


def test_csv_out_writes_rows_at_once(tmp_path):
    out = e3_build.CsvOut(tmp_path / "x" / "a.csv", ["a", "b"])
    out.write({"a": 1, "b": 2})
    assert (tmp_path / "x" / "a.csv").read_text().splitlines() == ["a,b", "1,2"]
    out.close()


def test_setup_lines_name_the_machine_and_docker():
    runner = FakeRunner(
        answers={"docker version": "29.9.2\n", "docker info": "Linux\n"}
    )
    lines = e3_build.setup_lines(runner)
    assert lines[1] == "Docker Engine: 29.9.2"
    assert lines[2] == "Docker: Linux"
