import pytest
from physionet.api.models import (
    ProjectVersion,
    PublishedProject,
    ProjectDetail,
    PaginatedResponse,
)


def test_project_version_creation():
    """Test ProjectVersion dataclass creation."""
    pv = ProjectVersion(
        slug="test-project",
        title="Test Project",
        version="1.0",
        abstract="Test abstract",
        citation="Test citation",
    )

    assert pv.slug == "test-project"
    assert pv.title == "Test Project"
    assert pv.version == "1.0"
    assert pv.abstract == "Test abstract"
    assert pv.citation == "Test citation"


def test_published_project_from_dict():
    """Test PublishedProject creation from API response dict."""
    data = {
        "slug": "mimic-iv-demo",
        "version": "2.2",
        "title": "MIMIC-IV Demo",
        "short_description": "Demo dataset",
        "abstract": "Abstract text",
        "core_doi": "10.1234/test",
        "version_doi": "10.1234/test.v2.2",
        "is_latest_version": True,
        "publish_date": "2023-01-01",
        "license": {"name": "MIT"},
        "dua": None,
        "main_storage_size": 1000000,
        "compressed_storage_size": 500000,
    }

    project = PublishedProject.from_dict(data)

    assert project.slug == "mimic-iv-demo"
    assert project.version == "2.2"
    assert project.title == "MIMIC-IV Demo"
    assert project.short_description == "Demo dataset"
    assert project.core_doi == "10.1234/test"
    assert project.is_latest_version is True
    assert project.main_storage_size == 1000000


def test_published_project_from_dict_with_missing_fields():
    """Test PublishedProject handles missing optional fields."""
    data = {
        "slug": "test-project",
        "version": "1.0",
        "title": "Test",
    }

    project = PublishedProject.from_dict(data)

    assert project.slug == "test-project"
    assert project.version == "1.0"
    assert project.title == "Test"
    assert project.short_description == ""
    assert project.abstract == ""
    assert project.core_doi is None
    assert project.is_latest_version is False
    assert project.main_storage_size == 0


def test_project_detail_from_dict():
    """Test ProjectDetail creation from API response dict."""
    data = {
        "slug": "test-project",
        "title": "Test Project",
        "version": "1.0",
        "abstract": "Test abstract",
        "license": {"name": "MIT"},
        "short_description": "Short desc",
        "project_home_page": "https://example.com",
        "publish_datetime": "2023-01-01T00:00:00",
        "doi": "10.1234/test",
        "main_storage_size": 1000000,
        "compressed_storage_size": 500000,
    }

    detail = ProjectDetail.from_dict(data)

    assert detail.slug == "test-project"
    assert detail.title == "Test Project"
    assert detail.version == "1.0"
    assert detail.doi == "10.1234/test"
    assert detail.project_home_page == "https://example.com"


def test_project_detail_from_dict_with_missing_fields():
    """Test ProjectDetail handles missing optional fields."""
    data = {
        "slug": "test-project",
        "title": "Test",
        "version": "1.0",
    }

    detail = ProjectDetail.from_dict(data)

    assert detail.slug == "test-project"
    assert detail.abstract == ""
    assert detail.license is None
    assert detail.project_home_page is None
    assert detail.doi == ""
    assert detail.main_storage_size == 0


def test_paginated_response_creation():
    """Test PaginatedResponse creation."""
    response = PaginatedResponse(
        count=100,
        next="https://api.example.com/page2",
        previous=None,
        results=["item1", "item2", "item3"],
    )

    assert response.count == 100
    assert response.next == "https://api.example.com/page2"
    assert response.previous is None
    assert len(response.results) == 3
