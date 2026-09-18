"""Cover what `publish.run` says about the staged tree it names.

The tree named by `Staged workspace at: <path>` has usually gone by the time
a caller reads the line, cleanup having defaulted to on since issue #269.
These tests pin what the line says to what actually became of the tree,
rather than to the option that asked for it: those two answers differ
whenever a removal fails. The plan snapshot cannot do this: it redacts the
whole line, so it passes whichever state the line reports.
"""

from __future__ import annotations

import shutil
import typing as typ

from lading.commands import publish, publish_staging

from .conftest import make_config, make_crate, make_workspace

if typ.TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _staging_line(output: str) -> str:
    """Return the summary line naming the staged workspace.

    Returns
    -------
    str
        The line beginning with the staged-workspace prefix.

    Raises
    ------
    AssertionError
        If the output holds no such line, which would mean the summary
        stopped naming the staged tree at all.
    """
    for line in output.splitlines():
        if line.startswith("Staged workspace at:"):
            return line
    message = f"no staged-workspace line in publish output: {output!r}"
    raise AssertionError(message)


def _run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, cleanup: bool) -> str:
    """Run a publish against a one-crate workspace and return its summary.

    The build directory is named so the staged tree stays under ``tmp_path``
    rather than the system temporary directory, which keeps the retained case
    from leaking a tree the suite's leak check would then fail on.

    Returns
    -------
    str
        The publish summary.
    """
    root = tmp_path.resolve()
    workspace = make_workspace(root, make_crate(root, "alpha"))
    monkeypatch.setattr("lading.workspace.load_workspace", lambda _: workspace)
    return publish.run(
        root,
        make_config(),
        options=publish.PublishOptions(
            build_directory=tmp_path.parent / "build", cleanup=cleanup
        ),
    )


def test_the_summary_says_a_removed_tree_has_gone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Cleanup is the default, so the common case must report the removal.

    Naming a path with nothing else said reads as an invitation to go and
    look at a directory that publish deleted on its way out.
    """
    output = _run(monkeypatch, tmp_path, cleanup=True)

    assert _staging_line(output).endswith("(removed)"), (
        "a publish that removed its staged tree did not say so"
    )


def test_the_summary_leaves_a_retained_tree_unqualified(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A tree kept for debugging is still there, so nothing is appended.

    This is what stops the previous case passing against a call site that
    reported removal unconditionally.
    """
    output = _run(monkeypatch, tmp_path, cleanup=False)

    assert not _staging_line(output).endswith("(removed)"), (
        "a retained staged tree was reported as removed"
    )


def test_the_summary_does_not_claim_a_removal_that_failed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A removal that raised leaves the tree, so the line must not say gone.

    This is the case the option alone cannot answer, and the one that
    separates reporting an intention from reporting an outcome. With
    `cleanup=True` the request was made and refused; a summary built from the
    request calls a directory that is still on disk removed, and sends whoever
    reads it looking for something they were told had been deleted.
    """

    def _refuse(*_args: object, **_kwargs: object) -> None:
        """Fail every removal the way a permission error would."""
        message = "refusing to remove the staged tree"
        raise OSError(message)

    build_directory = tmp_path.parent / "build"
    cleanup_target = build_directory / tmp_path.resolve().name
    monkeypatch.setattr(shutil, "rmtree", _refuse)
    try:
        output = _run(monkeypatch, tmp_path, cleanup=True)
    finally:
        # Undone before the tree is swept, because the sweep needs the real
        # `rmtree` back, and the target is untracked because a failed removal
        # deliberately leaves it registered for a later attempt.
        monkeypatch.undo()
        publish_staging._ACTIVE_STAGING_ROOTS.discard(cleanup_target)
        shutil.rmtree(build_directory, ignore_errors=True)

    assert not _staging_line(output).endswith("(removed)"), (
        "a staged tree whose removal failed was reported as removed"
    )
