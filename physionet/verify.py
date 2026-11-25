"""Check a local or remote PhysioNet dataset against its published checksums.

The checksums are, in order of preference:

1. a checksum file passed as checksums=
2. the project's SHA256SUMS.txt, when the project and version are known (given,
   or read from a folder named like `physionet download` names them, such as
   mimic-iv-demo-2.2, or like a wget copy, such as .../files/mitdb/1.0.0). A
   copy cached by physionet.load() or fetch() is used if there is one, so this
   works offline; otherwise it is downloaded from PhysioNet.
3. a SHA256SUMS.txt file in the folder itself
"""

import hashlib
import os
import re
import shlex
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlsplit

import requests
from urllib3.exceptions import HTTPError as Urllib3HTTPError

from physionet.api.exceptions import ForbiddenError, NotFoundError
from physionet.download import MAX_JOBS, _filter_files, _is_safe_path, _parse_manifest, _progress_bar

__all__ = ["VerifyResult", "verify"]

CHECKSUM_FILE = "SHA256SUMS.txt"
_CHUNK = 1024 * 1024
# PhysioNet versions are numbers separated by dots, e.g. 1.0.0 or 2.2.
_VERSION = re.compile(r"^\d+(\.\d+)*$")


@dataclass
class VerifyResult:
    """The outcome of verify()."""

    path: Path | str
    """The local folder or remote URL that was checked."""
    source: str
    """Where the checksums came from."""
    slug: Optional[str] = None
    version: Optional[str] = None
    matched: List[str] = field(default_factory=list)
    """Files whose content matches the checksum list."""
    mismatched: List[str] = field(default_factory=list)
    """Files whose content does not match: corrupt, changed or incomplete."""
    missing: List[str] = field(default_factory=list)
    """Files in the checksum list that are not in the folder."""
    extra: List[str] = field(default_factory=list)
    """Files in the folder that are not in the checksum list."""
    unreadable: List[Tuple[str, str]] = field(default_factory=list)
    """Files that could not be read, and why."""
    outside: List[str] = field(default_factory=list)
    """Files that are symbolic links to somewhere outside the folder, so the
    copy does not hold them. They are not read."""
    files_source: Optional[str] = None
    """Where remote file contents were read from, when source selection is automatic."""

    @property
    def checked(self) -> int:
        """How many files in the checksum list were checked (after include and exclude)."""
        return len(self.matched) + len(self.mismatched) + len(self.missing) + len(self.unreadable) + len(
            self.outside
        )

    @property
    def passed(self) -> bool:
        """Whether every file in the checksum list is present and matches.

        False if no files were checked, for example because the checksum list
        is empty or include matches none of its files. Extra files do not count
        against this.
        """
        return self.checked > 0 and not (self.mismatched or self.missing or self.unreadable or self.outside)

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "source": self.source,
            "files_source": self.files_source,
            "slug": self.slug,
            "version": self.version,
            "passed": self.passed,
            "matched": self.matched,
            "mismatched": self.mismatched,
            "missing": self.missing,
            "extra": self.extra,
            "unreadable": [{"path": path, "error": error} for path, error in self.unreadable],
            "outside": self.outside,
            "checked": self.checked,
        }


def verify(
    path: str,
    slug: Optional[str] = None,
    version: Optional[str] = None,
    *,
    checksums: Optional[str] = None,
    include: Optional[List[str]] = None,
    exclude: Optional[List[str]] = None,
    jobs: int = 1,
    show_progress: bool = True,
    cache_dir: Optional[str] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
    token: Optional[str] = None,
    base_url: str = "https://physionet.org",
) -> VerifyResult:
    """
    Check local or remote files against a PhysioNet project's checksums.

    Args:
        path: A local folder, s3://bucket/prefix, or physionet://slug/version
        slug: Project identifier. Read from the folder name if not given.
        version: Project version. Read from the folder name if not given.
        checksums: A checksum file to use instead (lines of "<sha256>  <path>")
        include: Only check files matching these glob patterns
        exclude: Skip files matching these glob patterns
        jobs: Number of files to read at once, from 1 to MAX_JOBS (8)
        show_progress: Show a progress bar while files are read
        cache_dir: Cache folder to look in for a cached checksum list
        username, password, token: Credentials, for a restricted project's
            checksum list. Without them, the sign-in saved by `physionet login`
            is used.
        base_url: PhysioNet base URL

    Returns:
        A VerifyResult. Its passed property is True when every file in the
        checksum list is present and matches.

    Raises:
        FileNotFoundError: If the folder, S3 prefix or checksum file does not exist
        ValueError: If no checksums can be found for the folder, or jobs is
            not between 1 and MAX_JOBS
        NotFoundError: If the project or version is not found
        ForbiddenError: If your account cannot access the project's checksums
        ConnectionError: If PhysioNet cannot be reached for the checksums
    """
    if isinstance(jobs, bool) or not isinstance(jobs, int) or not 1 <= jobs <= MAX_JOBS:
        raise ValueError(f"jobs must be a whole number from 1 to {MAX_JOBS}, not {jobs!r}")
    if isinstance(path, str) and path.startswith("s3://"):
        return _verify_s3(
            path, slug, version, checksums, include, exclude, jobs, show_progress,
            cache_dir, username, password, token, base_url,
        )
    if isinstance(path, str) and path.startswith("physionet://"):
        return _verify_physionet(
            path, slug, version, checksums, include, exclude, jobs, show_progress,
            cache_dir, username, password, token, base_url,
        )
    folder = Path(path).expanduser()
    if not folder.is_dir():
        raise FileNotFoundError(f"{folder} is not a folder")
    inferred_project = False
    if checksums is not None:
        slug = version = None
    elif version is not None and slug is None:
        raise ValueError("version needs slug")
    elif version is None:
        guessed_slug, guessed_version = _guess_project(folder)
        if slug is None or slug == guessed_slug:
            inferred_project = slug is None and guessed_slug is not None
            slug, version = guessed_slug, guessed_version
        else:
            raise ValueError(f"Give the version of {slug} that {folder} holds")

    try:
        entries, source = _checksums(
            folder, slug, version, checksums, cache_dir, username, password, token, base_url
        )
    except NotFoundError as exc:
        if not inferred_project:
            raise
        command = f"physionet verify {shlex.quote(str(folder))} SLUG --version {version}"
        raise NotFoundError(
            f"{exc}. The project and version were inferred from the folder name {folder.name!r}. "
            f"If the folder name differs from the PhysioNet slug, pass the actual slug explicitly: `{command}`."
        ) from exc
    expected: Dict[str, str] = {}
    for name, checksum in _filter_files(entries, include, exclude):
        if _is_safe_path(name):
            expected[name] = checksum.lower()

    result = VerifyResult(path=folder, source=source, slug=slug, version=version)
    present = []
    root = os.path.realpath(folder)
    for name in expected:
        file = folder.joinpath(*PurePosixPath(name).parts)
        if not file.is_file():
            result.missing.append(name)
        elif not _is_inside(root, file):
            # A link (or a linked folder) to a file elsewhere: reading it would
            # pass the check without the copy holding the file.
            result.outside.append(name)
        else:
            present.append((name, file))

    local = [(name, "") for name in _local_files(folder) if name != CHECKSUM_FILE]
    result.extra = sorted(name for name, _ in _filter_files(local, include, exclude) if name not in expected)

    total = 0
    for _, file in present:
        try:
            total += file.stat().st_size
        except OSError:
            pass
    with _progress_bar(
        total=total, unit="B", unit_scale=True, desc="Verifying", leave=False, show_progress=show_progress
    ) as progress:
        def check(item):
            name, file = item
            try:
                return name, _sha256(file, progress.update), None
            except OSError as exc:
                return name, None, exc.strerror or str(exc)

        if jobs == 1:
            outcomes = map(check, present)
        else:
            executor = ThreadPoolExecutor(max_workers=jobs)
            outcomes = executor.map(check, present)
        try:
            for name, digest, error in outcomes:
                if error is not None:
                    result.unreadable.append((name, error))
                elif digest == expected[name]:
                    result.matched.append(name)
                else:
                    result.mismatched.append(name)
        finally:
            if jobs != 1:
                executor.shutdown(wait=True, cancel_futures=True)

    for names in (result.matched, result.mismatched, result.missing, result.outside):
        names.sort()
    result.unreadable.sort()
    return result


def _verify_s3(
    path, slug, version, checksums, include, exclude, jobs, show_progress,
    cache_dir, username, password, token, base_url,
) -> VerifyResult:
    """Stream S3 objects through SHA-256 without saving copies to disk."""
    import boto3
    from botocore import UNSIGNED
    from botocore.config import Config
    from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError

    parsed = urlsplit(path)
    if not parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError(f"Invalid S3 folder URL: {path}")
    bucket = parsed.netloc
    prefix = parsed.path.strip("/")
    object_prefix = f"{prefix}/" if prefix else ""
    s3 = boto3.client("s3")

    try:
        objects = _s3_objects(s3, bucket, object_prefix)
    except (NoCredentialsError, ClientError) as signed_error:
        stale_codes = {"ExpiredToken", "InvalidToken", "InvalidAccessKeyId", "SignatureDoesNotMatch"}
        if isinstance(signed_error, ClientError) and signed_error.response.get("Error", {}).get("Code") not in stale_codes:
            raise OSError(f"Could not list {path}: {signed_error}") from signed_error
        try:
            # Public buckets can be read even when credentials are absent or stale.
            s3 = boto3.client("s3", config=Config(signature_version=UNSIGNED))
            objects = _s3_objects(s3, bucket, object_prefix)
        except (BotoCoreError, ClientError) as unsigned_error:
            error = signed_error if isinstance(signed_error, ClientError) else unsigned_error
            raise OSError(f"Could not list {path}: {error}") from error
    except (BotoCoreError, ClientError) as exc:
        raise OSError(f"Could not list {path}: {exc}") from exc
    if not objects:
        raise FileNotFoundError(f"{path} has no files, or the S3 prefix does not exist")

    inferred_project = False
    if checksums is not None:
        slug = version = None
    elif version is not None and slug is None:
        raise ValueError("version needs slug")
    elif version is None:
        from physionet.data import _NAME

        parts = prefix.split("/")
        guessed_slug, guessed_version = (parts[-2], parts[-1]) if len(parts) >= 2 else (None, None)
        if not guessed_slug or not _NAME.fullmatch(guessed_slug) or not _VERSION.fullmatch(guessed_version):
            guessed_slug = guessed_version = None
        if slug is None or slug == guessed_slug:
            inferred_project = slug is None and guessed_slug is not None
            slug, version = guessed_slug, guessed_version
        else:
            raise ValueError(f"Give the version of {slug} that {path} holds")

    if checksums is not None or (slug is not None and version is not None):
        try:
            entries, source = _checksums(
                Path("."), slug, version, checksums, cache_dir, username, password, token, base_url
            )
        except NotFoundError as exc:
            if not inferred_project:
                raise
            command = f"physionet verify {shlex.quote(path)} SLUG --version {version}"
            raise NotFoundError(
                f"{exc}. The project and version were inferred from the S3 path. "
                f"If they differ from the PhysioNet project, pass the actual slug explicitly: `{command}`."
            ) from exc
    elif CHECKSUM_FILE in objects:
        try:
            with closing(s3.get_object(Bucket=bucket, Key=object_prefix + CHECKSUM_FILE)["Body"]) as body:
                entries = _parse_manifest(body.read().decode("utf-8"))
        except (BotoCoreError, ClientError) as exc:
            raise OSError(f"Could not read {path}/{CHECKSUM_FILE}: {exc}") from exc
        source = f"{path.rstrip('/')}/{CHECKSUM_FILE} (in the folder)"
    else:
        raise ValueError(
            f"Cannot tell which dataset {path} holds. Give the dataset and version "
            "(e.g. physionet verify S3_URL mimiciv --version 3.1), or a checksum file."
        )

    expected = {
        name: checksum.lower() for name, checksum in _filter_files(entries, include, exclude) if _is_safe_path(name)
    }
    result = VerifyResult(path=path, source=source, slug=slug, version=version)
    present = [(name, objects[name]) for name in expected if name in objects]
    result.missing = sorted(name for name in expected if name not in objects)
    extra = [(name, "") for name in objects if name != CHECKSUM_FILE]
    result.extra = sorted(name for name, _ in _filter_files(extra, include, exclude) if name not in expected)

    with _progress_bar(
        total=sum(size for _, size in present), unit="B", unit_scale=True,
        desc="Verifying", leave=False, show_progress=show_progress,
    ) as progress:
        def check(item):
            name, _ = item
            try:
                with closing(s3.get_object(Bucket=bucket, Key=object_prefix + name)["Body"]) as body:
                    return name, _sha256_body(body, progress.update), None
            except (BotoCoreError, ClientError, OSError) as exc:
                return name, None, str(exc)

        if jobs == 1:
            outcomes = map(check, present)
        else:
            executor = ThreadPoolExecutor(max_workers=jobs)
            outcomes = executor.map(check, present)
        try:
            for name, digest, error in outcomes:
                if error is not None:
                    result.unreadable.append((name, error))
                elif digest == expected[name]:
                    result.matched.append(name)
                else:
                    result.mismatched.append(name)
        finally:
            if jobs != 1:
                executor.shutdown(wait=True, cancel_futures=True)

    for names in (result.matched, result.mismatched):
        names.sort()
    result.unreadable.sort()
    return result


def _s3_objects(s3, bucket: str, prefix: str) -> Dict[str, int]:
    """List every object under a prefix, omitting S3 folder markers."""
    objects = {}
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for item in page.get("Contents", []):
            key = item["Key"]
            if key != prefix and not key.endswith("/"):
                objects[key[len(prefix):]] = item["Size"]
    return objects


def _verify_physionet(
    path, slug, version, checksums, include, exclude, jobs, show_progress,
    cache_dir, username, password, token, base_url,
) -> VerifyResult:
    """Read published files remotely and hash their original response bytes."""
    from physionet.data import _Remote, _check_name, latest_version
    from physionet.download import S3_BASE_URL, _configure_retry_adapter, _select_source

    parsed = urlsplit(path)
    parts = parsed.path.strip("/").split("/")
    if not parsed.netloc or len(parts) != 1 or not parts[0] or parsed.query or parsed.fragment:
        raise ValueError("Use physionet://SLUG/VERSION to verify a published dataset")
    url_slug, url_version = parsed.netloc, parts[0]
    _check_name(url_slug, "project")
    if url_version == "latest":
        url_version = latest_version(
            url_slug, username=username, password=password, token=token, base_url=base_url
        )
    else:
        _check_name(url_version, "version")
    if slug is not None and slug != url_slug:
        raise ValueError(f"Project {slug} does not match the physionet:// URL ({url_slug})")
    if version is not None and version != url_version:
        raise ValueError(f"Version {version} does not match the physionet:// URL ({url_version})")
    slug, version = url_slug, url_version
    entries, source = _checksums(
        Path("."), slug, version, checksums, cache_dir, username, password, token, base_url
    )
    expected = {
        name: checksum.lower() for name, checksum in _filter_files(entries, include, exclude) if _is_safe_path(name)
    }
    result = VerifyResult(path=path, source=source, slug=slug, version=version)

    with _Remote(base_url.rstrip("/"), username, password, token) as remote:
        client = remote.client
        source_type, source_base = _select_source(client, slug, version, "auto", remote.username, verbose=False)
        if source_type == "s3":
            source_base = f"{S3_BASE_URL}/{slug}/{version}"
        result.files_source = source_base
        _configure_retry_adapter(client.session, f"{source_base}/", 3, adapter_prefix=source_base)

        with _progress_bar(
            unit="B", unit_scale=True, desc="Verifying",
            leave=False, show_progress=show_progress,
        ) as progress:
            def check(item):
                name, checksum = item
                url = f"{source_base}/{name}"
                try:
                    with client.session.get(
                        url, stream=True, headers={"Accept-Encoding": "identity"}, timeout=client.timeout
                    ) as response:
                        if response.status_code == 404:
                            return name, "missing", None
                        response.raise_for_status()
                        digest = _sha256_body(response.raw, progress.update)
                        return name, "matched" if digest == checksum else "mismatched", None
                except (requests.RequestException, Urllib3HTTPError, OSError) as exc:
                    return name, "unreadable", str(exc)

            if jobs == 1:
                outcomes = map(check, expected.items())
            else:
                executor = ThreadPoolExecutor(max_workers=jobs)
                outcomes = executor.map(check, expected.items())
            try:
                for name, status, error in outcomes:
                    if error is not None:
                        result.unreadable.append((name, error))
                    else:
                        getattr(result, status).append(name)
            finally:
                if jobs != 1:
                    executor.shutdown(wait=True, cancel_futures=True)

    for names in (result.matched, result.mismatched, result.missing):
        names.sort()
    result.unreadable.sort()
    return result


def _guess_project(folder: Path) -> Tuple[Optional[str], Optional[str]]:
    """
    Read the project and version from a folder's name, if it follows a known
    pattern. The path as given is tried first, so a link named like a
    download folder works, then the path with links followed.
    """
    for candidate in _named_paths(folder):
        guess = _project_from_path(candidate)
        if guess != (None, None):
            return guess
    return None, None


def _named_paths(folder: Path) -> List[Path]:
    """The folder as given (made absolute, without following links), then with links followed."""
    given = Path(os.path.abspath(folder))
    resolved = folder.resolve()
    return [given] if given == resolved else [given, resolved]


def _project_from_path(folder: Path) -> Tuple[Optional[str], Optional[str]]:
    from physionet.data import _NAME

    # `physionet download` saves to <slug>-<version>
    slug, _, version = folder.name.rpartition("-")
    if slug and _NAME.match(slug) and _VERSION.match(version):
        return slug, version
    # wget -r saves to .../physionet.org/files/<slug>/<version>
    if folder.parent.parent.name in ("files", "content") and _VERSION.match(folder.name):
        if _NAME.match(folder.parent.name):
            return folder.parent.name, folder.name
    return None, None


def _checksums(folder, slug, version, checksums, cache_dir, username, password, token, base_url):
    """Return the (path, sha256) entries to check against, and where they came from."""
    if checksums is not None:
        checksum_file = Path(checksums).expanduser()
        return _parse_manifest(checksum_file.read_text()), str(checksum_file)

    if slug is not None and version is not None:
        from physionet.data import (
            MANIFEST_FOLDER,
            _Remote,
            _check_name,
            _download_manifest,
            _origin_folder,
            _parse_cached_manifest,
            get_cache_dir,
        )

        _check_name(slug, "project")
        _check_name(version, "version")
        site = base_url.rstrip("/")
        cached = get_cache_dir(cache_dir) / _origin_folder(site) / MANIFEST_FOLDER / slug / version / CHECKSUM_FILE
        if cached.is_file() and not cached.is_symlink():
            return _parse_cached_manifest(cached.read_text()), f"{site} ({slug} {version}, cached)"
        with _Remote(site, username, password, token) as remote:
            try:
                entries, _ = _download_manifest(remote, slug, version)
            except (requests.ConnectionError, requests.Timeout) as exc:
                raise ConnectionError(
                    f"Could not reach {site} to get the checksums for {slug} {version}. "
                    f"Pass checksums= (--checksums) to use a local checksum file. ({type(exc).__name__})"
                ) from None
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else None
                if status == 404:
                    raise NotFoundError(f"PhysioNet has no project {slug} version {version}") from exc
                if status in (401, 403):
                    raise ForbiddenError(
                        f"Access to the checksums for {slug} {version} was denied. Restricted datasets need "
                        "a PhysioNet account with access to the project: run `physionet login`, set "
                        "PHYSIONET_TOKEN, or pass username and password."
                    ) from exc
                raise
        return entries, f"{site} ({slug} {version})"

    local = folder / CHECKSUM_FILE
    if local.is_file():
        return _parse_manifest(local.read_text()), f"{local} (in the folder)"
    raise ValueError(
        f"Cannot tell which dataset {folder} holds. Give the dataset and version "
        "(e.g. physionet verify FOLDER mimiciv --version 3.1), or a checksum file."
    )


def _is_inside(root: str, file: Path) -> bool:
    """Whether a file, with every symbolic link followed, is inside root (a real path)."""
    target = os.path.realpath(file)
    try:
        return os.path.commonpath([root, target]) == root
    except ValueError:
        # On Windows, a link to another drive: paths on different drives
        # have no common path, and the file is not inside root.
        return False


def _local_files(folder: Path) -> List[str]:
    """Every file below folder, as a path relative to it with / separators."""
    names = []
    for current, dirnames, filenames in os.walk(folder):
        dirnames.sort()
        relative = Path(current).relative_to(folder)
        for name in filenames:
            names.append((relative / name).as_posix() if relative.parts else name)
    return names


def _sha256(file: Path, advance) -> str:
    with open(file, "rb") as handle:
        return _sha256_body(handle, advance)


def _sha256_body(handle, advance) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: handle.read(_CHUNK), b""):
        digest.update(chunk)
        advance(len(chunk))
    return digest.hexdigest()
