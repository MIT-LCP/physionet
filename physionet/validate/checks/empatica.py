"""Recognize the headerless CSV layout exported by Empatica E4 Connect."""

import csv
from pathlib import Path
from typing import Optional, Tuple

from physionet.validate.checks.csv_layout import CsvLayout, is_unix_timestamp


_SENSOR_LAYOUTS = {
    "ACC.csv": (("acc_x", "acc_y", "acc_z"), 32),
    "BVP.csv": (("bvp",), 64),
    "EDA.csv": (("eda",), 4),
    "TEMP.csv": (("temp",), 4),
}


def is_e4_sensor_file(file_path: Path) -> bool:
    """Match a sensor's timestamp and sample-rate rows, without reading its data."""
    layout = _SENSOR_LAYOUTS.get(file_path.name)
    if not layout or not file_path.is_file():
        return False

    columns, sample_rate = layout
    channels = len(columns)
    try:
        with file_path.open(newline="", encoding="utf-8") as stream:
            reader = csv.reader(stream)
            timestamps = next(reader)
            rates = next(reader)
    except (OSError, UnicodeError, StopIteration, csv.Error):
        return False

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
        return _SENSOR_LAYOUTS[file_path.name][0]
    return None


def is_e4_tags_file(file_path: Path) -> bool:
    """Identify event tags only when a neighboring E4 sensor confirms the format."""
    return file_path.name.lower() == "tags.csv" and any(
        is_e4_sensor_file(file_path.parent / name) for name in _SENSOR_LAYOUTS
    )


def get_e4_csv_layout(file_path: Path) -> Optional[CsvLayout]:
    """Describe recognized E4 sensor and event-tag files for CSV checks."""
    columns = e4_sensor_columns(file_path)
    if columns is not None:
        return CsvLayout(columns=columns, metadata_rows=2)
    if is_e4_tags_file(file_path):
        return CsvLayout(
            columns=("event_time",),
            allow_empty=True,
            empty_message="No Empatica E4 event markers recorded (empty tags.csv)",
            ignore_unix_timestamps_as_phone_numbers=True,
        )
    return None
