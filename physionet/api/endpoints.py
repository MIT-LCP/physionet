from typing import List, Optional, Iterator
from urllib.parse import quote

from physionet.api.exceptions import PhysioNetAPIError
from physionet.api.models import PublishedProject, ProjectVersion, ProjectDetail, ProjectAccessStatus
from physionet.retry import configure_retry_adapter


_VERSION_ADAPTER_CACHE_ATTR = "_physionet_version_adapters"


class ProjectsAPI:
    """API methods for interacting with projects."""

    def __init__(self, client):
        self.client = client

    def get_access_status(self, project_slug: str, version: str = "latest") -> ProjectAccessStatus:
        """Ask the server whether the current user can download this version.

        ``version='latest'`` is resolved on the server. Requirements are
        informational; each file request remains independently authorized.
        """
        slug = quote(project_slug, safe="")
        selected_version = quote(version, safe="")
        response = self.client._make_request(
            "GET", f"projects/{slug}/versions/{selected_version}/access/"
        )
        try:
            return ProjectAccessStatus.from_dict(response.json())
        except (KeyError, TypeError, ValueError) as exc:
            raise PhysioNetAPIError(
                "Unexpected response from the PhysioNet server for the access status "
                f"(HTTP {response.status_code}, {response.headers.get('Content-Type') or 'no content type'})."
            ) from exc

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

    def list_versions(self, project_slug: str, retries: int = 3) -> List[ProjectVersion]:
        """
        List all versions of a project.

        Args:
            project_slug: Project identifier
            retries: Number of retries for transient server errors (0 to disable)

        Returns:
            List of ProjectVersion objects
        """
        endpoint = f"projects/{project_slug}/versions/"
        url = f"{self.client.api_base}projects/{project_slug}/versions/"
        configure_retry_adapter(
            self.client.session,
            url,
            retries,
            adapter_prefix=url,
            cache_attr=_VERSION_ADAPTER_CACHE_ATTR,
        )
        response = self.client._make_request("GET", endpoint)
        try:
            return [
                ProjectVersion(
                    slug=v["slug"],
                    title=v["title"],
                    version=v["version"],
                    abstract=v["abstract"],
                    citation=v["citation"],
                )
                for v in response.json()
            ]
        except (KeyError, TypeError, ValueError) as exc:
            raise PhysioNetAPIError(
                "Unexpected response from the PhysioNet server for the project versions "
                f"(HTTP {response.status_code}, {response.headers.get('Content-Type') or 'no content type'})."
            ) from exc
        finally:
            response.close()

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
