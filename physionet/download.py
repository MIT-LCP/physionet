"""Download PhysioNet datasets."""

import fnmatch
import hashlib
import re
import socket
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path, PurePosixPath
from typing import Callable, Dict, List, Optional, Tuple, Union

import requests
from requests.adapters import HTTPAdapter
from tqdm import tqdm
from urllib3.util.retry import Retry

from physionet.api.client import PhysioNetClient
from physionet.api.exceptions import ForbiddenError, NotFoundError
from physionet.api.utils import format_size
from physionet.auth import resolve_credentials

S3_BASE_URL = "https://physionet-open.s3.amazonaws.com"
S3_BUCKET = "physionet-open"
# The AWS open data mirror copies this server only, so source="auto" tries
# the mirror only when downloading from it.
MIRRORED_BASE_URL = "https://physionet.org"
_DOWNLOAD_ADAPTER_CACHE_ATTR = "_physionet_download_adapters"
# Most files that download() fetches at once. This stays below the default
# connection pool size (10), so parallel downloads reuse connections, and
# keeps the load on PhysioNet modest.
MAX_JOBS = 8


class _DownloadCancelled(Exception):
    """A parallel download was stopped because the whole download was interrupted."""


class _Cancellation:
    """Stop parallel downloads, including ones waiting on a slow server.

    Setting it stops each download at its next chunk. A download waiting for
    data that has not arrived would not reach its next chunk until the read
    timed out, so set() also shuts down the socket of each response being
    read, which ends a waiting read at once.
    """

    def __init__(self):
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._responses = set()

    def is_set(self) -> bool:
        return self._event.is_set()

    def set(self) -> None:
        with self._lock:
            self._event.set()
            responses = list(self._responses)
        for response in responses:
            _abort_response(response)

    def watch(self, response: requests.Response) -> None:
        """Track a response being read, or end it now if already cancelled."""
        with self._lock:
            if not self._event.is_set():
                self._responses.add(response)
                return
        _abort_response(response)

    def unwatch(self, response: requests.Response) -> None:
        with self._lock:
            self._responses.discard(response)


def _abort_response(response: requests.Response) -> None:
    """End any read in progress on a streamed response, from another thread.

    Closing the response does not wake a thread blocked reading it, but
    shutting down its socket does. The socket is found through http.client's
    response, so this is best effort: if it cannot be found, the download
    still stops at its next chunk.
    """
    fp = getattr(getattr(response.raw, "_fp", None), "fp", None)
    sock = getattr(getattr(fp, "raw", None), "_sock", None)
    if isinstance(sock, socket.socket):
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


def _progress_bar(*args, show_progress: bool = True, **kwargs) -> tqdm:
    """
    Create a tqdm progress bar, hidden if show_progress is False.

    When the bar is shown, `disable` is left unset, so that tqdm's own
    TQDM_DISABLE environment variable can still hide it.
    """
    if not show_progress:
        kwargs["disable"] = True
    return tqdm(*args, **kwargs)


class DownloadError(Exception):
    """One or more files failed while downloading a project."""

    def __init__(self, destination: Path, failures: List[Tuple[str, Exception]], downloaded: int, skipped: int):
        self.destination = destination
        self.failures = failures
        self.downloaded = downloaded
        self.skipped = skipped
        details = "; ".join(f"{path}: {error}" for path, error in failures)
        super().__init__(f"{len(failures)} file(s) failed ({details})")


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
    token: Optional[str] = None,
    retries: int = 3,
    jobs: int = 1,
    show_progress: bool = True,
    files: Optional[Union[List[str], Dict[str, str]]] = None,
    verbose: bool = True,
) -> Path:
    """
    Download a PhysioNet project, or some of its files.

    Args:
        slug: Project identifier (e.g., "mimic-iv-demo")
        version: Version to download (default: latest)
        output_dir: Directory to save files to
        include: Glob patterns for files to include
        exclude: Glob patterns for files to exclude
        source: Download source - "auto", "physionet", or "aws". "auto" uses the
            AWS open data mirror when the dataset is on it and base_url is
            https://physionet.org, and base_url otherwise.
        dry_run: If True, only print what would be downloaded
        username: PhysioNet username (or use PHYSIONET_USERNAME env var)
        password: PhysioNet password (or use PHYSIONET_PASSWORD env var)
        base_url: PhysioNet base URL
        token: PhysioNet access token (or use PHYSIONET_TOKEN env var).
            Without credentials, the sign-in saved by `physionet login` is used.
        retries: Number of retries for transient download errors (0 to disable)
        jobs: Number of files to download at once, from 1 to MAX_JOBS (8). With
            more than one, only the overall progress bar is shown.
        show_progress: Show progress bars (default True). The messages about
            the download (such as the summary at the end) are still printed.
        files: Download only these files, given as paths within the project
            (e.g. ["100.hea", "100.dat"]). They are looked up in the project's
            checksum list, and include and exclude still apply. A dict also
            says where to save each file, relative to output_dir, instead of
            under output_dir/<slug>-<version> (e.g. {"data/100.hea": "100.hea"}).
        verbose: Print messages about the download, such as where files are
            saved and the summary at the end (default True). Errors are
            printed either way.

    Returns:
        Path to the folder the files were saved in: output_dir/<slug>-<version>,
        or output_dir if files is a dict

    Raises:
        NotFoundError: If the project or version is not found
        ForbiddenError: If access is denied before file transfers start
        DownloadError: If one or more file transfers fail, including access denial
        KeyboardInterrupt: If the download is interrupted
        FileNotFoundError: If the project has no file named in files
        ValueError: If jobs is not between 1 and MAX_JOBS, or if files is a dict
            that saves a file outside output_dir or two files in one place
    """
    if isinstance(jobs, bool) or not isinstance(jobs, int) or not 1 <= jobs <= MAX_JOBS:
        raise ValueError(f"jobs must be a whole number from 1 to {MAX_JOBS}, not {jobs!r}")
    # Where to save each file, relative to the destination, if not its path
    saved_as = _check_saved_as(files) if isinstance(files, dict) else None
    say = print if verbose else _say_nothing
    username, password, token = resolve_credentials(username, password, token)
    client = PhysioNetClient(base_url=base_url, username=username, password=password, token=token)

    try:
        # Resolve version
        version = _resolve_version(client, slug, version)

        # Get file manifest
        requested = files
        try:
            files = _get_file_manifest(client, slug, version, retries)
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code in (401, 403):
                raise ForbiddenError(
                    f"Cannot access {slug} v{version} files. Sign in with `physionet login` (or check "
                    "your credentials), and check the project's access requirements."
                ) from exc
            raise

        if requested is not None:
            files = _select_files(files, requested, slug, version)

        # Filter files
        files = _filter_files(files, include=include, exclude=exclude)

        if not files:
            say("No files match the specified filters.")
            return Path(output_dir)

        # Select source
        source_type, source_base = _select_source(client, slug, version, source, username, verbose=verbose)

        if source_type == "s3":
            source_label = f"s3://{source_base}/{slug}/{version}"
        else:
            source_label = source_base

        # Prepare output directory
        dest = Path(output_dir) if saved_as is not None else Path(output_dir) / f"{slug}-{version}"
        total_size = 0

        def local_path(filepath: str) -> str:
            return saved_as[filepath] if saved_as is not None else filepath

        # A checksum list could name paths outside the project folder (from a
        # compromised or untrusted server). Never write those.
        unsafe = _unsafe_paths(files, dest, local_path)
        if unsafe:
            print(
                f"Not downloading {len(unsafe)} file(s) whose paths would be written outside "
                f"{dest}: {', '.join(repr(path) for path in unsafe)}",
                file=sys.stderr,
            )
            unsafe_set = set(unsafe)
            files = [entry for entry in files if entry[0] not in unsafe_set]

        if dry_run:
            print(f"Project: {slug} v{version}")
            print(f"Source: {source_label}")
            print(f"Destination: {dest}")
            print(f"Files ({len(files)}):")
            for filepath, checksum in files:
                saved = f" -> {local_path(filepath)}" if saved_as is not None else ""
                print(f"  {filepath}{saved}")
            return dest

        dest.mkdir(parents=True, exist_ok=True)

        # Download files
        say(f"Downloading {slug} v{version} ({len(files)} files)")
        say(f"Source: {source_label}")
        say(f"Destination: {dest}")
        say()

        skipped = 0
        downloaded = 0
        failures = [
            (path, ValueError("path is outside the project folder; not downloaded")) for path in unsafe
        ]
        download_prefix = None
        if source_type == "http":
            download_prefix = f"{source_base.rstrip('/')}/"
            _configure_retry_adapter(client.session, download_prefix, retries, adapter_prefix=download_prefix)

        def fetch(filepath: str, expected_hash: str, **options) -> Optional[int]:
            """Download one file; return its size, or None if it was already there."""
            file_dest = dest / local_path(filepath)
            file_dest.parent.mkdir(parents=True, exist_ok=True)

            # Skip files that already exist and pass checksum
            if file_dest.exists() and _verify_checksum(file_dest, expected_hash):
                return None

            if source_type == "s3":
                s3_key = f"{slug}/{version}/{filepath}"
                return _download_file_s3(source_base, s3_key, file_dest, expected_hash, **options)
            file_url = f"{source_base}/{filepath}"
            return _download_file(
                file_url,
                file_dest,
                expected_hash,
                client.session,
                retries=retries,
                adapter_prefix=download_prefix,
                **options,
            )

        def record(filepath: str, size: Optional[int]) -> None:
            nonlocal skipped, downloaded, total_size
            if size is None:
                skipped += 1
            else:
                total_size += size
                downloaded += 1

        def report_failure(filepath: str, error: Exception) -> None:
            print(f"\nError downloading {filepath}: {error}", file=sys.stderr)
            failures.append((filepath, error))

        try:
            if jobs == 1:
                progress = _progress_bar(files, desc="Overall", unit="file", show_progress=show_progress)
                try:
                    for filepath, expected_hash in progress:
                        progress.set_postfix_str(filepath, refresh=True)
                        try:
                            size = fetch(filepath, expected_hash, show_progress=show_progress)
                        except KeyboardInterrupt:
                            raise
                        except Exception as e:
                            report_failure(filepath, e)
                        else:
                            record(filepath, size)
                finally:
                    progress.close()
            else:
                _download_in_parallel(files, fetch, record, report_failure, jobs, source_type, show_progress)
        except KeyboardInterrupt:
            print(
                f"\nDownload interrupted. {downloaded} downloaded, {skipped} skipped before interruption.",
                file=sys.stderr,
            )
            raise

        # Parallel downloads finish in any order; report failures in manifest order.
        # Failures recorded before downloading (paths not in `files`) stay first.
        order = {filepath: index for index, (filepath, _) in enumerate(files)}
        failures.sort(key=lambda failure: order.get(failure[0], -1))

        say()
        outcome = "Incomplete" if failures else "Complete"
        say(f"{outcome}: {downloaded} downloaded, {skipped} skipped, {len(failures)} failed")
        if total_size > 0:
            say(f"Total downloaded: {format_size(total_size)}")

        if failures:
            raise DownloadError(dest, failures, downloaded, skipped)

        return dest
    finally:
        client.close()


def _is_safe_path(path: str) -> bool:
    """Whether a path from a checksum list stays inside the project folder.

    Rejects absolute paths, "." and ".." parts, empty parts and backslashes.
    """
    parts = PurePosixPath(path).parts
    return bool(parts) and "\\" not in path and not PurePosixPath(path).is_absolute() and all(
        part not in ("", ".", "..") for part in parts
    )


def _unsafe_paths(
    files: List[Tuple[str, str]], dest: Path, local_path: Callable[[str], str] = lambda path: path
) -> List[str]:
    """Paths in the checksum list that would be written outside dest.

    A path is unsafe if it, or the path it is saved as (local_path), is not a
    plain relative path, or if it resolves outside dest, for example through a
    symbolic link inside dest.
    """
    root = dest.resolve()
    unsafe = []
    for filepath, _ in files:
        local = local_path(filepath)
        if (
            not _is_safe_path(filepath)
            or not _is_safe_path(local)
            or not (dest / local).resolve().is_relative_to(root)
        ):
            unsafe.append(filepath)
    return unsafe


def _check_saved_as(saved_as: Dict[str, str]) -> Dict[str, str]:
    """Check the paths that download(files={...}) saves files as."""
    unsafe = [local for local in saved_as.values() if not _is_safe_path(local)]
    if unsafe:
        raise ValueError(
            "Files can only be saved inside output_dir, as relative paths without '..': "
            + ", ".join(repr(local) for local in unsafe[:5])
        )
    seen: Dict[str, str] = {}
    for filepath, local in saved_as.items():
        if local in seen:
            raise ValueError(f"{seen[local]!r} and {filepath!r} would both be saved as {local!r}")
        seen[local] = filepath
    return saved_as


def _select_files(
    manifest: List[Tuple[str, str]], requested: Union[List[str], Dict[str, str]], slug: str, version: str
) -> List[Tuple[str, str]]:
    """The checksum list entries of the requested files, in the order requested."""
    checksums = dict(manifest)
    wanted = list(dict.fromkeys(requested))
    missing = [path for path in wanted if path not in checksums]
    if missing:
        shown = ", ".join(repr(path) for path in missing[:5])
        more = f" and {len(missing) - 5} more" if len(missing) > 5 else ""
        raise FileNotFoundError(f"{slug} v{version} has no file {shown}{more}")
    return [(path, checksums[path]) for path in wanted]


def _say_nothing(*args, **kwargs) -> None:
    """Stand in for print() when download(verbose=False)."""


def _download_in_parallel(
    files, fetch, record, report_failure, jobs: int, source_type: str, show_progress: bool = True
) -> None:
    """Run fetch for each file on up to `jobs` threads, recording results as they finish.

    The requests session (and its saved sign-in refresh) is shared by the
    threads. boto3 clients are thread-safe but creating them is not, so S3
    downloads share one client. Per-file progress bars are hidden, since
    several at once would overwrite each other. On Ctrl-C, files not started
    are cancelled and running ones stop straight away, even while waiting on
    a slow server. A partial HTTP download is kept, so a later download
    resumes it; a partial S3 download is downloaded again in full.
    """
    cancel = _Cancellation()
    options = {"show_progress": False, "cancel": cancel}
    if source_type == "s3":
        import boto3

        options["s3_client"] = boto3.client("s3")

    executor = ThreadPoolExecutor(max_workers=jobs, thread_name_prefix="physionet-download")
    progress = _progress_bar(total=len(files), desc="Overall", unit="file", show_progress=show_progress)
    futures = {}
    try:
        futures = {
            executor.submit(fetch, filepath, expected_hash, **options): filepath
            for filepath, expected_hash in files
        }
        for future in as_completed(futures):
            filepath = futures[future]
            try:
                size = future.result()
            except Exception as e:
                report_failure(filepath, e)
            else:
                record(filepath, size)
            progress.set_postfix_str(filepath, refresh=False)
            progress.update(1)
    except BaseException as exc:
        running = sum(future.running() for future in futures)
        cancel.set()
        if isinstance(exc, KeyboardInterrupt) and running:
            print(f"\nStopping {running} running download(s)...", file=sys.stderr)
        raise
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
        progress.close()


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


def _get_file_manifest(
    client: PhysioNetClient, slug: str, version: str, retries: int = 3
) -> List[Tuple[str, str]]:
    """
    Get the file manifest from SHA256SUMS.txt.

    Fetches the checksums file via the /files/ endpoint, which works
    without authentication for open-access projects. Transient errors are
    retried as for downloads (`retries=0` to disable).

    Returns:
        List of (filepath, sha256_hash) tuples
    """
    url = f"{client.base_url}/files/{slug}/{version}/SHA256SUMS.txt"
    _configure_manifest_retries(client, slug, version, retries)
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
    verbose: bool = True,
) -> Tuple[str, str]:
    """
    Select the download source and return (source_type, source_base).

    With verbose=False, the choice is made without printing it.

    Returns:
        A tuple of (source_type, source_base) where source_type is "http" or "s3",
        and source_base is the URL prefix (for HTTP) or bucket name (for S3).
    """
    if source == "physionet":
        return ("http", f"{client.base_url}/files/{slug}/{version}")

    if source == "aws":
        return ("s3", S3_BUCKET)

    # The mirror copies physionet.org only. For any other server (staging, a
    # local or other deployment, a bulk download host), use that server.
    if client.base_url.lower() != MIRRORED_BASE_URL:
        return ("http", f"{client.base_url}/files/{slug}/{version}")

    # Auto-select: try S3 first, fall back to PhysioNet direct.
    # Not all projects are mirrored to S3, so we probe with a HEAD request.
    s3_check_url = f"{S3_BASE_URL}/{slug}/{version}/SHA256SUMS.txt"
    try:
        if verbose:
            print("Checking AWS S3 availability...")
        resp = requests.head(s3_check_url, timeout=5)
        if resp.status_code == 200:
            if verbose:
                print("Found on S3. Using AWS S3.")
            return ("http", f"{S3_BASE_URL}/{slug}/{version}")
    except requests.RequestException:
        pass

    if verbose:
        print("Not available on S3. Using PhysioNet.")
    return ("http", f"{client.base_url}/files/{slug}/{version}")


def _download_file_s3(
    bucket: str,
    key: str,
    dest: Path,
    expected_hash: str,
    s3_client=None,
    show_progress: bool = True,
    cancel: Optional[_Cancellation] = None,
) -> int:
    """
    Download a single file from S3 using boto3 with checksum verification.

    Uses the standard AWS credential chain (env vars, ~/.aws/credentials, IAM roles, etc.).

    Args:
        s3_client: boto3 S3 client to use (default: create one)
        show_progress: Show a progress bar for this file
        cancel: Stop the transfer once this event is set

    Returns:
        Number of bytes downloaded
    """
    if s3_client is None:
        import boto3

        s3_client = boto3.client("s3")
    s3 = s3_client

    # Get object metadata for progress bar
    head = s3.head_object(Bucket=bucket, Key=key)
    total_size = head["ContentLength"]

    downloaded = 0
    with open(dest, "wb") as f:
        with _progress_bar(
            total=total_size,
            unit="B",
            unit_scale=True,
            desc=dest.name,
            leave=False,
            show_progress=show_progress,
        ) as pbar:

            def progress_callback(bytes_amount):
                nonlocal downloaded
                if cancel is not None and cancel.is_set():
                    raise _DownloadCancelled()
                downloaded += bytes_amount
                pbar.update(bytes_amount)

            s3.download_fileobj(bucket, key, f, Callback=progress_callback)

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
    adapter_prefix: Optional[str] = None,
    show_progress: bool = True,
    cancel: Optional[_Cancellation] = None,
) -> int:
    """
    Download a single file with resume support and checksum verification.

    Args:
        retries: Number of retries for transient errors (0 to disable)
        show_progress: Show a progress bar for this file
        cancel: Stop between chunks once this event is set, keeping the
            partial file so a later download can resume it

    Returns:
        Number of bytes downloaded
    """
    _configure_retry_adapter(session, url, retries, adapter_prefix=adapter_prefix)

    headers = {}
    mode = "wb"
    existing_size = 0

    # Resume support: if file partially exists, request remaining bytes
    if dest.exists():
        existing_size = dest.stat().st_size
        headers["Range"] = f"bytes={existing_size}-"
        mode = "ab"

    response = None

    def get(**kwargs) -> requests.Response:
        new_response = session.get(url, stream=True, timeout=60, **kwargs)
        if cancel is not None:
            cancel.watch(new_response)
        return new_response

    try:
        response = get(headers=headers)

        # A server may ignore Range or return a different range. Only append bytes
        # when a 206 response explicitly starts where the local file ends.
        restart = False
        if headers and response.status_code == 416:
            if _verify_checksum(dest, expected_hash):
                return 0
            restart = True
        elif headers and response.status_code == 200:
            mode = "wb"
        elif headers and response.status_code == 206:
            restart = not _range_starts_at(response.headers.get("Content-Range", ""), existing_size)
        elif not headers and response.status_code == 206:
            raise ValueError("Server returned a partial response without a Range request")

        if restart:
            previous_response = response
            response = None
            if cancel is not None:
                cancel.unwatch(previous_response)
            previous_response.close()
            response = get()
            mode = "wb"
            if response.status_code == 206:
                raise ValueError("Server returned a partial response to a full-file request")

        if response.status_code == 403:
            error_msg = response.text or "Access denied"
            raise ForbiddenError(error_msg)

        response.raise_for_status()

        # Get total size for progress bar
        content_length = response.headers.get("Content-Length")
        total = int(content_length) if content_length else None
        downloaded = 0

        with open(dest, mode) as f:
            with _progress_bar(
                total=total,
                initial=0,
                unit="B",
                unit_scale=True,
                desc=dest.name,
                leave=False,
                show_progress=show_progress,
            ) as pbar:
                for chunk in response.iter_content(chunk_size=8192):
                    if cancel is not None and cancel.is_set():
                        raise _DownloadCancelled()
                    f.write(chunk)
                    downloaded += len(chunk)
                    pbar.update(len(chunk))

        # Verify checksum
        if not _verify_checksum(dest, expected_hash):
            dest.unlink()
            raise ValueError(f"Checksum verification failed for {dest.name}")

        return downloaded
    finally:
        if response is not None:
            if cancel is not None:
                cancel.unwatch(response)
            response.close()


def _configure_retry_adapter(
    session: requests.Session,
    url: str,
    retries: int,
    adapter_prefix: Optional[str] = None,
) -> Optional[HTTPAdapter]:
    """Mount and cache a retry adapter scoped to the download URL.

    The default adapters belong to the whole session and may also be used by
    API requests. A dedicated adapter keeps the download retry policy and
    connection pool separate from those shared adapters.

    A more-specific adapter supplied by the caller remains selected by
    ``requests``. Only the dataset prefix is mounted, so downloads do not add
    one adapter entry per file.

    ``retries=0`` leaves caller-provided adapters unchanged. If a dedicated
    adapter was cached by an earlier download, its policy is reset to
    ``Retry(0, read=False)`` so a later download on the same session does not
    keep retrying.
    """
    prefix = (adapter_prefix or _download_adapter_prefix(url)).rstrip("/") + "/"
    cache = getattr(session, _DOWNLOAD_ADAPTER_CACHE_ATTR, None)
    if retries <= 0:
        if isinstance(cache, dict):
            adapter = cache.get(prefix)
            if isinstance(adapter, HTTPAdapter):
                adapter.max_retries = Retry(total=0, read=False)
        return None

    if not isinstance(cache, dict):
        cache = {}
        setattr(session, _DOWNLOAD_ADAPTER_CACHE_ATTR, cache)

    adapter = cache.get(prefix)
    if adapter is None:
        adapter = HTTPAdapter(
            max_retries=Retry(
                total=retries,
                backoff_factor=1,
                status_forcelist=[502, 503, 504],
                raise_on_status=False,
            )
        )
        cache[prefix] = adapter
        session.mount(prefix, adapter)
    else:
        # The adapter is dedicated to this download prefix, so changing its
        # policy does not modify a shared adapter or discard its pool.
        adapter.max_retries = Retry(
            total=retries,
            backoff_factor=1,
            status_forcelist=[502, 503, 504],
            raise_on_status=False,
        )

    return adapter


def _configure_manifest_retries(client: PhysioNetClient, slug: str, version: str, retries: int) -> None:
    """Retry transient errors for a project version's checksum list, as for its files."""
    prefix = f"{client.base_url}/files/{slug}/{version}"
    _configure_retry_adapter(client.session, f"{prefix}/", retries, adapter_prefix=prefix)


def _download_adapter_prefix(url: str) -> str:
    """Return a path-scoped adapter prefix for a file URL."""
    return url.rsplit("/", 1)[0]


def _range_starts_at(content_range: str, expected_start: int) -> bool:
    """Check that Content-Range describes a valid range at the local offset."""
    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+|\*)", content_range.strip(), re.IGNORECASE)
    if not match:
        return False
    start, end = int(match.group(1)), int(match.group(2))
    total = match.group(3)
    return start == expected_start and end >= start and (total == "*" or int(total) > end)


def _verify_checksum(path: Path, expected_hash: str) -> bool:
    """Verify SHA256 checksum of a file."""
    sha256 = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest() == expected_hash
