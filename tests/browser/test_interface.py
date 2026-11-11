"""Browser tests of the web interface: the whole demo, as a visitor does it.

Each test signs up its own new user, so the tests never see each other's
jobs. They run against `docker compose up` in demo mode (fake AI replaying
the real AI's times, heartbeat 1 s, stuck after 5 s): the 100 sample reviews
take about 3 minutes, a 12-text job about 20 seconds, and a crash is
recovered within seconds. In demo mode every AI answer is "Demo: " plus the
text's first words (D125).
"""

import json
import re

import pytest

from tests.browser.conftest import (
    WEB,
    api,
    check_accessibility,
    new_page,
    pass_of,
    sample_texts,
    send_job,
    sign_up,
)

# Playwright is only in the optional `browser` group: without it (e.g. the
# CI `test` job, or after a plain `uv sync`) this whole file is skipped.
expect = pytest.importorskip("playwright.sync_api").expect

# The longest a demo job (up to the 100 sample reviews) may take before a
# test gives up.
JOB_SECONDS = 360


def wait_until_finished(page, word="Done"):
    """Wait on the job page until its top line starts with `word`."""
    expect(page.locator("#kicker")).to_have_text(
        re.compile(f"^{word} "), timeout=JOB_SECONDS * 1000
    )


def test_sign_up_send_samples_watch_live_and_download(page):
    sign_up(page)
    expect(page.locator("h1")).to_have_text("What should Kuyruk do?")
    page.click("#samples")
    # All 100 sample reviews, one per line; the cost line from
    # POST /jobs/estimate (19,192 tokens).
    expect(page.locator("#cost-line")).to_have_text(
        re.compile(r"^100 texts · 19,192 tokens · [\d,]+ left today$")
    )
    page.click("#send")
    page.wait_for_url(re.compile(r"/job\.html\?ticket="))
    # Live: the top line says Live, the sentence follows the job.
    expect(page.locator("#kicker")).to_have_text(
        re.compile(r"^Live · Summarise · ticket ")
    )
    expect(page.locator("#sentence")).to_have_text(
        re.compile(r"(Waiting for a worker|Summarising text \d+ of 100)…")
    )
    expect(page.locator("#dots .dot")).to_have_count(100)
    wait_until_finished(page)
    expect(page.locator("#big-count")).to_have_text("100 / 100")
    expect(page.locator("#dots .dot.ok")).to_have_count(100, timeout=5000)
    expect(page.locator("#cancel")).to_be_hidden()

    page.click("#results-link")
    expect(page.locator("#headline")).to_have_text("Done. 100 summaries.")
    expect(page.locator("#rows > div")).to_have_count(100)
    first = page.locator("#rows > div").first
    expect(first.locator(".original")).to_have_text(sample_texts()[0])
    # The demo AI's answer: labelled, and made from the text itself (D125).
    first_words = " ".join(sample_texts()[0].split()[:6])
    expect(first.locator(".result")).to_have_text(
        re.compile(rf"^Demo: {re.escape(first_words)}")
    )

    with page.expect_download() as csv_download:
        page.click("#csv")
    # Read as bytes: reading as text would turn the CSV's \r\n line ends into \n.
    csv_text = csv_download.value.path().read_bytes().decode("utf-8")
    # The byte-order mark, so Excel reads it as UTF-8 (Turkish letters).
    assert csv_text.startswith(
        "\ufeffposition,original_text,result,state,error_message"
    )
    assert csv_text.count("\r\n") == 101
    with page.expect_download() as json_download:
        page.click("#json")
    rows = json.loads(json_download.value.path().read_text(encoding="utf-8"))
    assert [row["position"] for row in rows] == list(range(1, 101))
    assert all(
        row["state"] == "ok" and row["result"].startswith("Demo: ") for row in rows
    )
    # Every answer differs (each echoes its own text), unlike a fixed answer.
    assert len({row["result"] for row in rows}) > 90


def test_my_jobs_lists_and_filters(page):
    sign_up(page)
    send_job(page, ["bir", "iki"], task="normalise")
    wait_until_finished(page)
    page.goto(f"{WEB}/jobs.html")
    rows = page.locator("#job-list li")
    expect(rows).to_have_count(1)
    expect(rows.first).to_contain_text("Clean · 2 texts")
    expect(rows.first).to_contain_text("Done")
    page.click("#filters button[data-state=cancelled]")
    expect(page.locator("#job-list")).to_contain_text("No jobs in this state.")
    page.click("#filters button[data-state=done]")
    expect(rows).to_have_count(1)
    expect(page.locator("#filters button[data-state=done]")).to_have_attribute(
        "aria-pressed", "true"
    )


def test_double_click_on_send_makes_one_job(page):
    sign_up(page)
    page.fill("#textbox", "Fena değil, iş görür.")
    # Two clicks in the same instant, as a double-click does.
    page.evaluate(
        "() => { const b = document.querySelector('#send'); b.click(); b.click(); }"
    )
    page.wait_for_url(re.compile(r"/job\.html\?ticket="))
    assert len(api(page, "GET", "/jobs").json()) == 1


def test_resending_the_same_content_reuses_the_ticket(page):
    sign_up(page)
    page.fill("#textbox", "aldim begendim tesekkurler")
    # The first send fails on the way (as a dropped connection would), the
    # second press must reuse the same Idempotency-Key.
    keys = []

    def remember_key(route):
        keys.append(route.request.headers.get("idempotency-key"))
        if len(keys) == 1:
            route.abort()
        else:
            route.continue_()

    page.route("**/jobs", remember_key)
    page.click("#send")
    expect(page.locator("#send-error")).to_contain_text("Can't reach Kuyruk's API")
    page.click("#send")
    page.wait_for_url(re.compile(r"/job\.html\?ticket="))
    assert keys[0] and keys[0] == keys[1]


def test_a_broken_job_is_explained_before_sending(page):
    sign_up(page)
    page.fill("#textbox", "x" * 2001)
    expect(page.locator("#send-error")).to_contain_text("Kuyruk can't accept this")
    expect(page.locator("#send-error")).to_contain_text("Request ID")


def test_cancel_a_running_job(page):
    sign_up(page)
    ticket = send_job(page, sample_texts())
    expect(page.locator("#kicker")).to_have_text(re.compile(r"^Live "))
    page.click("#cancel")
    wait_until_finished(page, "Cancelled")
    expect(page.locator("#sentence")).to_have_text(
        "Cancelled. Finished texts keep their results."
    )
    assert api(page, "GET", f"/jobs/{ticket}").json()["state"] == "cancelled"


def test_crash_the_worker_and_watch_it_recover(page):
    sign_up(page)
    ticket = send_job(page, sample_texts())
    # Wait until the worker has really started, so there is a running job.
    expect(page.locator("#sentence")).to_have_text(
        re.compile(r"Summarising text \d+ of 100…"), timeout=30_000
    )
    # Keep watching the job in this tab; crash from a second tab. The
    # "another worker took over" notice is shown to whoever watches live at
    # the takeover (someone who opens the job later gets only a snapshot, D73).
    # Each tab keeps its own login (sessionStorage, D116), so the second tab
    # gets the same pass, as logging in there would give it.
    crash_tab = page.context.new_page()
    crash_tab.add_init_script(
        f"sessionStorage.setItem('kuyruk.pass', {json.dumps(pass_of(page))});"
        "sessionStorage.setItem('kuyruk.user', 'tester');"
    )
    crash_tab.goto(f"{WEB}/experiments.html#crash")
    crash_tab.click("#crash-button")
    expect(crash_tab.locator("#crash-message")).to_contain_text(
        re.compile("(Asking the worker to crash|The worker has stopped)")
    )
    steps = crash_tab.locator("#crash-steps li")
    # Step 1 at once; 2 when another worker takes over; 3 at its first text.
    expect(steps.nth(0)).to_have_class(re.compile("is-lit"), timeout=10_000)
    expect(steps.nth(1)).to_have_class(re.compile("is-lit"), timeout=60_000)
    expect(steps.nth(2)).to_have_class(re.compile("is-lit"), timeout=60_000)
    expect(crash_tab.locator("#crash-message")).to_contain_text("nothing was lost")
    expect(crash_tab.locator("#when-2")).to_have_text(re.compile(r"^after [\d.]+ s$"))
    expect(crash_tab.locator("#crash-message a")).to_have_attribute(
        "href", f"job.html?ticket={ticket}"
    )

    expect(page.locator("#recovered")).to_contain_text(
        re.compile(r"The worker stopped at text \d+\. Another worker took over")
    )
    expect(page.locator("#recovered")).to_contain_text("Nothing was lost.")
    wait_until_finished(page)
    job = api(page, "GET", f"/jobs/{ticket}").json()
    assert job["state"] == "done"
    assert job["succeeded"] + job["failed"] == 100


def test_crash_without_a_running_job_says_what_to_do(page):
    sign_up(page)
    page.goto(f"{WEB}/experiments.html#crash")
    page.click("#crash-button")
    expect(page.locator("#crash-message")).to_contain_text("Start a job first")
    expect(page.locator("#crash-message a")).to_have_attribute("href", "new.html")


def test_a_dropped_stream_reconnects_with_a_fresh_snapshot(page):
    sign_up(page)
    opened = []

    def drop_first(route):
        opened.append(route.request.url)
        # The first connection fails, as a network drop would.
        if len(opened) == 1:
            route.abort()
        else:
            route.continue_()

    page.route("**/events", drop_first)
    send_job(page, sample_texts()[:12])
    wait_until_finished(page)
    assert len(opened) >= 2
    # The login pass went in the header, never in the address (D67).
    assert all("token" not in url and pass_of(page) not in url for url in opened)


def test_language_switch_changes_every_screen_and_is_remembered(page):
    sign_up(page)
    ticket = send_job(page, ["Kargo hızlı geldi."], task="count_tokens")
    wait_until_finished(page)
    turkish = {
        "index.html": "Demoda her şey çalışır.",
        "experiments.html": "Bilerek bozduk.",
        "new.html": "Kuyruk ne yapsın?",
        "jobs.html": "İşlerim",
        f"results.html?ticket={ticket}": "Bitti. 1 metnin token'ları sayıldı.",
        "status.html": "Her şey çalışıyor.",
    }
    page.goto(f"{WEB}/index.html")
    page.click("[data-testid=lang-switch]")
    expect(page.locator("html")).to_have_attribute("lang", "tr")
    for address, heading in turkish.items():
        page.goto(f"{WEB}/{address}")
        expect(page.locator("h1").first).to_contain_text(heading)
        expect(page.locator("[data-testid=lang-switch]")).to_have_text("EN")
    page.goto(f"{WEB}/job.html?ticket={ticket}")
    expect(page.locator("#kicker")).to_have_text(
        re.compile(r"^Bitti · Token say · takip no ")
    )
    # Remembered in localStorage (not a secret); switch back.
    assert page.evaluate("localStorage.getItem('kuyruk.lang')") == "tr"
    page.click("[data-testid=lang-switch]")
    expect(page.locator("#kicker")).to_have_text(re.compile(r"^Done · Count · ticket "))
    page.goto(f"{WEB}/login.html")
    expect(page.locator("h1")).to_have_text("Log in.")


def test_first_visit_follows_the_browsers_language(browser):
    turkish = new_page(browser, locale="tr-TR")
    turkish.goto(f"{WEB}/index.html")
    expect(turkish.locator("h1")).to_contain_text("Demoda her şey çalışır.")
    # Turkish number style from findings.json: "1.331 MB", "%98".
    expect(turkish.locator("main")).to_contain_text("1.331 MB")
    turkish.context.close()


def test_the_pass_stays_in_this_tab_only_and_log_out_forgets_it(page):
    sign_up(page)
    assert pass_of(page)
    # Never in localStorage (kept on the computer), never in the address.
    stored = page.evaluate("Object.keys(localStorage)")
    assert all("pass" not in key for key in stored)
    assert pass_of(page) not in page.url
    page.click("[data-testid=account]")
    expect(page.locator("#account-menu")).to_contain_text("AI budget today")
    expect(page.locator("#account-menu")).to_contain_text(
        re.compile(r"tokens left · resets \d\d:\d\d")
    )
    page.click("[data-testid=logout]")
    page.wait_for_url(re.compile(r"/login\.html"))
    assert pass_of(page) is None
    # A page that needs the login now sends you to log in.
    page.goto(f"{WEB}/jobs.html")
    page.wait_for_url(re.compile(r"/login\.html\?next=jobs\.html"))


def test_wrong_password_shows_the_api_message(page):
    name = sign_up(page)
    page.evaluate("sessionStorage.clear()")
    page.goto(f"{WEB}/login.html")
    page.fill("#username", name)
    page.fill("#password", "wrong-password")
    page.click("#login-submit")
    expect(page.locator("#login-error")).to_contain_text(
        "Incorrect username or password."
    )


def test_turkish_screens_show_turkish_error_messages(browser):
    turkish = new_page(browser, locale="tr-TR")
    name = sign_up(turkish)
    turkish.evaluate("sessionStorage.clear()")
    turkish.goto(f"{WEB}/login.html")
    turkish.fill("#username", name)
    turkish.fill("#password", "wrong-password")
    turkish.click("#login-submit")
    expect(turkish.locator("#login-error")).to_contain_text(
        "Kullanıcı adı veya şifre hatalı."
    )
    # A refused job: the Turkish sentence, then the API's own detail.
    turkish.fill("#password", "gizli1234")
    turkish.click("#login-submit")
    turkish.wait_for_url(re.compile(r"/new\.html"))
    turkish.fill("#textbox", "x" * 2001)
    expect(turkish.locator("#send-error")).to_contain_text(
        "Kuyruk bunu kabul edemiyor."
    )
    expect(turkish.locator("#send-error")).to_contain_text("API'den gelen ayrıntı:")
    expect(turkish.locator("#send-error")).to_contain_text("İstek no:")
    assert turkish.errors == []
    turkish.context.close()


def test_status_screen_reports_every_part(page):
    page.goto(f"{WEB}/status.html")
    expect(page.locator("h1")).to_have_text("Everything is working.")
    for part in ("API", "Worker", "Database", "AI", "Queue"):
        expect(page.locator("#parts dt", has_text=part)).to_have_count(1)
    expect(page.locator("#parts")).to_contain_text(
        "Demo AI · replays times measured on Gemma 4 E4B"
    )
    expect(page.locator("#parts")).to_contain_text(
        re.compile(r"Working · last heartbeat \d+ seconds? ago")
    )


def screens(page, ticket):
    """Every screen's address (job and results need a ticket)."""
    return [
        "index.html",
        "signup.html",
        "login.html",
        "new.html",
        "jobs.html",
        f"job.html?ticket={ticket}",
        f"results.html?ticket={ticket}",
        "status.html",
        "experiments.html",
    ]


@pytest.mark.parametrize("language", ["en", "tr"])
def test_every_screen_passes_the_accessibility_check(browser, language):
    page = new_page(browser, locale="tr-TR" if language == "tr" else "en-GB")
    sign_up(page)
    ticket = send_job(
        page, ["Fena değil, iş görür.", "aldim begendim"], task="normalise"
    )
    wait_until_finished(page, "Bitti" if language == "tr" else "Done")
    for address in screens(page, ticket):
        page.goto(f"{WEB}/{address}")
        page.wait_for_load_state("networkidle")
        expect(page.locator("html")).to_have_attribute("lang", language)
        check_accessibility(page)
    # The open account menu too.
    page.goto(f"{WEB}/jobs.html")
    page.click("[data-testid=account]")
    expect(page.locator("#account-menu .bar")).to_be_visible()
    check_accessibility(page)
    assert page.errors == []
    page.context.close()


def test_every_screen_fits_a_phone_without_sideways_scrolling(browser):
    page = new_page(browser, width=390)
    sign_up(page)
    ticket = send_job(page, sample_texts()[:3], task="normalise")
    wait_until_finished(page)
    for address in screens(page, ticket):
        page.goto(f"{WEB}/{address}")
        page.wait_for_load_state("networkidle")
        width = page.evaluate("document.documentElement.scrollWidth")
        assert width <= 390, f"{address} is {width} px wide at phone width"
    page.context.close()
