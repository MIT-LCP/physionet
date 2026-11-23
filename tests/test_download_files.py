"""Tests for downloading chosen files, and where they are saved (download(files=...))."""

import hashlib
import sys
from importlib import import_module
from unittest.mock import patch

import pytest
import requests_mock

from physionet.cli import main
from physionet.download import DownloadError, download

# physionet exports a download() function, which hides the submodule as an attribute.
download_module = import_module("physionet.download")

BASE = "https://physionet.org"
FILES_URL = f"{BASE}/files/demo/1.0"
VERSIONS = [{"slug": "demo", "title": "Demo", "version": "1.0", "abstract": "", "citation": ""}]
CONTENT = {
    "RECORDS": b"100\n101\n",
    "100.hea": b"100 2 360 650000\n",
    "100.dat": b"signal 100",
    "100.atr": b"annotations 100",
    "101.hea": b"101 2 360 650000\n",
    "101.dat": b"signal 101",
    "data/sub/102.hea": b"102 1 360 10\n",
}


def sha256(body):
    return hashlib.sha256(body).hexdigest()


@pytest.fixture
def server(monkeypatch):
    for name in ("PHYSIONET_USERNAME", "PHYSIONET_PASSWORD", "PHYSIONET_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    with requests_mock.Mocker() as mock:
        mock.get(f"{BASE}/api/v1/projects/demo/versions/", json=VERSIONS)
        mock.get(
            f"{FILES_URL}/SHA256SUMS.txt",
            text="".join(f"{sha256(body)}  {path}\n" for path, body in CONTENT.items()),
        )
        for path, body in CONTENT.items():
            mock.get(f"{FILES_URL}/{path}", content=body)
        yield mock


def downloaded(mock):
    """The project files that were requested, other than the checksum list."""
    prefix = FILES_URL + "/"
    paths = [r.url[len(prefix) :] for r in mock.request_history if r.url.startswith(prefix)]
    return sorted(path for path in paths if path != "SHA256SUMS.txt")


def run(tmp_path, **kwargs):
    return download("demo", "1.0", output_dir=str(tmp_path), source="physionet", show_progress=False, **kwargs)


def test_list_of_files(server, tmp_path):
    dest = run(tmp_path, files=["100.hea", "100.dat", "data/sub/102.hea"])

    assert dest == tmp_path / "demo-1.0"
    assert downloaded(server) == ["100.dat", "100.hea", "data/sub/102.hea"]
    assert (dest / "data" / "sub" / "102.hea").read_bytes() == CONTENT["data/sub/102.hea"]
    assert not (dest / "101.hea").exists()


def test_dict_says_where_to_save_files(server, tmp_path):
    dest = run(tmp_path, files={"100.hea": "records/100.hea", "data/sub/102.hea": "102.hea"})

    assert dest == tmp_path
    assert (tmp_path / "records" / "100.hea").read_bytes() == CONTENT["100.hea"]
    assert (tmp_path / "102.hea").read_bytes() == CONTENT["data/sub/102.hea"]
    assert not (tmp_path / "demo-1.0").exists()


def test_files_already_saved_are_skipped(server, tmp_path, capsys):
    (tmp_path / "100.hea").write_bytes(CONTENT["100.hea"])
    run(tmp_path, files={"100.hea": "100.hea", "100.dat": "100.dat"})

    assert downloaded(server) == ["100.dat"]
    assert "1 downloaded, 1 skipped" in capsys.readouterr().out


def test_include_and_exclude_still_apply(server, tmp_path):
    run(tmp_path, files=["100.hea", "100.dat", "100.atr"], exclude=["*.dat"])
    assert downloaded(server) == ["100.atr", "100.hea"]


def test_missing_files_are_reported_before_downloading(server, tmp_path):
    with pytest.raises(FileNotFoundError, match=r"demo v1.0 has no file '999.hea', '999.dat'"):
        run(tmp_path, files=["100.hea", "999.hea", "999.dat"])
    assert downloaded(server) == []


@pytest.mark.parametrize("saved_as", ["../100.hea", "/tmp/100.hea", "a/../../100.hea", "a\\100.hea", ""])
def test_files_are_only_saved_inside_output_dir(server, tmp_path, saved_as):
    with pytest.raises(ValueError, match="only be saved inside output_dir"):
        run(tmp_path, files={"100.hea": saved_as})
    assert server.request_history == []


@pytest.mark.parametrize("saved_as", ["ecg//100.hea", "./100.hea", "ecg/./100.hea", "ecg/100.hea/", "."])
def test_destinations_must_be_written_plainly(server, tmp_path, saved_as):
    """Different spellings of one path could otherwise overwrite each other."""
    with pytest.raises(ValueError, match="only be saved inside output_dir"):
        run(tmp_path, files={"100.hea": saved_as})
    assert server.request_history == []


def test_destinations_that_differ_only_in_case(server, tmp_path):
    with pytest.raises(ValueError, match="same file where file names ignore case"):
        run(tmp_path, files={"100.dat": "ecg/same.dat", "101.dat": "ECG/Same.dat"})
    assert server.request_history == []


def test_destinations_that_are_one_file_through_a_symbolic_link(server, tmp_path):
    (tmp_path / "real").mkdir()
    try:
        (tmp_path / "link").symlink_to(tmp_path / "real", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are not available")

    with pytest.raises(ValueError, match="through a symbolic link"):
        run(tmp_path, files={"100.dat": "link/same.dat", "101.dat": "real/same.dat"})
    assert downloaded(server) == []
    assert not (tmp_path / "real" / "same.dat").exists()


def test_two_files_cannot_be_saved_in_one_place(server, tmp_path):
    with pytest.raises(ValueError, match="would both be saved as '100.hea'"):
        run(tmp_path, files={"100.hea": "100.hea", "data/sub/102.hea": "100.hea"})
    assert server.request_history == []


def test_verbose_false_prints_only_errors(server, tmp_path, capsys):
    server.get(f"{FILES_URL}/100.dat", status_code=500)
    with pytest.raises(DownloadError):
        run(tmp_path, files=["100.hea", "100.dat"], verbose=False, retries=0)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Error downloading 100.dat" in captured.err


def test_verbose_by_default(server, tmp_path, capsys):
    run(tmp_path, files=["100.hea"])
    out = capsys.readouterr().out
    assert "Downloading demo v1.0 (1 files)" in out and "Complete: 1 downloaded" in out


def test_dry_run_shows_where_files_would_be_saved(server, tmp_path, capsys):
    run(tmp_path, files={"data/sub/102.hea": "102.hea"}, dry_run=True)
    assert "data/sub/102.hea -> 102.hea" in capsys.readouterr().out
    assert downloaded(server) == []


def leftover_parts(folder):
    return [path.name for path in folder.rglob("*.part")]


def test_overwrite_downloads_saved_files_again(server, tmp_path, capsys):
    (tmp_path / "100.hea").write_bytes(CONTENT["100.hea"])
    (tmp_path / "100.dat").write_bytes(b"wrong!!!!!")  # same size, wrong content

    run(tmp_path, files={"100.hea": "100.hea", "100.dat": "100.dat"}, overwrite=True)

    # Without overwrite, the correct 100.hea would have been skipped
    assert downloaded(server) == ["100.dat", "100.hea"]
    assert (tmp_path / "100.hea").read_bytes() == CONTENT["100.hea"]
    assert (tmp_path / "100.dat").read_bytes() == CONTENT["100.dat"]
    assert "2 downloaded, 0 skipped" in capsys.readouterr().out
    assert leftover_parts(tmp_path) == []


@pytest.mark.parametrize("failure", ["server error", "wrong checksum"])
def test_overwrite_keeps_the_saved_file_if_the_download_fails(server, tmp_path, failure):
    (tmp_path / "100.dat").write_bytes(b"saved before")
    if failure == "server error":
        server.get(f"{FILES_URL}/100.dat", status_code=500)
    else:
        server.get(f"{FILES_URL}/100.dat", content=b"not what the checksum says")

    with pytest.raises(DownloadError):
        run(tmp_path, files={"100.dat": "100.dat"}, overwrite=True, retries=0)

    assert (tmp_path / "100.dat").read_bytes() == b"saved before"
    assert leftover_parts(tmp_path) == []


def test_overwrite_replaces_nothing_if_a_file_is_missing(server, tmp_path):
    (tmp_path / "100.hea").write_bytes(b"saved before")
    with pytest.raises(FileNotFoundError):
        run(tmp_path, files={"100.hea": "100.hea", "999.hea": "999.hea"}, overwrite=True)
    assert (tmp_path / "100.hea").read_bytes() == b"saved before"
    assert downloaded(server) == []


def test_overwrite_replaces_nothing_outside_output_dir(server, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (tmp_path / "victim.dat").write_bytes(b"keep me")
    with pytest.raises(ValueError):
        download(
            "demo", "1.0", output_dir=str(out), source="physionet", files={"100.dat": "../victim.dat"}, overwrite=True
        )
    assert (tmp_path / "victim.dat").read_bytes() == b"keep me"
    assert server.request_history == []


def test_cli_passes_overwrite():
    for argv, expected in [([], False), (["--overwrite"], True)]:
        with patch.object(download_module, "download") as mock_download:
            with patch.object(sys, "argv", ["physionet", "download", "demo", *argv]):
                assert main() == 0
        assert mock_download.call_args.kwargs["overwrite"] is expected
