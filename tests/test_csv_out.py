"""Tests for the experiments' shared CSV writer (`bench/csv_out.py`)."""

from bench.csv_out import CsvOut


def test_rows_are_saved_at_once_and_the_file_appears_only_at_the_end(tmp_path):
    path = tmp_path / "x" / "a.csv"
    out = CsvOut(path, ["a", "b"])
    out.write({"a": 1, "b": 2})
    # Saved to the side file straight away; the real file isn't there yet.
    assert out.partial.read_text().splitlines() == ["a,b", "1,2"]
    assert not path.exists()
    out.close()
    assert path.read_text().splitlines() == ["a,b", "1,2"]
    assert not out.partial.exists()


def test_a_run_that_stops_leaves_the_old_results_untouched(tmp_path):
    path = tmp_path / "a.csv"
    path.write_text("a\nold\n")
    out = CsvOut(path, ["a"])
    out.write({"a": "new"})
    # The script stops here, without close().
    assert path.read_text() == "a\nold\n"
