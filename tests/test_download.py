"""Tests for physionet.download module."""

import hashlib
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import requests
import requests_mock as rm

from physionet.download import (
    download,
    _parse_manifest,
    _filter_files,
    _select_source,
    _download_file,
    _verify_checksum,
    _resolve_version,
    S3_BASE_URL,
)
from physionet.api.client import PhysioNetClient
from physionet.api.exceptions import ForbiddenError, NotFoundError


# --- Manifest parsing ---


class TestParseManifest:
    def test_basic(self):
        text = "abc123  file1.csv\ndef456  subdir/file2.txt\n"
        result = _parse_manifest(text)
        assert result == [
            ("file1.csv", "abc123"),
            ("subdir/file2.txt", "def456"),
        ]

    def test_empty(self):
        assert _parse_manifest("") == []
        assert _parse_manifest("  \n  \n") == []

    def test_ignores_malformed_lines(self):
        text = "abc123  file1.csv\nbadline\ndef456  file2.csv\n"
        result = _parse_manifest(text)
        assert len(result) == 2

    def test_preserves_paths_with_spaces(self):
        text = "abc123  path with spaces/file.csv\n"
        result = _parse_manifest(text)
        assert result == [("path with spaces/file.csv", "abc123")]


# --- File filtering ---


class TestFilterFiles:
    @pytest.fixture
    def sample_files(self):
        return [
            ("data.csv", "hash1"),
            ("readme.md", "hash2"),
            ("subdir/notes.pdf", "hash3"),
            ("subdir/data2.csv", "hash4"),
        ]

    def test_no_filters(self, sample_files):
        result = _filter_files(sample_files)
        assert result == sample_files

    def test_include_pattern(self, sample_files):
        result = _filter_files(sample_files, include=["*.csv"])
        assert result == [("data.csv", "hash1"), ("subdir/data2.csv", "hash4")]

    def test_exclude_pattern(self, sample_files):
        result = _filter_files(sample_files, exclude=["*.pdf"])
        assert result == [
            ("data.csv", "hash1"),
            ("readme.md", "hash2"),
            ("subdir/data2.csv", "hash4"),
        ]

    def test_include_and_exclude(self, sample_files):
        result = _filter_files(sample_files, include=["subdir/*"], exclude=["*.pdf"])
        assert result == [("subdir/data2.csv", "hash4")]

    def test_multiple_include_patterns(self, sample_files):
        result = _filter_files(sample_files, include=["*.csv", "*.md"])
        assert len(result) == 3


# --- Source selection ---


class TestSelectSource:
    def test_physionet_source(self):
        client = MagicMock()
        client.base_url = "https://physionet.org"
        result = _select_source(client, "demo", "1.0", "physionet", None)
        assert result == "https://physionet.org/files/demo/1.0"

    def test_aws_source(self):
        client = MagicMock()
        result = _select_source(client, "demo", "1.0", "aws", None)
        assert result == f"{S3_BASE_URL}/demo/1.0"

    def test_auto_no_credentials(self):
        client = MagicMock()
        result = _select_source(client, "demo", "1.0", "auto", None)
        assert result == f"{S3_BASE_URL}/demo/1.0"

    def test_auto_with_credentials(self):
        client = MagicMock()
        client.base_url = "https://physionet.org"
        result = _select_source(client, "demo", "1.0", "auto", "user")
        assert result == "https://physionet.org/files/demo/1.0"


# --- Checksum verification ---


class TestVerifyChecksum:
    def test_valid_checksum(self, tmp_path):
        file = tmp_path / "test.txt"
        content = b"hello world"
        file.write_bytes(content)
        expected = hashlib.sha256(content).hexdigest()
        assert _verify_checksum(file, expected) is True

    def test_invalid_checksum(self, tmp_path):
        file = tmp_path / "test.txt"
        file.write_bytes(b"hello world")
        assert _verify_checksum(file, "0" * 64) is False


# --- Version resolution ---


class TestResolveVersion:
    def test_latest_version(self):
        client = MagicMock()
        v1 = MagicMock(version="1.0")
        v2 = MagicMock(version="2.0")
        client.projects.list_versions.return_value = [v1, v2]

        result = _resolve_version(client, "demo", None)
        assert result == "2.0"

    def test_specific_version(self):
        client = MagicMock()
        v1 = MagicMock(version="1.0")
        v2 = MagicMock(version="2.0")
        client.projects.list_versions.return_value = [v1, v2]

        result = _resolve_version(client, "demo", "1.0")
        assert result == "1.0"

    def test_invalid_version(self):
        client = MagicMock()
        v1 = MagicMock(version="1.0")
        client.projects.list_versions.return_value = [v1]

        with pytest.raises(NotFoundError, match="Version '9.9' not found"):
            _resolve_version(client, "demo", "9.9")

    def test_no_versions(self):
        client = MagicMock()
        client.projects.list_versions.return_value = []

        with pytest.raises(NotFoundError, match="No versions found"):
            _resolve_version(client, "demo", None)


# --- File download ---


class TestDownloadFile:
    def test_download_new_file(self, tmp_path):
        content = b"file content here"
        expected_hash = hashlib.sha256(content).hexdigest()
        dest = tmp_path / "output.txt"

        with rm.Mocker() as m:
            m.get("https://example.com/file.txt", content=content)
            session = requests.Session()
            size = _download_file("https://example.com/file.txt", dest, expected_hash, session)

        assert dest.read_bytes() == content
        assert size == len(content)

    def test_download_checksum_mismatch(self, tmp_path):
        content = b"file content"
        dest = tmp_path / "output.txt"

        with rm.Mocker() as m:
            m.get("https://example.com/file.txt", content=content)
            session = requests.Session()
            with pytest.raises(ValueError, match="Checksum verification failed"):
                _download_file("https://example.com/file.txt", dest, "bad_hash", session)

        assert not dest.exists()

    def test_download_resume(self, tmp_path):
        part1 = b"first part"
        part2 = b" second part"
        full_content = part1 + part2
        expected_hash = hashlib.sha256(full_content).hexdigest()
        dest = tmp_path / "output.txt"
        dest.write_bytes(part1)

        with rm.Mocker() as m:
            m.get(
                "https://example.com/file.txt",
                content=part2,
                status_code=206,
                headers={"Content-Length": str(len(part2))},
            )
            session = requests.Session()
            _download_file("https://example.com/file.txt", dest, expected_hash, session)

        assert dest.read_bytes() == full_content

    def test_download_403(self, tmp_path):
        dest = tmp_path / "output.txt"

        with rm.Mocker() as m:
            m.get("https://example.com/file.txt", status_code=403, text="Access denied")
            session = requests.Session()
            with pytest.raises(ForbiddenError, match="Access denied"):
                _download_file("https://example.com/file.txt", dest, "hash", session)

    def test_resume_416_valid_checksum(self, tmp_path):
        """File already complete (416 Range Not Satisfiable) with valid checksum."""
        content = b"complete file"
        expected_hash = hashlib.sha256(content).hexdigest()
        dest = tmp_path / "output.txt"
        dest.write_bytes(content)

        with rm.Mocker() as m:
            m.get("https://example.com/file.txt", status_code=416)
            session = requests.Session()
            size = _download_file("https://example.com/file.txt", dest, expected_hash, session)

        assert size == 0
        assert dest.read_bytes() == content


# --- Full download integration ---


class TestDownload:
    def _make_manifest(self, files):
        """Create a SHA256SUMS.txt content from (path, content) pairs."""
        lines = []
        for path, content in files:
            h = hashlib.sha256(content).hexdigest()
            lines.append(f"{h}  {path}")
        return "\n".join(lines)

    def test_download_dry_run(self, tmp_path, capsys):
        manifest = self._make_manifest([("file.csv", b"data")])
        versions_response = [
            {"slug": "demo", "title": "Demo", "version": "1.0", "abstract": "", "citation": ""}
        ]

        with rm.Mocker() as m:
            m.get("https://physionet.org/api/v1/projects/demo/versions/", json=versions_response)
            m.get(
                "https://physionet.org/api/v1/projects/published/demo/1.0/sha256sums/",
                text=manifest,
            )

            result = download(
                "demo",
                version="1.0",
                output_dir=str(tmp_path),
                dry_run=True,
                base_url="https://physionet.org",
            )

        captured = capsys.readouterr()
        assert "file.csv" in captured.out
        assert "demo v1.0" in captured.out

    def test_download_with_filter(self, tmp_path, capsys):
        manifest = self._make_manifest([
            ("file.csv", b"csv data"),
            ("readme.md", b"readme"),
        ])
        versions_response = [
            {"slug": "demo", "title": "Demo", "version": "1.0", "abstract": "", "citation": ""}
        ]

        with rm.Mocker() as m:
            m.get("https://physionet.org/api/v1/projects/demo/versions/", json=versions_response)
            m.get(
                "https://physionet.org/api/v1/projects/published/demo/1.0/sha256sums/",
                text=manifest,
            )

            result = download(
                "demo",
                version="1.0",
                output_dir=str(tmp_path),
                include=["*.csv"],
                dry_run=True,
                base_url="https://physionet.org",
            )

        captured = capsys.readouterr()
        assert "file.csv" in captured.out
        assert "readme.md" not in captured.out

    def test_download_files(self, tmp_path):
        file_content = b"hello world"
        file_hash = hashlib.sha256(file_content).hexdigest()
        manifest = f"{file_hash}  data.csv\n"
        versions_response = [
            {"slug": "demo", "title": "Demo", "version": "1.0", "abstract": "", "citation": ""}
        ]

        with rm.Mocker() as m:
            m.get("https://physionet.org/api/v1/projects/demo/versions/", json=versions_response)
            m.get(
                "https://physionet.org/api/v1/projects/published/demo/1.0/sha256sums/",
                text=manifest,
            )
            m.get(f"{S3_BASE_URL}/demo/1.0/data.csv", content=file_content)

            result = download(
                "demo",
                version="1.0",
                output_dir=str(tmp_path),
                base_url="https://physionet.org",
            )

        assert (tmp_path / "demo-1.0" / "data.csv").read_bytes() == file_content

    def test_download_not_found(self, tmp_path):
        with rm.Mocker() as m:
            m.get("https://physionet.org/api/v1/projects/nonexistent/versions/", status_code=404, json={"error": "Not found"})

            with pytest.raises(NotFoundError):
                download("nonexistent", output_dir=str(tmp_path), base_url="https://physionet.org")

    def test_download_forbidden(self, tmp_path, capsys):
        versions_response = [
            {"slug": "restricted", "title": "Restricted", "version": "1.0", "abstract": "", "citation": ""}
        ]
        manifest = f"{'a' * 64}  data.csv\n"

        with rm.Mocker() as m:
            m.get("https://physionet.org/api/v1/projects/restricted/versions/", json=versions_response)
            m.get(
                "https://physionet.org/api/v1/projects/published/restricted/1.0/sha256sums/",
                text=manifest,
            )
            m.get(
                f"{S3_BASE_URL}/restricted/1.0/data.csv",
                status_code=403,
                text="You must sign the DUA",
            )

            download("restricted", version="1.0", output_dir=str(tmp_path), base_url="https://physionet.org")

        captured = capsys.readouterr()
        assert "1 failed" in captured.out
        assert not (tmp_path / "restricted-1.0" / "data.csv").exists()


# --- Keyboard interrupt handling ---


class TestDownloadFileInterrupt:
    def test_partial_file_deleted_on_interrupt(self, tmp_path):
        """Partially downloaded file is deleted when KeyboardInterrupt occurs."""
        dest = tmp_path / "output.txt"

        def interrupted_iter(chunk_size=8192):
            yield b"partial data"
            raise KeyboardInterrupt()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Length": "1000"}
        mock_response.iter_content = interrupted_iter

        mock_session = MagicMock()
        mock_session.get.return_value = mock_response

        with pytest.raises(KeyboardInterrupt):
            _download_file("https://example.com/file.txt", dest, "fakehash", mock_session)

        assert not dest.exists(), "Partial file should be deleted on KeyboardInterrupt"


class TestDownloadInterrupt:
    def _make_manifest(self, files):
        """Create a SHA256SUMS.txt content from (path, content) pairs."""
        lines = []
        for path, content in files:
            h = hashlib.sha256(content).hexdigest()
            lines.append(f"{h}  {path}")
        return "\n".join(lines)

    def test_download_interrupted_returns_gracefully(self, tmp_path, capsys):
        """download() catches KeyboardInterrupt and prints summary."""
        file1_content = b"file one"
        file2_content = b"file two"
        file1_hash = hashlib.sha256(file1_content).hexdigest()
        file2_hash = hashlib.sha256(file2_content).hexdigest()
        manifest = f"{file1_hash}  file1.csv\n{file2_hash}  file2.csv\n"
        versions_response = [
            {"slug": "demo", "title": "Demo", "version": "1.0", "abstract": "", "citation": ""}
        ]

        call_count = 0

        def mock_download_file(url, dest, expected_hash, session):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                dest.write_bytes(file1_content)
                return len(file1_content)
            raise KeyboardInterrupt()

        with rm.Mocker() as m:
            m.get("https://physionet.org/api/v1/projects/demo/versions/", json=versions_response)
            m.get(
                "https://physionet.org/api/v1/projects/published/demo/1.0/sha256sums/",
                text=manifest,
            )

            with patch("physionet.download._download_file", side_effect=mock_download_file):
                result = download(
                    "demo",
                    version="1.0",
                    output_dir=str(tmp_path),
                    base_url="https://physionet.org",
                )

        captured = capsys.readouterr()
        assert "interrupted" in captured.out.lower()
        assert "1 downloaded" in captured.out
        assert result == tmp_path / "demo-1.0"
