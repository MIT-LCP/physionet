"""Recognize the headerless CSV layout exported by Empatica E4 Connect."""

import csv
from pathlib import Path
from typing import Optional, Tuple

from physionet.validate.checks.csv_layout import CsvLayout, is_unix_timestamp


# Keyed by lowercase file name; E4 Connect writes upper-case names, but
# re-exported or hand-tidied datasets sometimes lowercase them.
_SENSOR_LAYOUTS = {
    "acc.csv": (("acc_x", "acc_y", "acc_z"), 32),
    "bvp.csv": (("bvp",), 64),
    "eda.csv": (("eda",), 4),
    "hr.csv": (("hr",), 1),
    "temp.csv": (("temp",), 4),
}

_IBI_COLUMNS = ("time_offset", "ibi")


def _read_first_rows(file_path: Path, count: int) -> Optional[list]:
    """Return the first rows of a CSV file, or None if it cannot supply them."""
    try:
        with file_path.open(newline="", encoding="utf-8") as stream:
            reader = csv.reader(stream)
            return [next(reader) for _ in range(count)]
    except (OSError, UnicodeError, StopIteration, csv.Error):
        return None


def is_e4_sensor_file(file_path: Path) -> bool:
    """Match a sensor's timestamp and sample-rate rows, without reading its data."""
    layout = _SENSOR_LAYOUTS.get(file_path.name.lower())
    if not layout or not file_path.is_file():
        return False

    rows = _read_first_rows(file_path, 2)
    if rows is None:
        return False
    timestamps, rates = rows
    columns, sample_rate = layout
    channels = len(columns)
    try:
        return (
            len(timestamps) == channels
            and len(rates) == channels
            and all(is_unix_timestamp(value) for value in timestamps)
            and all(float(value) == sample_rate for value in rates)
        )
    except ValueError:
        return False


def e4_sensor_columns(file_path: Path) -> Optional[Tuple[str, ...]]:
    """Return positional channel names for a recognized E4 sensor file."""
    if is_e4_sensor_file(file_path):
        return _SENSOR_LAYOUTS[file_path.name.lower()][0]
    return None


def is_e4_ibi_file(file_path: Path) -> bool:
    """Match IBI.csv, whose first row is the start time followed by 'IBI'."""
    if file_path.name.lower() != "ibi.csv" or not file_path.is_file():
        return False
    rows = _read_first_rows(file_path, 1)
    if rows is None:
        return False
    first = rows[0]
    return len(first) == 2 and is_unix_timestamp(first[0]) and first[1].strip() == "IBI"


def is_e4_tags_file(file_path: Path) -> bool:
    """Identify event tags only when a neighboring E4 sensor or IBI file confirms the format."""
    if file_path.name.lower() != "tags.csv":
        return False
    try:
        siblings = list(file_path.parent.iterdir())
    except OSError:
        return False
    return any(is_e4_sensor_file(sibling) or is_e4_ibi_file(sibling) for sibling in siblings)


def get_e4_csv_layout(file_path: Path) -> Optional[CsvLayout]:
    """Describe recognized E4 sensor, IBI, and event-tag files for CSV checks."""
    columns = e4_sensor_columns(file_path)
    if columns is not None:
        return CsvLayout(columns=columns, metadata_rows=2)
    if is_e4_ibi_file(file_path):
        return CsvLayout(
            columns=_IBI_COLUMNS,
            metadata_rows=1,
            no_data_message="No Empatica E4 inter-beat intervals detected (IBI.csv has only its start-time row)",
        )
    if is_e4_tags_file(file_path):
        return CsvLayout(
            columns=("event_time",),
            allow_empty=True,
            empty_message="No Empatica E4 event markers recorded (empty tags.csv)",
            unix_timestamp_columns=("event_time",),
        )
    return None
