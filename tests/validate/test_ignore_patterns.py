"""Every check applies ignore patterns the same way."""

import pytest

from physionet.validate import ValidationConfig, validate_dataset
from physionet.validate.config import is_ignored_path, should_ignore


@pytest.mark.parametrize(
    ("name", "patterns", "expected"),
    [
        ("bad.csv", ["*.csv"], True),
        ("bad.csv.bak", ["*.csv"], False),
        ("__pycache__", ["__pycache__"], True),
        ("notes.txt", ["*.csv", ".git"], False),
    ],
)
def test_should_ignore(name, patterns, expected):
    assert should_ignore(name, patterns) is expected


def test_folder_patterns_apply_below_the_dataset_root_only(tmp_path):
    root = tmp_path / "scratch" / "dataset"
    assert is_ignored_path(root / "scratch" / "a.csv", root, ["scratch"])
    assert not is_ignored_path(root / "data" / "a.csv", root, ["scratch"])


@pytest.mark.parametrize("patterns", [["*.csv"], ["skip"]])
def test_ignored_csv_is_skipped_by_every_check(tmp_path, patterns):
    folder = tmp_path / "skip"
    folder.mkdir()
    # Inconsistent columns, a phone number and a real date would each be reported.
    (folder / "bad.csv").write_text("a,b\n1\n555-123-4567,2019-03-04\n")
    (tmp_path / "README.md").write_text("Synthetic data.\n")

    result = validate_dataset(
        str(tmp_path), ValidationConfig(ignore_patterns=patterns), show_progress=False
    )

    issues = [issue for check in result.check_results.values() for issue in check.issues]
    assert [issue for issue in issues if issue.file and "bad.csv" in issue.file] == []
    assert result.dataset_stats.file_count == 1
