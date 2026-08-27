"""Download PhysioNet datasets."""

import fnmatch
import hashlib
import os
from pathlib import Path
from typing import List, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from tqdm import tqdm
from urllib3.util.retry import Retry

from physionet.api.client import PhysioNetClient
from physionet.api.exceptions import ForbiddenError, NotFoundError
from physionet.api.utils import format_size, get_credentials_from_env

S3_BASE_URL = "https://physionet-open.s3.amazonaws.com"
S3_BUCKET = "physionet-open"


def download(
    slug: str,
    version: Optional[str] = None,
    output_dir: str = ".",
    include: Optional[List[str]] = None,
    exclude: Optional[List[str]] = None,
    source: str = "auto",
    dry_run: bool = False,
    username: Optional[str] = None,
    password: Optional[str] = None,
    base_url: str = "https://physionet.org",
    retries: int = 3,
) -> Path:
    """
    Download a PhysioNet project.

    Args:
        slug: Project identifier (e.g., "mimic-iv-demo")
        version: Version to download (default: latest)
        output_dir: Directory to save files to
        include: Glob patterns for files to include
        exclude: Glob patterns for files to exclude
        source: Download source - "auto", "physionet", or "aws"
        dry_run: If True, only print what would be downloaded
        username: PhysioNet username (or use PHYSIONET_USERNAME env var)
        password: PhysioNet password (or use PHYSIONET_PASSWORD env var)
        base_url: PhysioNet base URL
        retries: Number of retries for transient download errors (0 to disable)

    Returns:
        Path to the output directory

    Raises:
        NotFoundError: If the project or version is not found
        ForbiddenError: If access is denied
    """
    if username is None or password is None:
        env_user, env_pass = get_credentials_from_env()
        username = username or env_user
        password = password or env_pass

    client = PhysioNetClient(base_url=base_url, username=username, password=password)

    try:
        # Resolve version
        version = _resolve_version(client, slug, version)

        # Get file manifest
        files = _get_file_manifest(client, slug, version)

        # Filter files
        files = _filter_files(files, include=include, exclude=exclude)

        if not files:
            print("No files match the specified filters.")
            return Path(output_dir)

        # Select source
        source_type, source_base = _select_source(client, slug, version, source, username)

        if source_type == "s3":
            source_label = f"s3://{source_base}/{slug}/{version}"
        else:
            source_label = source_base

        # Prepare output directory
        dest = Path(output_dir) / f"{slug}-{version}"
        total_size = 0

        if dry_run:
            print(f"Project: {slug} v{version}")
            print(f"Source: {source_label}")
            print(f"Destination: {dest}")
            print(f"Files ({len(files)}):")
            for filepath, checksum in files:
                print(f"  {filepath}")
            return dest

        dest.mkdir(parents=True, exist_ok=True)

        # Download files
        print(f"Downloading {slug} v{version} ({len(files)} files)")
        print(f"Source: {source_label}")
        print(f"Destination: {dest}")
        print()

        skipped = 0
        downloaded = 0
        failed = 0

        progress = tqdm(files, desc="Overall", unit="file")
        try:
            for filepath, expected_hash in progress:
                progress.set_postfix_str(filepath, refresh=True)
                file_dest = dest / filepath
                file_dest.parent.mkdir(parents=True, exist_ok=True)

                # Skip files that already exist and pass checksum
                if file_dest.exists() and _verify_checksum(file_dest, expected_hash):
                    skipped += 1
                    continue

                try:
                    if source_type == "s3":
                        s3_key = f"{slug}/{version}/{filepath}"
                        size = _download_file_s3(source_base, s3_key, file_dest, expected_hash)
                    else:
                        file_url = f"{source_base}/{filepath}"
                        size = _download_file(file_url, file_dest, expected_hash, client.session, retries=retries)
                    total_size += size
                    downloaded += 1
                except KeyboardInterrupt:
                    raise
                except Exception as e:
                    print(f"\nError downloading {filepath}: {e}")
                    failed += 1
        except KeyboardInterrupt:
            progress.close()
            print()
            print(f"\nDownload interrupted. {downloaded} downloaded, {skipped} skipped before interruption.")
            return dest

        print()
        print(f"Complete: {downloaded} downloaded, {skipped} skipped, {failed} failed")
        if total_size > 0:
            print(f"Total downloaded: {format_size(total_size)}")

        return dest
    finally:
        client.close()


def _resolve_version(client: PhysioNetClient, slug: str, version: Optional[str]) -> str:
    """Resolve the version to download, defaulting to the latest."""
    versions = client.projects.list_versions(slug)

    if not versions:
        raise NotFoundError(f"No versions found for project '{slug}'")

    if version is None:
        # Return the latest version (last in the list)
        return versions[-1].version

    # Validate the requested version exists
    available = [v.version for v in versions]
    if version not in available:
        raise NotFoundError(f"Version '{version}' not found for project '{slug}'. Available: {', '.join(available)}")

    return version


def _get_file_manifest(client: PhysioNetClient, slug: str, version: str) -> List[Tuple[str, str]]:
    """
    Get the file manifest from SHA256SUMS.txt.

    Fetches the checksums file via the /files/ endpoint, which works
    without authentication for open-access projects.

    Returns:
        List of (filepath, sha256_hash) tuples
    """
    url = f"{client.base_url}/files/{slug}/{version}/SHA256SUMS.txt"
    response = client.session.get(url, timeout=client.timeout)
    response.raise_for_status()
    return _parse_manifest(response.text)


def _parse_manifest(text: str) -> List[Tuple[str, str]]:
    """
    Parse SHA256SUMS.txt content into (filepath, hash) tuples.

    Handles both formats:
    - <hash>  <filepath> (two spaces, standard sha256sum output)
    - <hash> <filepath> (single space)
    """
    files = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) == 2:
            checksum, filepath = parts
            files.append((filepath, checksum))
    return files


def _filter_files(
    files: List[Tuple[str, str]],
    include: Optional[List[str]] = None,
    exclude: Optional[List[str]] = None,
) -> List[Tuple[str, str]]:
    """Filter files based on include/exclude glob patterns."""
    result = files

    if include:
        result = [(f, h) for f, h in result if any(fnmatch.fnmatch(f, pat) for pat in include)]

    if exclude:
        result = [(f, h) for f, h in result if not any(fnmatch.fnmatch(f, pat) for pat in exclude)]

    return result


def _select_source(
    client: PhysioNetClient,
    slug: str,
    version: str,
    source: str,
    username: Optional[str],
) -> Tuple[str, str]:
    """
    Select the download source and return (source_type, source_base).

    Returns:
        A tuple of (source_type, source_base) where source_type is "http" or "s3",
        and source_base is the URL prefix (for HTTP) or bucket name (for S3).
    """
    if source == "physionet":
        return ("http", f"{client.base_url}/files/{slug}/{version}")

    if source == "aws":
        return ("s3", S3_BUCKET)

    # Auto-select: try S3 first, fall back to PhysioNet direct.
    # Not all projects are mirrored to S3, so we probe with a HEAD request.
    s3_check_url = f"{S3_BASE_URL}/{slug}/{version}/SHA256SUMS.txt"
    try:
        resp = requests.head(s3_check_url, timeout=5)
        if resp.status_code == 200:
            return ("http", f"{S3_BASE_URL}/{slug}/{version}")
    except requests.RequestException:
        pass

    return ("http", f"{client.base_url}/files/{slug}/{version}")


def _download_file_s3(
    bucket: str,
    key: str,
    dest: Path,
    expected_hash: str,
) -> int:
    """
    Download a single file from S3 using boto3 with checksum verification.

    Uses the standard AWS credential chain (env vars, ~/.aws/credentials, IAM roles, etc.).

    Returns:
        Number of bytes downloaded
    """
    import boto3

    s3 = boto3.client("s3")

    # Get object metadata for progress bar
    head = s3.head_object(Bucket=bucket, Key=key)
    total_size = head["ContentLength"]

    downloaded = 0
    try:
        with open(dest, "wb") as f:
            with tqdm(
                total=total_size,
                unit="B",
                unit_scale=True,
                desc=dest.name,
                leave=False,
            ) as pbar:

                def progress_callback(bytes_amount):
                    nonlocal downloaded
                    downloaded += bytes_amount
                    pbar.update(bytes_amount)

                s3.download_fileobj(bucket, key, f, Callback=progress_callback)
    except KeyboardInterrupt:
        if dest.exists():
            dest.unlink()
        raise

    # Verify checksum
    if not _verify_checksum(dest, expected_hash):
        dest.unlink()
        raise ValueError(f"Checksum verification failed for {dest.name}")

    return downloaded


def _download_file(
    url: str,
    dest: Path,
    expected_hash: str,
    session: requests.Session,
    retries: int = 3,
) -> int:
    """
    Download a single file with resume support and checksum verification.

    Args:
        retries: Number of retries for transient errors (0 to disable)

    Returns:
        Number of bytes downloaded
    """
    # Mount a retry adapter scoped to this download
    if retries > 0:
        retry_strategy = Retry(
            total=retries,
            backoff_factor=1,
            status_forcelist=[502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("https://", adapter)
        session.mount("http://", adapter)

    headers = {}
    mode = "wb"
    existing_size = 0

    # Resume support: if file partially exists, request remaining bytes
    if dest.exists():
        existing_size = dest.stat().st_size
        headers["Range"] = f"bytes={existing_size}-"
        mode = "ab"

    response = session.get(url, headers=headers, stream=True, timeout=60)

    # If we requested a range and got 416, file is already complete
    if response.status_code == 416:
        if _verify_checksum(dest, expected_hash):
            return 0
        # Hash mismatch — re-download from scratch
        existing_size = 0
        mode = "wb"
        response = session.get(url, stream=True, timeout=60)

    if response.status_code == 403:
        error_msg = response.text or "Access denied"
        raise ForbiddenError(error_msg)

    response.raise_for_status()

    # Get total size for progress bar
    content_length = response.headers.get("Content-Length")
    total = int(content_length) if content_length else None
    downloaded = 0

    try:
        with open(dest, mode) as f:
            with tqdm(
                total=total,
                initial=0,
                unit="B",
                unit_scale=True,
                desc=dest.name,
                leave=False,
            ) as pbar:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
                    downloaded += len(chunk)
                    pbar.update(len(chunk))
    except KeyboardInterrupt:
        if dest.exists():
            dest.unlink()
        raise

    # Verify checksum
    if not _verify_checksum(dest, expected_hash):
        dest.unlink()
        raise ValueError(f"Checksum verification failed for {dest.name}")

    return downloaded


def _verify_checksum(path: Path, expected_hash: str) -> bool:
    """Verify SHA256 checksum of a file."""
    sha256 = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest() == expected_hash
