"""Recognize the headerless CSV layout exported by Empatica E4 Connect."""

import csv
from pathlib import Path


_SENSOR_LAYOUTS = {
    "ACC.csv": (3, 32),
    "BVP.csv": (1, 64),
    "EDA.csv": (1, 4),
    "TEMP.csv": (1, 4),
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

    channels, sample_rate = layout
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


def is_e4_tags_file(file_path: Path) -> bool:
    """Identify event tags only when a neighboring E4 sensor confirms the format."""
    return file_path.name.lower() == "tags.csv" and any(
        is_e4_sensor_file(file_path.parent / name) for name in _SENSOR_LAYOUTS
    )
