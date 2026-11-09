"""A CSV file written row by row, shared by the experiment scripts (E3, E4).

Rows go first into a side file next to the real one (`<name>.partial`), saved
to disk after every row, so nothing measured is lost if a run stops. Only
when the script finishes (`close`) does the side file replace the real one.
So a run that stops halfway, or is started by mistake, never wipes or
half-overwrites results that are already there.
"""

import csv
from pathlib import Path


class CsvOut:
    """A CSV file written row by row, that replaces the real file only at the end."""

    def __init__(self, path: Path, columns: list[str]) -> None:
        """Start the side file with its header line."""
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.partial = path.with_name(path.name + ".partial")
        self.file = self.partial.open("w", newline="")
        self.writer = csv.DictWriter(self.file, fieldnames=columns)
        self.writer.writeheader()

    def write(self, row: dict) -> None:
        """Add one row and save it to disk at once."""
        self.writer.writerow(row)
        self.file.flush()

    def close(self) -> None:
        """Close the side file and put it in place of the real one."""
        self.file.close()
        self.partial.replace(self.path)
