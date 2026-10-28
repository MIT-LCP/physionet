from html import unescape
from typing import List, Optional, Iterator
from urllib.parse import quote, unquote, urljoin, urlsplit

from physionet.api.models import PublishedProject, ProjectVersion, ProjectDetail
from physionet.api.exceptions import NotFoundError, PhysioNetAPIError
from physionet.citation import Citation, CitationBundle, ProjectBibTeXParser, STANDARD_PHYSIONET_CITATIONS


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

    def get_citations(self, project_slug: str, version: Optional[str] = None, include_bibtex: bool = False) -> CitationBundle:
        """Return the project's citation and the standard PhysioNet citation.

        The API supplies the project citation. BibTeX is read from the published
        project page because the API does not currently include that format.
        When version is omitted, follow PhysioNet's latest-project redirect.
        """
        if version is None:
            latest_url = f"{self.client.base_url}/content/{quote(project_slug, safe='')}/"
            response = self.client.session.head(
                latest_url, allow_redirects=False, timeout=self.client.timeout
            )
            if response.status_code == 404:
                raise NotFoundError(f"No latest published version found for project '{project_slug}'")
            response.raise_for_status()
            target = urlsplit(urljoin(latest_url, response.headers.get("Location", "")))
            base = urlsplit(self.client.base_url)
            prefix = f"/content/{quote(project_slug, safe='')}/"
            suffix = target.path[len(prefix):] if target.path.startswith(prefix) else ""
            if (
                response.status_code != 302
                or (target.scheme, target.netloc) != (base.scheme, base.netloc)
                or not suffix.endswith("/")
                or "/" in suffix[:-1]
                or not suffix[:-1]
                or target.query
                or target.fragment
            ):
                raise PhysioNetAPIError(f"Invalid latest-version redirect for project '{project_slug}'")
            version = unquote(suffix[:-1])
            if "/" in version or version in (".", ".."):
                raise PhysioNetAPIError(f"Invalid latest-version redirect for project '{project_slug}'")

        project_version = next(
            (item for item in self.list_versions(project_slug) if item.version == version), None
        )
        if project_version is None:
            raise NotFoundError(f"Version '{version}' not found for project '{project_slug}'")

        details = self.get_details(project_slug, version)
        if not project_version.citation:
            raise PhysioNetAPIError(
                f"PhysioNet does not provide a citation for {project_slug} v{version}; "
                f"see {details.source_url or self.client.base_url}"
            )

        page_url = (
            f"{self.client.base_url}/content/{quote(project_slug, safe='')}/{quote(version, safe='')}/"
        )
        bibtex = None
        if include_bibtex:
            response = self.client.session.get(page_url, timeout=self.client.timeout)
            response.raise_for_status()
            parser = ProjectBibTeXParser()
            parser.feed(response.text)
            bibtex = parser.bibtex

        return CitationBundle(
            project=Citation(
                text=unescape(project_version.citation),
                bibtex=bibtex,
                doi=details.doi or None,
                url=details.source_url or page_url,
            ),
            physionet=STANDARD_PHYSIONET_CITATIONS,
            version=version,
        )

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
