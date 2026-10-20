"""Data integrity validation checks."""

import csv
from pathlib import Path
from typing import Optional, Callable

from physionet.validate.models import CheckResult, ValidationIssue, CheckCategory, Severity
from physionet.validate.config import ValidationConfig
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

        if any(p in str(csv_file) for p in config.ignore_patterns):
            continue

        _validate_csv_structure(csv_file, path, result)

    return result


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
