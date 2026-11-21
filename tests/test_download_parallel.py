"""Tests for downloading several files at once (download(jobs=N), --jobs)."""

import hashlib
import sys
import threading
from concurrent.futures import as_completed as real_as_completed
from unittest.mock import MagicMock, patch

import pytest
import requests_mock

from importlib import import_module
from physionet.cli import main
from physionet.download import (
    MAX_JOBS,
    DownloadError,
    _Cancellation,
    _DownloadCancelled,
    _download_file,
    download,
)

# physionet exports a download() function, which hides the submodule as an attribute.
download_module = import_module("physionet.download")

BASE = "https://physionet.org"
VERSIONS_URL = f"{BASE}/api/v1/projects/demo/versions/"
VERSIONS = [{"slug": "demo", "title": "Demo", "version": "1.0", "abstract": "", "citation": ""}]
FILES_URL = f"{BASE}/files/demo/1.0"


def make_files(count):
    return {f"dir{i % 3}/file{i:03d}.dat": f"content of file {i}".encode() for i in range(count)}


def manifest_for(files):
    return "\n".join(f"{hashlib.sha256(body).hexdigest()}  {path}" for path, body in files.items())


def mock_project(mock, files):
    mock.get(VERSIONS_URL, json=VERSIONS)
    mock.get(f"{FILES_URL}/SHA256SUMS.txt", text=manifest_for(files))


def download_threads():
    return [thread for thread in threading.enumerate() if thread.name.startswith("physionet-download")]


def test_parallel_download_gets_every_file(tmp_path, capsys):
    files = make_files(20)
    with requests_mock.Mocker() as mock:
        mock_project(mock, files)
        for path, body in files.items():
            mock.get(f"{FILES_URL}/{path}", content=body)
        dest = download("demo", version="1.0", output_dir=str(tmp_path), source="physionet", jobs=4)

    for path, body in files.items():
        assert (dest / path).read_bytes() == body
    assert "Complete: 20 downloaded, 0 skipped, 0 failed" in capsys.readouterr().out
    assert download_threads() == []


@pytest.fixture
def local_server():
    """A real HTTP server on localhost (requests_mock handles one request at a time).

    Yields (base_url, files, on_file), where on_file is called before each file
    response is sent.
    """
    import json
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    state = {"files": {}, "on_file": lambda path: None}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def send_body(self, body, content_type="application/octet-stream", status=200):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            files = state["files"]
            prefix = "/files/demo/1.0/"
            if self.path == "/api/v1/projects/demo/versions/":
                self.send_body(json.dumps(VERSIONS).encode(), "application/json")
            elif self.path == prefix + "SHA256SUMS.txt":
                self.send_body(manifest_for(files).encode(), "text/plain")
            elif self.path.startswith(prefix) and self.path[len(prefix):] in files:
                path = self.path[len(prefix):]
                state["on_file"](path)
                stall = state.get("stall")
                if stall is None:
                    self.send_body(files[path])
                    return
                # Send half the file, then go quiet until the test releases it.
                body = files[path]
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body[: len(body) // 2])
                self.wfile.flush()
                stall.wait(30)
                try:
                    self.wfile.write(body[len(body) // 2:])
                except OSError:
                    pass
            else:
                self.send_body(b"not found", "text/plain", status=404)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", state
    finally:
        server.shutdown()
        server.server_close()


def test_parallel_download_really_runs_files_at_once(tmp_path, local_server):
    # Each file response waits until `jobs` requests are in flight together;
    # downloading one at a time, the barrier times out and the files fail.
    base_url, state = local_server
    jobs = 3
    state["files"] = make_files(9)
    barrier = threading.Barrier(jobs, timeout=5)
    state["on_file"] = lambda path: barrier.wait()

    dest = download("demo", version="1.0", output_dir=str(tmp_path), base_url=base_url, jobs=jobs)

    for path, body in state["files"].items():
        assert (dest / path).read_bytes() == body
    assert download_threads() == []


def test_parallel_failures_keep_other_files_and_are_in_manifest_order(tmp_path, capsys):
    files = make_files(6)
    paths = list(files)
    failing = {paths[1], paths[4]}
    with requests_mock.Mocker() as mock:
        mock_project(mock, files)
        for path, body in files.items():
            if path in failing:
                mock.get(f"{FILES_URL}/{path}", status_code=500)
            else:
                mock.get(f"{FILES_URL}/{path}", content=body)
        with pytest.raises(DownloadError) as caught:
            download("demo", version="1.0", output_dir=str(tmp_path), source="physionet", jobs=3)

    assert [path for path, _ in caught.value.failures] == [paths[1], paths[4]]
    assert caught.value.downloaded == 4
    dest = tmp_path / "demo-1.0"
    for path in files:
        assert (dest / path).exists() == (path not in failing)
    assert "Incomplete: 4 downloaded, 0 skipped, 2 failed" in capsys.readouterr().out


def test_parallel_download_skips_files_already_downloaded(tmp_path, capsys):
    files = make_files(5)
    dest = tmp_path / "demo-1.0"
    present = list(files)[:2]
    for path in present:
        (dest / path).parent.mkdir(parents=True, exist_ok=True)
        (dest / path).write_bytes(files[path])
    with requests_mock.Mocker() as mock:
        mock_project(mock, files)
        for path, body in files.items():
            mock.get(f"{FILES_URL}/{path}", content=body)
        download("demo", version="1.0", output_dir=str(tmp_path), source="physionet", jobs=4)

    requested = {request.path for request in mock.request_history}
    assert not any(request_path.endswith(path) for path in present for request_path in requested)
    assert "Complete: 3 downloaded, 2 skipped, 0 failed" in capsys.readouterr().out


@pytest.mark.parametrize("jobs", [0, -1, MAX_JOBS + 1, True, 2.0, "4"])
def test_jobs_must_be_a_whole_number_in_range(tmp_path, jobs):
    with requests_mock.Mocker() as mock:
        with pytest.raises(ValueError, match="jobs must be"):
            download("demo", version="1.0", output_dir=str(tmp_path), jobs=jobs)
    assert mock.request_history == []


def test_cli_passes_jobs_and_rejects_bad_values(capsys):
    with patch.object(download_module, "download") as mock_download:
        for args, expected in ((["--jobs", "3"], 3), (["-j", str(MAX_JOBS)], MAX_JOBS), ([], 1)):
            with patch.object(sys, "argv", ["physionet", "download", "demo", *args]):
                assert main() == 0
            assert mock_download.call_args.kwargs["jobs"] == expected

        for bad in ("0", str(MAX_JOBS + 1), "two", "2.5"):
            with patch.object(sys, "argv", ["physionet", "download", "demo", "--jobs", bad]):
                with pytest.raises(SystemExit) as caught:
                    main()
            assert caught.value.code == 2
            assert f"must be a whole number from 1 to {MAX_JOBS}" in capsys.readouterr().err
    assert mock_download.call_count == 3


def test_parallel_s3_download_shares_one_client(tmp_path):
    files = make_files(6)
    s3 = MagicMock()
    s3.head_object.side_effect = lambda Bucket, Key: {"ContentLength": len(files[Key.split("/", 2)[2]])}

    def download_fileobj(bucket, key, fileobj, Callback):
        body = files[key.split("/", 2)[2]]
        fileobj.write(body)
        Callback(len(body))

    s3.download_fileobj.side_effect = download_fileobj
    boto3 = MagicMock()
    boto3.client.return_value = s3

    with requests_mock.Mocker() as mock, patch.dict("sys.modules", {"boto3": boto3}):
        mock_project(mock, files)
        dest = download("demo", version="1.0", output_dir=str(tmp_path), source="aws", jobs=3)

    boto3.client.assert_called_once_with("s3")
    assert s3.download_fileobj.call_count == 6
    for path, body in files.items():
        assert (dest / path).read_bytes() == body


def test_cancelled_file_download_stops_and_keeps_the_partial_file(tmp_path):
    import requests

    dest = tmp_path / "big.dat"
    cancel = _Cancellation()
    cancel.set()
    with requests_mock.Mocker() as mock:
        mock.get("https://example.com/big.dat", content=b"x" * 50000)
        with pytest.raises(_DownloadCancelled):
            _download_file(
                "https://example.com/big.dat", dest, "hash", requests.Session(),
                show_progress=False, cancel=cancel,
            )
    assert dest.exists()


def test_ctrl_c_during_parallel_download_cancels_files_not_started(tmp_path, capsys):
    files = make_files(40)

    def interrupted_after_first(futures):
        completed = real_as_completed(futures)
        next(completed)
        raise KeyboardInterrupt()

    with requests_mock.Mocker() as mock:
        mock_project(mock, files)
        for path, body in files.items():
            mock.get(f"{FILES_URL}/{path}", content=body)
        with patch.object(download_module, "as_completed", interrupted_after_first):
            with pytest.raises(KeyboardInterrupt):
                download("demo", version="1.0", output_dir=str(tmp_path), source="physionet", jobs=2)

    file_requests = [r for r in mock.request_history if "/files/demo/1.0/dir" in r.url]
    assert len(file_requests) < len(files)
    assert download_threads() == []
    assert "Download interrupted" in capsys.readouterr().err


@pytest.mark.skipif(sys.platform == "win32", reason="sends SIGINT to the test process")
def test_ctrl_c_stops_files_waiting_on_a_slow_server(tmp_path, local_server, capsys):
    # Both files stall halfway. Ctrl-C must end the waiting reads rather than
    # wait up to the 60 s read timeout for a next chunk that never comes.
    import os
    import signal
    import time

    base_url, state = local_server
    jobs = 2
    state["files"] = {f"file{i}.dat": b"x" * 20000 for i in range(jobs)}
    state["stall"] = threading.Event()
    in_flight = threading.Barrier(jobs + 1, timeout=10)
    state["on_file"] = lambda path: in_flight.wait()

    def interrupt_once_files_stall():
        in_flight.wait()
        time.sleep(0.3)
        os.kill(os.getpid(), signal.SIGINT)

    interrupter = threading.Thread(target=interrupt_once_files_stall, daemon=True)
    interrupter.start()
    started = time.monotonic()
    try:
        with pytest.raises(KeyboardInterrupt):
            download("demo", version="1.0", output_dir=str(tmp_path), base_url=base_url, jobs=jobs)
        elapsed = time.monotonic() - started
    finally:
        state["stall"].set()
        interrupter.join(5)

    assert elapsed < 5
    assert download_threads() == []
    err = capsys.readouterr().err
    assert "Stopping 2 running download(s)" in err
    assert "Download interrupted" in err


def test_cancellation_ends_a_response_watched_after_it_was_set():
    cancel = _Cancellation()
    cancel.set()
    response = MagicMock()
    with patch.object(download_module, "_abort_response") as abort:
        cancel.watch(response)
    abort.assert_called_once_with(response)
