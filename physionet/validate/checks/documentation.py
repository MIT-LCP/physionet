"""Documentation validation checks."""

from pathlib import Path

from physionet.validate.models import CheckResult, ValidationIssue, CheckCategory, Severity
from physionet.validate.config import ValidationConfig


def check_documentation(path: Path, config: ValidationConfig) -> CheckResult:
    """
    Check documentation completeness.

    Validates:
    - Required files exist (if any are specified in config)

    Args:
        path: Path to dataset directory
        config: Validation configuration

    Returns:
        CheckResult with any documentation issues found
    """
    result = CheckResult(category=CheckCategory.DOCUMENTATION)

    # Check for required files
    for required_file in config.required_files:
        file_path = path / required_file
        if not file_path.exists():
            # Customize suggestion for README.md
            if required_file == "README.md":
                suggestion = (
                    "Add README.md to your dataset. At minimum, the file should include "
                    "a title and a brief description of the package content."
                )
            else:
                suggestion = f"Add {required_file} to your dataset"

            result.issues.append(
                ValidationIssue(
                    severity=Severity.ERROR,
                    category=CheckCategory.DOCUMENTATION,
                    file=required_file,
                    message=f"Required file not found: {required_file}",
                    suggestion=suggestion,
                )
            )

    return result
