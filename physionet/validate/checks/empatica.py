"""Recognize the headerless CSV layout exported by Empatica E4 Connect."""

import csv
from pathlib import Path


_SENSOR_LAYOUTS = {
    "ACC.csv": (("acc_x", "acc_y", "acc_z"), 32),
    "BVP.csv": (("bvp",), 64),
    "EDA.csv": (("eda",), 4),
    "TEMP.csv": (("temp",), 4),
}


def is_unix_timestamp(value: str) -> bool:
    """Recognize ten-digit Unix seconds, optionally with a fractional part."""
    whole, separator, fraction = value.strip().partition(".")
    if len(whole) != 10 or not whole.isdigit():
        return False
    if separator and (not fraction or not fraction.isdigit()):
        return False
    return 946684800 <= int(whole) < 4102444800


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


def e4_sensor_columns(file_path: Path):
    """Return positional channel names for a recognized E4 sensor file."""
    if is_e4_sensor_file(file_path):
        return _SENSOR_LAYOUTS[file_path.name][0]
    return None


def is_e4_tags_file(file_path: Path) -> bool:
    """Identify event tags only when a neighboring E4 sensor confirms the format."""
    return file_path.name.lower() == "tags.csv" and any(
        is_e4_sensor_file(file_path.parent / name) for name in _SENSOR_LAYOUTS
    )
