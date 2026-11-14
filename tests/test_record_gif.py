"""Tests for the README's GIF script, `scripts/record_gif.py` (Slice 18b, D136).

The recording itself needs the running setup and a real browser, so it is
not tested here (running the script is its check: it stops if the GIF is
too big or doesn't last its planned time). These tests cover the two parts
that work without a browser: the timing plan (how long the GIF plays, and
where it pauses) and joining pictures into a GIF.

Joining needs Pillow, an image library installed only with the browser
group, so that test is skipped without it, like the browser tests.
"""

import io
import sys
from pathlib import Path

import pytest

# The script lives in scripts/, which is not a package: put that folder on
# Python's search path so it can be imported like a module.
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import record_gif  # noqa: E402  (must come after the path change above)


def test_the_gif_lasts_10_to_15_seconds():
    """The plan adds up to the length Adar asked for."""
    assert 10_000 <= record_gif.total_ms() <= 15_000


def test_the_key_moments_are_held_longer_than_progress():
    """The notice and the finished job stay on screen much longer than a step."""
    for moment in ("notice", "done"):
        assert record_gif.HOLDS_MS[moment] >= 2_000
        assert record_gif.HOLDS_MS[moment] > 10 * record_gif.STEP_MS


def test_the_crash_comes_at_a_progress_point_inside_the_job():
    """The crash is pressed at one of the picture points, well before the end."""
    assert record_gif.CRASH_AT in record_gif.POINTS
    assert 0 < record_gif.CRASH_AT < record_gif.TEXTS // 2


def test_every_time_is_whole_hundredths_of_a_second():
    """A GIF stores times in hundredths of a second, so nothing gets rounded."""
    times = [*record_gif.HOLDS_MS.values(), record_gif.STEP_MS]
    assert all(ms % 10 == 0 for ms in times)


def png(colour: str) -> bytes:
    """A small one-colour picture, as PNG bytes (like a browser screenshot)."""
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), colour).save(buffer, format="PNG")
    return buffer.getvalue()


def test_build_gif_keeps_every_picture_and_its_time(tmp_path):
    """Three different pictures give a looping GIF of three, with their times."""
    image = pytest.importorskip("PIL.Image")
    path = tmp_path / "out.gif"
    size = record_gif.build_gif(
        [png("white"), png("blue"), png("orange")], [800, 120, 2500], path
    )
    assert size == path.stat().st_size > 0
    gif = image.open(path)
    durations = []
    for frame in range(gif.n_frames):
        gif.seek(frame)
        durations.append(gif.info["duration"])
    assert durations == [800, 120, 2500]
    assert gif.info["loop"] == 0  # plays again and again
    assert gif.size == (40, 30)
    assert record_gif.gif_facts(path) == (3, 3420)  # read back from the file


def test_identical_pictures_in_a_row_become_one_frame(tmp_path):
    """Pillow merges two same pictures; gif_facts reports the real frame count."""
    pytest.importorskip("PIL.Image")
    path = tmp_path / "out.gif"
    record_gif.build_gif(
        [png("white"), png("white"), png("blue")], [100, 200, 300], path
    )
    assert record_gif.gif_facts(path) == (2, 600)  # the time is kept


def test_the_sample_file_has_the_planned_number_of_texts():
    """The real sample file matches the plan (the script checks this first)."""
    record_gif.check_samples()  # stops the test with SystemExit if not


def test_a_sample_file_of_another_size_stops_the_run(tmp_path):
    """A file with a different number of reviews stops the run with a message."""
    path = tmp_path / "samples.json"
    path.write_text('["one", "two"]', encoding="utf-8")
    with pytest.raises(SystemExit, match="has 2 reviews"):
        record_gif.check_samples(path)


def test_build_gif_refuses_a_picture_without_a_time(tmp_path):
    """Pictures and times must pair up exactly."""
    pytest.importorskip("PIL.Image")
    with pytest.raises(ValueError):
        record_gif.build_gif([png("white"), png("blue")], [800], tmp_path / "x.gif")


def test_a_small_colour_in_one_picture_keeps_its_colour(tmp_path):
    """A small orange mark in one picture stays orange, not the nearest grey.

    The first GIF made in Slice 18b lost its orange outline and red link:
    the palette came from only some pictures and favoured big areas. Here
    every picture is full of grey shades (250 of a GIF's 256 colours),
    and one picture has a tiny orange square.
    """
    image = pytest.importorskip("PIL.Image")

    def picture(orange: bool) -> bytes:
        """A picture with a strip of 250 grey shades, plus the orange mark if asked."""
        canvas = image.new("RGB", (60, 50), "white")
        greys = [(v, v, min(v + 3, 255)) for v in range(0, 250, 1)]
        for x in range(60):
            for y in range(5):
                canvas.putpixel((x, y), greys[(x * 5 + y) % len(greys)])
        if orange:
            for x in range(30, 33):
                for y in range(30, 33):
                    canvas.putpixel((x, y), (232, 89, 12))
        buffer = io.BytesIO()
        canvas.save(buffer, format="PNG")
        return buffer.getvalue()

    pictures = [picture(False), picture(True), *[picture(False)] * 3]
    path = tmp_path / "out.gif"
    record_gif.build_gif(pictures, [100] * 5, path)
    gif = image.open(path)
    gif.seek(1)
    r, g, b = gif.convert("RGB").getpixel((31, 31))
    assert r > 180 and g < 130 and b < 80  # still clearly orange
