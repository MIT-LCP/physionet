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
        assert run_cli("access", "mimiciv", "--version", "3.1") == 0
        human = capsys.readouterr()
        assert run_cli("access", "mimiciv", "--version", "3.1", "--json") == 0
        machine = capsys.readouterr()
    assert "CITI training: pending" in human.out
    assert "Data use agreement: required" in human.out
    assert "PhysioNet credentialing: complete" not in human.out
    assert json.loads(machine.out) == ACCESS
    assert machine.err == ""


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
                 status_code=404, json={"detail": "Not found"})
        assert run_cli("access", "mimiciv", "--json") == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "supports the access API" in output.err


def test_access_cli_missing_unattended_password_fails_before_request(monkeypatch, capsys):
    monkeypatch.setenv("PHYSIONET_USERNAME", "researcher")
    monkeypatch.delenv("PHYSIONET_PASSWORD", raising=False)
    assert run_cli("access", "mimiciv") == 1
    assert "PHYSIONET_PASSWORD" in capsys.readouterr().err
