"""Tests for checking a local copy of a dataset against its checksums."""

import hashlib
import json
import os
from unittest.mock import patch

import pytest
import requests
import requests_mock as rm

import physionet
from physionet.api.exceptions import ForbiddenError, NotFoundError
from physionet.cli import main
from physionet.verify import verify

BASE = "https://physionet.org"
FILES = {"RECORDS": b"100\n", "100.hea": b"header", "sub/100.dat": b"\x00\x01" * 1000}


def sha256(content):
    return hashlib.sha256(content).hexdigest()


def manifest(files=FILES, extra_lines=""):
    return "".join(f"{sha256(content)}  {name}\n" for name, content in files.items()) + extra_lines


def make_copy(root, files=FILES):
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return root


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    # conftest.py already keeps credentials and the saved sign-in away
    monkeypatch.setenv("PHYSIONET_CACHE", str(tmp_path / "cache"))


def mock_checksums(mocker, slug="demo", version="1.0", text=None, **kwargs):
    url = f"{BASE}/files/{slug}/{version}/SHA256SUMS.txt"
    if kwargs:
        return mocker.get(url, **kwargs)
    return mocker.get(url, text=manifest() if text is None else text)


def run_cli(*args):
    with patch("sys.argv", ["physionet", *args]):
        return main()


def test_a_complete_copy_passes(tmp_path):
    folder = make_copy(tmp_path / "demo-1.0")
    with rm.Mocker() as mocker:
        mock_checksums(mocker)
        result = verify(folder, show_progress=False)

    assert result.passed
    assert (result.slug, result.version) == ("demo", "1.0")
    assert result.matched == ["100.hea", "RECORDS", "sub/100.dat"]
    assert result.mismatched == result.missing == result.extra == result.unreadable == []
    assert result.source == "https://physionet.org (demo 1.0)"
    assert verify is physionet.verify


def test_problems_are_reported(tmp_path):
    folder = make_copy(tmp_path / "demo-1.0")
    (folder / "100.hea").write_bytes(b"changed")
    (folder / "RECORDS").unlink()
    (folder / "notes.txt").write_text("mine")
    with rm.Mocker() as mocker:
        mock_checksums(mocker)
        result = verify(folder, show_progress=False, jobs=3)

    assert not result.passed
    assert result.matched == ["sub/100.dat"]
    assert result.mismatched == ["100.hea"]
    assert result.missing == ["RECORDS"]
    assert result.extra == ["notes.txt"]


def test_extra_files_alone_do_not_fail(tmp_path):
    folder = make_copy(tmp_path / "demo-1.0")
    (folder / "notes.txt").write_text("mine")
    (folder / "SHA256SUMS.txt").write_text(manifest())
    with rm.Mocker() as mocker:
        mock_checksums(mocker)
        result = verify(folder, show_progress=False)

    assert result.passed
    # The checksum file itself is not reported as extra
    assert result.extra == ["notes.txt"]


def test_include_and_exclude(tmp_path):
    folder = make_copy(tmp_path / "demo-1.0", {"100.hea": FILES["100.hea"]})
    (folder / "notes.txt").write_text("mine")
    with rm.Mocker() as mocker:
        mock_checksums(mocker)
        included = verify(folder, include=["*.hea"], show_progress=False)
        excluded = verify(folder, exclude=["sub/*", "RECORDS"], show_progress=False)

    assert included.passed and included.matched == ["100.hea"] and included.extra == []
    assert excluded.passed and excluded.extra == ["notes.txt"]


def test_unsafe_paths_in_the_checksum_list_are_ignored(tmp_path):
    folder = make_copy(tmp_path / "demo-1.0")
    (tmp_path / "secret.txt").write_text("outside")
    with rm.Mocker() as mocker:
        mock_checksums(mocker, text=manifest(extra_lines=f"{'0' * 64}  ../secret.txt\n{'0' * 64}  /etc/hosts\n"))
        result = verify(folder, show_progress=False)

    assert result.passed
    assert result.missing == []


@pytest.mark.parametrize(
    "parts, expected",
    [
        (("mimic-iv-demo-2.2",), ("mimic-iv-demo", "2.2")),
        (("mitdb-1.0.0",), ("mitdb", "1.0.0")),
        (("physionet.org", "files", "mitdb", "1.0.0"), ("mitdb", "1.0.0")),
        (("physionet.org", "content", "mitdb", "1.0.0"), ("mitdb", "1.0.0")),
    ],
)
def test_project_is_read_from_the_folder_name(tmp_path, parts, expected):
    folder = make_copy(tmp_path.joinpath(*parts))
    slug, version = expected
    with rm.Mocker() as mocker:
        mock_checksums(mocker, slug, version)
        result = verify(folder, show_progress=False)

    assert result.passed
    assert (result.slug, result.version) == expected


def test_project_given_explicitly(tmp_path):
    folder = make_copy(tmp_path / "anything")
    with rm.Mocker() as mocker:
        mock_checksums(mocker)
        assert verify(folder, "demo", "1.0", show_progress=False).passed
        # The version is read from the folder name only when it names the same project
        assert verify(make_copy(tmp_path / "demo-1.0"), "demo", show_progress=False).passed

    with pytest.raises(ValueError, match="Give the version of other"):
        verify(tmp_path / "demo-1.0", "other", show_progress=False)
    with pytest.raises(ValueError, match="version needs slug"):
        verify(folder, version="1.0", show_progress=False)


def test_cached_checksums_are_used_offline(tmp_path):
    cached = tmp_path / "cache" / "physionet.org" / ".manifests" / "demo" / "1.0" / "SHA256SUMS.txt"
    cached.parent.mkdir(parents=True)
    cached.write_text(manifest())
    folder = make_copy(tmp_path / "demo-1.0")

    with rm.Mocker():  # any request would fail
        result = verify(folder, show_progress=False)

    assert result.passed
    assert result.source == "https://physionet.org (demo 1.0, cached)"


def test_downloaded_checksums_are_not_written_to_the_cache(tmp_path):
    with rm.Mocker() as mocker:
        mock_checksums(mocker)
        verify(make_copy(tmp_path / "demo-1.0"), show_progress=False)

    assert not (tmp_path / "cache").exists()


def test_checksums_from_a_file_or_the_folder(tmp_path):
    folder = make_copy(tmp_path / "copy")
    (tmp_path / "sums.txt").write_text(manifest())

    from_file = verify(folder, checksums=str(tmp_path / "sums.txt"), show_progress=False)
    assert from_file.passed and from_file.source == str(tmp_path / "sums.txt")
    assert (from_file.slug, from_file.version) == (None, None)

    with pytest.raises(ValueError, match="Cannot tell which dataset"):
        verify(folder, show_progress=False)

    (folder / "SHA256SUMS.txt").write_text(manifest())
    in_folder = verify(folder, show_progress=False)
    assert in_folder.passed and in_folder.source.endswith("(in the folder)")


def test_server_errors(tmp_path):
    folder = make_copy(tmp_path / "demo-1.0")
    with rm.Mocker() as mocker:
        mock_checksums(mocker, status_code=404)
        with pytest.raises(NotFoundError):
            verify(folder, show_progress=False)
    with rm.Mocker() as mocker:
        mock_checksums(mocker, status_code=403)
        with pytest.raises(ForbiddenError, match="physionet login"):
            verify(folder, show_progress=False)
    with rm.Mocker() as mocker:
        mock_checksums(mocker, exc=requests.ConnectionError)
        with pytest.raises(ConnectionError, match="--checksums"):
            verify(folder, show_progress=False)


def test_bad_arguments(tmp_path):
    with pytest.raises(FileNotFoundError):
        verify(tmp_path / "missing", "demo", "1.0")
    with pytest.raises(ValueError, match="jobs"):
        verify(tmp_path, "demo", "1.0", jobs=0)
    with pytest.raises(ValueError, match="not a valid project"):
        verify(tmp_path, "../x", "1.0")


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="needs POSIX permissions, not as root")
def test_unreadable_files_are_reported(tmp_path):
    folder = make_copy(tmp_path / "demo-1.0")
    (folder / "100.hea").chmod(0)
    try:
        with rm.Mocker() as mocker:
            mock_checksums(mocker)
            result = verify(folder, show_progress=False)
    finally:
        (folder / "100.hea").chmod(0o600)

    assert not result.passed
    assert [name for name, _ in result.unreadable] == ["100.hea"]


# --- Command line ---


def test_cli_passing_copy(tmp_path, capsys):
    folder = make_copy(tmp_path / "demo-1.0")
    with rm.Mocker() as mocker:
        mock_checksums(mocker)
        assert run_cli("verify", str(folder)) == 0

    out = capsys.readouterr().out
    assert out == (
        f"Checking {folder} against the checksums from https://physionet.org (demo 1.0)\n"
        "✓ 3 of 3 file(s) match\n"
    )


def test_cli_failing_copy_suggests_a_download(tmp_path, capsys):
    folder = make_copy(tmp_path / "demo-1.0")
    (folder / "100.hea").write_bytes(b"changed")
    (folder / "RECORDS").unlink()
    (folder / "notes.txt").write_text("mine")
    with rm.Mocker() as mocker:
        mock_checksums(mocker)
        assert run_cli("verify", str(folder), "--exclude", "notes*", "-j", "2") == 1

    out = capsys.readouterr().out
    assert "✓ 1 of 3 file(s) match" in out
    assert "✗ 1 file(s) do not match (corrupt, changed or incomplete):\n    100.hea\n" in out
    assert "✗ 1 file(s) are missing:\n    RECORDS\n" in out
    assert "notes.txt" not in out.split("To download")[0]
    assert f"physionet download demo --version 1.0 --output {tmp_path} --exclude 'notes*'" in out


def test_cli_lists_at_most_twenty_files_per_problem(tmp_path, capsys):
    files = {f"{i:03}.hea": b"x" for i in range(25)}
    folder = tmp_path / "copy"
    folder.mkdir()
    (tmp_path / "sums.txt").write_text(manifest(files))

    assert run_cli("verify", str(folder), "--checksums", str(tmp_path / "sums.txt")) == 1
    out = capsys.readouterr().out
    assert "✗ 25 file(s) are missing:" in out
    assert "    019.hea\n    and 5 more (--json lists them all)" in out
    # Not a physionet download folder, so no download command is suggested
    assert "To download" not in out


def test_cli_json(tmp_path, capsys):
    folder = make_copy(tmp_path / "demo-1.0")
    (folder / "RECORDS").unlink()
    with rm.Mocker() as mocker:
        mock_checksums(mocker)
        assert run_cli("verify", str(folder), "--json") == 1

    data = json.loads(capsys.readouterr().out)
    assert data["passed"] is False
    assert data["missing"] == ["RECORDS"]
    assert (data["slug"], data["version"]) == ("demo", "1.0")


def test_cli_errors(tmp_path, capsys):
    assert run_cli("verify", str(tmp_path / "missing")) == 1
    assert "is not a folder" in capsys.readouterr().err

    (tmp_path / "copy").mkdir()
    assert run_cli("verify", str(tmp_path / "copy")) == 1
    assert "Cannot tell which dataset" in capsys.readouterr().err

    with rm.Mocker() as mocker:
        mock_checksums(mocker, status_code=403)
        assert run_cli("verify", str(tmp_path / "copy"), "demo", "--version", "1.0") == 1
    assert capsys.readouterr().err.startswith("Access denied:")
