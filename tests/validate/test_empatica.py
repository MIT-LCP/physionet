"""Regression tests for Empatica E4 exports and ordinary CSV safeguards."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from physionet.validate import ValidationConfig, validate_dataset
from physionet.validate.checks import check_filesystem, check_integrity, check_privacy, check_quality
from physionet.validate.checks.csv_layout import is_unix_timestamp
from physionet.validate.checks.empatica import is_e4_ibi_file, is_e4_sensor_file
from physionet.validate.models import Severity


E4_EXAMPLE = Path(__file__).parent.parent / "fixtures" / "e4_session"


def write_e4_sensor(directory):
    (directory / "EDA.csv").write_text(
        "1673588411.000000\n4.000000\n0.123456\n0.234567\n0.345678\n"
    )


@pytest.mark.parametrize("timestamp", ["1673588411", "631152000", "5680281600"])
@pytest.mark.parametrize("empty_tags", [True, False])
def test_integer_and_shifted_e4_timestamps_validate_cleanly(tmp_path, timestamp, empty_tags):
    (tmp_path / "ACC.csv").write_text(
        f"{timestamp},{timestamp},{timestamp}\n"
        "32,32,32\n"
        "27,-25,50\n"
    )
    (tmp_path / "EDA.csv").write_text(
        f"{timestamp}\n4\n0.123456\n0.234567\n"
    )
    (tmp_path / "tags.csv").write_text("" if empty_tags else f"{timestamp}.000000\n")

    result = validate_dataset(
        str(tmp_path), ValidationConfig(check_documentation=False), show_progress=False
    )

    assert result.total_errors == 0
    assert result.total_warnings == 0
    assert result.total_info == (1 if empty_tags else 0)


@pytest.mark.parametrize(
    ("timestamp", "expected"),
    [
        ("631152000", True),
        ("5680281600.123456", True),
        ("1673588411", True),
        ("63115200", False),
        ("56802816000", False),
        ("-631152000", False),
        ("000000000", False),
        ("631152000.", False),
        ("631152000.1e3", False),
    ],
)
def test_unix_timestamp_shape(timestamp, expected):
    assert is_unix_timestamp(timestamp) is expected


def test_e4_headerless_sensor_files_do_not_trigger_csv_structure_errors(tmp_path):
    write_e4_sensor(tmp_path)
    (tmp_path / "ACC.csv").write_text(
        "1673588411.000000,1673588411.000000,1673588411.000000\n"
        "32.000000,32.000000,32.000000\n"
        "27,-25,50\n27,-24,50\n"
    )

    result = check_integrity(tmp_path, ValidationConfig())

    assert result.error_count == 0
    assert not any("Duplicate column names" in issue.message for issue in result.issues)


def test_e4_tags_timestamps_are_not_reported_as_phone_numbers(tmp_path):
    write_e4_sensor(tmp_path)
    (tmp_path / "tags.csv").write_text("1673588411.000000\n1673588422.000000\n")

    result = check_privacy(tmp_path, ValidationConfig())

    assert not any("phone number" in issue.message for issue in result.issues)


def test_empty_e4_tags_means_no_events_not_a_damaged_csv(tmp_path):
    write_e4_sensor(tmp_path)
    (tmp_path / "tags.csv").write_text("")

    filesystem = check_filesystem(tmp_path, ValidationConfig())
    integrity = check_integrity(tmp_path, ValidationConfig())

    tag_issues = [issue for issue in filesystem.issues if issue.file == "tags.csv"]
    assert len(tag_issues) == 1
    assert tag_issues[0].severity == Severity.INFO
    assert "No Empatica E4 event markers" in tag_issues[0].message
    assert not any(issue.file == "tags.csv" for issue in integrity.issues)


def test_e4_sensor_still_reports_malformed_rows(tmp_path):
    write_e4_sensor(tmp_path)
    with (tmp_path / "EDA.csv").open("a") as stream:
        stream.write("1,2\n")

    result = check_integrity(tmp_path, ValidationConfig())
    quality = check_quality(tmp_path, ValidationConfig())

    assert any(issue.file == "EDA.csv" and "2 columns, expected 1" in issue.message
               for issue in result.issues)
    assert not any("Could not perform quality checks" in issue.message
                   for issue in quality.issues)


def test_e4_sensor_with_metadata_but_no_samples_warns(tmp_path):
    (tmp_path / "EDA.csv").write_text("1673588411\n4\n")

    result = check_integrity(tmp_path, ValidationConfig())

    assert any(issue.file == "EDA.csv" and "no data" in issue.message
               for issue in result.issues)


def test_e4_tags_first_row_is_checked_as_data(tmp_path):
    write_e4_sensor(tmp_path)
    (tmp_path / "tags.csv").write_text("1673588411,unexpected\n1673588422\n")

    result = check_integrity(tmp_path, ValidationConfig())

    assert any(issue.file == "tags.csv" and issue.line == 1
               and "2 columns, expected 1" in issue.message for issue in result.issues)


def test_other_empty_files_and_phone_numbers_still_warn(tmp_path):
    (tmp_path / "tags.csv").write_text("")
    (tmp_path / "contacts.csv").write_text("phone\n2125551234\n")

    filesystem = check_filesystem(tmp_path, ValidationConfig())
    integrity = check_integrity(tmp_path, ValidationConfig())
    privacy = check_privacy(tmp_path, ValidationConfig())

    assert any(issue.file == "tags.csv" and issue.severity == Severity.WARNING
               for issue in filesystem.issues)
    assert any(issue.file == "tags.csv" and issue.severity == Severity.ERROR
               for issue in integrity.issues)
    assert any(issue.file == "contacts.csv" and "phone number" in issue.message
               for issue in privacy.issues)


def test_non_timestamp_phone_in_e4_tags_still_warns(tmp_path):
    write_e4_sensor(tmp_path)
    (tmp_path / "tags.csv").write_text("1673588411.000000\n555-123-4567\n")

    result = check_privacy(tmp_path, ValidationConfig())

    assert any(issue.file == "tags.csv" and "phone number" in issue.message
               for issue in result.issues)


@pytest.mark.parametrize("sample_large_files", [True, False])
def test_e4_acc_quality_checks_each_data_channel(tmp_path, sample_large_files):
    (tmp_path / "ACC.csv").write_text(
        "1673588411,1673588411,1673588411\n"
        "32,32,32\n"
        ",,50\n,,51\n"
    )

    quality = check_quality(tmp_path, ValidationConfig(sample_large_files=sample_large_files))
    result = validate_dataset(
        str(tmp_path), ValidationConfig(check_documentation=False), show_progress=False
    )

    assert {issue.column for issue in quality.issues} == {"acc_x", "acc_y"}
    assert result.status == "warning"


def test_e4_quality_range_excludes_timestamp_and_sample_rate(tmp_path):
    (tmp_path / "EDA.csv").write_text("1673588411\n4\n0.5\n1.5\n")

    result = check_quality(tmp_path, ValidationConfig(value_ranges={"eda": (0, 1)}))

    assert len(result.issues) == 1
    assert result.issues[0].column == "eda"
    assert result.issues[0].value == "1.5"


@pytest.mark.parametrize(
    ("filename", "column", "valid_range"),
    [("BVP.csv", "bvp", (0, 1)), ("TEMP.csv", "temp", (30, 40))],
)
def test_single_channel_e4_quality_ignores_metadata(filename, column, valid_range):
    sensor = E4_EXAMPLE / filename

    assert is_e4_sensor_file(sensor)
    result = check_quality(E4_EXAMPLE, ValidationConfig(value_ranges={column: valid_range}))

    assert not any(issue.file == filename for issue in result.issues)


def test_generic_csv_named_like_e4_sensor_keeps_its_header(tmp_path):
    (tmp_path / "EDA.csv").write_text("eda\n4\n15\n")

    assert not is_e4_sensor_file(tmp_path / "EDA.csv")
    result = check_quality(tmp_path, ValidationConfig(value_ranges={"eda": (0, 10)}))

    assert len(result.issues) == 1
    assert result.issues[0].column == "eda"
    assert result.issues[0].value == "15.0"


def test_empty_tags_without_valid_e4_neighbor_remains_an_error(tmp_path):
    (tmp_path / "EDA.csv").write_text("1673588411\n5\n0.123456\n")
    (tmp_path / "tags.csv").write_text("")

    filesystem = check_filesystem(tmp_path, ValidationConfig())
    integrity = check_integrity(tmp_path, ValidationConfig())

    assert any(issue.file == "tags.csv" and issue.severity == Severity.WARNING
               for issue in filesystem.issues)
    assert any(issue.file == "tags.csv" and issue.severity == Severity.ERROR
               for issue in integrity.issues)


@pytest.mark.parametrize("sample_large_files", [True, False])
def test_malformed_e4_tags_extra_fields_are_scanned_for_phi(tmp_path, sample_large_files):
    write_e4_sensor(tmp_path)
    (tmp_path / "tags.csv").write_text(
        "1673588411.000000,person@example.com,555-123-4567\n"
        "1673588422.000000\n"
        "1673588433.000000,123-45-6789\n"
    )

    result = check_privacy(tmp_path, ValidationConfig(sample_large_files=sample_large_files))

    # One warning per column and pattern.
    assert {issue.column for issue in result.issues} == {"extra field"}
    assert len(result.issues) == 3
    assert any("email address" in issue.message for issue in result.issues)
    assert any("phone number" in issue.message for issue in result.issues)
    assert any("SSN" in issue.message for issue in result.issues)
    assert not any("Could not perform" in issue.message for issue in result.issues)


def test_malformed_generic_csv_extra_fields_are_scanned_for_phi(tmp_path):
    (tmp_path / "contacts.csv").write_text("id\n1,person@example.com\n2,555-123-4567\n")

    result = check_privacy(tmp_path, ValidationConfig())

    assert {issue.column for issue in result.issues} == {"extra field"}
    assert len(result.issues) == 2
    assert not any("Could not perform" in issue.message for issue in result.issues)


def test_complete_validation_accepts_e4_export_without_false_alerts(tmp_path):
    write_e4_sensor(tmp_path)
    (tmp_path / "tags.csv").write_text("")
    config = ValidationConfig(check_documentation=False)

    result = validate_dataset(str(tmp_path), config, show_progress=False)

    assert result.total_errors == 0
    assert result.total_warnings == 0
    assert result.total_info == 1


def test_synthetic_e4_example_validates_through_python_and_cli(tmp_path):
    config = ValidationConfig()
    result = validate_dataset(str(E4_EXAMPLE), config, show_progress=False)

    assert result.status == "pass"
    assert result.total_errors == 0
    assert result.total_warnings == 0
    assert result.dataset_stats.file_count == 8
    assert set(result.to_dict()["checks"]) == {
        "filesystem", "documentation", "integrity", "quality", "privacy"
    }

    report = tmp_path / "report.json"
    cli = subprocess.run(
        [sys.executable, "-m", "physionet", "validate", str(E4_EXAMPLE), "--report", str(report)],
        capture_output=True,
        text=True,
    )

    assert cli.returncode == 0, cli.stdout + cli.stderr
    assert report.exists()
    report_data = json.loads(report.read_text())
    assert report_data["summary"]["status"] == "pass"
    assert report_data["summary"]["total_errors"] == 0
    assert report_data["summary"]["total_warnings"] == 0


def test_e4_hr_file_is_recognized_and_range_checked(tmp_path):
    (tmp_path / "HR.csv").write_text("1673588421.000000\n1.000000\n72.00\n400.00\n")

    assert is_e4_sensor_file(tmp_path / "HR.csv")
    result = check_quality(tmp_path, ValidationConfig(value_ranges={"hr": (20, 250)}))

    flagged = [issue for issue in result.issues if issue.column == "hr"]
    assert len(flagged) == 1 and "400" in flagged[0].message
    assert check_integrity(tmp_path, ValidationConfig()).issues == []


def test_e4_hr_confirms_tags_without_other_sensors(tmp_path):
    (tmp_path / "HR.csv").write_text("1673588421.000000\n1.000000\n72.00\n")
    (tmp_path / "tags.csv").write_text("")

    result = validate_dataset(
        str(tmp_path), ValidationConfig(check_documentation=False), show_progress=False
    )

    assert result.total_errors == 0
    assert result.total_warnings == 0


def test_e4_ibi_file_uses_offset_and_interval_columns(tmp_path):
    (tmp_path / "IBI.csv").write_text(
        "1673588411.000000, IBI\n12.343750,0.828125\n13.156250,0.812500\n"
    )

    assert is_e4_ibi_file(tmp_path / "IBI.csv")
    result = validate_dataset(
        str(tmp_path),
        ValidationConfig(check_documentation=False, value_ranges={"ibi": (0.3, 2.0)}),
        show_progress=False,
    )

    assert result.total_errors == 0
    assert result.total_warnings == 0


def test_e4_ibi_with_no_beats_is_information_not_a_warning(tmp_path):
    (tmp_path / "IBI.csv").write_text("1673588411.000000, IBI\n")

    result = check_integrity(tmp_path, ValidationConfig())

    assert [issue.severity for issue in result.issues] == [Severity.INFO]
    assert "No Empatica E4 inter-beat intervals" in result.issues[0].message


def test_e4_ibi_still_reports_malformed_rows(tmp_path):
    (tmp_path / "IBI.csv").write_text("1673588411.000000, IBI\n12.343750\n")

    result = check_integrity(tmp_path, ValidationConfig())

    assert any(issue.severity == Severity.ERROR and "expected 2" in issue.message
               for issue in result.issues)


def test_generic_csv_named_ibi_keeps_its_header(tmp_path):
    (tmp_path / "IBI.csv").write_text("time,ibi\n1,0.8\n")

    assert not is_e4_ibi_file(tmp_path / "IBI.csv")


def test_lowercase_e4_file_names_are_recognized(tmp_path):
    (tmp_path / "eda.csv").write_text("1673588411\n4\n0.123456\n")
    (tmp_path / "acc.csv").write_text("1673588411, 1673588411, 1673588411\n32, 32, 32\n-1, 63, 12\n")
    (tmp_path / "TAGS.csv").write_text("")

    result = validate_dataset(
        str(tmp_path), ValidationConfig(check_documentation=False), show_progress=False
    )

    assert result.total_errors == 0
    assert result.total_warnings == 0
    assert result.total_info == 1
