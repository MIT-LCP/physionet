# PhysioNet

A collection of tools for working with the [PhysioNet](http://physionet.org/) repository.

## Installation

```bash
pip install physionet
```

## Usage

### API Client

Interact with the PhysioNet API to explore and search published projects:

```python
from physionet import PhysioNetClient

# Create a client instance
client = PhysioNetClient()

# List all published projects
projects = client.projects.list_published()
print(f"Total projects: {len(projects)}")

# Display first few projects
for project in projects[:5]:
    print(f"{project.slug} v{project.version}: {project.title}")

# Search for projects
ecg_projects = client.projects.search('ECG')
print(f"Found {len(ecg_projects)} ECG-related projects")

# Get all versions of a project
versions = client.projects.list_versions('mimic-iv-demo')
for version in versions:
    print(f"Version {version.version}: {version.title}")

# Get detailed information about a specific version
details = client.projects.get_details('mimic-iv-demo', '2.2')
print(f"Title: {details.title}")
print(f"DOI: {details.doi}")
print(f"Published: {details.publish_datetime}")
print(f"Size: {details.main_storage_size} bytes")
```

### Authenticated Requests

For endpoints that require authentication (e.g., downloading checksums):

```python
from physionet import PhysioNetClient

# Create client with authentication
client = PhysioNetClient(
    username='your_username',
    password='your_password'
)

# Download checksums file
client.projects.download_checksums(
    'mimic-iv-demo',
    '2.2',
    'checksums.txt'
)

# Or use environment variables
# Set PHYSIONET_USERNAME and PHYSIONET_PASSWORD
from physionet.api.utils import get_credentials_from_env

username, password = get_credentials_from_env()
client = PhysioNetClient(username=username, password=password)
```

### Using Context Manager

```python
from physionet import PhysioNetClient

# Automatically close session when done
with PhysioNetClient() as client:
    projects = client.projects.list_published()
    print(f"Found {len(projects)} projects")
```

### Utility Functions

```python
from physionet.api.utils import format_size

# Format bytes to human-readable size
size = format_size(16224447)
print(size)  # "15.47 MB"
```

## Error Handling

```python
from physionet import PhysioNetClient
from physionet.api.exceptions import NotFoundError, RateLimitError, ForbiddenError

client = PhysioNetClient()

try:
    details = client.projects.get_details('nonexistent-project', '1.0')
except NotFoundError:
    print("Project not found")
except RateLimitError:
    print("Rate limit exceeded, please wait before retrying")
except ForbiddenError:
    print("Access denied - check credentials or project permissions")
```

## Contributing

Contributions are welcome!

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
