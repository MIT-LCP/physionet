"""Duplicate-content checks for datasets being prepared for submission."""

import hashlib
import json
import subprocess
import sys
from unittest.mock import patch

import pytest

from physionet.validate import ValidationConfig, validate_dataset
from physionet.validate.checks import check_filesystem
from physionet.validate.checks.filesystem import PREFIX_SIZE
from physionet.validate.models import CheckCategory, Severity


def duplicate_issues(result):
    return [issue for issue in result.issues if issue.code == "duplicate-file"]


def test_reports_each_duplicate_with_both_paths_and_checksum(tmp_path):
    for directory in ("a", "b", "c"):
        (tmp_path / directory).mkdir()
    content = b"same bytes"
    (tmp_path / "a" / "record.bin").write_bytes(content)
    (tmp_path / "b" / "backup.bin").write_bytes(content)
    (tmp_path / "c" / "copy.bin").write_bytes(content)
    (tmp_path / "c" / "distinct.bin").write_bytes(b"other text")  # Same size, different bytes.

    result = check_filesystem(tmp_path, ValidationConfig())
    issues = duplicate_issues(result)

    assert [(issue.file, issue.severity) for issue in issues] == [
        ("b/backup.bin", Severity.WARNING),
        ("c/copy.bin", Severity.WARNING),
    ]
    expected_hash = hashlib.sha256(content).hexdigest()
    assert all("a/record.bin" in issue.message and expected_hash in issue.message for issue in issues)
    assert all(issue.to_dict()["code"] == "duplicate-file" for issue in issues)


def test_ignores_empty_files_and_configured_paths(tmp_path):
    (tmp_path / "record.bin").write_bytes(b"content")
    (tmp_path / "ignored").mkdir()
    (tmp_path / "ignored" / "copy.bin").write_bytes(b"content")
    (tmp_path / "copy.pyc").write_bytes(b"content")
    (tmp_path / "empty-a.bin").write_bytes(b"")
    (tmp_path / "empty-b.bin").write_bytes(b"")

    result = check_filesystem(
        tmp_path, ValidationConfig(ignore_patterns=["ignored", "*.pyc"])
    )

    assert duplicate_issues(result) == []
    assert sum(issue.message == "Empty file (0 bytes)" for issue in result.issues) == 2


def test_does_not_follow_symlinks_when_checking_duplicates(tmp_path):
    (tmp_path / "record.bin").write_bytes(b"content")
    try:
        (tmp_path / "link.bin").symlink_to(tmp_path / "record.bin")
    except (NotImplementedError, OSError):
        pytest.skip("Symlinks are unavailable on this filesystem")

    result = check_filesystem(tmp_path, ValidationConfig())

    assert duplicate_issues(result) == []


def test_python_config_can_skip_hashing(tmp_path):
    (tmp_path / "first.bin").write_bytes(b"content")
    (tmp_path / "second.bin").write_bytes(b"content")
    config = ValidationConfig(check_duplicate_files=False, check_documentation=False)

    with patch("physionet.validate.checks.filesystem._sha256_prefix") as hash_prefix, patch(
        "physionet.validate.checks.filesystem._sha256_file"
    ) as hash_file:
        result = validate_dataset(str(tmp_path), config, show_progress=False)

    hash_prefix.assert_not_called()
    hash_file.assert_not_called()
    assert duplicate_issues(result.check_results[CheckCategory.FILESYSTEM]) == []


def test_unreadable_candidate_is_reported(tmp_path):
    content = b"A" * (PREFIX_SIZE + 1)  # Larger than the prefix, so the full file is hashed.
    (tmp_path / "first.bin").write_bytes(content)
    (tmp_path / "second.bin").write_bytes(content)

    with patch("physionet.validate.checks.filesystem._sha256_file", side_effect=OSError("read denied")):
        result = check_filesystem(tmp_path, ValidationConfig())

    assert duplicate_issues(result) == []
    failures = [issue for issue in result.issues if "Could not check file for duplicate content" in issue.message]
    assert {issue.file for issue in failures} == {"first.bin", "second.bin"}
    assert all("read denied" in issue.message for issue in failures)


def test_small_files_are_read_once(tmp_path):
    (tmp_path / "first.bin").write_bytes(b"A" * PREFIX_SIZE)
    (tmp_path / "second.bin").write_bytes(b"A" * PREFIX_SIZE)

    with patch("physionet.validate.checks.filesystem._sha256_file") as hash_file:
        result = check_filesystem(tmp_path, ValidationConfig())

    hash_file.assert_not_called()
    issues = duplicate_issues(result)
    assert [issue.file for issue in issues] == ["second.bin"]
    assert hashlib.sha256(b"A" * PREFIX_SIZE).hexdigest() in issues[0].message


@pytest.mark.parametrize("check_duplicates", [True, False])
def test_file_that_cannot_be_read_is_reported(tmp_path, check_duplicates):
    (tmp_path / "tags.csv").write_bytes(b"")

    with patch(
        "physionet.validate.checks.filesystem.get_csv_layout", side_effect=PermissionError("access denied")
    ):
        result = check_filesystem(tmp_path, ValidationConfig(check_duplicate_files=check_duplicates))

    assert [(issue.file, issue.message) for issue in result.issues] == [
        ("tags.csv", "Could not read file: access denied")
    ]


def test_different_prefixes_avoid_full_file_hashing(tmp_path):
    (tmp_path / "first.bin").write_bytes(b"A" * 100000)
    (tmp_path / "second.bin").write_bytes(b"B" * 100000)

    with patch("physionet.validate.checks.filesystem._sha256_file") as hash_file:
        result = check_filesystem(tmp_path, ValidationConfig())

    hash_file.assert_not_called()
    assert duplicate_issues(result) == []


def test_matching_prefixes_are_checked_to_the_end(tmp_path):
    prefix = b"A" * PREFIX_SIZE
    (tmp_path / "first.bin").write_bytes(prefix + b"tail A")
    (tmp_path / "second.bin").write_bytes(prefix + b"tail B")

    result = check_filesystem(tmp_path, ValidationConfig())

    assert duplicate_issues(result) == []


def test_cli_reports_pairs_in_json_and_can_skip_duplicate_check(tmp_path):
    (tmp_path / "first.bin").write_bytes(b"content")
    (tmp_path / "second.bin").write_bytes(b"content")
    report = tmp_path / "report.json"

    cli = subprocess.run(
        [sys.executable, "-m", "physionet", "validate", str(tmp_path),
         "--checks", "filesystem", "--report", str(report)],
        capture_output=True, text=True,
    )

    assert cli.returncode == 0
    assert "second.bin - Identical content to first.bin" in cli.stdout
    issues = json.loads(report.read_text())["checks"]["filesystem"]["issues"]
    duplicates = [issue for issue in issues if issue.get("code") == "duplicate-file"]
    assert len(duplicates) == 1
    assert duplicates[0]["file"] == "second.bin"
    assert "first.bin" in duplicates[0]["message"]

    skipped = subprocess.run(
        [sys.executable, "-m", "physionet", "validate", str(tmp_path),
         "--checks", "filesystem", "--no-duplicate-check"],
        capture_output=True, text=True,
    )
    assert skipped.returncode == 0
    assert "Identical content" not in skipped.stdout
