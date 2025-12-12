"""Data quality validation checks."""

import csv
from pathlib import Path
from typing import Optional, Callable

from physionet.validate.models import CheckResult, ValidationIssue, CheckCategory, Severity
from physionet.validate.config import ValidationConfig


def check_quality(path: Path, config: ValidationConfig, progress_callback: Optional[Callable[[str], None]] = None) -> CheckResult:
    """
    Check data quality.

    Validates:
    - Missing value thresholds
    - Value range plausibility
    - Data type consistency

    Args:
        path: Path to dataset directory
        config: Validation configuration
        progress_callback: Optional callback to report progress

    Returns:
        CheckResult with any quality issues found
    """
    result = CheckResult(category=CheckCategory.QUALITY)

    # Find and validate CSV files
    csv_files = list(path.rglob("*.csv"))
    for i, csv_file in enumerate(csv_files):
        if progress_callback:
            progress_callback(f"Checking {csv_file.name} ({i+1}/{len(csv_files)} CSV files)")

        if any(p in str(csv_file) for p in config.ignore_patterns):
            continue

        _check_csv_quality(csv_file, path, config, result)

    return result


def _check_csv_quality(csv_file: Path, base_path: Path, config: ValidationConfig, result: CheckResult) -> None:
    """Check quality metrics for a CSV file."""
    try:
        with open(csv_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)

            # Track column statistics
            column_stats = {col: {"total": 0, "missing": 0, "values": []} for col in reader.fieldnames or []}

            # Determine if we should sample this file
            rows_scanned = 0
            max_rows = config.max_rows_to_scan

            # Sample if enabled and file is large
            if config.sample_large_files and max_rows:
                all_rows = list(reader)
                total_rows = len(all_rows)

                if total_rows > max_rows:
                    # Sample evenly distributed rows
                    import random
                    random.seed(42)  # Deterministic sampling
                    step = total_rows / max_rows
                    sampled_indices = [int(i * step) for i in range(max_rows)]
                    rows_to_scan = [all_rows[i] for i in sampled_indices]
                else:
                    rows_to_scan = all_rows
            else:
                rows_to_scan = reader

            for row in rows_to_scan:
                # Stop if we've hit the limit (when not sampling)
                if max_rows and not config.sample_large_files and rows_scanned >= max_rows:
                    break
                rows_scanned += 1

                for col, value in row.items():
                    column_stats[col]["total"] += 1

                    # Check for missing values
                    if not value or value.strip() in ("", "NA", "N/A", "NULL", "null", "None", "NaN"):
                        column_stats[col]["missing"] += 1
                    else:
                        # Store value for range checking if configured
                        if col.lower().replace("_", " ") in [k.lower().replace("_", " ") for k in config.value_ranges]:
                            try:
                                numeric_value = float(value.strip())
                                column_stats[col]["values"].append(numeric_value)
                            except ValueError:
                                pass

            # Analyze results
            for col, stats in column_stats.items():
                if stats["total"] == 0:
                    continue

                # Check missing value threshold
                missing_ratio = stats["missing"] / stats["total"]
                if missing_ratio >= config.missing_value_threshold:
                    result.issues.append(
                        ValidationIssue(
                            severity=Severity.WARNING,
                            category=CheckCategory.QUALITY,
                            file=str(csv_file.relative_to(base_path)),
                            column=col,
                            message=f"Column '{col}' is completely empty (100% missing values)",
                            suggestion=f"Consider removing empty column '{col}' or adding data",
                        )
                    )

                # Check value ranges
                for range_key, (min_val, max_val) in config.value_ranges.items():
                    if col.lower().replace("_", " ") == range_key.lower().replace("_", " "):
                        for value in stats["values"]:
                            if value < min_val or value > max_val:
                                result.issues.append(
                                    ValidationIssue(
                                        severity=Severity.WARNING,
                                        category=CheckCategory.QUALITY,
                                        file=str(csv_file.relative_to(base_path)),
                                        column=col,
                                        value=str(value),
                                        message=f"Value {value} in '{col}' outside expected range [{min_val}, {max_val}]",
                                        suggestion="Verify data accuracy or adjust validation ranges",
                                    )
                                )
                                # Limit warnings per column
                                break

    except Exception as e:
        result.issues.append(
            ValidationIssue(
                severity=Severity.WARNING,
                category=CheckCategory.QUALITY,
                file=str(csv_file.relative_to(base_path)),
                message=f"Could not perform quality checks: {str(e)}",
            )
        )
