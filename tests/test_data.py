"""Tests for fetching PhysioNet files into the cache and loading tables."""

import gzip
import hashlib
import os
import stat

import pytest
import requests
import requests_mock as rm

import physionet
from physionet.api.exceptions import ForbiddenError, NotFoundError
from physionet.data import _origin_folder, fetch, get_cache_dir, load

BASE = "https://physionet.org"
FILES = f"{BASE}/files/demo/1.0"
PATIENTS = gzip.compress(b"subject_id,age\n1,50\n2,60\n")
NOTES = b"Synthetic notes.\n"


def sha256(content):
    return hashlib.sha256(content).hexdigest()


MANIFEST = (
    f"{sha256(PATIENTS)}  hosp/patients.csv.gz\n"
    f"{sha256(NOTES)}  docs/notes.txt\n"
    f"{sha256(b'x')}  ../outside.csv\n"
)


def mock_project(mocker, manifest=MANIFEST, patients=PATIENTS):
    mocker.get(f"{FILES}/SHA256SUMS.txt", text=manifest)
    mocker.get(f"{FILES}/hosp/patients.csv.gz", content=patients)
    mocker.get(f"{FILES}/docs/notes.txt", content=NOTES)


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("PHYSIONET_CACHE", str(tmp_path / "cache"))
    monkeypatch.delenv("PHYSIONET_USERNAME", raising=False)
    monkeypatch.delenv("PHYSIONET_PASSWORD", raising=False)
    return tmp_path / "cache"


def test_load_reads_a_table_and_stores_it_by_project_and_version(cache):
    with rm.Mocker() as mocker:
        mock_project(mocker)
        df = load("demo", "hosp/patients.csv.gz", "1.0", source="physionet")

    assert df.to_dict("list") == {"subject_id": [1, 2], "age": [50, 60]}
    assert (cache / "physionet.org" / "demo" / "1.0" / "hosp" / "patients.csv.gz").read_bytes() == PATIENTS
    assert list(cache.rglob("*.part")) == []


def test_pinned_version_in_cache_makes_no_network_requests(cache):
    with rm.Mocker() as mocker:
        mock_project(mocker)
        load("demo", "hosp/patients", "1.0", source="physionet")

    # A mocker with no routes fails any request that is made.
    with rm.Mocker():
        df = load("demo", "hosp/patients", "1.0", source="physionet")
        path = fetch("demo", "hosp/patients.csv.gz", "1.0")

    assert len(df) == 2
    assert path == cache / "physionet.org" / "demo" / "1.0" / "hosp" / "patients.csv.gz"


def test_checksum_list_is_fetched_once_per_version(cache):
    with rm.Mocker() as mocker:
        mock_project(mocker)
        fetch("demo", "hosp/patients.csv.gz", "1.0", source="physionet")
        fetch("demo", "docs/notes.txt", "1.0", source="physionet")

    manifest_requests = [r for r in mocker.request_history if r.url.endswith("SHA256SUMS.txt")]
    assert len(manifest_requests) == 1


def test_latest_version_is_looked_up_when_not_given(cache):
    versions = [
        {"slug": "demo", "title": "Demo", "version": v, "abstract": "", "citation": ""}
        for v in ("0.9", "1.0")
    ]
    with rm.Mocker() as mocker:
        mocker.get(f"{BASE}/api/v1/projects/demo/versions/", json=versions)
        mock_project(mocker)
        path = fetch("demo", "docs/notes.txt", source="physionet")

    assert path == cache / "physionet.org" / "demo" / "1.0" / "docs" / "notes.txt"


def test_offline_without_a_version_explains_how_to_use_the_cache(cache):
    with rm.Mocker() as mocker:
        mocker.get(f"{BASE}/api/v1/projects/demo/versions/", exc=requests.ConnectionError)
        with pytest.raises(ConnectionError, match="Pass version="):
            load("demo", "hosp/patients")


def test_checksum_mismatch_leaves_nothing_in_the_cache(cache):
    with rm.Mocker() as mocker:
        mock_project(mocker, patients=gzip.compress(b"tampered\n"))
        with pytest.raises(ValueError, match="Checksum verification failed"):
            fetch("demo", "hosp/patients.csv.gz", "1.0", source="physionet", retries=0)

    assert not (cache / "physionet.org" / "demo" / "1.0" / "hosp" / "patients.csv.gz").exists()
    assert [p for p in (cache / "physionet.org" / "demo").rglob("*") if p.is_file()] == []


def test_unknown_file_suggests_close_matches(cache):
    with rm.Mocker() as mocker:
        mock_project(mocker)
        with pytest.raises(FileNotFoundError, match=r"hosp/patient is not a file in demo 1\.0.*hosp/patients"):
            load("demo", "hosp/patient", "1.0", source="physionet")


def test_name_without_extension_must_match_one_table(cache):
    plain_csv = b"subject_id\n"
    manifest = MANIFEST + f"{sha256(plain_csv)}  hosp/patients.csv\n"
    with rm.Mocker() as mocker:
        mock_project(mocker, manifest=manifest)
        with pytest.raises(ValueError, match="include the extension"):
            load("demo", "hosp/patients", "1.0", source="physionet")


def test_unsupported_format_points_to_fetch(cache):
    with rm.Mocker() as mocker:
        mock_project(mocker)
        with pytest.raises(ValueError, match=r"docs/notes\.txt is not a supported table.*physionet\.fetch"):
            load("demo", "docs/notes.txt", "1.0", source="physionet")
        with pytest.raises(FileNotFoundError, match=r"Did you mean: docs/notes\.txt"):
            load("demo", "docs/notes", "1.0", source="physionet")

    assert not any(r.url.endswith("notes.txt") for r in mocker.request_history)


def test_reader_options_are_passed_to_pandas(cache):
    with rm.Mocker() as mocker:
        mock_project(mocker)
        df = load("demo", "hosp/patients", "1.0", source="physionet", usecols=["age"], nrows=1)

    assert df.to_dict("list") == {"age": [50]}


@pytest.mark.parametrize("path", ["../outside.csv", "/etc/passwd/../../x", "a/../../b.csv", "", "a\\b.csv"])
def test_paths_outside_the_project_are_rejected(cache, path):
    with rm.Mocker():
        with pytest.raises(ValueError, match="not a valid path"):
            fetch("demo", path, "1.0")


@pytest.mark.parametrize(("slug", "version"), [("../demo", "1.0"), ("demo", "../1.0"), (".hidden", "1.0")])
def test_invalid_project_or_version_names_are_rejected(cache, slug, version):
    with rm.Mocker():
        with pytest.raises(ValueError, match="not a valid"):
            fetch(slug, "docs/notes.txt", version)


@pytest.mark.parametrize(
    ("status", "error", "message"),
    [(404, NotFoundError, "no project demo version 1.0"), (403, ForbiddenError, "PHYSIONET_USERNAME")],
)
def test_missing_or_restricted_projects_raise_clear_errors(cache, status, error, message):
    with rm.Mocker() as mocker:
        mocker.get(f"{FILES}/SHA256SUMS.txt", status_code=status)
        with pytest.raises(error, match=message):
            load("demo", "hosp/patients", "1.0")

    assert not (cache / "physionet.org" / ".manifests" / "demo" / "1.0").exists()


def test_credentials_are_sent_for_restricted_files(cache):
    with rm.Mocker() as mocker:
        mock_project(mocker)
        fetch("demo", "docs/notes.txt", "1.0", source="physionet", username="user", password="pass")

    file_request = mocker.request_history[-1]
    assert file_request.url == f"{FILES}/docs/notes.txt"
    assert file_request.headers["Authorization"].startswith("Basic ")


def test_auto_source_falls_back_to_physionet_quietly(cache, capsys):
    with rm.Mocker() as mocker:
        mocker.head("https://physionet-open.s3.amazonaws.com/demo/1.0/SHA256SUMS.txt", status_code=404)
        mock_project(mocker)
        fetch("demo", "docs/notes.txt", "1.0")

    assert capsys.readouterr().out == ""


def test_cache_location_settings(tmp_path, monkeypatch):
    monkeypatch.delenv("PHYSIONET_CACHE", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert get_cache_dir() == tmp_path / "xdg" / "physionet"

    monkeypatch.setenv("PHYSIONET_CACHE", str(tmp_path / "env"))
    assert get_cache_dir() == tmp_path / "env"
    assert get_cache_dir(str(tmp_path / "arg")) == tmp_path / "arg"


def test_exported_from_the_package():
    assert physionet.load is load and physionet.fetch is fetch


def test_each_server_has_its_own_cache(cache):
    for base, body in [("https://a.example", b"from A\n"), ("http://b.example:8000", b"from B\n")]:
        with rm.Mocker() as mocker:
            mocker.get(f"{base}/files/demo/1.0/SHA256SUMS.txt", text=f"{sha256(body)}  x.txt\n")
            mocker.get(f"{base}/files/demo/1.0/x.txt", content=body)
            path = fetch("demo", "x.txt", "1.0", base_url=base, source="physionet")
        assert path.read_bytes() == body
        assert mocker.call_count == 2

    assert (cache / "a.example" / "demo" / "1.0" / "x.txt").exists()
    assert (cache / "http_b.example_8000" / "demo" / "1.0" / "x.txt").exists()


@pytest.mark.parametrize(
    ("base_url", "folder"),
    [
        ("https://physionet.org", "physionet.org"),
        ("https://PhysioNet.org/", "physionet.org"),
        ("http://localhost:8000", "http_localhost_8000"),
        ("https://[::1]:8443", "host___1_8443"),
    ],
)
def test_origin_folder_names(base_url, folder):
    assert _origin_folder(base_url) == folder


def test_cached_files_are_trusted_unless_verify_is_requested(cache):
    with rm.Mocker() as mocker:
        mock_project(mocker)
        path = fetch("demo", "docs/notes.txt", "1.0", source="physionet")
    path.write_bytes(b"edited\n")

    with rm.Mocker():
        assert fetch("demo", "docs/notes.txt", "1.0").read_bytes() == b"edited\n"

    with rm.Mocker() as mocker:
        mock_project(mocker)
        assert fetch("demo", "docs/notes.txt", "1.0", source="physionet", verify=True).read_bytes() == NOTES
        assert mocker.call_count == 1  # Only the file; the checksum list is cached.


@pytest.mark.skipif(os.name == "nt", reason="POSIX file permissions")
def test_files_fetched_with_credentials_are_private(cache):
    with rm.Mocker() as mocker:
        mock_project(mocker)
        restricted = fetch("demo", "docs/notes.txt", "1.0", source="physionet", username="u", password="p")
        open_file = fetch("demo", "hosp/patients.csv.gz", "1.0", source="physionet")

    assert stat.S_IMODE(restricted.stat().st_mode) == 0o600
    assert stat.S_IMODE(open_file.stat().st_mode) & 0o044  # Open data keeps normal permissions.


@pytest.mark.parametrize("linked", ["docs", "1.0", "physionet.org"])
def test_symlinks_in_the_cache_are_refused(cache, tmp_path, linked):
    outside = tmp_path / "outside"
    outside.mkdir()
    link = {"docs": cache / "physionet.org" / "demo" / "1.0" / "docs",
            "1.0": cache / "physionet.org" / "demo" / "1.0",
            "physionet.org": cache / "physionet.org"}[linked]
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("Symlinks are unavailable on this filesystem")

    with rm.Mocker() as mocker:
        mock_project(mocker)
        with pytest.raises(ValueError, match="symbolic links are not allowed"):
            fetch("demo", "docs/notes.txt", "1.0", source="physionet")

    assert list(outside.rglob("*")) == []


def test_cache_folder_itself_may_be_a_symlink(tmp_path, monkeypatch):
    real = tmp_path / "scratch"
    real.mkdir()
    (tmp_path / "link").symlink_to(real, target_is_directory=True)
    monkeypatch.setenv("PHYSIONET_CACHE", str(tmp_path / "link"))

    with rm.Mocker() as mocker:
        mock_project(mocker)
        fetch("demo", "docs/notes.txt", "1.0", source="physionet")

    assert (real / "physionet.org" / "demo" / "1.0" / "docs" / "notes.txt").exists()
