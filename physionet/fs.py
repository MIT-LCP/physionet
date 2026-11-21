"""Open PhysioNet files with any fsspec-aware tool, using physionet:// paths.

Once the package is installed, pandas, polars, DuckDB, xarray, Dask and other
tools that use fsspec can read PhysioNet files directly:

    import pandas as pd
    patients = pd.read_csv("physionet://mimic-iv-demo/2.2/hosp/patients.csv.gz")

Paths are physionet://<slug>/<version>/<path within the project>. The version
can be "latest", which is looked up online once per filesystem. Pinning a
version keeps results reproducible and lets cached files be read offline.

Files are opened through the same cache as physionet.fetch(): each file is
downloaded once, its SHA-256 checksum is checked against the project's
SHA256SUMS.txt, and later reads come from the cache. Credentials work as for
fetch() (the sign-in saved by `physionet login`, PHYSIONET_TOKEN, or a
username and password), and can also be passed as storage options:

    pd.read_csv("physionet://mimiciv/3.1/hosp/patients.csv.gz",
                storage_options={"token": "pnpat_..."})

Listing a version uses only its checksum list, so browsing a dataset does not
download it. Sizes are known for cached files; for other files info() asks the
server, and ls() reports None. The filesystem is read-only.
"""

import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from fsspec import AbstractFileSystem

from physionet.api.exceptions import ForbiddenError, NotFoundError
from physionet.data import (
    DEFAULT_BASE_URL,
    _Remote,
    _check_name,
    _clean_path,
    _fetch,
    _latest_version,
    _manifest,
    _origin_folder,
    get_cache_dir,
)

__all__ = ["PhysioNetFileSystem"]


class PhysioNetFileSystem(AbstractFileSystem):
    """A read-only fsspec filesystem for PhysioNet projects (protocol "physionet").

    Args:
        cache_dir: Cache folder (default: see physionet.get_cache_dir)
        source: "auto", "physionet", or "aws", as for physionet.fetch()
        username: PhysioNet username (or use PHYSIONET_USERNAME env var)
        password: PhysioNet password (or use PHYSIONET_PASSWORD env var)
        token: PhysioNet access token (or use PHYSIONET_TOKEN env var).
            Without credentials, the sign-in saved by `physionet login` is used.
        base_url: PhysioNet base URL
        retries: Number of retries for transient download errors
        verify: Re-check the checksum of files that are already cached
    """

    protocol = "physionet"
    root_marker = ""

    def __init__(
        self,
        cache_dir: Optional[str] = None,
        source: str = "auto",
        username: Optional[str] = None,
        password: Optional[str] = None,
        token: Optional[str] = None,
        base_url: str = DEFAULT_BASE_URL,
        retries: int = 3,
        verify: bool = False,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.cache_dir = cache_dir
        self.source = source
        self.base_url = base_url.rstrip("/")
        self.retries = retries
        self.verify = verify
        self._remote = _Remote(self.base_url, username, password, token)
        self._lock = threading.Lock()
        self._latest: Dict[str, str] = {}
        self._manifests: Dict[Tuple[str, str], Dict[str, str]] = {}

    @classmethod
    def _strip_protocol(cls, path):
        if isinstance(path, list):
            return [cls._strip_protocol(item) for item in path]
        path = str(path)
        for prefix in ("physionet://", "physionet:"):
            if path.startswith(prefix):
                path = path[len(prefix):]
                break
        return path.strip("/")

    # --- Paths, versions and checksum lists ---

    def _split(self, path: str) -> Tuple[Optional[str], Optional[str], str]:
        """Split a path into (slug, version as written, path within the project)."""
        path = self._strip_protocol(path)
        parts = path.split("/", 2) if path else []
        slug = parts[0] if parts else None
        version = parts[1] if len(parts) > 1 else None
        rest = parts[2] if len(parts) > 2 else ""
        if slug is not None:
            _check_name(slug, "project")
        if version is not None and version != "latest":
            _check_name(version, "version")
        return slug, version, rest

    def _client(self):
        # The client is created on first use; several threads may get here at once.
        with self._lock:
            return self._remote.client

    def _resolve(self, slug: str, version: str) -> str:
        if version != "latest":
            return version
        with self._lock:
            known = self._latest.get(slug)
        if known is None:
            self._client()
            with _mapped_errors(f"{slug}/latest"):
                known = _latest_version(self._remote, slug)
            with self._lock:
                self._latest[slug] = known
        return known

    def _files(self, slug: str, version: str) -> Dict[str, str]:
        """{path: sha256} for a project version, from its cached checksum list."""
        resolved = self._resolve(slug, version)
        key = (slug, resolved)
        with self._lock:
            files = self._manifests.get(key)
        if files is None:
            self._client()
            with _mapped_errors(f"{slug}/{version}"):
                files, _ = _manifest(
                    self._remote, get_cache_dir(self.cache_dir), _origin_folder(self.base_url), slug, resolved
                )
            with self._lock:
                self._manifests[key] = files
        return files

    def _cached_file(self, slug: str, version: str, rest: str) -> Path:
        resolved = self._resolve(slug, version)
        return get_cache_dir(self.cache_dir) / _origin_folder(self.base_url) / slug / resolved / rest

    # --- fsspec interface ---

    def ls(self, path, detail=True, **kwargs):
        slug, version, rest = self._split(path)
        if slug is None:
            with _mapped_errors(""):
                projects = self._client().projects.list_published()
            names = sorted({project.slug for project in projects})
            entries = [_directory(name) for name in names]
        elif version is None:
            entries = [_directory(f"{slug}/{v}") for v in self._versions(slug)]
        else:
            files = self._files(slug, version)
            base = f"{slug}/{version}"
            if rest in files:
                entries = [self._file_entry(slug, version, rest, files[rest])]
            else:
                prefix = f"{rest}/" if rest else ""
                children: Dict[str, Optional[str]] = {}
                for name, checksum in files.items():
                    if name.startswith(prefix):
                        child, _, below = name[len(prefix):].partition("/")
                        children.setdefault(child, None if below else checksum)
                        if below:
                            children[child] = None
                if not children:
                    raise FileNotFoundError(f"physionet://{base}/{rest}")
                entries = []
                for child, checksum in sorted(children.items()):
                    relative = f"{prefix}{child}"
                    if checksum is None:
                        entries.append(_directory(f"{base}/{relative}"))
                    else:
                        entries.append(self._file_entry(slug, version, relative, checksum))
        return entries if detail else [entry["name"] for entry in entries]

    def info(self, path, **kwargs):
        slug, version, rest = self._split(path)
        if slug is None:
            return _directory("")
        if version is None:
            self._versions(slug)
            return _directory(slug)
        files = self._files(slug, version)
        if not rest:
            return _directory(f"{slug}/{version}")
        if rest in files:
            entry = self._file_entry(slug, version, rest, files[rest])
            if entry["size"] is None:
                entry["size"] = self._remote_size(slug, version, rest)
            return entry
        if any(name.startswith(f"{rest}/") for name in files):
            return _directory(f"{slug}/{version}/{rest}")
        raise FileNotFoundError(f"physionet://{slug}/{version}/{rest}")

    def _open(self, path, mode="rb", **kwargs):
        if mode != "rb":
            raise PermissionError("physionet:// is read-only")
        return open(self.local_path(path), "rb")

    def local_path(self, path) -> Path:
        """Download a file into the cache if needed, and return its local path."""
        slug, version, rest = self._split(path)
        if not slug or not version or not rest:
            raise IsADirectoryError(f"physionet://{self._strip_protocol(path)} is not a file")
        rest = _clean_path(rest)
        resolved = self._resolve(slug, version)
        self._client()
        with _mapped_errors(f"{slug}/{version}/{rest}"):
            return _fetch(
                self._remote, slug, rest, [rest], resolved, self.cache_dir, self.source, self.retries, self.verify
            )

    def ukey(self, path):
        """The file's SHA-256 checksum, which never changes for a published version."""
        slug, version, rest = self._split(path)
        files = self._files(slug, version) if slug and version else {}
        if rest not in files:
            raise FileNotFoundError(f"physionet://{self._strip_protocol(path)}")
        return files[rest]

    def checksum(self, path):
        return int(self.ukey(path)[:16], 16)

    # --- Helpers ---

    def _versions(self, slug: str) -> List[str]:
        with _mapped_errors(slug):
            versions = [v.version for v in self._client().projects.list_versions(slug)]
        if not versions:
            raise FileNotFoundError(f"physionet://{slug}")
        return versions

    def _file_entry(self, slug: str, version: str, rest: str, checksum: str) -> dict:
        cached = self._cached_file(slug, version, rest)
        size = cached.stat().st_size if cached.is_file() and not cached.is_symlink() else None
        return {"name": f"{slug}/{version}/{rest}", "type": "file", "size": size, "sha256": checksum}

    def _remote_size(self, slug: str, version: str, rest: str) -> Optional[int]:
        """Ask the server for a file's size without downloading it."""
        resolved = self._resolve(slug, version)
        client = self._client()
        url = f"{client.base_url}/files/{slug}/{resolved}/{rest}"
        try:
            response = client.session.head(url, allow_redirects=True, timeout=client.timeout)
        except requests.RequestException:
            return None
        length = response.headers.get("Content-Length") if response.ok else None
        return int(length) if length and length.isdigit() else None


def _directory(name: str) -> dict:
    return {"name": name, "type": "directory", "size": 0}


class _mapped_errors:
    """Raise the standard errors that fsspec users expect for missing or forbidden paths."""

    def __init__(self, path: str):
        self.path = path

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None and issubclass(exc_type, NotFoundError):
            raise FileNotFoundError(f"physionet://{self.path}: {exc}") from exc
        if exc_type is not None and issubclass(exc_type, ForbiddenError):
            raise PermissionError(f"physionet://{self.path}: {exc}") from exc
        return False
