"""Shared CSV layout information for validation checks."""

import csv
from dataclasses import dataclass
from typing import Optional, TextIO, Tuple


@dataclass(frozen=True)
class CsvLayout:
    """Describe which CSV rows are metadata and how data columns are named.

    With no explicit columns, the first row is treated as a header. Explicit
    columns describe a headerless file; metadata_rows precede its data rows.
    """

    columns: Optional[Tuple[str, ...]] = None
    metadata_rows: int = 0
    allow_empty: bool = False
    empty_message: Optional[str] = None
    ignore_unix_timestamps_as_phone_numbers: bool = False

    def dict_reader(self, stream: TextIO) -> csv.DictReader:
        """Read data rows under the layout's column names."""
        reader = csv.DictReader(stream, fieldnames=self.columns)
        for _ in range(self.metadata_rows):
            next(reader, None)
        return reader


def is_unix_timestamp(value: str) -> bool:
    """Recognize ten-digit Unix seconds, optionally with a fractional part."""
    whole, separator, fraction = value.strip().partition(".")
    if len(whole) != 10 or not whole.isdigit():
        return False
    if separator and (not fraction or not fraction.isdigit()):
        return False
    return 946684800 <= int(whole) < 4102444800
