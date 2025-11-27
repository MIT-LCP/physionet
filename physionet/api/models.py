from dataclasses import dataclass
from typing import Optional, List, Any


@dataclass
class ProjectVersion:
    """Represents a project version."""

    slug: str
    title: str
    version: str
    abstract: str
    citation: str


@dataclass
class PublishedProject:
    """Represents a published project."""

    slug: str
    version: str
    title: str
    short_description: str
    abstract: str
    core_doi: Optional[str]
    version_doi: Optional[str]
    is_latest_version: bool
    publish_date: str
    license: Optional[dict]
    dua: Optional[dict]
    main_storage_size: int
    compressed_storage_size: int

    @classmethod
    def from_dict(cls, data: dict) -> "PublishedProject":
        """Create instance from API response dictionary."""
        return cls(
            slug=data["slug"],
            version=data["version"],
            title=data["title"],
            short_description=data.get("short_description", ""),
            abstract=data.get("abstract", ""),
            core_doi=data.get("core_doi"),
            version_doi=data.get("version_doi"),
            is_latest_version=data.get("is_latest_version", False),
            publish_date=data.get("publish_date", ""),
            license=data.get("license"),
            dua=data.get("dua"),
            main_storage_size=data.get("main_storage_size", 0),
            compressed_storage_size=data.get("compressed_storage_size", 0),
        )


@dataclass
class ProjectDetail:
    """Detailed project information."""

    slug: str
    title: str
    version: str
    abstract: str
    license: Optional[dict]
    short_description: str
    project_home_page: Optional[str]
    publish_datetime: str
    doi: str
    main_storage_size: int
    compressed_storage_size: int

    @classmethod
    def from_dict(cls, data: dict) -> "ProjectDetail":
        """Create instance from API response dictionary."""
        return cls(
            slug=data["slug"],
            title=data["title"],
            version=data["version"],
            abstract=data.get("abstract", ""),
            license=data.get("license"),
            short_description=data.get("short_description", ""),
            project_home_page=data.get("project_home_page"),
            publish_datetime=data.get("publish_datetime", ""),
            doi=data.get("doi", ""),
            main_storage_size=data.get("main_storage_size", 0),
            compressed_storage_size=data.get("compressed_storage_size", 0),
        )


@dataclass
class PaginatedResponse:
    """Paginated API response."""

    count: int
    next: Optional[str]
    previous: Optional[str]
    results: List[Any]
