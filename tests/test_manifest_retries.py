"""Tests for retrying the download of a project's checksum list (SHA256SUMS.txt)."""

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

import physionet
from physionet.fs import PhysioNetFileSystem

NOTES = b"Synthetic notes.\n"
MANIFEST = f"{hashlib.sha256(NOTES).hexdigest()}  notes.txt\n".encode()
VERSIONS = [{"slug": "demo", "title": "Demo", "version": "1.0", "abstract": "", "citation": ""}]


@pytest.fixture
def server(tmp_path, monkeypatch):
    """A local PhysioNet that can fail the checksum list or a file before succeeding."""
    monkeypatch.setenv("PHYSIONET_CACHE", str(tmp_path / "cache"))
    for name in ("PHYSIONET_USERNAME", "PHYSIONET_PASSWORD", "PHYSIONET_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    state = {
        "deployment": "", "status": 502,
        "manifest_failures": 1, "file_failures": 0,
        "manifest_requests": 0, "file_requests": 0,
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, status, body=b"", content_type="text/plain"):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            deployment = state["deployment"]
            if self.path == f"{deployment}/api/v1/projects/demo/versions/":
                self.send(200, json.dumps(VERSIONS).encode(), "application/json")
            elif self.path == f"{deployment}/files/demo/1.0/SHA256SUMS.txt":
                state["manifest_requests"] += 1
                if state["manifest_requests"] <= state["manifest_failures"]:
                    self.send(state["status"])
                else:
                    self.send(200, MANIFEST)
            elif self.path == f"{deployment}/files/demo/1.0/notes.txt":
                state["file_requests"] += 1
                if state["file_requests"] <= state["file_failures"]:
                    self.send(state["status"])
                else:
                    self.send(200, NOTES)
            else:
                self.send(404)

        def do_HEAD(self):
            self.send(404)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    state["base_url"] = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        yield state
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("status", [502, 503, 504])
def test_fetch_retries_the_checksum_list(server, status):
    server["status"] = status
    path = physionet.fetch("demo", "notes.txt", "1.0", base_url=server["base_url"], show_progress=False)
    assert path.read_bytes() == NOTES
    assert server["manifest_requests"] == 2


def test_fetch_retries_the_checksum_list_when_signed_in(server):
    # The list is first requested without the sign-in, to find out whether
    # the project is open
    path = physionet.fetch(
        "demo",
        "notes.txt",
        "1.0",
        base_url=server["base_url"],
        username="user",
        password="pass",
        show_progress=False,
    )
    assert path.read_bytes() == NOTES
    assert server["manifest_requests"] == 2


def test_filesystem_retries_the_checksum_list(server):
    fs = PhysioNetFileSystem(base_url=server["base_url"], show_progress=False, skip_instance_cache=True)
    assert fs.ls("demo/1.0", detail=False) == ["demo/1.0/notes.txt"]
    assert server["manifest_requests"] == 2


def test_download_retries_the_checksum_list(server, tmp_path):
    destination = physionet.download(
        "demo", version="1.0", output_dir=str(tmp_path / "out"), base_url=server["base_url"], show_progress=False
    )
    assert (destination / "notes.txt").read_bytes() == NOTES
    assert server["manifest_requests"] == 2


@pytest.mark.parametrize(("manifest_failures", "file_failures"), [(1, 0), (0, 1)], ids=["manifest", "file"])
def test_download_retries_on_custom_deployment_path(server, tmp_path, manifest_failures, file_failures):
    server.update(
        deployment="/lab~/My%20API", status=503, manifest_failures=manifest_failures, file_failures=file_failures,
    )
    destination = physionet.download(
        "demo", version="1.0", retries=1, output_dir=str(tmp_path / "out"),
        base_url=f"{server['base_url']}/lab%7E/My API", source="physionet", show_progress=False,
    )
    assert server["manifest_requests"] == 1 + manifest_failures
    assert server["file_requests"] == 1 + file_failures
    assert (destination / "notes.txt").read_bytes() == NOTES


def test_retries_can_be_turned_off(server):
    with pytest.raises(requests.HTTPError) as caught:
        physionet.fetch("demo", "notes.txt", "1.0", base_url=server["base_url"], retries=0)
    assert caught.value.response.status_code == 502
    assert server["manifest_requests"] == 1


def test_errors_that_are_not_transient_are_not_retried(server):
    server["status"] = 404
    with pytest.raises(physionet.api.exceptions.NotFoundError):
        physionet.fetch("demo", "notes.txt", "1.0", base_url=server["base_url"])
    assert server["manifest_requests"] == 1
