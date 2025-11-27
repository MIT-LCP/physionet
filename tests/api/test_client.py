import pytest
import requests_mock
from physionet.api.client import PhysioNetClient
from physionet.api.exceptions import (
    BadRequestError,
    ForbiddenError,
    NotFoundError,
    RateLimitError,
    PhysioNetAPIError,
)


def test_client_initialization():
    """Test client initializes with correct defaults."""
    client = PhysioNetClient()

    assert client.base_url == "https://physionet.org"
    assert client.api_base == "https://physionet.org/api/v1/"
    assert client.timeout == 30
    assert "User-Agent" in client.session.headers
    assert client.session.headers["Accept"] == "application/json"


def test_client_initialization_with_custom_base_url():
    """Test client with custom base URL."""
    client = PhysioNetClient(base_url="https://test.example.com")

    assert client.base_url == "https://test.example.com"
    assert client.api_base == "https://test.example.com/api/v1/"


def test_client_initialization_with_trailing_slash():
    """Test that trailing slash is removed from base URL."""
    client = PhysioNetClient(base_url="https://physionet.org/")

    assert client.base_url == "https://physionet.org"


def test_client_initialization_with_auth():
    """Test client initializes with authentication."""
    client = PhysioNetClient(username="testuser", password="testpass")

    assert client.session.auth == ("testuser", "testpass")


def test_client_initialization_without_auth():
    """Test client initializes without authentication."""
    client = PhysioNetClient()

    assert client.session.auth is None


def test_client_has_projects_api():
    """Test client has projects API endpoint."""
    client = PhysioNetClient()

    assert hasattr(client, "projects")
    assert client.projects.client is client


def test_make_request_success():
    """Test successful API request."""
    client = PhysioNetClient()

    with requests_mock.Mocker() as m:
        m.get("https://physionet.org/api/v1/test/", json={"status": "ok"})

        response = client._make_request("GET", "test/")

        assert response.json() == {"status": "ok"}


def test_make_request_with_params():
    """Test API request with query parameters."""
    client = PhysioNetClient()

    with requests_mock.Mocker() as m:
        m.get("https://physionet.org/api/v1/test/", json={"status": "ok"})

        response = client._make_request("GET", "test/", params={"page": 1, "size": 10})

        assert "page=1" in m.last_request.url
        assert "size=10" in m.last_request.url


def test_error_handling_400():
    """Test 400 Bad Request error handling."""
    client = PhysioNetClient()

    with requests_mock.Mocker() as m:
        m.get("https://physionet.org/api/v1/test/", status_code=400, json={"error": "Bad request"})

        with pytest.raises(BadRequestError) as exc_info:
            client._make_request("GET", "test/")

        assert "Bad request" in str(exc_info.value)


def test_error_handling_403():
    """Test 403 Forbidden error handling."""
    client = PhysioNetClient()

    with requests_mock.Mocker() as m:
        m.get("https://physionet.org/api/v1/test/", status_code=403, json={"error": "Forbidden"})

        with pytest.raises(ForbiddenError) as exc_info:
            client._make_request("GET", "test/")

        assert "Forbidden" in str(exc_info.value)


def test_error_handling_404():
    """Test 404 Not Found error handling."""
    client = PhysioNetClient()

    with requests_mock.Mocker() as m:
        m.get("https://physionet.org/api/v1/test/", status_code=404, json={"error": "Not found"})

        with pytest.raises(NotFoundError) as exc_info:
            client._make_request("GET", "test/")

        assert "Not found" in str(exc_info.value)


def test_error_handling_429():
    """Test 429 Rate Limit error handling."""
    client = PhysioNetClient()

    with requests_mock.Mocker() as m:
        m.get("https://physionet.org/api/v1/test/", status_code=429, json={"error": "Rate limit exceeded"})

        with pytest.raises(RateLimitError) as exc_info:
            client._make_request("GET", "test/")

        assert "Rate limit exceeded" in str(exc_info.value)


def test_error_handling_500():
    """Test 500 Server Error handling."""
    client = PhysioNetClient()

    with requests_mock.Mocker() as m:
        m.get("https://physionet.org/api/v1/test/", status_code=500, json={"error": "Server error"})

        with pytest.raises(PhysioNetAPIError) as exc_info:
            client._make_request("GET", "test/")

        assert "HTTP 500" in str(exc_info.value)


def test_error_handling_non_json_response():
    """Test error handling with non-JSON error response."""
    client = PhysioNetClient()

    with requests_mock.Mocker() as m:
        m.get("https://physionet.org/api/v1/test/", status_code=500, text="Internal Server Error")

        with pytest.raises(PhysioNetAPIError) as exc_info:
            client._make_request("GET", "test/")

        assert "Internal Server Error" in str(exc_info.value)


def test_context_manager():
    """Test client works as context manager."""
    with requests_mock.Mocker() as m:
        m.get("https://physionet.org/api/v1/test/", json={"status": "ok"})

        with PhysioNetClient() as client:
            assert client.session is not None
            response = client._make_request("GET", "test/")
            assert response.json() == {"status": "ok"}


def test_close_method():
    """Test close method closes session."""
    client = PhysioNetClient()
    assert client.session is not None
    client.close()
