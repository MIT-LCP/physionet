"""
Sign in to PhysioNet from scripts and the command line.

``login()`` uses the OAuth device flow (RFC 8628). It shows a short code
and a link; you open the link, sign in to PhysioNet if needed, and
approve. The package then saves a token, and later downloads use it.
No password is stored.

Tokens are saved in a ``credentials.json`` file in the user's config
folder (``~/.config/physionet`` on Linux and macOS, ``%APPDATA%\\physionet``
on Windows, or ``PHYSIONET_CONFIG_DIR``), readable only by the user. A
token is only ever sent to the PhysioNet site that issued it.
"""

import json
import os
import sys
import time
import uuid
import webbrowser
from pathlib import Path
from typing import Dict, Optional, Tuple
from urllib.parse import urlsplit

import requests
from requests.auth import AuthBase

DEFAULT_BASE_URL = "https://physionet.org"
CLIENT_ID = "physionet-python-client"
SCOPE = "data:download profile:read"
TOKEN_ENV = "PHYSIONET_TOKEN"
CONFIG_DIR_ENV = "PHYSIONET_CONFIG_DIR"
USER_AGENT = "PhysioNet-Python-Client/1.0"
DEVICE_CODE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
# Refresh a token when it has less than this many seconds left.
EXPIRY_MARGIN = 60
REQUEST_TIMEOUT = 30


class LoginError(Exception):
    """Signing in to PhysioNet failed."""


def origin(url: str) -> Tuple[str, Optional[str], Optional[int]]:
    """Return the scheme, host, and effective port of a URL."""
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    default_ports = {"http": 80, "https": 443}
    return scheme, parsed.hostname, parsed.port or default_ports.get(scheme)


def _site_key(base_url: str) -> str:
    scheme, host, port = origin(base_url)
    default_port = {"http": 80, "https": 443}.get(scheme)
    return f"{scheme}://{host}" if port == default_port else f"{scheme}://{host}:{port}"


def allows_tokens(base_url: str) -> bool:
    """Tokens are only sent over HTTPS, or over HTTP to this computer for testing."""
    scheme, host, _ = origin(base_url)
    return scheme == "https" or (scheme == "http" and host in ("localhost", "127.0.0.1", "::1"))


# Saved sign-ins


def credentials_path() -> Path:
    """Return the file where sign-ins are saved."""
    override = os.getenv(CONFIG_DIR_ENV)
    if override:
        folder = Path(override)
    elif sys.platform == "win32":
        folder = Path(os.getenv("APPDATA") or Path.home() / "AppData" / "Roaming") / "physionet"
    else:
        folder = Path(os.getenv("XDG_CONFIG_HOME") or Path.home() / ".config") / "physionet"
    return folder / "credentials.json"


def _read_all() -> Dict[str, dict]:
    try:
        data = json.loads(credentials_path().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_all(data: Dict[str, dict]) -> None:
    path = credentials_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    partial = path.with_name(f".{path.name}.{uuid.uuid4().hex}.part")
    # Create the file readable only by this user before writing the tokens.
    fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle, indent=2)
        os.replace(partial, path)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


def load_saved_login(base_url: str = DEFAULT_BASE_URL) -> Optional[dict]:
    """Return the saved sign-in for a PhysioNet site, or None."""
    record = _read_all().get(_site_key(base_url))
    if isinstance(record, dict) and record.get("access_token"):
        return record
    return None


def _save_login(base_url: str, record: dict) -> None:
    data = _read_all()
    data[_site_key(base_url)] = record
    _write_all(data)


def _forget_login(base_url: str) -> Optional[dict]:
    data = _read_all()
    record = data.pop(_site_key(base_url), None)
    if record is not None:
        _write_all(data)
    return record


def _is_expired(record: dict) -> bool:
    return record.get("expires_at", 0) - EXPIRY_MARGIN <= time.time()


def _record_from_token(token: dict, previous: Optional[dict] = None) -> dict:
    previous = previous or {}
    return {
        "access_token": token["access_token"],
        # Keep the old refresh token if the server did not send a new one.
        "refresh_token": token.get("refresh_token") or previous.get("refresh_token"),
        "expires_at": time.time() + int(token.get("expires_in", 3600)),
        "scope": token.get("scope", previous.get("scope", "")),
        "username": previous.get("username"),
    }


# Talking to the server


def _post(base_url: str, path: str, data: dict) -> requests.Response:
    return requests.post(
        f"{base_url.rstrip('/')}{path}",
        data=data,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=REQUEST_TIMEOUT,
    )


def _error(response: requests.Response) -> Tuple[str, str]:
    """Return the OAuth error code and a description from a failed response."""
    try:
        body = response.json()
    except ValueError:
        return "", f"HTTP {response.status_code}"
    if not isinstance(body, dict):
        return "", f"HTTP {response.status_code}"
    code = str(body.get("error", ""))
    return code, str(body.get("error_description") or code or f"HTTP {response.status_code}")


def _fetch_username(base_url: str, access_token: str) -> Optional[str]:
    try:
        response = requests.get(
            f"{base_url.rstrip('/')}/oauth/userinfo",
            headers={"Authorization": f"Bearer {access_token}", "User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT,
        )
        if response.ok:
            return response.json().get("username")
    except (requests.RequestException, ValueError):
        pass
    return None


def login(base_url: str = DEFAULT_BASE_URL, open_browser: bool = True) -> Optional[str]:
    """
    Sign in to PhysioNet and save the sign-in for later downloads.

    Prints a link and a code. Open the link, sign in to PhysioNet if
    needed, enter the code and approve. This waits until you do, then
    saves a token in the credentials file (see credentials_path()).

    Args:
        base_url: PhysioNet site to sign in to
        open_browser: Also try to open the link in a web browser

    Returns:
        The PhysioNet username, if the site reports it

    Raises:
        LoginError: If the sign-in is denied, expires, or fails
    """
    base_url = base_url.rstrip("/")
    if not allows_tokens(base_url):
        raise LoginError(f"Signing in needs an HTTPS address, not {base_url}")

    try:
        response = _post(base_url, "/oauth/device-authorization/", {"client_id": CLIENT_ID, "scope": SCOPE})
    except requests.RequestException as exc:
        raise LoginError(f"Could not reach {base_url}: {exc}") from None
    if response.status_code != 200:
        raise LoginError(f"{base_url} did not start the sign-in: {_error(response)[1]}")
    start = response.json()

    print(f"To sign in, open {start['verification_uri']}")
    print(f"and enter the code: {start['user_code']}")
    if open_browser:
        try:
            webbrowser.open(start["verification_uri"])
        except Exception:
            pass
    print("Waiting for you to approve the sign-in (press Ctrl+C to cancel)...", flush=True)

    interval = int(start.get("interval") or 5)
    deadline = time.monotonic() + int(start.get("expires_in") or 900)
    expired_message = "The code expired before the sign-in was approved. Run the sign-in again."
    while True:
        time.sleep(interval)
        if time.monotonic() > deadline:
            raise LoginError(expired_message)
        try:
            response = _post(base_url, "/oauth/token/", {
                "grant_type": DEVICE_CODE_GRANT,
                "device_code": start["device_code"],
                "client_id": CLIENT_ID,
            })
        except (requests.ConnectionError, requests.Timeout):
            continue
        if response.status_code == 200:
            break
        code, description = _error(response)
        if code == "authorization_pending":
            continue
        if code == "slow_down":
            interval += 5
            continue
        if code == "access_denied":
            raise LoginError("The sign-in was denied.")
        if code == "expired_token":
            raise LoginError(expired_message)
        raise LoginError(f"Sign-in failed: {description}")

    record = _record_from_token(response.json())
    record["username"] = _fetch_username(base_url, record["access_token"])
    _save_login(base_url, record)
    return record["username"]


def logout(base_url: str = DEFAULT_BASE_URL) -> bool:
    """
    Sign out: revoke the saved sign-in for a PhysioNet site and delete it.

    Returns:
        True if there was a saved sign-in
    """
    base_url = base_url.rstrip("/")
    record = _forget_login(base_url)
    if record is None:
        return False
    # Revoking the refresh token also revokes its access token.
    for token_type in ("refresh_token", "access_token"):
        token = record.get(token_type)
        if not token:
            continue
        try:
            _post(base_url, "/oauth/revoke-token/", {
                "token": token, "token_type_hint": token_type, "client_id": CLIENT_ID,
            })
        except requests.RequestException:
            pass
    return True


def _refresh(base_url: str, record: dict) -> Optional[dict]:
    """
    Get a new access token with the refresh token, save it, and return the
    updated sign-in. Returns None if the sign-in can no longer be used.
    """
    # Another process may have refreshed the token already.
    latest = load_saved_login(base_url)
    if latest and not _is_expired(latest):
        return latest
    record = latest or record
    if not record.get("refresh_token"):
        return None
    try:
        response = _post(base_url, "/oauth/token/", {
            "grant_type": "refresh_token",
            "refresh_token": record["refresh_token"],
            "client_id": CLIENT_ID,
        })
    except requests.RequestException:
        return None
    if response.status_code == 200:
        new_record = _record_from_token(response.json(), previous=record)
        _save_login(base_url, new_record)
        return new_record

    latest = load_saved_login(base_url)
    if latest and not _is_expired(latest):
        return latest
    if _error(response)[0] == "invalid_grant":
        # The sign-in was revoked or has expired.
        _forget_login(base_url)
    return None


class OriginBearerAuth(AuthBase):
    """Send a bearer token, but only to one PhysioNet site."""

    def __init__(self, base_url: str, token: str):
        self.origin = origin(base_url)
        self.token = token

    def __call__(self, request):
        if origin(request.url) == self.origin:
            request.headers["Authorization"] = f"Bearer {self.token}"
        return request


class SavedLoginAuth(AuthBase):
    """
    Send the saved sign-in's token to one PhysioNet site, and refresh it
    when it expires.

    If the sign-in can no longer be used, requests continue without it,
    so open data still downloads, and a message says to sign in again.
    """

    def __init__(self, base_url: str, record: dict):
        self.base_url = base_url.rstrip("/")
        self.origin = origin(base_url)
        self.record: Optional[dict] = record
        self._warned = False

    def __call__(self, request):
        if origin(request.url) != self.origin or self.record is None:
            return request
        if _is_expired(self.record):
            self.record = _refresh(self.base_url, self.record)
            if self.record is None:
                if not self._warned:
                    print(
                        "Your PhysioNet sign-in has expired or was signed out. "
                        "Run `physionet login` to sign in again.",
                        file=sys.stderr,
                    )
                    self._warned = True
                return request
        request.headers["Authorization"] = f"Bearer {self.record['access_token']}"
        return request


def resolve_credentials(
    username: Optional[str] = None, password: Optional[str] = None, token: Optional[str] = None
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Choose which credentials to use, returning (username, password, token).

    In order: a token argument, a username and password given as
    arguments, the PHYSIONET_TOKEN environment variable, then the
    PHYSIONET_USERNAME and PHYSIONET_PASSWORD environment variables. If
    none are set, PhysioNetClient uses the saved sign-in, if any.
    """
    if token:
        return None, None, token
    if username and password:
        return username, password, None
    env_token = os.getenv(TOKEN_ENV)
    if env_token and not username:
        return None, None, env_token
    return username or os.getenv("PHYSIONET_USERNAME"), password or os.getenv("PHYSIONET_PASSWORD"), None
