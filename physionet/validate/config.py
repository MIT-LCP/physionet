"""Configuration for validation checks."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


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
    phi_patterns: List[str] = field(default_factory=lambda: [
        r"\b\d{3}-\d{2}-\d{4}\b",  # SSN pattern
        r"\b[\w\.-]+@[\w\.-]+\.\w+\b",  # Email pattern
        r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b",  # Phone pattern
    ])
