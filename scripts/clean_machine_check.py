"""Clean-machine check: does `docker compose up` work for a first-time visitor?

Spec 12 says Slice 9 is done when Kuyruk starts on a clean machine. This
script imitates one (D86) and then uses Kuyruk like a visitor would:

1. copies the project's files into a temporary folder, using git's list of
   files, so the local `.env` (git-ignored) is *not* there: just like a fresh
   download, compose must fall back to its demo-only values;
2. runs everything under its own Docker project name, `kuyruk-check`, so the
   containers, the database volume (Docker's storage folder) and the images of
   the normal local setup are never touched (`--rmi local` removes only the
   images this project built, never the shared Postgres image);
3. removes anything left from an earlier check, builds the image with no
   cache (nothing reused from earlier builds), starts it with
   `docker compose up`, and waits until all four containers are healthy;
4. checks the web interface answers on its own address and that the API
   lets only the interface's pages call it from a browser (CORS, D121);
5. signs up, logs in, sends a job and watches its live progress to the end;
6. sends a longer job, presses the demo crash button in the middle, and checks
   that Docker restarted the worker, the new worker took the job over
   (`recovered`), and every text still finished;
7. prints a pass/fail line per step, then removes everything again (also
   after a failure).

Run from the project folder (Docker Desktop must be running, and nothing else
may use ports 8000, 8080 or 5432, e.g. stop the normal setup with
`docker compose stop`):

    uv run python scripts/clean_machine_check.py

It exits with code 0 if every step passed, 1 otherwise.
"""

import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

import httpx

PROJECT = "kuyruk-check"
API = "http://localhost:8000"
WEB = "http://localhost:8080"
ROOT = Path(__file__).resolve().parent.parent

# A few Turkish reviews, repeated to make jobs of any length.
REVIEWS = [
    "Kargo çok hızlı geldi, ürün de güzel ama kutusu ezikti.",
    "İade süreci uzun sürdü; müşteri hizmetleri ilgisizdi.",
    "Fiyatına göre gayet başarılı, tavsiye ederim.",
    "Ürün açıklamadaki gibi değildi, rengi farklı çıktı.",
]

# Seconds to wait for the containers to become healthy, and at most for one
# live-progress stream to reach its end.
STARTUP_TIMEOUT = 180
STREAM_TIMEOUT = 120


class Check:
    """Collects the result of each step and prints the summary at the end."""

    def __init__(self) -> None:
        """Start with no results and the clock running."""
        self.results: list[tuple[bool, str, float]] = []
        self.started = time.monotonic()

    def record(self, passed: bool, name: str, detail: str = "") -> bool:
        """Remember one step's result, print it at once, and return `passed`."""
        elapsed = time.monotonic() - self.started
        line = f"{name}{': ' + detail if detail else ''}"
        self.results.append((passed, line, elapsed))
        print(f"{'✅' if passed else '❌'} [{elapsed:6.1f} s] {line}", flush=True)
        return passed

    def summary(self) -> bool:
        """Print the pass/fail summary; True if every step passed."""
        passed = sum(1 for ok, _, _ in self.results if ok)
        total = len(self.results)
        all_passed = passed == total and total > 0
        print()
        print("=" * 64)
        print(f"Clean-machine check: {'PASS' if all_passed else 'FAIL'}")
        seconds = time.monotonic() - self.started
        print(f"{passed} of {total} steps passed in {seconds:.0f} s")
        for ok, line, _ in self.results:
            if not ok:
                print(f"  failed: {line}")
        print("=" * 64)
        return all_passed


def compose(
    folder: Path, *args: str, check: bool = True
) -> subprocess.CompletedProcess:
    """Run `docker compose -p kuyruk-check ...` in the copied project folder."""
    return subprocess.run(
        ["docker", "compose", "-p", PROJECT, *args],
        cwd=folder,
        check=check,
        text=True,
        capture_output=True,
    )


def port_in_use(port: int) -> bool:
    """True if something on this computer already answers on `port`."""
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def copy_project(target: Path) -> None:
    """Copy the files git knows about (committed or new, not ignored).

    Ignored files, above all `.env`, are left behind, so the copy looks like a
    fresh download plus this session's not-yet-committed changes.
    """
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split("\0")
    for name in filter(None, listed):
        source = ROOT / name
        # A file deleted but not yet committed is still on git's list.
        if source.is_file():
            destination = target / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)


def read_events(response: httpx.Response) -> Iterator[tuple[str, dict]]:
    """Turn a live-progress (SSE) stream into (event name, data) pairs.

    An SSE stream is plain text: an `event:` line, a `data:` line, and a blank
    line after each event. Lines starting with ":" are keep-alive comments.
    """
    name, data = "message", ""
    for line in response.iter_lines():
        if line.startswith("event:"):
            name = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            data = line.removeprefix("data:").strip()
        elif line == "" and data:
            yield name, json.loads(data)
            name, data = "message", ""


def restart_count(folder: Path) -> int:
    """How many times Docker has restarted the worker container."""
    container = compose(folder, "ps", "-q", "worker").stdout.strip()
    inspected = subprocess.run(
        ["docker", "inspect", "-f", "{{.RestartCount}}", container],
        check=True,
        capture_output=True,
        text=True,
    )
    return int(inspected.stdout.strip())


def crash_count(folder: Path, ticket: str) -> int:
    """Read the job's crash count straight from the database.

    It is an internal field, never in an API reply, so ask Postgres inside
    the db container.
    """
    query = f"SELECT crash_count FROM jobs WHERE id = '{ticket}'"
    result = compose(
        folder,
        "exec",
        "-T",
        "db",
        "psql",
        "-U",
        "kuyruk",
        "-d",
        "kuyruk",
        "-tA",
        "-c",
        query,
    )
    return int(result.stdout.strip())


def send_job(client: httpx.Client, headers: dict, count: int) -> str:
    """Send an `llm` job of `count` texts and return its ticket."""
    texts = [REVIEWS[n % len(REVIEWS)] for n in range(count)]
    reply = client.post("/jobs", json={"task": "llm", "texts": texts}, headers=headers)
    reply.raise_for_status()
    return reply.json()["id"]


def watch_job(check: Check, client: httpx.Client, headers: dict, count: int) -> None:
    """Step 4: send a job and follow its live progress to the end."""
    ticket = send_job(client, headers, count)
    check.record(True, f"Job of {count} texts sent", f"ticket {ticket}")
    seen = []
    with client.stream(
        "GET", f"/jobs/{ticket}/events", headers=headers, timeout=STREAM_TIMEOUT
    ) as stream:
        for name, data in read_events(stream):
            seen.append(name)
            if name == "end":
                check.record(
                    data["state"] == "done",
                    "Live progress reached the end",
                    f"end: {data['state']} ({seen.count('progress')} progress events)",
                )
                break
    job = client.get(f"/jobs/{ticket}", headers=headers).json()
    check.record(
        job["succeeded"] == count and job["failed"] == 0,
        "All texts finished",
        f"{job['succeeded']} ok, {job['failed']} failed of {job['total']}",
    )


def crash_and_recover(
    check: Check, folder: Path, client: httpx.Client, headers: dict, count: int
) -> None:
    """Step 5: crash the worker mid-job and watch another one finish it."""
    restarts_before = restart_count(folder)
    ticket = send_job(client, headers, count)
    check.record(
        True, f"Job of {count} texts sent for the crash demo", f"ticket {ticket}"
    )
    crashed_at = None
    recovered = None
    with client.stream(
        "GET", f"/jobs/{ticket}/events", headers=headers, timeout=STREAM_TIMEOUT
    ) as stream:
        for name, data in read_events(stream):
            # The first progress event is the opening snapshot (0 done, job
            # maybe still queued); press the button once texts are being done,
            # i.e. a worker is really running the job.
            if name == "progress" and data["done"] > 0 and crashed_at is None:
                reply = client.post("/demo/crash-worker", headers=headers)
                crashed_at = time.monotonic()
                check.record(
                    reply.status_code == 202,
                    "Demo crash button pressed",
                    f"{reply.status_code}, at {data['done']} of {data['total']} done",
                )
            elif name == "recovered":
                recovered = data
                check.record(
                    True,
                    "Stream showed the recovery",
                    f"recovered from text {data['from_position']}, "
                    f"{time.monotonic() - crashed_at:.1f} s after the crash",
                )
            elif name == "end":
                check.record(
                    data["state"] == "done",
                    "Crashed job ended",
                    f"end: {data['state']}",
                )
                break
    if recovered is None:
        check.record(False, "Stream showed the recovery", "no recovered event")
    restarts_after = restart_count(folder)
    check.record(
        restarts_after > restarts_before,
        "Docker restarted the crashed worker",
        f"restart count {restarts_before} -> {restarts_after}",
    )
    job = client.get(f"/jobs/{ticket}", headers=headers).json()
    check.record(
        job["succeeded"] == count and job["failed"] == 0,
        "No text lost after the crash",
        f"{job['succeeded']} ok, {job['failed']} failed of {job['total']}",
    )
    crashes = crash_count(folder, ticket)
    check.record(crashes == 1, "Crash counted once", f"crash count {crashes}")


def run(check: Check, folder: Path) -> None:
    """All the steps, in order; stops at the first one that makes the rest pointless."""
    copy_project(folder)
    check.record(
        not (folder / ".env").exists(), "Project copied without .env", str(folder)
    )

    compose(folder, "down", "-v", "--rmi", "local", "--remove-orphans", check=False)
    check.record(True, "Old kuyruk-check containers, volumes and images removed")

    built = compose(folder, "build", "--no-cache", "--pull", check=False)
    if not check.record(built.returncode == 0, "Image built with no cache"):
        print(built.stdout[-3000:], built.stderr[-3000:])
        return
    image = subprocess.run(
        ["docker", "image", "ls", f"{PROJECT}-api", "--format", "{{.Size}}"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    check.record(True, "Image size", image)

    up = compose(
        folder,
        "up",
        "-d",
        "--wait",
        "--wait-timeout",
        str(STARTUP_TIMEOUT),
        check=False,
    )
    if not check.record(
        up.returncode == 0, "docker compose up: db, api, worker, web healthy"
    ):
        print(up.stdout[-3000:], up.stderr[-3000:])
        print(compose(folder, "logs", "--tail", "40", check=False).stdout)
        return

    page = httpx.get(f"{WEB}/", timeout=30)
    check.record(
        page.status_code == 200 and "js/pages/welcome.js" in page.text,
        "Web interface answers",
        str(page.status_code),
    )
    allowed = httpx.options(
        f"{API}/jobs",
        headers={
            "Origin": WEB,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
        timeout=30,
    )
    blocked = httpx.get(f"{API}/health", headers={"Origin": "http://evil.example"})
    check.record(
        allowed.headers.get("access-control-allow-origin") == WEB
        and "access-control-allow-origin" not in blocked.headers,
        "CORS: the interface allowed, another site not",
    )

    with httpx.Client(base_url=API, timeout=30) as client:
        health = client.get("/health")
        check.record(health.json() == {"status": "ok"}, "GET /health", health.text)
        account = {"username": "ziyaretci", "password": "deneme1234"}
        registered = client.post("/register", json=account)
        check.record(
            registered.status_code == 201, "Signed up", str(registered.status_code)
        )
        token = client.post("/token", data=account)
        if not check.record(
            token.status_code == 200, "Logged in", str(token.status_code)
        ):
            return
        headers = {"Authorization": f"Bearer {token.json()['access_token']}"}

        watch_job(check, client, headers, count=10)
        crash_and_recover(check, folder, client, headers, count=40)


def main() -> None:
    """Check the ports, run every step, always clean up, print the summary."""
    check = Check()
    busy = [port for port in (8000, 8080, 5432) if port_in_use(port)]
    if busy:
        check.record(
            False,
            "Ports free",
            f"port(s) {busy} in use; stop the normal setup with `docker compose stop`",
        )
        sys.exit(0 if check.summary() else 1)

    folder = Path(tempfile.mkdtemp(prefix="kuyruk-check-")) / "kuyruk"
    folder.mkdir()
    try:
        run(check, folder)
    except Exception as error:  # any surprise is a failed step, not a crash
        check.record(False, "Unexpected error", repr(error))
        print(compose(folder, "logs", "--tail", "40", check=False).stdout)
    finally:
        compose(folder, "down", "-v", "--rmi", "local", "--remove-orphans", check=False)
        shutil.rmtree(folder.parent, ignore_errors=True)
        check.record(
            True, "Cleaned up: kuyruk-check containers, volumes and images removed"
        )
    sys.exit(0 if check.summary() else 1)


if __name__ == "__main__":
    main()
