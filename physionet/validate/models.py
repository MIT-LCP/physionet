"""Data models for validation results."""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from enum import Enum
from datetime import datetime
import textwrap


class Severity(Enum):
    """Severity levels for validation issues."""
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class CheckCategory(Enum):
    """Categories of validation checks."""
    FILESYSTEM = "filesystem"
    DOCUMENTATION = "documentation"
    INTEGRITY = "integrity"
    QUALITY = "quality"
    PRIVACY = "privacy"


@dataclass
class ValidationIssue:
    """Represents a single validation issue."""
    severity: Severity
    category: CheckCategory
    message: str
    file: Optional[str] = None
    line: Optional[int] = None
    column: Optional[str] = None
    value: Optional[str] = None
    suggestion: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert issue to dictionary format."""
        result = {
            "severity": self.severity.value,
            "category": self.category.value,
            "message": self.message,
        }
        if self.file:
            result["file"] = self.file
        if self.line is not None:
            result["line"] = self.line
        if self.column:
            result["column"] = self.column
        if self.value:
            result["value"] = self.value
        if self.suggestion:
            result["suggestion"] = self.suggestion
        return result


@dataclass
class CheckResult:
    """Results from a specific category of checks."""
    category: CheckCategory
    issues: List[ValidationIssue] = field(default_factory=list)

    @property
    def status(self) -> str:
        """Get overall status for this check category."""
        if any(issue.severity == Severity.ERROR for issue in self.issues):
            return "error"
        elif any(issue.severity == Severity.WARNING for issue in self.issues):
            return "warning"
        return "pass"

    @property
    def error_count(self) -> int:
        """Count of errors in this category."""
        return sum(1 for issue in self.issues if issue.severity == Severity.ERROR)

    @property
    def warning_count(self) -> int:
        """Count of warnings in this category."""
        return sum(1 for issue in self.issues if issue.severity == Severity.WARNING)

    @property
    def info_count(self) -> int:
        """Count of info messages in this category."""
        return sum(1 for issue in self.issues if issue.severity == Severity.INFO)


@dataclass
class DatasetStats:
    """Statistics about the dataset being validated."""
    total_size_bytes: int = 0
    file_count: int = 0
    directory_count: int = 0


@dataclass
class ValidationResult:
    """Complete validation results for a dataset."""
    dataset_path: str
    timestamp: str
    check_results: Dict[CheckCategory, CheckResult] = field(default_factory=dict)
    dataset_stats: DatasetStats = field(default_factory=DatasetStats)

    @property
    def total_errors(self) -> int:
        """Total count of errors across all checks."""
        return sum(result.error_count for result in self.check_results.values())

    @property
    def total_warnings(self) -> int:
        """Total count of warnings across all checks."""
        return sum(result.warning_count for result in self.check_results.values())

    @property
    def total_info(self) -> int:
        """Total count of info messages across all checks."""
        return sum(result.info_count for result in self.check_results.values())

    @property
    def status(self) -> str:
        """Overall validation status."""
        if self.total_errors > 0:
            return "error"
        elif self.total_warnings > 0:
            return "warning"
        return "pass"

    def summary(self) -> str:
        """Generate a human-readable summary."""
        # Format timestamp as human-readable
        try:
            dt = datetime.fromisoformat(self.timestamp.replace('Z', '+00:00'))
            formatted_timestamp = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
        except (ValueError, AttributeError):
            formatted_timestamp = self.timestamp

        # Get package version
        try:
            import physionet
            validator_version = physionet.__version__
        except (ImportError, AttributeError):
            validator_version = "unknown"

        lines = []

        # Section 1: Metadata
        lines.extend([
            "PhysioNet Dataset Validation Report",
            "=" * 50,
            "",
            "Metadata:",
            f"  Dataset: {self.dataset_path}",
            f"  Validator version: {validator_version}",
            f"  Timestamp: {formatted_timestamp}",
            f"  Total size: {self._format_size(self.dataset_stats.total_size_bytes)} "
            f"({self.dataset_stats.file_count} files)",
            "",
        ])

        # Section 2: Validation Results
        lines.extend([
            "Validation Results:",
            "=" * 50,
        ])

        for category, result in self.check_results.items():
            # Only show ✗ for errors, ✓ for pass or warnings-only
            status_icon = "✗" if result.error_count > 0 else "✓"
            issue_summary = ""
            if result.error_count or result.warning_count:
                parts = []
                if result.error_count:
                    parts.append(f"{result.error_count} error{'s' if result.error_count != 1 else ''}")
                if result.warning_count:
                    parts.append(f"{result.warning_count} warning{'s' if result.warning_count != 1 else ''}")
                issue_summary = f" ({', '.join(parts)})"

            lines.append(f"{status_icon} {category.value.replace('_', ' ').title()}{issue_summary}")

            for issue in result.issues:
                icon = "✗" if issue.severity == Severity.ERROR else "⚠"
                location = f" {issue.file}"
                if issue.line:
                    location += f":{issue.line}"
                lines.append(f"  {icon}{location} - {issue.message}")

        lines.append("")

        # Section 3: Summary
        lines.extend([
            "Summary:",
            "=" * 50,
            f"{self.total_errors} error{'s' if self.total_errors != 1 else ''}, "
            f"{self.total_warnings} warning{'s' if self.total_warnings != 1 else ''}",
            "",
        ])

        if self.status == "error":
            lines.append("✗ Dataset has errors that must be fixed before submission")
        elif self.status == "warning":
            lines.append("⚠ Dataset has warnings that should be addressed before submission")
        else:
            lines.append("✓ Dataset passed validation")

        # Add recommendations section if there are issues
        recommendations = self._generate_recommendations()
        if recommendations:
            lines.extend([
                "",
                "Recommendations:",
                "=" * 50,
            ])
            lines.extend(recommendations)

        # Add note about including validation report in submission
        note_text = "Note: A validation report (PHYSIONET_REPORT.md) has been saved in your dataset folder. Please include this file in your final submission."
        lines.append("")
        lines.extend(self._wrap_text(note_text))

        return "\n".join(lines) + "\n"

    def to_dict(self) -> Dict[str, Any]:
        """Convert validation result to dictionary format."""
        return {
            "dataset_path": self.dataset_path,
            "timestamp": self.timestamp,
            "dataset_stats": {
                "total_size_bytes": self.dataset_stats.total_size_bytes,
                "file_count": self.dataset_stats.file_count,
                "directory_count": self.dataset_stats.directory_count,
            },
            "summary": {
                "total_errors": self.total_errors,
                "total_warnings": self.total_warnings,
                "total_info": self.total_info,
                "status": self.status,
            },
            "checks": {
                category.value: {
                    "status": result.status,
                    "issues": [issue.to_dict() for issue in result.issues],
                }
                for category, result in self.check_results.items()
            },
        }

    def _generate_recommendations(self) -> List[str]:
        """Generate actionable recommendations based on issues found."""
        recommendations = []

        # Check for very large datasets (>200GB)
        size_gb = self.dataset_stats.total_size_bytes / (1024 ** 3)
        if size_gb > 200:
            recommendations.append("\nDataset Size:")
            large_dataset_text = (
                f"  ℹ  Your dataset is very large ({self._format_size(self.dataset_stats.total_size_bytes)}). "
                "If you need assistance uploading large files, please contact the PhysioNet team at contact@physionet.org"
            )
            recommendations.extend(self._wrap_text(large_dataset_text, indent="     "))

        # Collect unique suggestions from all issues
        suggestions_by_category = {}

        for category, result in self.check_results.items():
            category_suggestions = {}

            for issue in result.issues:
                if issue.suggestion:
                    # Group by suggestion to avoid duplicates
                    if issue.suggestion not in category_suggestions:
                        category_suggestions[issue.suggestion] = {
                            'severity': issue.severity,
                            'count': 0
                        }
                    category_suggestions[issue.suggestion]['count'] += 1

            if category_suggestions:
                suggestions_by_category[category] = category_suggestions

        # Generate recommendations by category
        for category, suggestions in suggestions_by_category.items():
            if not suggestions:
                continue

            recommendations.append(f"\n{category.value.replace('_', ' ').title()}:")

            # Sort by severity (errors first) and then by count
            sorted_suggestions = sorted(
                suggestions.items(),
                key=lambda x: (x[1]['severity'] != Severity.ERROR, -x[1]['count'])
            )

            for suggestion, info in sorted_suggestions:
                count = info['count']
                icon = "✗" if info['severity'] == Severity.ERROR else "⚠"
                count_str = f" ({count} file{'s' if count != 1 else ''})" if count > 1 else ""
                suggestion_text = f"  {icon} {suggestion}{count_str}"
                # Wrap long suggestions
                wrapped = self._wrap_text(suggestion_text, indent="     ")
                recommendations.extend(wrapped)

        return recommendations

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        """Format byte size as human-readable string."""
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size_bytes < 1024.0:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.1f} PB"

    @staticmethod
    def _wrap_text(text: str, width: int = 80, indent: str = "      ") -> List[str]:
        """Wrap text to specified width with continuation indent."""
        # Use textwrap to wrap the text
        wrapped = textwrap.fill(text, width=width, subsequent_indent=indent)
        return wrapped.split('\n')
