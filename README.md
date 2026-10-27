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

### Find and inspect a dataset

Search published projects, inspect a specific version, then download the files
you need:

```bash
physionet search "ECG" --resource-type database
physionet versions mimic-iv-demo
physionet show mimic-iv-demo --version 2.2
physionet download mimic-iv-demo --version 2.2 --include "*.csv"
```

Search results show the project slug, version, title, and access policy. `show`
displays the license, DOI, size, topics, and a link to the PhysioNet page when
available. Specify a version with `show` so the metadata refers to the exact
dataset you intend to use. The PhysioNet page explains any access requirements;
the server decides whether your account can download a project.

All three discovery commands support `--json` for scripts. JSON is written to
stdout, and errors are written to stderr:

```bash
physionet search "ECG" --json > projects.json
physionet show mimic-iv-demo --version 2.2 --json > project.json
physionet versions mimic-iv-demo --json > versions.json
```

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

For credentialed datasets, provide your PhysioNet username. The CLI prompts for
your password when run in a terminal. Your account must already meet the
dataset's access requirements on its PhysioNet page.

```bash
physionet download mimic-iv --version 3.1 --username user
```

For unattended downloads, set both environment variables instead:

```bash
export PHYSIONET_USERNAME=user
export PHYSIONET_PASSWORD=pass
physionet download mimic-iv --version 3.1
```

In a non-interactive session, a username without a password fails with an
explanation instead of waiting for input. The Python API still accepts explicit
`username` and `password` arguments. Avoid putting passwords in command-line
arguments, where they may appear in shell history or process listings.

PhysioNet credentials are sent only to the configured PhysioNet origin. Requests
to S3 mirrors or other hosts do not receive them; `--source aws` uses the AWS
credential chain described above.

Downloads support automatic resume, SHA256 checksum verification, and retry on transient errors.

If any selected file fails, the command reports the failed file and exits with status `1`;
successful files remain in the destination. In Python, `download()` raises
`DownloadError` with the failed paths and causes. Pressing Ctrl-C exits the command
with status `130` and leaves the incomplete file in place so a later HTTP download
can resume it. If the server ignores the resume request, the client downloads the
file from the beginning. Explicit AWS downloads currently restart incomplete files
on the next attempt.

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
| `--no-duplicate-check` | Skip checking files for identical content. Useful when hashing large datasets would take too long. |

**Check categories:**

- **filesystem** - File naming issues (spaces, special characters, long names),
  proprietary formats (suggests open alternatives), hidden files, version
  control artifacts, and byte-identical files.
- **documentation** - Missing or incomplete documentation (e.g. `README.md`).
- **integrity** - CSV structure, encoding, and duplicate column detection.
- **quality** - Missing values, outliers, and data type consistency.
- **privacy** - PHI patterns (SSN, email, phone numbers), ages above 89,
  dates that may be real, and sensitive file detection.

Dates from the last 200 years up to today are reported as possibly real, since
de-identified datasets usually shift dates (often into the future). This covers
written dates in CSV and text files, and Unix timestamps in columns with
time-like names such as `charttime` or `timestamp`. Dates shifted into that
window still need to be confirmed by the submitter. Set
`ValidationConfig(date_lookback_years=...)` to a number of years from 1 to 1000
to change the window, or `None` to turn the check off. The terminal shows date
warnings for the first 10 files; the saved report lists every file.

CSV exports from the Empatica E4 wearable are recognized, so their headerless
layout, empty `tags.csv` files, and Unix timestamps don't produce false errors
or phone-number warnings. Their timestamps are checked as dates.

The filesystem check warns about non-empty files with identical bytes, even if
they have different names or are in different folders. Each warning names the
duplicate file, the first matching path, and its SHA-256 checksum. Identical
files may be intentional; review them before removing anything. The validator
compares file sizes and a small prefix before hashing matching candidates in
chunks, and skips symbolic links, empty files, and ignored paths. Hashing large
matching files can take time; use `--no-duplicate-check` or
`ValidationConfig(check_duplicate_files=False)` to skip this check.

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

### Load a table

`load()` reads a table from a dataset into a pandas DataFrame. The file is
downloaded on first use and kept in a local cache, so later calls read it from
disk.

```python
import physionet

# The extension can be left out when only one table has that name
patients = physionet.load("mimic-iv-demo", "hosp/patients", version="2.2")

# Options are passed to pandas
admissions = physionet.load(
    "mimic-iv-demo", "hosp/admissions.csv.gz", version="2.2",
    usecols=["subject_id", "admittime"], parse_dates=["admittime"],
)

# Any other file: get its path in the cache
license_path = physionet.fetch("mimic-iv-demo", "LICENSE.txt", version="2.2")
```

Supported table formats are `.csv`, `.csv.gz`, `.tsv`, `.tsv.gz`, `.parquet`
(needs `pyarrow`) and `.xlsx`. For other files, such as WFDB records or PDFs,
use `fetch()` and open the returned path yourself.

Restricted datasets use the same credentials as `download()`: `username` and
`password` arguments, or the `PHYSIONET_USERNAME` and `PHYSIONET_PASSWORD`
environment variables. As with `download()`, `source="auto"` (the default)
uses the AWS open data mirror when a dataset is on it, and PhysioNet otherwise;
`source="physionet"` or `source="aws"` picks one.

**Pin the version** for reproducible analyses. With a version, a file that has
been loaded once is read from the cache without contacting PhysioNet, so it
works offline. Without one, the latest version is looked up online each time.
Load each file once while online before relying on it offline.

**The cache** is at `~/.cache/physionet/physionet.org/<dataset>/<version>/`.
Set `PHYSIONET_CACHE` (or pass `cache_dir=`) to use another folder; if neither
is set, `$XDG_CACHE_HOME/physionet` is used when `XDG_CACHE_HOME` is set.
Files from a different `base_url` are kept in a folder named after that
server. Delete the cache folder to clear it.

- Files are only added after their SHA-256 checksum matches the dataset's
  `SHA256SUMS.txt`. Cached files are not checked again unless you pass
  `verify=True`, which re-checks the file and downloads it again if it changed.
- Files fetched with credentials are saved readable only by you. Anyone who can
  read the cache folder can read any other file in it, so a cache shared on a
  cluster suits open datasets. Keep restricted data in your own cache.
- Symbolic links inside the cache folder are refused. Only use a cache folder
  that people you don't trust cannot write to.

**Errors** from `load()` and `fetch()`:

| Error | When |
|-------|------|
| `FileNotFoundError` | The dataset version has no such file. The message suggests close matches. |
| `NotFoundError` | The dataset or version does not exist. |
| `ForbiddenError` | Your account cannot access the dataset, or no credentials were given. |
| `ConnectionError` | PhysioNet cannot be reached and the file is not cached yet, or no version was given while offline. |
| `ValueError` | The file is not a supported table format (use `fetch()`), or a name without an extension matches more than one table. |

### Download

```python
from physionet.download import download

# Download a dataset
download("mimic-iv-demo", version="2.2", output_dir="/data")

# Download from S3 using boto3
download("mimic-iv-demo", source="aws")

# Handle a partial failure while keeping successfully downloaded files
from physionet.download import DownloadError

try:
    download("mimic-iv-demo", version="2.2", output_dir="/data")
except DownloadError as error:
    for path, cause in error.failures:
        print(f"{path}: {cause}")
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
for project in ecg_projects:
    print(project.slug, project.version, project.access_policy, project.source_url)

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
print(f"Access policy: {details.access_policy}")
print(f"Topics: {details.topics}")
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

`physionet.load()` and `physionet.fetch()` raise the same `NotFoundError` and
`ForbiddenError`, plus the built-in errors listed under
[Load a table](#load-a-table):

```python
import physionet
from physionet.api.exceptions import ForbiddenError

try:
    patients = physionet.load("mimiciv", "hosp/patients", version="3.1")
except ForbiddenError:
    print("Set PHYSIONET_USERNAME and PHYSIONET_PASSWORD for an account with access")
except ConnectionError:
    print("Offline, and this file is not cached yet")
```

## Contributing

Contributions are welcome!

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
