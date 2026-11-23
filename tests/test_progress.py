"""Tests for turning progress bars off with show_progress=False."""

import gzip
import hashlib
import os
import subprocess
import sys
import textwrap

import pytest
import requests_mock

import physionet
from physionet.fs import PhysioNetFileSystem

BASE = "https://physionet.org"
VERSIONS_URL = f"{BASE}/api/v1/projects/demo/versions/"
VERSIONS = [{"slug": "demo", "title": "Demo", "version": "1.0", "abstract": "", "citation": ""}]
FILES_URL = f"{BASE}/files/demo/1.0"
FILES = {
    "data/table.csv": b"id,value\n1,10\n2,20\n",
    "notes.txt": b"notes",
    "hosp/patients.csv.gz": gzip.compress(b"subject_id\n1\n"),
}


def manifest():
    return "".join(f"{hashlib.sha256(body).hexdigest()}  {path}\n" for path, body in FILES.items())


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("PHYSIONET_CACHE", str(tmp_path / "cache"))
    for name in ("PHYSIONET_USERNAME", "PHYSIONET_PASSWORD", "PHYSIONET_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    with requests_mock.Mocker() as mock:
        mock.get(VERSIONS_URL, json=VERSIONS)
        mock.get(f"{FILES_URL}/SHA256SUMS.txt", text=manifest())
        for path, body in FILES.items():
            mock.get(f"{FILES_URL}/{path}", content=body, headers={"Content-Length": str(len(body))})
        yield mock


def has_progress_bar(text):
    # tqdm draws bars like "notes.txt: 100%|██████| 5.00/5.00 [00:00<...]"
    return "%|" in text


@pytest.mark.parametrize("jobs", [1, 2])
@pytest.mark.parametrize("show_progress", [True, False])
def test_download(server, tmp_path, capsys, jobs, show_progress):
    physionet.download(
        "demo",
        version="1.0",
        output_dir=str(tmp_path / "out"),
        source="physionet",
        jobs=jobs,
        show_progress=show_progress,
    )
    captured = capsys.readouterr()
    assert has_progress_bar(captured.err) == show_progress
    # The messages about the download are still printed
    assert "Complete: 3 downloaded" in captured.out
    assert (tmp_path / "out" / "demo-1.0" / "notes.txt").read_bytes() == FILES["notes.txt"]


@pytest.mark.parametrize("show_progress", [True, False])
def test_fetch_and_load(server, capsys, show_progress):
    path = physionet.fetch("demo", "notes.txt", "1.0", source="physionet", show_progress=show_progress)
    assert path.read_bytes() == FILES["notes.txt"]
    assert has_progress_bar(capsys.readouterr().err) == show_progress

    table = physionet.load("demo", "hosp/patients", "1.0", source="physionet", show_progress=show_progress)
    assert table["subject_id"].tolist() == [1]
    assert has_progress_bar(capsys.readouterr().err) == show_progress


@pytest.mark.parametrize("show_progress", [True, False])
def test_filesystem(server, capsys, show_progress):
    fs = PhysioNetFileSystem(source="physionet", show_progress=show_progress, skip_instance_cache=True)
    with fs.open("demo/1.0/data/table.csv") as f:
        assert f.read() == FILES["data/table.csv"]
    assert has_progress_bar(capsys.readouterr().err) == show_progress


def test_tqdm_disable_hides_every_bar(tmp_path):
    """TQDM_DISABLE also hides the bars for each file, which used to ignore it."""
    script = textwrap.dedent(
        f"""
        import requests_mock
        import physionet

        with requests_mock.Mocker() as mock:
            mock.get({VERSIONS_URL!r}, json={VERSIONS!r})
            mock.get({FILES_URL + "/SHA256SUMS.txt"!r}, text={manifest()!r})
            for path, body in {FILES!r}.items():
                mock.get({FILES_URL!r} + "/" + path, content=body, headers={{"Content-Length": str(len(body))}})
            physionet.download("demo", version="1.0", output_dir={str(tmp_path / "out")!r}, source="physionet")
        """
    )
    env = dict(os.environ, TQDM_DISABLE="1", PHYSIONET_CACHE=str(tmp_path / "cache"))
    for name in ("PHYSIONET_USERNAME", "PHYSIONET_PASSWORD", "PHYSIONET_TOKEN"):
        env.pop(name, None)
    result = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True, check=True)
    assert "Complete: 3 downloaded" in result.stdout
    assert not has_progress_bar(result.stderr)
