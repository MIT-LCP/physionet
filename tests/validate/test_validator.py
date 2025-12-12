"""Tests for main validation functionality."""

import pytest
import tempfile
from pathlib import Path

from physionet.validate import validate_dataset, ValidationConfig
from physionet.validate.models import Severity, CheckCategory


class TestValidateDataset:
    """Tests for validate_dataset function."""

    def test_nonexistent_path_raises_error(self):
        """Test that validating a nonexistent path raises ValueError."""
        with pytest.raises(ValueError, match="does not exist"):
            validate_dataset("/nonexistent/path")

    def test_file_instead_of_directory_raises_error(self, tmp_path):
        """Test that validating a file instead of directory raises ValueError."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("test")

        with pytest.raises(ValueError, match="not a directory"):
            validate_dataset(str(test_file))

    def test_empty_directory_validation(self, tmp_path):
        """Test validation of an empty directory."""
        result = validate_dataset(str(tmp_path))

        assert result.dataset_path == tmp_path.name
        assert result.timestamp is not None
        assert result.dataset_stats.file_count == 0
        assert result.dataset_stats.total_size_bytes == 0

        # Should have error for missing README.md
        assert result.total_errors == 1
        assert any("README.md" in str(issue.message) for issue in result.check_results[CheckCategory.DOCUMENTATION].issues)

    def test_minimal_valid_dataset(self, tmp_path):
        """Test validation of a minimal valid dataset."""
        # Create README and a simple CSV file
        (tmp_path / "README.md").write_text("# Test Dataset")
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("id,value\n1,100\n2,200\n")

        result = validate_dataset(str(tmp_path))

        assert result.dataset_stats.file_count == 2
        assert result.total_errors == 0

    def test_validation_with_custom_config(self, tmp_path):
        """Test validation with custom configuration."""
        # Create a dataset with custom requirements
        readme = tmp_path / "README.md"
        readme.write_text("# Test")

        config = ValidationConfig(
            check_filesystem=True,
            check_documentation=False,  # Disable documentation checks
            check_integrity=False,
            check_quality=False,
            check_phi=False,
        )

        result = validate_dataset(str(tmp_path), config)

        # Should only have filesystem checks
        assert CheckCategory.FILESYSTEM in result.check_results
        assert CheckCategory.DOCUMENTATION not in result.check_results

    def test_validation_without_progress_bar(self, tmp_path):
        """Test validation with progress bar disabled."""
        readme = tmp_path / "README.md"
        readme.write_text("# Test")

        # Should not raise any errors with show_progress=False
        result = validate_dataset(str(tmp_path), show_progress=False)
        assert result.total_errors == 0


class TestValidationStats:
    """Tests for dataset statistics calculation."""

    def test_calculates_file_count(self, tmp_path):
        """Test that file count is calculated correctly."""
        (tmp_path / "README.md").write_text("# Test")
        (tmp_path / "data.csv").write_text("col1,col2\n1,2\n")
        (tmp_path / "subdir").mkdir()
        (tmp_path / "subdir" / "data2.csv").write_text("col1\n1\n")

        result = validate_dataset(str(tmp_path))

        assert result.dataset_stats.file_count == 3
        assert result.dataset_stats.directory_count == 1

    def test_calculates_total_size(self, tmp_path):
        """Test that total size is calculated correctly."""
        content = "x" * 1000
        (tmp_path / "README.md").write_text(content)

        result = validate_dataset(str(tmp_path))

        assert result.dataset_stats.total_size_bytes >= 1000

    def test_ignores_specified_patterns(self, tmp_path):
        """Test that ignored patterns are not counted in stats."""
        (tmp_path / "README.md").write_text("# Test")
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "config").write_text("test")

        result = validate_dataset(str(tmp_path))

        # .git directory and its contents should be ignored
        assert result.dataset_stats.file_count == 1


class TestValidationResult:
    """Tests for ValidationResult model."""

    def test_summary_format(self, tmp_path):
        """Test that summary is properly formatted."""
        (tmp_path / "README.md").write_text("# Test")

        result = validate_dataset(str(tmp_path))
        summary = result.summary()

        assert "PhysioNet Dataset Validation Report" in summary
        assert tmp_path.name in summary
        assert "Summary:" in summary
        assert "Metadata:" in summary
        assert "Validation Results:" in summary

    def test_to_dict_format(self, tmp_path):
        """Test that to_dict produces valid structure."""
        (tmp_path / "README.md").write_text("# Test")

        result = validate_dataset(str(tmp_path))
        result_dict = result.to_dict()

        assert "dataset_path" in result_dict
        assert "timestamp" in result_dict
        assert "dataset_stats" in result_dict
        assert "summary" in result_dict
        assert "checks" in result_dict

        assert result_dict["summary"]["total_errors"] == result.total_errors
        assert result_dict["summary"]["total_warnings"] == result.total_warnings

    def test_recommendations_section(self, tmp_path):
        """Test that recommendations section is included when there are issues."""
        # Create files with issues to trigger recommendations
        (tmp_path / "file with spaces.csv").write_text("col1,col2\n1,2\n")
        (tmp_path / ".env").write_text("API_KEY=secret")
        (tmp_path / "empty.txt").write_text("")

        result = validate_dataset(str(tmp_path))
        summary = result.summary()

        # Should include recommendations section
        assert "Recommendations:" in summary
        assert "Replace spaces with underscores or hyphens" in summary
        assert "Remove" in summary  # Various remove recommendations

    def test_large_dataset_recommendation(self, tmp_path):
        """Test that large datasets get upload assistance recommendation."""
        # Create README to avoid documentation errors
        (tmp_path / "README.md").write_text("# Large Dataset")

        # Create a large file (simulated - we'll modify the stats)
        (tmp_path / "data.csv").write_text("col1,col2\n1,2\n")

        result = validate_dataset(str(tmp_path))

        # Manually set large size for testing (>200GB)
        result.dataset_stats.total_size_bytes = 250 * 1024 ** 3  # 250 GB

        summary = result.summary()

        # Should include contact recommendation for large datasets
        assert "contact@physionet.org" in summary
        assert "very large" in summary.lower()
        assert "250" in summary  # Should show the size
