from typing import List, Optional, Iterator
from physionet.api.models import PublishedProject, ProjectVersion, ProjectDetail


class ProjectsAPI:
    """API methods for interacting with projects."""

    def __init__(self, client):
        self.client = client

    def list_published(self) -> List[PublishedProject]:
        """
        List all published projects.

        Returns:
            List of PublishedProject objects

        Note:
            The API returns all projects in a single response (no pagination).
        """
        response = self.client._make_request("GET", "projects/published/")
        data = response.json()

        return [PublishedProject.from_dict(p) for p in data]

    def iter_published(self) -> Iterator[PublishedProject]:
        """
        Iterator that yields all published projects.

        Yields:
            PublishedProject objects

        Note:
            This is a convenience method that iterates over list_published() results.
        """
        for project in self.list_published():
            yield project

    def search(self, search_term: str, resource_type: Optional[List[str]] = None) -> List[PublishedProject]:
        """
        Search published projects.

        Args:
            search_term: Search keywords
            resource_type: Filter by resource type(s), or ['all'] for all types

        Returns:
            List of matching PublishedProject objects
        """
        params = {"search_term": search_term}

        if resource_type:
            params["resource_type"] = resource_type

        response = self.client._make_request("GET", "projects/search/", params=params)
        data = response.json()

        return [PublishedProject.from_dict(p) for p in data]

    def list_versions(self, project_slug: str) -> List[ProjectVersion]:
        """
        List all versions of a project.

        Args:
            project_slug: Project identifier

        Returns:
            List of ProjectVersion objects
        """
        endpoint = f"projects/{project_slug}/versions/"
        response = self.client._make_request("GET", endpoint)
        data = response.json()

        return [
            ProjectVersion(
                slug=v["slug"],
                title=v["title"],
                version=v["version"],
                abstract=v["abstract"],
                citation=v["citation"],
            )
            for v in data
        ]

    def get_details(self, project_slug: str, version: str) -> ProjectDetail:
        """
        Get detailed information about a specific project version.

        Args:
            project_slug: Project identifier
            version: Version number

        Returns:
            ProjectDetail object
        """
        endpoint = f"projects/{project_slug}/versions/{version}/"
        response = self.client._make_request("GET", endpoint)
        data = response.json()

        return ProjectDetail.from_dict(data)

    def download_checksums(self, project_slug: str, version: str, output_path: str):
        """
        Download SHA256 checksums file for a project.

        Args:
            project_slug: Project identifier
            version: Version number
            output_path: Path to save the checksums file

        Note:
            Requires authentication and project access permissions.
        """
        endpoint = f"projects/published/{project_slug}/{version}/sha256sums/"
        response = self.client._make_request("GET", endpoint)

        with open(output_path, "wb") as f:
            f.write(response.content)
