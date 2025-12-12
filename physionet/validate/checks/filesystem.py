"""File system validation checks."""

import os
from pathlib import Path

from physionet.validate.models import CheckResult, ValidationIssue, CheckCategory, Severity
from physionet.validate.config import ValidationConfig

# Proprietary file formats and their recommended open alternatives
PROPRIETARY_FORMATS = {
    '.mat': 'MATLAB format; consider .csv, .zarr, .parquet, or .npy instead',
    '.sas7bdat': 'SAS format; consider .csv or .parquet instead',
    '.dta': 'Stata format; consider .csv or .parquet instead',
    '.sav': 'SPSS format; consider .csv or .parquet instead',
    '.xlsx': 'Excel format; consider .csv instead',
    '.xls': 'Excel format; consider .csv instead',
    '.rds': 'R binary format; consider .csv or .parquet instead',
    '.rdata': 'R binary format; consider .csv or .parquet instead',
    '.ppt': 'PowerPoint format; consider .pdf instead',
    '.pptx': 'PowerPoint format; consider .pdf instead',
}


def check_filesystem(path: Path, config: ValidationConfig) -> CheckResult:
    """
    Check file system organization and structure.

    Validates:
    - File naming conventions
    - Presence of version control artifacts
    - File sizes
    - Small file count

    Args:
        path: Path to dataset directory
        config: Validation configuration

    Returns:
        CheckResult with any filesystem issues found
    """
    result = CheckResult(category=CheckCategory.FILESYSTEM)

    # Check for version control artifacts
    for pattern in [".git", ".svn", ".hg", "__pycache__", ".pytest_cache"]:
        found_paths = list(path.rglob(pattern))
        if found_paths:
            result.issues.append(
                ValidationIssue(
                    severity=Severity.WARNING,
                    category=CheckCategory.FILESYSTEM,
                    message=f"Found version control/build artifacts: {pattern}",
                    suggestion=f"Remove {pattern} directories before submission",
                )
            )

    # Check for hidden and temp files
    for root, dirs, files in os.walk(path):
        # Filter ignored directories
        dirs[:] = [d for d in dirs if not any(p in d for p in config.ignore_patterns)]

        for file in files:
            file_path = Path(root) / file

            # Skip ignored files
            if any(p in file for p in config.ignore_patterns):
                continue

            # Check for hidden files (starting with .)
            if file.startswith(".") and file not in [".gitignore", ".gitattributes"]:
                result.issues.append(
                    ValidationIssue(
                        severity=Severity.WARNING,
                        category=CheckCategory.FILESYSTEM,
                        file=str(file_path.relative_to(path)),
                        message=f"Hidden file found: {file}",
                        suggestion="Remove hidden files before submission",
                    )
                )

            # Check for temp files
            if file.endswith(("~", ".tmp", ".bak", ".swp")):
                result.issues.append(
                    ValidationIssue(
                        severity=Severity.WARNING,
                        category=CheckCategory.FILESYSTEM,
                        file=str(file_path.relative_to(path)),
                        message=f"Temporary file found: {file}",
                        suggestion="Remove temporary files before submission",
                    )
                )

            # Check file size
            try:
                size = file_path.stat().st_size
                if size == 0:
                    result.issues.append(
                        ValidationIssue(
                            severity=Severity.WARNING,
                            category=CheckCategory.FILESYSTEM,
                            file=str(file_path.relative_to(path)),
                            message="Empty file (0 bytes)",
                            suggestion="Remove empty files or add content",
                        )
                    )
                elif config.max_file_size_bytes and size > config.max_file_size_bytes:
                    result.issues.append(
                        ValidationIssue(
                            severity=Severity.INFO,
                            category=CheckCategory.FILESYSTEM,
                            file=str(file_path.relative_to(path)),
                            message=f"Large file: {_format_size(size)}",
                            suggestion="Consider splitting or compressing large files",
                        )
                    )
            except (OSError, PermissionError):
                pass

            # Check for excessively long filenames
            # Most filesystems support 255 characters, but recommend shorter for compatibility
            if len(file) > 255:
                result.issues.append(
                    ValidationIssue(
                        severity=Severity.ERROR,
                        category=CheckCategory.FILESYSTEM,
                        file=str(file_path.relative_to(path)),
                        message=f"Filename exceeds maximum length ({len(file)} characters): {file[:50]}...",
                        suggestion="Shorten filename to 255 characters or less",
                    )
                )
            elif len(file) > 100:
                result.issues.append(
                    ValidationIssue(
                        severity=Severity.WARNING,
                        category=CheckCategory.FILESYSTEM,
                        file=str(file_path.relative_to(path)),
                        message=f"Filename is very long ({len(file)} characters): {file[:50]}...",
                        suggestion="Consider shortening filename for better compatibility (recommended: under 100 characters)",
                    )
                )

            # Check for spaces in filename
            if " " in file:
                result.issues.append(
                    ValidationIssue(
                        severity=Severity.WARNING,
                        category=CheckCategory.FILESYSTEM,
                        file=str(file_path.relative_to(path)),
                        message=f"Filename contains spaces: {file}",
                        suggestion="Replace spaces with underscores or hyphens",
                    )
                )

            # Check for invalid/awkward characters in filename
            # Include path separators, quotes, and other problematic characters
            invalid_chars = set('<>:"|?*/\\\'')
            found_invalid = [char for char in invalid_chars if char in file]

            if found_invalid:
                char_list = ", ".join(f"'{char}'" for char in found_invalid)
                result.issues.append(
                    ValidationIssue(
                        severity=Severity.ERROR,
                        category=CheckCategory.FILESYSTEM,
                        file=str(file_path.relative_to(path)),
                        message=f"Filename contains invalid characters ({char_list}): {file}",
                        suggestion="Remove special characters from filename (use only letters, numbers, underscores, hyphens, and periods)",
                    )
                )

            # Check for proprietary file formats
            file_ext = "." + file.split(".")[-1] if "." in file else ""
            file_ext_lower = file_ext.lower()

            if file_ext_lower in PROPRIETARY_FORMATS:
                result.issues.append(
                    ValidationIssue(
                        severity=Severity.WARNING,
                        category=CheckCategory.FILESYSTEM,
                        file=str(file_path.relative_to(path)),
                        message=f"Proprietary file format detected: {file}",
                        suggestion=f"{PROPRIETARY_FORMATS[file_ext_lower]}",
                    )
                )

    return result


def _format_size(size_bytes: int) -> str:
    """Format byte size as human-readable string."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} PB"
