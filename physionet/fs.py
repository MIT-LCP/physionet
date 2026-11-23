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

To read only part of a large file, such as a few seconds of a long recording,
pass stream=True. The parts that are needed are then read with HTTP range
requests, instead of downloading the whole file. They are not checked against
the checksum list or kept in the cache, but a file already in the cache is read
from there:

    with fsspec.open("physionet://mitdb/1.0.0/100.dat", stream=True) as f:
        f.seek(1_000_000)
        data = f.read(10_800)

Listing a version uses only its checksum list, so browsing a dataset does not
download it. Sizes are known for cached files; for other files info() asks the
server, and ls() reports None. The filesystem is read-only.
"""

import re
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from fsspec import AbstractFileSystem
from fsspec.spec import AbstractBufferedFile

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
    _refuse_symlinks,
    get_cache_dir,
)
from physionet.download import S3_BASE_URL, _configure_retry_adapter, _select_source

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
        show_progress: Show a progress bar while a file is downloaded
            (default True)
        stream: Read only the parts of a file that are needed, with HTTP range
            requests, instead of downloading the whole file into the cache
            (default False). A file that is already in the cache is read from
            there, after checking it again if verify=True. Parts that are read
            are not checked against the file's checksum, and are not kept in
            the cache. If the server sends whole files instead of parts, the
            file is downloaded into the cache as without stream. Can also be
            given for one file, as fs.open(path, stream=True).
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
        show_progress: bool = True,
        stream: bool = False,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.cache_dir = cache_dir
        self.source = source
        self.base_url = base_url.rstrip("/")
        self.retries = retries
        self.verify = verify
        self.show_progress = show_progress
        self.stream = stream
        self._remote = _Remote(self.base_url, username, password, token)
        self._lock = threading.Lock()
        self._latest: Dict[str, str] = {}
        self._manifests: Dict[Tuple[str, str], Dict[str, str]] = {}
        self._file_urls: Dict[Tuple[str, str], str] = {}

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
                    self._remote,
                    get_cache_dir(self.cache_dir),
                    _origin_folder(self.base_url),
                    slug,
                    resolved,
                    self.retries,
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

    def _open(self, path, mode="rb", block_size=None, stream=None, **kwargs):
        if mode != "rb":
            raise PermissionError("physionet:// is read-only")
        if not (self.stream if stream is None else stream):
            return open(self.local_path(path), "rb")

        slug, version, rest = self._split(path)
        if not slug or not version or not rest:
            raise IsADirectoryError(f"physionet://{self._strip_protocol(path)} is not a file")
        rest = _clean_path(rest)
        resolved = self._resolve(slug, version)
        # A file that is already in the cache was checked when it was
        # downloaded, and needs no requests
        cache = get_cache_dir(self.cache_dir)
        cached = self._cached_file(slug, resolved, rest)
        if cached.is_file():
            _refuse_symlinks(cache, cached)
            if self.verify:
                # Check its checksum, and download it again if it changed
                return open(self.local_path(path), "rb")
            return open(cached, "rb")
        # The checksum list is not needed, and for a large dataset it is
        # large, so a missing file is reported by the server instead
        try:
            return _RangeFile(
                self,
                path,
                self._file_url(slug, resolved, rest),
                self._client().session,
                timeout=self._client().timeout,
                block_size=block_size,
            )
        except _RangesNotSupported:
            # Download the whole file once, into the cache, as without stream
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
                self._remote,
                slug,
                rest,
                [rest],
                resolved,
                self.cache_dir,
                self.source,
                self.retries,
                self.verify,
                show_progress=self.show_progress,
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

    def _file_url(self, slug: str, version: str, rest: str) -> str:
        """The URL to read a file from, from the same source as downloads use."""
        key = (slug, version)
        with self._lock:
            base = self._file_urls.get(key)
        if base is None:
            client = self._client()
            source_type, base = _select_source(
                client, slug, version, self.source, self._remote.username, verbose=False
            )
            if source_type == "s3":
                # The open data mirror can be read over HTTPS, without boto3
                base = f"{S3_BASE_URL}/{slug}/{version}"
            _configure_retry_adapter(client.session, f"{base}/", self.retries, adapter_prefix=base)
            with self._lock:
                self._file_urls[key] = base
        return f"{base}/{rest}"

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


class _RangeFile(AbstractBufferedFile):
    """
    A file read with HTTP range requests, for PhysioNetFileSystem(stream=True).

    Requests start at DEFAULT_BLOCK_SIZE bytes, and double, up to
    MAX_BLOCK_SIZE, while each read carries on from where the last request
    ended, so that reading a whole file takes few requests while a small read
    from the middle of a large file fetches little more than it needs. The
    first request also gives the size of the file.
    """

    DEFAULT_BLOCK_SIZE = 32 * 1024
    MAX_BLOCK_SIZE = 8 * 1024 * 1024

    def __init__(self, fs, path, url, session, timeout, block_size=None):
        # Set before the first request, for its error messages. Until the
        # file is set up, it counts as closed, so nothing is done on deletion.
        self.path = path
        self.closed = True
        self.url = url
        self.session = session
        self.timeout = timeout
        self._initial_request_size = block_size or self.DEFAULT_BLOCK_SIZE
        self._request_size = self._initial_request_size
        self._buffer_start = 0
        self._buffer, size = self._request(0, self._initial_request_size)
        # fsspec's own read-ahead cache is not used, since the buffer here
        # sets the size of each request
        super().__init__(fs, path, mode="rb", block_size=self._initial_request_size, cache_type="none", size=size)

    def _fetch_range(self, start, end):
        end = min(end, self.size)
        if start >= end:
            return b""
        buffer_end = self._buffer_start + len(self._buffer)
        if self._buffer_start <= start and end <= buffer_end:
            return self._buffer[start - self._buffer_start : end - self._buffer_start]

        # Use the part of the range that is already in the buffer
        head = b""
        if self._buffer_start <= start < buffer_end:
            head = self._buffer[start - self._buffer_start :]
            start = buffer_end
        if start == buffer_end:
            self._request_size = min(2 * self._request_size, self.MAX_BLOCK_SIZE)
        else:
            self._request_size = self._initial_request_size

        self._buffer, _ = self._request(start, max(end, start + self._request_size))
        self._buffer_start = start
        return head + self._buffer[: end - start]

    def _request(self, start, end):
        """
        Request bytes start to end - 1, returning them and the size of the file.

        The body is only read for a partial response, and only up to the length
        that its Content-Range gives. A server that sends the whole file
        instead raises _RangesNotSupported for the first request, and OSError
        after that, without reading the file.
        """
        response = self.session.get(
            self.url,
            # Compression would change the byte offsets
            headers={"Range": f"bytes={start}-{end - 1}", "Accept-Encoding": "identity"},
            timeout=self.timeout,
            stream=True,
        )
        try:
            # Later requests go straight to where the file was redirected
            if response.history and response.url.startswith("https://"):
                self.url = response.url
            content_range = response.headers.get("Content-Range", "")
            if response.status_code == 206:
                match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", content_range.strip())
                if not match or int(match.group(1)) != start or int(match.group(2)) >= end:
                    raise OSError(f"Unexpected Content-Range {content_range!r} for {self.path}")
                return self._read_body(response, int(match.group(2)) - start + 1), int(match.group(3))
            if response.status_code == 416:
                # The range starts at or after the end of the file
                match = re.fullmatch(r"bytes \*/(\d+)", content_range.strip())
                if not match:
                    raise OSError(f"Unexpected Content-Range {content_range!r} for {self.path}")
                return b"", int(match.group(1))
            if response.status_code == 200:
                # The server ignores the range and would send the whole file
                if getattr(self, "size", None) is None:
                    raise _RangesNotSupported(self.path)
                raise OSError(f"The server stopped sending parts of physionet://{self.path}")
            if response.status_code == 404:
                raise FileNotFoundError(f"physionet://{self.path}")
            if response.status_code in (401, 403):
                raise PermissionError(f"physionet://{self.path}: access denied ({response.status_code})")
            response.raise_for_status()
            raise OSError(f"Unexpected response {response.status_code} for physionet://{self.path}")
        finally:
            response.close()

    def _read_body(self, response, length):
        """Read a response body of the given length, and no more."""
        data = bytearray()
        for chunk in response.iter_content(chunk_size=65536):
            data += chunk
            if len(data) > length:
                raise OSError(f"The server sent more than was asked for, for physionet://{self.path}")
        if len(data) != length:
            raise OSError(f"The server sent {len(data)} of {length} bytes for physionet://{self.path}")
        return bytes(data)


class _RangesNotSupported(Exception):
    """The server sends whole files, not the parts that were asked for."""
