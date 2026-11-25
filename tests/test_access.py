"""The CLI displays the server's access decision without reimplementing it."""

import json
import sys
from dataclasses import asdict
from unittest.mock import patch

import requests
import requests_mock

from physionet.api import PhysioNetClient
from physionet.cli import main


ACCESS = {
    "slug": "mimiciv", "version": "3.1", "access_policy": "Credentialed",
    "authenticated": True, "can_download": False, "status": "action_required",
    "project_url": "https://physionet.org/content/mimiciv/3.1/", "reason": None,
    "requirements": [
        {"code": "sign_in", "label": "Sign in to PhysioNet", "status": "complete",
         "url": "https://physionet.org/login/"},
        {"code": "credentialing", "label": "PhysioNet credentialing", "status": "complete",
         "url": "https://physionet.org/settings/credentialing/"},
        {"code": "training", "label": "CITI training", "status": "pending",
         "url": "https://physionet.org/settings/training/"},
        {"code": "dua", "label": "Data use agreement", "status": "required",
         "url": "https://physionet.org/content/mimiciv/3.1/"},
    ],
}


def run_cli(*args):
    with patch.object(sys, "argv", ["physionet", *args]):
        return main()


def test_python_api_gets_server_status_and_resolves_latest():
    with requests_mock.Mocker() as mock:
        mock.get("https://physionet.org/api/v1/projects/mimiciv/versions/latest/access/", json=ACCESS)
        with PhysioNetClient(username="researcher", password="secret") as client:
            status = client.projects.get_access_status("mimiciv")
    assert status.version == "3.1"
    assert status.requirements[2].status == "pending"
    assert asdict(status) == ACCESS
    assert mock.last_request.headers["Authorization"].startswith("Basic ")


def test_access_cli_prints_steps_and_json(capsys):
    with requests_mock.Mocker() as mock:
        mock.get("https://physionet.org/api/v1/projects/mimiciv/versions/3.1/access/", json=ACCESS)
        assert run_cli("access", "mimiciv", "--version", "3.1",
                       "--username", "researcher", "--password", "secret") == 0
        human = capsys.readouterr()
        assert run_cli("access", "mimiciv", "--version", "3.1", "--json",
                       "--username", "researcher", "--password", "secret") == 0
        machine = capsys.readouterr()
    assert "CITI training: pending" in human.out
    assert "Data use agreement: required" in human.out
    assert "PhysioNet credentialing: complete" not in human.out
    assert json.loads(machine.out) == ACCESS
    assert machine.err == ""


def test_access_cli_anonymous_check_does_not_claim_to_know_account_access(monkeypatch, capsys):
    monkeypatch.delenv("PHYSIONET_USERNAME", raising=False)
    monkeypatch.delenv("PHYSIONET_PASSWORD", raising=False)
    anonymous = {
        **ACCESS, "authenticated": False, "status": "sign_in_required",
        "requirements": [
            {**item, "status": "required" if item["code"] == "sign_in" else "unknown"}
            for item in ACCESS["requirements"]
        ],
    }
    with requests_mock.Mocker() as mock:
        mock.get("https://physionet.org/api/v1/projects/mimiciv/versions/latest/access/", json=anonymous)
        assert run_cli("access", "mimiciv") == 0
        human = capsys.readouterr()
        assert run_cli("access", "mimiciv", "--json") == 0
    output = capsys.readouterr()
    assert "anonymous; your account access was not checked" in human.out
    assert "physionet access mimiciv --username YOUR_USERNAME" in human.out
    assert "not available to you yet" not in human.out
    assert "CITI training: unknown" not in human.out
    assert json.loads(output.out)["authenticated"] is False
    assert json.loads(output.out)["can_download"] is False
    assert "Authorization" not in mock.last_request.headers


def test_access_cli_uses_environment_and_custom_server(monkeypatch, capsys):
    monkeypatch.setenv("PHYSIONET_USERNAME", "researcher")
    monkeypatch.setenv("PHYSIONET_PASSWORD", "secret")
    monkeypatch.setenv("PHYSIONET_BASE_URL", "https://test.example.org")
    with requests_mock.Mocker() as mock:
        mock.get("https://test.example.org/api/v1/projects/mimiciv/versions/latest/access/", json=ACCESS)
        assert run_cli("access", "mimiciv", "--json") == 0
    assert mock.last_request.headers["Authorization"].startswith("Basic ")
    assert json.loads(capsys.readouterr().out)["status"] == "action_required"


def test_access_cli_old_server_is_clear_and_fails(capsys):
    with requests_mock.Mocker() as mock:
        mock.get("https://physionet.org/api/v1/projects/mimiciv/versions/latest/access/",
                 status_code=404, text="<!DOCTYPE html><html>large error page</html>",
                 headers={"Content-Type": "text/html"})
        mock.get("https://physionet.org/api/v1/projects/mimiciv/versions/", json=[
            {"slug": "mimiciv", "title": "MIMIC-IV", "version": "3.1",
             "abstract": "", "citation": ""},
        ])
        assert run_cli("access", "mimiciv", "--json") == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "does not appear to provide the access-status API" in output.err
    assert "large error page" not in output.err
    assert len(output.err) < 250


def test_access_cli_distinguishes_unknown_project_and_version(capsys):
    with requests_mock.Mocker() as mock:
        mock.get("https://physionet.org/api/v1/projects/unknown/versions/latest/access/",
                 status_code=404, text="<html>Not found</html>")
        mock.get("https://physionet.org/api/v1/projects/unknown/versions/",
                 status_code=404, text="<html>Not found</html>")
        assert run_cli("access", "unknown") == 1
    assert "Project not found: unknown" in capsys.readouterr().err

    with requests_mock.Mocker() as mock:
        mock.get("https://physionet.org/api/v1/projects/mimiciv/versions/9.9/access/",
                 status_code=404, text="<html>Not found</html>")
        mock.get("https://physionet.org/api/v1/projects/mimiciv/versions/", json=[
            {"slug": "mimiciv", "title": "MIMIC-IV", "version": "3.1",
             "abstract": "", "citation": ""},
        ])
        assert run_cli("access", "mimiciv", "--version", "9.9") == 1
    assert "Version 9.9 not found for mimiciv" in capsys.readouterr().err


def test_access_cli_empty_versions_means_project_not_found(capsys):
    # PhysioNet returns 200 with [] for a slug that has no published versions.
    with requests_mock.Mocker() as mock:
        mock.get("https://physionet.org/api/v1/projects/mimic-iii/versions/latest/access/",
                 status_code=404, text="<html>Not found</html>")
        mock.get("https://physionet.org/api/v1/projects/mimic-iii/versions/", json=[])
        assert run_cli("access", "mimic-iii") == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "Project not found: mimic-iii.\n"


def test_access_cli_missing_unattended_password_fails_before_request(monkeypatch, capsys):
    monkeypatch.setenv("PHYSIONET_USERNAME", "researcher")
    monkeypatch.delenv("PHYSIONET_PASSWORD", raising=False)
    assert run_cli("access", "mimiciv") == 1
    assert "PHYSIONET_PASSWORD" in capsys.readouterr().err


ANONYMOUS = {**ACCESS, "authenticated": False, "status": "sign_in_required"}
ACCESS_URL = "https://physionet.org/api/v1/projects/mimiciv/versions/latest/access/"
VERSIONS_URL = "https://physionet.org/api/v1/projects/mimiciv/versions/"
VERSIONS = [{"slug": "mimiciv", "title": "MIMIC-IV", "version": "3.1", "abstract": "", "citation": ""}]


def test_access_cli_uses_the_saved_sign_in(capsys):
    from physionet import auth
    auth._save_login("https://physionet.org", {
        "access_token": "saved-access", "refresh_token": "saved-refresh",
        "expires_at": 9999999999, "username": "researcher",
    })
    with requests_mock.Mocker() as mock:
        mock.get(ACCESS_URL, json=ACCESS)
        assert run_cli("access", "mimiciv") == 0
    assert mock.last_request.headers["Authorization"] == "Bearer saved-access"
    assert "CITI training: pending" in capsys.readouterr().out


def test_access_cli_uses_personal_token_for_its_owner(monkeypatch, capsys):
    monkeypatch.setenv("PHYSIONET_TOKEN", "pnpat_test-token")
    with requests_mock.Mocker() as mock:
        mock.get(ACCESS_URL, json=ACCESS)
        assert run_cli("access", "mimiciv", "--json") == 0
    assert mock.last_request.headers["Authorization"] == "Bearer pnpat_test-token"
    assert json.loads(capsys.readouterr().out)["authenticated"] is True


def test_access_cli_rejects_anonymous_answer_to_a_token(monkeypatch, capsys):
    monkeypatch.setenv("PHYSIONET_TOKEN", "pnpat_test-token")
    with requests_mock.Mocker() as mock:
        mock.get(ACCESS_URL, json=ANONYMOUS)
        assert run_cli("access", "mimiciv", "--json") == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "answered anonymously for your sign-in or token" in output.err


def test_access_cli_rejected_token_has_a_clear_error(monkeypatch, capsys):
    monkeypatch.setenv("PHYSIONET_TOKEN", "pnpat_bad-token")
    with requests_mock.Mocker() as mock:
        mock.get(ACCESS_URL, status_code=401, json={"detail": "Invalid or expired token."})
        assert run_cli("access", "mimiciv") == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "server rejected your sign-in or token" in output.err


def test_access_cli_saved_username_does_not_prompt(capsys):
    from physionet import auth
    auth._save_login("https://physionet.org", {
        "access_token": "saved-access", "refresh_token": "saved-refresh",
        "expires_at": 9999999999, "username": "researcher",
    })
    with patch("getpass.getpass", side_effect=AssertionError("unexpected password prompt")):
        with requests_mock.Mocker() as mock:
            mock.get(ACCESS_URL, json=ACCESS)
            assert run_cli("access", "mimiciv", "--username", "researcher") == 0
    assert mock.last_request.headers["Authorization"] == "Bearer saved-access"
    assert "password" not in capsys.readouterr().err


def test_access_cli_token_cannot_select_a_username(monkeypatch, capsys):
    monkeypatch.setenv("PHYSIONET_TOKEN", "pnpat_test-token")
    with requests_mock.Mocker() as mock:
        assert run_cli("access", "mimiciv", "--username", "another-account") == 1
    assert mock.request_history == []
    assert "PHYSIONET_PASSWORD" in capsys.readouterr().err


def test_access_cli_anonymous_flag_ignores_saved_and_environment_tokens(monkeypatch, capsys):
    from physionet import auth
    auth._save_login("https://physionet.org", {
        "access_token": "saved-access", "refresh_token": "saved-refresh",
        "expires_at": 9999999999, "username": "researcher",
    })
    monkeypatch.setenv("PHYSIONET_TOKEN", "pnpat_test-token")
    with requests_mock.Mocker() as mock:
        mock.get(ACCESS_URL, json=ANONYMOUS)
        assert run_cli("access", "mimiciv", "--anonymous") == 0
    assert "Authorization" not in mock.last_request.headers
    assert "anonymous" in capsys.readouterr().out


def test_access_cli_empty_password_fails_before_request(monkeypatch, capsys):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("getpass.getpass", lambda prompt: "")
    with requests_mock.Mocker() as mock:
        assert run_cli("access", "mimiciv", "--username", "researcher") == 1
    assert mock.request_history == []
    assert "No password entered" in capsys.readouterr().err


def test_access_cli_ctrl_c_at_password_prompt_exits_130(monkeypatch):
    def interrupt(prompt):
        raise KeyboardInterrupt

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("getpass.getpass", interrupt)
    assert run_cli("access", "mimiciv", "--username", "researcher") == 130


def test_access_cli_json_404_from_updated_server_is_not_called_missing_api(capsys):
    with requests_mock.Mocker() as mock:
        mock.get(ACCESS_URL, status_code=404, json={"detail": "Not found."})
        mock.get(VERSIONS_URL, json=VERSIONS)
        assert run_cli("access", "mimiciv") == 1
    err = capsys.readouterr().err
    assert "access-status API" not in err
    assert "Access status not found for mimiciv: Not found." in err


def test_access_cli_reports_why_the_project_check_failed(capsys):
    with requests_mock.Mocker() as mock:
        mock.get(ACCESS_URL, status_code=404, text="<html>Not found</html>")
        mock.get(VERSIONS_URL, status_code=503, text="Service Unavailable")
        assert run_cli("access", "mimiciv") == 1
    err = capsys.readouterr().err
    assert "HTTP 503" in err
    assert "access-status API" not in err


def test_access_cli_reports_forbidden_and_network_errors(capsys):
    with requests_mock.Mocker() as mock:
        mock.get(ACCESS_URL, status_code=403, json={"detail": "Forbidden by policy"})
        assert run_cli("access", "mimiciv") == 1
    assert capsys.readouterr().err == "Access status unavailable: Forbidden by policy\n"

    with requests_mock.Mocker() as mock:
        mock.get(ACCESS_URL, exc=requests.ConnectionError("connection refused"))
        assert run_cli("access", "mimiciv") == 1
    assert "connection refused" in capsys.readouterr().err


def test_unexpected_access_payload_raises_api_error(capsys):
    import pytest
    from physionet.api.exceptions import PhysioNetAPIError

    partial = {key: value for key, value in ACCESS.items() if key != "project_url"}
    cases = [
        {"json": partial},
        {"text": "<html>Sign in</html>", "headers": {"Content-Type": "text/html"}},
        {"json": ["not", "an", "object"]},
    ]
    for case in cases:
        with requests_mock.Mocker() as mock:
            mock.get(ACCESS_URL, **case)
            with PhysioNetClient(use_saved_login=False) as client:
                with pytest.raises(PhysioNetAPIError, match="Unexpected response"):
                    client.projects.get_access_status("mimiciv")
            assert run_cli("access", "mimiciv") == 1
        err = capsys.readouterr().err
        assert err.startswith("Access status unavailable: Unexpected response")
        assert "KeyError" not in err and "project_url" not in err


def test_access_cli_ctrl_c_during_project_check_exits_130(capsys):
    with requests_mock.Mocker() as mock:
        mock.get(ACCESS_URL, status_code=404, text="<html>Not found</html>")
        mock.get(VERSIONS_URL, exc=KeyboardInterrupt)
        assert run_cli("access", "mimiciv") == 130
    assert "Traceback" not in capsys.readouterr().err


def test_access_cli_wrong_password_explains_the_401(capsys):
    with requests_mock.Mocker() as mock:
        mock.get(ACCESS_URL, status_code=401, json={"detail": "Invalid username/password."})
        assert run_cli("access", "mimiciv", "--username", "researcher", "--password", "wrong") == 1
    err = capsys.readouterr().err
    assert "did not accept your username and password" in err
    assert "HTTP 401" not in err


def test_access_cli_ctrl_d_at_password_prompt_is_no_password(monkeypatch, capsys):
    def end_of_input(prompt):
        raise EOFError

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("getpass.getpass", end_of_input)
    with requests_mock.Mocker() as mock:
        assert run_cli("access", "mimiciv", "--username", "researcher") == 1
        assert run_cli("download", "mimiciv", "--username", "researcher") == 1
    assert mock.request_history == []
    err = capsys.readouterr().err
    assert "No password entered" in err
    assert "Traceback" not in err


def test_access_reason_is_kept_and_explains_unavailable_files(capsys):
    unavailable = {**ACCESS, "status": "unavailable", "reason": "embargoed"}
    url = "https://physionet.org/api/v1/projects/mimiciv/versions/3.1/access/"
    with requests_mock.Mocker() as mock:
        mock.get(url, json=unavailable)
        assert run_cli("access", "mimiciv", "--version", "3.1",
                       "--username", "researcher", "--password", "secret") == 0
        human = capsys.readouterr().out
        assert run_cli("access", "mimiciv", "--version", "3.1", "--json",
                       "--username", "researcher", "--password", "secret") == 0
        machine = json.loads(capsys.readouterr().out)
    assert "unavailable; the files are under embargo" in human
    assert machine["reason"] == "embargoed"

    without_reason = {key: value for key, value in ACCESS.items() if key != "reason"}
    with requests_mock.Mocker() as mock:
        mock.get(ACCESS_URL, json=without_reason)
        with PhysioNetClient(use_saved_login=False) as client:
            assert client.projects.get_access_status("mimiciv").reason is None


def test_access_cli_odd_versions_response_is_reported_not_raised(capsys):
    odd_responses = [
        {"json": {"results": []}},
        {"json": [{"slug": "mimiciv"}]},
    ]
    for case in odd_responses:
        with requests_mock.Mocker() as mock:
            mock.get(ACCESS_URL, status_code=404, text="<html>Not found</html>")
            mock.get(VERSIONS_URL, **case)
            assert run_cli("access", "mimiciv") == 1
        err = capsys.readouterr().err
        assert "the project could not be checked: Unexpected response" in err
        assert "Traceback" not in err


def test_access_cli_password_without_username_fails_before_request(monkeypatch, capsys):
    monkeypatch.delenv("PHYSIONET_USERNAME", raising=False)
    with requests_mock.Mocker() as mock:
        assert run_cli("access", "mimiciv", "--password", "secret") == 1
    assert mock.request_history == []
    assert "--password needs --username" in capsys.readouterr().err


def test_password_from_environment_alone_still_allows_anonymous_check(monkeypatch, capsys):
    monkeypatch.delenv("PHYSIONET_USERNAME", raising=False)
    monkeypatch.setenv("PHYSIONET_PASSWORD", "secret")
    with requests_mock.Mocker() as mock:
        mock.get(ACCESS_URL, json=ANONYMOUS)
        assert run_cli("access", "mimiciv") == 0
    assert "Authorization" not in mock.last_request.headers


def test_auth_commands_use_physionet_base_url(monkeypatch, capsys):
    monkeypatch.setenv("PHYSIONET_BASE_URL", "https://staging.example.org")
    assert run_cli("whoami") == 0
    assert "staging.example.org" in capsys.readouterr().out
    monkeypatch.setenv("PHYSIONET_BASE_URL", "")
    assert run_cli("whoami") == 0
    assert "https://physionet.org" in capsys.readouterr().out


def test_access_cli_fails_when_credentials_are_answered_anonymously(capsys):
    # e.g. the credentials were dropped on the way, so the server never saw them
    with requests_mock.Mocker() as mock:
        mock.get(ACCESS_URL, json=ANONYMOUS)
        assert run_cli("access", "mimiciv", "--username", "researcher", "--password", "secret") == 1
        human = capsys.readouterr()
        assert run_cli("access", "mimiciv", "--json", "--username", "researcher", "--password", "secret") == 1
        machine = capsys.readouterr()
    for output in (human, machine):
        assert output.out == ""
        assert "did not accept your username and password" in output.err


def test_access_cli_account_hint_keeps_version_and_server(capsys):
    url = "http://localhost:8000/api/v1/projects/mimiciv/versions/2.2/access/"
    with requests_mock.Mocker() as mock:
        mock.get(url, json=ANONYMOUS)
        assert run_cli("access", "mimiciv", "--version", "2.2", "--base-url", "http://localhost:8000") == 0
    hint = ("To check your account: physionet access mimiciv --version 2.2 "
            "--base-url http://localhost:8000 --username YOUR_USERNAME")
    assert hint in capsys.readouterr().out


def test_access_cli_anonymous_unavailable_does_not_blame_signing_in(capsys):
    unexplained = {**ANONYMOUS, "status": "unavailable", "reason": "other"}
    with requests_mock.Mocker() as mock:
        mock.get(ACCESS_URL, json=unexplained)
        assert run_cli("access", "mimiciv") == 0
    out = capsys.readouterr().out
    assert "unavailable; check the project page for details" in out
    assert "anonymous request" not in out


def test_physionet_base_url_from_the_environment_does_not_leak_into_tests():
    import os

    assert "PHYSIONET_BASE_URL" not in os.environ
