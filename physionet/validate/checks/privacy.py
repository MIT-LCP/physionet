"""Privacy and PHI validation checks."""

import csv
import os
import re
from pathlib import Path
from typing import Optional, Callable

from physionet.validate.models import CheckResult, ValidationIssue, CheckCategory, Severity
from physionet.validate.config import ValidationConfig

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
    - Date patterns

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

    # Check CSV files
    csv_files = list(path.rglob("*.csv"))
    for i, csv_file in enumerate(csv_files):
        if progress_callback:
            progress_callback(f"Checking {csv_file.name} ({i+1}/{len(csv_files)} CSV files)")

        if any(p in str(csv_file) for p in config.ignore_patterns):
            continue

        _check_csv_privacy(csv_file, path, config, pattern_info, result)

    # Check text files for PHI
    text_files = list(path.rglob("*.txt"))
    for i, text_file in enumerate(text_files):
        if progress_callback:
            progress_callback(f"Checking {text_file.name} ({i+1}/{len(text_files)} text files)")

        if any(p in str(text_file) for p in config.ignore_patterns):
            continue

        _check_text_file_privacy(text_file, path, pattern_info, result, config)

    return result


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
    result: CheckResult
) -> None:
    """Check a CSV file for privacy issues."""
    relative_path = str(csv_file.relative_to(base_path))

    # Track which columns have which types of issues (to report only once per column)
    # Maps column name to the pattern name that matched
    phi_columns = {}  # {column: pattern_name}
    age_columns = set()  # Columns with age violations

    try:
        with open(csv_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)

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

            for line_num, row in enumerate(rows_to_scan, start=2):  # Start at 2 (after header)
                # Stop if we've hit the limit (when not sampling)
                if max_rows and not is_sampled and rows_scanned >= max_rows:
                    break
                rows_scanned += 1

                for col, value in row.items():
                    if not value:
                        continue

                    value_str = str(value).strip()

                    # Check for PHI patterns (only track if not already found in this column)
                    if col not in phi_columns:
                        for pattern, pattern_name in pattern_info:
                            if pattern.search(value_str):
                                phi_columns[col] = pattern_name
                                break

                    # Check for age violations (only track if not already found in this column)
                    if col not in age_columns and "age" in col.lower():
                        try:
                            age_value = float(value_str)
                            if age_value > config.allowed_age_max:
                                age_columns.add(col)
                        except ValueError:
                            pass

        # Report one issue per column type with specific pattern info
        for col, pattern_name in phi_columns.items():
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

    except Exception as e:
        result.issues.append(
            ValidationIssue(
                severity=Severity.WARNING,
                category=CheckCategory.PRIVACY,
                file=str(csv_file.relative_to(base_path)),
                message=f"Could not perform privacy checks: {str(e)}",
            )
        )


def _check_text_file_privacy(text_file: Path, base_path: Path, pattern_info: list, result: CheckResult, config: ValidationConfig) -> None:
    """Check a text file for privacy issues."""
    relative_path = str(text_file.relative_to(base_path))
    detected_patterns = set()

    try:
        with open(text_file, "r", encoding="utf-8") as f:
            content = f.read()

            # Check for PHI patterns and track which ones are found
            for line in content.split("\n"):
                for pattern, pattern_name in pattern_info:
                    if pattern.search(line):
                        detected_patterns.add(pattern_name)

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
