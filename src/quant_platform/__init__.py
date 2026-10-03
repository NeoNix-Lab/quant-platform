"""Canonical quantitative platform runtime package."""

from importlib.metadata import PackageNotFoundError, version


try:
    __version__ = version("quant-platform")
except PackageNotFoundError:
    # A source checkout is not an installed distribution. Do not duplicate
    # pyproject's version here: installed consumers always receive metadata.
    __version__ = "0+unknown"


__all__ = ["__version__", "data", "experiments"]
