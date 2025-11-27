import pytest
import requests_mock
from physionet.api.client import PhysioNetClient
from physionet.api.models import PublishedProject, ProjectVersion, ProjectDetail


@pytest.fixture
def client():
    """Fixture providing a PhysioNetClient instance."""
    return PhysioNetClient()


def test_list_published_basic(client):
    """Test listing published projects."""
    mock_response = [
        {
            "slug": "project-1",
            "version": "1.0",
            "title": "Project 1",
            "short_description": "Description 1",
            "abstract": "Abstract 1",
            "core_doi": "10.1234/p1",
            "version_doi": "10.1234/p1.v1",
            "is_latest_version": True,
            "publish_date": "2023-01-01",
            "license": {"name": "MIT"},
            "dua": None,
            "main_storage_size": 1000,
            "compressed_storage_size": 500,
        },
        {
            "slug": "project-2",
            "version": "2.0",
            "title": "Project 2",
            "short_description": "Description 2",
            "abstract": "Abstract 2",
            "core_doi": "10.1234/p2",
            "version_doi": "10.1234/p2.v2",
            "is_latest_version": True,
            "publish_date": "2023-02-01",
            "license": {"name": "GPL"},
            "dua": None,
            "main_storage_size": 2000,
            "compressed_storage_size": 1000,
        },
    ]

    with requests_mock.Mocker() as m:
        m.get("https://physionet.org/api/v1/projects/published/", json=mock_response)

        result = client.projects.list_published()

        assert len(result) == 2
        assert isinstance(result[0], PublishedProject)
        assert result[0].slug == "project-1"
        assert result[1].slug == "project-2"


def test_iter_published(client):
    """Test iterating through published projects."""
    mock_response = [
        {
            "slug": "project-1",
            "version": "1.0",
            "title": "Project 1",
            "short_description": "",
            "abstract": "",
            "core_doi": None,
            "version_doi": None,
            "is_latest_version": True,
            "publish_date": "",
            "license": None,
            "dua": None,
            "main_storage_size": 0,
            "compressed_storage_size": 0,
        },
        {
            "slug": "project-2",
            "version": "1.0",
            "title": "Project 2",
            "short_description": "",
            "abstract": "",
            "core_doi": None,
            "version_doi": None,
            "is_latest_version": True,
            "publish_date": "",
            "license": None,
            "dua": None,
            "main_storage_size": 0,
            "compressed_storage_size": 0,
        },
    ]

    with requests_mock.Mocker() as m:
        m.get("https://physionet.org/api/v1/projects/published/", json=mock_response)

        projects = list(client.projects.iter_published())

        assert len(projects) == 2
        assert projects[0].slug == "project-1"
        assert projects[1].slug == "project-2"


def test_search_projects(client):
    """Test searching for projects."""
    mock_response = [
        {
            "slug": "ecg-project",
            "version": "1.0",
            "title": "ECG Database",
            "short_description": "ECG data",
            "abstract": "ECG abstract",
            "core_doi": None,
            "version_doi": None,
            "is_latest_version": True,
            "publish_date": "",
            "license": None,
            "dua": None,
            "main_storage_size": 0,
            "compressed_storage_size": 0,
        }
    ]

    with requests_mock.Mocker() as m:
        m.get("https://physionet.org/api/v1/projects/search/", json=mock_response)

        results = client.projects.search(search_term="ECG", resource_type=["all"])

        assert "search_term=ECG" in m.last_request.url
        assert len(results) == 1
        assert isinstance(results[0], PublishedProject)
        assert results[0].slug == "ecg-project"


def test_list_versions(client):
    """Test listing all versions of a project."""
    mock_response = [
        {
            "slug": "test-project",
            "title": "Test Project",
            "version": "1.0",
            "abstract": "Version 1.0",
            "citation": "Citation v1.0",
        },
        {
            "slug": "test-project",
            "title": "Test Project",
            "version": "2.0",
            "abstract": "Version 2.0",
            "citation": "Citation v2.0",
        },
    ]

    with requests_mock.Mocker() as m:
        m.get("https://physionet.org/api/v1/projects/test-project/versions/", json=mock_response)

        versions = client.projects.list_versions("test-project")

        assert len(versions) == 2
        assert isinstance(versions[0], ProjectVersion)
        assert versions[0].version == "1.0"
        assert versions[1].version == "2.0"


def test_get_details(client):
    """Test getting project details."""
    mock_response = {
        "slug": "test-project",
        "title": "Test Project",
        "version": "1.0",
        "abstract": "Test abstract",
        "license": {"name": "MIT"},
        "short_description": "Short desc",
        "project_home_page": "https://example.com",
        "publish_datetime": "2023-01-01T00:00:00",
        "doi": "10.1234/test",
        "main_storage_size": 1000,
        "compressed_storage_size": 500,
    }

    with requests_mock.Mocker() as m:
        m.get("https://physionet.org/api/v1/projects/test-project/versions/1.0/", json=mock_response)

        detail = client.projects.get_details("test-project", "1.0")

        assert isinstance(detail, ProjectDetail)
        assert detail.slug == "test-project"
        assert detail.version == "1.0"
        assert detail.doi == "10.1234/test"


def test_download_checksums(client, tmp_path):
    """Test downloading checksums file."""
    checksum_content = b"abc123 file1.txt\ndef456 file2.txt\n"
    output_file = tmp_path / "checksums.txt"

    with requests_mock.Mocker() as m:
        m.get(
            "https://physionet.org/api/v1/projects/published/test-project/1.0/sha256sums/", content=checksum_content
        )

        client.projects.download_checksums("test-project", "1.0", str(output_file))

        assert output_file.exists()
        assert output_file.read_bytes() == checksum_content
