# PhysioNet

A collection of tools for working with the [PhysioNet](http://physionet.org/) repository.

## Installation

```bash
pip install physionet
```

Requires Python 3.9 or later.

## Command-Line Interface

The package provides a `physionet` command-line tool. You can also run it as a
module with `python -m physionet`.

### `physionet download`

Download datasets from PhysioNet:

```bash
# Download the latest version of a dataset
physionet download mimic-iv-demo

# Download a specific version
physionet download mimic-iv-demo --version 2.2

# Download to a specific directory
physionet download mimic-iv-demo --output /data

# Preview what would be downloaded
physionet download mimic-iv-demo --dry-run

# Download only specific files
physionet download mimic-iv-demo --include "*.csv" --exclude "*/notes/*"
```

**Download sources:**

The `--source` flag controls where files are downloaded from:

- `auto` (default) — tries S3 first, falls back to PhysioNet direct if the dataset is not available on S3
- `physionet` — always downloads from PhysioNet directly
- `aws` — downloads from S3 using boto3 and the standard AWS credential chain

```bash
# Download from PhysioNet directly
physionet download mimic-iv-demo --source physionet

# Download from S3 using boto3
physionet download mimic-iv-demo --source aws
```

When using `--source aws`, boto3 discovers credentials automatically via the [standard AWS credential chain](https://docs.aws.amazon.com/sdkref/latest/guide/standardized-credentials.html) (environment variables, `~/.aws/credentials`, IAM roles, etc.). This allows downloading credentialed datasets from S3 without passing PhysioNet credentials.

**Authentication:**

For credentialed datasets, provide PhysioNet credentials via flags or environment variables:

```bash
# Via flags
physionet download mimic-iv --username user --password pass

# Via environment variables
export PHYSIONET_USERNAME=user
export PHYSIONET_PASSWORD=pass
physionet download mimic-iv
```

Downloads support automatic resume, SHA256 checksum verification, and retry on transient errors.

### `physionet validate`

Validate a dataset before submission to PhysioNet. The validator checks for
common issues that can delay the review process.

```bash
physionet validate /path/to/dataset
```

A validation report is automatically saved as `PHYSIONET_REPORT.md` in the
dataset directory.

**Options:**

| Option | Description |
|---|---|
| `--checks CATEGORIES` | Comma-separated list of check categories to run. Categories: `filesystem`, `documentation`, `integrity`, `quality`, `privacy`. Default: all. |
| `--report FILE` | Save the report to a specific path. Use a `.json` extension for JSON output, otherwise Markdown. |
| `--level {error,warning,info}` | Minimum severity level to display. Default: `info`. |
| `--no-sampling` | Disable row sampling for large CSV files. Scans all rows (slower but more thorough). |
| `--max-rows N` | Maximum number of rows to scan per CSV file. Default: 10000. Only applies when sampling is enabled. |

**Check categories:**

- **filesystem** - File naming issues (spaces, special characters, long names),
  proprietary formats (suggests open alternatives), hidden files, version
  control artifacts.
- **documentation** - Missing or incomplete documentation (e.g. `README.md`).
- **integrity** - CSV structure, encoding, and duplicate column detection.
- **quality** - Missing values, outliers, and data type consistency.
- **privacy** - PHI patterns (SSN, email, phone numbers), date patterns, and
  sensitive file detection.

**Examples:**

```bash
# Run only filesystem and privacy checks
physionet validate /path/to/dataset --checks filesystem,privacy

# Save report as JSON to a custom path
physionet validate /path/to/dataset --report results.json

# Show only errors and warnings (suppress info messages)
physionet validate /path/to/dataset --level warning

# Scan all rows in CSV files (no sampling)
physionet validate /path/to/dataset --no-sampling

# Limit scanning to 5000 rows per file
physionet validate /path/to/dataset --max-rows 5000
```

**Exit codes:**

- `0` - Validation passed (no errors).
- `1` - Validation failed with errors.

## Python API

### Download

```python
from physionet.download import download

# Download a dataset
download("mimic-iv-demo", version="2.2", output_dir="/data")

# Download from S3 using boto3
download("mimic-iv-demo", source="aws")
```

### Validation

```python
from physionet import validate_dataset, ValidationConfig

# Run with default settings
result = validate_dataset("/path/to/dataset")

# Run specific checks with custom settings
config = ValidationConfig(
    check_filesystem=True,
    check_documentation=True,
    check_integrity=False,
    check_quality=False,
    check_phi=True,
    max_rows_to_scan=5000,
)

result = validate_dataset("/path/to/dataset", config, show_progress=True)

# Print the summary report
print(result.summary())

# Export as a dictionary (for JSON serialization)
data = result.to_dict()
```

### API Client

Interact with the PhysioNet REST API to explore and search published projects:

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
