# PhysioNet

A collection of tools for working with the [PhysioNet](http://physionet.org/) repository.

## Installation

```bash
pip install physionet
```

Requires Python 3.10 or later.

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

### Preview tables before downloading

`physionet tables` lists a version's tables and their columns, and
`physionet head` shows a table's first rows. Both read only the start of each
file, so they take seconds even for multi-gigabyte tables:

```bash
# Every table and its columns
physionet tables mimic-iv-demo --version 2.2

# Which tables have a stay_id column? (case-insensitive; exits 1 if none)
physionet tables mimic-iv-demo --version 2.2 --column stay_id

# Only the tables in one folder, as JSON
physionet tables mimic-iv-demo --version 2.2 --prefix hosp --json

# The first 10 rows of a table (the extension can be left out)
physionet head mimic-iv-demo hosp/labevents --version 2.2

# The first 3 rows, as CSV
physionet head mimic-iv-demo hosp/patients --version 2.2 -n 3 --csv
```

CSV and TSV files, gzipped or not, can be previewed. Parquet and Excel files
need the whole file, so read them with `physionet.load()`. Files already in the
cache are read from there. Restricted datasets use the same sign-in as
`physionet download`, and both commands take `--username`, `--source` and
`--base-url`.

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

# Download 4 files at once (helps most for datasets with many small files)
physionet download mitdb --jobs 4

# Download files again, even those already saved with the right content
physionet download mitdb --overwrite
```

`--jobs` (or `-j`) takes 1 to 8, and defaults to 1. With more than one, only
the overall progress bar is shown. Ctrl-C stops all running files straight
away. Run the same command again to continue: files downloaded from PhysioNet
resume where they stopped, and partly downloaded files from AWS start again.

Files that are already saved and match the dataset's checksums are skipped,
and saved files that do not match are downloaded again. `--overwrite` also
downloads the matching ones again. Each file is downloaded next to the saved
one and replaces it only once its checksum matches, so a failed download
leaves the saved file as it was.

**Download sources:**

The `--source` flag controls where files are downloaded from:

- `auto` (default) — tries S3 first, falls back to PhysioNet direct if the dataset is not available on S3. The S3 mirror only copies https://physionet.org, so with another `--base-url` (or `PHYSIONET_BASE_URL`), `auto` downloads from that server
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

Restricted and credentialed datasets need a PhysioNet account that meets the
dataset's access requirements on its PhysioNet page. The simplest way to use
your account is to sign in once:

```bash
physionet login
```

This shows a link and a short code. Open the link, sign in to PhysioNet if
needed, enter the code and approve. Later downloads, from the CLI and the
Python API, then use your account. It works on remote machines too (over SSH,
on a cluster, or in a notebook): open the link on any device. No password is
stored. The sign-in is saved in `~/.config/physionet/credentials.json`
(`%APPDATA%\physionet` on Windows), readable only by you.

```bash
physionet whoami   # Show which account you are signed in as
physionet logout   # Sign out and delete the saved sign-in
```

`physionet login` only opens a browser when one can be shown, so over SSH it
just prints the link. Signing in again replaces, and signs out, the previous
sign-in. `login`, `logout` and `whoami` use https://physionet.org unless you
pass `--base-url` or set `PHYSIONET_BASE_URL`; `physionet download` and
`physionet access` use the same setting, so a download from another server
uses the sign-in saved for that server.

You can also see and sign out signed-in devices on PhysioNet, under
Settings > Tokens.

For unattended jobs (CI, scheduled scripts), create a personal access token
with the "Download data" scope on PhysioNet under Settings > Tokens, and set it
as an environment variable:

```bash
export PHYSIONET_TOKEN=pnpat_...
physionet download mimic-iv --version 3.1
```

Check your current access before starting a restricted download:

```bash
physionet access mimiciv --version 3.1 --username user
physionet access mimiciv --version 3.1 --username user --json
```

`access` asks the PhysioNet server whether this request can download files
directly from PhysioNet for the selected version. Without credentials, it
checks anonymous access and cannot tell whether your account has completed
credentialing, training, or a data use agreement. Use `--username` to check
your own account and see any outstanding steps. The command prompts for a
password in a terminal; for unattended use, set `PHYSIONET_USERNAME`
and `PHYSIONET_PASSWORD`. The saved sign-in from `physionet login` and
`PHYSIONET_TOKEN` are not used for this check: the server does not accept
tokens here, because the answer shows your credentialing and training status.
Downloads still use them. Omit `--version` to ask the server for its latest
published version. A successful status lookup exits with `0` even if access
is pending; check `authenticated` and `can_download` in JSON to understand
which account, if any, was checked. Cloud access may have separate requirements.
The response is informational: the server authorizes each file request again
when it happens. Some projects may allow access through other routes,
such as events; `can_download` is the server's current decision.

The command requires a PhysioNet server with the access-status API. If you
use a local or other deployment, pass `--base-url` or set
`PHYSIONET_BASE_URL`. Until that server is updated, the command says the
server does not appear to provide the access-status API. In Python, use:

```python
from physionet import PhysioNetClient

with PhysioNetClient(username="user", password="password") as client:
    status = client.projects.get_access_status("mimiciv", version="3.1")
    if status.can_download:
        print("Ready to download")
    else:
        for step in status.requirements:
            if step.status != "complete":
                print(step.label, step.status, step.url)
```

You can still use your username and password. The CLI prompts for the password
when run in a terminal:

```bash
physionet download mimic-iv --version 3.1 --username user
```

or set `PHYSIONET_USERNAME` and `PHYSIONET_PASSWORD`. In a non-interactive
session, a username without a password fails with an explanation instead of
waiting for input. Avoid putting passwords in command-line arguments, where
they may appear in shell history or process listings.

If several are set, credentials are used in this order: `token`, or `username`
and `password`, passed to the Python API; then `PHYSIONET_TOKEN`; then
`PHYSIONET_USERNAME` and `PHYSIONET_PASSWORD`; then the saved sign-in.

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

### `physionet doctor`

Check everything downloads depend on, and get told how to fix what is wrong:

```bash
physionet doctor                              # your setup
physionet doctor mimiciv                      # and a dataset
physionet doctor mimiciv --username USERNAME  # and your account's access to it
```

```
physionet 0.4.0 · Python 3.12.4 · macOS 15.1

Package
  ✓ physionet 0.4.0 is the latest release
  ✓ physionet:// paths are registered with fsspec

Server
  ✓ https://physionet.org reachable (230 ms)
  ✓ Access status API available

Sign-in
  ✓ Username and password for researcher accepted (--username and --password)

Downloads
  ✓ AWS open data mirror reachable
  ℹ No AWS credentials found; --source aws works only for open datasets
  ✓ Cache: ~/.cache/physionet (2.3 GB, writable)
  ✗ 3 restricted file(s) in the cache can be read by other users
      Fix: physionet doctor --fix-permissions

Dataset mimiciv
  ✓ Latest version 3.1
  ℹ Access policy: Credentialed
  ✗ You can't download mimiciv 3.1 yet
      CITI training: pending - https://physionet.org/settings/training/
      Fix: https://physionet.org/content/mimiciv/3.1/
  ℹ Not on the AWS open data mirror; downloads come from PhysioNet

2 problems.
```

It checks:

- **Package**: whether a newer release is on PyPI (skip with `--no-version-check`),
  and that `physionet://` paths are registered.
- **Server**: that the server (`--base-url` or `PHYSIONET_BASE_URL`) answers, and
  has the access status API.
- **Sign-in**: which credentials are used (a username and password, then
  `PHYSIONET_TOKEN`, then the saved sign-in), whether the server accepts them,
  and that the saved sign-in file is private.
- **Downloads**: the AWS mirror, AWS credentials for `--source aws`, the cache
  folder, and that restricted files in the cache are readable only by you.
  `--fix-permissions` fixes them.
- **A dataset**, if you name one: its versions, your access and the steps still
  needed, and whether it is on the AWS mirror. The server checks an account's
  access only for a username and password, not for a token or the saved sign-in.

It exits with status 1 if there is a problem. `--json` prints the same report
for scripts. Reports never include passwords or tokens, only where credentials
come from and the account name, so you can paste one into a bug report. In
Python, `physionet.doctor.run_checks()` returns the checks.

### `physionet cache`

`physionet.load()`, `fetch()` and `physionet://` paths keep the files they
download in a cache (see [Load a table](#load-a-table)). List what is in it, and
delete files you no longer need:

```bash
physionet cache                               # list the dataset versions in the cache
physionet cache list mimiciv --json           # one dataset, as JSON
physionet cache clean mimiciv --version 3.1   # delete one version
physionet cache clean --older-than 90d        # files downloaded more than 90 days ago (or 2w, 12h)
physionet cache clean --dry-run               # show what would be deleted
physionet cache dir                           # print the cache folder
```

```
Cache: /home/me/.cache/physionet (2 dataset version(s), 34 file(s), 1.21 GB)

DATASET        VERSION  FILES     SIZE  DOWNLOADED
mimic-iv-demo  2.2         31  15.20 MB  2026-11-20
mimiciv        3.1          3   1.19 GB  2026-10-02  restricted
```

`clean` asks before deleting; pass `--yes` in scripts. Deleted files are
downloaded again the next time they are used. A version whose files are all
deleted is removed with its cached checksum list. `--cache-dir` picks a cache
other than the default (`PHYSIONET_CACHE`, or `~/.cache/physionet`).

Only folders the package created, with a cached checksum list, are touched, so a
cache folder set to the wrong place does not lead to other files being deleted.
Symbolic links are removed, never followed. Unfinished downloads are kept for a
day, in case another program is still writing them. `physionet download` saves
files to the folder you choose, not the cache, so they are not listed here.

### `physionet verify`

Check that a downloaded copy of a dataset is complete and undamaged, for
example after copying it to a cluster or downloading it with `wget`:

```bash
physionet verify ./mimic-iv-demo-2.2                  # dataset and version read from the folder name
physionet verify ./physionet.org/files/mitdb/1.0.0    # a wget copy
physionet verify ./data mimiciv --version 3.1         # any folder
physionet verify ./data --checksums SHA256SUMS.txt    # against a checksum file
physionet verify ./mimiciv-3.1 --include "hosp/*" -j 4
```

```
Checking /data/mimic-iv-demo-2.2 against the checksums from https://physionet.org (mimic-iv-demo 2.2)
✓ 29 of 31 file(s) match
✗ 1 file(s) do not match (corrupt, changed or incomplete):
    hosp/labevents.csv.gz
✗ 1 file(s) are missing:
    icu/chartevents.csv.gz

To download missing and damaged files again:
  physionet download mimic-iv-demo --version 2.2 --output /data
```

Every file is compared with the project's published SHA-256 checksums. A
checksum list cached by `physionet.load()` or `fetch()` is used if there is one,
so this also works offline; otherwise it is downloaded (restricted datasets use
your sign-in, as for downloads). Without a dataset and version, a
`SHA256SUMS.txt` in the folder is used. That only shows the files match that
list, not that the list itself is PhysioNet's.

Files that are not in the checksum list are listed but do not count as
problems. A file that is a symbolic link to somewhere outside the folder counts
as a problem, since the copy does not hold it. It exits with status 1 if a file
is missing, damaged, unreadable or linked from outside, or if no files were
checked (an empty checksum list, or `--include` matching nothing); `--json`
lists every file. In Python:

```python
result = physionet.verify("mimic-iv-demo-2.2")
result.passed, result.mismatched, result.missing, result.extra
```

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

Restricted datasets use the same credentials as `download()`: the sign-in
saved by `physionet login` (or `physionet.login()` in Python), a `token`
argument or `PHYSIONET_TOKEN`, or a username and password. As with `download()`, `source="auto"` (the default)
uses the AWS open data mirror when a dataset is on it, and PhysioNet otherwise
(with a `base_url` other than https://physionet.org, it uses that server);
`source="physionet"` or `source="aws"` picks one.

**Pin the version** for reproducible analyses. With a version, a file that has
been loaded once is read from the cache without contacting PhysioNet, so it
works offline. Without one, the latest version is looked up online each time.
Load each file once while online before relying on it offline.

`latest_version()` returns the version that is used when none is given, so you
can look it up once and pin it:

```python
version = physionet.latest_version("mimic-iv-demo")  # e.g. "2.2"
patients = physionet.load("mimic-iv-demo", "hosp/patients", version=version)
```

**The cache** is at `~/.cache/physionet/physionet.org/<dataset>/<version>/`.
Set `PHYSIONET_CACHE` (or pass `cache_dir=`) to use another folder; if neither
is set, `$XDG_CACHE_HOME/physionet` is used when `XDG_CACHE_HOME` is set.
Files from a different `base_url` are kept in a folder named after that
server. See what is in it, and free space, with `physionet cache` (see
[below](#physionet-cache)) or in Python:

```python
physionet.list_cache()                     # one entry per dataset version
physionet.clean_cache("mimiciv", "3.1")    # delete one version
physionet.clean_cache(older_than=90)       # files downloaded over 90 days ago
```

- Files are only added after their SHA-256 checksum matches the dataset's
  `SHA256SUMS.txt`. Cached files are not checked again unless you pass
  `verify=True`, which re-checks the file and downloads it again if it changed.
- Files from restricted projects are saved readable only by you; files from open
  projects keep normal permissions, even when you are signed in. Anyone who can
  read the cache folder can read any other file in it, so a cache shared on a
  cluster suits open datasets. Keep restricted data in your own cache.
- Symbolic links inside the cache folder are refused. Only use a cache folder
  that people you don't trust cannot write to.

**Progress bars** are shown while a file is downloaded. Pass
`show_progress=False` to `load()`, `fetch()` or `download()` to hide them, or
set `TQDM_DISABLE=1` to hide every progress bar in the program.

**Errors** from `load()` and `fetch()`:

| Error | When |
|-------|------|
| `FileNotFoundError` | The dataset version has no such file. The message suggests close matches. |
| `NotFoundError` | The dataset or version does not exist. |
| `ForbiddenError` | Your account cannot access the dataset, or no credentials were given. |
| `ConnectionError` | PhysioNet cannot be reached and the file is not cached yet, or no version was given while offline. |
| `ValueError` | The file is not a supported table format (use `fetch()`), or a name without an extension matches more than one table. |

### Preview a table

`head()` and `tables()` read only the start of each file, like the
`physionet head` and `physionet tables` commands:

```python
import physionet

# The first rows of a table, as a DataFrame
physionet.head("mimic-iv-demo", "hosp/labevents", version="2.2", n=5)

# One row per table: "table", "columns" (a list, or None for Parquet and
# Excel files) and "error" (why a header could not be read, or None)
physionet.tables("mimic-iv-demo", version="2.2")
physionet.tables("mimic-iv-demo", version="2.2", column="stay_id")
physionet.tables("mimic-iv-demo", version="2.2", prefix="icu")
```

They take the same `cache_dir`, `source` and credential arguments as `fetch()`.
`tables()` reads one header per table, and asks for a `prefix` when a version
has more than 500 tables.

### Open files with pandas, polars, DuckDB and other tools

Installing the package adds a `physionet://` path type to
[fsspec](https://filesystem-spec.readthedocs.io/), the file layer used by
pandas, polars, DuckDB, xarray, Dask and others. Paths are
`physionet://<slug>/<version>/<path within the project>`:

```python
import pandas as pd

patients = pd.read_csv("physionet://mimic-iv-demo/2.2/hosp/patients.csv.gz")
```

Files go through the same cache as `fetch()`: each one is downloaded once,
checked against the project's SHA256SUMS.txt, and read from the cache after
that, including offline. Credentials also work the same way (the sign-in saved
by `physionet login`, `PHYSIONET_TOKEN`, or a username and password), and the
`fetch()` options can be passed as storage options:

```python
pd.read_csv(
    "physionet://mimiciv/3.1/hosp/patients.csv.gz",
    storage_options={"token": "pnpat_...", "cache_dir": "/data/physionet", "show_progress": False},
)
```

You can also browse a dataset without downloading it:

```python
import fsspec

fs = fsspec.filesystem("physionet")
fs.ls("physionet://mimic-iv-demo/2.2/hosp")      # one folder level
fs.glob("physionet://mimic-iv-demo/2.2/icu/*")   # file names
fs.info("physionet://mimic-iv-demo/2.2/hosp/admissions.csv.gz")  # size and SHA-256
fs.local_path("physionet://mimic-iv-demo/2.2/hosp/admissions.csv.gz")  # cached file path
```

With DuckDB, register the filesystem first:

```python
import duckdb

con = duckdb.connect()
con.register_filesystem(fsspec.filesystem("physionet"))
con.sql("select count(*) from read_csv_auto('physionet://mimic-iv-demo/2.2/hosp/admissions.csv.gz')")
```

**Read part of a file.** By default a file is downloaded in full the first
time it is opened. To read only part of a large file, such as a few seconds of
a long recording, pass `stream=True` as a storage option (or to
`fsspec.filesystem("physionet")`, or to `fs.open()` for one file). The parts
that are needed are then read with HTTP range requests, from the same source as
downloads (the AWS open data mirror for open datasets, and PhysioNet with your
sign-in for restricted ones):

```python
import fsspec

with fsspec.open("physionet://mitdb/1.0.0/100.dat", stream=True) as f:
    f.seek(1_000_000)
    data = f.read(10_800)
```

Requests start small and grow while a file is read from start to end, so
reading a whole file this way also takes few requests. Parts read this way are
not checked against `SHA256SUMS.txt` and are not kept in the cache, but a file
that is already in the cache is read from there (and checked again first with
`verify=True`). If a server sends whole files instead of parts, the file is
downloaded into the cache once, as without `stream`.

Use `latest` as the version to get the newest one (looked up online). Pin a
version for reproducible results. The filesystem is read-only. A missing
dataset, version or file raises `FileNotFoundError`, and a dataset your
account cannot access raises `PermissionError`.

### Download

```python
from physionet.download import download

# Download a dataset
download("mimic-iv-demo", version="2.2", output_dir="/data")

# Download from S3 using boto3
download("mimic-iv-demo", source="aws")

# Download 4 files at once
download("mitdb", output_dir="/data", jobs=4)

# Without progress bars (the summary is still printed)
download("mitdb", output_dir="/data", show_progress=False)

# Only some files, looked up in the checksum list
download("mitdb", "1.0.0", output_dir="/data", files=["100.hea", "100.dat", "100.atr"])

# Choose where each file is saved, relative to output_dir, instead of under
# /data/mitdb-1.0.0, and print nothing but errors
download(
    "mitdb", "1.0.0", output_dir="/data", files={"100.hea": "ecg/100.hea"},
    verbose=False, show_progress=False,
)

# Download files again, even those already saved with the right content
download("mitdb", "1.0.0", output_dir="/data", overwrite=True)

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

# Or a personal access token
client = PhysioNetClient(token="pnpat_...")

# Without credentials, the client uses the sign-in saved by `physionet login`
client = PhysioNetClient()
```

### Sign in from Python

In a notebook, sign in without leaving Python:

```python
import physionet

physionet.login()   # Prints a link and a code to approve in your browser
df = physionet.load("mimic-iv", "hosp/patients", version="3.1")
physionet.logout()
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
