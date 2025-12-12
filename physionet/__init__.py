from physionet.api import PhysioNetClient

try:
    from importlib.metadata import version
    __version__ = version("physionet")
except Exception:
    __version__ = "unknown"

__all__ = ["PhysioNetClient"]
