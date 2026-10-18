"""CLI outcomes for incomplete downloads."""

import hashlib
import sys
from unittest.mock import patch

import requests_mock

from physionet.cli import main


VERSIONS = [{"slug": "demo", "title": "Demo", "version": "1.0", "abstract": "", "citation": ""}]


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
        with patch("physionet.download._download_file", side_effect=interrupt_file):
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
