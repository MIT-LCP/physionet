"""Preview PhysioNet tables without downloading them.

head() returns the first rows of a table, and tables() lists a version's
tables with their column names. Both read only the start of each file: the
download is stopped as soon as enough rows have arrived, and gzipped files are
decompressed as they stream. Files that are already in the fetch() cache are
read from there instead.

CSV and TSV files (optionally gzipped) can be previewed. Parquet and Excel
files need the whole file, so use physionet.load() for those.
"""

import gzip
import io
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath
from typing import Iterator, List, Optional, Tuple

import pandas as pd
import requests

from physionet.api.exceptions import ForbiddenError, NotFoundError
from physionet.data import (
    DEFAULT_BASE_URL,
    TABLE_READERS,
    _Remote,
    _check_name,
    _choose,
    _clean_path,
    _latest_version,
    _manifest,
    _origin_folder,
    _reader_for,
    get_cache_dir,
)
from physionet.download import _select_source

__all__ = ["head", "tables"]

# Files that can be previewed from their first bytes, and their separators.
PREVIEW_FORMATS = ((".csv.gz", ","), (".csv", ","), (".tsv.gz", "\t"), (".tsv", "\t"))
# Stop reading after this much (decompressed) text, even if fewer rows arrived.
MAX_PREVIEW_BYTES = 16 * 1024 * 1024
# tables() reads one header per table; above this, ask for a narrower prefix.
MAX_TABLES = 500
_CHUNK = 64 * 1024
_HEADER_THREADS = 8


def head(
    slug: str,
    path: str,
    version: Optional[str] = None,
    n: int = 10,
    *,
    cache_dir: Optional[str] = None,
    source: str = "auto",
    username: Optional[str] = None,
    password: Optional[str] = None,
    token: Optional[str] = None,
    base_url: str = DEFAULT_BASE_URL,
) -> pd.DataFrame:
    """
    Return the first rows of a table, reading only the start of the file.

    Args:
        slug: Project identifier (e.g., "mimic-iv-demo")
        path: Table path within the project, with or without the extension
            (e.g., "hosp/patients" or "hosp/patients.csv.gz")
        version: Project version (default: latest)
        n: Number of rows to return
        cache_dir, source, username, password, token, base_url: as for
            physionet.fetch()

    Returns:
        A DataFrame with up to n rows (fewer if the table is shorter).

    Raises:
        FileNotFoundError: If the project version has no such table
        NotFoundError: If the project or version is not found
        ForbiddenError: If your account cannot access the project
        ValueError: If the file cannot be previewed (Parquet, Excel or not a
            table), or n is not a positive whole number
    """
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise ValueError(f"n must be a positive whole number, not {n!r}")
    with _Remote(base_url, username, password, token) as remote:
        version, manifest = _version_and_manifest(remote, slug, version, cache_dir)
        name = _choose_table(manifest, path, slug, version)
        separator = _separator(name)
        if separator is None:
            raise ValueError(
                f"{name} cannot be previewed without downloading it. "
                f"Use physionet.load({slug!r}, {name!r}) instead."
            )
        return _read_rows(remote, slug, version, name, separator, n, cache_dir, source)


def tables(
    slug: str,
    version: Optional[str] = None,
    column: Optional[str] = None,
    *,
    prefix: Optional[str] = None,
    cache_dir: Optional[str] = None,
    source: str = "auto",
    username: Optional[str] = None,
    password: Optional[str] = None,
    token: Optional[str] = None,
    base_url: str = DEFAULT_BASE_URL,
) -> pd.DataFrame:
    """
    List a version's tables and their column names, reading only each header.

    Args:
        slug: Project identifier (e.g., "mimic-iv-demo")
        version: Project version (default: latest)
        column: Only list tables with this column (case-insensitive)
        prefix: Only list tables under this folder (e.g., "hosp")
        cache_dir, source, username, password, token, base_url: as for
            physionet.fetch()

    Returns:
        A DataFrame with one row per table: "table" (its path), "columns" (a
        list of names, or None if the file cannot be previewed, such as
        Parquet or Excel), and "error" (why the header could not be read, or
        None).

    Raises:
        NotFoundError: If the project or version is not found
        ForbiddenError: If your account cannot access the project
        ValueError: If more than MAX_TABLES tables match; pass a prefix
    """
    with _Remote(base_url, username, password, token) as remote:
        version, manifest = _version_and_manifest(remote, slug, version, cache_dir)
        names = sorted(name for name in manifest if _reader_for(name))
        if prefix:
            folder = _clean_path(prefix).rstrip("/") + "/"
            names = [name for name in names if name.startswith(folder)]
        if len(names) > MAX_TABLES:
            raise ValueError(
                f"{slug} {version} has {len(names)} tables, more than the {MAX_TABLES} that are "
                "listed at once. Pass a prefix (e.g. prefix=\"subfolder\", or --prefix) to list fewer."
            )

        # Starting the client before the threads saves each thread from doing it.
        remote.client

        def header(name: str) -> Tuple[str, Optional[List[str]], Optional[str]]:
            separator = _separator(name)
            if separator is None:
                return name, None, None
            try:
                frame = _read_rows(remote, slug, version, name, separator, 0, cache_dir, source)
            except (OSError, ValueError, ForbiddenError, NotFoundError, requests.RequestException) as exc:
                return name, None, str(exc) or type(exc).__name__
            return name, [str(column) for column in frame.columns], None

        with ThreadPoolExecutor(max_workers=_HEADER_THREADS) as executor:
            rows = list(executor.map(header, names))

    # object dtype keeps None as None; pandas 3 would store the text columns as
    # strings and turn None into NaN.
    result = pd.DataFrame(rows, columns=["table", "columns", "error"], dtype=object)
    if column is not None:
        wanted = column.lower()
        keep = result["columns"].map(lambda cols: cols is not None and wanted in (c.lower() for c in cols))
        result = result[keep].reset_index(drop=True)
    return result


# --- Shared steps ---


def _version_and_manifest(remote: _Remote, slug: str, version: Optional[str], cache_dir: Optional[str]):
    _check_name(slug, "project")
    if version is None:
        version = _latest_version(remote, slug)
    _check_name(version, "version")
    manifest, _ = _manifest(remote, get_cache_dir(cache_dir), _origin_folder(remote.base_url), slug, version)
    return version, manifest


def _choose_table(manifest, path: str, slug: str, version: str) -> str:
    path = _clean_path(path)
    if _reader_for(path) or path in manifest:
        candidates = [path]
    else:
        candidates = [path + extension for extension, _ in TABLE_READERS] + [path]
    name, _ = _choose(manifest, path, candidates, slug, version)
    return name


def _separator(name: str) -> Optional[str]:
    lower = name.lower()
    for extension, separator in PREVIEW_FORMATS:
        if lower.endswith(extension):
            return separator
    return None


def _read_rows(remote, slug, version, name, separator, n, cache_dir, source) -> pd.DataFrame:
    """Parse the first n rows (n=0 for just the header) from the start of a file.

    Text is read in chunks until pandas can parse n complete rows, so quoted
    fields that contain line breaks are handled. If MAX_PREVIEW_BYTES is
    reached first, the rows parsed so far are returned.
    """
    text = ""
    parsed = None
    for piece, at_end in _text_chunks(remote, slug, version, name, cache_dir, source):
        text += piece
        # Until the end of the file, leave out a last line that may be cut off.
        complete = text if at_end else text[: text.rfind("\n") + 1]
        if not complete:
            continue
        try:
            frame = pd.read_csv(io.StringIO(complete), sep=separator, nrows=n, low_memory=False)
        except pd.errors.EmptyDataError:
            frame = pd.DataFrame() if at_end else None
        except pd.errors.ParserError:
            if at_end:
                raise
            frame = None  # probably a quoted field that continues in the next chunk
        if frame is not None:
            parsed = frame
            if len(frame) >= n or at_end:
                return frame
    return parsed if parsed is not None else pd.DataFrame()


def _text_chunks(remote, slug, version, name, cache_dir, source) -> Iterator[Tuple[str, bool]]:
    """Yield (text, at_end) from the start of a file, from the cache or the server.

    at_end is True only for the last piece of the whole file. Reading stops
    without it once MAX_PREVIEW_BYTES of text have been read.
    """
    cached = get_cache_dir(cache_dir) / _origin_folder(remote.base_url) / slug / version / PurePosixPath(name)
    if cached.is_file() and not cached.is_symlink():
        yield from _local_chunks(cached)
    else:
        yield from _remote_chunks(remote, slug, version, name, source)


def _local_chunks(path: Path) -> Iterator[Tuple[str, bool]]:
    opener = gzip.open if path.name.lower().endswith(".gz") else open
    decoder = _decoder()
    total = 0
    with opener(path, "rb") as handle:
        while total < MAX_PREVIEW_BYTES:
            data = handle.read(_CHUNK)
            if not data:
                yield decoder.decode(b"", final=True), True
                return
            total += len(data)
            yield decoder.decode(data), False


def _remote_chunks(remote, slug, version, name, source) -> Iterator[Tuple[str, bool]]:
    client = remote.client
    gzipped = name.lower().endswith(".gz")
    source_type, source_base = _select_source(client, slug, version, source, remote.username, verbose=False)
    if source_type == "s3":
        import boto3

        body = boto3.client("s3").get_object(Bucket=source_base, Key=f"{slug}/{version}/{name}")["Body"]
        raw_chunks = body.iter_chunks(_CHUNK)
        close = body.close
    else:
        response = client.session.get(f"{source_base}/{name}", stream=True, timeout=client.timeout)
        if response.status_code in (401, 403):
            response.close()
            raise ForbiddenError(f"Access to {name} in {slug} {version} was denied")
        if response.status_code == 404:
            response.close()
            raise FileNotFoundError(f"{name} is not a file in {slug} {version}")
        response.raise_for_status()
        # A .gz file is read exactly as stored and decompressed here, even if
        # the server marks it as gzip-encoded; other files are decoded as sent.
        raw_chunks = response.raw.stream(_CHUNK, decode_content=not gzipped)
        close = response.close

    inflate = _Gunzip() if gzipped else None
    decoder = _decoder()
    total = 0
    try:
        for raw in raw_chunks:
            data = inflate.feed(raw) if inflate else raw
            total += len(data)
            yield decoder.decode(data), False
            if total >= MAX_PREVIEW_BYTES:
                return
        yield decoder.decode(b"", final=True), True
    finally:
        close()


class _Gunzip:
    """Decompress a gzip stream incrementally, including files of several gzip members."""

    def __init__(self):
        self._inflate = zlib.decompressobj(wbits=31)

    def feed(self, data: bytes) -> bytes:
        out = []
        while data:
            out.append(self._inflate.decompress(data))
            if not self._inflate.eof:
                break
            data = self._inflate.unused_data
            self._inflate = zlib.decompressobj(wbits=31)
        return b"".join(out)


def _decoder():
    import codecs

    return codecs.getincrementaldecoder("utf-8-sig")(errors="replace")
