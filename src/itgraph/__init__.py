"""Collection and storage layer for the IT-media channel graph."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("it-media-graph")
except PackageNotFoundError:  # pragma: no cover - source checkout only
    __version__ = "0.0.0"

__all__ = ["__version__"]
