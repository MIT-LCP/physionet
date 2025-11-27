import requests
from typing import Optional, Dict, Any
from urllib.parse import urljoin

from .exceptions import (
    PhysioNetAPIError,
    BadRequestError,
    ForbiddenError,
    NotFoundError,
    RateLimitError,
)
from .endpoints import ProjectsAPI


class PhysioNetClient:
    """Main client for interacting with PhysioNet API v1."""

    def __init__(
        self,
        base_url: str = "https://physionet.org",
        username: Optional[str] = None,
        password: Optional[str] = None,
        timeout: int = 30,
    ):
        """
        Initialize PhysioNet API client.

        Args:
            base_url: Base URL for PhysioNet (default: https://physionet.org)
            username: Optional username for authenticated requests
            password: Optional password for authenticated requests
            timeout: Request timeout in seconds
        """
        self.base_url = base_url.rstrip("/")
        self.api_base = f"{self.base_url}/api/v1/"
        self.timeout = timeout
        self.session = requests.Session()

        if username and password:
            self.session.auth = (username, password)

        self.session.headers.update({"User-Agent": "PhysioNet-Python-Client/1.0", "Accept": "application/json"})

        self.projects = ProjectsAPI(self)

    def _make_request(
        self, method: str, endpoint: str, params: Optional[Dict[str, Any]] = None, **kwargs
    ) -> requests.Response:
        """
        Make HTTP request to API.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint path
            params: Query parameters
            **kwargs: Additional arguments for requests

        Returns:
            Response object

        Raises:
            PhysioNetAPIError: On API errors
            requests.RequestException: On network errors
        """
        url = urljoin(self.api_base, endpoint)

        response = self.session.request(method=method, url=url, params=params, timeout=self.timeout, **kwargs)

        if response.status_code >= 400:
            self._handle_error(response)

        return response

    def _handle_error(self, response: requests.Response):
        """Handle API error responses."""
        try:
            error_data = response.json()
            error_msg = error_data.get("error", str(error_data))
        except Exception:
            error_msg = response.text or response.reason

        if response.status_code == 400:
            raise BadRequestError(error_msg)
        elif response.status_code == 403:
            raise ForbiddenError(error_msg)
        elif response.status_code == 404:
            raise NotFoundError(error_msg)
        elif response.status_code == 429:
            raise RateLimitError(error_msg)
        else:
            raise PhysioNetAPIError(f"HTTP {response.status_code}: {error_msg}")

    def close(self):
        """Close the session."""
        self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
