import json
import socket
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import MagicMock

import pytest
import requests
import requests_mock
from physionet.api.client import PhysioNetClient
from physionet.api.exceptions import PhysioNetAPIError
from physionet.api.models import PublishedProject, ProjectVersion, ProjectDetail


@pytest.fixture
def client():
    """Fixture providing a PhysioNetClient instance."""
    return PhysioNetClient()


@pytest.fixture
def version_server():
    state = {"statuses": [], "requests": 0, "deployment": ""}
    versions = [{
        "slug": "demo",
        "title": "Demo",
        "version": "1.0",
        "abstract": "",
        "citation": "",
    }]

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path != f"{state['deployment']}/api/v1/projects/demo/versions/":
                body = b"not found"
                status = 404
            else:
                state["requests"] += 1
                status = state["statuses"].pop(0) if state["statuses"] else 200
                body = json.dumps(versions).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_list_published_basic(client):
    """Test listing published projects."""
    mock_response = [
        {
            "slug": "project-1",
            "version": "1.0",
            "title": "Project 1",
            "short_description": "Description 1",
            "abstract": "Abstract 1",
            "core_doi": "10.1234/p1",
            "version_doi": "10.1234/p1.v1",
            "is_latest_version": True,
            "publish_date": "2023-01-01",
            "license": {"name": "MIT"},
            "dua": None,
            "main_storage_size": 1000,
            "compressed_storage_size": 500,
        },
        {
            "slug": "project-2",
            "version": "2.0",
            "title": "Project 2",
            "short_description": "Description 2",
            "abstract": "Abstract 2",
            "core_doi": "10.1234/p2",
            "version_doi": "10.1234/p2.v2",
            "is_latest_version": True,
            "publish_date": "2023-02-01",
            "license": {"name": "GPL"},
            "dua": None,
            "main_storage_size": 2000,
            "compressed_storage_size": 1000,
        },
    ]

    with requests_mock.Mocker() as m:
        m.get("https://physionet.org/api/v1/projects/published/", json=mock_response)

        result = client.projects.list_published()

        assert len(result) == 2
        assert isinstance(result[0], PublishedProject)
        assert result[0].slug == "project-1"
        assert result[1].slug == "project-2"


def test_iter_published(client):
    """Test iterating through published projects."""
    mock_response = [
        {
            "slug": "project-1",
            "version": "1.0",
            "title": "Project 1",
            "short_description": "",
            "abstract": "",
            "core_doi": None,
            "version_doi": None,
            "is_latest_version": True,
            "publish_date": "",
            "license": None,
            "dua": None,
            "main_storage_size": 0,
            "compressed_storage_size": 0,
        },
        {
            "slug": "project-2",
            "version": "1.0",
            "title": "Project 2",
            "short_description": "",
            "abstract": "",
            "core_doi": None,
            "version_doi": None,
            "is_latest_version": True,
            "publish_date": "",
            "license": None,
            "dua": None,
            "main_storage_size": 0,
            "compressed_storage_size": 0,
        },
    ]

    with requests_mock.Mocker() as m:
        m.get("https://physionet.org/api/v1/projects/published/", json=mock_response)

        projects = list(client.projects.iter_published())

        assert len(projects) == 2
        assert projects[0].slug == "project-1"
        assert projects[1].slug == "project-2"


def test_search_projects(client):
    """Test searching for projects."""
    mock_response = [
        {
            "slug": "ecg-project",
            "version": "1.0",
            "title": "ECG Database",
            "short_description": "ECG data",
            "abstract": "ECG abstract",
            "core_doi": None,
            "version_doi": None,
            "is_latest_version": True,
            "publish_date": "",
            "license": None,
            "dua": None,
            "main_storage_size": 0,
            "compressed_storage_size": 0,
        }
    ]

    with requests_mock.Mocker() as m:
        m.get("https://physionet.org/api/v1/projects/search/", json=mock_response)

        results = client.projects.search(search_term="ECG", resource_type=["all"])

        assert "search_term=ECG" in m.last_request.url
        assert len(results) == 1
        assert isinstance(results[0], PublishedProject)
        assert results[0].slug == "ecg-project"


def test_list_versions(client):
    """Test listing all versions of a project."""
    mock_response = [
        {
            "slug": "test-project",
            "title": "Test Project",
            "version": "1.0",
            "abstract": "Version 1.0",
            "citation": "Citation v1.0",
        },
        {
            "slug": "test-project",
            "title": "Test Project",
            "version": "2.0",
            "abstract": "Version 2.0",
            "citation": "Citation v2.0",
        },
    ]

    with requests_mock.Mocker() as m:
        m.get("https://physionet.org/api/v1/projects/test-project/versions/", json=mock_response)

        versions = client.projects.list_versions("test-project")

        assert len(versions) == 2
        assert isinstance(versions[0], ProjectVersion)
        assert versions[0].version == "1.0"
        assert versions[1].version == "2.0"


@pytest.mark.parametrize(
    ("deployment", "wire_path"),
    [
        ("", ""),
        ("/My API", "/My%20API"),
        ("/café", "/caf%C3%A9"),
        ("/%73ite", "/site"),
        ("/lab%2Fwest", "/lab%2Fwest"),
    ],
    ids=["root", "space", "unicode", "escaped-unreserved", "escaped-slash"],
)
def test_list_versions_retries_transient_status(version_server, deployment, wire_path):
    base_url, state = version_server
    state["deployment"] = wire_path
    state["statuses"] = [503]
    client = PhysioNetClient(base_url=base_url + deployment)
    versions_url = f"{base_url}{wire_path}/api/v1/projects/demo/versions/"
    default_adapter = client.session.get_adapter("http://")

    try:
        versions = client.projects.list_versions("demo", retries=1)
    finally:
        client.close()

    assert versions[0].version == "1.0"
    assert state["requests"] == 2
    version_adapter = client.session.get_adapter(versions_url)
    assert version_adapter is not default_adapter
    assert version_adapter.max_retries.total == 1
    assert version_adapter.max_retries.raise_on_status is False


def test_list_versions_retries_zero_makes_one_request(version_server):
    base_url, state = version_server
    state["statuses"] = [503, 200]
    client = PhysioNetClient(base_url=base_url)
    versions_url = f"{base_url}/api/v1/projects/demo/versions/"
    default_adapter = client.session.get_adapter("http://")

    try:
        with pytest.raises(PhysioNetAPIError, match="HTTP 503"):
            client.projects.list_versions("demo", retries=0)
    finally:
        client.close()

    assert state["requests"] == 1
    assert client.session.get_adapter(versions_url) is default_adapter
    assert not hasattr(client.session, "_physionet_version_adapters")


def test_list_versions_disables_cached_retries_on_custom_deployment(version_server):
    base_url, state = version_server
    state["deployment"] = "/My%20API"
    with PhysioNetClient(base_url=base_url + "/My API", use_saved_login=False) as client:
        client.projects.list_versions("demo", retries=1)
        state["statuses"] = [503, 200]
        with pytest.raises(PhysioNetAPIError, match="HTTP 503"):
            client.projects.list_versions("demo", retries=0)
    assert state["requests"] == 2  # One successful lookup, then one failed lookup.


def test_list_versions_closes_response(client):
    response = MagicMock()
    response.json.return_value = [{
        "slug": "demo",
        "title": "Demo",
        "version": "1.0",
        "abstract": "",
        "citation": "",
    }]
    client._make_request = MagicMock(return_value=response)

    client.projects.list_versions("demo")

    response.close.assert_called_once()


def test_get_details(client):
    """Test getting project details."""
    mock_response = {
        "slug": "test-project",
        "title": "Test Project",
        "version": "1.0",
        "abstract": "Test abstract",
        "license": {"name": "MIT"},
        "short_description": "Short desc",
        "project_home_page": "https://example.com",
        "publish_datetime": "2023-01-01T00:00:00",
        "doi": "10.1234/test",
        "main_storage_size": 1000,
        "compressed_storage_size": 500,
    }

    with requests_mock.Mocker() as m:
        m.get("https://physionet.org/api/v1/projects/test-project/versions/1.0/", json=mock_response)

        detail = client.projects.get_details("test-project", "1.0")

        assert isinstance(detail, ProjectDetail)
        assert detail.slug == "test-project"
        assert detail.version == "1.0"
        assert detail.doi == "10.1234/test"


def test_download_checksums(client, tmp_path):
    """Test downloading checksums file."""
    checksum_content = b"abc123 file1.txt\ndef456 file2.txt\n"
    output_file = tmp_path / "checksums.txt"

    with requests_mock.Mocker() as m:
        m.get(
            "https://physionet.org/api/v1/projects/published/test-project/1.0/sha256sums/", content=checksum_content
        )

        client.projects.download_checksums("test-project", "1.0", str(output_file))

        assert output_file.exists()
        assert output_file.read_bytes() == checksum_content


@pytest.fixture
def projects_server():
    """A local PhysioNet API that lists versions for any project, recording connections."""
    state = {"requests": 0, "connections": set(), "delay": 0, "by_slug": Counter(), "statuses": {}}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_GET(self):
            state["requests"] += 1
            state["connections"].add(self.client_address)
            if state["delay"]:
                time.sleep(state["delay"])
            slug = self.path.split("/")[4]
            state["by_slug"][slug] += 1
            statuses = state["statuses"].get(slug)
            status = statuses.pop(0) if statuses else 200
            if status == 503 and state["by_slug"][slug] == 1 and state.get("both_at_server"):
                # Let both requests reach the server before replying to either one.
                state["both_at_server"].wait(timeout=5)
            version = {"slug": slug, "title": slug, "version": "1.0", "abstract": "", "citation": ""}
            body = json.dumps([version]).encode()
            try:
                self.send_response(status)
                if self.close_connection:
                    self.send_header("Connection", "close")
                if status == 302:
                    self.send_header("Location", state["locations"][slug])
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                # A client that timed out has already closed the connection
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    # Wait for request threads when closing, so that none outlives the test and
    # writes to stderr while a later test is running
    server.daemon_threads = False
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_list_versions_reuses_one_connection_for_all_projects(projects_server):
    base_url, state = projects_server
    client = PhysioNetClient(base_url=base_url)
    adapters_before = len(client.session.adapters)
    try:
        for i in range(50):
            assert client.projects.list_versions(f"project{i}")[0].slug == f"project{i}"
    finally:
        client.close()

    assert state["requests"] == 50
    assert len(state["connections"]) == 1
    # One adapter for every project, rather than one per project
    assert len(client.session.adapters) == adapters_before + 1


@pytest.mark.parametrize("budgets", [(1, 0), (0, 1), (1, 2), (2, 1)], ids=["1-0", "0-1", "1-2", "2-1"])
@pytest.mark.parametrize("warm_pool", [True, False], ids=["warm", "cold"])
def test_concurrent_version_lookups_keep_their_retry_counts(projects_server, monkeypatch, budgets, warm_pool):
    base_url, state = projects_server
    low, high = sorted(budgets)
    slugs = {low: "low", high: "high"}
    state["statuses"] = {slug: [503] * high for slug in slugs.values()}
    state["both_at_server"] = threading.Barrier(2)
    first_at_send = threading.Event()
    both_at_send = threading.Barrier(2)

    with PhysioNetClient(base_url=base_url, use_saved_login=False) as client:
        prefix = f"{client.api_base}projects/"
        mounts_before = len(client.session.adapters)
        if warm_pool:
            client.projects.list_versions("warm")
        pool_before = client.session.get_adapter(prefix).poolmanager
        send = client.session.send

        def send_together(request, **kwargs):
            first_at_send.set()
            both_at_send.wait(timeout=5)
            return send(request, **kwargs)

        def lookup(slug, retries):
            try:
                return client.projects.list_versions(slug, retries=retries)[0].slug
            except PhysioNetAPIError as exc:
                return exc.response.status_code

        monkeypatch.setattr(client.session, "send", send_together)
        first_retries, second_retries = budgets
        with ThreadPoolExecutor(max_workers=2) as workers:
            first = workers.submit(lookup, slugs[first_retries], retries=first_retries)
            assert first_at_send.wait(timeout=5)
            second = workers.submit(lookup, slugs[second_retries], retries=second_retries)
            futures = {first_retries: first, second_retries: second}
            results = {budget: task.result(timeout=10) for budget, task in futures.items()}
        pool_after = client.session.get_adapter(prefix).poolmanager
        mounts_after = len(client.session.adapters)

    assert results == {high: "high", low: 503}
    assert state["by_slug"]["low"] == low + 1
    assert state["by_slug"]["high"] == high + 1
    assert mounts_after == mounts_before + 1
    if warm_pool:
        assert pool_after is pool_before


@pytest.mark.parametrize("send_via", ["get", "session.send", "adapter.send"])
def test_version_retry_count_survives_a_nested_request_and_redirect(projects_server, send_via):
    base_url, state = projects_server
    target = "/api/v1/projects/target/versions/"
    hop = "/api/v1/projects/hop/versions/"
    state.update(
        statuses={"outer": [302], "hop": [302], "target": [503], "other": [503]},
        locations={"outer": hop, "hop": target},
    )
    other_statuses = []
    with PhysioNetClient(base_url=base_url, use_saved_login=False) as client:
        # Requests 2.32 can keep a multi-hop redirect's pool alive until garbage collection.
        client.session.headers["Connection"] = "close"

        def on_redirect(response, **kwargs):
            if response.is_redirect and response.headers["Location"] == hop:
                client.projects.list_versions("nested", retries=0)
                session = client.session
                url = f"{client.api_base}projects/other/versions/"
                if send_via == "get":
                    other = session.get(url)
                else:
                    prepared = session.prepare_request(requests.Request("GET", url))
                    sender = session if send_via == "session.send" else session.get_adapter(url)
                    other = sender.send(prepared, timeout=5)
                with other:
                    other_statuses.append(other.status_code)

        client.session.hooks["response"].append(on_redirect)
        try:
            result = [version.slug for version in client.projects.list_versions("outer", retries=1)]
        except PhysioNetAPIError as error:
            result = error.response.status_code
    assert result == ["target"]
    assert other_statuses == [503]
    assert state["by_slug"] == {"outer": 1, "nested": 1, "other": 1, "hop": 1, "target": 2}


def test_version_lookup_respects_a_more_specific_adapter(projects_server):
    base_url, state = projects_server
    state["statuses"]["demo"] = [503]
    with PhysioNetClient(base_url=base_url, use_saved_login=False) as client:
        prefix = f"{client.api_base}projects/demo/"
        caller_adapter = requests.adapters.HTTPAdapter()
        client.session.mount(prefix, caller_adapter)
        with pytest.raises(PhysioNetAPIError, match="HTTP 503"):
            client.projects.list_versions("demo", retries=1)
        assert client.session.get_adapter(prefix + "versions/") is caller_adapter
    assert state["by_slug"]["demo"] == 1


def test_copied_client_can_disable_its_cached_version_retries(projects_server):
    base_url, state = projects_server
    state["statuses"]["copied"] = [503]
    with PhysioNetClient(base_url=base_url, use_saved_login=False) as original:
        original.projects.list_versions("warm", retries=1)
        with deepcopy(original) as copied:
            with pytest.raises(PhysioNetAPIError, match="HTTP 503"):
                copied.projects.list_versions("copied", retries=0)
    assert state["by_slug"]["copied"] == 1


def test_list_versions_timeout_makes_one_request(projects_server):
    base_url, state = projects_server
    state["delay"] = 1.0
    client = PhysioNetClient(base_url=base_url, timeout=0.2)
    start = time.monotonic()
    try:
        with pytest.raises(requests.exceptions.ReadTimeout):
            client.projects.list_versions("demo", retries=3)
    finally:
        client.close()

    assert state["requests"] == 1
    assert time.monotonic() - start < 1.0


def test_list_versions_connection_refused_is_immediate():
    # A port that nothing listens on
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    client = PhysioNetClient(base_url=f"http://127.0.0.1:{port}")
    start = time.monotonic()
    try:
        with pytest.raises(requests.exceptions.ConnectionError):
            client.projects.list_versions("demo", retries=3)
    finally:
        client.close()

    assert time.monotonic() - start < 1.0
