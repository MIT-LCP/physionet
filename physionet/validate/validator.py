"""Main validation logic."""

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from tqdm import tqdm

from physionet.validate.config import ValidationConfig
from physionet.validate.models import (
    ValidationResult,
    CheckResult,
    ValidationIssue,
    CheckCategory,
    Severity,
    DatasetStats,
)
from physionet.validate.checks import (
    check_filesystem,
    check_documentation,
    check_integrity,
    check_quality,
    check_privacy,
)


def validate_dataset(
    dataset_path: str,
    config: Optional[ValidationConfig] = None,
    show_progress: bool = True
) -> ValidationResult:
    """
    Validate a PhysioNet dataset before submission.

    Args:
        dataset_path: Path to the dataset directory
        config: Optional validation configuration. If None, uses defaults.
        show_progress: Whether to show progress bar. Default True.

    Returns:
        ValidationResult containing all validation issues and statistics

    Raises:
        ValueError: If dataset_path doesn't exist or isn't a directory
    """
    path = Path(dataset_path)
    if not path.exists():
        raise ValueError(f"Dataset path does not exist: {dataset_path}")
    if not path.is_dir():
        raise ValueError(f"Dataset path is not a directory: {dataset_path}")

    if config is None:
        config = ValidationConfig()

    # Initialize result
    result = ValidationResult(
        dataset_path=path.name,  # Use just the dataset folder name, not full path
        timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    )

    # Calculate dataset statistics
    result.dataset_stats = _calculate_stats(path, config)

    # Determine which checks to run
    checks_to_run = []
    if config.check_filesystem:
        checks_to_run.append(("Filesystem", CheckCategory.FILESYSTEM, check_filesystem))
    if config.check_documentation:
        checks_to_run.append(("Documentation", CheckCategory.DOCUMENTATION, check_documentation))
    if config.check_integrity:
        checks_to_run.append(("Integrity", CheckCategory.INTEGRITY, check_integrity))
    if config.check_quality:
        checks_to_run.append(("Quality", CheckCategory.QUALITY, check_quality))
    if config.check_phi:
        checks_to_run.append(("Privacy", CheckCategory.PRIVACY, check_privacy))

    # Run validation checks with progress bar
    if show_progress:
        progress_bar = tqdm(
            total=100,
            desc="Running validation checks",
            unit="%",
            leave=False,
            ncols=100,
            bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt}%"
        )

        steps_per_check = 100 // len(checks_to_run) if checks_to_run else 100

        for i, (name, category, check_func) in enumerate(checks_to_run):
            # Create a callback to update progress during this check
            def update_progress(msg: str):
                progress_bar.set_description(f"{name}: {msg}"[:80])

            progress_bar.set_description(f"{name}"[:80])

            # Call check function with progress callback if it supports it
            try:
                result.check_results[category] = check_func(path, config, progress_callback=update_progress)
            except TypeError:
                # Function doesn't support progress_callback parameter
                result.check_results[category] = check_func(path, config)

            # Update progress
            progress_bar.update(steps_per_check)

        progress_bar.close()
    else:
        for name, category, check_func in checks_to_run:
            # Try with progress_callback first, fall back to without
            try:
                result.check_results[category] = check_func(path, config, progress_callback=None)
            except TypeError:
                result.check_results[category] = check_func(path, config)

    return result


def _calculate_stats(path: Path, config: ValidationConfig) -> DatasetStats:
    """Calculate statistics about the dataset."""
    stats = DatasetStats()

    for root, dirs, files in os.walk(path):
        # Filter out ignored directories
        dirs[:] = [d for d in dirs if not _should_ignore(d, config.ignore_patterns)]

        stats.directory_count += len(dirs)

        for file in files:
            if _should_ignore(file, config.ignore_patterns):
                continue

            file_path = Path(root) / file
            try:
                stats.file_count += 1
                stats.total_size_bytes += file_path.stat().st_size
            except (OSError, PermissionError):
                # Skip files we can't access
                pass

    return stats


def _should_ignore(name: str, patterns: list) -> bool:
    """Check if a file or directory should be ignored."""
    for pattern in patterns:
        if pattern.startswith("*"):
            if name.endswith(pattern[1:]):
                return True
        elif pattern in name:
            return True
    return False
