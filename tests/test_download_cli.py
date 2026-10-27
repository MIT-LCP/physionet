"""CLI outcomes for incomplete downloads."""

import hashlib
import sys
from importlib import import_module
from unittest.mock import patch

import requests_mock

from physionet.cli import main


download_module = import_module("physionet.download")
VERSIONS = [{"slug": "demo", "title": "Demo", "version": "1.0", "abstract": "", "citation": ""}]


def test_cli_prompts_for_password_with_username(monkeypatch):
    monkeypatch.delenv("PHYSIONET_PASSWORD", raising=False)
    monkeypatch.delenv("PHYSIONET_USERNAME", raising=False)
    with patch.object(sys, "argv", ["physionet", "download", "demo", "--username", "researcher"]):
        with patch.object(sys.stdin, "isatty", return_value=True):
            with patch("physionet.cli.getpass.getpass", return_value="secret") as prompt:
                with patch.object(download_module, "download") as mock_download:
                    assert main() == 0
    prompt.assert_called_once_with("PhysioNet password: ")
    assert mock_download.call_args.kwargs["username"] == "researcher"
    assert mock_download.call_args.kwargs["password"] == "secret"


def test_cli_requires_password_for_unattended_download(monkeypatch, capsys):
    monkeypatch.delenv("PHYSIONET_PASSWORD", raising=False)
    monkeypatch.delenv("PHYSIONET_USERNAME", raising=False)
    with patch.object(sys, "argv", ["physionet", "download", "demo", "--username", "researcher"]):
        with patch.object(sys.stdin, "isatty", return_value=False):
            with patch.object(download_module, "download") as mock_download:
                assert main() == 1
    mock_download.assert_not_called()
    assert "PHYSIONET_PASSWORD" in capsys.readouterr().err


def test_cli_uses_environment_credentials_without_prompt(monkeypatch):
    monkeypatch.setenv("PHYSIONET_USERNAME", "researcher")
    monkeypatch.setenv("PHYSIONET_PASSWORD", "secret")
    with patch.object(sys, "argv", ["physionet", "download", "demo"]):
        with patch("physionet.cli.getpass.getpass") as prompt:
            with patch.object(download_module, "download") as mock_download:
                assert main() == 0
    prompt.assert_not_called()
    assert mock_download.call_args.kwargs["password"] == "secret"


def test_cli_does_not_prompt_for_environment_username_alone(monkeypatch):
    monkeypatch.setenv("PHYSIONET_USERNAME", "researcher")
    monkeypatch.delenv("PHYSIONET_PASSWORD", raising=False)
    with patch.object(sys, "argv", ["physionet", "download", "demo"]):
        with patch("physionet.cli.getpass.getpass") as prompt:
            with patch.object(download_module, "download") as mock_download:
                assert main() == 0
    prompt.assert_not_called()
    assert mock_download.call_args.kwargs["password"] is None


def test_cli_exits_nonzero_when_a_file_fails(tmp_path, capsys):
    good_content = b"good data"
    manifest = "\n".join(
        [
            f"{hashlib.sha256(good_content).hexdigest()}  good.csv",
            f"{hashlib.sha256(b'expected data').hexdigest()}  bad.csv",
        ]
    )

    with requests_mock.Mocker() as mock:
        mock.get("https://physionet.org/api/v1/projects/demo/versions/", json=VERSIONS)
        mock.get("https://physionet.org/files/demo/1.0/SHA256SUMS.txt", text=manifest)
        mock.get("https://physionet.org/files/demo/1.0/good.csv", content=good_content)
        mock.get("https://physionet.org/files/demo/1.0/bad.csv", status_code=403, text="Access denied")
        with patch.object(
            sys,
            "argv",
            ["physionet", "download", "demo", "--version", "1.0", "--source", "physionet", "--output", str(tmp_path)],
        ):
            status = main()

    output = capsys.readouterr()
    assert status == 1
    assert "1 failed" in output.out
    assert "bad.csv" in output.err
    assert output.err.count("https://physionet.org/content/demo/") == 1
    assert (tmp_path / "demo-1.0" / "good.csv").read_bytes() == good_content


def test_cli_exits_130_on_interrupt(tmp_path, capsys):
    content = b"expected data"
    manifest = f"{hashlib.sha256(content).hexdigest()}  data.csv"

    def interrupt_file(url, dest, expected_hash, session, retries=3):
        dest.write_bytes(b"partial")
        raise KeyboardInterrupt()

    with requests_mock.Mocker() as mock:
        mock.get("https://physionet.org/api/v1/projects/demo/versions/", json=VERSIONS)
        mock.get("https://physionet.org/files/demo/1.0/SHA256SUMS.txt", text=manifest)
        with patch.object(download_module, "_download_file", side_effect=interrupt_file):
            with patch.object(
                sys,
                "argv",
                [
                    "physionet", "download", "demo", "--version", "1.0",
                    "--source", "physionet", "--output", str(tmp_path),
                ],
            ):
                status = main()

    output = capsys.readouterr()
    assert status == 130
    assert "interrupted" in output.err.lower()
    assert (tmp_path / "demo-1.0" / "data.csv").read_bytes() == b"partial"
