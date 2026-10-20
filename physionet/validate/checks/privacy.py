"""Privacy and PHI validation checks."""

import csv
import os
import re
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from physionet.validate.models import CheckResult, ValidationIssue, CheckCategory, Severity
from physionet.validate.config import ValidationConfig
from physionet.validate.checks.csv_formats import find_files_by_suffix, get_csv_layout
from physionet.validate.checks.csv_layout import is_unix_timestamp
from physionet.validate.checks.dates import (
    DateRange, RealisticDateFinder, dates_in_text, is_time_like_column,
)

# Report realistic dates for at most this many files, then summarize the rest.
MAX_DATE_WARNING_FILES = 10

# Pattern names for better error messages
PHI_PATTERN_NAMES = {
    r"\b\d{3}-\d{2}-\d{4}\b": "SSN",
    r"\b[\w\.-]+@[\w\.-]+\.\w+\b": "email address",
    r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b": "phone number",
}

# Sensitive configuration files that should not be included in datasets
SENSITIVE_FILES = {
    # API keys and credentials
    ".env": "environment variables (may contain API keys)",
    ".env.local": "local environment variables",
    ".env.production": "production environment variables",
    "credentials.json": "credential file",
    "secrets.json": "secrets file",
    "config.json": "configuration file (may contain credentials)",
    ".aws/credentials": "AWS credentials",
    ".aws/config": "AWS configuration",

    # SSH and certificates
    "id_rsa": "SSH private key",
    "id_dsa": "SSH private key",
    "id_ecdsa": "SSH private key",
    "id_ed25519": "SSH private key",
    ".pem": "private certificate/key",
    ".key": "private key",
    ".p12": "certificate file",
    ".pfx": "certificate file",

    # Database
    ".pgpass": "PostgreSQL password file",
    ".my.cnf": "MySQL configuration (may contain passwords)",

    # Other sensitive files
    ".netrc": "authentication credentials",
    ".htpasswd": "HTTP authentication",
    "docker-compose.override.yml": "Docker override (may contain secrets)",
}


def check_privacy(path: Path, config: ValidationConfig, progress_callback: Optional[Callable[[str], None]] = None) -> CheckResult:
    """
    Check for potential privacy issues and PHI.

    Validates:
    - PHI pattern detection
    - Age de-identification
    - Sensitive configuration files (keys, credentials)
    - Dates within config.date_lookback_years of today, which may be real

    Args:
        path: Path to dataset directory
        config: Validation configuration
        progress_callback: Optional callback to report progress

    Returns:
        CheckResult with any privacy issues found
    """
    result = CheckResult(category=CheckCategory.PRIVACY)

    # Check for sensitive configuration files
    if progress_callback:
        progress_callback("Checking for sensitive configuration files")
    _check_sensitive_files(path, config, result)

    # Compile PHI patterns with names
    pattern_info = [(re.compile(pattern), PHI_PATTERN_NAMES.get(pattern, "unknown pattern"))
                    for pattern in config.phi_patterns]

    date_finder = (
        RealisticDateFinder(config.date_lookback_years) if config.date_lookback_years else None
    )
    date_findings: List[Tuple[str, Dict[Optional[str], DateRange]]] = []

    # Check CSV files
    csv_files = find_files_by_suffix(path, ".csv")
    for i, csv_file in enumerate(csv_files):
        if progress_callback:
            progress_callback(f"Checking {csv_file.name} ({i+1}/{len(csv_files)} CSV files)")

        if any(p in str(csv_file) for p in config.ignore_patterns):
            continue

        _check_csv_privacy(csv_file, path, config, pattern_info, result, date_finder, date_findings)

    # Check text files for PHI
    text_files = find_files_by_suffix(path, ".txt")
    for i, text_file in enumerate(text_files):
        if progress_callback:
            progress_callback(f"Checking {text_file.name} ({i+1}/{len(text_files)} text files)")

        if any(p in str(text_file) for p in config.ignore_patterns):
            continue

        _check_text_file_privacy(
            text_file, path, pattern_info, result, config, date_finder, date_findings
        )

    if date_finder:
        _report_realistic_dates(date_findings, config.date_lookback_years, result)

    return result


def _report_realistic_dates(
    findings: List[Tuple[str, Dict[Optional[str], DateRange]]],
    lookback_years: int,
    result: CheckResult,
) -> None:
    """Warn about files holding dates that may be real, one warning per file."""
    for relative_path, ranges in findings[:MAX_DATE_WARNING_FILES]:
        places = []
        for column, date_range in ranges.items():
            where = f"column '{column}': " if column is not None else ""
            places.append(f"{where}{date_range.describe()}")
        columns = [column for column in ranges if column is not None]
        result.issues.append(
            ValidationIssue(
                severity=Severity.WARNING,
                category=CheckCategory.PRIVACY,
                file=relative_path,
                column=columns[0] if len(columns) == 1 else None,
                message=(
                    f"Dates within the last {lookback_years} years may be real "
                    f"({'; '.join(places)})"
                ),
                suggestion="Confirm these dates have been shifted or removed",
            )
        )
    remaining = len(findings) - MAX_DATE_WARNING_FILES
    if remaining > 0:
        result.issues.append(
            ValidationIssue(
                severity=Severity.WARNING,
                category=CheckCategory.PRIVACY,
                message=(
                    f"Dates within the last {lookback_years} years also found in "
                    f"{remaining} more file{'s' if remaining != 1 else ''} "
                    f"(showing first {MAX_DATE_WARNING_FILES})"
                ),
                suggestion="Confirm these dates have been shifted or removed",
            )
        )


def _check_sensitive_files(path: Path, config: ValidationConfig, result: CheckResult) -> None:
    """Check for sensitive configuration files that shouldn't be in the dataset."""
    for root, dirs, files in os.walk(path):
        # Filter out ignored directories
        dirs[:] = [d for d in dirs if not any(p in d for p in config.ignore_patterns)]

        for file in files:
            file_path = Path(root) / file
            relative_path = str(file_path.relative_to(path))

            # Skip ignored files
            if any(p in str(file_path) for p in config.ignore_patterns):
                continue

            # Check exact filename matches
            if file in SENSITIVE_FILES:
                result.issues.append(
                    ValidationIssue(
                        severity=Severity.ERROR,
                        category=CheckCategory.PRIVACY,
                        file=relative_path,
                        message=f"Sensitive file detected: {SENSITIVE_FILES[file]}",
                        suggestion=f"Remove '{file}' from the dataset before submission",
                    )
                )
                continue

            # Check file extensions for sensitive files
            for sensitive_name, description in SENSITIVE_FILES.items():
                # Check if it's an extension pattern (starts with .)
                if sensitive_name.startswith(".") and "." in file:
                    ext = "." + file.split(".")[-1]
                    if ext == sensitive_name:
                        result.issues.append(
                            ValidationIssue(
                                severity=Severity.ERROR,
                                category=CheckCategory.PRIVACY,
                                file=relative_path,
                                message=f"Sensitive file detected: {description}",
                                suggestion=f"Remove '{file}' from the dataset before submission",
                            )
                        )
                        break

            # Check for common patterns in filenames
            lower_file = file.lower()
            if any(keyword in lower_file for keyword in ["password", "secret", "token", "apikey", "api_key"]):
                result.issues.append(
                    ValidationIssue(
                        severity=Severity.WARNING,
                        category=CheckCategory.PRIVACY,
                        file=relative_path,
                        message=f"File name suggests sensitive content: '{file}'",
                        suggestion="Review file contents and remove if it contains credentials or keys",
                    )
                )


def _check_csv_privacy(
    csv_file: Path,
    base_path: Path,
    config: ValidationConfig,
    pattern_info: list,
    result: CheckResult,
    date_finder: Optional[RealisticDateFinder] = None,
    date_findings: Optional[list] = None,
) -> None:
    """Check a CSV file for privacy issues."""
    relative_path = str(csv_file.relative_to(base_path))
    date_ranges: Dict[Optional[str], DateRange] = {}

    # Report each pattern at most once per column, including malformed extra fields.
    phi_columns = {}  # {column: set of pattern names}
    age_columns = set()  # Columns with age violations
    layout = get_csv_layout(csv_file)

    try:
        if date_finder and layout.columns is not None and layout.metadata_rows:
            # Headerless layouts may keep start times in rows before the data.
            with open(csv_file, "r", encoding="utf-8", newline="") as f:
                for index, row in zip(range(layout.metadata_rows), csv.reader(f)):
                    names = layout.metadata_row_names
                    label = names[index] if index < len(names) else f"metadata row {index + 1}"
                    for value in row:
                        date_finder.scan(value, True, date_ranges, label)

        with open(csv_file, "r", encoding="utf-8") as f:
            reader = layout.dict_reader(f)

            # Determine if we should sample this file
            rows_scanned = 0
            max_rows = config.max_rows_to_scan

            # Count total rows first if we're sampling
            if config.sample_large_files and max_rows:
                # Read all rows into list to enable sampling
                all_rows = list(reader)
                total_rows = len(all_rows)

                if total_rows > max_rows:
                    # Sample evenly distributed rows
                    import random
                    random.seed(42)  # Deterministic sampling
                    step = total_rows / max_rows
                    sampled_indices = [int(i * step) for i in range(max_rows)]
                    rows_to_scan = [all_rows[i] for i in sampled_indices]
                    is_sampled = True
                else:
                    rows_to_scan = all_rows
                    is_sampled = False
            else:
                # No sampling, but still respect max_rows limit
                rows_to_scan = reader
                is_sampled = False

            for row in rows_to_scan:
                # Stop if we've hit the limit (when not sampling)
                if max_rows and not is_sampled and rows_scanned >= max_rows:
                    break
                rows_scanned += 1

                for raw_col, raw_value in row.items():
                    # DictReader stores surplus CSV fields under a None key.
                    col = raw_col if raw_col is not None else "extra field"
                    values = raw_value if isinstance(raw_value, list) else [raw_value]
                    for value in values:
                        if not value:
                            continue

                        value_str = str(value).strip()

                        if date_finder:
                            unix_allowed = raw_col is not None and (
                                raw_col in layout.unix_timestamp_columns
                                or is_time_like_column(raw_col)
                            )
                            date_finder.scan(value_str, unix_allowed, date_ranges, col)

                        for pattern, pattern_name in pattern_info:
                            if (pattern_name == "phone number"
                                    and raw_col in layout.unix_timestamp_columns
                                    and is_unix_timestamp(value_str)):
                                continue
                            if pattern.search(value_str):
                                phi_columns.setdefault(col, set()).add(pattern_name)

                        # Check for age violations (only track if not already found in this column)
                        if col not in age_columns and "age" in col.lower():
                            try:
                                age_value = float(value_str)
                                if age_value > config.allowed_age_max:
                                    age_columns.add(col)
                            except ValueError:
                                pass

        # Report one issue per column type with specific pattern info
        for col, pattern_names in phi_columns.items():
            for pattern_name in sorted(pattern_names):
                result.issues.append(
                    ValidationIssue(
                        severity=Severity.WARNING,
                        category=CheckCategory.PRIVACY,
                        file=relative_path,
                        column=col,
                        message=f"Potential private information detected in column '{col}' (pattern: {pattern_name})",
                        suggestion="Review and remove or de-identify sensitive information",
                    )
                )

        for col in age_columns:
            result.issues.append(
                ValidationIssue(
                    severity=Severity.WARNING,
                    category=CheckCategory.PRIVACY,
                    file=relative_path,
                    column=col,
                    message=f"Ages exceeding HIPAA limit of {config.allowed_age_max} found in column '{col}'",
                    suggestion=f"De-identify ages >{config.allowed_age_max} (e.g., set to {config.allowed_age_max}+)",
                )
            )

        if date_ranges and date_findings is not None:
            date_findings.append((relative_path, date_ranges))

    except Exception as e:
        result.issues.append(
            ValidationIssue(
                severity=Severity.WARNING,
                category=CheckCategory.PRIVACY,
                file=str(csv_file.relative_to(base_path)),
                message=f"Could not perform privacy checks: {str(e)}",
            )
        )


def _check_text_file_privacy(
    text_file: Path,
    base_path: Path,
    pattern_info: list,
    result: CheckResult,
    config: ValidationConfig,
    date_finder: Optional[RealisticDateFinder] = None,
    date_findings: Optional[list] = None,
) -> None:
    """Check a text file for privacy issues."""
    relative_path = str(text_file.relative_to(base_path))
    detected_patterns = set()
    date_ranges: Dict[Optional[str], DateRange] = {}

    try:
        with open(text_file, "r", encoding="utf-8") as f:
            content = f.read()

            # Check for PHI patterns and track which ones are found
            for line in content.split("\n"):
                for pattern, pattern_name in pattern_info:
                    if pattern.search(line):
                        detected_patterns.add(pattern_name)
                if date_finder:
                    # Free text has no column names, so Unix timestamps are not inferred.
                    date_finder.scan(line, False, date_ranges, None)

        if date_ranges and date_findings is not None:
            date_findings.append((relative_path, date_ranges))

        # Report once per file with specific patterns found
        if detected_patterns:
            patterns_str = ", ".join(sorted(detected_patterns))
            result.issues.append(
                ValidationIssue(
                    severity=Severity.WARNING,
                    category=CheckCategory.PRIVACY,
                    file=relative_path,
                    message=f"Potential private information detected ({patterns_str})",
                    suggestion="Review and remove or de-identify sensitive information",
                )
            )

    except UnicodeDecodeError:
        # Skip binary files
        pass
    except Exception as e:
        result.issues.append(
            ValidationIssue(
                severity=Severity.WARNING,
                category=CheckCategory.PRIVACY,
                file=str(text_file.relative_to(base_path)),
                message=f"Could not perform privacy checks: {str(e)}",
            )
        )
