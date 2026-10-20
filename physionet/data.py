"""Fetch PhysioNet files into a local cache and load tables from them.

Files are stored at <cache>/<slug>/<version>/<path>. The cache folder is the
cache_dir argument, or $PHYSIONET_CACHE, or $XDG_CACHE_HOME/physionet, or
~/.cache/physionet.

A downloaded file is moved into the cache only after its SHA-256 checksum
matches the project's SHA256SUMS.txt, so every cached file is complete and
verified. Published versions never change, so each version's checksum list
is cached as well. With an explicit version, a file that is already cached is
returned without any network request. Without a version, the latest version
is looked up online each time.
"""

import os
import re
import uuid
from difflib import get_close_matches
from pathlib import Path, PurePosixPath
from typing import Callable, Dict, List, Optional, Tuple

import pandas as pd
import requests

from physionet.api.client import PhysioNetClient
from physionet.api.exceptions import ForbiddenError, NotFoundError
from physionet.api.utils import get_credentials_from_env
from physionet.download import (
    _download_file,
    _download_file_s3,
    _get_file_manifest,
    _resolve_version,
    _select_source,
)

__all__ = ["fetch", "load", "get_cache_dir"]

DEFAULT_BASE_URL = "https://physionet.org"
MANIFEST_FOLDER = ".manifests"  # Starts with a dot, so it cannot clash with a project slug.

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _read_tsv(path, **kwargs):
    kwargs.setdefault("sep", "\t")
    return pd.read_csv(path, **kwargs)


# Checked in order, so a name without an extension prefers .csv.gz.
TABLE_READERS: Tuple[Tuple[str, Callable[..., pd.DataFrame]], ...] = (
    (".csv.gz", pd.read_csv),
    (".csv", pd.read_csv),
    (".parquet", pd.read_parquet),
    (".tsv.gz", _read_tsv),
    (".tsv", _read_tsv),
    (".xlsx", pd.read_excel),
)


def get_cache_dir(cache_dir: Optional[str] = None) -> Path:
    """Return the folder that fetch() and load() store files in."""
    if cache_dir:
        return Path(cache_dir).expanduser()
    if os.environ.get("PHYSIONET_CACHE"):
        return Path(os.environ["PHYSIONET_CACHE"]).expanduser()
    if os.environ.get("XDG_CACHE_HOME"):
        return Path(os.environ["XDG_CACHE_HOME"]).expanduser() / "physionet"
    return Path.home() / ".cache" / "physionet"


def fetch(
    slug: str,
    path: str,
    version: Optional[str] = None,
    *,
    cache_dir: Optional[str] = None,
    source: str = "auto",
    username: Optional[str] = None,
    password: Optional[str] = None,
    base_url: str = DEFAULT_BASE_URL,
    retries: int = 3,
) -> Path:
    """
    Download one file from a PhysioNet project into the cache, if needed.

    Args:
        slug: Project identifier (e.g., "mimic-iv-demo")
        path: File path within the project (e.g., "hosp/patients.csv.gz")
        version: Project version. Pin it for reproducible, offline-friendly
            use; leave it out to use the latest version.
        cache_dir: Cache folder (default: see get_cache_dir)
        source: Download source - "auto", "physionet", or "aws"
        username: PhysioNet username (or use PHYSIONET_USERNAME env var)
        password: PhysioNet password (or use PHYSIONET_PASSWORD env var)
        base_url: PhysioNet base URL
        retries: Number of retries for transient download errors

    Returns:
        Path to the verified file in the cache

    Raises:
        FileNotFoundError: If the project version has no such file
        NotFoundError: If the project or version is not found
        ForbiddenError: If your account cannot access the project
    """
    with _Remote(base_url, username, password) as remote:
        path = _clean_path(path)
        return _fetch(remote, slug, path, [path], version, cache_dir, source, retries)


def load(
    slug: str,
    table: str,
    version: Optional[str] = None,
    *,
    cache_dir: Optional[str] = None,
    source: str = "auto",
    username: Optional[str] = None,
    password: Optional[str] = None,
    base_url: str = DEFAULT_BASE_URL,
    retries: int = 3,
    **read_kwargs,
) -> pd.DataFrame:
    """
    Load a table from a PhysioNet project as a pandas DataFrame.

    The file is fetched into the cache on first use. Supported formats are
    .csv, .csv.gz, .tsv, .tsv.gz, .parquet and .xlsx. The extension can be
    left out (e.g. "hosp/patients") when only one supported file has that name.

    Args:
        slug: Project identifier (e.g., "mimic-iv-demo")
        table: Table path within the project, with or without its extension
        version: Project version (default: latest)
        read_kwargs: Passed to the pandas reader (e.g. usecols, nrows, dtype)
        Other arguments are as for fetch().

    Returns:
        The table as a DataFrame

    Raises:
        FileNotFoundError: If the project version has no such table
        ValueError: If the file is not a supported table format, or the name
            without an extension matches more than one file
    """
    table = _clean_path(table)
    if _reader_for(table):
        candidates = [table]
    else:
        # The exact name comes last, so a file in another format is reported clearly.
        candidates = [table + extension for extension, _ in TABLE_READERS] + [table]

    with _Remote(base_url, username, password) as remote:
        file_path = _fetch(
            remote, slug, table, candidates, version, cache_dir, source, retries, tables_only=True
        )
    return _reader_for(file_path.name)(file_path, **read_kwargs)


class _Remote:
    """Connect to PhysioNet only when the cache cannot answer."""

    def __init__(self, base_url: str, username: Optional[str], password: Optional[str]):
        if username is None or password is None:
            env_user, env_pass = get_credentials_from_env()
            username = username or env_user
            password = password or env_pass
        self.base_url = base_url
        self.username = username
        self.password = password
        self._client: Optional[PhysioNetClient] = None

    @property
    def client(self) -> PhysioNetClient:
        if self._client is None:
            self._client = PhysioNetClient(
                base_url=self.base_url, username=self.username, password=self.password
            )
        return self._client

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        if self._client is not None:
            self._client.close()


def _fetch(
    remote: _Remote,
    slug: str,
    requested: str,
    candidates: List[str],
    version: Optional[str],
    cache_dir: Optional[str],
    source: str,
    retries: int,
    tables_only: bool = False,
) -> Path:
    """Return the cached file for the one candidate path the project has."""
    _check_name(slug, "project")
    if version is None:
        version = _latest_version(remote, slug)
    _check_name(version, "version")

    cache = get_cache_dir(cache_dir)
    manifest = _manifest(remote, cache, slug, version)
    name, expected_hash = _choose(manifest, requested, candidates, slug, version)
    if tables_only and _reader_for(name) is None:
        raise ValueError(
            f"{name} is not a supported table format. Use physionet.fetch() to get the file "
            f"and read it yourself. Supported: {', '.join(ext for ext, _ in TABLE_READERS)}"
        )

    target = cache / slug / version / PurePosixPath(name)
    if target.is_file():
        return target

    # Download into a uniquely named folder, so parallel jobs fetching the same
    # file never write to one file; whichever finishes last replaces an
    # identical copy. The file keeps its own name, which the progress bar shows.
    staging = target.parent / f".download-{uuid.uuid4().hex}"
    staging.mkdir(parents=True)
    partial = staging / target.name
    try:
        source_type, source_base = _select_source(
            remote.client, slug, version, source, remote.username, verbose=False
        )
        if source_type == "s3":
            _download_file_s3(source_base, f"{slug}/{version}/{name}", partial, expected_hash)
        else:
            _download_file(
                f"{source_base}/{name}", partial, expected_hash, remote.client.session, retries=retries
            )
        os.replace(partial, target)
    finally:
        if partial.exists():
            partial.unlink()
        staging.rmdir()
    return target


def _latest_version(remote: _Remote, slug: str) -> str:
    try:
        return _resolve_version(remote.client, slug, None)
    except (requests.ConnectionError, requests.Timeout) as exc:
        raise ConnectionError(
            f"Could not reach PhysioNet to find the latest version of {slug}. "
            "Pass version= to use files that are already cached."
        ) from exc


def _manifest(remote: _Remote, cache: Path, slug: str, version: str) -> Dict[str, str]:
    """Return {path: sha256} for a project version, caching the checksum list."""
    cached = cache / MANIFEST_FOLDER / slug / version / "SHA256SUMS.txt"
    if cached.is_file():
        entries = _parse_cached_manifest(cached.read_text())
    else:
        try:
            entries = _get_file_manifest(remote.client, slug, version)
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status == 404:
                raise NotFoundError(f"PhysioNet has no project {slug} version {version}") from exc
            if status in (401, 403):
                raise ForbiddenError(
                    f"Access to {slug} {version} was denied. Restricted datasets need a PhysioNet "
                    "account with access to the project: pass username and password, or set "
                    "PHYSIONET_USERNAME and PHYSIONET_PASSWORD."
                ) from exc
            raise
        cached.parent.mkdir(parents=True, exist_ok=True)
        partial = cached.with_name(f".{cached.name}.{uuid.uuid4().hex}.part")
        partial.write_text("".join(f"{checksum}  {name}\n" for name, checksum in entries))
        os.replace(partial, cached)
    # Skip any entry that could point outside the project folder.
    return {name: checksum for name, checksum in entries if _is_safe_path(name)}


def _parse_cached_manifest(text: str) -> List[Tuple[str, str]]:
    entries = []
    for line in text.splitlines():
        checksum, _, name = line.partition("  ")
        if checksum and name:
            entries.append((name, checksum))
    return entries


def _choose(
    manifest: Dict[str, str], requested: str, candidates: List[str], slug: str, version: str
) -> Tuple[str, str]:
    found = [name for name in candidates if name in manifest]
    if len(found) == 1:
        return found[0], manifest[found[0]]
    if len(found) > 1:
        raise ValueError(f"{requested} matches {', '.join(found)}; include the extension")

    message = f"{requested} is not a file in {slug} {version}."
    suggestions = get_close_matches(requested, list(manifest), n=3, cutoff=0.6)
    if suggestions:
        message += f" Did you mean: {', '.join(suggestions)}?"
    raise FileNotFoundError(message)


def _reader_for(name: str) -> Optional[Callable[..., pd.DataFrame]]:
    lower = name.lower()
    for extension, reader in TABLE_READERS:
        if lower.endswith(extension):
            return reader
    return None


def _clean_path(path: str) -> str:
    cleaned = str(path).strip().lstrip("/")
    if not _is_safe_path(cleaned):
        raise ValueError(f"{path!r} is not a valid path within a project")
    return cleaned


def _is_safe_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return bool(parts) and "\\" not in path and not PurePosixPath(path).is_absolute() and all(
        part not in ("", ".", "..") for part in parts
    )


def _check_name(value: str, kind: str) -> None:
    if not isinstance(value, str) or not _NAME.match(value):
        raise ValueError(f"{value!r} is not a valid {kind} name")
