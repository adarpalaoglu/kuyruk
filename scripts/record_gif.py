"""Record the README's GIF of Kuyruk at work (Slice 18b, D136).

The README opens with a short animated picture (a GIF) of the web interface:
the sample reviews are sent, the dots fill in, a crash is simulated, the grey
"another worker took over" notice appears, and the job finishes. A script
makes it, so anyone can make the same GIF again with no recording by hand.

It needs the running setup in demo mode (`docker compose up -d --wait`) and
the browser tests' libraries (Playwright drives the browser, Pillow joins the
pictures into a GIF):

    uv run --group browser python scripts/record_gif.py

How it works. A real job of the sample reviews takes about 3 minutes, far
too long for a GIF. So the script takes a picture of the screen at fixed
points (the start, every few finished texts, the moments around each button
press) and plays them back quickly: about 13 seconds in all, with longer
pauses on the three moments that matter (sending, the notice, the finished
job). The README calls it "sped up".

What stays the same on a rerun: the steps, the crash at the same number of
finished texts, the points where pictures are taken and how long each is
shown, so the story and the length are the same every time. What can differ
slightly: the ticket number, clock times, "time left" and exactly how many
dots are filled in each picture (the worker never runs at exactly the same
speed twice).

Each run signs up a new user ("gif_" plus 6 random letters and digits), so
an earlier run's jobs or used budget never show up.
"""

import io
import json
import os
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).parent.parent
OUT = ROOT / "docs" / "screenshots" / "kuyruk-at-work.gif"
WEB = os.environ.get("KUYRUK_WEB_URL", "http://localhost:8080").rstrip("/")
API = os.environ.get("KUYRUK_API_URL", "http://localhost:8000").rstrip("/")
PASSWORD = "gif-demo-1234"

# The browser window. 800 px wide is about as wide as GitHub shows a README
# picture, so the GIF is shown at its real size and its text stays sharp.
WIDTH = 800
HEIGHT = 720

# The sample reviews that "Use sample reviews" sends, all of them. The GIF's
# timing is planned for exactly TEXTS of them; `check_samples` stops the run
# if the file ever holds a different number.
SAMPLES = ROOT / "web" / "data" / "sample_reviews.json"
TEXTS = 100
CRASH_AT = 20  # "Simulate a crash" is pressed once this many texts are done
STEP = 4  # a picture every 4 finished texts
POINTS = list(range(0, TEXTS, STEP))  # 0, 4, 8, ... 96 finished texts

# How long each picture is shown in the GIF, in milliseconds. A GIF stores
# times in hundredths of a second, so every value is a multiple of 10.
STEP_MS = 120  # one progress picture
HOLDS_MS = {
    "new": 800,  # New job, empty
    "samples_outline": 700,  # "Use sample reviews" outlined, about to be pressed
    "samples_filled": 900,  # the box filled with the reviews, and the cost line
    "send_outline": 700,  # "Send" outlined, about to be pressed
    "crash_outline": 800,  # "Simulate a crash" outlined, about to be pressed
    "crash_pressed": 1000,  # just after the press: the worker has stopped
    "notice": 2500,  # the grey notice: another worker took over
    "done": 2500,  # the finished job
}

MAX_BYTES = 5 * 1024 * 1024  # the README's limit for the GIF: under 5 MB

# A thin outline drawn round a button just before it is pressed, because a
# recording shows no mouse pointer. Orange, so it stands out from the blue.
OUTLINE = "3px solid #E8590C"


def check_samples(path: Path = SAMPLES) -> None:
    """Stop with a clear message unless the sample file holds TEXTS reviews.

    The picture points and the length of the GIF are planned for exactly
    TEXTS texts. With any other number the job would end at a different
    count, so the script would wait for a count that never comes.
    """
    count = len(json.loads(path.read_text(encoding="utf-8")))
    if count != TEXTS:
        sys.exit(
            f"{path.name} has {count} reviews, but the GIF is planned for {TEXTS}. "
            "Change TEXTS (and check the timing plan) in scripts/record_gif.py."
        )


def total_ms() -> int:
    """How long the whole GIF plays, in milliseconds (one play, no repeat)."""
    return sum(HOLDS_MS.values()) + len(POINTS) * STEP_MS


def build_gif(pictures: list[bytes], durations_ms: list[int], path: Path) -> int:
    """Join PNG pictures into a looping GIF; returns the file's size in bytes.

    A GIF can hold only 256 colours per picture. All pictures share one
    palette (the list of colours used), so a colour never flickers between
    pictures. The palette is chosen from every distinct colour of every
    picture, each counted once: counted by how often it appears, the large
    white and grey areas would take every place, and a small orange outline
    or red link would turn grey. No dithering (mixing dots of two colours to
    fake a third): the interface's flat colours look cleaner without it, and
    the file stays smaller.
    """
    from PIL import Image  # only needed here; the timing plan works without it

    if len(pictures) != len(durations_ms):
        raise ValueError("Every picture needs exactly one duration.")
    images = [Image.open(io.BytesIO(p)).convert("RGB") for p in pictures]
    distinct = set()
    for image in images:
        # getcolors gives (count, colour) pairs. It needs to be told the most
        # colours to expect: a picture can't have more than its pixels.
        limit = image.width * image.height
        distinct.update(colour for _, colour in image.getcolors(limit))
    colours = sorted(distinct)  # sorted, so a rerun picks the same palette
    strip = Image.new("RGB", (len(colours), 1))
    strip.putdata(colours)
    palette = strip.quantize(colors=256, method=Image.Quantize.MEDIANCUT)
    frames = [
        image.quantize(palette=palette, dither=Image.Dither.NONE) for image in images
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        path,
        save_all=True,
        append_images=frames[1:],
        duration=durations_ms,
        loop=0,  # play again and again
    )
    return path.stat().st_size


def gif_facts(path: Path) -> tuple[int, int]:
    """How many frames the GIF file holds and how long it plays (ms), read back.

    Pillow stores two pictures in a row that look exactly the same as one
    frame, with their times added, so the file can hold fewer frames than
    the pictures taken. Reading the file itself reports what is really there.
    """
    from PIL import Image, ImageSequence

    with Image.open(path) as gif:
        durations = [frame.info["duration"] for frame in ImageSequence.Iterator(gif)]
    return len(durations), sum(durations)


def sign_up() -> tuple[str, str]:
    """Create a new user for this run and log in; returns (name, login pass)."""
    import httpx

    name = f"gif_{uuid.uuid4().hex[:6]}"
    account = {"username": name, "password": PASSWORD}
    httpx.post(f"{API}/register", json=account, timeout=10).raise_for_status()
    reply = httpx.post(f"{API}/token", data=account, timeout=10)
    reply.raise_for_status()
    return name, reply.json()["access_token"]


def record() -> tuple[list[bytes], list[int]]:
    """Drive the interface through one job and return its pictures and times."""
    import httpx
    from playwright.sync_api import expect, sync_playwright

    # The screenshot script's helper opens a logged-in page in one language.
    sys.path.insert(0, str(Path(__file__).parent))
    from screenshots import page_for

    name, token = sign_up()
    headers = {"Authorization": f"Bearer {token}"}
    pictures: list[bytes] = []
    durations: list[int] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = page_for(browser, token, name, WIDTH, "en")
        page.set_viewport_size({"width": WIDTH, "height": HEIGHT})

        def snap(moment: str | None, ms: int | None = None, view="top") -> None:
            """Take one picture of the window, shown for a moment's time.

            `view` is where the page is scrolled to first. The window is
            shorter than the pages, so: "top" (the page's top), "bottom"
            (New job's box and buttons), or a selector (the job page from
            its progress bar down, where "Simulate a crash" is in sight).
            """
            page.mouse.move(0, 0)  # no hover effect on any button
            page.evaluate(
                """(view) => {
                    if (view === "top") window.scrollTo(0, 0);
                    else if (view === "bottom")
                        window.scrollTo(0, document.body.scrollHeight);
                    else window.scrollTo(0, document.querySelector(view)
                        .getBoundingClientRect().top + window.scrollY - 24);
                }""",
                view,
            )
            page.wait_for_timeout(150)  # let the last change finish drawing
            pictures.append(page.screenshot())
            durations.append(HOLDS_MS[moment] if moment else ms)

        def outline(selector: str, on: bool) -> None:
            """Draw (or remove) the outline round the button about to be pressed."""
            page.locator(selector).evaluate(
                "(el, style) => { el.style.outline = style;"
                " el.style.outlineOffset = style ? '3px' : ''; }",
                OUTLINE if on else "",
            )

        def done() -> int:
            """How many texts the job page shows as finished (its progress bar)."""
            value = page.locator("#progress").get_attribute("aria-valuenow")
            return int(value or 0)

        # New job: press "Use sample reviews", then "Send".
        page.goto(f"{WEB}/new.html")
        page.wait_for_load_state("networkidle")
        snap("new")
        outline("#samples", True)
        snap("samples_outline", view="bottom")
        page.click("#samples")
        outline("#samples", False)
        expect(page.locator("#cost-line")).to_contain_text("tokens")
        snap("samples_filled", view="bottom")
        outline("#send", True)
        snap("send_outline", view="bottom")
        page.click("#send")

        # The job page: a picture every STEP finished texts. If the worker
        # finished several points' worth between two looks, one picture
        # stands for all of them (shown that much longer), so the total time
        # never changes.
        # Up to a minute: another job may still be holding the worker.
        expect(page.locator("#sentence")).to_contain_text("Summarising", timeout=60_000)
        crashed = False
        remaining = list(POINTS)
        deadline = time.monotonic() + 600
        while remaining:
            if time.monotonic() > deadline:
                sys.exit("The job didn't finish within 10 minutes.")
            count = done()  # read once, so every point sees the same count
            reached = [p for p in remaining if count >= p]
            if not reached:
                page.wait_for_timeout(100)
                continue
            remaining = [p for p in remaining if p not in reached]
            snap(None, STEP_MS * len(reached))
            if not crashed and max(reached) >= CRASH_AT:
                crashed = True
                outline("#crash", True)
                snap("crash_outline", view="#progress")
                page.click("#crash")
                outline("#crash", False)
                snap("crash_pressed", view="#progress")
                expect(page.locator("#recovered")).to_be_visible(timeout=60_000)
                snap("notice", view="#progress")

        # Wait until the job has ended, then for the page to show it. Only
        # "done" makes a GIF; "failed" or "cancelled" stop the run at once.
        ticket = page.url.split("ticket=")[1]
        while True:
            reply = httpx.get(f"{API}/jobs/{ticket}", headers=headers)
            if reply.status_code != 200:
                sys.exit(f"The API answered {reply.status_code}: {reply.text}")
            state = reply.json()["state"]
            if state == "done":
                break
            if state in ("failed", "cancelled"):
                sys.exit(f"The job ended {state}, not done. No GIF was written.")
            if time.monotonic() > deadline:
                sys.exit("The job didn't finish within 10 minutes.")
            time.sleep(0.5)
        expect(page.locator("#progress")).to_have_attribute("aria-valuenow", str(TEXTS))
        page.wait_for_timeout(1500)
        snap("done")
        browser.close()
    return pictures, durations


def main() -> None:
    """Record the job, write the GIF and check its length and size."""
    check_samples()
    print(f"Recording one job of {TEXTS} sample reviews (about 3 to 4 minutes)...")
    pictures, durations = record()
    size = build_gif(pictures, durations, OUT)
    frames, played_ms = gif_facts(OUT)
    seconds = played_ms / 1000
    megabytes = size / 1024 / 1024
    print(f"{OUT.relative_to(ROOT)}: {len(pictures)} pictures taken, {frames} frames")
    print(f"  in the file, {seconds:.1f} s, {megabytes:.2f} MB")
    # Every planned picture is shown for its planned time, so the total must
    # be exactly the plan's; anything else means a step was skipped.
    if played_ms != total_ms():
        sys.exit(f"The GIF lasts {seconds} s, not the planned {total_ms() / 1000} s.")
    if size >= MAX_BYTES:
        sys.exit(
            "The GIF is 5 MB or more. Make the window smaller or use fewer points."
        )


if __name__ == "__main__":
    main()
