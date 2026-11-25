"""Command-line interface for physionet package."""

import argparse
import getpass
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

from physionet.download import MAX_JOBS
from physionet.validate import validate_dataset, ValidationConfig

# Issues of one kind (such as date warnings) shown per category in the terminal.
TERMINAL_MAX_PER_CODE = 10


DEFAULT_BASE_URL = "https://physionet.org"


def _default_base_url():
    """The PhysioNet server to use when --base-url is not given."""
    return os.getenv("PHYSIONET_BASE_URL") or DEFAULT_BASE_URL


def _jobs(value):
    """Parse --jobs: a whole number from 1 to MAX_JOBS."""
    try:
        jobs = int(value)
    except ValueError:
        jobs = None
    if jobs is None or not 1 <= jobs <= MAX_JOBS:
        raise argparse.ArgumentTypeError(f"must be a whole number from 1 to {MAX_JOBS}, not {value!r}")
    return jobs


def _positive_int(value):
    """Parse a whole number of at least 1."""
    try:
        number = int(value)
    except ValueError:
        number = 0
    if number < 1:
        raise argparse.ArgumentTypeError(f"must be a whole number of at least 1, not {value!r}")
    return number


_AGE_UNITS = {"h": 1 / 24, "d": 1, "w": 7}


def _age(value):
    """Parse --older-than: a number of days, or a number followed by h, d or w."""
    from datetime import timedelta

    text = value.strip().lower()
    unit = text[-1] if text and text[-1] in _AGE_UNITS else "d"
    number = text[:-1] if text and text[-1] in _AGE_UNITS else text
    try:
        days = float(number) * _AGE_UNITS[unit]
    except ValueError:
        days = -1
    if not days >= 0 or days == float("inf"):
        raise argparse.ArgumentTypeError(f"must be an age such as 90d, 2w or 12h, not {value!r}")
    return timedelta(days=days)


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

    login_parser = subparsers.add_parser(
        "login", help="Sign in to PhysioNet in your browser, so downloads can use your account"
    )
    login_parser.add_argument(
        "--no-browser", action="store_true", help="Print the sign-in link without opening a browser"
    )
    logout_parser = subparsers.add_parser("logout", help="Sign out and delete the saved sign-in")
    whoami_parser = subparsers.add_parser("whoami", help="Show which PhysioNet account you are signed in as")
    for auth_parser in (login_parser, logout_parser, whoami_parser):
        auth_parser.add_argument(
            "--base-url", default=_default_base_url(),
            help=f"PhysioNet site (or PHYSIONET_BASE_URL; default: {DEFAULT_BASE_URL})",
        )
    access_parser = subparsers.add_parser(
        "access", help="Check your current access to a published dataset"
    )
    access_parser.add_argument("slug", help="Project identifier (e.g., mimiciv)")
    access_parser.add_argument("--version", default="latest", help="Project version (default: latest)")
    access_parser.add_argument("--username", help="PhysioNet username (or PHYSIONET_USERNAME)")
    access_parser.add_argument("--password", help="PhysioNet password (or PHYSIONET_PASSWORD)")
    access_parser.add_argument(
        "--base-url", default=_default_base_url(),
        help=f"PhysioNet server URL (or PHYSIONET_BASE_URL; default: {DEFAULT_BASE_URL})",
    )
    access_parser.add_argument("--json", action="store_true", help="Print JSON to stdout")

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
        help="Download source (default: auto; tries the AWS mirror first, for physionet.org only)",
    )
    download_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be downloaded without downloading",
    )
    download_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Download files again even if they are already saved with the right content",
    )
    download_parser.add_argument(
        "--username",
        help="PhysioNet username (or set PHYSIONET_USERNAME env var)",
    )
    download_parser.add_argument(
        "--password",
        help="PhysioNet password (or set PHYSIONET_PASSWORD env var)",
    )
    download_parser.add_argument(
        "-j", "--jobs", type=_jobs, default=1, metavar="N",
        help=f"Number of files to download at once, from 1 to {MAX_JOBS} (default: 1)",
    )
    download_parser.add_argument(
        "--base-url", default=_default_base_url(),
        help=f"PhysioNet server URL (or PHYSIONET_BASE_URL; default: {DEFAULT_BASE_URL})",
    )

    # Preview subcommands
    head_parser = subparsers.add_parser(
        "head", help="Show the first rows of a table, reading only the start of the file"
    )
    head_parser.add_argument("slug", help="Project identifier (e.g., mimic-iv-demo)")
    head_parser.add_argument("path", help="Table path, with or without the extension (e.g., hosp/patients)")
    head_parser.add_argument("-n", "--rows", type=_positive_int, default=10, metavar="N",
                             help="Number of rows (default: 10)")
    head_parser.add_argument("--csv", action="store_true", help="Print the rows as CSV")
    tables_parser = subparsers.add_parser(
        "tables", help="List a dataset's tables and their columns, reading only each header"
    )
    tables_parser.add_argument("slug", help="Project identifier (e.g., mimic-iv-demo)")
    tables_parser.add_argument("--column", help="Only list tables with this column (case-insensitive)")
    tables_parser.add_argument("--prefix", help="Only list tables under this folder (e.g., hosp)")
    tables_parser.add_argument("--json", action="store_true", help="Print JSON to stdout")
    for preview_parser in (head_parser, tables_parser):
        preview_parser.add_argument("--version", help="Project version (default: latest)")
        preview_parser.add_argument(
            "--source", choices=["auto", "physionet", "aws"], default="auto",
            help="Where to read from (default: auto)",
        )
        preview_parser.add_argument("--username", help="PhysioNet username (or PHYSIONET_USERNAME)")
        preview_parser.add_argument("--password", help="PhysioNet password (or PHYSIONET_PASSWORD)")
        preview_parser.add_argument(
            "--base-url", default=_default_base_url(),
            help=f"PhysioNet server URL (or PHYSIONET_BASE_URL; default: {DEFAULT_BASE_URL})",
        )

    # Doctor subcommand
    doctor_parser = subparsers.add_parser(
        "doctor", help="Check your setup (sign-in, server, cache) and, optionally, access to a dataset"
    )
    doctor_parser.add_argument("slug", nargs="?", help="Also check this dataset (e.g., mimiciv)")
    doctor_parser.add_argument("--version", help="Dataset version to check (default: latest)")
    doctor_parser.add_argument("--username", help="PhysioNet username (or PHYSIONET_USERNAME)")
    doctor_parser.add_argument("--password", help="PhysioNet password (or PHYSIONET_PASSWORD)")
    doctor_parser.add_argument(
        "--base-url", default=_default_base_url(),
        help=f"PhysioNet server URL (or PHYSIONET_BASE_URL; default: {DEFAULT_BASE_URL})",
    )
    doctor_parser.add_argument("--json", action="store_true", help="Print JSON to stdout")
    doctor_parser.add_argument(
        "--fix-permissions", action="store_true",
        help="Make restricted files in the cache readable only by you",
    )
    doctor_parser.add_argument(
        "--no-version-check", action="store_true", help="Do not ask PyPI for the latest release"
    )

    # Cache subcommand
    cache_parser = subparsers.add_parser(
        "cache", help="List or delete files kept by physionet.load(), fetch() and physionet:// paths"
    )
    cache_subparsers = cache_parser.add_subparsers(dest="cache_command", metavar="{list,clean,dir}")
    cache_list_parser = cache_subparsers.add_parser("list", help="List the dataset versions in the cache (default)")
    cache_list_parser.add_argument("slug", nargs="?", help="Only list this dataset")
    cache_list_parser.add_argument("--json", action="store_true", help="Print JSON to stdout")
    cache_clean_parser = cache_subparsers.add_parser("clean", help="Delete files from the cache")
    cache_clean_parser.add_argument("slug", nargs="?", help="Only delete files of this dataset")
    cache_clean_parser.add_argument("--version", help="Only delete files of this version (needs a dataset)")
    cache_clean_parser.add_argument(
        "--older-than", type=_age, metavar="AGE",
        help="Only delete files downloaded longer ago than this, e.g. 90d, 2w or 12h",
    )
    cache_clean_parser.add_argument("--dry-run", action="store_true", help="Show what would be deleted")
    cache_clean_parser.add_argument("-y", "--yes", action="store_true", help="Do not ask for confirmation")
    cache_dir_parser = cache_subparsers.add_parser("dir", help="Print the cache folder")
    for parser_ in (cache_parser, cache_list_parser, cache_clean_parser, cache_dir_parser):
        parser_.add_argument(
            "--cache-dir", default=argparse.SUPPRESS,
            help="Cache folder (default: PHYSIONET_CACHE, or ~/.cache/physionet)",
        )

    # Verify subcommand
    verify_parser = subparsers.add_parser(
        "verify", help="Check a downloaded copy of a dataset against its published checksums"
    )
    verify_parser.add_argument("path", help="Folder to check, e.g. one made by physionet download")
    verify_parser.add_argument(
        "slug", nargs="?", help="Project identifier (default: read from the folder name, e.g. mimiciv-3.1)"
    )
    verify_parser.add_argument("--version", help="Project version (default: read from the folder name)")
    verify_parser.add_argument(
        "--checksums", metavar="FILE", help="Check against this checksum file instead of the project's"
    )
    verify_parser.add_argument(
        "--include", action="append", help="Only check files matching this glob pattern (can be repeated)"
    )
    verify_parser.add_argument(
        "--exclude", action="append", help="Skip files matching this glob pattern (can be repeated)"
    )
    verify_parser.add_argument(
        "-j", "--jobs", type=_jobs, default=1, metavar="N",
        help=f"Number of files to read at once, from 1 to {MAX_JOBS} (default: 1)",
    )
    verify_parser.add_argument("--json", action="store_true", help="Print JSON to stdout")
    verify_parser.add_argument("--username", help="PhysioNet username (or PHYSIONET_USERNAME)")
    verify_parser.add_argument("--password", help="PhysioNet password (or PHYSIONET_PASSWORD)")
    verify_parser.add_argument(
        "--base-url", default=_default_base_url(),
        help=f"PhysioNet server URL (or PHYSIONET_BASE_URL; default: {DEFAULT_BASE_URL})",
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
    validate_parser.add_argument(
        "--no-duplicate-check",
        action="store_true",
        help="Skip hashing same-size files to check for duplicate content",
    )

    args = parser.parse_args()

    if args.command in ("search", "show", "versions"):
        return _handle_discovery(args)
    elif args.command in ("login", "logout", "whoami"):
        return _handle_auth(args)
    elif args.command == "access":
        return _handle_access(args)
    elif args.command == "download":
        return _handle_download(args)
    elif args.command in ("head", "tables"):
        return _handle_preview(args)
    elif args.command == "doctor":
        return _handle_doctor(args)
    elif args.command == "cache":
        return _handle_cache(args)
    elif args.command == "verify":
        return _handle_verify(args)
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
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def _handle_auth(args):
    """Sign in, sign out, or show the saved sign-in."""
    from physionet.auth import LoginError, TOKEN_ENV, credentials_path, load_saved_login, login, logout

    site = args.base_url.rstrip("/")
    if args.command == "login":
        try:
            username = login(site, open_browser=not args.no_browser)
        except LoginError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            print("\nSign-in cancelled.", file=sys.stderr)
            return 130
        account = f" as {username}" if username else ""
        print(f"Signed in to {site}{account}. Saved to {credentials_path()}")
        return 0

    if args.command == "logout":
        if logout(site):
            print(f"Signed out of {site}.")
        else:
            print(f"Not signed in to {site}.")
        return 0

    record = load_saved_login(site)
    if record is None:
        print(f"Not signed in to {site}. Run `physionet login` to sign in.")
    else:
        account = record.get("username") or "an unknown account"
        print(f"Signed in to {site} as {account}.")
    if os.getenv(TOKEN_ENV):
        print(f"{TOKEN_ENV} is set, so downloads use that token instead.")
    return 0


_DOCTOR_SYMBOLS = {"ok": "\u2713", "info": "\u2139", "warning": "\u26a0", "problem": "\u2717"}


def _handle_doctor(args):
    """Check the setup, print what is wrong and how to fix it."""
    from physionet.doctor import environment, fix_permissions, run_checks, summary

    if args.fix_permissions:
        changed = fix_permissions()
        for path in changed:
            print(f"Made readable only by you: {path}")
        print(f"Fixed {len(changed)} file(s)." if changed else "No restricted files needed fixing.")
        print()

    username = args.username or os.getenv("PHYSIONET_USERNAME")
    password = args.password or os.getenv("PHYSIONET_PASSWORD")
    if username and not password and sys.stdin.isatty() and not args.json:
        password = _prompt_password() or None
    try:
        checks = run_checks(
            base_url=args.base_url, slug=args.slug, version=args.version, username=username,
            password=password, check_pypi=not args.no_version_check,
        )
    except KeyboardInterrupt:
        return 130
    counts = summary(checks)
    env = environment()

    if args.json:
        print(json.dumps({"environment": env, "checks": [check.to_dict() for check in checks], **counts}))
        return 1 if counts["problems"] else 0

    print(f"physionet {env['physionet']} \u00b7 Python {env['python']} \u00b7 {env['os']}")
    section = None
    for check in checks:
        if check.section != section:
            section = check.section
            print(f"\n{section}")
        print(f"  {_DOCTOR_SYMBOLS.get(check.status, '-')} {check.message}")
        for detail in check.details:
            print(f"      {detail}")
        if check.fix and check.status != "ok":
            print(f"      Fix: {check.fix}")

    problems, warnings = counts["problems"], counts["warnings"]
    print()
    if not problems and not warnings:
        print("No problems found.")
    else:
        parts = []
        if problems:
            parts.append(f"{problems} problem{'s' if problems != 1 else ''}")
        if warnings:
            parts.append(f"{warnings} warning{'s' if warnings != 1 else ''}")
        print(", ".join(parts) + ".")
    return 1 if problems else 0


def _handle_preview(args):
    """Handle the head and tables subcommands."""
    import shutil

    import requests

    from physionet.api.exceptions import ForbiddenError, NotFoundError, PhysioNetAPIError
    from physionet.preview import head, tables

    username = args.username or os.getenv("PHYSIONET_USERNAME")
    password = args.password or os.getenv("PHYSIONET_PASSWORD")
    if username and not password:
        if not sys.stdin.isatty():
            print("PhysioNet password required: set PHYSIONET_PASSWORD for unattended use.", file=sys.stderr)
            return 1
        password = _prompt_password()
        if not password:
            print("No password entered; nothing was read.", file=sys.stderr)
            return 1
    options = dict(source=args.source, username=username, password=password, base_url=args.base_url)
    try:
        if args.command == "head":
            frame = head(args.slug, args.path, args.version, n=args.rows, **options)
            if args.csv:
                sys.stdout.write(frame.to_csv(index=False))
            elif frame.empty and not len(frame.columns):
                print("The table is empty.")
            else:
                width = shutil.get_terminal_size().columns
                print(frame.to_string(index=False, max_colwidth=40, line_width=width))
            return 0

        found = tables(args.slug, args.version, column=args.column, prefix=args.prefix, **options)
        if args.json:
            print(json.dumps(found.to_dict("records")))
        elif found.empty:
            where = f" under {args.prefix}" if args.prefix else ""
            if args.column:
                print(f"No tables in {args.slug}{where} have a column named {args.column}.", file=sys.stderr)
            else:
                print(f"No tables found in {args.slug}{where}.", file=sys.stderr)
        else:
            name_width = min(max(len(name) for name in found["table"]), 48)
            for row in found.itertuples(index=False):
                if isinstance(row.error, str):
                    detail = f"(could not read the header: {row.error})"
                elif not isinstance(row.columns, list):
                    detail = "(Parquet or Excel; use physionet.load() to read it)"
                else:
                    detail = ", ".join(row.columns)
                print(f"{row.table:<{name_width}}  {detail}")
        return 1 if found.empty and args.column else 0
    except KeyboardInterrupt:
        return 130
    except (FileNotFoundError, NotFoundError) as exc:
        print(f"Not found: {exc}", file=sys.stderr)
        return 1
    except ForbiddenError as exc:
        print(f"Access denied: {exc}", file=sys.stderr)
        print(f"See {args.base_url.rstrip('/')}/content/{args.slug}/ for access requirements.", file=sys.stderr)
        return 1
    except (ValueError, ConnectionError, PhysioNetAPIError, requests.RequestException) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def _prompt_password():
    """Prompt for a PhysioNet password; Ctrl-D counts as no password."""
    try:
        return getpass.getpass("PhysioNet password: ")
    except EOFError:
        print(file=sys.stderr)
        return ""


_UNAVAILABLE_REASONS = {
    "embargoed": "the files are under embargo",
    "region_blocked": "downloads are not available in your region",
    "downloads_disabled": "downloads are disabled for this project",
    "deprecated_files": "the files for this version are deprecated",
}


def _unavailable_message(access):
    """Describe why the server says files are unavailable, when it says why."""
    reason = _UNAVAILABLE_REASONS.get(access.reason or "")
    if reason:
        return f"PhysioNet file downloads: unavailable; {reason}"
    return "PhysioNet file downloads: unavailable; check the project page for details"


def _access_command(args):
    """The `physionet access` command for this slug, version and server."""
    import shlex

    parts = ["physionet", "access", args.slug]
    if args.version != "latest":
        parts += ["--version", args.version]
    if args.base_url.rstrip("/") != DEFAULT_BASE_URL:
        parts += ["--base-url", args.base_url]
    return " ".join(shlex.quote(part) for part in parts)


def _handle_access(args):
    """Show the server's current access decision and required steps."""
    import requests

    from physionet.api.client import PhysioNetClient
    from physionet.api.exceptions import NotFoundError, PhysioNetAPIError

    username = args.username or os.getenv("PHYSIONET_USERNAME")
    if args.password and not username:
        print(
            "--password needs --username (or PHYSIONET_USERNAME); access was not checked.",
            file=sys.stderr,
        )
        return 1
    password = args.password or os.getenv("PHYSIONET_PASSWORD")
    # The server only checks an account given by username and password, so the
    # saved sign-in is never sent here.
    client_options = dict(base_url=args.base_url, username=username, password=password, use_saved_login=False)
    try:
        if username and not password:
            if not sys.stdin.isatty():
                print(
                    "PhysioNet password required: set PHYSIONET_PASSWORD for unattended use.",
                    file=sys.stderr,
                )
                return 1
            password = _prompt_password()
            if not password:
                print("No password entered; access was not checked.", file=sys.stderr)
                return 1
            client_options["password"] = password
        with PhysioNetClient(**client_options) as client:
            access = client.projects.get_access_status(args.slug, args.version)
        if username and not access.authenticated:
            # The server answered as if no account was given, so the result
            # says nothing about this account. Fail as for a rejected password
            # rather than report anonymous access as the account's.
            print(
                "The server did not accept your username and password. Check them and try again.",
                file=sys.stderr,
            )
            return 1
        if args.json:
            print(json.dumps(asdict(access)))
        else:
            print(f"{access.slug} v{access.version} [{access.access_policy}]")
            if not access.authenticated:
                print("Access check: anonymous; your account access was not checked.")
                if access.can_download:
                    print("PhysioNet file downloads: available without signing in")
                elif access.status == "sign_in_required":
                    print(f"To check your account: {_access_command(args)} --username YOUR_USERNAME")
                elif access.status == "unavailable":
                    print(_unavailable_message(access))
                else:
                    print("PhysioNet file downloads: unavailable for this anonymous request")
                if not access.can_download:
                    print(f"Project page: {access.project_url}")
            elif access.can_download:
                print("PhysioNet file downloads: available to you")
            elif access.status == "unavailable":
                print(_unavailable_message(access))
            else:
                print("PhysioNet file downloads: not available to you yet")
            if access.authenticated and not access.can_download:
                for requirement in access.requirements:
                    if requirement.status != "complete":
                        print(f"  {requirement.label}: {requirement.status} — {requirement.url}")
                print(f"Project page: {access.project_url}")
        return 0
    except NotFoundError as exc:
        # The follow-up versions check runs inside this handler, so the
        # KeyboardInterrupt clause below would not see a Ctrl-C raised here.
        try:
            message = _access_not_found_message(args, client_options, exc)
        except KeyboardInterrupt:
            return 130
        print(message, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    except (PhysioNetAPIError, requests.RequestException) as exc:
        response = getattr(exc, "response", None)
        if username and response is not None and response.status_code == 401:
            print(
                "The server did not accept your username and password. Check them and try again.",
                file=sys.stderr,
            )
        else:
            print(f"Access status unavailable: {exc}", file=sys.stderr)
        return 1


def _access_not_found_message(args, client_options, error):
    """Explain a 404 from the access-status API.

    The API answers an unknown project or version with a JSON 404. A server
    without the API answers with its HTML 404 page, which looks the
    same as an unknown project, so check the versions list to tell them apart.
    """
    import requests

    from physionet.api.client import PhysioNetClient
    from physionet.api.exceptions import NotFoundError, PhysioNetAPIError

    from_api = False
    if error.response is not None:
        try:
            from_api = isinstance(error.response.json(), dict)
        except ValueError:
            pass
    try:
        with PhysioNetClient(**client_options) as client:
            versions = client.projects.list_versions(args.slug)
    except NotFoundError:
        return f"Project not found: {args.slug}."
    except (PhysioNetAPIError, requests.RequestException) as exc:
        return f"Access status not found ({error}), and the project could not be checked: {exc}"
    if not versions:
        return f"Project not found: {args.slug}."
    if args.version != "latest" and not any(item.version == args.version for item in versions):
        return f"Version {args.version} not found for {args.slug}."
    if from_api:
        return f"Access status not found for {args.slug}: {error}"
    return (
        "This PhysioNet server does not appear to provide the access-status API. "
        f"See {args.base_url.rstrip('/')}/content/{args.slug}/ for access requirements."
    )


def _handle_download(args):
    """Handle the download subcommand."""
    from physionet.download import DownloadError, download
    from physionet.api.exceptions import ForbiddenError, NotFoundError

    project_page = f"{args.base_url.rstrip('/')}/content/{args.slug}/"
    try:
        password = args.password or os.getenv("PHYSIONET_PASSWORD")
        if args.username and not password:
            if not sys.stdin.isatty():
                print(
                    "PhysioNet password required: set PHYSIONET_PASSWORD for unattended downloads.",
                    file=sys.stderr,
                )
                return 1
            password = _prompt_password()
            if not password:
                print("No password entered; nothing was downloaded.", file=sys.stderr)
                return 1
        download(
            slug=args.slug,
            version=args.version,
            output_dir=args.output,
            include=args.include,
            exclude=args.exclude,
            source=args.source,
            dry_run=args.dry_run,
            username=args.username,
            password=password,
            base_url=args.base_url,
            jobs=args.jobs,
            overwrite=args.overwrite,
        )
        return 0
    except DownloadError as e:
        print(f"Download incomplete: {e}", file=sys.stderr)
        if any(isinstance(cause, ForbiddenError) for _, cause in e.failures):
            print(
                f"Visit {project_page} for access requirements.",
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
            f"Visit {project_page} to request access.",
            file=sys.stderr,
        )
        return 1
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def _handle_cache(args):
    """List, clean, or locate the cache."""
    from physionet.api.utils import format_size
    from physionet.cache import clean_cache, list_cache
    from physionet.data import get_cache_dir

    cache_dir = getattr(args, "cache_dir", None)
    cache = get_cache_dir(cache_dir)
    command = args.cache_command or "list"
    if command == "dir":
        print(cache)
        return 0

    if command == "list":
        entries = list_cache(getattr(args, "slug", None), cache_dir=str(cache))
        if getattr(args, "json", False):
            print(json.dumps({"cache": str(cache), "versions": [entry.to_dict() for entry in entries]}))
            return 0
        if not entries:
            print(f"Cache: {cache} (empty)" if not getattr(args, "slug", None)
                  else f"{args.slug} is not in the cache at {cache}.")
            return 0
        files = sum(entry.files for entry in entries)
        size = sum(entry.size for entry in entries)
        print(f"Cache: {cache} ({len(entries)} dataset version(s), {files} file(s), {format_size(size)})")
        print()
        _print_cache_table(entries, format_size)
        unfinished = sum(entry.unfinished for entry in entries)
        if unfinished:
            print(f"\n{unfinished} unfinished download(s). `physionet cache clean` removes those "
                  "not changed in the last day.")
        return 0

    if args.version and not args.slug:
        print("Error: --version needs a dataset, e.g. physionet cache clean mimiciv --version 3.1", file=sys.stderr)
        return 1
    options = dict(slug=args.slug, version=args.version, older_than=args.older_than, cache_dir=str(cache))
    planned = clean_cache(**options, dry_run=True)
    what = args.slug + (f" {args.version}" if args.version else "") if args.slug else "the cache"
    age = f" downloaded more than {_describe_age(args.older_than)} ago" if args.older_than else ""
    if planned.files == 0 and planned.size == 0 and planned.unfinished == 0 and not planned.versions:
        print(f"Nothing to delete from {what}{age}.")
        # A dry run only fails to read folders, so these are folders it could not check.
        return _print_clean_errors(planned.errors, "could not be read")
    summary = _clean_summary(planned, format_size)
    if args.dry_run:
        print(f"Would delete {summary} from {what}{age} in {cache}.")
        _print_removed_versions(planned.versions, "Would remove")
        return _print_clean_errors(planned.errors, "could not be read")
    if not args.yes:
        if not sys.stdin.isatty():
            print(f"Would delete {summary} from {what}{age}. Pass --yes to delete without asking.",
                  file=sys.stderr)
            return 1
        try:
            answer = input(f"Delete {summary} from {what}{age} in {cache}? [y/N] ")
        except (EOFError, KeyboardInterrupt):
            print()
            return 130
        if answer.strip().lower() not in ("y", "yes"):
            print("Nothing was deleted.")
            return 0
    result = clean_cache(**options)
    print(f"Deleted {_clean_summary(result, format_size)}.")
    _print_removed_versions(result.versions, "Removed")
    return _print_clean_errors(result.errors, "could not be deleted or read")


def _print_clean_errors(errors, problem):
    """Print the paths clean_cache() could not handle; return the exit status."""
    if not errors:
        return 0
    print(f"{len(errors)} path(s) {problem}:", file=sys.stderr)
    for path, reason in errors[:10]:
        print(f"  {path}: {reason}", file=sys.stderr)
    if len(errors) > 10:
        print(f"  and {len(errors) - 10} more", file=sys.stderr)
    return 1


def _clean_summary(result, format_size):
    summary = f"{result.files} file(s), {format_size(result.size)}"
    if result.unfinished:
        summary += f", and {result.unfinished} unfinished download(s)"
    return summary


def _print_cache_table(entries, format_size):
    show_origin = any(entry.origin != "physionet.org" for entry in entries)
    rows = [
        (
            *((entry.origin,) if show_origin else ()),
            entry.slug,
            entry.version,
            str(entry.files),
            format_size(entry.size),
            entry.last_downloaded.astimezone().strftime("%Y-%m-%d") if entry.last_downloaded else "-",
            "restricted" if entry.restricted else "",
        )
        for entry in entries
    ]
    header = (*(("SERVER",) if show_origin else ()), "DATASET", "VERSION", "FILES", "SIZE", "DOWNLOADED", "")
    widths = [max(len(row[i]) for row in rows + [header]) for i in range(len(header))]
    numeric = {len(header) - 4, len(header) - 3}  # FILES and SIZE
    for row in [header] + rows:
        cells = [cell.rjust(width) if i in numeric else cell.ljust(width)
                 for i, (cell, width) in enumerate(zip(row, widths))]
        print("  ".join(cells).rstrip())


def _print_removed_versions(versions, verb):
    for entry in versions:
        print(f"  {verb} {entry.slug} {entry.version}"
              + (f" ({entry.origin})" if entry.origin != "physionet.org" else ""))


def _describe_age(age):
    if age.days and age.days % 7 == 0 and age.seconds == 0:
        return f"{age.days // 7} week(s)"
    if age.days and age.seconds == 0:
        return f"{age.days} day(s)"
    hours = age.total_seconds() / 3600
    return f"{hours:g} hour(s)"

# Files listed per problem in the terminal; --json lists them all.
VERIFY_MAX_LISTED = 20


def _handle_verify(args):
    """Check a folder against a project's checksums."""
    from physionet.api.exceptions import ForbiddenError, NotFoundError
    from physionet.verify import verify

    try:
        result = verify(
            args.path,
            args.slug,
            args.version,
            checksums=args.checksums,
            include=args.include,
            exclude=args.exclude,
            jobs=args.jobs,
            show_progress=not args.json,
            username=args.username,
            password=args.password or os.getenv("PHYSIONET_PASSWORD"),
            base_url=args.base_url,
        )
    except KeyboardInterrupt:
        return 130
    except ForbiddenError as exc:
        print(f"Access denied: {exc}", file=sys.stderr)
        return 1
    except (FileNotFoundError, NotFoundError, ValueError, ConnectionError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result.to_dict()))
        return 0 if result.passed else 1

    print(f"Checking {result.path} against the checksums from {result.source}")

    def listing(names):
        for name in names[:VERIFY_MAX_LISTED]:
            print(f"    {name}")
        if len(names) > VERIFY_MAX_LISTED:
            print(f"    and {len(names) - VERIFY_MAX_LISTED} more (--json lists them all)")

    checked = len(result.matched) + len(result.mismatched) + len(result.missing) + len(result.unreadable)
    if result.matched:
        print(f"✓ {len(result.matched)} of {checked} file(s) match")
    if result.mismatched:
        print(f"✗ {len(result.mismatched)} file(s) do not match (corrupt, changed or incomplete):")
        listing(result.mismatched)
    if result.missing:
        print(f"✗ {len(result.missing)} file(s) are missing:")
        listing(result.missing)
    if result.unreadable:
        print(f"✗ {len(result.unreadable)} file(s) could not be read:")
        listing([f"{name}: {error}" for name, error in result.unreadable])
    if result.extra:
        print(f"ℹ {len(result.extra)} file(s) are not in the checksum list:")
        listing(result.extra)
    if checked == 0:
        print("No files in the checksum list match --include and --exclude.")

    if result.passed:
        return 0
    if result.slug and result.path.resolve().name == f"{result.slug}-{result.version}":
        command = (f"physionet download {result.slug} --version {result.version} "
                   f"--output {_quote(str(result.path.resolve().parent))}")
        for pattern in args.include or []:
            command += f" --include {_quote(pattern)}"
        for pattern in args.exclude or []:
            command += f" --exclude {_quote(pattern)}"
        print(f"\nTo download missing and damaged files again:\n  {command}")
    return 1


def _quote(text):
    import shlex

    return shlex.quote(text)


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
    if args.no_duplicate_check:
        config.check_duplicate_files = False

    # Run validation
    try:
        print(f"Validating dataset: {dataset_path}")
        result = validate_dataset(str(dataset_path), config, show_progress=True)
        print()

        # Keep the terminal readable; the saved report below lists every issue.
        print(result.summary(max_per_code=TERMINAL_MAX_PER_CODE))

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
