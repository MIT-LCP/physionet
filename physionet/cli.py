"""Command-line interface for physionet package."""

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from physionet.validate import validate_dataset, ValidationConfig


def main():
    """Main entry point for the CLI."""
    parser = argparse.ArgumentParser(
        prog="physionet",
        description="Tools for working with PhysioNet datasets",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    search_parser = subparsers.add_parser("search", help="Search published datasets and other projects")
    search_parser.add_argument("query", help="Words to search for")
    search_parser.add_argument(
        "--resource-type", action="append", help="Filter by resource type (can be repeated)"
    )
    search_parser.add_argument("--json", action="store_true", help="Print JSON to stdout")

    show_parser = subparsers.add_parser("show", help="Show metadata for a project version")
    show_parser.add_argument("slug", help="Project identifier")
    show_parser.add_argument("--version", required=True, help="Project version")
    show_parser.add_argument("--json", action="store_true", help="Print JSON to stdout")

    versions_parser = subparsers.add_parser("versions", help="List versions of a project")
    versions_parser.add_argument("slug", help="Project identifier")
    versions_parser.add_argument("--json", action="store_true", help="Print JSON to stdout")

    cite_parser = subparsers.add_parser("cite", help="Get project and standard PhysioNet citations")
    cite_parser.add_argument("slug", help="Project identifier")
    cite_parser.add_argument("--version", help="Project version (default: latest)")
    citation_output = cite_parser.add_mutually_exclusive_group()
    citation_output.add_argument("--bibtex", action="store_true", help="Print BibTeX entries")
    citation_output.add_argument("--json", action="store_true", help="Print citation data as JSON")

    # Download subcommand
    download_parser = subparsers.add_parser(
        "download",
        help="Download a dataset from PhysioNet",
    )
    download_parser.add_argument(
        "slug",
        help="Project identifier (e.g., mimic-iv-demo)",
    )
    download_parser.add_argument(
        "--version",
        help="Project version to download (default: latest)",
    )
    download_parser.add_argument(
        "--output",
        default=".",
        help="Output directory (default: current directory)",
    )
    download_parser.add_argument(
        "--include",
        action="append",
        help="Glob pattern for files to include (can be repeated)",
    )
    download_parser.add_argument(
        "--exclude",
        action="append",
        help="Glob pattern for files to exclude (can be repeated)",
    )
    download_parser.add_argument(
        "--source",
        choices=["auto", "physionet", "aws"],
        default="auto",
        help="Download source (default: auto)",
    )
    download_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be downloaded without downloading",
    )
    download_parser.add_argument(
        "--username",
        help="PhysioNet username (or set PHYSIONET_USERNAME env var)",
    )
    download_parser.add_argument(
        "--password",
        help="PhysioNet password (or set PHYSIONET_PASSWORD env var)",
    )

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

    if args.command in ("search", "show", "versions", "cite"):
        return _handle_discovery(args)
    elif args.command == "download":
        return _handle_download(args)
    elif args.command == "validate":
        return _handle_validate(args)
    elif args.command is None:
        parser.print_help()
        return 0
    else:
        print(f"Unknown command: {args.command}", file=sys.stderr)
        return 1


def _handle_discovery(args):
    """Print project search results, metadata, or versions."""
    from physionet.api.client import PhysioNetClient
    from physionet.api.utils import format_size

    try:
        with PhysioNetClient() as client:
            if args.command == "search":
                projects = client.projects.search(args.query, resource_type=args.resource_type)
                if args.json:
                    print(json.dumps([asdict(project) for project in projects]))
                elif not projects:
                    print("No projects found.")
                else:
                    for project in projects:
                        access = project.access_policy or "Access unknown"
                        print(f"{project.slug} v{project.version}  {project.title}  [{access}]")
            elif args.command == "show":
                detail = client.projects.get_details(args.slug, args.version)
                if args.json:
                    print(json.dumps(asdict(detail)))
                else:
                    print(f"{detail.title} ({detail.slug} v{detail.version})")
                    print(f"Access: {detail.access_policy or 'Unknown'}")
                    print(f"Type: {detail.resource_type or 'Unknown'}")
                    print(f"License: {(detail.license or {}).get('name', 'Unknown')}")
                    print(f"DOI: {detail.doi or 'None'}")
                    print(f"Size: {format_size(detail.main_storage_size)}")
                    if detail.topics:
                        print(f"Topics: {', '.join(detail.topics)}")
                    if detail.source_url:
                        print(f"PhysioNet page: {detail.source_url}")
                    if detail.abstract:
                        print(f"\n{detail.abstract}")
            elif args.command == "versions":
                versions = client.projects.list_versions(args.slug)
                if args.json:
                    print(json.dumps([asdict(version) for version in versions]))
                elif not versions:
                    print(f"No versions found for {args.slug}.")
                else:
                    for version in versions:
                        print(f"{version.version}  {version.title}")
            elif args.command == "cite":
                citations = client.projects.get_citations(
                    args.slug, args.version, include_bibtex=args.bibtex or args.json
                )
                if args.bibtex:
                    if not citations.project.bibtex:
                        raise ValueError(
                            f"BibTeX is unavailable for {args.slug} v{citations.version}; "
                            f"see {citations.project.url}"
                        )
                    print("\n\n".join(
                        [citations.project.bibtex]
                        + [citation.bibtex for citation in citations.physionet]
                    ))
                elif args.json:
                    print(json.dumps(asdict(citations)))
                else:
                    print(f"Project citation ({args.slug} v{citations.version}):")
                    print(citations.project.text)
                    print("\nStandard PhysioNet citations:")
                    for number, citation in enumerate(citations.physionet, start=1):
                        print(f"{number}. {citation.text}")
                    print(f"\nSee {citations.project.url} for any additional publication citations.")
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def _handle_download(args):
    """Handle the download subcommand."""
    from physionet.download import DownloadError, download
    from physionet.api.exceptions import ForbiddenError, NotFoundError

    try:
        download(
            slug=args.slug,
            version=args.version,
            output_dir=args.output,
            include=args.include,
            exclude=args.exclude,
            source=args.source,
            dry_run=args.dry_run,
            username=args.username,
            password=args.password,
        )
        return 0
    except DownloadError as e:
        print(f"Download incomplete: {e}", file=sys.stderr)
        if any(isinstance(cause, ForbiddenError) for _, cause in e.failures):
            print(
                f"Visit https://physionet.org/content/{args.slug}/ for access requirements.",
                file=sys.stderr,
            )
        return 1
    except KeyboardInterrupt:
        return 130
    except NotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except ForbiddenError as e:
        print(f"Access denied: {e}", file=sys.stderr)
        print(
            f"Visit https://physionet.org/content/{args.slug}/ to request access.",
            file=sys.stderr,
        )
        return 1
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
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
