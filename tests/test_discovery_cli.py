"""Researcher-facing project discovery commands."""

import json
import sys
from unittest.mock import patch

import requests_mock

from physionet.cli import main


PROJECT = {
    "slug": "demo",
    "version": "2.2",
    "title": "Demo ECG Database",
    "short_description": "Example records",
    "abstract": "ECG recordings for research.",
    "core_doi": "10.1234/demo",
    "version_doi": "10.1234/demo.2.2",
    "is_latest_version": True,
    "publish_date": "2024-01-01",
    "license": {"name": "Open license"},
    "dua": None,
    "main_storage_size": 1024,
    "compressed_storage_size": 512,
    "resource_type": "Database",
    "access_policy": "Open",
    "topics": ["ECG"],
    "source_url": "https://physionet.org/content/demo/2.2/",
}

DETAIL = {
    "slug": "demo",
    "version": "2.2",
    "title": "Demo ECG Database",
    "abstract": "ECG recordings for research.",
    "license": {"name": "Open license"},
    "short_description": "Example records",
    "project_home_page": None,
    "publish_datetime": "2024-01-01T00:00:00Z",
    "doi": "10.1234/demo.2.2",
    "main_storage_size": 1024,
    "compressed_storage_size": 512,
    "resource_type": "Database",
    "access_policy": "Open",
    "topics": ["ECG"],
    "source_url": "https://physionet.org/content/demo/2.2/",
}

VERSIONS = [
    {"slug": "demo", "title": "Demo ECG Database", "version": "1.0", "abstract": "", "citation": "v1"},
    {"slug": "demo", "title": "Demo ECG Database", "version": "2.2", "abstract": "", "citation": "v2"},
]


def run_cli(*arguments):
    with patch.object(sys, "argv", ["physionet", *arguments]):
        return main()


def test_search_shows_actionable_results_and_passes_filter(capsys):
    with requests_mock.Mocker() as mock:
        mock.get("https://physionet.org/api/v1/projects/search/", json=[PROJECT])
        status = run_cli("search", "ECG", "--resource-type", "database")

    output = capsys.readouterr()
    assert status == 0
    assert "demo v2.2  Demo ECG Database  [Open]" in output.out
    assert output.err == ""
    assert mock.last_request.qs == {"search_term": ["ecg"], "resource_type": ["database"]}


def test_search_json_is_only_json_on_stdout(capsys):
    with requests_mock.Mocker() as mock:
        mock.get("https://physionet.org/api/v1/projects/search/", json=[PROJECT])
        status = run_cli("search", "ECG", "--json")

    output = capsys.readouterr()
    assert status == 0
    assert output.err == ""
    assert json.loads(output.out)[0]["access_policy"] == "Open"
    assert json.loads(output.out)[0]["topics"] == ["ECG"]


def test_show_displays_access_requirements_and_metadata(capsys):
    with requests_mock.Mocker() as mock:
        mock.get("https://physionet.org/api/v1/projects/demo/versions/2.2/", json=DETAIL)
        status = run_cli("show", "demo", "--version", "2.2")

    output = capsys.readouterr()
    assert status == 0
    assert "Access: Open" in output.out
    assert "License: Open license" in output.out
    assert "DOI: 10.1234/demo.2.2" in output.out
    assert "PhysioNet page: https://physionet.org/content/demo/2.2/" in output.out


def test_show_json_preserves_platform_fields(capsys):
    with requests_mock.Mocker() as mock:
        mock.get("https://physionet.org/api/v1/projects/demo/versions/2.2/", json=DETAIL)
        status = run_cli("show", "demo", "--version", "2.2", "--json")

    output = capsys.readouterr()
    assert status == 0
    assert output.err == ""
    data = json.loads(output.out)
    assert data["version"] == "2.2"
    assert data["access_policy"] == "Open"
    assert data["source_url"] == DETAIL["source_url"]


def test_versions_readable_and_json(capsys):
    with requests_mock.Mocker() as mock:
        mock.get("https://physionet.org/api/v1/projects/demo/versions/", json=VERSIONS)
        assert run_cli("versions", "demo") == 0
        readable = capsys.readouterr()
        assert run_cli("versions", "demo", "--json") == 0
        machine = capsys.readouterr()

    assert "1.0  Demo ECG Database" in readable.out
    assert "2.2  Demo ECG Database" in readable.out
    assert [item["version"] for item in json.loads(machine.out)] == ["1.0", "2.2"]
    assert machine.err == ""


def test_discovery_error_is_reported_on_stderr(capsys):
    with requests_mock.Mocker() as mock:
        mock.get("https://physionet.org/api/v1/projects/demo/versions/2.2/", status_code=404, text="Not found")
        status = run_cli("show", "demo", "--version", "2.2", "--json")

    output = capsys.readouterr()
    assert status == 1
    assert output.out == ""
    assert "Not found" in output.err
