"""Browser tests for Slice 17b: the interface fixes after first use.

- Sign up and log in are two different pages, linked to each other, and the
  two passwords are compared before anything is sent (D126).
- In demo mode, a running job's page has "Simulate a crash" next to Cancel,
  and it crashes that job (D127).
- A finished job with texts left without a result offers to send them as a
  new job, from the job page and the results page; the old job stays as it
  is (D128).
- Right after sign-up, New job shows a three-step guide that can be hidden
  for good (D129).

Like test_interface.py, these run against `docker compose up` in demo mode
and each test signs up its own user. Where words matter, both languages are
checked.
"""

import json
import re
from pathlib import Path

import httpx
import pytest

from tests.browser.conftest import (
    API,
    PASSWORD,
    WEB,
    api,
    cancel_unfinished_jobs,
    check_accessibility,
    log_in,
    new_page,
    pass_of,
    sample_texts,
    send_job,
    sign_up,
    unique_name,
)

expect = pytest.importorskip("playwright.sync_api").expect

JOB_SECONDS = 360

# The words each test looks for, in both languages.
WORDS = {
    "en": {
        "locale": "en-GB",
        "signup_h1": "Create your account.",
        "login_h1": "Log in.",
        "create": "Create account",
        "log_in": "Log in",
        "signup_title": "Kuyruk – Create your account",
        "login_title": "Kuyruk – Log in",
        "mismatch": "The passwords don't match. Enter the same password twice.",
        "crash": "Simulate a crash",
        "resend": "Send the texts without a result as a new job",
        "never_started": "never started",
        "failed": "failed",
        "from_note": "without a result from job",
        "guide": "Start here",
        "hide": "Hide the guide",
        "cancelled": "Cancelled",
        "done": "Done",
    },
    "tr": {
        "locale": "tr-TR",
        "signup_h1": "Hesabınızı oluşturun.",
        "login_h1": "Giriş yapın.",
        "create": "Hesap oluştur",
        "log_in": "Giriş yap",
        "signup_title": "Kuyruk – Hesap oluştur",
        "login_title": "Kuyruk – Giriş yap",
        "mismatch": "Şifreler eşleşmiyor. Aynı şifreyi iki kez girin.",
        "crash": "Çökme başlat",
        "resend": "Sonucu olmayan metinleri yeni iş olarak gönder",
        "never_started": "hiç başlamadı",
        "failed": "başarısız",
        "from_note": "numaralı işten sonucu olmayan",
        "guide": "Buradan başlayın",
        "hide": "Rehberi gizle",
        "cancelled": "İptal edildi",
        "done": "Bitti",
    },
}


I18N = Path(__file__).parents[2] / "web" / "i18n"


def text_of(lang, key, **slots):
    """A text from the site's own language file, with its {slots} filled in.

    For the explanation lines, so the test checks exactly the wording in the
    files (and that the page shows it) without copying every sentence here.
    """
    value = json.loads((I18N / f"{lang}.json").read_text(encoding="utf-8"))
    for part in key.split("."):
        value = value[part]
    for name, slot in slots.items():
        value = value.replace(f"{{{name}}}", str(slot))
    return value


@pytest.fixture(params=["en", "tr"])
def lang(request):
    """Run the test once in English and once in Turkish."""
    return request.param


@pytest.fixture
def lpage(browser, lang):
    """A desktop page whose browser asks for `lang` (so the site starts in it)."""
    page = new_page(browser, locale=WORDS[lang]["locale"])
    yield page
    cancel_unfinished_jobs(page)
    assert page.errors == [], f"the page's code failed: {page.errors}"
    page.context.close()


def wait_finished(page, word):
    """Wait on the job page until its top line starts with `word`."""
    expect(page.locator("#kicker")).to_have_text(
        re.compile(f"^{word} "), timeout=JOB_SECONDS * 1000
    )


def leftover_lines(page) -> list[str]:
    """The box's lines on New job once a job's leftovers are filled in."""
    expect(page.locator("#from-note")).to_be_visible()
    return page.locator("#textbox").input_value().split("\n")


def mark_failed(page, ticket, positions):
    """Make the results reply say these texts failed, as the AI giving up would.

    Demo jobs never fail a text, so the test changes the API's reply on its
    way to the page (only in this test browser; the database is unchanged).
    """

    def change(route):
        reply = route.fetch()
        items = reply.json()
        for item in items:
            if item["position"] in positions:
                item.update(state="failed", result=None, error_message="AI unavailable")
        route.fulfill(response=reply, body=json.dumps(items))

    page.route(f"**/jobs/{ticket}/results", change)


# --- D126: two account pages -------------------------------------------------


def test_sign_up_and_log_in_are_two_different_pages(lpage, lang):
    words = WORDS[lang]
    lpage.goto(f"{WEB}/index.html")
    # "Try it yourself" opens sign-up; the header's "Log in" opens log-in.
    expect(lpage.locator("#try-link")).to_have_attribute("href", "signup.html")
    expect(lpage.locator(".nav-login")).to_have_attribute("href", "login.html")
    lpage.click("#try-link")
    lpage.wait_for_url(re.compile(r"/signup\.html$"))
    expect(lpage.locator("h1")).to_have_text(words["signup_h1"])
    expect(lpage).to_have_title(words["signup_title"])
    expect(lpage.locator("form input")).to_have_count(3)
    expect(lpage.locator("#signup-submit")).to_have_text(words["create"])
    lpage.click("#switch-link")
    lpage.wait_for_url(re.compile(r"/login\.html$"))
    expect(lpage.locator("h1")).to_have_text(words["login_h1"])
    expect(lpage).to_have_title(words["login_title"])
    expect(lpage.locator("form input")).to_have_count(2)
    expect(lpage.locator("#confirm")).to_have_count(0)
    expect(lpage.locator("#login-submit")).to_have_text(words["log_in"])
    lpage.click("#switch-link")
    lpage.wait_for_url(re.compile(r"/signup\.html$"))


def test_the_come_back_page_survives_switching_pages(page):
    # Sent to log in from My jobs, then switching to sign-up: after
    # creating the account, My jobs opens.
    page.goto(f"{WEB}/jobs.html")
    page.wait_for_url(re.compile(r"/login\.html\?next=jobs\.html"))
    page.click("#switch-link")
    page.wait_for_url(re.compile(r"/signup\.html\?next=jobs\.html"))
    page.fill("#username", unique_name())
    page.fill("#password", PASSWORD)
    page.fill("#confirm", PASSWORD)
    page.click("#signup-submit")
    page.wait_for_url(re.compile(r"/jobs\.html$"))


def test_mismatched_passwords_are_caught_before_sending(lpage, lang):
    sent = []
    lpage.route("**/register", lambda route: (sent.append(1), route.continue_()))
    lpage.goto(f"{WEB}/signup.html")
    lpage.fill("#username", unique_name())
    lpage.fill("#password", PASSWORD)
    lpage.fill("#confirm", PASSWORD + "x")
    lpage.click("#signup-submit")
    expect(lpage.locator("#signup-error")).to_have_text(WORDS[lang]["mismatch"])
    expect(lpage.locator("#confirm")).to_have_attribute("aria-invalid", "true")
    expect(lpage.locator("#confirm")).to_be_focused()
    assert sent == []
    # Fixed: the account is created and New job opens.
    lpage.fill("#confirm", PASSWORD)
    lpage.click("#signup-submit")
    lpage.wait_for_url(re.compile(r"/new\.html$"))
    assert sent == [1]


# --- D127: the crash button on the job page -----------------------------------


def test_crash_from_the_job_page_crashes_this_job_and_recovers(page):
    sign_up(page)
    ticket = send_job(page, sample_texts())
    crash = page.locator("#crash")
    # Shown once the job runs, a pill with its line; Cancel with its own.
    expect(crash).to_be_visible(timeout=30_000)
    expect(crash).to_have_text(WORDS["en"]["crash"])
    expect(crash).to_have_class(re.compile("btn-outline"))
    expect(page.locator("#crash-hint")).to_have_text(text_of("en", "job.crash_hint"))
    expect(page.locator("#cancel")).to_be_visible()
    expect(page.locator("#cancel-hint")).to_have_text(text_of("en", "job.cancel_hint"))
    with page.expect_request("**/demo/crash-worker*") as asked:
        crash.click()
    # The button names this job, so no other running job can be hit.
    assert f"ticket={ticket}" in asked.value.url
    expect(page.locator("#crash-status")).to_contain_text(
        re.compile("(Asking the worker to crash|The worker has stopped)")
    )
    expect(page.locator("#recovered")).to_contain_text(
        re.compile(r"Another worker took over"), timeout=60_000
    )
    expect(page.locator("#recovered")).to_contain_text("Nothing was lost.")
    expect(page.locator("#crash-status")).to_be_hidden()
    # A second press that fails (here: the API says 409) keeps the earlier,
    # real takeover's notice (review fix).
    page.route(
        "**/demo/crash-worker*",
        lambda route: route.fulfill(
            status=409,
            json={"error": {"code": "no_running_job", "message": "x"}},
        ),
    )
    crash.click()
    expect(page.locator("#crash-status")).to_contain_text("Start a job first")
    expect(page.locator("#recovered")).to_contain_text("Nothing was lost.")
    # Cancel ends the job for good; the crash button goes with it.
    page.click("#cancel")
    wait_finished(page, "Cancelled")
    expect(crash).to_be_hidden()
    expect(page.locator("#crash-hint")).to_be_hidden()
    expect(page.locator("#cancel-hint")).to_be_hidden()


def test_a_queued_job_has_no_crash_button_in_either_language(lpage, lang):
    sign_up(lpage)
    first = send_job(lpage, sample_texts())
    expect(lpage.locator("#crash")).to_have_text(WORDS[lang]["crash"], timeout=30_000)
    expect(lpage.locator("#crash-hint")).to_have_text(text_of(lang, "job.crash_hint"))
    expect(lpage.locator("#cancel-hint")).to_have_text(text_of(lang, "job.cancel_hint"))
    # One worker: the second job waits in the queue, with nothing to crash.
    second = send_job(lpage, sample_texts()[:3])
    assert api(lpage, "GET", f"/jobs/{second}").json()["state"] == "queued"
    expect(lpage.locator("#cancel")).to_be_visible()
    expect(lpage.locator("#crash")).to_be_hidden()
    for ticket in (second, first):
        api(lpage, "POST", f"/jobs/{ticket}/cancel")


def test_the_info_button_explains_cancel_and_crash(lpage, lang):
    sign_up(lpage)
    send_job(lpage, sample_texts())
    info = lpage.locator("#crash-info-button")
    panel = lpage.locator("#crash-info")
    expect(info).to_be_visible(timeout=30_000)
    # A real button with a name; closed at first.
    expect(info).to_have_attribute("aria-label", text_of(lang, "job.info_label"))
    expect(info).to_have_attribute("aria-expanded", "false")
    expect(info).to_have_attribute("aria-controls", "crash-info")
    expect(panel).to_be_hidden()
    # Opened from the keyboard: the sentences show in place.
    info.focus()
    lpage.keyboard.press("Enter")
    expect(info).to_have_attribute("aria-expanded", "true")
    expect(panel).to_have_text(text_of(lang, "job.info_text"))
    check_accessibility(lpage)
    lpage.keyboard.press("Space")
    expect(panel).to_be_hidden()
    expect(info).to_have_attribute("aria-expanded", "false")


def test_a_crash_answered_after_the_job_ended_says_too_late(page):
    """The job ends while the crash request is on its way (review fix)."""
    sign_up(page)
    ticket = send_job(page, sample_texts()[:12])
    expect(page.locator("#crash")).to_be_visible(timeout=30_000)
    held = []
    # Hold the crash request: answer it only after the job has ended.
    page.route("**/demo/crash-worker*", lambda route: held.append(route))
    page.click("#crash")
    expect(page.locator("#crash-status")).to_contain_text("Asking the worker")
    api(page, "POST", f"/jobs/{ticket}/cancel")
    wait_finished(page, "Cancelled")
    held[0].fulfill(status=202, json={"ticket": ticket, "message": "x"})
    page.wait_for_timeout(500)
    expect(page.locator("#crash-status")).to_have_text(
        text_of("en", "experiments.crash_too_late")
    )


# --- D128: send the texts without a result as a new job ------------------------


def test_a_cancelled_job_sends_its_remaining_texts_as_a_new_job(lpage, lang):
    words = WORDS[lang]
    sign_up(lpage)
    texts = sample_texts()[:12]
    old = send_job(lpage, texts)
    lpage.click("#cancel")
    wait_finished(lpage, words["cancelled"])
    # The button shows once the worker has let go (D130); then nothing changes.
    expect(lpage.locator("#resend")).to_be_visible(timeout=60_000)
    before = api(lpage, "GET", f"/jobs/{old}").json()
    assert before["worker_busy"] is False
    waiting = [
        item["original_text"]
        for item in api(lpage, "GET", f"/jobs/{old}/results").json()
        if item["state"] == "waiting"
    ]
    assert waiting, "the job finished before Cancel; nothing left to resend"
    # The job page offers it, with the counts.
    expect(lpage.locator("#resend-link")).to_have_text(words["resend"])
    expect(lpage.locator("#resend-counts")).to_have_text(
        f"{len(waiting)} {words['never_started']}"
    )
    expect(lpage.locator("#resend-hint")).to_have_text(text_of(lang, "job.resend_hint"))
    # So does the results page.
    lpage.goto(f"{WEB}/results.html?ticket={old}")
    expect(lpage.locator("#resend-link")).to_have_text(words["resend"])
    expect(lpage.locator("#resend-link")).to_have_attribute(
        "href", f"new.html?from={old}"
    )
    lpage.click("#resend-link")
    # New job: exactly those texts, the same task, and the cost first.
    lpage.wait_for_url(re.compile(rf"/new\.html\?from={old}$"))
    expect(lpage.locator("#from-note")).to_contain_text(words["from_note"])
    expect(lpage.locator("#from-note")).to_contain_text(old[:7])
    expect(lpage.locator("input[name=task][value=llm]")).to_be_checked()
    assert leftover_lines(lpage) == waiting
    expect(lpage.locator("#cost-line")).to_have_text(
        re.compile(rf"^{len(waiting)} \S+ · [\d.,]+ token")
    )
    lpage.click("#send")
    lpage.wait_for_url(re.compile(r"/job\.html\?ticket="))
    new = lpage.url.split("ticket=")[1]
    assert new != old
    sent = [
        i["original_text"] for i in api(lpage, "GET", f"/jobs/{new}/results").json()
    ]
    assert sent == waiting
    # The old job stays exactly as it was.
    assert api(lpage, "GET", f"/jobs/{old}").json() == before
    api(lpage, "POST", f"/jobs/{new}/cancel")


def test_failed_texts_are_counted_and_included(page):
    sign_up(page)
    old = send_job(page, sample_texts()[:12])
    page.click("#cancel")
    wait_finished(page, "Cancelled")
    expect(page.locator("#resend")).to_be_visible(timeout=60_000)
    items = api(page, "GET", f"/jobs/{old}/results").json()
    # Pretend text 1 failed (demo jobs never fail a text); every other text
    # keeps its real state.
    mark_failed(page, old, {1})
    waiting = [i for i in items if i["state"] == "waiting" and i["position"] != 1]
    assert waiting
    page.reload()
    expect(page.locator("#resend-counts")).to_have_text(
        f"{len(waiting)} never started · 1 failed"
    )
    page.click("#resend-link")
    page.wait_for_url(re.compile(r"/new\.html\?from="))
    # Text 1 and the waiting ones, in their original order.
    expected = [items[0]["original_text"]] + [i["original_text"] for i in waiting]
    assert leftover_lines(page) == expected


def test_a_done_job_offers_only_its_failed_texts(page):
    sign_up(page)
    texts = sample_texts()[:3]
    old = send_job(page, texts, task="count_tokens")
    wait_finished(page, "Done")
    # Every text has a result: nothing to resend.
    expect(page.locator("#resend")).to_be_hidden()
    page.goto(f"{WEB}/results.html?ticket={old}")
    expect(page.locator("#resend")).to_be_hidden()
    # With text 2 failed, only text 2 goes into the new job.
    mark_failed(page, old, {2})
    page.goto(f"{WEB}/job.html?ticket={old}")
    expect(page.locator("#resend-counts")).to_have_text("1 failed")
    page.goto(f"{WEB}/results.html?ticket={old}")
    expect(page.locator("#resend-counts")).to_have_text("1 failed")
    page.click("#resend-link")
    page.wait_for_url(re.compile(r"/new\.html\?from="))
    expect(page.locator("input[name=task][value=count_tokens]")).to_be_checked()
    assert leftover_lines(page) == [texts[1]]


def test_no_resend_while_the_cancelled_texts_are_still_with_the_ai(page):
    """D130: pressing in the in-flight seconds can't send a text twice."""
    sign_up(page)
    old = send_job(page, sample_texts())
    expect(page.locator("#crash")).to_be_visible(timeout=30_000)
    # Watch the button: record whether it showed while the worker held the job.
    page.evaluate(
        """() => {
          window.shownWhileBusy = false;
          const box = document.getElementById('resend');
          new MutationObserver(async () => {
            if (box.hidden) return;
            const reply = await fetch(KUYRUK_CONFIG.apiUrl + '/jobs/"""
        + old
        + """', {
              headers: {
                Authorization: 'Bearer ' + sessionStorage.getItem('kuyruk.pass'),
              }});
            if ((await reply.json()).worker_busy) window.shownWhileBusy = true;
          }).observe(box, {attributes: true});
        }"""
    )
    page.click("#cancel")
    wait_finished(page, "Cancelled")
    # Right after the cancel the worker still holds the job (texts in flight).
    assert api(page, "GET", f"/jobs/{old}").json()["worker_busy"] is True
    expect(page.locator("#resend")).to_be_hidden()
    # New job opened in those seconds waits, then gets only the final leftovers.
    # A second tab needs the login too (each tab keeps its own, D116).
    other = page.context.new_page()
    other.add_init_script(
        f"sessionStorage.setItem('kuyruk.pass', {json.dumps(pass_of(page))});"
        "sessionStorage.setItem('kuyruk.user', 'tester');"
    )
    other.goto(f"{WEB}/new.html?from={old}")
    expect(page.locator("#resend")).to_be_visible(timeout=60_000)
    final = [
        i["original_text"]
        for i in api(page, "GET", f"/jobs/{old}/results").json()
        if i["state"] != "ok"
    ]
    assert leftover_lines(other) == final
    assert page.evaluate("window.shownWhileBusy") is False
    other.close()


def test_a_done_job_never_flashes_the_resend_button(page):
    """While a job ends, the button must not show on old texts (review fix)."""
    sign_up(page)
    send_job(page, sample_texts()[:12])
    page.evaluate(
        """() => {
          window.resendShown = false;
          const box = document.getElementById('resend');
          new MutationObserver(() => { if (!box.hidden) window.resendShown = true; })
            .observe(box, {attributes: true});
        }"""
    )
    wait_finished(page, "Done")
    page.wait_for_timeout(2000)
    assert page.evaluate("window.resendShown") is False


def test_sign_up_retry_after_a_failed_log_in_only_logs_in(page):
    """Account made, log-in failed: pressing again doesn't say "taken"."""
    registers = []
    tokens = []
    page.route("**/register", lambda route: (registers.append(1), route.continue_()))

    def fail_first(route):
        tokens.append(1)
        if len(tokens) == 1:
            route.abort()
        else:
            route.continue_()

    page.route("**/token", fail_first)
    page.goto(f"{WEB}/signup.html")
    page.fill("#username", unique_name())
    page.fill("#password", PASSWORD)
    page.fill("#confirm", PASSWORD)
    page.click("#signup-submit")
    expect(page.locator("#signup-error")).to_contain_text("Can't reach")
    page.click("#signup-submit")
    page.wait_for_url(re.compile(r"/new\.html$"))
    assert registers == [1]
    assert len(tokens) == 2


# --- Explanation lines under the other important actions --------------------


def test_every_important_action_has_its_line(lpage, lang):
    sign_up(lpage)
    numbers = json.loads(
        (Path(__file__).parents[2] / "web" / "data" / "findings.json").read_text()
    )
    lpage.goto(f"{WEB}/new.html")
    expect(lpage.locator("#send-hint")).to_have_text(text_of(lang, "new.send_hint"))
    expect(lpage.locator("#send")).to_have_attribute("aria-describedby", "send-hint")
    expect(lpage.locator("#samples-hint")).to_have_text(
        text_of(
            lang,
            "new.samples_hint",
            samples_count=numbers["samples_count"],
            samples_demo_min=numbers["samples_demo_min"],
        )
    )
    ticket = send_job(lpage, ["Kargo hızlı geldi."], task="count_tokens")
    wait_finished(lpage, WORDS[lang]["done"])
    lpage.goto(f"{WEB}/results.html?ticket={ticket}")
    expect(lpage.locator("#download-hint")).to_have_text(
        text_of(lang, "results.download_hint")
    )
    lpage.click("[data-testid=account]")
    expect(lpage.locator("#account-menu .menu-hint")).to_have_text(
        text_of(lang, "menu.budget_hint")
    )


# --- D129: the first-time guide ---------------------------------------------


def test_the_guide_shows_after_sign_up_and_stays_hidden(lpage, lang):
    words = WORDS[lang]
    sign_up(lpage, keep_guide=True)
    guide = lpage.locator("#guide")
    expect(guide).to_be_visible()
    expect(lpage.locator("#guide-title")).to_have_text(words["guide"])
    # Three steps in demo mode, each with its link.
    expect(lpage.locator("#guide .guide-steps li:visible")).to_have_count(3)
    expect(lpage.locator("#guide li:nth-child(2) a")).to_have_attribute(
        "href", "jobs.html"
    )
    expect(lpage.locator("#guide li:nth-child(3) a")).to_have_attribute(
        "href", "experiments.html#crash"
    )
    # Step 1 fills in the sample reviews.
    lpage.click("#guide-samples")
    expect(lpage.locator("#cost-line")).to_have_text(re.compile(r"^100 "))
    # Hidden, and still hidden after a reload.
    expect(lpage.locator("#guide-hide")).to_have_text(words["hide"])
    lpage.click("#guide-hide")
    expect(guide).to_be_hidden()
    lpage.reload()
    lpage.wait_for_load_state("networkidle")
    expect(guide).to_be_hidden()


def test_the_guide_does_not_show_after_a_plain_log_in(page):
    name = unique_name()
    httpx.post(
        f"{API}/register", json={"username": name, "password": PASSWORD}, timeout=10
    )
    log_in(page, name)
    page.wait_for_load_state("networkidle")
    expect(page.locator("#guide")).to_be_hidden()


# --- Accessibility and phone width for the new states -------------------------


def new_states(page):
    """Visit each new state of the screens; yields once on each."""
    page.goto(f"{WEB}/signup.html")
    yield "signup"
    # The guide and, after a cancel, the leftovers on New job.
    name = sign_up(page, keep_guide=True)
    yield "new with guide"
    ticket = send_job(page, sample_texts())
    expect(page.locator("#crash")).to_be_visible(timeout=30_000)
    yield "job with crash button"
    page.click("#cancel")
    expect(page.locator("#resend")).to_be_visible(timeout=JOB_SECONDS * 1000)
    yield "job with resend"
    page.goto(f"{WEB}/results.html?ticket={ticket}")
    expect(page.locator("#resend")).to_be_visible()
    yield "results with resend"
    page.goto(f"{WEB}/new.html?from={ticket}")
    expect(page.locator("#from-note")).to_be_visible()
    expect(page.locator("#guide")).to_be_visible()
    yield "new with leftovers"
    page.evaluate(f"localStorage.setItem('kuyruk.guide.{name}', 'hidden')")


def test_the_new_states_pass_the_accessibility_check(lpage, lang):
    for _state in new_states(lpage):
        # Not "network idle": the job page keeps its live stream open.
        expect(lpage.locator("html")).not_to_have_class("loading")
        expect(lpage.locator("html")).to_have_attribute("lang", lang)
        check_accessibility(lpage)


def test_the_new_states_fit_a_phone(browser):
    page = new_page(browser, width=390)
    try:
        for state in new_states(page):
            expect(page.locator("html")).not_to_have_class("loading")
            width = page.evaluate("document.documentElement.scrollWidth")
            assert width <= 390, f"{state} is {width} px wide at phone width"
    finally:
        cancel_unfinished_jobs(page)
    assert page.errors == []
    page.context.close()
