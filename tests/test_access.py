"""The CLI displays the server's access decision without reimplementing it."""

import json
import sys
from dataclasses import asdict
from unittest.mock import patch

import requests_mock

from physionet.api import PhysioNetClient
from physionet.cli import main


ACCESS = {
    "slug": "mimiciv", "version": "3.1", "access_policy": "Credentialed",
    "authenticated": True, "can_download": False, "status": "action_required",
    "project_url": "https://physionet.org/content/mimiciv/3.1/",
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
    assert "does not yet provide the access-status API" in output.err
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
