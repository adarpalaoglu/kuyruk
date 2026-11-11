"""Shared helpers for the browser tests of the web interface (D123).

These tests drive a real browser (Chromium, through Playwright) against the
real running setup: `docker compose up` in demo mode, with the interface at
KUYRUK_WEB_URL and the API at KUYRUK_API_URL. Without KUYRUK_WEB_URL every
test here is skipped, so the normal fast test run is unaffected. Run them:

    docker compose up -d --wait
    KUYRUK_WEB_URL=http://localhost:8080 \\
        uv run --group browser pytest tests/browser -v --no-cov

(--no-cov: these tests exercise the containers, not this folder's code, so
the coverage gate doesn't apply to them.)

The browser is opened with `bypass_csp`: the interface's safety header
(Content-Security-Policy) forbids running code the page didn't load itself,
which is exactly how Playwright waits for things and how the accessibility
checker (axe) is added. This changes only the test browser, not the site.
"""

import json
import os
import re
import uuid
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

WEB = os.environ.get("KUYRUK_WEB_URL", "").rstrip("/")
API = os.environ.get("KUYRUK_API_URL", "http://localhost:8000").rstrip("/")
PASSWORD = "gizli1234"
AXE = Path(__file__).parent / "vendor" / "axe-4.14.0.min.js"


def pytest_collection_modifyitems(items):
    """Mark every test in this folder `browser`; skip them without WEB.

    Like the Postgres tests without POSTGRES_TEST_URL: the normal test run
    lists them as skipped.
    """
    skip = pytest.mark.skip(reason="set KUYRUK_WEB_URL to run the browser tests")
    for item in items:
        if "tests/browser" in str(item.fspath):
            item.add_marker(pytest.mark.browser)
            if not WEB:
                item.add_marker(skip)


@pytest.fixture(scope="session")
def browser() -> Iterator:
    """One Chromium for the whole run (starting it is the slow part)."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        chromium = playwright.chromium.launch()
        yield chromium
        chromium.close()


def new_page(browser, width=1280, locale="en-GB"):
    """A fresh page: its own storage (nobody logged in), English by default."""
    context = browser.new_context(
        viewport={"width": width, "height": 900},
        locale=locale,
        bypass_csp=True,
        accept_downloads=True,
    )
    page = context.new_page()
    errors = []
    # Any error the page's own code throws makes the test fail at the end.
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.errors = errors
    return page


# Where a failing test's last screen is saved (CI offers it as a download).
FAILURES = Path(__file__).parents[2] / "browser-failures"


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Remember each test's outcome on the test, so `page` can see a failure."""
    outcome = yield
    report = outcome.get_result()
    setattr(item, f"report_{report.when}", report)


@pytest.fixture
def page(browser, request) -> Iterator:
    """An English page at desktop width, closed after the test.

    If the test failed, the page as it was is saved as a picture in
    browser-failures/, to see what went wrong.
    """
    fresh = new_page(browser)
    yield fresh
    report = getattr(request.node, "report_call", None)
    # The picture first, so it shows the failure, not the clean-up after it.
    if report is not None and report.failed:
        FAILURES.mkdir(exist_ok=True)
        fresh.screenshot(
            path=str(FAILURES / f"{request.node.name}.png"), full_page=True
        )
    cancel_unfinished_jobs(fresh)
    assert fresh.errors == [], f"the page's code failed: {fresh.errors}"
    fresh.context.close()


def unique_name() -> str:
    """A username nobody has used yet (3–30 of a–z, 0–9, _)."""
    return "t" + uuid.uuid4().hex[:12]


def sign_up(page, keep_guide=False) -> str:
    """Create an account through the interface; returns the username.

    Sign-up switches the first-time guide on (D129); it is hidden again here
    unless `keep_guide`, so the other tests see New job as usual.
    """
    name = unique_name()
    page.goto(f"{WEB}/signup.html")
    page.fill("#username", name)
    page.fill("#password", PASSWORD)
    page.fill("#confirm", PASSWORD)
    page.click("#signup-submit")
    page.wait_for_url(re.compile(r"/new\.html$"))
    if not keep_guide:
        page.evaluate(f"localStorage.setItem('kuyruk.guide.{name}', 'hidden')")
        # New job is already showing the guide; open it again without.
        page.reload()
        page.wait_for_load_state("networkidle")
    return name


def log_in(page, name, password=PASSWORD) -> None:
    """Log in through the Log in page (after a log-out, or in a new tab)."""
    page.goto(f"{WEB}/login.html")
    page.fill("#username", name)
    page.fill("#password", password)
    page.click("#login-submit")
    page.wait_for_url(re.compile(r"/new\.html"))


def pass_of(page) -> str:
    """The login pass the page keeps in sessionStorage."""
    return page.evaluate("sessionStorage.getItem('kuyruk.pass')")


def api(page, method, path, **kwargs) -> httpx.Response:
    """Call the API directly as the page's logged-in user (to check results)."""
    headers = {"Authorization": f"Bearer {pass_of(page)}"}
    return httpx.request(method, API + path, headers=headers, timeout=10, **kwargs)


def sample_texts() -> list[str]:
    """The sample reviews behind the "Use sample reviews" button."""
    path = Path(__file__).parents[2] / "web" / "data" / "sample_reviews.json"
    return json.loads(path.read_text(encoding="utf-8"))


def send_job(page, texts, task="llm") -> str:
    """Fill New job and press Send; returns the ticket from the job page."""
    page.goto(f"{WEB}/new.html")
    page.check(f"input[name=task][value={task}]", force=True)
    page.fill("#textbox", "\n".join(texts))
    page.click("#send")
    page.wait_for_url(re.compile(r"/job\.html\?ticket="))
    return page.url.split("ticket=")[1]


def cancel_unfinished_jobs(page) -> None:
    """Cancel the page user's queued and running jobs, at the end of a test.

    There is one worker, so a job a test leaves behind (e.g. one that failed
    halfway) would make every later test's job wait in the queue.
    """
    try:
        if not page.context.pages or not pass_of(page):
            return
        for state in ("queued", "running"):
            reply = api(page, "GET", "/jobs", params={"status": state})
            # E.g. the pass ran out: nothing more can be cleaned up.
            if reply.status_code != 200:
                return
            for job in reply.json():
                api(page, "POST", f"/jobs/{job['id']}/cancel")
    except Exception as error:  # noqa: BLE001 - clean-up must never hide a result
        print(f"Couldn't cancel the test's unfinished jobs: {error}")


def check_accessibility(page) -> None:
    """Run axe (an automatic accessibility checker) on the page as it is now.

    WCAG 2.1 A and AA rules: labels, names, contrast (at least 4.5:1 for
    normal text), roles, language. Any violation fails, with its rule and
    the elements it found.
    """
    page.add_script_tag(path=str(AXE))
    found = page.evaluate(
        """async () => {
            const result = await axe.run(document, {
              runOnly: {
                type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'],
              },
            });
            return result.violations.map(v => ({
              rule: v.id, impact: v.impact,
              where: v.nodes.slice(0, 3).map(n => n.target.join(' ')),
            }));
        }"""
    )
    assert found == [], f"accessibility problems on {page.url}: {found}"
