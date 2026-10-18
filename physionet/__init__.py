from physionet.api import PhysioNetClient
from physionet.citation import Citation, CitationBundle
from physionet.validate import validate_dataset, ValidationConfig, ValidationResult
from physionet.download import download

try:
    from importlib.metadata import version
    __version__ = version("physionet")
except Exception:
    __version__ = "unknown"

__all__ = [
    "PhysioNetClient",
    "Citation",
    "CitationBundle",
    "validate_dataset",
    "ValidationConfig",
    "ValidationResult",
    "download",
]
