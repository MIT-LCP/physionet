"""Read WFDB waveform records and annotations from PhysioNet, through the cache.

Records are read with the wfdb package (pip install "physionet[wfdb]"). Only
the files that the read needs are downloaded: the record's header, then the
signal files and, for a multi-segment record, the segments that hold the
requested time range. They are kept in the same cache as physionet.fetch(), and
checked against the project's checksums when they are downloaded.
"""

import os
import re
from datetime import timedelta
from pathlib import Path, PurePosixPath
from typing import Optional, Sequence, Union

from physionet.data import DEFAULT_BASE_URL, _Remote, _check_name, _clean_path, _fetch, _latest_version

__all__ = ["load_record", "load_annotation"]

Time = Union[int, float, str, timedelta, None]

_UNITS = {"ms": 0.001, "s": 1, "sec": 1, "m": 60, "min": 60, "h": 3600, "hr": 3600}
_NUMBER_WITH_UNIT = re.compile(r"^\s*(\d+(?:\.\d*)?|\.\d+)\s*([a-z]*)\s*$", re.IGNORECASE)
_CLOCK = re.compile(r"^\s*(?:(\d+):)?(\d+):(\d+(?:\.\d*)?)\s*$")


def load_record(
    slug: str,
    record: str,
    version: Optional[str] = None,
    *,
    start: Time = None,
    end: Time = None,
    channels: Optional[Sequence[Union[int, str]]] = None,
    physical: bool = True,
    cache_dir: Optional[str] = None,
    source: str = "auto",
    username: Optional[str] = None,
    password: Optional[str] = None,
    token: Optional[str] = None,
    base_url: str = DEFAULT_BASE_URL,
    retries: int = 3,
    show_progress: bool = True,
):
    """
    Read a WFDB record from a PhysioNet project, or part of it.

    Args:
        slug: Project identifier (e.g., "mitdb")
        record: Record name within the project, without an extension (e.g.,
            "100", or "p00/p000020/p000020-2183-04-28-17-47")
        version: Project version (default: latest). Pin it to read cached
            files offline.
        start: Where to start reading, from the beginning of the record: seconds
            (e.g. 300), a string such as "90s", "5min", "1.5h" or "00:05:00", or
            a timedelta. Default: the beginning.
        end: Where to stop reading, in the same form. Default: the end.
        channels: Channels to read, as indices (e.g. [0, 1]) or signal names
            (e.g. ["II", "V5"]). Default: all.
        physical: Return physical values in p_signal (default True), or digital
            values in d_signal
        Other arguments are as for physionet.fetch().

    Returns:
        A wfdb.Record. Its p_signal holds the samples (one column per channel),
        sig_name the channel names, fs the sampling frequency, and
        to_dataframe() returns the samples with a time index.

    Raises:
        ImportError: If the wfdb package is not installed
        FileNotFoundError: If the project version has no such record
        ValueError: If start or end is not a valid time, or start is not before end
        NotFoundError, ForbiddenError, ConnectionError: As for physionet.fetch()
    """
    wfdb = _import_wfdb()
    record = _record_name(record)
    channel_names, channel_indices = _split_channels(channels)

    with _Remote(base_url, username, password, token) as remote:
        reader = _Reader(remote, slug, version, cache_dir, source, retries, show_progress)
        header_path = reader.fetch(f"{record}.hea")
        local_name = str(header_path)[: -len(".hea")]
        header = reader.read(lambda: wfdb.rdheader(local_name))

        fs = float(header.fs)
        sampfrom = _to_sample(start, fs, "start") if start is not None else 0
        sampto = _to_sample(end, fs, "end") if end is not None else None
        if header.sig_len is not None:
            if sampfrom >= header.sig_len and header.sig_len > 0:
                raise ValueError(
                    f"start is at or after the end of {record}, which is "
                    f"{_format_seconds(header.sig_len / fs)} long"
                )
            sampto = header.sig_len if sampto is None else min(sampto, header.sig_len)
        if sampto is not None and sampto <= sampfrom:
            raise ValueError("start must be before end")

        return reader.read(
            lambda: wfdb.rdrecord(
                local_name,
                sampfrom=sampfrom,
                sampto=sampto,
                channels=channel_indices,
                channel_names=channel_names,
                physical=physical,
            )
        )


def load_annotation(
    slug: str,
    record: str,
    extension: str = "atr",
    version: Optional[str] = None,
    *,
    start: Time = None,
    end: Time = None,
    cache_dir: Optional[str] = None,
    source: str = "auto",
    username: Optional[str] = None,
    password: Optional[str] = None,
    token: Optional[str] = None,
    base_url: str = DEFAULT_BASE_URL,
    retries: int = 3,
    show_progress: bool = True,
):
    """
    Read a WFDB annotation file from a PhysioNet project.

    Args:
        slug: Project identifier (e.g., "mitdb")
        record: Record name within the project (e.g., "100")
        extension: Annotator, i.e. the annotation file's extension (default "atr")
        version: Project version (default: latest)
        start, end: Only read annotations in this range, as for load_record().
            Times are converted to samples with the annotation file's sampling
            frequency, or the record header's if the file does not give one.
        Other arguments are as for physionet.fetch().

    Returns:
        A wfdb.Annotation. Its sample holds the sample number of each
        annotation, symbol the labels, and fs the sampling frequency.

    Raises:
        ImportError: If the wfdb package is not installed
        FileNotFoundError: If the project version has no such annotation file
        ValueError: If start or end is not a valid time, or start is not before end
    """
    wfdb = _import_wfdb()
    record = _record_name(record)
    if not re.fullmatch(r"[A-Za-z0-9_]+", extension or ""):
        raise ValueError(f"{extension!r} is not a valid annotation extension")

    with _Remote(base_url, username, password, token) as remote:
        reader = _Reader(remote, slug, version, cache_dir, source, retries, show_progress)
        annotation_path = reader.fetch(f"{record}.{extension}")
        local_name = str(annotation_path)[: -len(extension) - 1]
        if start is None and end is None:
            return reader.read(lambda: wfdb.rdann(local_name, extension))

        # Times need a sampling frequency: the annotation file's, if it has one
        annotation = reader.read(lambda: wfdb.rdann(local_name, extension))
        fs = annotation.fs
        if not fs:
            reader.fetch(f"{record}.hea")
            fs = reader.read(lambda: wfdb.rdheader(local_name)).fs
        sampfrom = _to_sample(start, float(fs), "start") if start is not None else 0
        sampto = _to_sample(end, float(fs), "end") if end is not None else None
        if sampto is not None and sampto <= sampfrom:
            raise ValueError("start must be before end")
        return reader.read(lambda: wfdb.rdann(local_name, extension, sampfrom=sampfrom, sampto=sampto))


class _Reader:
    """Fetch a project's files into the cache when wfdb asks for them."""

    def __init__(self, remote, slug, version, cache_dir, source, retries, show_progress):
        _check_name(slug, "project")
        if version is None:
            version = _latest_version(remote, slug, retries=retries)
        self.remote = remote
        self.slug = slug
        self.version = version
        self.cache_dir = cache_dir
        self.source = source
        self.retries = retries
        self.show_progress = show_progress
        self.root: Optional[Path] = None
        self.fetched = set()

    def fetch(self, name: str) -> Path:
        """Return the cached path of a file in the project, downloading it if needed."""
        path = _fetch(
            self.remote,
            self.slug,
            name,
            [name],
            self.version,
            self.cache_dir,
            self.source,
            self.retries,
            False,
            show_progress=self.show_progress,
        )
        if self.root is None:
            self.root = path.parents[len(PurePosixPath(name).parts) - 1]
        self.fetched.add(name)
        return path

    def read(self, read):
        """
        Call read(); each time wfdb cannot find a file of the project, fetch
        it and try again.
        """
        while True:
            try:
                return read()
            except FileNotFoundError as exc:
                name = self._project_path(exc.filename)
                if name is None or name in self.fetched:
                    raise
                try:
                    self.fetch(name)
                except FileNotFoundError as missing:
                    raise FileNotFoundError(
                        f"The record needs {name}, which is not a file in {self.slug} {self.version}"
                    ) from missing

    def _project_path(self, filename) -> Optional[str]:
        """The path within the project of a file wfdb could not open, if it is inside the project."""
        if not filename or self.root is None:
            return None
        root = os.path.realpath(self.root)
        wanted = os.path.realpath(os.fspath(filename))
        if os.path.commonpath([root, wanted]) != root or wanted == root:
            return None
        return PurePosixPath(*Path(os.path.relpath(wanted, root)).parts).as_posix()


def _import_wfdb():
    try:
        import wfdb
    except ImportError:
        raise ImportError(
            'Reading WFDB records needs the wfdb package: pip install "physionet[wfdb]"'
        ) from None
    return wfdb


def _record_name(record: str) -> str:
    name = _clean_path(record)
    if name.endswith(".hea"):
        name = name[: -len(".hea")]
    if not name:
        raise ValueError(f"{record!r} is not a valid record name")
    return name


def _split_channels(channels):
    """Return (channel_names, channel_indices) for wfdb, from a list of names or indices."""
    if channels is None:
        return None, None
    if isinstance(channels, (str, int)):
        channels = [channels]
    channels = list(channels)
    if all(isinstance(channel, str) for channel in channels):
        return channels, None
    if all(isinstance(channel, int) and not isinstance(channel, bool) and channel >= 0 for channel in channels):
        return None, channels
    raise ValueError("channels must be all signal names or all channel numbers (0 or more)")


def _to_seconds(value: Time, what: str) -> float:
    if isinstance(value, timedelta):
        seconds = value.total_seconds()
    elif isinstance(value, bool):
        seconds = -1.0
    elif isinstance(value, (int, float)):
        seconds = float(value)
    elif isinstance(value, str) and _CLOCK.match(value):
        hours, minutes, secs = _CLOCK.match(value).groups()
        seconds = int(hours or 0) * 3600 + int(minutes) * 60 + float(secs)
    elif isinstance(value, str) and _NUMBER_WITH_UNIT.match(value):
        number, unit = _NUMBER_WITH_UNIT.match(value).groups()
        unit = unit.lower() or "s"
        seconds = float(number) * _UNITS[unit] if unit in _UNITS else -1.0
    else:
        seconds = -1.0
    if not 0 <= seconds < float("inf"):
        raise ValueError(
            f"{what} must be a time such as 300 (seconds), '90s', '5min', '1.5h' or '00:05:00', not {value!r}"
        )
    return seconds


def _to_sample(value: Time, fs: float, what: str) -> int:
    return int(round(_to_seconds(value, what) * fs))


def _format_seconds(seconds: float) -> str:
    return str(timedelta(seconds=round(seconds)))
