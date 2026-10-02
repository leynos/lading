"""Lading CLI package.

This package initialises the Cyclopts application and exposes the
:func:`lading.cli.main` entry point for process launchers.
"""

from .cli import app, main
from .exceptions import LadingError

__all__ = ["LadingError", "app", "main"]
