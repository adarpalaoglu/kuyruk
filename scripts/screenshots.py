"""Take screenshots of every screen of the web interface (Slice 17).

The pictures in `docs/screenshots/` let the built interface be compared with
the approved mockup (`docs/mockup/*.dc.html`): every screen at 1280 px (a
laptop) and 390 px (a phone) in English, plus Welcome, Job and Results in
Turkish. Slice 17b's additions too: Create your account, New job with the
first-time guide, and a cancelled job's page and results with "Send the
texts without a result as a new job" (both widths in English, 1280 px in
Turkish), and the job page with its "i" explanation open (1280 px, both
languages).

It needs the running setup in demo mode (`docker compose up -d --wait`) and
the browser tests' library:

    uv run --group browser python scripts/screenshots.py

It first creates realistic data through the API, like the mockup's: user
"ayse" with finished, cancelled and running jobs, and a 500-text job that it
crashes on purpose, so the Job screen shows the "another worker took over"
notice (pressing the job page's own "Simulate a crash" button). At the end
the 500-text job is cancelled, so the worker is free again.
"""

import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

import httpx
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).parent.parent
OUT = ROOT / "docs" / "screenshots"
WEB = os.environ.get("KUYRUK_WEB_URL", "http://localhost:8080").rstrip("/")
API = os.environ.get("KUYRUK_API_URL", "http://localhost:8000").rstrip("/")
PASSWORD = "ayse-demo-1234"
SAMPLES = ROOT / "web" / "data" / "sample_reviews.json"


def log_in() -> tuple[str, str]:
    """Sign up (or log in) as ayse; a fresh name if ayse has another password."""
    for name in ["ayse", f"ayse_{uuid.uuid4().hex[:6]}"]:
        account = {"username": name, "password": PASSWORD}
        httpx.post(f"{API}/register", json=account, timeout=10)
        reply = httpx.post(f"{API}/token", data=account, timeout=10)
        if reply.status_code == 200:
            return name, reply.json()["access_token"]
    sys.exit("Could not sign up or log in.")


def make_data(name: str, token: str) -> dict:
    """Create the jobs the screens show; returns their tickets."""
    headers = {"Authorization": f"Bearer {token}"}
    texts = json.loads(SAMPLES.read_text(encoding="utf-8"))

    def send(task: str, count: int) -> str:
        job = {"task": task, "texts": (texts * (count // len(texts) + 1))[:count]}
        reply = httpx.post(f"{API}/jobs", json=job, headers=headers, timeout=30)
        reply.raise_for_status()
        return reply.json()["id"]

    def wait(ticket: str, states=("done", "failed", "cancelled")) -> None:
        for _ in range(600):
            state = httpx.get(f"{API}/jobs/{ticket}", headers=headers).json()["state"]
            if state in states:
                return
            time.sleep(1)
        sys.exit(f"Job {ticket} didn't finish.")

    tickets = {
        "count": send("count_tokens", 1000),
        "clean": send("normalise", 120),
        "summary": send("llm", 12),
    }
    for ticket in tickets.values():
        wait(ticket)
    # A cancelled job: started, then cancelled after a few texts.
    tickets["cancelled"] = send("llm", 20)
    wait(tickets["cancelled"], ("running",))
    time.sleep(6)
    httpx.post(f"{API}/jobs/{tickets['cancelled']}/cancel", headers=headers)
    # The big running job the Job screen shows.
    tickets["running"] = send("llm", 500)
    wait(tickets["running"], ("running",))
    tickets["headers"] = headers
    return tickets


def page_for(browser, token: str, name: str, width: int, lang: str, guide=False):
    """A page logged in as `name` (or logged out, token ""), in `lang`.

    `guide` shows New job's first-time guide, as right after sign-up (D129);
    otherwise it is hidden, as for someone who has used Kuyruk before.
    """
    context = browser.new_context(
        viewport={"width": width, "height": 900},
        locale="tr-TR" if lang == "tr" else "en-GB",
        bypass_csp=True,
    )
    script = f"localStorage.setItem('kuyruk.lang', '{lang}');"
    if token:
        script += (
            f"sessionStorage.setItem('kuyruk.pass', '{token}');"
            f"sessionStorage.setItem('kuyruk.user', '{name}');"
            f"localStorage.setItem('kuyruk.guide.{name}', "
            f"'{'show' if guide else 'hidden'}');"
        )
    context.add_init_script(script)
    return context.new_page()


def shoot(page, file: str) -> None:
    """Save the whole page (not just the visible part) as a PNG.

    The Job screen's live stream stays open, so the network is never idle
    there; it only waits for the page to have loaded.
    """
    live = "job.html" in page.url
    page.wait_for_load_state("load" if live else "networkidle")
    page.wait_for_timeout(600)
    page.screenshot(path=str(OUT / file), full_page=True)
    print(f"  {file}")


# The README shows only the top of two long pages (Slice 18a): down to the
# end of the element named here, plus a little of the background under it.
# A CSS selector ("a way to point at one part of a page") for each.
TOPS = {
    "welcome": ".problem-card",  # the hero and the first problem card
    "experiments": ".exp",  # the heading and Experiment 1's band
}
TOP_MARGIN = 24  # pixels of background kept under that element


def shoot_top(page, screen: str) -> None:
    """Save the top of a page, down to the end of its TOPS element.

    Pictures cut at a fixed height would end in the middle of a card when
    the layout changes; cutting at the element's own bottom edge doesn't.
    """
    # The element's bottom edge measured from the top of the whole page (not
    # of the visible window, which moves if the page is scrolled). None if
    # the element is missing.
    bottom = page.evaluate(
        """(selector) => {
            const element = document.querySelector(selector);
            if (!element) return null;
            return element.getBoundingClientRect().bottom + window.scrollY;
        }""",
        TOPS[screen],
    )
    if bottom is None:
        raise SystemExit(
            f"The {screen} page has no {TOPS[screen]} to cut at. Was it renamed? "
            "Update TOPS in scripts/screenshots.py."
        )
    height = bottom + TOP_MARGIN
    file = f"{screen}-top-1280-en.png"
    clip = {"x": 0, "y": 0, "width": 1280, "height": height}
    page.screenshot(path=str(OUT / file), full_page=True, clip=clip)
    print(f"  {file}")


def main() -> None:
    """Make the data, then save every screen."""
    OUT.mkdir(parents=True, exist_ok=True)
    name, token = log_in()
    print(f"Making data as {name} (about 2 minutes)...")
    tickets = make_data(name, token)
    headers = tickets.pop("headers")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()

        # The Job screen first, while the 500-text job runs: watch it, crash
        # its worker, and wait for the takeover notice.
        job = page_for(browser, token, name, 1280, "en")
        job.goto(f"{WEB}/job.html?ticket={tickets['running']}")
        expect(job.locator("#sentence")).to_have_text(re.compile("Summarising"))
        # Let it run a while first, so the screen shows some progress.
        job.wait_for_timeout(40_000)
        job.click("#crash")
        expect(job.locator("#recovered")).to_be_visible(timeout=60_000)
        job.wait_for_timeout(4000)
        print("Screens:")
        shoot(job, "job-1280-en.png")
        job.set_viewport_size({"width": 390, "height": 900})
        shoot(job, "job-390-en.png")
        job.set_viewport_size({"width": 1280, "height": 900})
        # The "i" panel on Cancel vs a crash, opened, in both languages.
        job.click("#crash-info-button")
        shoot(job, "job-info-1280-en.png")
        job.click("[data-testid=lang-switch]")
        shoot(job, "job-info-1280-tr.png")
        job.click("#crash-info-button")
        shoot(job, "job-1280-tr.png")
        job.context.close()

        screens = {
            "welcome": ("index.html", False),
            "signup": ("signup.html", False),
            "login": ("login.html", False),
            "new": ("new.html", True),
            "new-guide": ("new.html", True),
            "jobs": ("jobs.html", True),
            "results": (f"results.html?ticket={tickets['summary']}", True),
            "status": ("status.html", True),
            "experiments": ("experiments.html", True),
            "job-cancelled": (f"job.html?ticket={tickets['cancelled']}", True),
            "results-cancelled": (f"results.html?ticket={tickets['cancelled']}", True),
        }
        for width in (1280, 390):
            for screen, (address, logged_in) in screens.items():
                page = page_for(
                    browser,
                    token if logged_in else "",
                    name,
                    width,
                    "en",
                    guide=screen == "new-guide",
                )
                page.goto(f"{WEB}/{address}")
                if screen == "new":
                    page.click("#samples")
                    expect(page.locator("#cost-line")).to_contain_text("tokens")
                if screen == "jobs":
                    expect(page.locator("#job-list li")).not_to_have_count(0)
                    page.click("[data-testid=account]")
                    expect(page.locator("#account-menu .bar")).to_be_visible()
                shoot(page, f"{screen}-{width}-en.png")
                if width == 1280 and screen in TOPS:
                    shoot_top(page, screen)
                page.context.close()
        turkish = ("welcome", "results", "signup", "new-guide", "job-cancelled")
        for screen in turkish:
            address, logged_in = screens[screen]
            page = page_for(
                browser,
                token if logged_in else "",
                name,
                1280,
                "tr",
                guide=screen == "new-guide",
            )
            page.goto(f"{WEB}/{address}")
            shoot(page, f"{screen}-1280-tr.png")
            page.context.close()
        browser.close()
    # Free the worker again.
    httpx.post(f"{API}/jobs/{tickets['running']}/cancel", headers=headers)
    print(f"Saved to {OUT.relative_to(ROOT)}.")


if __name__ == "__main__":
    main()
