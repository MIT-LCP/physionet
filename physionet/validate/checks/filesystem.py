"""File system validation checks."""

import hashlib
import os
from pathlib import Path
from typing import Dict, List

from physionet.validate.models import CheckResult, ValidationIssue, CheckCategory, Severity
from physionet.validate.config import ValidationConfig
from physionet.validate.checks.csv_formats import get_csv_layout

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

HASH_CHUNK_SIZE = 1024 * 1024
PREFIX_SIZE = 64 * 1024


def check_filesystem(path: Path, config: ValidationConfig) -> CheckResult:
    """
    Check file system organization and structure.

    Validates:
    - File naming conventions
    - Presence of version control artifacts
    - File sizes
    - Small file count
    - Byte-identical files

    Args:
        path: Path to dataset directory
        config: Validation configuration

    Returns:
        CheckResult with any filesystem issues found
    """
    result = CheckResult(category=CheckCategory.FILESYSTEM)
    files_by_size: Dict[int, List[Path]] = {}

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
        dirs[:] = [d for d in dirs if not _should_ignore(d, config.ignore_patterns)]

        for file in files:
            file_path = Path(root) / file

            # Skip ignored files
            if _should_ignore(file, config.ignore_patterns):
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
                if config.check_duplicate_files and size > 0 and not file_path.is_symlink() and file_path.is_file():
                    files_by_size.setdefault(size, []).append(file_path)
                if size == 0:
                    layout = get_csv_layout(file_path)
                    if layout.allow_empty:
                        result.issues.append(
                            ValidationIssue(
                                severity=Severity.INFO,
                                category=CheckCategory.FILESYSTEM,
                                file=str(file_path.relative_to(path)),
                                message=layout.empty_message or "Empty file is valid for this CSV layout",
                            )
                        )
                        continue
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
            except OSError as exc:
                if config.check_duplicate_files and not file_path.is_symlink():
                    _report_duplicate_check_error(path, file_path, result, exc)

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

    if config.check_duplicate_files:
        _check_duplicate_files(path, files_by_size, result)

    return result


def _should_ignore(name: str, patterns: list) -> bool:
    """Apply the validator's existing suffix and substring ignore rules."""
    return any(
        name.endswith(pattern[1:]) if pattern.startswith("*") else pattern in name
        for pattern in patterns
    )


def _sha256_file(file_path: Path) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as source:
        for chunk in iter(lambda: source.read(HASH_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_prefix(file_path: Path) -> str:
    with file_path.open("rb") as source:
        return hashlib.sha256(source.read(PREFIX_SIZE)).hexdigest()


def _report_duplicate_check_error(path: Path, file_path: Path, result: CheckResult, exc: OSError) -> None:
    result.issues.append(
        ValidationIssue(
            severity=Severity.WARNING,
            category=CheckCategory.FILESYSTEM,
            file=file_path.relative_to(path).as_posix(),
            message=f"Could not check file for duplicate content: {exc}",
        )
    )


def _check_duplicate_files(path: Path, files_by_size: Dict[int, List[Path]], result: CheckResult) -> None:
    """Compare size, then a prefix, then full SHA-256 before reporting duplicates."""
    for size in sorted(files_by_size):
        candidates = files_by_size[size]
        if len(candidates) < 2:
            continue

        files_by_prefix: Dict[str, List[Path]] = {}
        for file_path in sorted(candidates, key=lambda item: item.relative_to(path).as_posix()):
            try:
                prefix = _sha256_prefix(file_path)
            except OSError as exc:
                _report_duplicate_check_error(path, file_path, result, exc)
                continue
            files_by_prefix.setdefault(prefix, []).append(file_path)

        for prefix_matches in files_by_prefix.values():
            if len(prefix_matches) < 2:
                continue
            files_by_hash: Dict[str, List[Path]] = {}
            for file_path in prefix_matches:
                try:
                    digest = _sha256_file(file_path)
                except OSError as exc:
                    _report_duplicate_check_error(path, file_path, result, exc)
                    continue
                files_by_hash.setdefault(digest, []).append(file_path)

            for digest, matches in files_by_hash.items():
                first = matches[0].relative_to(path).as_posix()
                for duplicate in matches[1:]:
                    result.issues.append(
                        ValidationIssue(
                            severity=Severity.WARNING,
                            category=CheckCategory.FILESYSTEM,
                            file=duplicate.relative_to(path).as_posix(),
                            message=f"Identical content to {first} (SHA-256: {digest})",
                            suggestion="Remove accidental copies or document why both files are needed",
                            code="duplicate-file",
                        )
                    )


def _format_size(size_bytes: int) -> str:
    """Format byte size as human-readable string."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} PB"
