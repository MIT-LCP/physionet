"""WFDB record checks in dataset preflight."""

import os
import struct
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

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


def _fake_wfdb(monkeypatch, check_dataset):
    monkeypatch.setitem(sys.modules, "wfdb", SimpleNamespace(check_dataset=check_dataset))


def test_preflight_passes_ignore_rules_to_wfdb(tmp_path, monkeypatch):
    (tmp_path / "record.hea").write_text("record 1 250 2\n")
    calls = {}

    def check_dataset(directory, ignore=None):
        calls["ignore"] = ignore
        return []

    _fake_wfdb(monkeypatch, check_dataset)

    result = check_integrity(tmp_path, ValidationConfig())

    assert result.issues == []
    assert calls["ignore"](Path(".git"))
    assert calls["ignore"](Path("data", "__pycache__", "x.hea"))
    assert not calls["ignore"](Path("record.hea"))


def test_preflight_filters_ignored_issues_from_older_wfdb(tmp_path, monkeypatch):
    (tmp_path / "record.hea").write_text("record 1 250 2\n")
    root = tmp_path.resolve()
    issues = [
        SimpleNamespace(path=root / ".git" / "old.hea", code="header", message="bad"),
        SimpleNamespace(path=root / "record.hea", code="header", message="bad"),
    ]
    _fake_wfdb(monkeypatch, lambda directory: issues)

    result = check_integrity(tmp_path, ValidationConfig())

    assert [issue.file for issue in result.issues] == ["record.hea"]


def test_preflight_does_not_follow_symlinked_headers(tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "elsewhere.hea").write_text("elsewhere 1 250 2\n")
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    os.symlink(outside / "elsewhere.hea", dataset / "link.hea")
    issue = SimpleNamespace(
        path=dataset.resolve() / "link.hea", code="symlink", message="Header is a symbolic link"
    )
    _fake_wfdb(monkeypatch, lambda directory: [issue])

    result = check_integrity(dataset, ValidationConfig())

    assert [(issue.code, issue.file) for issue in result.issues] == [("wfdb_symlink", "link.hea")]


def test_preflight_reports_paths_outside_dataset(tmp_path, monkeypatch):
    (tmp_path / "record.hea").write_text("record 1 250 2\n")
    outside = Path("/elsewhere/record.dat")
    issue = SimpleNamespace(path=outside, code="missing_signal", message="missing")
    _fake_wfdb(monkeypatch, lambda directory: [issue])

    result = check_integrity(tmp_path, ValidationConfig())

    assert [(issue.code, issue.file) for issue in result.issues] == [("wfdb_missing_signal", str(outside))]


def test_preflight_warns_when_wfdb_checker_fails(tmp_path, monkeypatch):
    (tmp_path / "record.hea").write_text("record 1 250 2\n")

    def check_dataset(directory):
        raise PermissionError("Permission denied")

    _fake_wfdb(monkeypatch, check_dataset)

    result = check_integrity(tmp_path, ValidationConfig())

    assert [(issue.severity, issue.code) for issue in result.issues] == [
        (Severity.WARNING, "wfdb_checker_failed")
    ]
    assert "Permission denied" in result.issues[0].message


def test_preflight_with_installed_wfdb(tmp_path):
    """Run the real checker when the installed wfdb provides it."""
    wfdb = pytest.importorskip("wfdb")
    if not hasattr(wfdb, "check_dataset"):
        pytest.skip("Installed wfdb has no check_dataset")
    (tmp_path / "good.hea").write_text("good 1 250 2\ngood.dat 16 200 11 0 0 0 0 ECG\n")
    (tmp_path / "good.dat").write_bytes(struct.pack("<hh", 0, 1))
    (tmp_path / "bad.hea").write_text("folder/bad 1 250 2\n")
    hidden = tmp_path / ".git"
    hidden.mkdir()
    (hidden / "ignored.hea").write_text("folder/ignored 1 250 2\n")

    result = check_integrity(tmp_path, ValidationConfig())

    assert [(issue.code, issue.file) for issue in result.issues] == [("wfdb_header", "bad.hea")]
