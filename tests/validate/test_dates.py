"""Dates that may be real are flagged for review; shifted or ambiguous values are not."""

from datetime import date

import pytest

from physionet.validate import ValidationConfig, validate_dataset
from physionet.validate.checks import check_privacy
from physionet.validate.models import CheckCategory
from physionet.validate.checks.dates import (
    RealisticDateFinder, dates_in_text, is_time_like_column, unix_timestamp_date,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2023-01-13", date(2023, 1, 13)),
        ("2023-01-13T08:15:00", date(2023, 1, 13)),
        ("2023-01-13 08:15", date(2023, 1, 13)),
        ("2023/1/13", date(2023, 1, 13)),
        ("13/01/2023", date(2023, 1, 13)),
        ("01-13-2023", date(2023, 1, 13)),
        ("Admitted 13 Jan 2023 overnight", date(2023, 1, 13)),
        ("January 13th, 2023", date(2023, 1, 13)),
        ("Sept 2019", date(2019, 9, 1)),
    ],
)
def test_written_dates_are_found(text, expected):
    assert expected in list(dates_in_text(text))


def test_full_date_is_not_also_counted_as_month_and_year():
    assert list(dates_in_text("seen 5 May 2019 and in June 2020")) == [
        date(2019, 5, 5), date(2020, 6, 1)
    ]


@pytest.mark.parametrize(
    "text",
    [
        "0.123456", "13/01/23", "20230113", "08:15:00", "2023", "1.2.3",
        "2023-13-45", "maybe 2023", "version 1.10.2", "-25.50",
    ],
)
def test_non_dates_are_ignored(text):
    assert list(dates_in_text(text)) == []


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1673588411", date(2023, 1, 13)),
        ("1673588411.250000", date(2023, 1, 13)),
        ("1673588411000", date(2023, 1, 13)),
        ("631152000", date(1990, 1, 1)),
        ("12345678", None),
        ("0673588411", None),
        ("2023-01-13", None),
    ],
)
def test_unix_seconds_and_milliseconds(value, expected):
    assert unix_timestamp_date(value) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("charttime", True), ("event_time", True), ("Timestamp", True), ("ts", True),
        ("start_ts", True), ("admit_date", True), ("epoch", True),
        ("subject_id", False), ("status", False), ("patients", False), (None, False),
    ],
)
def test_time_like_column_names(name, expected):
    assert is_time_like_column(name) is expected


def test_range_runs_from_lookback_years_ago_to_today():
    finder = RealisticDateFinder(200, today=date(2026, 10, 20))

    assert finder.in_range(date(1826, 1, 1))
    assert finder.in_range(date(2026, 10, 20))
    assert not finder.in_range(date(1825, 12, 31))
    assert not finder.in_range(date(2150, 1, 1))


def _date_warnings(result):
    return [issue for issue in result.issues if "Dates within the last" in issue.message]


def test_csv_dates_are_reported_once_per_file_with_their_range(tmp_path):
    (tmp_path / "events.csv").write_text(
        "subject_id,charttime,note\n"
        "10000032,2019-03-04 10:00,seen 5 May 2019\n"
        "10000032,2019-05-01 08:30,\n"
    )

    warnings = _date_warnings(check_privacy(tmp_path, ValidationConfig()))

    assert len(warnings) == 1
    assert warnings[0].file == "events.csv"
    assert "column 'charttime': 2019-03-04 to 2019-05-01" in warnings[0].message
    assert "column 'note': 2019-05-05" in warnings[0].message


def test_shifted_future_dates_are_not_reported(tmp_path):
    (tmp_path / "events.csv").write_text(
        "charttime,epoch\n2180-07-23 10:00,6531840000\n"
    )

    assert _date_warnings(check_privacy(tmp_path, ValidationConfig())) == []


def test_unix_timestamps_need_a_time_like_column(tmp_path):
    (tmp_path / "ids.csv").write_text("record_id\n1673588411\n")
    (tmp_path / "times.csv").write_text("timestamp_ms\n1673588411000\n")

    warnings = _date_warnings(check_privacy(tmp_path, ValidationConfig()))

    assert [(issue.file, issue.column) for issue in warnings] == [("times.csv", "timestamp_ms")]


def test_text_file_dates_are_reported(tmp_path):
    (tmp_path / "notes.txt").write_text("Patient admitted on 13 Jan 2023.\nNo other dates.\n")

    warnings = _date_warnings(check_privacy(tmp_path, ValidationConfig()))

    assert [issue.file for issue in warnings] == ["notes.txt"]
    assert "2023-01-13" in warnings[0].message


def test_e4_start_times_and_event_times_are_reported_as_dates_not_phones(tmp_path):
    (tmp_path / "EDA.csv").write_text("1673588411.000000\n4.000000\n0.123456\n")
    (tmp_path / "tags.csv").write_text("1673588500.000000\n")

    result = check_privacy(tmp_path, ValidationConfig())

    assert not any("phone number" in issue.message for issue in result.issues)
    messages = {issue.file: issue.message for issue in _date_warnings(result)}
    assert "column 'start_time': 2023-01-13" in messages["EDA.csv"]
    assert "column 'event_time': 2023-01-13" in messages["tags.csv"]


def test_date_check_can_be_turned_off(tmp_path):
    (tmp_path / "events.csv").write_text("charttime\n2019-03-04\n")

    result = check_privacy(tmp_path, ValidationConfig(date_lookback_years=None))

    assert _date_warnings(result) == []


def test_many_files_with_dates_are_summarized(tmp_path):
    for number in range(13):
        (tmp_path / f"visit_{number:02d}.csv").write_text("charttime\n2019-03-04\n")

    result = validate_dataset(
        str(tmp_path), ValidationConfig(check_documentation=False), show_progress=False
    )
    warnings = _date_warnings(result.check_results[CheckCategory.PRIVACY])

    assert len(warnings) == 11
    assert warnings[-1].file is None
    assert "3 more files (showing first 10)" in warnings[-1].message
    assert "  ⚠ Dates within the last 200 years also found in 3 more files" in result.summary()
