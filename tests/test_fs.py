"""Tests for the physionet:// fsspec filesystem."""

import hashlib

import fsspec
import pandas as pd
import pytest
import requests_mock

from physionet.fs import PhysioNetFileSystem

BASE = "https://physionet.org"
API = f"{BASE}/api/v1"
FILES = f"{BASE}/files/demo/1.0"
VERSIONS = [
    {"slug": "demo", "title": "Demo", "version": "0.9", "abstract": "", "citation": ""},
    {"slug": "demo", "title": "Demo", "version": "1.0", "abstract": "", "citation": ""},
]
CONTENT = {
    "README.md": b"# Demo\n",
    "data/table.csv": b"id,value\n1,10\n2,20\n",
    "data/sub/deep.txt": b"deep",
    "notes.txt": b"notes",
}


def sha(body):
    return hashlib.sha256(body).hexdigest()


def manifest(extra=""):
    return "".join(f"{sha(body)}  {path}\n" for path, body in CONTENT.items()) + extra


@pytest.fixture
def server():
    with requests_mock.Mocker() as mock:
        mock.get(f"{API}/projects/demo/versions/", json=VERSIONS)
        mock.get(f"{FILES}/SHA256SUMS.txt", text=manifest())
        for path, body in CONTENT.items():
            mock.get(f"{FILES}/{path}", content=body)
            mock.head(f"{FILES}/{path}", headers={"Content-Length": str(len(body))})
        yield mock


@pytest.fixture
def fs(tmp_path):
    return PhysioNetFileSystem(cache_dir=str(tmp_path / "cache"), source="physionet", skip_instance_cache=True)


def requests_to(mock, url, method="GET"):
    return [request for request in mock.request_history if request.url == url and request.method == method]


def test_protocol_is_registered_through_the_package_entry_point():
    assert fsspec.get_filesystem_class("physionet") is PhysioNetFileSystem


def test_pandas_reads_physionet_paths_and_reuses_the_cache(server, tmp_path):
    options = {"cache_dir": str(tmp_path / "cache"), "source": "physionet"}
    first = pd.read_csv("physionet://demo/1.0/data/table.csv", storage_options=options)
    second = pd.read_csv("physionet://demo/1.0/data/table.csv", storage_options=options)

    assert first.to_dict("list") == {"id": [1, 2], "value": [10, 20]}
    assert second.equals(first)
    assert len(requests_to(server, f"{FILES}/data/table.csv")) == 1
    assert (tmp_path / "cache" / "physionet.org" / "demo" / "1.0" / "data" / "table.csv").is_file()


def test_ls_lists_one_level_of_a_version(server, fs):
    assert fs.ls("physionet://demo/1.0", detail=False) == [
        "demo/1.0/README.md", "demo/1.0/data", "demo/1.0/notes.txt",
    ]
    assert fs.ls("demo/1.0/data", detail=False) == ["demo/1.0/data/sub", "demo/1.0/data/table.csv"]
    entries = {entry["name"]: entry for entry in fs.ls("demo/1.0/data")}
    assert entries["demo/1.0/data/sub"]["type"] == "directory"
    assert entries["demo/1.0/data/table.csv"]["type"] == "file"
    assert entries["demo/1.0/data/table.csv"]["sha256"] == sha(CONTENT["data/table.csv"])
    # Listing never downloads file contents, and fetches the checksum list once.
    assert requests_to(server, f"{FILES}/data/table.csv") == []
    assert len(requests_to(server, f"{FILES}/SHA256SUMS.txt")) == 1


def test_ls_on_a_file_returns_the_file_and_missing_paths_raise(server, fs):
    assert fs.ls("demo/1.0/notes.txt", detail=False) == ["demo/1.0/notes.txt"]
    with pytest.raises(FileNotFoundError):
        fs.ls("demo/1.0/missing")


def test_ls_lists_versions_and_projects(server, fs):
    server.get(f"{API}/projects/published/", json=[
        {"slug": slug, "version": "1.0", "title": slug} for slug in ("demo", "other", "demo")
    ])
    assert fs.ls("physionet://demo", detail=False) == ["demo/0.9", "demo/1.0"]
    assert fs.ls("physionet://", detail=False) == ["demo", "other"]


def test_info_exists_isdir_isfile(server, fs):
    info = fs.info("physionet://demo/1.0/data/table.csv")
    assert info["type"] == "file"
    assert info["size"] == len(CONTENT["data/table.csv"])  # from the server, without downloading
    assert requests_to(server, f"{FILES}/data/table.csv") == []

    assert fs.isdir("demo/1.0/data") and fs.isdir("demo/1.0") and fs.isdir("demo")
    assert fs.isfile("demo/1.0/data/sub/deep.txt")
    assert not fs.exists("demo/1.0/data/nope.csv")


def test_cached_file_size_needs_no_request(server, fs):
    fs.cat("demo/1.0/notes.txt")
    server.reset_mock()
    assert fs.info("demo/1.0/notes.txt")["size"] == len(CONTENT["notes.txt"])
    assert server.request_history == []


def test_open_cat_text_and_glob(server, fs):
    assert fs.cat("physionet://demo/1.0/data/sub/deep.txt") == b"deep"
    with fs.open("demo/1.0/README.md", "r") as handle:
        assert handle.read() == "# Demo\n"
    assert sorted(fs.glob("demo/1.0/**/*.txt")) == ["demo/1.0/data/sub/deep.txt", "demo/1.0/notes.txt"]


def test_latest_is_resolved_once(server, fs):
    assert fs.cat("physionet://demo/latest/notes.txt") == b"notes"
    assert fs.ls("demo/latest", detail=False)[0] == "demo/latest/README.md"
    assert len(requests_to(server, f"{API}/projects/demo/versions/")) == 1


def test_ukey_is_the_published_checksum(server, fs):
    assert fs.ukey("demo/1.0/data/table.csv") == sha(CONTENT["data/table.csv"])
    with pytest.raises(FileNotFoundError):
        fs.ukey("demo/1.0/nope")


def test_a_file_that_fails_its_checksum_is_not_cached(server, fs, tmp_path):
    server.get(f"{FILES}/notes.txt", content=b"tampered")
    with pytest.raises(ValueError, match="Checksum"):
        fs.cat("demo/1.0/notes.txt")
    assert not (tmp_path / "cache" / "physionet.org" / "demo" / "1.0" / "notes.txt").exists()


def test_errors_are_the_standard_ones(server, fs):
    server.get(f"{BASE}/files/demo/9.9/SHA256SUMS.txt", status_code=404)
    with pytest.raises(FileNotFoundError, match="no project demo version 9.9"):
        fs.ls("demo/9.9")

    server.get(f"{BASE}/files/secret/1.0/SHA256SUMS.txt", status_code=403)
    with pytest.raises(PermissionError, match="denied"):
        fs.cat("physionet://secret/1.0/data.csv")

    server.get(f"{API}/projects/nothing/versions/", json=[])
    with pytest.raises(FileNotFoundError):
        fs.info("physionet://nothing")

    with pytest.raises(PermissionError, match="read-only"):
        fs.open("demo/1.0/new.txt", "wb")
    with pytest.raises(IsADirectoryError):
        fs.cat("demo/1.0")


@pytest.mark.parametrize("path", ["../etc/1.0/passwd", "demo/../1.0/x", "demo/1.0/../escape.txt", "demo/1.0/a\\b"])
def test_paths_cannot_leave_the_project(server, fs, path):
    with pytest.raises((ValueError, FileNotFoundError)):
        fs.cat(f"physionet://{path}")
    assert not any("escape" in request.url or "passwd" in request.url for request in server.request_history)


def test_unsafe_entries_in_the_checksum_list_are_not_listed(server, fs):
    server.get(f"{FILES}/SHA256SUMS.txt", text=manifest(f"{sha(b'x')}  ../escape.txt\n{sha(b'x')}  /abs.txt\n"))
    names = fs.find("demo/1.0")
    assert not any("escape" in name or "abs.txt" in name for name in names)
    assert len(names) == len(CONTENT)
