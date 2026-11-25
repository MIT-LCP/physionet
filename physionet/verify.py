"""Check a local copy of a PhysioNet dataset against its published checksums.

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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Tuple

import requests

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

    path: Path
    """The folder that was checked."""
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

    @property
    def passed(self) -> bool:
        """Whether every file in the checksum list is present and matches.

        Extra files do not count against this.
        """
        return not (self.mismatched or self.missing or self.unreadable)

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "source": self.source,
            "slug": self.slug,
            "version": self.version,
            "passed": self.passed,
            "matched": self.matched,
            "mismatched": self.mismatched,
            "missing": self.missing,
            "extra": self.extra,
            "unreadable": [{"path": path, "error": error} for path, error in self.unreadable],
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
    Check the files in a folder against a PhysioNet project's checksums.

    Args:
        path: The folder, e.g. one made by `physionet download`
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
        FileNotFoundError: If the folder or checksum file does not exist
        ValueError: If no checksums can be found for the folder, or jobs is
            not between 1 and MAX_JOBS
        NotFoundError: If the project or version is not found
        ForbiddenError: If your account cannot access the project's checksums
        ConnectionError: If PhysioNet cannot be reached for the checksums
    """
    if isinstance(jobs, bool) or not isinstance(jobs, int) or not 1 <= jobs <= MAX_JOBS:
        raise ValueError(f"jobs must be a whole number from 1 to {MAX_JOBS}, not {jobs!r}")
    folder = Path(path).expanduser()
    if not folder.is_dir():
        raise FileNotFoundError(f"{folder} is not a folder")
    if checksums is not None:
        slug = version = None
    elif version is not None and slug is None:
        raise ValueError("version needs slug")
    elif version is None:
        guessed_slug, guessed_version = _guess_project(folder)
        if slug is None or slug == guessed_slug:
            slug, version = guessed_slug, guessed_version
        else:
            raise ValueError(f"Give the version of {slug} that {folder} holds")

    entries, source = _checksums(
        folder, slug, version, checksums, cache_dir, username, password, token, base_url
    )
    expected: Dict[str, str] = {}
    for name, checksum in _filter_files(entries, include, exclude):
        if _is_safe_path(name):
            expected[name] = checksum.lower()

    result = VerifyResult(path=folder, source=source, slug=slug, version=version)
    present = []
    for name in expected:
        file = folder.joinpath(*PurePosixPath(name).parts)
        if file.is_file():
            present.append((name, file))
        else:
            result.missing.append(name)

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

    for names in (result.matched, result.mismatched, result.missing):
        names.sort()
    result.unreadable.sort()
    return result


def _guess_project(folder: Path) -> Tuple[Optional[str], Optional[str]]:
    """Read the project and version from a folder's name, if it follows a known pattern."""
    from physionet.data import _NAME

    folder = folder.resolve()
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
    digest = hashlib.sha256()
    with open(file, "rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
            advance(len(chunk))
    return digest.hexdigest()
