"""Data integrity validation checks."""

import csv
import inspect
from pathlib import Path
from typing import Optional, Callable

from physionet.validate.models import CheckResult, ValidationIssue, CheckCategory, Severity
from physionet.validate.config import ValidationConfig, is_ignored_path
from physionet.validate.checks.csv_formats import find_files_by_suffix, get_csv_layout


def check_integrity(path: Path, config: ValidationConfig, progress_callback: Optional[Callable[[str], None]] = None) -> CheckResult:
    """
    Check data integrity and format validation.

    Validates:
    - CSV file structure
    - File format validity
    - Basic structural consistency

    Args:
        path: Path to dataset directory
        config: Validation configuration
        progress_callback: Optional callback to report progress

    Returns:
        CheckResult with any integrity issues found
    """
    result = CheckResult(category=CheckCategory.INTEGRITY)

    # Find and validate CSV files
    csv_files = find_files_by_suffix(path, ".csv")
    for i, csv_file in enumerate(csv_files):
        if progress_callback:
            progress_callback(f"Checking {csv_file.name} ({i+1}/{len(csv_files)} CSV files)")

        if is_ignored_path(csv_file, path, config.ignore_patterns):
            continue

        _validate_csv_structure(csv_file, path, result)

    _validate_wfdb(path, config, result, progress_callback)

    return result


def _validate_wfdb(
    path: Path,
    config: ValidationConfig,
    result: CheckResult,
    progress_callback: Optional[Callable[[str], None]],
) -> None:
    """Check WFDB records when the optional wfdb package is available."""
    records = (
        file_path
        for suffix in ("*.hea", "*.wfdb")
        for file_path in path.rglob(suffix)
    )
    if not any(
        not is_ignored_path(file_path, path, config.ignore_patterns)
        for file_path in records
    ):
        return

    if progress_callback:
        progress_callback("Checking WFDB records")
    try:
        import wfdb
    except ImportError:
        wfdb = None

    if wfdb is None or not hasattr(wfdb, "check_dataset"):
        result.issues.append(
            ValidationIssue(
                severity=Severity.WARNING,
                category=CheckCategory.INTEGRITY,
                code="wfdb_checker_unavailable",
                message=(
                    "WFDB records were not checked. Install or upgrade wfdb "
                    "with 'pip install -U wfdb' and run preflight again."
                ),
            )
        )
        return

    root = path.resolve()

    def ignore(relative_path: Path) -> bool:
        return is_ignored_path(root / relative_path, root, config.ignore_patterns)

    try:
        if _accepts_ignore(wfdb.check_dataset):
            # Skip ignored folders while walking the dataset
            issues = wfdb.check_dataset(root, ignore=ignore)
        else:
            issues = wfdb.check_dataset(root)
    except Exception as exc:
        result.issues.append(
            ValidationIssue(
                severity=Severity.WARNING,
                category=CheckCategory.INTEGRITY,
                code="wfdb_checker_failed",
                message=f"WFDB records could not be checked: {exc}",
            )
        )
        return

    for issue in issues:
        # Report paths as found in the dataset. Resolving them would follow
        # symbolic links, possibly to files outside the dataset.
        try:
            relative_path = Path(issue.path).relative_to(root)
        except ValueError:
            file = str(issue.path)
        else:
            # Older wfdb versions check ignored folders too
            if ignore(relative_path):
                continue
            file = str(relative_path)
        result.issues.append(
            ValidationIssue(
                severity=Severity.ERROR,
                category=CheckCategory.INTEGRITY,
                code=f"wfdb_{issue.code}",
                file=file,
                message=issue.message,
            )
        )


def _accepts_ignore(check_dataset: Callable) -> bool:
    """Whether the installed wfdb's check_dataset takes an ignore argument."""
    try:
        parameters = inspect.signature(check_dataset).parameters
    except (TypeError, ValueError):
        return False
    return "ignore" in parameters or any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
    )


def _validate_csv_structure(csv_file: Path, base_path: Path, result: CheckResult) -> None:
    """Validate CSV file structure."""
    layout = get_csv_layout(csv_file)
    try:
        with open(csv_file, "r", encoding="utf-8") as f:
            # Try to detect dialect
            sample = f.read(1024)
            f.seek(0)

            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
            except csv.Error:
                # Use default dialect if detection fails
                dialect = csv.excel

            reader = csv.reader(f, dialect)

            # Read the first row, which is a header for ordinary CSV files.
            try:
                first_row = next(reader)
            except StopIteration:
                if layout.allow_empty:
                    return
                result.issues.append(
                    ValidationIssue(
                        severity=Severity.ERROR,
                        category=CheckCategory.INTEGRITY,
                        file=str(csv_file.relative_to(base_path)),
                        message="CSV file is empty",
                    )
                )
                return

            if not first_row:
                result.issues.append(
                    ValidationIssue(
                        severity=Severity.ERROR,
                        category=CheckCategory.INTEGRITY,
                        file=str(csv_file.relative_to(base_path)),
                        message="CSV file has no header row",
                    )
                )
                return

            if layout.columns is None:
                header = first_row
                if len(header) != len(set(header)):
                    duplicates = [col for col in header if header.count(col) > 1]
                    result.issues.append(
                        ValidationIssue(
                            severity=Severity.ERROR,
                            category=CheckCategory.INTEGRITY,
                            file=str(csv_file.relative_to(base_path)),
                            message=f"Duplicate column names found: {', '.join(set(duplicates))}",
                        )
                    )
                if any(not col.strip() for col in header):
                    result.issues.append(
                        ValidationIssue(
                            severity=Severity.ERROR,
                            category=CheckCategory.INTEGRITY,
                            file=str(csv_file.relative_to(base_path)),
                            message="CSV contains empty column names",
                        )
                    )
                expected_cols = len(header)
                first_data_line = 2
            else:
                # Headerless files have explicit names; validate their first data row.
                f.seek(0)
                reader = csv.reader(f, dialect)
                for _ in range(layout.metadata_rows):
                    next(reader, None)
                expected_cols = len(layout.columns)
                first_data_line = layout.metadata_rows + 1

            # Validate row consistency
            row_count = 0
            for line_num, row in enumerate(reader, start=first_data_line):
                row_count += 1
                if len(row) != expected_cols:
                    result.issues.append(
                        ValidationIssue(
                            severity=Severity.ERROR,
                            category=CheckCategory.INTEGRITY,
                            file=str(csv_file.relative_to(base_path)),
                            line=line_num,
                            message=f"Row has {len(row)} columns, expected {expected_cols}",
                        )
                    )
                    # Only report first few inconsistencies to avoid spam
                    if len([i for i in result.issues if i.file == str(csv_file.relative_to(base_path))]) >= 5:
                        result.issues.append(
                            ValidationIssue(
                                severity=Severity.INFO,
                                category=CheckCategory.INTEGRITY,
                                file=str(csv_file.relative_to(base_path)),
                                message=f"Additional row inconsistencies may exist (showing first 5)",
                            )
                        )
                        break

            if row_count == 0 and not layout.allow_empty:
                result.issues.append(
                    ValidationIssue(
                        severity=Severity.INFO if layout.no_data_message else Severity.WARNING,
                        category=CheckCategory.INTEGRITY,
                        file=str(csv_file.relative_to(base_path)),
                        message=layout.no_data_message or "CSV file contains only header row (no data)",
                    )
                )

    except UnicodeDecodeError:
        result.issues.append(
            ValidationIssue(
                severity=Severity.ERROR,
                category=CheckCategory.INTEGRITY,
                file=str(csv_file.relative_to(base_path)),
                message="CSV file has encoding issues (not valid UTF-8)",
                suggestion="Convert file to UTF-8 encoding",
            )
        )
    except Exception as e:
        result.issues.append(
            ValidationIssue(
                severity=Severity.WARNING,
                category=CheckCategory.INTEGRITY,
                file=str(csv_file.relative_to(base_path)),
                message=f"Could not validate CSV file: {str(e)}",
            )
        )
