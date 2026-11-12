from typing import Optional, Dict, Any
from urllib.parse import urljoin

import requests
from requests.auth import AuthBase, HTTPBasicAuth

from physionet.auth import OriginBearerAuth, SavedLoginAuth, allows_tokens, load_saved_login, origin as _origin
from .exceptions import (
    PhysioNetAPIError,
    BadRequestError,
    ForbiddenError,
    NotFoundError,
    RateLimitError,
)
from .endpoints import ProjectsAPI


class _OriginScopedBasicAuth(AuthBase):
    """Apply Basic authentication only to the configured PhysioNet origin."""

    def __init__(self, base_url: str, username: str, password: str):
        self.origin = _origin(base_url)
        self.basic_auth = HTTPBasicAuth(username, password)

    def __call__(self, request):
        if _origin(request.url) == self.origin:
            return self.basic_auth(request)
        return request


class PhysioNetClient:
    """Main client for interacting with PhysioNet API v1."""

    def __init__(
        self,
        base_url: str = "https://physionet.org",
        username: Optional[str] = None,
        password: Optional[str] = None,
        timeout: int = 30,
        token: Optional[str] = None,
        use_saved_login: bool = True,
    ):
        """
        Initialize PhysioNet API client.

        Credentials are only sent to base_url. Without a username and
        password or a token, the client uses the sign-in saved by
        `physionet login`, if there is one.

        Args:
            base_url: Base URL for PhysioNet (default: https://physionet.org)
            username: Optional username for authenticated requests
            password: Optional password for authenticated requests
            timeout: Request timeout in seconds
            token: Optional personal access token (starting "pnpat_") or OAuth access token
            use_saved_login: Use the saved sign-in when no other credentials are given
        """
        self.base_url = base_url.rstrip("/")
        self.api_base = f"{self.base_url}/api/v1/"
        self.timeout = timeout
        self.session = requests.Session()
        # How requests are authenticated: "password", "token", "login", or None.
        self.auth_method: Optional[str] = None

        if username and password:
            self.session.auth = _OriginScopedBasicAuth(self.base_url, username, password)
            self.auth_method = "password"
        elif token:
            if not allows_tokens(self.base_url):
                raise ValueError(f"Tokens are only sent over HTTPS, not to {self.base_url}")
            self.session.auth = OriginBearerAuth(self.base_url, token)
            self.auth_method = "token"
        elif use_saved_login and allows_tokens(self.base_url):
            record = load_saved_login(self.base_url)
            if record is not None:
                self.session.auth = SavedLoginAuth(self.base_url, record)
                self.auth_method = "login"

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
