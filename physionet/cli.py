"""Command-line interface for physionet package."""

import argparse
import json
import sys
from pathlib import Path

from physionet.validate import validate_dataset, ValidationConfig


def main():
    """Main entry point for the CLI."""
    parser = argparse.ArgumentParser(
        prog="physionet",
        description="Tools for working with PhysioNet datasets",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Validate subcommand
    validate_parser = subparsers.add_parser(
        "validate",
        help="Validate a dataset before submission to PhysioNet",
    )
    validate_parser.add_argument(
        "path",
        help="Path to the dataset directory to validate",
    )
    validate_parser.add_argument(
        "--report",
        metavar="FILE",
        help="Generate detailed JSON report and save to FILE",
    )
    validate_parser.add_argument(
        "--checks",
        metavar="CATEGORIES",
        help="Comma-separated list of check categories to run (filesystem,documentation,integrity,quality,privacy)",
    )
    validate_parser.add_argument(
        "--level",
        choices=["error", "warning", "info"],
        default="info",
        help="Minimum severity level to display (default: info)",
    )
    validate_parser.add_argument(
        "--no-sampling",
        action="store_true",
        help="Disable sampling for large files (scan all rows, slower but more thorough)",
    )
    validate_parser.add_argument(
        "--max-rows",
        type=int,
        metavar="N",
        help="Maximum rows to scan per CSV file (default: 10000)",
    )

    args = parser.parse_args()

    if args.command == "validate":
        return _handle_validate(args)
    elif args.command is None:
        parser.print_help()
        return 0
    else:
        print(f"Unknown command: {args.command}", file=sys.stderr)
        return 1


def _handle_validate(args):
    """Handle the validate subcommand."""
    # Validate path
    dataset_path = Path(args.path)
    if not dataset_path.exists():
        print(f"Error: Path does not exist: {args.path}", file=sys.stderr)
        return 1

    if not dataset_path.is_dir():
        print(f"Error: Path is not a directory: {args.path}", file=sys.stderr)
        return 1

    # Configure validation
    config = ValidationConfig()

    # Parse check categories if specified
    if args.checks:
        categories = [c.strip().lower() for c in args.checks.split(",")]
        config.check_filesystem = "filesystem" in categories
        config.check_documentation = "documentation" in categories
        config.check_integrity = "integrity" in categories
        config.check_quality = "quality" in categories
        config.check_phi = "privacy" in categories

    # Configure sampling options
    if args.no_sampling:
        config.sample_large_files = False
    if args.max_rows:
        config.max_rows_to_scan = args.max_rows

    # Run validation
    try:
        print(f"Validating dataset: {dataset_path}")
        result = validate_dataset(str(dataset_path), config, show_progress=True)
        print()

        print(result.summary())

        # Save validation report - either to specified path or default location
        if args.report:
            report_path = Path(args.report)
            # Determine format based on file extension
            if report_path.suffix.lower() == '.json':
                # Save as JSON
                with open(report_path, "w", encoding="utf-8") as f:
                    json.dump(result.to_dict(), f, indent=2)
            else:
                # Save as Markdown
                with open(report_path, "w", encoding="utf-8") as f:
                    f.write(result.summary())
        else:
            # Default: save as Markdown in the root of the dataset folder
            report_path = dataset_path / "PHYSIONET_REPORT.md"
            with open(report_path, "w", encoding="utf-8") as f:
                f.write(result.summary())

        print()
        print(f"Validation report saved to: {report_path}")

        if result.status == "error":
            return 1
        elif result.status == "warning" and args.level == "error":
            return 0  # Warnings don't fail if level is error
        return 0

    except Exception as e:
        print(f"Error during validation: {str(e)}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
