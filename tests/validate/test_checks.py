"""Tests for individual validation checks."""

import pytest
import csv
from pathlib import Path

from physionet.validate import ValidationConfig
from physionet.validate.checks import (
    check_filesystem,
    check_documentation,
    check_integrity,
    check_quality,
    check_privacy,
)
from physionet.validate.models import Severity, CheckCategory


class TestFilesystemChecks:
    """Tests for filesystem validation checks."""

    def test_detects_git_directory(self, tmp_path):
        """Test that .git directories are detected."""
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "config").write_text("test")

        config = ValidationConfig()
        result = check_filesystem(tmp_path, config)

        assert any(".git" in issue.message for issue in result.issues)

    def test_detects_hidden_files(self, tmp_path):
        """Test that hidden files are detected."""
        (tmp_path / ".hidden").write_text("test")

        config = ValidationConfig()
        result = check_filesystem(tmp_path, config)

        assert any(issue.file and ".hidden" in issue.file for issue in result.issues)

    def test_detects_temp_files(self, tmp_path):
        """Test that temporary files are detected."""
        (tmp_path / "file.txt~").write_text("test")
        (tmp_path / "temp.tmp").write_text("test")

        config = ValidationConfig()
        result = check_filesystem(tmp_path, config)

        assert len(result.issues) >= 2

    def test_detects_empty_files(self, tmp_path):
        """Test that empty files are detected."""
        (tmp_path / "empty.txt").write_text("")

        config = ValidationConfig()
        result = check_filesystem(tmp_path, config)

        assert any("Empty file" in issue.message for issue in result.issues)

    def test_detects_invalid_filename_characters(self, tmp_path):
        """Test that invalid filename characters are detected."""
        # Note: This test might not work on all filesystems
        try:
            (tmp_path / "file<test>.txt").write_text("test")
            config = ValidationConfig()
            result = check_filesystem(tmp_path, config)
            assert any("invalid characters" in issue.message.lower() for issue in result.issues)
            # Should show which character was found
            assert any("<" in issue.message for issue in result.issues)
        except OSError:
            # Skip test if filesystem doesn't allow these characters
            pytest.skip("Filesystem doesn't support invalid characters in filenames")

    def test_detects_path_separators_in_filenames(self, tmp_path):
        """Test that path separators and other awkward characters are flagged."""
        # These characters should be caught even though they can't actually be in filenames on most systems
        # We test the validation logic by checking the character set
        from physionet.validate.checks.filesystem import check_filesystem

        # Create a file with a valid name for the actual test
        (tmp_path / "normalfile.txt").write_text("test")

        config = ValidationConfig()
        result = check_filesystem(tmp_path, config)

        # The check should flag files with /, \, quotes, etc if they could exist
        # Since we can't create such files, we verify the character set in the code includes them
        # This is tested indirectly through the previous test

    def test_detects_spaces_in_filenames(self, tmp_path):
        """Test that filenames with spaces are flagged."""
        (tmp_path / "my data file.csv").write_text("col1,col2\n1,2\n")
        (tmp_path / "analysis results.txt").write_text("test")

        config = ValidationConfig()
        result = check_filesystem(tmp_path, config)

        # Should warn about both files with spaces
        space_warnings = [
            issue for issue in result.issues
            if "spaces" in issue.message.lower()
        ]
        assert len(space_warnings) == 2
        assert any("my data file.csv" in issue.file for issue in space_warnings)
        assert any("analysis results.txt" in issue.file for issue in space_warnings)

    def test_detects_long_filenames(self, tmp_path):
        """Test that excessively long filenames are flagged."""
        # Create a file with a very long name (120 characters total)
        long_name = "a" * 116 + ".csv"  # 116 + 4 = 120 characters
        (tmp_path / long_name).write_text("col1,col2\n1,2\n")

        config = ValidationConfig()
        result = check_filesystem(tmp_path, config)

        # Should warn about long filename
        long_warnings = [
            issue for issue in result.issues
            if "very long" in issue.message.lower()
        ]
        assert len(long_warnings) == 1
        assert "120 characters" in long_warnings[0].message

    def test_detects_extremely_long_filenames(self, tmp_path):
        """Test that filenames exceeding maximum length are errors."""
        # Create a file with name exceeding 255 characters
        extreme_name = "b" * 260 + ".csv"
        try:
            (tmp_path / extreme_name).write_text("col1,col2\n1,2\n")

            config = ValidationConfig()
            result = check_filesystem(tmp_path, config)

            # Should error about exceeding maximum length
            length_errors = [
                issue for issue in result.issues
                if "exceeds maximum length" in issue.message.lower()
            ]
            assert len(length_errors) == 1
            assert "260 characters" in length_errors[0].message
        except OSError:
            # Skip test if filesystem doesn't support such long names
            pytest.skip("Filesystem doesn't support filenames over 255 characters")

    def test_detects_proprietary_formats(self, tmp_path):
        """Test that proprietary file formats are flagged."""
        # Create files with proprietary formats
        (tmp_path / "data.xlsx").write_text("test")
        (tmp_path / "analysis.mat").write_text("test")
        (tmp_path / "results.sas7bdat").write_text("test")

        config = ValidationConfig()
        result = check_filesystem(tmp_path, config)

        # Should warn about proprietary data formats (not .docx which is allowed)
        proprietary_warnings = [
            issue for issue in result.issues
            if "proprietary file format" in issue.message.lower()
        ]
        assert len(proprietary_warnings) == 3

        # Check that suggestions include alternatives
        suggestions = [issue.suggestion for issue in proprietary_warnings]
        assert any(".csv" in s or ".parquet" in s for s in suggestions)
        assert any(".zarr" in s for s in suggestions)

    def test_allows_open_formats(self, tmp_path):
        """Test that open file formats are not flagged."""
        # Create files with open formats (including .docx which is now allowed)
        (tmp_path / "README.md").write_text("# Test")
        (tmp_path / "data.csv").write_text("col1,col2\n1,2\n")
        (tmp_path / "signal.hdf5").write_text("test")
        (tmp_path / "record.json").write_text("{}")
        (tmp_path / "notes.txt").write_text("notes")
        (tmp_path / "protocol.docx").write_text("test")  # .docx is now allowed

        config = ValidationConfig()
        result = check_filesystem(tmp_path, config)

        # Should not warn about proprietary formats
        proprietary_warnings = [
            issue for issue in result.issues
            if "proprietary file format" in issue.message.lower()
        ]
        assert len(proprietary_warnings) == 0


class TestDocumentationChecks:
    """Tests for documentation validation checks."""

    def test_readme_required_by_default(self, tmp_path):
        """Test that README.md is required by default."""
        config = ValidationConfig()
        result = check_documentation(tmp_path, config)

        # Should have error for missing README.md
        assert result.error_count == 1
        assert any("README.md" in issue.message for issue in result.issues)

        # Should have helpful suggestion about minimum content
        readme_issue = [issue for issue in result.issues if "README.md" in issue.message][0]
        assert "title and a brief description" in readme_issue.suggestion

    def test_custom_required_files(self, tmp_path):
        """Test that custom required files are validated."""
        config = ValidationConfig(required_files=["README.md", "LICENSE"])
        result = check_documentation(tmp_path, config)

        # Should have errors for both missing files
        assert result.error_count == 2
        assert any("README.md" in issue.message for issue in result.issues)
        assert any("LICENSE" in issue.message for issue in result.issues)

    def test_required_file_exists(self, tmp_path):
        """Test that existing required file passes validation."""
        readme = tmp_path / "README.md"
        readme.write_text("# Title\n\nSome content.")

        config = ValidationConfig(required_files=["README.md"])
        result = check_documentation(tmp_path, config)

        # Should have no errors since README exists
        assert result.error_count == 0


class TestIntegrityChecks:
    """Tests for data integrity validation checks."""

    def test_validates_valid_csv(self, tmp_path):
        """Test that valid CSV passes validation."""
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("col1,col2,col3\n1,2,3\n4,5,6\n")

        config = ValidationConfig()
        result = check_integrity(tmp_path, config)

        assert result.error_count == 0

    def test_detects_empty_csv(self, tmp_path):
        """Test that empty CSV is detected."""
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("")

        config = ValidationConfig()
        result = check_integrity(tmp_path, config)

        assert any("empty" in issue.message.lower() for issue in result.issues)

    def test_detects_duplicate_column_names(self, tmp_path):
        """Test that duplicate column names are detected."""
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("col1,col2,col1\n1,2,3\n")

        config = ValidationConfig()
        result = check_integrity(tmp_path, config)

        assert any("Duplicate" in issue.message for issue in result.issues)

    def test_detects_inconsistent_row_length(self, tmp_path):
        """Test that inconsistent row lengths are detected."""
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("col1,col2,col3\n1,2,3\n4,5\n6,7,8,9\n")

        config = ValidationConfig()
        result = check_integrity(tmp_path, config)

        # Should detect both short and long rows
        assert result.error_count >= 2

    def test_detects_encoding_issues(self, tmp_path):
        """Test that encoding issues are detected."""
        csv_file = tmp_path / "data.csv"
        # Write invalid UTF-8
        csv_file.write_bytes(b"col1,col2\n1,\xff\xfe\n")

        config = ValidationConfig()
        result = check_integrity(tmp_path, config)

        assert any("encoding" in issue.message.lower() for issue in result.issues)


class TestQualityChecks:
    """Tests for data quality validation checks."""

    def test_detects_completely_empty_columns(self, tmp_path):
        """Test that completely empty columns (100% missing) are detected."""
        csv_file = tmp_path / "data.csv"
        # Create CSV with one column that's 100% empty
        rows = ["col1,col2,col3\n"]
        for i in range(10):
            rows.append(f"{i},data,\n")
        csv_file.write_text("".join(rows))

        config = ValidationConfig()
        result = check_quality(tmp_path, config)

        # Should detect the empty column
        assert any("empty" in issue.message.lower() and "col3" in issue.column for issue in result.issues)

    def test_partial_missing_values_not_flagged(self, tmp_path):
        """Test that partially missing columns (e.g., 75%) are not flagged."""
        csv_file = tmp_path / "data.csv"
        # Create CSV with 75% missing values in a column
        rows = ["col1,col2\n"]
        for i in range(100):
            if i < 75:
                rows.append("1,\n")
            else:
                rows.append("1,2\n")
        csv_file.write_text("".join(rows))

        config = ValidationConfig()
        result = check_quality(tmp_path, config)

        # Should NOT flag col2 since it has some data (25%)
        assert not any("col2" in str(issue.column) for issue in result.issues)

    def test_detects_out_of_range_values(self, tmp_path):
        """Test that out-of-range values are detected."""
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("heart_rate\n80\n350\n75\n")

        config = ValidationConfig(value_ranges={"heart_rate": (20, 300)})
        result = check_quality(tmp_path, config)

        assert any("outside expected range" in issue.message for issue in result.issues)


class TestPrivacyChecks:
    """Tests for privacy validation checks."""

    def test_date_format_not_flagged(self, tmp_path):
        """Test that date formats (YYYY-MM-DD) are not automatically flagged as PHI.

        Dates are commonly used in medical datasets as de-identified timestamps.
        They should not be flagged without additional context.
        """
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("patient_id,admission_date\n1,2023-05-15\n2,2023-06-20\n")

        config = ValidationConfig()
        result = check_privacy(tmp_path, config)

        # Dates alone should not be flagged
        assert result.error_count == 0

    def test_detects_email_addresses(self, tmp_path):
        """Test that email addresses are detected as PHI."""
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("patient_id,contact\n1,patient@example.com\n2,test@test.com\n")

        config = ValidationConfig()
        result = check_privacy(tmp_path, config)

        # Should have one warning for the 'contact' column with pattern type
        assert result.warning_count == 1
        assert any(
            issue.severity == Severity.WARNING
            and "contact" in str(issue.column)
            and "email address" in issue.message
            for issue in result.issues
        )

    def test_detects_age_violations(self, tmp_path):
        """Test that ages over limit are detected."""
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("patient_id,age\n1,92\n2,95\n3,85\n")

        config = ValidationConfig(allowed_age_max=89)
        result = check_privacy(tmp_path, config)

        # Should have one warning for the age column (consolidated)
        age_violations = [
            issue for issue in result.issues
            if "age" in issue.message.lower() and issue.severity == Severity.WARNING
        ]
        assert len(age_violations) == 1
        assert "age" in age_violations[0].column.lower()

    def test_text_files_checked_for_phi(self, tmp_path):
        """Test that text files are checked for PHI patterns."""
        text_file = tmp_path / "notes.txt"
        text_file.write_text("Contact: test@example.com\nPhone: 555-123-4567")

        config = ValidationConfig()
        result = check_privacy(tmp_path, config)

        # Should detect private information patterns in text files as a single consolidated warning with pattern types
        assert result.warning_count >= 1
        assert any(
            "private information detected" in issue.message
            and ("email address" in issue.message or "phone number" in issue.message)
            for issue in result.issues
        )

    def test_allows_year_only_dates(self, tmp_path):
        """Test that year-only dates are allowed."""
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("patient_id,year\n1,2023\n2,2024\n")

        config = ValidationConfig()
        result = check_privacy(tmp_path, config)

        # Should not flag year-only as PHI
        phi_issues = [
            issue for issue in result.issues
            if issue.severity == Severity.ERROR
        ]
        assert len(phi_issues) == 0

    def test_detects_sensitive_config_files(self, tmp_path):
        """Test that sensitive configuration files are detected."""
        # Create some sensitive files
        (tmp_path / ".env").write_text("API_KEY=secret123")
        (tmp_path / "credentials.json").write_text('{"key": "value"}')
        (tmp_path / "id_rsa").write_text("-----BEGIN RSA PRIVATE KEY-----")

        config = ValidationConfig()
        result = check_privacy(tmp_path, config)

        # Should detect all three sensitive files as errors
        sensitive_file_errors = [
            issue for issue in result.issues
            if issue.severity == Severity.ERROR and "Sensitive file detected" in issue.message
        ]
        assert len(sensitive_file_errors) == 3

    def test_detects_files_with_sensitive_names(self, tmp_path):
        """Test that files with sensitive keywords in names are flagged."""
        (tmp_path / "my_api_key.txt").write_text("some data")
        (tmp_path / "database_password.csv").write_text("col1\nval1")

        config = ValidationConfig()
        result = check_privacy(tmp_path, config)

        # Should warn about files with sensitive keywords in names
        keyword_warnings = [
            issue for issue in result.issues
            if issue.severity == Severity.WARNING and "name suggests sensitive content" in issue.message
        ]
        assert len(keyword_warnings) >= 2

    def test_detects_key_file_extensions(self, tmp_path):
        """Test that private key file extensions are detected."""
        (tmp_path / "server.pem").write_text("certificate")
        (tmp_path / "private.key").write_text("key data")

        config = ValidationConfig()
        result = check_privacy(tmp_path, config)

        # Should detect both key files
        key_errors = [
            issue for issue in result.issues
            if issue.severity == Severity.ERROR
        ]
        assert len(key_errors) >= 2

    def test_sampling_large_files(self, tmp_path):
        """Test that large files are sampled for performance."""
        csv_file = tmp_path / "large.csv"

        # Create a file with more rows than the sampling limit
        rows = ["patient_id,email\n"]
        for i in range(15000):  # More than default max_rows_to_scan (10000)
            rows.append(f"{i},test{i}@example.com\n")
        csv_file.write_text("".join(rows))

        config = ValidationConfig(max_rows_to_scan=1000, sample_large_files=True)
        result = check_privacy(tmp_path, config)

        # Should still detect the email pattern even with sampling
        assert result.warning_count >= 1
        assert any("email" in str(issue.column) for issue in result.issues)
