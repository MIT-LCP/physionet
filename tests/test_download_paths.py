"""download() never writes outside the project folder, whatever SHA256SUMS.txt says."""

import hashlib

import pytest
import requests_mock

from physionet.download import DownloadError, _is_safe_path, download

BASE = "https://physionet.org"
VERSIONS = [{"slug": "demo", "title": "Demo", "version": "1.0", "abstract": "", "citation": ""}]
FILES_URL = f"{BASE}/files/demo/1.0"


def sha(body):
    return hashlib.sha256(body).hexdigest()


@pytest.mark.parametrize("path, safe", [
    ("data.csv", True),
    ("sub/dir/data.csv", True),
    ("./data.csv", True),  # normalised to data.csv
    ("../escape.txt", False),
    ("sub/../../escape.txt", False),
    ("/etc/passwd", False),
    ("C:\\Windows\\x.txt", False),
    ("sub\\..\\x.txt", False),
    ("", False),
])
def test_is_safe_path(path, safe):
    assert _is_safe_path(path) is safe


def test_download_skips_paths_outside_the_project_folder(tmp_path, capsys):
    out = tmp_path / "out"
    outside = tmp_path / "outside"
    outside.mkdir()
    good = b"good data"
    evil = b"should never be written"
    unsafe = ["../escape.txt", f"{outside}/absolute.txt", "sub\\..\\..\\backslash.txt", "link/through-link.txt"]

    # A symbolic link inside the destination that points outside it.
    dest = out / "demo-1.0"
    dest.mkdir(parents=True)
    (dest / "link").symlink_to(outside, target_is_directory=True)

    manifest = "\n".join([f"{sha(good)}  data/good.csv"] + [f"{sha(evil)}  {path}" for path in unsafe])
    with requests_mock.Mocker() as mock:
        # Registered first, so it only answers what nothing else matches: any
        # other request would be for an unsafe file.
        mock.get(requests_mock.ANY, content=evil)
        mock.get(f"{BASE}/api/v1/projects/demo/versions/", json=VERSIONS)
        mock.get(f"{FILES_URL}/SHA256SUMS.txt", text=manifest)
        mock.get(f"{FILES_URL}/data/good.csv", content=good)
        with pytest.raises(DownloadError) as caught:
            download("demo", version="1.0", output_dir=str(out), source="physionet")

    assert (dest / "data" / "good.csv").read_bytes() == good
    assert sorted(path for path, _ in caught.value.failures) == sorted(unsafe)
    assert all("outside the project folder" in str(error) for _, error in caught.value.failures)
    assert caught.value.downloaded == 1
    # Nothing was written outside the destination, and the unsafe files were never requested.
    assert list(outside.iterdir()) == []
    assert not (out / "escape.txt").exists() and not (tmp_path / "escape.txt").exists()
    file_requests = [r.url for r in mock.request_history if "/files/demo/1.0/" in r.url]
    assert file_requests == [f"{FILES_URL}/SHA256SUMS.txt", f"{FILES_URL}/data/good.csv"]
    err = capsys.readouterr().err
    assert "Not downloading 4 file(s) whose paths would be written outside" in err


def test_dry_run_warns_about_unsafe_paths_and_lists_only_safe_ones(tmp_path, capsys):
    manifest = f"{sha(b'a')}  ok.csv\n{sha(b'b')}  ../escape.txt\n"
    with requests_mock.Mocker() as mock:
        mock.get(f"{BASE}/api/v1/projects/demo/versions/", json=VERSIONS)
        mock.get(f"{FILES_URL}/SHA256SUMS.txt", text=manifest)
        download("demo", version="1.0", output_dir=str(tmp_path), source="physionet", dry_run=True)

    output = capsys.readouterr()
    assert "  ok.csv" in output.out
    assert "escape.txt" not in output.out
    assert "Not downloading 1 file(s)" in output.err
    assert not (tmp_path / "demo-1.0").exists()


def test_parallel_download_reports_unsafe_paths_first(tmp_path):
    # Failures are sorted into manifest order after a parallel download; refused
    # paths are not in the list being downloaded, and must not break that sort.
    files = {f"f{i}.csv": f"x{i}".encode() for i in range(6)}
    manifest = "".join(f"{sha(body)}  {path}\n" for path, body in files.items()) + f"{sha(b'x')}  ../escape.txt\n"
    with requests_mock.Mocker() as mock:
        mock.get(f"{BASE}/api/v1/projects/demo/versions/", json=VERSIONS)
        mock.get(f"{FILES_URL}/SHA256SUMS.txt", text=manifest)
        mock.get(f"{FILES_URL}/f3.csv", status_code=500)
        for path, body in files.items():
            if path != "f3.csv":
                mock.get(f"{FILES_URL}/{path}", content=body)
        with pytest.raises(DownloadError) as caught:
            download("demo", version="1.0", output_dir=str(tmp_path), source="physionet", jobs=4)

    assert [path for path, _ in caught.value.failures] == ["../escape.txt", "f3.csv"]
    assert caught.value.downloaded == 5
