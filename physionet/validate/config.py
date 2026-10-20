"""Configuration for validation checks."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

MAX_LOOKBACK_YEARS = 1000


def validate_lookback_years(value) -> None:
    """Reject date windows that would contain no dates or cannot be represented."""
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_LOOKBACK_YEARS:
        raise ValueError(
            f"date_lookback_years must be a whole number from 1 to {MAX_LOOKBACK_YEARS}, "
            f"or None to turn the date check off (got {value!r})"
        )


@dataclass
class ValidationConfig:
    """Configuration for dataset validation."""

    # General settings
    check_filesystem: bool = True
    check_documentation: bool = True
    check_integrity: bool = True
    check_quality: bool = True
    check_phi: bool = True

    # File system settings
    max_file_size_bytes: Optional[int] = None  # None = no limit
    warn_small_files_threshold: int = 100  # Warn if more than this many small files
    check_duplicate_files: bool = True
    ignore_patterns: List[str] = field(default_factory=lambda: [
        ".git", ".gitignore", ".DS_Store", "__pycache__", "*.pyc", ".pytest_cache"
    ])

    # Documentation settings
    required_files: List[str] = field(default_factory=lambda: ["README.md"])
    recommended_readme_sections: List[str] = field(default_factory=list)

    # Performance settings
    max_rows_to_scan: Optional[int] = 10000  # Max rows to scan per CSV for privacy/quality checks (None = all rows)
    sample_large_files: bool = True  # If True, sample rows from large files instead of scanning all

    # Quality settings
    missing_value_threshold: float = 1.0  # Warn if column has 100% missing values
    value_ranges: Dict[str, Tuple[float, float]] = field(default_factory=dict)
    # Example: {"heart_rate": (20, 300), "temperature": (32, 43)}

    # Privacy settings
    allowed_age_max: int = 89
    # Warn about dates from this many years ago up to today, which may be real
    # rather than shifted. None turns the date check off.
    date_lookback_years: Optional[int] = 200
    phi_patterns: List[str] = field(default_factory=lambda: [
        r"\b\d{3}-\d{2}-\d{4}\b",  # SSN pattern
        r"\b[\w\.-]+@[\w\.-]+\.\w+\b",  # Email pattern
        r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b",  # Phone pattern
    ])

    def __post_init__(self) -> None:
        if self.date_lookback_years is not None:
            validate_lookback_years(self.date_lookback_years)
