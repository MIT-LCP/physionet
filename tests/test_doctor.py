"""Tests for physionet doctor."""

import json
import os
import stat
import sys
from unittest.mock import patch

import pytest
import requests
import requests_mock

import physionet
from physionet import auth
from physionet.cli import main
from physionet.doctor import FAIL, INFO, OK, PYPI_URL, WARN, fix_permissions, run_checks, summary
from physionet.download import S3_BASE_URL

BASE = "https://physionet.org"
PROBE = f"{BASE}/api/v1/projects/physionet-doctor-check/versions/latest/access/"
USERINFO = f"{BASE}/oauth/userinfo"
VERSIONS = [{"slug": "mimiciv", "title": "MIMIC-IV", "version": v, "abstract": "", "citation": ""}
            for v in ("3.0", "3.1")]
ACCESS = {
    "slug": "mimiciv", "version": "3.1", "access_policy": "Credentialed", "authenticated": True,
    "can_download": False, "status": "action_required", "reason": None,
    "project_url": f"{BASE}/content/mimiciv/3.1/",
    "requirements": [
        {"code": "credentialing", "label": "PhysioNet credentialing", "status": "complete", "url": f"{BASE}/c/"},
        {"code": "training", "label": "CITI training", "status": "pending", "url": f"{BASE}/settings/training/"},
    ],
}


@pytest.fixture(autouse=True)
def no_aws_credentials():
    with patch("physionet.doctor._aws_credentials_found", return_value=False):
        yield


@pytest.fixture
def server():
    with requests_mock.Mocker() as mock:
        mock.get(PYPI_URL, json={"info": {"version": physionet.__version__}})
        mock.get(PROBE, status_code=404, json={"detail": "Not found."})
        mock.head(f"{S3_BASE_URL}/", status_code=403)  # any answer means it is reachable
        yield mock


def by_message(checks, text):
    found = [check for check in checks if text in check.message]
    assert found, f"no check mentions {text!r}: {[c.message for c in checks]}"
    return found[0]


def run(tmp_path, **kwargs):
    return run_checks(cache_dir=str(tmp_path / "cache"), **kwargs)


def test_a_healthy_signed_out_setup_has_no_problems(server, tmp_path):
    checks = run(tmp_path)
    assert summary(checks) == {"problems": 0, "warnings": 0}
    assert by_message(checks, "is the latest release").status == OK
    assert by_message(checks, "physionet:// paths are registered").status == OK
    assert by_message(checks, "reachable").section == "Server"
    assert by_message(checks, "Access status API available").status == OK
    assert by_message(checks, "Not signed in").status == INFO
    assert by_message(checks, "AWS open data mirror reachable").status == OK
    assert by_message(checks, "not created yet").status == OK


def test_an_old_package_and_an_unreachable_pypi(server, tmp_path):
    server.get(PYPI_URL, json={"info": {"version": "99.0.0"}})
    outdated = by_message(run(tmp_path), "99.0.0 is available")
    assert outdated.status == WARN and outdated.fix == "pip install -U physionet"

    server.get(PYPI_URL, exc=requests.ConnectionError)
    assert by_message(run(tmp_path), "could not check PyPI").status == INFO

    server.reset_mock()
    skipped = run(tmp_path, check_pypi=False)
    assert not any(request.url.startswith(PYPI_URL) for request in server.request_history)
    assert by_message(skipped, f"physionet {physionet.__version__}").status == INFO


def test_an_unreachable_server_skips_the_checks_that_need_it(server, tmp_path):
    server.get(PROBE, exc=requests.ConnectionError)
    checks = run(tmp_path, slug="mimiciv", username="researcher", password="secret")
    assert by_message(checks, "cannot be reached").status == FAIL
    assert by_message(checks, "not checked because the server cannot be reached").section == "Sign-in"
    assert [c.message for c in checks if c.section == "Dataset mimiciv"] == [
        "Not checked because the server cannot be reached"]
    assert summary(checks)["problems"] == 1


def test_a_server_without_the_access_api(server, tmp_path):
    server.get(PROBE, status_code=404, text="<html>Not found</html>")
    assert by_message(run(tmp_path), "no access status API").status == WARN


def test_username_and_password(server, tmp_path):
    def answer(request, context):
        good = request.headers.get("Authorization") == requests.auth._basic_auth_str("researcher", "right")
        context.status_code = 404 if good else 401
        return {"detail": "Not found." if good else "Invalid username/password."}

    server.get(PROBE, json=answer)
    assert by_message(run(tmp_path, username="researcher", password="right"), "accepted").status == OK
    wrong = by_message(run(tmp_path, username="researcher", password="wrong"), "did not accept the password")
    assert wrong.status == FAIL
    missing = by_message(run(tmp_path, username="researcher"), "no password")
    assert missing.status == WARN


@pytest.mark.parametrize("status, expected, text", [
    (200, OK, "Signed in as researcher with the token in PHYSIONET_TOKEN"),
    (401, FAIL, "did not accept the token in PHYSIONET_TOKEN"),
    (403, INFO, "cannot show the account name"),
])
def test_token_from_the_environment(server, tmp_path, monkeypatch, status, expected, text):
    monkeypatch.setenv("PHYSIONET_TOKEN", "pnpat_secret")
    server.get(USERINFO, status_code=status, json={"username": "researcher"})
    assert by_message(run(tmp_path), text).status == expected
    userinfo = [request for request in server.request_history if request.url == USERINFO]
    assert [request.headers["Authorization"] for request in userinfo] == ["Bearer pnpat_secret"]


def test_tokens_are_not_sent_over_http(tmp_path, monkeypatch):
    # Plain HTTP is allowed only to this computer (localhost), for testing.
    monkeypatch.setenv("PHYSIONET_TOKEN", "pnpat_secret")
    server = "http://physionet.example.org"
    with requests_mock.Mocker() as mock:
        mock.get(PYPI_URL, json={"info": {"version": physionet.__version__}})
        mock.get(f"{server}/api/v1/projects/physionet-doctor-check/versions/latest/access/",
                 status_code=404, json={})
        checks = run(tmp_path, base_url=server)
    assert by_message(checks, "only sent over HTTPS").status == FAIL
    assert all("Authorization" not in request.headers for request in mock.request_history)
    assert by_message(checks, f"AWS mirror is not used with {server}").status == INFO


def saved_sign_in():
    auth._save_login(BASE, {
        "access_token": "saved-access", "refresh_token": "saved-refresh",
        "expires_at": 9999999999, "username": "researcher",
    })


def test_saved_sign_in_that_works(server, tmp_path):
    saved_sign_in()
    server.get(USERINFO, json={"username": "researcher"})
    assert by_message(run(tmp_path), "Signed in as researcher (saved sign-in)").status == OK


def test_saved_sign_in_that_was_revoked(server, tmp_path):
    saved_sign_in()
    server.get(USERINFO, status_code=401, json={"detail": "Invalid token"})
    server.post(f"{BASE}/oauth/token/", status_code=400, json={"error": "invalid_grant"})
    revoked = by_message(run(tmp_path), "no longer works")
    assert revoked.status == FAIL and revoked.fix == "physionet login"


def test_a_token_hides_the_saved_sign_in(server, tmp_path, monkeypatch):
    saved_sign_in()
    monkeypatch.setenv("PHYSIONET_TOKEN", "pnpat_secret")
    server.get(USERINFO, json={"username": "researcher"})
    warning = by_message(run(tmp_path), "used instead of the saved sign-in")
    assert warning.status == WARN and "Unset PHYSIONET_TOKEN" in warning.fix


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_a_credentials_file_others_can_read(server, tmp_path):
    saved_sign_in()
    server.get(USERINFO, json={"username": "researcher"})
    os.chmod(auth.credentials_path(), 0o644)
    loose = by_message(run(tmp_path), "can be read by other users")
    assert loose.status == FAIL and loose.fix.startswith("chmod 600 ")


def make_cached(cache, slug, access, files):
    manifest = cache / "physionet.org" / ".manifests" / slug / "1.0"
    manifest.mkdir(parents=True)
    (manifest / "ACCESS").write_text(f"{access}\n")
    paths = []
    for name in files:
        path = cache / "physionet.org" / slug / "1.0" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("data")
        os.chmod(path, 0o644)
        paths.append(path)
    return paths


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_restricted_files_others_can_read_are_found_and_fixed(server, tmp_path):
    cache = tmp_path / "cache"
    restricted = make_cached(cache, "secret", "restricted", ["a.csv", "sub/b.csv"])
    make_cached(cache, "open", "open", ["c.csv"])

    loose = by_message(run(tmp_path), "restricted file(s) in the cache can be read by other users")
    assert loose.status == FAIL and loose.message.startswith("2 ")
    assert loose.fix == "physionet doctor --fix-permissions"
    assert sorted(loose.details) == sorted(str(path) for path in restricted)
    assert "Cache:" in by_message(run(tmp_path), "writable").message

    assert sorted(fix_permissions(str(cache))) == sorted(restricted)
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in restricted)
    assert stat.S_IMODE((cache / "physionet.org" / "open" / "1.0" / "c.csv").stat().st_mode) == 0o644
    assert summary(run(tmp_path))["problems"] == 0


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="POSIX permissions, not as root")
def test_a_cache_that_is_not_writable(server, tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    os.chmod(cache, 0o500)
    try:
        assert by_message(run(tmp_path), "is not writable").status == FAIL
    finally:
        os.chmod(cache, 0o700)


def dataset(server, access=None, on_mirror=False):
    server.get(f"{BASE}/api/v1/projects/mimiciv/versions/", json=VERSIONS)
    if access is not None:
        server.get(f"{BASE}/api/v1/projects/mimiciv/versions/3.1/access/", json=access)
    server.head(f"{S3_BASE_URL}/mimiciv/3.1/SHA256SUMS.txt", status_code=200 if on_mirror else 404)


def section(checks, name="Dataset mimiciv"):
    return [check for check in checks if check.section == name]


def test_dataset_outstanding_steps_are_listed(server, tmp_path):
    dataset(server, ACCESS)
    server.get(PROBE, status_code=404, json={})
    checks = section(run(tmp_path, slug="mimiciv", version="3.1", username="researcher", password="right"))
    assert by_message(checks, "Version 3.1 exists").status == OK
    assert by_message(checks, "Access policy: Credentialed").status == INFO
    blocked = by_message(checks, "You can't download mimiciv 3.1 yet")
    assert blocked.status == FAIL
    assert blocked.details == [f"CITI training: pending - {BASE}/settings/training/"]
    assert blocked.fix == f"{BASE}/content/mimiciv/3.1/"
    assert by_message(checks, "Not on the AWS open data mirror").status == INFO


def test_dataset_open_to_everyone_on_the_mirror(server, tmp_path):
    dataset(server, {**ACCESS, "access_policy": "Open", "authenticated": False, "can_download": True,
                     "status": "ready", "requirements": []}, on_mirror=True)
    checks = section(run(tmp_path, slug="mimiciv"))
    assert by_message(checks, "Latest version 3.1").status == OK
    assert by_message(checks, "anyone, without signing in").status == OK
    assert by_message(checks, "On the AWS open data mirror").status == OK


def test_dataset_signed_out_or_with_a_token(server, tmp_path, monkeypatch):
    dataset(server, {**ACCESS, "authenticated": False, "status": "sign_in_required"})
    signed_out = by_message(section(run(tmp_path, slug="mimiciv")), "Signing in is required")
    assert signed_out.status == INFO and "physionet login" in signed_out.fix

    monkeypatch.setenv("PHYSIONET_TOKEN", "pnpat_secret")
    server.get(USERINFO, json={"username": "researcher"})
    with_token = by_message(section(run(tmp_path, slug="mimiciv")), "not with the token in PHYSIONET_TOKEN")
    assert with_token.fix == "physionet doctor mimiciv --username YOUR_USERNAME"
    # The dataset check never sends the token: the server would answer anonymously anyway.
    access_requests = [r for r in server.request_history if r.url.endswith("/mimiciv/versions/3.1/access/")]
    assert access_requests and all("Authorization" not in r.headers for r in access_requests)


def test_dataset_unknown_project_or_version(server, tmp_path):
    server.get(f"{BASE}/api/v1/projects/nothing/versions/", status_code=404, json={"detail": "Not found."})
    missing = by_message(run(tmp_path, slug="nothing"), "has no project nothing")
    assert missing.status == FAIL

    dataset(server)
    wrong = by_message(run(tmp_path, slug="mimiciv", version="9.9"), "has no version 9.9")
    assert wrong.status == FAIL and wrong.fix == "Available: 3.0, 3.1"


def run_cli(*args):
    with patch.object(sys, "argv", ["physionet", "doctor", *args]):
        return main()


def test_cli_text_json_exit_codes_and_no_secrets(server, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("PHYSIONET_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("PHYSIONET_PASSWORD", "s3cret-pass")
    monkeypatch.setenv("PHYSIONET_USERNAME", "researcher")
    server.get(PROBE, status_code=401, json={"detail": "Invalid username/password."})

    assert run_cli() == 1
    text = capsys.readouterr().out
    assert run_cli("--json") == 1
    report = json.loads(capsys.readouterr().out)

    assert text.startswith(f"physionet {physionet.__version__} · Python ")
    assert "\nSign-in\n  ✗ The server did not accept the password for researcher" in text
    assert "      Fix: Check the username and password" in text
    assert text.rstrip().endswith("1 problem.")
    assert report["problems"] == 1 and report["environment"]["physionet"] == physionet.__version__
    assert {"section", "status", "message", "fix", "details"} == set(report["checks"][0])
    for output in (text, json.dumps(report)):
        assert "s3cret-pass" not in output


def test_cli_healthy_exit_zero_and_fix_permissions(server, tmp_path, capsys, monkeypatch):
    cache = tmp_path / "cache"
    monkeypatch.setenv("PHYSIONET_CACHE", str(cache))
    restricted = make_cached(cache, "secret", "restricted", ["a.csv"]) if sys.platform != "win32" else []

    if restricted:
        assert run_cli("--no-version-check") == 1
        capsys.readouterr()
        assert run_cli("--fix-permissions", "--no-version-check") == 0
        out = capsys.readouterr().out
        assert f"Made readable only by you: {restricted[0]}" in out and "Fixed 1 file(s)." in out
    assert run_cli("--no-version-check") == 0
    assert capsys.readouterr().out.rstrip().endswith("No problems found.")
