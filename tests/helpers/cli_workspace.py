"""Shared helpers for the ``lading.cli`` test modules.

The CLI test suite is split across four modules by the area of the command
line surface under test. Each drives ``cli.main`` or ``cli.app``, so they all
need the same minimal single-crate workspace, and the white-box tests all need
the root logger restored around them. Both helpers live here rather than being
copied four ways.
"""

import collections.abc as cabc
import logging
from contextlib import contextmanager
from pathlib import Path

from lading.workspace import WorkspaceCrate, WorkspaceGraph


@contextmanager
def preserve_root_logger() -> cabc.Iterator[logging.Logger]:
    """Capture and restore the root logger configuration around a test.

    Yields
    ------
    logging.Logger
        The root logger, with its handlers and level still in place.
    """
    root_logger = logging.getLogger()
    prior_handlers = list(root_logger.handlers)
    prior_level = root_logger.level
    try:
        yield root_logger
    finally:
        for handler in list(root_logger.handlers):
            root_logger.removeHandler(handler)
        for handler in prior_handlers:
            root_logger.addHandler(handler)
        root_logger.setLevel(prior_level)


def make_workspace(root: Path) -> WorkspaceGraph:
    """Return a representative workspace graph for CLI tests.

    Parameters
    ----------
    root : Path
        The workspace root the single crate is placed under.

    Returns
    -------
    WorkspaceGraph
        A graph holding one publishable crate named ``crate``.
    """
    crate_root = root / "crate"
    crate = WorkspaceCrate(
        name="crate",
        version="0.1.0",
        manifest_path=crate_root / "Cargo.toml",
        root_path=crate_root,
        publish=True,
        readme_is_workspace=False,
        dependencies=(),
    )
    return WorkspaceGraph(workspace_root=root, crates=(crate,))
