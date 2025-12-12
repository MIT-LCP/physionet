"""Tests for CLI interface."""

import pytest
import json
import subprocess
import sys
from pathlib import Path


class TestValidateCLI:
    """Tests for the validate CLI command."""

    def test_cli_validates_directory(self, tmp_path):
        """Test that CLI can validate a directory."""
        # Create a minimal dataset
        readme = tmp_path / "README.md"
        readme.write_text("""# Test Dataset

## Background
Test background.

## Methods
Test methods.

## Data Description
Test data.

## Usage Notes
Test usage.

## References
Test references.
""")

        # Run CLI
        result = subprocess.run(
            [sys.executable, "-m", "physionet", "validate", str(tmp_path)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        assert "PhysioNet Dataset Validation Report" in result.stdout

    def test_cli_handles_nonexistent_path(self):
        """Test that CLI handles nonexistent paths gracefully."""
        result = subprocess.run(
            [sys.executable, "-m", "physionet", "validate", "/nonexistent/path"],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 1
        assert "does not exist" in result.stderr

    def test_cli_generates_json_report(self, tmp_path):
        """Test that CLI can generate JSON report."""
        # Create dataset
        readme = tmp_path / "README.md"
        readme.write_text("# Test")

        # Run CLI with --report
        report_file = tmp_path / "report.json"
        result = subprocess.run(
            [sys.executable, "-m", "physionet", "validate", str(tmp_path), "--report", str(report_file)],
            capture_output=True,
            text=True,
        )

        # Check that report was created
        assert report_file.exists()

        # Validate JSON structure
        with open(report_file) as f:
            report = json.load(f)

        assert "dataset_path" in report
        assert "timestamp" in report
        assert "summary" in report
        assert "checks" in report

    def test_cli_filters_by_check_category(self, tmp_path):
        """Test that CLI can filter checks by category."""
        readme = tmp_path / "README.md"
        readme.write_text("# Test")

        result = subprocess.run(
            [sys.executable, "-m", "physionet", "validate", str(tmp_path), "--checks", "filesystem"],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        # Should only show filesystem checks
        assert "Filesystem" in result.stdout or "filesystem" in result.stdout.lower()

    def test_cli_exits_with_error_on_validation_failure(self, tmp_path):
        """Test that CLI exits with error code when validation fails."""
        # Create dataset with PHI
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("patient_id,email\n1,test@example.com\n")

        result = subprocess.run(
            [sys.executable, "-m", "physionet", "validate", str(tmp_path)],
            capture_output=True,
            text=True,
        )

        # Should exit with error code due to validation errors
        assert result.returncode == 1

    def test_cli_shows_help(self):
        """Test that CLI shows help message."""
        result = subprocess.run(
            [sys.executable, "-m", "physionet", "--help"],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        assert "validate" in result.stdout

    def test_validate_subcommand_help(self):
        """Test that validate subcommand shows help."""
        result = subprocess.run(
            [sys.executable, "-m", "physionet", "validate", "--help"],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        assert "path" in result.stdout
        assert "--report" in result.stdout
        assert "--checks" in result.stdout
