import pytest
import os
from physionet.api.utils import get_credentials_from_env, format_size


def test_get_credentials_from_env_with_credentials(monkeypatch):
    """Test getting credentials from environment variables."""
    monkeypatch.setenv("PHYSIONET_USERNAME", "testuser")
    monkeypatch.setenv("PHYSIONET_PASSWORD", "testpass")

    username, password = get_credentials_from_env()

    assert username == "testuser"
    assert password == "testpass"


def test_get_credentials_from_env_without_credentials(monkeypatch):
    """Test getting credentials when environment variables are not set."""
    monkeypatch.delenv("PHYSIONET_USERNAME", raising=False)
    monkeypatch.delenv("PHYSIONET_PASSWORD", raising=False)

    username, password = get_credentials_from_env()

    assert username is None
    assert password is None


def test_get_credentials_from_env_partial(monkeypatch):
    """Test getting credentials when only one variable is set."""
    monkeypatch.setenv("PHYSIONET_USERNAME", "testuser")
    monkeypatch.delenv("PHYSIONET_PASSWORD", raising=False)

    username, password = get_credentials_from_env()

    assert username == "testuser"
    assert password is None


def test_format_size_bytes():
    """Test formatting bytes."""
    assert format_size(100) == "100.00 B"
    assert format_size(512) == "512.00 B"


def test_format_size_kilobytes():
    """Test formatting kilobytes."""
    assert format_size(1024) == "1.00 KB"
    assert format_size(1536) == "1.50 KB"
    assert format_size(2048) == "2.00 KB"


def test_format_size_megabytes():
    """Test formatting megabytes."""
    assert format_size(1024 * 1024) == "1.00 MB"
    assert format_size(1024 * 1024 * 5) == "5.00 MB"
    assert format_size(1024 * 1024 * 1.5) == "1.50 MB"


def test_format_size_gigabytes():
    """Test formatting gigabytes."""
    assert format_size(1024 * 1024 * 1024) == "1.00 GB"
    assert format_size(1024 * 1024 * 1024 * 2.5) == "2.50 GB"


def test_format_size_terabytes():
    """Test formatting terabytes."""
    assert format_size(1024 * 1024 * 1024 * 1024) == "1.00 TB"
    assert format_size(1024 * 1024 * 1024 * 1024 * 3) == "3.00 TB"


def test_format_size_petabytes():
    """Test formatting petabytes."""
    assert format_size(1024 * 1024 * 1024 * 1024 * 1024) == "1.00 PB"
    assert format_size(1024 * 1024 * 1024 * 1024 * 1024 * 2) == "2.00 PB"


def test_format_size_edge_cases():
    """Test edge cases for size formatting."""
    assert format_size(0) == "0.00 B"
    assert format_size(1) == "1.00 B"
    assert format_size(1023) == "1023.00 B"
