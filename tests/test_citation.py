"""Versioned project citations and CLI output."""

import json
import sys
from unittest.mock import patch

import pytest
import requests_mock

from physionet.api.client import PhysioNetClient
from physionet.api.exceptions import NotFoundError, PhysioNetAPIError
from physionet.cli import main


VERSIONS = [
    {"slug": "demo", "title": "Demo", "version": "2.2", "abstract": "", "citation": "Demo &amp; ECG v2.2 citation."},
    {"slug": "demo", "title": "Demo", "version": "1.0", "abstract": "", "citation": "Demo v1.0 citation."},
]
DETAIL = {
    "slug": "demo",
    "title": "Demo",
    "version": "2.2",
    "doi": "10.1234/demo",
    "source_url": "https://physionet.org/content/demo/2.2/",
}
PROJECT_BIBTEX = "@article{PhysioNet-demo-2.2,\n  title = {A & B},\n  doi = {10.1234/demo}\n}"
PROJECT_PAGE = (
    '<textarea id="bibtex-data">'
    '@article{PhysioNet-demo-2.2,\n  title = {A &amp; B},\n  doi = {10.1234/demo}\n}'
    '</textarea><textarea id="bibtex-data-platform">@article{other}</textarea>'
)
PUBLISHED = [
    {"slug": "demo", "version": "2.2", "is_latest_version": True, "title": "Demo"},
    {"slug": "other", "version": "9.0", "is_latest_version": True, "title": "Other"},
    {"slug": "demo", "version": "1.0", "is_latest_version": False, "title": "Demo"},
]


def register_project(mock, page=True, published=False):
    mock.get("https://physionet.org/api/v1/projects/demo/versions/", json=VERSIONS)
    mock.get("https://physionet.org/api/v1/projects/demo/versions/2.2/", json=DETAIL)
    if published:
        mock.get("https://physionet.org/api/v1/projects/published/", json=PUBLISHED)
    if page:
        mock.get("https://physionet.org/content/demo/2.2/", text=PROJECT_PAGE)


def run_cli(*arguments, version="2.2"):
    version_args = ["--version", version] if version is not None else []
    with patch.object(sys, "argv", ["physionet", "cite", "demo", *version_args, *arguments]):
        return main()


def test_get_citations_uses_exact_version_and_includes_both_standard_references():
    with requests_mock.Mocker() as mock:
        register_project(mock, page=False)
        with PhysioNetClient() as client:
            citations = client.projects.get_citations("demo", "2.2")

    assert citations.project.text == "Demo & ECG v2.2 citation."
    assert citations.project.bibtex is None
    assert citations.project.doi == "10.1234/demo"
    assert citations.project.url == "https://physionet.org/content/demo/2.2/"
    assert citations.version == "2.2"
    assert len(citations.physionet) == 2
    assert {item.doi for item in citations.physionet} == {
        "10.1038/s44360-026-00096-z", "10.1161/01.CIR.101.23.e215"
    }
    assert len(mock.request_history) == 2


def test_get_citations_reads_project_bibtex_from_physionet_page():
    with requests_mock.Mocker() as mock:
        register_project(mock)
        with PhysioNetClient() as client:
            citations = client.projects.get_citations("demo", "2.2", include_bibtex=True)

    assert citations.project.bibtex == PROJECT_BIBTEX
    assert all(item.bibtex.startswith("@article{") for item in citations.physionet)
    assert [request.url for request in mock.request_history][-1] == DETAIL["source_url"]


def test_get_citations_rejects_unknown_version():
    with requests_mock.Mocker() as mock:
        mock.get("https://physionet.org/api/v1/projects/demo/versions/", json=VERSIONS)
        with PhysioNetClient() as client, pytest.raises(NotFoundError, match="9.9"):
            client.projects.get_citations("demo", "9.9")
    assert len(mock.request_history) == 1


def test_get_citations_defaults_to_flagged_latest_even_when_not_last():
    with requests_mock.Mocker() as mock:
        register_project(mock, page=False, published=True)
        with PhysioNetClient() as client:
            citations = client.projects.get_citations("demo")

    assert citations.version == "2.2"
    assert citations.project.text == "Demo & ECG v2.2 citation."
    assert [request.path for request in mock.request_history] == [
        "/api/v1/projects/published/",
        "/api/v1/projects/demo/versions/",
        "/api/v1/projects/demo/versions/2.2/",
    ]


def test_get_citations_reports_missing_latest():
    with requests_mock.Mocker() as mock:
        mock.get("https://physionet.org/api/v1/projects/published/", json=PUBLISHED[1:])
        with PhysioNetClient() as client, pytest.raises(NotFoundError, match="No latest published version"):
            client.projects.get_citations("demo")
    assert len(mock.request_history) == 1


def test_get_citations_rejects_ambiguous_latest():
    with requests_mock.Mocker() as mock:
        mock.get("https://physionet.org/api/v1/projects/published/", json=PUBLISHED[:1] + [
            {"slug": "demo", "version": "3.0", "is_latest_version": True, "title": "Demo"}
        ])
        with PhysioNetClient() as client, pytest.raises(PhysioNetAPIError, match="Multiple latest versions"):
            client.projects.get_citations("demo")
    assert len(mock.request_history) == 1


def test_cite_prints_project_and_standard_physionet_citations(capsys):
    with requests_mock.Mocker() as mock:
        register_project(mock, page=False)
        status = run_cli()

    output = capsys.readouterr()
    assert status == 0
    assert "Demo & ECG v2.2 citation." in output.out
    assert "Standard PhysioNet citations:" in output.out
    assert "10.1038/s44360-026-00096-z" in output.out
    assert "10.1161/01.CIR.101.23.e215" in output.out
    assert DETAIL["source_url"] in output.out
    assert output.err == ""


def test_cite_without_version_shows_resolved_version(capsys):
    with requests_mock.Mocker() as mock:
        register_project(mock, page=False, published=True)
        status = run_cli(version=None)

    output = capsys.readouterr()
    assert status == 0
    assert "Project citation (demo v2.2):" in output.out
    assert "Demo & ECG v2.2 citation." in output.out
    assert output.err == ""


def test_cite_bibtex_prints_three_copyable_entries(capsys):
    with requests_mock.Mocker() as mock:
        register_project(mock)
        status = run_cli("--bibtex")

    output = capsys.readouterr()
    assert status == 0
    assert output.out.startswith(PROJECT_BIBTEX)
    assert output.out.count("@article{") == 3
    assert "Standard PhysioNet citations:" not in output.out
    assert output.err == ""


def test_cite_json_includes_text_and_bibtex(capsys):
    with requests_mock.Mocker() as mock:
        register_project(mock)
        status = run_cli("--json")

    output = capsys.readouterr()
    assert status == 0
    assert output.err == ""
    data = json.loads(output.out)
    assert data["project"]["text"] == "Demo & ECG v2.2 citation."
    assert data["version"] == "2.2"
    assert data["project"]["bibtex"] == PROJECT_BIBTEX
    assert len(data["physionet"]) == 2
    assert all(item["bibtex"] for item in data["physionet"])


@pytest.mark.parametrize("format_flag", ["--bibtex", "--json"])
def test_cite_without_version_supports_other_formats(capsys, format_flag):
    with requests_mock.Mocker() as mock:
        register_project(mock, published=True)
        status = run_cli(format_flag, version=None)

    output = capsys.readouterr()
    assert status == 0
    assert output.err == ""
    if format_flag == "--bibtex":
        assert output.out.startswith(PROJECT_BIBTEX)
    else:
        assert json.loads(output.out)["version"] == "2.2"


@pytest.mark.parametrize("version", ["2.2", None])
def test_cite_bibtex_reports_when_project_has_none(capsys, version):
    with requests_mock.Mocker() as mock:
        register_project(mock, published=version is None)
        mock.get("https://physionet.org/content/demo/2.2/", text="<html>Legacy project</html>")
        status = run_cli("--bibtex", version=version)

    output = capsys.readouterr()
    assert status == 1
    assert output.out == ""
    assert "BibTeX is unavailable" in output.err
    assert "demo v2.2" in output.err
