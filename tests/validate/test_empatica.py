"""Regression tests for Empatica E4 exports and ordinary CSV safeguards."""

from physionet.validate import ValidationConfig, validate_dataset
from physionet.validate.checks import check_filesystem, check_integrity, check_privacy, check_quality
from physionet.validate.models import Severity


def write_e4_sensor(directory):
    (directory / "EDA.csv").write_text(
        "1673588411.000000\n4.000000\n0.123456\n0.234567\n0.345678\n"
    )


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

    assert any(issue.file == "EDA.csv" and "2 columns, expected 1" in issue.message
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


def test_e4_acc_quality_checks_each_data_channel(tmp_path):
    (tmp_path / "ACC.csv").write_text(
        "1673588411,1673588411,1673588411\n"
        "32,32,32\n"
        ",,50\n,,51\n"
    )

    quality = check_quality(tmp_path, ValidationConfig())
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


def test_malformed_e4_tags_extra_fields_are_scanned_for_phi(tmp_path):
    write_e4_sensor(tmp_path)
    (tmp_path / "tags.csv").write_text(
        "1673588411.000000,person@example.com\n"
        "1673588422.000000\n"
        "1673588433.000000,555-123-4567\n"
    )

    result = check_privacy(tmp_path, ValidationConfig())

    assert {issue.column for issue in result.issues} == {"extra field"}
    assert len(result.issues) == 2
    assert any("email address" in issue.message for issue in result.issues)
    assert any("phone number" in issue.message for issue in result.issues)
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
