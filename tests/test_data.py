"""Tests for fetching PhysioNet files into the cache and loading tables."""

import gzip
import hashlib

import pytest
import requests
import requests_mock as rm

import physionet
from physionet.api.exceptions import ForbiddenError, NotFoundError
from physionet.data import fetch, get_cache_dir, load

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
    assert (cache / "demo" / "1.0" / "hosp" / "patients.csv.gz").read_bytes() == PATIENTS
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
    assert path == cache / "demo" / "1.0" / "hosp" / "patients.csv.gz"


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

    assert path == cache / "demo" / "1.0" / "docs" / "notes.txt"


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

    assert not (cache / "demo" / "1.0" / "hosp" / "patients.csv.gz").exists()
    assert [p for p in (cache / "demo").rglob("*") if p.is_file()] == []


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

    assert not (cache / ".manifests" / "demo" / "1.0").exists()


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
