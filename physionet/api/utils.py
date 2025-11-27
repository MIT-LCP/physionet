from typing import Optional
import os


def get_credentials_from_env() -> tuple[Optional[str], Optional[str]]:
    """
    Get PhysioNet credentials from environment variables.

    Returns:
        Tuple of (username, password) or (None, None)
    """
    username = os.getenv("PHYSIONET_USERNAME")
    password = os.getenv("PHYSIONET_PASSWORD")
    return username, password


def format_size(size_bytes: int) -> str:
    """
    Format bytes to human-readable size.

    Args:
        size_bytes: Size in bytes

    Returns:
        Formatted string (e.g., "1.5 GB")
    """
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} PB"
