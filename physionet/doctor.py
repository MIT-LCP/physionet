"""Check the setup that downloads depend on, and explain anything that is wrong.

run_checks() returns a list of Check results covering the package, the
PhysioNet server, the sign-in, downloads and the cache, and optionally one
dataset. `physionet doctor` prints them. The checks only read: nothing is
changed unless fix_permissions() is called (`physionet doctor --fix-permissions`).

Results never include passwords or tokens, only where credentials come from
and the account name, so a report can be shared in a bug report.
"""

import os
import platform
import stat
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import requests

from physionet.auth import (
    DEFAULT_BASE_URL,
    TOKEN_ENV,
    USER_AGENT,
    allows_tokens,
    credentials_path,
    load_saved_login,
)

__all__ = ["Check", "run_checks", "fix_permissions", "OK", "INFO", "WARN", "FAIL"]

OK, INFO, WARN, FAIL = "ok", "info", "warning", "problem"
TIMEOUT = 5
PYPI_URL = "https://pypi.org/pypi/physionet/json"
# A project name no real project uses: the access API answers it with a JSON
# 404 (an older server answers with its HTML 404 page), and with 401 if a
# username and password are wrong.
_PROBE_SLUG = "physionet-doctor-check"


@dataclass
class Check:
    """One check: its section, status (ok, info, warning or problem), message, and how to fix it."""

    section: str
    status: str
    message: str
    fix: Optional[str] = None
    details: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def run_checks(
    base_url: str = DEFAULT_BASE_URL,
    slug: Optional[str] = None,
    version: Optional[str] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
    cache_dir: Optional[str] = None,
    check_pypi: bool = True,
) -> List[Check]:
    """
    Run every check and return the results, in the order they should be shown.

    Args:
        base_url: PhysioNet server to check (default: https://physionet.org)
        slug: Also check this dataset: its versions, your access, and the AWS mirror
        version: Dataset version to check (default: latest)
        username, password: Credentials to check; PHYSIONET_USERNAME and
            PHYSIONET_PASSWORD are used if these are not given
        cache_dir: Cache folder (default: see physionet.get_cache_dir)
        check_pypi: Compare the installed version with the latest on PyPI
    """
    base_url = base_url.rstrip("/")
    username = username or os.getenv("PHYSIONET_USERNAME")
    password = password or os.getenv("PHYSIONET_PASSWORD")
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    checks: List[Check] = []
    try:
        checks += _package_checks(session, check_pypi)
        server_ok = _server_checks(session, base_url, checks)
        checks += _sign_in_checks(session, base_url, username, password, server_ok)
        checks += _download_checks(session, base_url, cache_dir)
        if slug:
            checks += _dataset_checks(base_url, slug, version, username, password, server_ok)
    finally:
        session.close()
    return checks


def summary(checks: List[Check]) -> Dict[str, int]:
    """Count the problems and warnings in a list of checks."""
    return {
        "problems": sum(check.status == FAIL for check in checks),
        "warnings": sum(check.status == WARN for check in checks),
    }


def environment() -> Dict[str, str]:
    """The package, Python and operating system versions, for the report's first line."""
    import physionet

    system = platform.system()
    if system == "Darwin":
        system = f"macOS {platform.mac_ver()[0]}".strip()
    elif system:
        system = f"{system} {platform.release()}".strip()
    return {"physionet": physionet.__version__, "python": platform.python_version(), "os": system or "unknown"}


# --- Package ---


def _package_checks(session: requests.Session, check_pypi: bool) -> List[Check]:
    import physionet

    checks = []
    installed = physionet.__version__
    if check_pypi:
        try:
            response = session.get(PYPI_URL, timeout=TIMEOUT)
            response.raise_for_status()
            latest = response.json()["info"]["version"]
        except (requests.RequestException, ValueError, KeyError):
            checks.append(Check("Package", INFO, f"physionet {installed} (could not check PyPI for a newer release)"))
        else:
            if _version_key(installed) < _version_key(latest):
                checks.append(Check(
                    "Package", WARN, f"physionet {installed} is installed; {latest} is available",
                    fix="pip install -U physionet",
                ))
            else:
                checks.append(Check("Package", OK, f"physionet {installed} is the latest release"))
    else:
        checks.append(Check("Package", INFO, f"physionet {installed}"))

    try:
        import fsspec

        from physionet.fs import PhysioNetFileSystem

        registered = fsspec.get_filesystem_class("physionet") is PhysioNetFileSystem
    except (ImportError, ValueError):
        registered = False
    if registered:
        checks.append(Check("Package", OK, "physionet:// paths are registered with fsspec"))
    else:
        checks.append(Check(
            "Package", WARN, "physionet:// paths are not registered with fsspec",
            fix="Reinstall the package: pip install --force-reinstall physionet",
        ))
    return checks


def _version_key(text: str):
    parts = []
    for piece in text.split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


# --- Server ---


def _server_checks(session: requests.Session, base_url: str, checks: List[Check]) -> bool:
    """Append the server checks; return whether the server answered."""
    start = time.monotonic()
    try:
        response = session.get(f"{base_url}/api/v1/projects/{_PROBE_SLUG}/versions/latest/access/",
                               timeout=TIMEOUT, headers={"Accept": "application/json"})
    except requests.RequestException as exc:
        checks.append(Check(
            "Server", FAIL, f"{base_url} cannot be reached ({type(exc).__name__})",
            fix="Check your network connection and the server URL (--base-url or PHYSIONET_BASE_URL)",
        ))
        return False
    elapsed = int((time.monotonic() - start) * 1000)
    checks.append(Check("Server", OK, f"{base_url} reachable ({elapsed} ms)"))
    if _is_json(response):
        checks.append(Check("Server", OK, "Access status API available"))
    else:
        checks.append(Check(
            "Server", WARN, "The server has no access status API, so `physionet access` will not work",
            fix="This server runs an older version of PhysioNet",
        ))
    return True


def _is_json(response: requests.Response) -> bool:
    try:
        return isinstance(response.json(), dict)
    except ValueError:
        return False


# --- Sign-in ---


def _sign_in_checks(session, base_url, username, password, server_ok) -> List[Check]:
    checks = []
    env_token = os.getenv(TOKEN_ENV)
    saved = load_saved_login(base_url) if allows_tokens(base_url) else None

    if username and password:
        source = "PHYSIONET_USERNAME and PHYSIONET_PASSWORD" if os.getenv("PHYSIONET_PASSWORD") == password else (
            "--username and --password")
        if not server_ok:
            checks.append(Check("Sign-in", INFO, f"Using username and password for {username} ({source}); "
                                "not checked because the server cannot be reached"))
        else:
            checks.append(_check_password(session, base_url, username, password, source))
    elif username:
        checks.append(Check(
            "Sign-in", WARN, f"A username ({username}) is set but no password",
            fix="Set PHYSIONET_PASSWORD, or pass --password",
        ))
    elif env_token:
        checks.append(_check_token(session, base_url, env_token, server_ok, f"the token in {TOKEN_ENV}"))
    elif saved is not None:
        checks.append(_check_saved_login(base_url, saved, server_ok))
    else:
        checks.append(Check(
            "Sign-in", INFO, "Not signed in; only open datasets can be downloaded",
            fix="physionet login",
        ))

    if env_token and saved is not None and not (username and password):
        checks.append(Check(
            "Sign-in", WARN, f"{TOKEN_ENV} is set, so it is used instead of the saved sign-in",
            fix=f"Unset {TOKEN_ENV} to use the saved sign-in",
        ))
    checks += _credentials_file_checks()
    return checks


def _check_password(session, base_url, username, password, source) -> Check:
    try:
        response = session.get(f"{base_url}/api/v1/projects/{_PROBE_SLUG}/versions/latest/access/",
                               auth=(username, password), timeout=TIMEOUT, headers={"Accept": "application/json"})
    except requests.RequestException as exc:
        return Check("Sign-in", WARN, f"Could not check the password for {username} ({type(exc).__name__})")
    if response.status_code == 401:
        return Check("Sign-in", FAIL, f"The server did not accept the password for {username} ({source})",
                     fix="Check the username and password")
    if not _is_json(response):
        return Check("Sign-in", INFO, f"Using username and password for {username} ({source}); "
                     "this server cannot check them without downloading a file")
    return Check("Sign-in", OK, f"Username and password for {username} accepted ({source})")


def _check_token(session, base_url, token, server_ok, where) -> Check:
    if not allows_tokens(base_url):
        return Check("Sign-in", FAIL, f"{where} is set, but tokens are only sent over HTTPS, not to {base_url}",
                     fix="Use an https:// server URL")
    if not server_ok:
        return Check("Sign-in", INFO, f"Using {where}; not checked because the server cannot be reached")
    try:
        response = session.get(f"{base_url}/oauth/userinfo", timeout=TIMEOUT,
                               headers={"Authorization": f"Bearer {token}"})
    except requests.RequestException as exc:
        return Check("Sign-in", WARN, f"Could not check {where} ({type(exc).__name__})")
    if response.status_code == 401:
        return Check("Sign-in", FAIL, f"The server did not accept {where}; it may have expired or been revoked",
                     fix="Create a new token under Settings > Tokens on PhysioNet, or unset it and run physionet login")
    if response.ok:
        name = _username(response)
        account = f" as {name}" if name else ""
        return Check("Sign-in", OK, f"Signed in{account} with {where}")
    # A token without the profile:read scope cannot read the account name,
    # but may still download data.
    return Check("Sign-in", INFO, f"Using {where}; it cannot show the account name, so it was not checked")


def _check_saved_login(base_url, record, server_ok) -> Check:
    account = record.get("username") or "an unknown account"
    if not server_ok:
        return Check("Sign-in", INFO, f"Saved sign-in for {account}; not checked because the server cannot be reached")
    from physionet.api.client import PhysioNetClient

    # The client refreshes an expired sign-in, as a download would.
    try:
        with PhysioNetClient(base_url=base_url, timeout=TIMEOUT) as client:
            response = client.session.get(f"{base_url}/oauth/userinfo", timeout=TIMEOUT)
    except requests.RequestException as exc:
        return Check("Sign-in", WARN, f"Could not check the saved sign-in for {account} ({type(exc).__name__})")
    if response.status_code == 401 or load_saved_login(base_url) is None:
        return Check("Sign-in", FAIL, f"The saved sign-in for {account} no longer works",
                     fix="physionet login")
    name = _username(response) if response.ok else None
    return Check("Sign-in", OK, f"Signed in as {name or account} (saved sign-in)")


def _username(response: requests.Response) -> Optional[str]:
    try:
        return response.json().get("username")
    except (ValueError, AttributeError):
        return None


def _credentials_file_checks() -> List[Check]:
    path = credentials_path()
    if sys.platform == "win32" or not path.exists():
        return []
    try:
        mode = path.stat().st_mode
    except OSError:
        return []
    if mode & 0o077:
        return [Check("Sign-in", FAIL, f"{path} can be read by other users",
                      fix=f"chmod 600 {path}")]
    return []


# --- Downloads and cache ---


def _download_checks(session, base_url, cache_dir) -> List[Check]:
    from physionet.data import get_cache_dir
    from physionet.download import MIRRORED_BASE_URL, S3_BASE_URL

    checks = []
    if base_url.lower() == MIRRORED_BASE_URL:
        try:
            session.head(f"{S3_BASE_URL}/", timeout=TIMEOUT)
        except requests.RequestException as exc:
            checks.append(Check(
                "Downloads", WARN, f"The AWS open data mirror cannot be reached ({type(exc).__name__})",
                fix="Open datasets will download from PhysioNet instead; use --source physionet to skip the mirror",
            ))
        else:
            checks.append(Check("Downloads", OK, "AWS open data mirror reachable"))
    else:
        checks.append(Check("Downloads", INFO, f"The AWS mirror is not used with {base_url}"))

    if _aws_credentials_found():
        checks.append(Check("Downloads", OK, "AWS credentials found (for --source aws)"))
    else:
        checks.append(Check("Downloads", INFO, "No AWS credentials found; --source aws works only for open datasets"))

    cache = get_cache_dir(cache_dir)
    checks.append(_cache_check(cache))
    loose = restricted_files_others_can_read(cache_dir)
    if loose:
        checks.append(Check(
            "Downloads", FAIL, f"{len(loose)} restricted file(s) in the cache can be read by other users",
            fix="physionet doctor --fix-permissions", details=[str(path) for path in loose[:5]],
        ))
    return checks


def _aws_credentials_found() -> bool:
    try:
        import boto3

        return boto3.Session().get_credentials() is not None
    except Exception:
        return False


def _cache_check(cache: Path) -> Check:
    if not cache.exists():
        parent = next((p for p in cache.parents if p.exists()), None)
        if parent is not None and os.access(parent, os.W_OK):
            return Check("Downloads", OK, f"Cache: {cache} (not created yet)")
        return Check("Downloads", FAIL, f"Cache: {cache} cannot be created",
                     fix="Set PHYSIONET_CACHE to a folder you can write to")
    if not os.access(cache, os.W_OK):
        return Check("Downloads", FAIL, f"Cache: {cache} is not writable",
                     fix="Set PHYSIONET_CACHE to a folder you can write to")
    size = 0
    for path in cache.rglob("*"):
        try:
            if path.is_file() and not path.is_symlink():
                size += path.stat().st_size
        except OSError:
            pass
    from physionet.api.utils import format_size

    return Check("Downloads", OK, f"Cache: {cache} ({format_size(size)}, writable)")


def restricted_files_others_can_read(cache_dir: Optional[str] = None) -> List[Path]:
    """Files of restricted datasets in the cache that group or other users can read."""
    from physionet.data import ACCESS_FILE, MANIFEST_FOLDER, get_cache_dir

    if sys.platform == "win32":
        return []
    cache = get_cache_dir(cache_dir)
    loose = []
    for access in sorted(cache.glob(f"*/{MANIFEST_FOLDER}/*/*/{ACCESS_FILE}")):
        try:
            if access.read_text().strip() != "restricted":
                continue
        except OSError:
            continue
        version_folder = access.parent
        origin = version_folder.parent.parent.parent
        files = origin / version_folder.parent.name / version_folder.name
        if not files.is_dir() or files.is_symlink():
            continue
        for path in sorted(files.rglob("*")):
            try:
                if path.is_file() and not path.is_symlink() and path.stat().st_mode & 0o077:
                    loose.append(path)
            except OSError:
                pass
    return loose


def fix_permissions(cache_dir: Optional[str] = None) -> List[Path]:
    """Make restricted files in the cache readable by their owner only; return the files changed."""
    changed = []
    for path in restricted_files_others_can_read(cache_dir):
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        changed.append(path)
    return changed


# --- One dataset ---


def _dataset_checks(base_url, slug, version, username, password, server_ok) -> List[Check]:
    section = f"Dataset {slug}"
    if not server_ok:
        return [Check(section, INFO, "Not checked because the server cannot be reached")]
    from physionet.api.client import PhysioNetClient
    from physionet.api.exceptions import NotFoundError, PhysioNetAPIError
    from physionet.download import MIRRORED_BASE_URL, S3_BASE_URL

    checks = []
    # The access API checks an account only for a username and password; it
    # answers token and saved sign-in requests anonymously.
    with_password = bool(username and password)
    try:
        with PhysioNetClient(base_url=base_url, username=username if with_password else None,
                             password=password if with_password else None, use_saved_login=False,
                             timeout=TIMEOUT) as client:
            versions = [v.version for v in client.projects.list_versions(slug)]
            if not versions:
                return [Check(section, FAIL, f"PhysioNet has no project {slug}",
                              fix="Check the name with: physionet search")]
            if version is None:
                version = versions[-1]
                checks.append(Check(section, OK, f"Latest version {version}"))
            elif version not in versions:
                return [Check(section, FAIL, f"{slug} has no version {version}",
                              fix=f"Available: {', '.join(versions)}")]
            else:
                checks.append(Check(section, OK, f"Version {version} exists"))
            try:
                access = client.projects.get_access_status(slug, version)
            except (PhysioNetAPIError, requests.RequestException):
                access = None
    except NotFoundError:
        return [Check(section, FAIL, f"PhysioNet has no project {slug}", fix="Check the name with: physionet search")]
    except (PhysioNetAPIError, requests.RequestException) as exc:
        return [Check(section, WARN, f"Could not check {slug} ({exc})")]

    if access is None:
        checks.append(Check(section, INFO, "Access could not be checked: the server has no access status API"))
    else:
        checks.append(Check(section, INFO, f"Access policy: {access.access_policy}"))
        checks.append(_access_check(section, access, with_password, _other_sign_in(base_url)))

    if base_url.lower() == MIRRORED_BASE_URL:
        try:
            response = requests.head(f"{S3_BASE_URL}/{slug}/{version}/SHA256SUMS.txt", timeout=TIMEOUT)
            on_mirror = response.status_code == 200
        except requests.RequestException:
            on_mirror = None
        if on_mirror:
            checks.append(Check(section, OK, "On the AWS open data mirror"))
        elif on_mirror is False:
            checks.append(Check(section, INFO, "Not on the AWS open data mirror; downloads come from PhysioNet"))
    return checks


def _other_sign_in(base_url: str) -> Optional[str]:
    """How downloads would sign in without a password: a token, the saved sign-in, or None."""
    if os.getenv(TOKEN_ENV):
        return f"the token in {TOKEN_ENV}"
    if allows_tokens(base_url) and load_saved_login(base_url) is not None:
        return "the saved sign-in"
    return None


def _access_check(section, access, with_password, other_sign_in=None) -> Check:
    if access.can_download:
        who = "you" if access.authenticated else "anyone, without signing in"
        return Check(section, OK, f"Files can be downloaded by {who}")
    if not access.authenticated:
        if with_password:
            return Check(section, FAIL, "The server did not accept the username and password",
                         fix="Check the username and password")
        check_account = f"physionet doctor {access.slug} --username YOUR_USERNAME"
        if other_sign_in:
            return Check(
                section, INFO, f"Your account's access was not checked: the server checks it only with a "
                f"username and password, not with {other_sign_in}", fix=check_account,
            )
        return Check(section, INFO, "Signing in is required to download this dataset",
                     fix=f"physionet login, then check your access with: {check_account}")
    steps = [f"{r.label}: {r.status} - {r.url}" for r in access.requirements if r.status != "complete"]
    if access.status == "unavailable":
        return Check(section, FAIL, "Files are unavailable" + (f" ({access.reason})" if access.reason else ""),
                     fix=access.project_url)
    return Check(section, FAIL, f"You can't download {access.slug} {access.version} yet", details=steps,
                 fix=access.project_url)
