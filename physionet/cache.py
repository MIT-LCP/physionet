"""See and clean up the files that fetch(), load() and physionet:// paths keep in the cache.

The cache is described in physionet.data. Each project version that has been
used has a cached checksum list at
<cache>/<host>/.manifests/<slug>/<version>/SHA256SUMS.txt, and its files at
<cache>/<host>/<slug>/<version>/. Only folders with a cached checksum list are
treated as part of the cache, so a cache folder set to the wrong place (for
example, your home folder) cannot lead to other files being deleted.

Symbolic links are never followed: a link is removed, not what it points to.
Unfinished downloads that were changed in the last day are left alone, since
another program may still be writing them.
"""

import os
import stat
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, List, Optional, Tuple, Union

from physionet.data import ACCESS_FILE, MANIFEST_FOLDER, _NAME, get_cache_dir

__all__ = ["CachedVersion", "CleanResult", "list_cache", "clean_cache"]

# A download in progress is staged in a folder with this prefix (see data._fetch).
STAGING_PREFIX = ".download-"
# Staging folders changed more recently than this may still be in use.
STAGING_GRACE = timedelta(days=1)


@dataclass
class CachedVersion:
    """One project version in the cache."""

    origin: str
    """Folder for the server the files came from, e.g. "physionet.org"."""
    slug: str
    version: str
    path: Path
    """Folder that holds the version's files (it may not exist yet)."""
    files: int
    size: int
    """Total size of the files, in bytes."""
    last_downloaded: Optional[datetime]
    """When the most recently downloaded file was saved (None if no files)."""
    restricted: Optional[bool]
    """Whether the project is restricted (None if not known)."""
    unfinished: int = 0
    """Downloads that were interrupted, or that are still running."""

    def to_dict(self) -> dict:
        return {
            "origin": self.origin,
            "slug": self.slug,
            "version": self.version,
            "path": str(self.path),
            "files": self.files,
            "size": self.size,
            "last_downloaded": self.last_downloaded.isoformat() if self.last_downloaded else None,
            "restricted": self.restricted,
            "unfinished": self.unfinished,
        }


@dataclass
class CleanResult:
    """What clean_cache() removed, or would remove with dry_run=True."""

    files: int = 0
    size: int = 0
    """Total size of the files, in bytes."""
    versions: List[CachedVersion] = field(default_factory=list)
    """Project versions that were removed completely, with their checksum lists."""
    unfinished: int = 0
    """Unfinished downloads (staging folders) removed."""
    errors: List[Tuple[Path, str]] = field(default_factory=list)
    """Paths that could not be removed or read, and why."""


def list_cache(slug: Optional[str] = None, *, cache_dir: Optional[str] = None) -> List[CachedVersion]:
    """
    List the project versions in the cache.

    Args:
        slug: Only list this project
        cache_dir: Cache folder (default: see physionet.get_cache_dir)

    Returns:
        One entry per project version, sorted by server, project and version
    """
    cache = get_cache_dir(cache_dir)
    entries = []
    for origin, entry_slug, version in _cached_versions(cache):
        if slug is not None and entry_slug != slug:
            continue
        entries.append(_describe(cache, origin, entry_slug, version))
    return entries


def clean_cache(
    slug: Optional[str] = None,
    version: Optional[str] = None,
    *,
    older_than: Union[timedelta, int, float, None] = None,
    cache_dir: Optional[str] = None,
    dry_run: bool = False,
) -> CleanResult:
    """
    Delete files from the cache.

    With no arguments, everything in the cache is deleted. Files that are
    deleted are downloaded again the next time they are used.

    Args:
        slug: Only delete files of this project
        version: Only delete files of this version (needs slug)
        older_than: Only delete files downloaded longer ago than this, as a
            timedelta or a number of days. A project version whose files are
            all deleted is removed with its checksum list.
        cache_dir: Cache folder (default: see physionet.get_cache_dir)
        dry_run: Report what would be deleted, without deleting anything

    Returns:
        A CleanResult. Paths that could not be deleted, for example files in a
        shared cache that belong to someone else, are listed in its errors.

    Raises:
        ValueError: If version is given without slug, or older_than is negative
    """
    if version is not None and slug is None:
        raise ValueError("version needs slug")
    if older_than is not None and not isinstance(older_than, timedelta):
        if isinstance(older_than, bool) or not isinstance(older_than, (int, float)):
            raise ValueError(f"older_than must be a timedelta or a number of days, not {older_than!r}")
        older_than = timedelta(days=older_than)
    if older_than is not None and older_than < timedelta(0):
        raise ValueError("older_than cannot be negative")

    now = time.time()
    cutoff = now - older_than.total_seconds() if older_than is not None else None
    staging_cutoff = now - STAGING_GRACE.total_seconds()
    cache = get_cache_dir(cache_dir)
    result = CleanResult()

    for entry in list_cache(slug, cache_dir=str(cache)):
        if version is not None and entry.version != version:
            continue
        kept = _remove_tree(entry.path, cutoff, staging_cutoff, dry_run, result)
        if kept == 0:
            result.versions.append(entry)
            if not dry_run:
                manifest = cache / entry.origin / MANIFEST_FOLDER / entry.slug / entry.version
                _remove_tree(manifest, None, now, False, CleanResult(errors=result.errors))
                for empty in (manifest.parent, entry.path.parent):
                    _remove_if_empty(empty)
    return result


def _cached_versions(cache: Path) -> Iterator[Tuple[str, str, str]]:
    """Yield (origin, slug, version) for every cached checksum list."""
    # The cache folder itself may be a link, as fetch() allows; links below it are not followed.
    if not cache.is_dir():
        return
    for origin in sorted(cache.iterdir(), key=lambda path: path.name):
        manifests = origin / MANIFEST_FOLDER
        if not _NAME.match(origin.name) or not _is_real_dir(origin) or not _is_real_dir(manifests):
            continue
        for slug in sorted(manifests.iterdir(), key=lambda path: path.name):
            if not _NAME.match(slug.name) or not _is_real_dir(slug):
                continue
            for version in sorted(slug.iterdir(), key=lambda path: path.name):
                manifest = version / "SHA256SUMS.txt"
                if _NAME.match(version.name) and _is_real_dir(version) and _is_real_file(manifest):
                    yield origin.name, slug.name, version.name


def _describe(cache: Path, origin: str, slug: str, version: str) -> CachedVersion:
    path = cache / origin / slug / version
    files = size = unfinished = 0
    newest = None
    for _, info in _files(path):
        files += 1
        size += info.st_size
        newest = info.st_mtime if newest is None else max(newest, info.st_mtime)
    if _is_real_dir(path):
        unfinished = sum(1 for child in path.rglob(f"{STAGING_PREFIX}*") if _is_real_dir(child))
    access = cache / origin / MANIFEST_FOLDER / slug / version / ACCESS_FILE
    restricted = None
    try:
        if _is_real_file(access):
            restricted = {"open": False, "restricted": True}.get(access.read_text().strip())
    except OSError:
        pass
    return CachedVersion(
        origin=origin,
        slug=slug,
        version=version,
        path=path,
        files=files,
        size=size,
        last_downloaded=datetime.fromtimestamp(newest, tz=timezone.utc) if newest is not None else None,
        restricted=restricted,
        unfinished=unfinished,
    )


def _files(top: Path) -> Iterator[Tuple[Path, os.stat_result]]:
    """Yield the downloaded files below top, skipping links and unfinished downloads."""
    if not _is_real_dir(top):
        return
    for folder, dirnames, filenames in os.walk(top, followlinks=False):
        dirnames[:] = sorted(name for name in dirnames if not name.startswith(STAGING_PREFIX))
        for name in sorted(filenames):
            path = Path(folder) / name
            try:
                info = path.lstat()
            except OSError:
                continue
            if not path.is_symlink() and os.path.isfile(path):
                yield path, info


def _remove_tree(
    top: Path,
    cutoff: Optional[float],
    staging_cutoff: float,
    dry_run: bool,
    result: CleanResult,
) -> int:
    """
    Delete files below top (those older than cutoff, if given), then the
    folders left empty, including top. Links are removed, never followed.
    Return the number of files and links that were kept.
    """
    if top.is_symlink():
        # Remove the link itself, never what it points to
        if not dry_run:
            try:
                top.unlink()
            except OSError as exc:
                result.errors.append((top, exc.strerror or str(exc)))
                return 1
        return 0
    if not _is_real_dir(top):
        return 0
    kept = 0
    staging_in_use = {}  # staging folder -> whether it was changed too recently to delete
    # os.walk skips folders it cannot list. Report them, and count them as
    # kept, so a version with files that could not be seen is not removed.
    unlisted: List[OSError] = []
    for folder, dirnames, filenames in os.walk(top, topdown=False, onerror=unlisted.append, followlinks=False):
        folder_path = Path(folder)
        parts = folder_path.relative_to(top).parts
        staging = next((top.joinpath(*parts[: i + 1]) for i, part in enumerate(parts)
                        if part.startswith(STAGING_PREFIX)), None)
        if staging is not None and staging not in staging_in_use:
            staging_in_use[staging] = _newest_change(staging) > staging_cutoff
        for name in filenames + [name for name in dirnames if (folder_path / name).is_symlink()]:
            path = folder_path / name
            try:
                info = path.lstat()
            except OSError:
                continue
            if staging is not None:
                if staging_in_use[staging]:
                    kept += 1
                    continue
            elif cutoff is not None and info.st_mtime > cutoff:
                kept += 1
                continue
            if not dry_run:
                try:
                    path.unlink()
                except OSError as exc:
                    result.errors.append((path, exc.strerror or str(exc)))
                    kept += 1
                    continue
            if not stat.S_ISLNK(info.st_mode):
                result.size += info.st_size
                if staging is None:
                    result.files += 1
        if staging is not None and staging_in_use[staging]:
            # A download may have just created this folder and not written to
            # it yet, so keep it even when it is empty.
            if folder_path == staging:
                kept += 1
            continue
        if not dry_run:
            _remove_if_empty(folder_path)
        if folder_path == staging and (dry_run or not folder_path.exists()):
            result.unfinished += 1
    for error in unlisted:
        result.errors.append((Path(error.filename or top), error.strerror or str(error)))
        kept += 1
    return kept


def _newest_change(top: Path) -> float:
    newest = top.lstat().st_mtime
    for folder, _, filenames in os.walk(top, followlinks=False):
        for name in filenames:
            try:
                newest = max(newest, (Path(folder) / name).lstat().st_mtime)
            except OSError:
                pass
    return newest


def _remove_if_empty(path: Path) -> None:
    try:
        if _is_real_dir(path) and not any(path.iterdir()):
            path.rmdir()
    except OSError:
        pass


def _is_real_dir(path: Path) -> bool:
    return not path.is_symlink() and path.is_dir()


def _is_real_file(path: Path) -> bool:
    return not path.is_symlink() and path.is_file()
