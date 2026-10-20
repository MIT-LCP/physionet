"""A new CSV format should work across checks through one layout detector."""

from physionet.validate import ValidationConfig
from physionet.validate.checks import check_filesystem, check_integrity, check_privacy, check_quality
from physionet.validate.checks import csv_formats
from physionet.validate.checks.csv_layout import CsvLayout
from physionet.validate.models import Severity


def test_custom_headerless_layout_is_used_by_all_csv_checks(tmp_path, monkeypatch):
    (tmp_path / "signals.csv").write_text(
        "device export\n"
        "5680288411,0.1\n"
        "5680288422,\n"
    )

    def detect(path):
        if path.name == "signals.csv":
            return CsvLayout(
                columns=("event_time", "signal"),
                metadata_rows=1,
                unix_timestamp_columns=("event_time",),
            )
        return None

    monkeypatch.setattr(csv_formats, "_FORMAT_DETECTORS", (detect,))
    config = ValidationConfig(missing_value_threshold=0.5)

    integrity = check_integrity(tmp_path, config)
    quality = check_quality(tmp_path, config)
    privacy = check_privacy(tmp_path, config)

    assert not integrity.issues
    assert len(quality.issues) == 1
    assert quality.issues[0].column == "signal"
    assert "missing values" in quality.issues[0].message
    assert not privacy.issues


def test_custom_layout_can_allow_an_empty_csv(tmp_path, monkeypatch):
    (tmp_path / "events.csv").write_text("")

    def detect(path):
        if path.name == "events.csv":
            return CsvLayout(columns=("event_time",), allow_empty=True,
                             empty_message="No events recorded")
        return None

    monkeypatch.setattr(csv_formats, "_FORMAT_DETECTORS", (detect,))

    filesystem = check_filesystem(tmp_path, ValidationConfig())
    integrity = check_integrity(tmp_path, ValidationConfig())

    assert len(filesystem.issues) == 1
    assert filesystem.issues[0].severity == Severity.INFO
    assert filesystem.issues[0].message == "No events recorded"
    assert not integrity.issues
