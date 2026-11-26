"""WFDB record checks in dataset preflight."""

import sys
from types import SimpleNamespace

from physionet.validate.checks.integrity import check_integrity
from physionet.validate.config import ValidationConfig
from physionet.validate.models import Severity


def test_preflight_reports_wfdb_errors(tmp_path, monkeypatch):
    (tmp_path / "record.hea").write_text("bad header\n")
    issue = SimpleNamespace(
        path=tmp_path / "record.hea",
        code="header",
        message="invalid syntax in record line",
    )
    monkeypatch.setitem(
        sys.modules,
        "wfdb",
        SimpleNamespace(check_dataset=lambda directory: [issue]),
    )

    result = check_integrity(tmp_path, ValidationConfig())

    assert len(result.issues) == 1
    assert result.issues[0].severity == Severity.ERROR
    assert result.issues[0].code == "wfdb_header"
    assert result.issues[0].file == "record.hea"


def test_preflight_warns_when_wfdb_checker_is_unavailable(tmp_path, monkeypatch):
    (tmp_path / "record.hea").write_text("bad header\n")
    monkeypatch.setitem(sys.modules, "wfdb", None)

    result = check_integrity(tmp_path, ValidationConfig())

    assert len(result.issues) == 1
    assert result.issues[0].severity == Severity.WARNING
    assert result.issues[0].code == "wfdb_checker_unavailable"


def test_preflight_skips_ignored_wfdb_files(tmp_path, monkeypatch):
    ignored = tmp_path / ".git"
    ignored.mkdir()
    (ignored / "record.hea").write_text("bad header\n")
    monkeypatch.setitem(
        sys.modules,
        "wfdb",
        SimpleNamespace(check_dataset=lambda directory: [_unexpected_issue()]),
    )

    result = check_integrity(tmp_path, ValidationConfig())

    assert result.issues == []


def _unexpected_issue():
    raise AssertionError("The WFDB checker should not run for ignored files")
