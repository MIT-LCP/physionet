"""Tests for reading physionet:// files with HTTP range requests (stream=True)."""

import hashlib
import re

import fsspec
import pytest
import requests_mock

from physionet.download import S3_BASE_URL
from physionet.fs import PhysioNetFileSystem, _RangeFile

BASE = "https://physionet.org"
FILES = f"{BASE}/files/demo/1.0"
MIRROR = f"{S3_BASE_URL}/demo/1.0"
VERSIONS = [{"slug": "demo", "title": "Demo", "version": "1.0", "abstract": "", "citation": ""}]
# Not a multiple of the request sizes, so the last request is a short one
BIG = bytes(range(256)) * 4100
CONTENT = {"big.dat": BIG, "small.hea": b"100 2 360 650000\n", "empty.txt": b""}


def manifest():
    return "".join(f"{hashlib.sha256(body).hexdigest()}  {path}\n" for path, body in CONTENT.items())


def serve_ranges(content, ignore_range=False):
    """A response callback that honours Range headers, as PhysioNet and S3 do."""

    def callback(request, context):
        match = re.fullmatch(r"bytes=(\d+)-(\d+)", request.headers.get("Range", ""))
        if ignore_range or not match:
            context.status_code = 200
            return content
        start, end = int(match.group(1)), int(match.group(2))
        if start >= len(content):
            context.status_code = 416
            context.headers["Content-Range"] = f"bytes */{len(content)}"
            return b""
        end = min(end, len(content) - 1)
        context.status_code = 206
        context.headers["Content-Range"] = f"bytes {start}-{end}/{len(content)}"
        return content[start : end + 1]

    return callback


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("PHYSIONET_CACHE", str(tmp_path / "cache"))
    for name in ("PHYSIONET_USERNAME", "PHYSIONET_PASSWORD", "PHYSIONET_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path / "cache"


@pytest.fixture
def server(cache):
    with requests_mock.Mocker() as mock:
        mock.get(f"{BASE}/api/v1/projects/demo/versions/", json=VERSIONS)
        mock.get(f"{FILES}/SHA256SUMS.txt", text=manifest())
        for path, body in CONTENT.items():
            mock.get(f"{FILES}/{path}", content=serve_ranges(body))
        yield mock


def stream_fs(**kwargs):
    kwargs.setdefault("source", "physionet")
    return PhysioNetFileSystem(stream=True, show_progress=False, skip_instance_cache=True, **kwargs)


def file_requests(mock, name):
    """The (start, end) byte ranges requested for a file, in order."""
    ranges = []
    for request in mock.request_history:
        if request.url.endswith("/" + name):
            start, end = re.fullmatch(r"bytes=(\d+)-(\d+)", request.headers["Range"]).groups()
            ranges.append((int(start), int(end) + 1))
    return ranges


def test_partial_read_fetches_little_and_caches_nothing(server, cache):
    with stream_fs().open("demo/1.0/big.dat") as f:
        assert f.size == len(BIG)
        f.seek(700_000)
        assert f.read(1000) == BIG[700_000:701_000]

    # The first request gives the size, and the second the part that was read
    initial = _RangeFile.DEFAULT_BLOCK_SIZE
    assert file_requests(server, "big.dat") == [(0, initial), (700_000, 700_000 + initial)]
    # The checksum list is not needed, and nothing is saved in the cache
    assert not any(request.url.endswith("SHA256SUMS.txt") for request in server.request_history)
    assert not any(path.is_file() for path in cache.rglob("*"))


def test_sequential_reads_use_larger_requests(server):
    with stream_fs().open("demo/1.0/big.dat") as f:
        chunks = iter(lambda: f.read(10_000), b"")
        assert b"".join(chunks) == BIG

    sizes = [end - start for start, end in file_requests(server, "big.dat")]
    block = _RangeFile.DEFAULT_BLOCK_SIZE
    # Each request continues where the last one ended, and is twice as large
    assert sizes[:5] == [block, 2 * block, 4 * block, 8 * block, 16 * block]
    assert all(size <= _RangeFile.MAX_BLOCK_SIZE for size in sizes)
    assert len(sizes) <= 7


def test_requests_get_small_again_after_a_jump(server):
    with stream_fs().open("demo/1.0/big.dat") as f:
        f.read(200_000)
        f.seek(900_000)
        assert f.read(10) == BIG[900_000:900_010]
        # Reading again from the buffer needs no request
        f.seek(900_000)
        assert f.read(10) == BIG[900_000:900_010]

    block = _RangeFile.DEFAULT_BLOCK_SIZE
    # A large read is fetched in one request, and after the jump the request
    # is small again
    assert file_requests(server, "big.dat") == [(0, block), (block, 200_000), (900_000, 900_000 + block)]


def test_files_already_in_the_cache_need_no_requests(server):
    fs = stream_fs()
    PhysioNetFileSystem(show_progress=False, source="physionet", skip_instance_cache=True).open(
        "demo/1.0/big.dat"
    ).close()
    server.reset_mock()
    with fs.open("demo/1.0/big.dat") as f:
        f.seek(5000)
        assert f.read(100) == BIG[5000:5100]
    assert server.request_history == []


def test_small_and_empty_files(server):
    fs = stream_fs()
    with fs.open("demo/1.0/small.hea", "rb") as f:
        assert f.read() == CONTENT["small.hea"]
    with fs.open("demo/1.0/empty.txt") as f:
        assert f.size == 0 and f.read() == b""
    # Text mode works too, as wfdb reads headers
    with fsspec.open("physionet://demo/1.0/small.hea", "r", stream=True, source="physionet") as f:
        assert f.read() == CONTENT["small.hea"].decode()


def test_server_that_ignores_the_range(server):
    server.get(f"{FILES}/big.dat", content=serve_ranges(BIG, ignore_range=True))
    with stream_fs().open("demo/1.0/big.dat") as f:
        f.seek(300_000)
        assert f.read(50) == BIG[300_000:300_050]


def test_requests_ask_for_uncompressed_data(server):
    with stream_fs().open("demo/1.0/big.dat") as f:
        f.read(10)
    request = server.request_history[-1]
    assert request.headers["Accept-Encoding"] == "identity"


@pytest.mark.parametrize(("status", "error"), [(404, FileNotFoundError), (403, PermissionError)])
def test_missing_or_forbidden_files(server, status, error):
    server.get(f"{FILES}/missing.dat", status_code=status)
    with pytest.raises(error, match="physionet://demo/1.0/missing.dat"):
        stream_fs().open("demo/1.0/missing.dat")


def test_unexpected_content_range(server):
    def wrong_range(request, context):
        context.status_code = 206
        context.headers["Content-Range"] = f"bytes 5-9/{len(BIG)}"
        return BIG[5:10]

    server.get(f"{FILES}/big.dat", content=wrong_range)
    with pytest.raises(OSError, match="Unexpected Content-Range"):
        stream_fs().open("demo/1.0/big.dat")


def test_open_data_is_read_from_the_mirror_without_credentials(server):
    server.head(f"{MIRROR}/SHA256SUMS.txt", status_code=200)
    server.get(f"{MIRROR}/big.dat", content=serve_ranges(BIG))
    with stream_fs(source="auto", username="user", password="pass").open("demo/1.0/big.dat") as f:
        assert f.read(100) == BIG[:100]

    mirror = [r for r in server.request_history if r.url.startswith(S3_BASE_URL) and r.method == "GET"]
    assert mirror and all("Authorization" not in r.headers for r in mirror)
    assert not any(r.url == f"{FILES}/big.dat" for r in server.request_history)


def test_restricted_data_is_read_from_physionet_with_credentials(server):
    server.head(f"{MIRROR}/SHA256SUMS.txt", status_code=404)
    with stream_fs(source="auto", username="user", password="pass").open("demo/1.0/big.dat") as f:
        assert f.read(100) == BIG[:100]

    request = server.request_history[-1]
    assert request.url == f"{FILES}/big.dat"
    assert request.headers["Authorization"].startswith("Basic ")


def test_redirected_files_are_read_from_where_they_moved(server):
    moved = "https://files.example.org/demo/big.dat"
    server.get(f"{FILES}/big.dat", status_code=302, headers={"Location": moved})
    server.get(moved, content=serve_ranges(BIG))
    with stream_fs().open("demo/1.0/big.dat") as f:
        f.seek(800_000)
        assert f.read(10) == BIG[800_000:800_010]

    # Only the first request goes through the redirect
    assert sum(r.url == f"{FILES}/big.dat" for r in server.request_history) == 1


def test_stream_can_be_chosen_for_one_file(server, cache):
    fs = PhysioNetFileSystem(show_progress=False, source="physionet", skip_instance_cache=True)
    with fs.open("demo/1.0/big.dat", stream=True) as f:
        assert f.read(10) == BIG[:10]
    assert not any(path.is_file() for path in cache.rglob("*.dat"))

    # Without it, the whole file is downloaded into the cache, as before
    with fs.open("demo/1.0/big.dat") as f:
        assert f.read(10) == BIG[:10]
    assert any(path.is_file() for path in cache.rglob("big.dat"))
