"""Shared fixtures for the bump confinement unit tests.

Both the component-walk suite and the glob-expansion suite need a FIFO that
skips on a platform without ``os.mkfifo``.
"""

import os
import typing as typ

import pytest

if typ.TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def fifo_path(tmp_path: Path) -> Path:
    """Return a FIFO path, skipping where the platform cannot create one.

    Parameters
    ----------
    tmp_path : Path
        Per-test temporary directory.

    Returns
    -------
    Path
        A FIFO beneath ``tmp_path/workspace/docs``.
    """
    fifo = tmp_path / "workspace" / "docs" / "named-pipe"
    fifo.parent.mkdir(parents=True)
    if not hasattr(os, "mkfifo"):
        pytest.skip("os.mkfifo is unavailable on this platform")
    try:
        os.mkfifo(fifo)
    except (AttributeError, OSError) as exc:  # pragma: no cover - capability
        pytest.skip(f"cannot create a FIFO here: {exc}")
    return fifo
