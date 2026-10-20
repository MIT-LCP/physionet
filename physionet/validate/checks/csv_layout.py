"""Shared CSV layout information for validation checks."""

import csv
from dataclasses import dataclass
from typing import Optional, TextIO, Tuple


@dataclass(frozen=True)
class CsvLayout:
    """Describe which CSV rows are metadata and how data columns are named.

    With no explicit columns, the first row is treated as a header. Explicit
    columns describe a headerless file; metadata_rows precede its data rows.
    A file with no data rows gets a warning unless no_data_message is set, in
    which case that message is reported as information.
    """

    columns: Optional[Tuple[str, ...]] = None
    metadata_rows: int = 0
    allow_empty: bool = False
    empty_message: Optional[str] = None
    no_data_message: Optional[str] = None
    # Columns whose Unix timestamps are not reported as phone numbers.
    unix_timestamp_columns: Tuple[str, ...] = ()

    def dict_reader(self, stream: TextIO) -> csv.DictReader:
        """Read data rows under the layout's column names."""
        reader = csv.DictReader(stream, fieldnames=self.columns)
        for _ in range(self.metadata_rows):
            next(reader, None)
        return reader


def is_unix_timestamp(value: str) -> bool:
    """Recognize nine- or ten-digit Unix seconds, including shifted dates."""
    whole, separator, fraction = value.strip().partition(".")
    if len(whole) not in (9, 10) or not whole.isdigit() or whole[0] == "0":
        return False
    if separator and (not fraction or not fraction.isdigit()):
        return False
    return True
